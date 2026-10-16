"""RunSingleArmPolicy: run a remote policy on one motion group.

The model lives in a separate **policy server** reached over a websocket
(openpi protocol, see ``internal/policy_client.py``; ``policies/dummy`` is a
complete server). This action carries no model: only the arm to drive and the
URL of the policy to ask.

Each step it sends the arm's joint state, receives absolute joint targets, and
streams the first one to the controller as a one-waypoint action chunk with
``delay_until_braking`` (``internal/policy_loop.py``). The policy server's
metadata declares its DOF (checked against the arm at connect) and its step
time (the loop's rate).

Inner loop only: the episode runs until halted. Duration and safety guards
are the tree's job: ``examples/trees/run_policy.json`` runs it in a
``parallel`` next to a ``delay`` (the halt stops the arm and the flow
succeeds; a ``Timeout`` would fail it), and guards go in a
``ReactiveSequence`` around it.

⚠️ It moves the robot as the policy says. Run with the cell clear.
"""

from __future__ import annotations

from novaflow.core import ActionContext, HaltToken, Router
from novaflow.types.data.motion_target import MotionTarget
from pydantic import Field

from src.actions.internal.policy_execution import (
    PolicyEpisodeOutput,
    PolicyInput,
    run_remote_policy,
)

router = Router()


class RunSingleArmPolicyInput(PolicyInput):
    """Run one episode of a remote policy on one arm."""

    motion_group: MotionTarget = Field(
        ..., description="The arm to drive (controller + motion group)."
    )


@router.action(
    action_name="RunSingleArmPolicy",
    input_model=RunSingleArmPolicyInput,
    output_model=PolicyEpisodeOutput,
    description=(
        "Run a remote policy (openpi websocket server) on one motion group until halted. "
        "Bound it with a parallel delay and guard it with a ReactiveSequence."
    ),
    tags=["policy", "learning", "motion"],
    preconditions="Policy server reachable; controller available; cell clear.",
    postconditions="Robot at standstill on the last target; `terminal` says why the episode ended.",
)
async def run_single_arm_policy(
    input: RunSingleArmPolicyInput, halt: HaltToken, ctx: ActionContext
) -> PolicyEpisodeOutput:
    return await run_remote_policy(input.motion_group, input, halt, ctx)
