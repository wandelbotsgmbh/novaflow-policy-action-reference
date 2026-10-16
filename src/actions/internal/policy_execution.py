"""Glue between a NovaFlow action and :class:`PolicyLoop`.

Owns the NOVA connection settings, building the loop for a ``MotionTarget``,
and mapping the loop's end to a NovaFlow verdict:

* halt (operator Stop, a tree ``Timeout`` or a reactive guard) → the loop
  stops the robot on its last target → ``terminal="halted"``;
* any error (policy, robot, controller) → ``ActionFailure`` with
  ``terminal="error"``, a node FAILURE the tree can recover from.

Connection settings come from the environment the app operator injects into
every NOVA app: ``NOVA_API`` (``api-gateway:8080`` in the cell, no scheme),
``CELL_NAME`` and, outside the cell if needed, ``NOVA_ACCESS_TOKEN``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING

import loguru
from novaflow.core import ActionFailure
from pydantic import BaseModel, Field

from src.actions.internal.action_chunks import ActionChunkStreamer, chunk_streaming_api
from src.actions.internal.controller_state import ControllerStateStream
from src.actions.internal.policy_client import WebsocketPolicyClient
from src.actions.internal.policy_loop import PolicyLoop

if TYPE_CHECKING:
    from novaflow.core import ActionContext, HaltToken
    from novaflow.types.data.motion_target import MotionTarget

logger = loguru.logger


class PolicyInput(BaseModel):
    """Where the policy runs and how to stream its targets."""

    policy_url: str = Field(
        default="ws://app-dummy-policy:8080",
        description="Websocket URL of the policy server (openpi protocol).",
    )
    prompt: str = Field(
        default="",
        description="Language instruction sent with every observation (omitted when empty).",
    )
    lead_ms: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "How long after its observation a target is due on the controller, in ms. "
            "0 = one policy step + 40 ms. Raise it if the log reports late targets."
        ),
    )
    delay_until_braking_ms: float = Field(
        default=200.0,
        ge=0.0,
        description="How long the arm keeps moving when the next target is late before it brakes, in ms.",
    )
    timeout_s: float = Field(
        default=5.0,
        gt=0.0,
        description="Deadline for connecting to the policy and for each inference.",
    )


class PolicyEpisodeOutput(BaseModel):
    """Verdict of one policy episode. Branch on ``terminal``, not ``reason``."""

    success: bool = Field(
        ..., description="True only if the episode ended without halt or error."
    )
    terminal: str = Field(
        ..., description="'halted' | 'error'. A tree Timeout ends it as 'halted'."
    )
    reason: str = Field(..., description="What ended the episode, human readable.")
    steps: int = Field(..., description="Policy inference steps executed.")
    duration_s: float = Field(..., description="Episode wall-clock duration.")


async def run_remote_policy(
    target: MotionTarget, settings: PolicyInput, halt: HaltToken, ctx: ActionContext
) -> PolicyEpisodeOutput:
    """Run one episode on ``target`` until halted."""
    api_url, token, cell = _nova_api(), os.environ.get("NOVA_ACCESS_TOKEN"), _cell()
    motion_group_id = f"{target.motion_group}@{target.controller}"
    logger.info(f"Policy {settings.policy_url} on {motion_group_id}")

    states = ControllerStateStream(api_url, token, cell, motion_group_id)
    chunk_api = chunk_streaming_api(api_url, token)

    def make_streamer(lead_ms: float) -> ActionChunkStreamer:
        return ActionChunkStreamer(
            chunk_api,
            cell=cell,
            controller=states.controller,
            motion_group_id=motion_group_id,
            tcp=target.tcp
            or states.sample.tcp,  # the active TCP unless the node names one
            clock=states.sample.clock,
            lead_ms=lead_ms,
            delay_until_braking_ms=settings.delay_until_braking_ms,
        )

    loop = PolicyLoop(
        states,
        WebsocketPolicyClient(settings.policy_url, timeout_s=settings.timeout_s),
        make_streamer,
        lead_ms=settings.lead_ms or None,
        prompt=settings.prompt,
    )
    return await run_episode(loop, halt, ctx)


def _nova_api() -> str:
    url = os.environ.get(
        "NOVA_API", "http://api-gateway.wandelbots.svc.cluster.local:8080"
    )
    return (url if "://" in url else f"http://{url}").rstrip("/")


def _cell() -> str:
    return os.environ.get("CELL_NAME", "cell")


async def run_episode(
    loop: PolicyLoop, halt: HaltToken, ctx: ActionContext
) -> PolicyEpisodeOutput:
    """Run the loop with halt → stop, progress reporting, and error → ``ActionFailure``."""
    halt.on_halt(loop.stop, name="policy-stop")
    progress = asyncio.create_task(_report_progress(loop, ctx))
    try:
        result = await loop.run()
    except Exception as exc:
        logger.bind(run_id=ctx.run_id).error(f"Policy episode failed: {exc}")
        raise ActionFailure(
            f"Policy episode failed: {exc}",
            output=PolicyEpisodeOutput(
                success=False,
                terminal="error",
                reason=str(exc),
                steps=loop.steps,
                duration_s=0.0,
            ),
        ) from exc
    finally:
        progress.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress

    logger.bind(run_id=ctx.run_id).info(
        f"Policy episode halted after {result.steps} steps, {result.duration_s:.1f}s"
    )
    return PolicyEpisodeOutput(
        success=False,
        terminal="halted",
        reason="halted",
        steps=result.steps,
        duration_s=result.duration_s,
    )


async def _report_progress(
    loop: PolicyLoop, ctx: ActionContext, interval_s: float = 0.5
) -> None:
    while True:
        with contextlib.suppress(Exception):
            await ctx.report_progress(loop.steps, "running")
        await asyncio.sleep(interval_s)
