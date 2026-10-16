"""``PolicyLoop``: observe → infer → one live target per step, at the policy's rate.

Each step:

1. read the motion group's latest state (:class:`ControllerStateStream`);
2. one inference round trip to the policy server (:class:`WebsocketPolicyClient`);
3. send the target as an action chunk of *one* waypoint with
   ``delay_until_braking`` (:class:`ActionChunkStreamer`), stamped from when its
   observation arrived plus ``lead_ms``;
4. sleep the rest of the policy's ``dt_ms``.

``lead_ms`` defaults to one policy step plus ``DEFAULT_LEAD_MARGIN_MS``: a target
is due one step after its observation, as the policy was trained, plus enough
margin for inference and delivery that the next target arrives before this one
is due (so a future waypoint is always queued).

The loop runs until :meth:`stop` (the action's halt), then stops the arm on its
last target and waits for standstill. It ends the episode with an error when
the controller leaves a safe state (e-stop, protective stop, ...), the motion
group stays paused (joint limit, collision, singularity, workspace boundary;
nobody is there to jog it free), the robot state goes stale, or the policy or
the chunk session fails. The rest of the safety envelope belongs to the
behavior tree. Why this replaces novapolicy's ``PolicyExecutor``: see the README.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import loguru
import numpy as np

from src.actions.internal.controller_state import SAFE_STATES

logger = loguru.logger

DEFAULT_LEAD_MARGIN_MS = 40.0
"""Added to the policy's step time for the default ``lead_ms``."""

STATE_WAIT_TIMEOUT_S = 10.0
STATE_MAX_AGE_S = 0.25
"""Don't infer on a state older than this; wait for a fresh one."""
STATE_TIMEOUT_S = 2.0
"""No fresh state for this long ends the episode."""
PAUSE_CONFIRM_S = 0.5
"""A jogging pause that lasts this long ends the episode."""
STANDSTILL_TIMEOUT_S = 2.0


class EpisodeError(RuntimeError):
    """The episode cannot continue (robot, controller or policy)."""


@dataclass
class EpisodeResult:
    steps: int
    duration_s: float


class PolicyLoop:
    """Run a policy on one motion group until :meth:`stop`.

    Args:
        states: The motion group's :class:`ControllerStateStream` (not yet started).
        policy: A :class:`WebsocketPolicyClient` (not yet connected).
        make_streamer: ``lead_ms -> ActionChunkStreamer`` (not yet opened); called
            once the first state is in, so it can use the active TCP and clock.
        lead_ms: How far past its observation each target is due; ``None`` =
            policy ``dt_ms`` + ``DEFAULT_LEAD_MARGIN_MS``.
        prompt: Optional language instruction, sent with every observation.
    """

    def __init__(
        self,
        states: Any,
        policy: Any,
        make_streamer: Callable[[float], Any],
        *,
        lead_ms: float | None = None,
        prompt: str = "",
    ) -> None:
        self._states = states
        self._policy = policy
        self._make_streamer = make_streamer
        self._lead_ms = lead_ms
        self._prompt = prompt
        self._streamer: Any = None
        self._stop = asyncio.Event()
        self.steps = 0

    def stop(self) -> None:
        """Ask the loop to end; :meth:`run` returns after the robot stopped."""
        self._stop.set()

    async def run(self) -> EpisodeResult:
        started = time.monotonic()
        self._states.start()
        try:
            metadata = await self._policy.connect()
            await self._wait_first_state()
            dof = self._states.sample.joints.size
            if metadata.dof != dof:
                raise EpisodeError(
                    f"Policy drives {metadata.dof} joints, {self._states.motion_group_id} has {dof}"
                )
            lead_ms = self._lead_ms or metadata.dt_ms + DEFAULT_LEAD_MARGIN_MS
            logger.info(
                f"Policy step {metadata.dt_ms:.1f} ms, waypoint lead {lead_ms:.1f} ms"
            )
            self._streamer = self._make_streamer(lead_ms)
            await self._streamer.open()
            await self._loop(metadata.dt_ms)
        finally:
            await self._shutdown()
        return EpisodeResult(steps=self.steps, duration_s=time.monotonic() - started)

    async def _loop(self, dt_ms: float) -> None:
        while not self._stop.is_set():
            tick = time.monotonic()
            sample = self._states.sample
            # Snapshot: the sample keeps updating while inference runs.
            joints, velocities, received_s = (
                sample.joints,
                sample.velocities,
                sample.received_s,
            )
            self._check_robot(tick)
            if tick - received_s > STATE_MAX_AGE_S:
                await asyncio.sleep(0.01)  # stream hiccup: don't act on stale state
                continue

            observation: dict[str, Any] = {
                "observation/joint_position": joints,
                "observation/joint_velocity": velocities
                if velocities is not None
                else np.zeros_like(joints),
            }
            if self._prompt:
                observation["prompt"] = self._prompt
            target = await self._policy.infer(observation)

            self._streamer.send_target(target, received_s)
            self._streamer.raise_if_failed()
            self.steps += 1

            remaining = dt_ms / 1000.0 - (time.monotonic() - tick)
            if remaining > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), remaining)

    def _check_robot(self, now: float) -> None:
        states, sample = self._states, self._states.sample
        if states.safety_state is not None and states.safety_state not in SAFE_STATES:
            raise EpisodeError(f"Controller {states.controller}: {states.safety_state}")
        if now - sample.received_s > STATE_TIMEOUT_S:
            raise EpisodeError(
                f"{states.motion_group_id}: no robot state for {STATE_TIMEOUT_S:.0f}s"
            )
        if (
            sample.paused_since_s is not None
            and now - sample.paused_since_s >= PAUSE_CONFIRM_S
        ):
            raise EpisodeError(
                f"{states.motion_group_id}: jogging {sample.jogging_state}"
            )

    async def _wait_first_state(self) -> None:
        deadline = time.monotonic() + STATE_WAIT_TIMEOUT_S
        while self._states.sample.joints is None:
            if time.monotonic() > deadline:
                raise EpisodeError(
                    f"No state for {self._states.motion_group_id} within {STATE_WAIT_TIMEOUT_S:.0f}s"
                )
            await asyncio.sleep(0.01)

    async def _shutdown(self) -> None:
        """Stop the arm on its last target, wait for standstill, close everything."""
        if self._streamer is not None:
            self._streamer.finish()
            deadline = time.monotonic() + STANDSTILL_TIMEOUT_S
            while self.steps and not self._states.sample.standstill:
                if time.monotonic() > deadline:
                    logger.warning(
                        f"No standstill within {STANDSTILL_TIMEOUT_S:.0f}s of the final waypoint"
                    )
                    break
                await asyncio.sleep(0.02)
            await self._streamer.close()
        await self._states.stop()
        await self._policy.close()
