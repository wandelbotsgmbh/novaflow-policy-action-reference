"""``PolicyLoop`` with a fake policy, state stream and streamer: one target per
step stamped from the observation; DOF check; safety, pause and staleness end
the episode; shutdown stops the arm and closes everything."""

import time

import numpy as np
import pytest

from src.actions.internal import policy_loop
from src.actions.internal.controller_state import MotionGroupSample
from src.actions.internal.policy_client import PolicyMetadata
from src.actions.internal.policy_loop import (
    DEFAULT_LEAD_MARGIN_MS,
    EpisodeError,
    PolicyLoop,
)

MG = "0@ur5e"


class FakeStates:
    def __init__(self, joints=(0.0, 0.1, 0.2)):
        self.motion_group_id, self.controller = MG, "ur5e"
        self.safety_state = "SAFETY_STATE_NORMAL"
        self.sample = MotionGroupSample(
            joints=np.asarray(joints, dtype=np.float32),
            received_s=time.monotonic(),
            standstill=True,
        )
        self.started = self.stopped = False

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeStreamer:
    def __init__(self, lead_ms):
        self.lead_ms = lead_ms
        self.targets = []
        self.opened = self.finished = self.closed = False

    async def open(self):
        self.opened = True

    def send_target(self, joints, sampled_s):
        self.targets.append((np.asarray(joints).tolist(), sampled_s))
        return True

    def raise_if_failed(self):
        pass

    def finish(self):
        self.finished = True

    async def close(self):
        self.closed = True


class FakePolicy:
    def __init__(self, dof=3, *, steps=3, on_infer=None):
        self.dof, self.steps, self.on_infer = dof, steps, on_infer
        self.observations = []
        self.loop = None
        self.closed = False

    async def connect(self):
        return PolicyMetadata(dof=self.dof, dt_ms=0.001, raw={})

    async def infer(self, obs):
        self.observations.append(obs)
        if self.on_infer:
            self.on_infer()
        if len(self.observations) >= self.steps:
            self.loop.stop()
        return obs["observation/joint_position"] + 1.0

    async def close(self):
        self.closed = True


def _loop(policy, states, **kwargs):
    made = []

    def make(lead_ms):
        made.append(FakeStreamer(lead_ms))
        return made[-1]

    loop = PolicyLoop(states, policy, make, **kwargs)
    policy.loop = loop
    return loop, made


async def test_one_target_per_step():
    states, policy = FakeStates(), FakePolicy(steps=3)
    loop, made = _loop(policy, states, prompt="wave")

    result = await loop.run()

    assert result.steps == 3
    obs = policy.observations[0]
    np.testing.assert_allclose(
        obs["observation/joint_position"], [0.0, 0.1, 0.2], rtol=1e-6
    )
    assert obs["observation/joint_velocity"].shape == (3,) and obs["prompt"] == "wave"
    assert [t for t, _ in made[0].targets] == [pytest.approx([1.0, 1.1, 1.2])] * 3


async def test_targets_carry_the_observation_time_not_the_send_time():
    states = FakeStates()
    stamped = states.sample.received_s

    def newer_state_lands_during_inference():
        states.sample.received_s += 0.05

    loop, made = _loop(
        FakePolicy(steps=1, on_infer=newer_state_lands_during_inference), states
    )
    await loop.run()

    assert made[0].targets[0][1] == stamped


async def test_lead_defaults_to_one_step_plus_margin_or_is_passed_through():
    loop, made = _loop(FakePolicy(steps=1), FakeStates())
    await loop.run()
    assert made[0].lead_ms == pytest.approx(0.001 + DEFAULT_LEAD_MARGIN_MS)

    loop, made = _loop(FakePolicy(steps=1), FakeStates(), lead_ms=90.0)
    await loop.run()
    assert made[0].lead_ms == 90.0


async def test_shutdown_stops_the_arm_and_closes_everything():
    states, policy = FakeStates(), FakePolicy(steps=2)
    loop, made = _loop(policy, states)
    await loop.run()

    assert made[0].opened and made[0].finished and made[0].closed
    assert states.started and states.stopped and policy.closed


async def test_dof_mismatch_fails_before_any_motion():
    states, policy = FakeStates(), FakePolicy(dof=6)
    loop, made = _loop(policy, states)

    with pytest.raises(EpisodeError, match="6 joints"):
        await loop.run()
    assert made == [] and policy.observations == [] and states.stopped


@pytest.mark.parametrize(
    "break_robot,match",
    [
        (
            lambda s: setattr(s, "safety_state", "SAFETY_STATE_DEVICE_EMERGENCY_STOP"),
            "EMERGENCY_STOP",
        ),
        (
            lambda s: (
                setattr(s.sample, "jogging_state", "PAUSED_NEAR_JOINT_LIMIT")
                or setattr(
                    s.sample,
                    "paused_since_s",
                    time.monotonic() - policy_loop.PAUSE_CONFIRM_S,
                )
            ),
            "PAUSED_NEAR_JOINT_LIMIT",
        ),
        (
            lambda s: setattr(s.sample, "received_s", time.monotonic() - 10),
            "no robot state",
        ),
    ],
)
async def test_robot_trouble_ends_the_episode(break_robot, match):
    states = FakeStates()
    loop, made = _loop(
        FakePolicy(steps=100, on_infer=lambda: break_robot(states)), states
    )

    with pytest.raises(EpisodeError, match=match):
        await loop.run()
    assert made[0].finished and made[0].closed
