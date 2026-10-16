"""Live robot state from NOVA's controller state stream.

The controller's state stream (``/cells/{cell}/controllers/{controller}/state-stream``),
raw JSON on purpose: the generated client's jogging-state union has rejected
kinds the controller really sends, which would kill the stream mid-episode.
Every message carries the controller's ``safety_state`` and, for the driven
motion group, everything the policy loop needs, folded into a latest-value
:class:`MotionGroupSample`:

``joints``, ``received_s``
    The observation, and when it arrived here (``time.monotonic()``). Targets
    computed from it are stamped from this time (see ``action_chunks.py``).
``velocities``
    NOVA 26.8 does not report ``joint_velocity``, so positions are
    differentiated over the controller's own ``timestamp`` (at least
    ``FD_BASELINE_S`` apart), which is immune to delivery jitter.
``clock``
    The action-chunk session clock, fed from ``execute.details.jogger_session_timestamp_ms``
    (sent as a string on this stream).
``jogging_state``, ``paused_since_s``
    ``RUNNING`` or a pause (``PAUSED_NEAR_JOINT_LIMIT``, ...), and since when.
``standstill``
    For waiting out the final waypoint.
``tcp``
    The active TCP, which the action-chunk session is initialised with.

The stream reconnects until :meth:`stop`; the policy loop treats a sample that
stops updating as a lost robot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import loguru
import numpy as np
import websockets
from numpy.typing import NDArray

from src.actions.internal.controller_clock import ControllerClock

logger = loguru.logger

FD_BASELINE_S = 0.016
"""Differentiate positions over at least this much controller time."""

SAFE_STATES = frozenset({"SAFETY_STATE_NORMAL", "SAFETY_STATE_REDUCED"})
"""Any other safety state (e-stop, protective stop, fault, ...) ends the episode."""

_RECONNECT_DELAY_S = 0.5


@dataclass
class MotionGroupSample:
    """Latest state of one motion group."""

    joints: NDArray[np.float32] | None = None
    velocities: NDArray[np.float32] | None = None
    received_s: float = 0.0
    standstill: bool = False
    tcp: str = ""
    jogging_state: str | None = None
    paused_since_s: float | None = None
    clock: ControllerClock = field(default_factory=ControllerClock)
    _history: deque[tuple[float, NDArray[np.float32]]] = field(default_factory=deque)


class ControllerStateStream:
    """Latest-value subscriber to the state of one motion group.

    Args:
        api_url: NOVA API base, e.g. ``http://api-gateway:8080``.
        access_token: Bearer token, if the instance needs one.
        cell: Cell name.
        motion_group_id: ``<index>@<controller>``.
        response_rate_ms: Requested stream period; the controller cycle at most.
    """

    def __init__(
        self,
        api_url: str,
        access_token: str | None,
        cell: str,
        motion_group_id: str,
        *,
        response_rate_ms: int = 8,
    ) -> None:
        self.motion_group_id = motion_group_id
        self.controller = motion_group_id.split("@", 1)[1]
        base = (
            api_url.rstrip("/")
            .replace("https://", "wss://")
            .replace("http://", "ws://")
        )
        self._url = (
            f"{base}/api/v2/cells/{cell}/controllers/{self.controller}/state-stream"
            f"?response_rate={response_rate_ms}"
        )
        self._headers = (
            {"Authorization": f"Bearer {access_token}"} if access_token else {}
        )
        self.sample = MotionGroupSample()
        self.safety_state: str | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(
            self._run(), name=f"state-stream-{self.motion_group_id}"
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def handle(self, message: dict[str, Any], received_s: float) -> None:
        """Fold one state message in (public for tests)."""
        state = message.get("result", message)
        self.safety_state = state.get("safety_state", self.safety_state)
        for group in state.get("motion_groups", []):
            if group.get("motion_group") == self.motion_group_id:
                _fold(self.sample, group, received_s)

    async def _run(self) -> None:
        while True:
            try:
                async with websockets.connect(
                    self._url, additional_headers=self._headers, close_timeout=1
                ) as ws:
                    logger.info(f"State stream open for controller {self.controller}")
                    async for raw in ws:
                        self.handle(json.loads(raw), time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnect; the loop watches staleness
                logger.warning(
                    f"State stream {self.controller} dropped ({exc}); reconnecting"
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)


def _fold(sample: MotionGroupSample, group: dict[str, Any], received_s: float) -> None:
    joints = np.asarray(group["joint_position"], dtype=np.float32)
    if (stamp := group.get("timestamp")) is not None:
        sample.velocities = _differentiate(sample, _timestamp_s(stamp), joints)
    sample.joints = joints
    sample.received_s = received_s
    sample.standstill = bool(group.get("standstill", False))
    sample.tcp = group.get("tcp") or sample.tcp

    details = (group.get("execute") or {}).get("details") or {}
    session_ms = details.get("jogger_session_timestamp_ms")
    if session_ms is not None and int(session_ms) > 0:
        sample.clock.observe(int(session_ms), received_s)

    kind = (details.get("state") or {}).get("kind")
    if kind is not None and kind != sample.jogging_state:
        sample.paused_since_s = None if kind == "RUNNING" else received_s
        sample.jogging_state = kind


def _differentiate(
    sample: MotionGroupSample, stamp_s: float, joints: NDArray[np.float32]
) -> NDArray[np.float32] | None:
    history = sample._history
    if history and stamp_s <= history[-1][0]:
        return sample.velocities  # duplicate frame
    history.append((stamp_s, joints))
    # Keep the oldest entry the newest one that is at least FD_BASELINE_S back.
    while len(history) > 2 and stamp_s - history[1][0] >= FD_BASELINE_S:
        history.popleft()
    then_s, then = history[0]
    if stamp_s - then_s < FD_BASELINE_S:
        return sample.velocities
    return ((joints - then) / (stamp_s - then_s)).astype(np.float32)


def _timestamp_s(stamp: str) -> float:
    """``2026-10-15T18:49:23.946470568Z`` -> POSIX seconds, sub-µs digits kept."""
    whole, _, frac = stamp.rstrip("Z").partition(".")
    return datetime.fromisoformat(whole + "+00:00").timestamp() + float(
        "0." + (frac or "0")
    )
