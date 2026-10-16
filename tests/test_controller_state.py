"""Controller state stream folding and the controller clock (no sockets)."""

import numpy as np
import pytest

from src.actions.internal.controller_clock import ControllerClock
from src.actions.internal.controller_state import ControllerStateStream, _timestamp_s

MG = "0@ur5e"


def _stream() -> ControllerStateStream:
    return ControllerStateStream("http://10.0.0.1:8080", None, "cell", MG)


def _message(
    joints, stamp="2026-10-15T00:00:00Z", safety="SAFETY_STATE_NORMAL", **group
) -> dict:
    return {
        "result": {
            "safety_state": safety,
            "motion_groups": [
                {
                    "motion_group": MG,
                    "joint_position": joints,
                    "timestamp": stamp,
                    **group,
                }
            ],
        }
    }


def test_url_targets_the_controller_state_stream():
    stream = ControllerStateStream("https://nova.example.com", "tok", "cell", MG)
    assert (
        stream._url
        == "wss://nova.example.com/api/v2/cells/cell/controllers/ur5e/state-stream?response_rate=8"
    )
    assert stream._headers == {"Authorization": "Bearer tok"}


def test_joints_safety_and_standstill_are_folded_in():
    stream = _stream()
    stream.handle(
        _message([0.1, 0.2], safety="SAFETY_STATE_PROTECTIVE_STOP", standstill=True),
        5.0,
    )

    sample = stream.sample
    np.testing.assert_allclose(sample.joints, [0.1, 0.2])
    assert (sample.received_s, sample.standstill) == (5.0, True)
    assert stream.safety_state == "SAFETY_STATE_PROTECTIVE_STOP"


def test_velocities_are_differentiated_over_controller_time():
    """NOVA 26.8 sends no joint_velocity; arrival times (all 1.0 here) don't matter."""
    stream = _stream()
    stream.handle(_message([0.0], "2026-10-15T00:00:00.000Z"), 1.0)
    stream.handle(_message([0.008], "2026-10-15T00:00:00.008Z"), 1.0)
    assert stream.sample.velocities is None  # under the 16 ms baseline
    stream.handle(_message([0.016], "2026-10-15T00:00:00.016Z"), 1.0)
    np.testing.assert_allclose(stream.sample.velocities, [1.0], rtol=1e-4)


def test_session_clock_reports_arrive_as_strings():
    stream = _stream()
    stream.sample.clock.start(10.0)
    execute = {"details": {"jogger_session_timestamp_ms": "500"}}
    stream.handle(_message([0.0], execute=execute), 10.52)
    assert stream.sample.clock.offset_ms == pytest.approx(500 - 10_520)


def test_jogging_pause_is_tracked_until_running_again():
    stream = _stream()

    def jog(kind, received_s):
        stream.handle(
            _message([0.0], execute={"details": {"state": {"kind": kind}}}), received_s
        )

    jog("RUNNING", 1.0)
    jog("PAUSED_NEAR_JOINT_LIMIT", 2.0)
    jog("PAUSED_NEAR_JOINT_LIMIT", 2.5)
    assert stream.sample.paused_since_s == 2.0
    jog("RUNNING", 3.0)
    assert stream.sample.paused_since_s is None


def test_other_motion_groups_are_ignored():
    stream = _stream()
    stream.handle(
        {
            "result": {
                "motion_groups": [{"motion_group": "1@ur5e", "joint_position": [9.0]}]
            }
        },
        1.0,
    )
    assert stream.sample.joints is None


def test_timestamp_parsing_keeps_sub_second_digits():
    delta = _timestamp_s("2026-10-15T18:49:23.954470568Z") - _timestamp_s(
        "2026-10-15T18:49:23.946470568Z"
    )
    assert delta == pytest.approx(0.008, abs=1e-6)


# -- ControllerClock ---------------------------------------------------------


def test_clock_ignores_reports_before_its_session_starts():
    """At connect NOVA still reports the previous session's frozen clock."""
    clock = ControllerClock()
    clock.observe(87_000, received_s=5.0)
    assert clock.offset_ms is None


def test_clock_ignores_a_stale_report_far_ahead_after_start():
    clock = ControllerClock()
    clock.start(10.0)
    clock.observe(87_000, received_s=10.01)
    assert clock.offset_ms == pytest.approx(-10_000.0)


def test_clock_takes_the_least_delayed_report():
    clock = ControllerClock()
    clock.start(10.0)
    clock.observe(100, received_s=10.130)  # 30 ms late
    clock.observe(200, received_s=10.205)  # 5 ms late: the tightest
    clock.observe(300, received_s=10.400)  # a stall must not drag it back
    assert clock.offset_ms == pytest.approx(200 - 10_205)


def test_clock_slews_small_corrections_and_jumps_big_ones():
    clock = ControllerClock()
    clock.start(10.0)
    assert clock.offset_at(10.0) == pytest.approx(-10_000.0)
    clock.observe(20, received_s=10.0)
    assert clock.offset_at(11.0) == pytest.approx(-10_000.0 + 5.0)  # 5 ms/s
    clock.observe(500, received_s=10.0)
    assert clock.offset_at(11.1) == pytest.approx(500 - 10_000.0)  # > 100 ms: at once
