"""Stream a live policy's targets to NOVA as action chunks of *one* waypoint.

Endpoint: ``/experimental/cells/{cell}/controllers/{controller}/execution/action-chunk-streaming``
(``ActionChunkStreamingApi`` in the API client), one session per motion group:
``InitializeActionChunksRequest``, then ``ActionChunkRequest``s. The controller
steps every 4 ms; each step it heads for the first queued waypoint whose
timestamp is still in the future, at the velocity that reaches it on time. A
new chunk replaces every queued waypoint at or after its first timestamp.
Waypoints already in the past are skipped silently.

Why one waypoint per chunk works
    Until NOVA 26.8.0-112 the controller braked as soon as no future waypoint
    was queued, so a policy sending one target per step stopped after every
    step. ``delay_until_braking`` (ms, on ``ActionChunkRequest``) changes that:
    with nothing queued the arm keeps its current velocity that long and only
    then brakes, and every chunk resets the countdown. A target stream at the
    policy's own rate (e.g. 15 Hz) is followed smoothly, and a late target is
    coasted through instead of braked for.

How targets are stamped (rules measured streaming teleoperation targets)
    * **From observation time.** A target's timestamp is when the state it was
      computed from arrived, on the controller's clock
      (:class:`ControllerClock`), plus ``lead_ms``. Stamping at send time would
      turn inference and network jitter into velocity noise.
    * **Spacing.** At most one waypoint per ``waypoint_spacing_ms``: the
      controller closes the error to the next waypoint in whatever time is
      left, so dense waypoints make it a very high-gain follower that rings
      after any disturbance. 15 Hz is 67 ms apart; faster policies get thinned.
    * **Late targets are dropped.** One that would land within ``MIN_LEAD_MS``
      of the controller's now would yank the coasting arm back if pulled
      forward; the next fresh one is right behind it. Only after
      ``LATE_FALLBACK_S`` with nothing on time (``lead_ms`` too short for the
      link) do late targets go out, pulled forward, with a warning.
    * **Final waypoint.** On stop, the last target once more with
      ``delay_until_braking: 0``, so the arm stops on it instead of coasting
      ``delay_until_braking`` ms past it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import loguru
from wandelbots_api_client.v2_pydantic import ApiClient, Configuration, models
from wandelbots_api_client.v2_pydantic.api import ActionChunkStreamingApi

from src.actions.internal.controller_clock import ControllerClock

logger = loguru.logger

DEFAULT_DELAY_UNTIL_BRAKING_MS = 200.0
"""Coast time after the last waypoint. Measured for teleoperation: 200 covers 100-150 ms
stalls; 120 braked during longer ones and then rang; 300 was worse than 200."""

DEFAULT_WAYPOINT_SPACING_MS = 40.0
"""Minimum distance between waypoint timestamps; keep it below ``lead_ms``."""

MIN_LEAD_MS = 16.0
"""Never schedule a waypoint closer than this to the controller's now."""

LATE_FALLBACK_S = 0.5
"""After this long without an on-time target, late ones go out pulled forward."""

OPEN_TIMEOUT_S = 10.0


class ActionChunkRequest(models.ActionChunkRequest):
    """``ActionChunkRequest`` plus ``delay_until_braking`` (NOVA 26.8.0-112).

    The API client pinned by novaflow-types (26.6.0) predates the field and
    would drop it; the server accepts it.
    """

    delay_until_braking: int | None = None


def chunk_streaming_api(
    api_url: str, access_token: str | None
) -> ActionChunkStreamingApi:
    """Client for the endpoint, e.g. ``chunk_streaming_api("http://api-gateway:8080", None)``."""
    configuration = Configuration(host=f"{api_url}/api/v2", access_token=access_token)
    return ActionChunkStreamingApi(api_client=ApiClient(configuration=configuration))


@dataclass
class StreamStats:
    sent: int = 0
    thinned: int = 0
    late_dropped: int = 0
    late_forced: int = 0


class ActionChunkStreamer:
    """One motion group's action-chunk session, fed one target at a time.

    Args:
        chunk_api: From :func:`chunk_streaming_api`.
        cell, controller, motion_group_id, tcp: Which motion group; the session
            needs a TCP even for joint waypoints.
        clock: That motion group's controller clock (fed by the state stream).
        lead_ms: How far past its observation a target is scheduled.
        delay_until_braking_ms: Coast time after the newest waypoint.
        waypoint_spacing_ms: Minimum waypoint spacing.
        now: Local monotonic clock (injectable for tests).
    """

    def __init__(
        self,
        chunk_api: Any,
        *,
        cell: str,
        controller: str,
        motion_group_id: str,
        tcp: str,
        clock: ControllerClock,
        lead_ms: float,
        delay_until_braking_ms: float = DEFAULT_DELAY_UNTIL_BRAKING_MS,
        waypoint_spacing_ms: float = DEFAULT_WAYPOINT_SPACING_MS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if lead_ms < MIN_LEAD_MS:
            raise ValueError(
                f"lead_ms must be at least {MIN_LEAD_MS:.0f} ms, got {lead_ms}"
            )
        self.motion_group_id = motion_group_id
        self._chunk_api = chunk_api
        self._cell = cell
        self._controller = controller
        self._tcp = tcp
        self._clock = clock
        self._lead_ms = float(lead_ms)
        self._delay_until_braking_ms = int(delay_until_braking_ms)
        self._spacing_ms = float(waypoint_spacing_ms)
        self._now = now
        self._outbox: asyncio.Queue[ActionChunkRequest | None] = asyncio.Queue()
        self._acked = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None
        self._last: tuple[int, list[float]] | None = None  # newest waypoint sent
        self._on_time_s = -float("inf")
        self._warned_late = False
        self.stats = StreamStats()

    # -- lifecycle -----------------------------------------------------------

    async def open(self) -> None:
        """Start the session and wait until the controller acknowledged it."""
        self._task = asyncio.create_task(
            self._chunk_api.execute_action_chunks(
                cell=self._cell,
                controller=self._controller,
                client_request_generator=self._requests,
            ),
            name=f"action-chunks-{self.motion_group_id}",
        )
        acked = asyncio.create_task(self._acked.wait())
        done, _ = await asyncio.wait(
            {acked, self._task},
            timeout=OPEN_TIMEOUT_S,
            return_when=asyncio.FIRST_COMPLETED,
        )
        acked.cancel()
        if self._task in done:
            self.raise_if_failed()
            raise RuntimeError(
                f"{self.motion_group_id}: action-chunk session ended at start"
            )
        if not done:
            raise TimeoutError(
                f"{self.motion_group_id}: no action-chunk session ack in {OPEN_TIMEOUT_S:.0f}s"
            )

    def raise_if_failed(self) -> None:
        if self._failure is None and self._task is not None and self._task.done():
            if not self._task.cancelled() and self._task.exception() is not None:
                self._failure = RuntimeError(
                    f"{self.motion_group_id}: {self._task.exception()}"
                )
        if self._failure is not None:
            raise self._failure

    async def close(self) -> None:
        if self._task is not None:
            self._outbox.put_nowait(
                None
            )  # ends the request generator, closing the socket
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(self._task, 2.0)
        s = self.stats
        logger.info(
            f"{self.motion_group_id}: {s.sent} waypoints sent, {s.thinned} thinned by spacing, "
            f"{s.late_dropped} late dropped, {s.late_forced} late forced"
        )

    # -- targets -------------------------------------------------------------

    def send_target(self, joints: Sequence[float], sampled_s: float) -> bool:
        """Schedule a target computed from a state that arrived at ``sampled_s``.

        Returns whether a waypoint went out (False: thinned or dropped as late).
        """
        self.raise_if_failed()
        now = self._now()
        if not self._clock.started:
            self._clock.start(now)  # this chunk starts the session clock
        offset = self._clock.offset_at(now)
        controller_now = now * 1000.0 + offset

        ts = sampled_s * 1000.0 + offset + self._lead_ms
        if self._last is not None and ts < self._last[0] + self._spacing_ms:
            self.stats.thinned += 1
            return False
        if ts >= controller_now + MIN_LEAD_MS:
            self._on_time_s, self._warned_late = now, False
        elif now - self._on_time_s < LATE_FALLBACK_S:
            self.stats.late_dropped += 1
            return False
        else:
            if not self._warned_late:
                logger.warning(
                    f"{self.motion_group_id}: targets arrive later than lead_ms ({self._lead_ms:.0f}); raise it"
                )
                self._warned_late = True
            self.stats.late_forced += 1
            ts = controller_now + MIN_LEAD_MS

        self._last = (int(ts), [float(q) for q in joints])
        self._outbox.put_nowait(_chunk(*self._last, self._delay_until_braking_ms))
        self.stats.sent += 1
        return True

    def finish(self) -> None:
        """Stop on the last target: send it once more with ``delay_until_braking`` 0."""
        if self._last is None or self._failure is not None:
            return
        now = self._now()
        ts = int(
            max(self._last[0], now * 1000.0 + self._clock.offset_at(now) + MIN_LEAD_MS)
        )
        self._outbox.put_nowait(_chunk(ts, self._last[1], 0))
        self._last = None

    # -- session -------------------------------------------------------------

    async def _requests(
        self, responses: AsyncGenerator[Any, None]
    ) -> AsyncGenerator[Any, None]:
        yield models.InitializeActionChunksRequest(
            motion_group=self.motion_group_id, tcp=self._tcp
        )
        watcher = asyncio.create_task(self._watch(responses))
        try:
            while (request := await self._outbox.get()) is not None:
                yield request
        finally:
            watcher.cancel()

    async def _watch(self, responses: AsyncGenerator[Any, None]) -> None:
        async for response in responses:
            self._acked.set()
            result = getattr(response, "root", response)
            if getattr(result, "kind", None) == "MOTION_ERROR":
                self._failure = RuntimeError(
                    f"{self.motion_group_id}: motion error: {result.message}"
                )
                logger.error(str(self._failure))


def _chunk(
    timestamp_ms: int, joints: list[float], delay_until_braking_ms: int
) -> ActionChunkRequest:
    return ActionChunkRequest(
        waypoints=[
            models.Waypoint(
                timestamp=timestamp_ms, waypoint=models.JointWaypoint(joints=joints)
            )
        ],
        delay_until_braking=delay_until_braking_ms,
    )
