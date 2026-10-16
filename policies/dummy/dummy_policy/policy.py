"""The dummy policy: a real policy's shape, no model.

It is shaped like the policy this repo is a reference for: joint state in, one
absolute joint target out (no chunking), at **15 Hz**. Swapping in a real model
means replacing :meth:`Episode.infer`; the server around it stays.

Motion: every joint follows the same bounded sine around the pose observed at
the start of the episode::

    q*(t) = home + A · sin(ωt)

``sin(0) = 0``, so the first target is where the robot already is, and the
sine has zero mean, so the arm comes back to ``home`` every full period. (An
earlier version gave each joint a phase offset; that oscillates around
``home − A·sin(φ)``, and because each episode re-anchors where the last one
stopped, the offset compounded and walked a live arm into a joint limit.)

An episode halted mid-cycle still leaves the arm off ``home``. So the anchor is
kept across episodes: a new episode starting within ``MAX_DELTA_RAD`` of the
previous anchor reuses it, and only a robot moved further away re-anchors.
Targets are clamped to ``home ± MAX_DELTA_RAD`` as a backstop.

Phase advances by exactly one step per inference, not by wall-clock time, so
consecutive targets are evenly spaced whatever the inference latency.

The dummy knows nothing about joint limits; NOVA pauses at a limit and the
action ends the episode with an error.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

NAME = "Dummy Joint Policy"
DT_MS = 1000.0 / 15.0
"""One inference per step at 15 Hz."""
AMPLITUDE_RAD = 0.12
PERIOD_S = 6.0
MAX_DELTA_RAD = 0.35
"""Hard clamp on |target - home| per joint."""

JOINT_POSITION = "observation/joint_position"


class PolicyInputError(ValueError):
    """The observation does not fit this policy."""


class DummyPolicy:
    """Serves episodes of the sine motion for a ``dof``-joint robot."""

    def __init__(self, dof: int) -> None:
        self.dof = dof
        self._last_anchor: NDArray[np.float32] | None = None

    def metadata(self) -> dict[str, Any]:
        """Sent to every client on connect; ``dof`` and ``dt_ms`` are required by the action."""
        return {"name": NAME, "dof": self.dof, "dt_ms": DT_MS, "action_horizon": 1}

    def new_episode(self) -> Episode:
        return Episode(self)

    def anchor(self, joints: NDArray[np.float32]) -> NDArray[np.float32]:
        """Reuse the previous anchor when the robot is still near it (see module docstring)."""
        previous = self._last_anchor
        if previous is not None and np.abs(joints - previous).max() <= MAX_DELTA_RAD:
            return previous.copy()
        self._last_anchor = joints.copy()
        return joints.copy()


class Episode:
    """One connection's state. A real policy would keep its history here."""

    def __init__(self, policy: DummyPolicy) -> None:
        self._policy = policy
        self.home: NDArray[np.float32] | None = None
        self.t_s = 0.0
        self.steps = 0

    def infer(self, observation: dict[str, Any]) -> dict[str, Any]:
        """``{"observation/joint_position": [dof]}`` -> ``{"actions": [1, dof]}``."""
        joints = self._joints(observation)
        if self.home is None:
            self.home = self._policy.anchor(joints)

        offset = AMPLITUDE_RAD * math.sin(2.0 * math.pi * self.t_s / PERIOD_S)
        target = np.clip(
            self.home + offset, self.home - MAX_DELTA_RAD, self.home + MAX_DELTA_RAD
        )

        self.t_s += DT_MS / 1000.0
        self.steps += 1
        return {"actions": target.astype(np.float32)[None, :]}

    def _joints(self, observation: dict[str, Any]) -> NDArray[np.float32]:
        if JOINT_POSITION not in observation:
            raise PolicyInputError(f"Observation is missing '{JOINT_POSITION}'")
        joints = np.asarray(observation[JOINT_POSITION], dtype=np.float32).reshape(-1)
        if joints.size != self._policy.dof:
            raise PolicyInputError(
                f"'{JOINT_POSITION}' has {joints.size} joints, this policy drives {self._policy.dof}"
            )
        return joints
