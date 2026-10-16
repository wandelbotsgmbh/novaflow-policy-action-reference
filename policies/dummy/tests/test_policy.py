"""The dummy's motion: bounded, continuous, zero-mean, and no drift across episodes."""

from __future__ import annotations

import numpy as np
import pytest
from dummy_policy.policy import (
    AMPLITUDE_RAD,
    DT_MS,
    JOINT_POSITION,
    MAX_DELTA_RAD,
    PERIOD_S,
    DummyPolicy,
    PolicyInputError,
)

DOF = 6
HOME = np.array([0.0, -1.571, 1.571, 0.0, 1.42, 3.142], dtype=np.float32)
STEPS_PER_PERIOD = round(PERIOD_S * 1000.0 / DT_MS)  # 90 at 15 Hz


def _infer(episode, joints=HOME) -> np.ndarray:
    actions = episode.infer({JOINT_POSITION: joints})["actions"]
    assert actions.shape == (1, DOF) and actions.dtype == np.float32
    return actions[0]


def _trajectory(steps: int, policy: DummyPolicy | None = None) -> np.ndarray:
    episode = (policy or DummyPolicy(DOF)).new_episode()
    return np.stack([_infer(episode) for _ in range(steps)])


def test_metadata_declares_dof_and_15_hz():
    metadata = DummyPolicy(DOF).metadata()
    assert metadata["dof"] == DOF and metadata["action_horizon"] == 1
    assert 1000.0 / metadata["dt_ms"] == pytest.approx(15.0)


def test_first_target_is_the_observed_pose():
    np.testing.assert_allclose(_infer(DummyPolicy(DOF).new_episode()), HOME, atol=1e-6)


def test_targets_stay_within_the_clamp_and_use_the_amplitude():
    trajectory = _trajectory(3 * STEPS_PER_PERIOD)
    assert np.all(np.abs(trajectory - HOME) <= MAX_DELTA_RAD + 1e-6)
    assert np.ptp(trajectory, axis=0).min() > 1.9 * AMPLITUDE_RAD


def test_consecutive_targets_are_one_step_apart():
    max_step = AMPLITUDE_RAD * (2.0 * np.pi / PERIOD_S) * DT_MS / 1000.0
    assert (
        np.abs(np.diff(_trajectory(STEPS_PER_PERIOD), axis=0)).max() <= max_step * 1.001
    )


def test_all_joints_move_identically():
    offsets = _trajectory(20) - HOME
    np.testing.assert_allclose(offsets, offsets[:, :1].repeat(DOF, axis=1), atol=1e-6)


def test_zero_mean_and_back_home_every_period():
    """Regression: a non-zero mean walked a live arm into a joint limit across episodes."""
    trajectory = _trajectory(2 * STEPS_PER_PERIOD + 1)
    np.testing.assert_allclose(trajectory[:-1].mean(axis=0), HOME, atol=1e-3)
    np.testing.assert_allclose(trajectory[-1], HOME, atol=1e-4)


def test_home_is_captured_from_the_first_observation_only():
    episode = DummyPolicy(DOF).new_episode()
    _infer(episode)
    target = _infer(episode, joints=HOME + 1.0)
    assert np.all(np.abs(target - HOME) <= MAX_DELTA_RAD + 1e-6)


@pytest.mark.parametrize(
    "steps", [STEPS_PER_PERIOD + STEPS_PER_PERIOD // 3, 4 * STEPS_PER_PERIOD]
)
def test_repeated_episodes_do_not_drift(steps):
    """Each episode starts where the last one stopped (mid-cycle or not); the
    shared anchor keeps 5 and 25 episodes in exactly the same place."""

    def run(episodes: int) -> np.ndarray:
        policy, position = DummyPolicy(DOF), HOME.copy()
        for _ in range(episodes):
            episode = policy.new_episode()
            for _ in range(steps):
                position = _infer(episode, joints=position)
        return position

    np.testing.assert_allclose(run(25), run(5), atol=1e-6)


def test_a_moved_robot_re_anchors_and_a_settled_one_does_not():
    policy = DummyPolicy(DOF)
    _infer(policy.new_episode())
    np.testing.assert_allclose(
        _infer(policy.new_episode(), joints=HOME + 0.05), HOME, atol=1e-6
    )
    np.testing.assert_allclose(
        _infer(policy.new_episode(), joints=HOME + 1.0), HOME + 1.0, atol=1e-6
    )


def test_bad_observations_are_named():
    episode = DummyPolicy(DOF).new_episode()
    with pytest.raises(PolicyInputError, match="missing"):
        episode.infer({})
    with pytest.raises(PolicyInputError, match="7 joints"):
        episode.infer({JOINT_POSITION: np.zeros(7, dtype=np.float32)})
