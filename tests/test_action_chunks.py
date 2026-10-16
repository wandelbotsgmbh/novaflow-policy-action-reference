"""``ActionChunkStreamer``: wire messages and stamping rules (lead, spacing, late
drop and fallback, final waypoint). A fake ``ActionChunkStreamingApi`` drives the
real request generator; a fake clock drives time."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.actions.internal.action_chunks import (
    LATE_FALLBACK_S,
    MIN_LEAD_MS,
    ActionChunkStreamer,
)
from src.actions.internal.controller_clock import ControllerClock

MG = "0@ur5e"
Q = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
ACK = SimpleNamespace(kind="INITIALIZE_RECEIVED", message="")


class FakeChunkApi:
    """Stands in for the generated client: records what the generator yields."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.replies: asyncio.Queue = asyncio.Queue()
        self.replies.put_nowait(ACK)

    async def execute_action_chunks(self, cell, controller, client_request_generator):
        async def responses():
            while (reply := await self.replies.get()) is not None:
                yield reply

        async for request in client_request_generator(responses()):
            self.sent.append(json.loads(request.model_dump_json(exclude_none=True)))

    def chunks(self) -> list[dict]:
        return [m for m in self.sent if m["message_type"] == "ActionChunkRequest"]


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


async def _open(lead_ms=100.0):
    chunk_api, now = FakeChunkApi(), Clock()
    streamer = ActionChunkStreamer(
        chunk_api,
        cell="cell",
        controller="ur5e",
        motion_group_id=MG,
        tcp="Flange",
        clock=ControllerClock(),
        lead_ms=lead_ms,
        now=now,
    )
    await streamer.open()
    return streamer, chunk_api, now


async def _flush():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_session_is_initialised_with_motion_group_and_tcp():
    streamer, chunk_api, _ = await _open()
    assert chunk_api.sent[0] == {
        "message_type": "InitializeActionChunksRequest",
        "motion_group": MG,
        "tcp": "Flange",
    }
    await streamer.close()


async def test_a_target_is_one_joint_waypoint_with_delay_until_braking():
    streamer, chunk_api, now = await _open(lead_ms=100.0)

    assert streamer.send_target(Q, sampled_s=now.t)
    await _flush()

    (chunk,) = chunk_api.chunks()
    assert chunk["delay_until_braking"] == 200
    # The first chunk starts the session clock at ~0, so the stamp is just the lead.
    assert chunk["waypoints"] == [
        {"timestamp": 100, "waypoint": {"kind": "JOINTS", "joints": Q}}
    ]
    await streamer.close()


async def test_stamp_is_observation_time_plus_lead_not_send_time():
    streamer, chunk_api, now = await _open(lead_ms=100.0)
    streamer.send_target(Q, sampled_s=now.t)

    now.t += 0.1  # sent 100 ms later, computed from a state that is 30 ms old
    streamer.send_target(Q, sampled_s=now.t - 0.03)
    await _flush()

    assert chunk_api.chunks()[1]["waypoints"][0]["timestamp"] == 100 - 30 + 100
    await streamer.close()


async def test_targets_closer_than_the_spacing_are_thinned():
    streamer, chunk_api, now = await _open()
    streamer.send_target(Q, sampled_s=now.t)

    now.t += 0.02
    assert not streamer.send_target(Q, sampled_s=now.t)  # 20 ms < 40 ms spacing
    now.t += 0.03
    assert streamer.send_target(Q, sampled_s=now.t)
    await _flush()

    assert len(chunk_api.chunks()) == 2 and streamer.stats.thinned == 1
    await streamer.close()


async def test_late_targets_are_dropped_then_forced_after_the_fallback():
    streamer, chunk_api, now = await _open(lead_ms=50.0)
    streamer.send_target(Q, sampled_s=now.t)

    now.t += 0.2  # a state 60 ms old with a 50 ms lead is due in the past: drop
    assert not streamer.send_target(Q, sampled_s=now.t - 0.06)
    assert streamer.stats.late_dropped == 1

    now.t += LATE_FALLBACK_S  # still nothing on time: send it, pulled forward
    assert streamer.send_target(Q, sampled_s=now.t - 0.06)
    await _flush()
    controller_now_ms = (now.t - 100.0) * 1000.0
    assert chunk_api.chunks()[-1]["waypoints"][0]["timestamp"] == int(
        controller_now_ms + MIN_LEAD_MS
    )
    assert streamer.stats.late_forced == 1
    await streamer.close()


async def test_finish_stops_on_the_last_target():
    streamer, chunk_api, now = await _open()
    streamer.send_target(Q, sampled_s=now.t)

    streamer.finish()
    await _flush()

    final = chunk_api.chunks()[-1]
    assert final["delay_until_braking"] == 0
    assert final["waypoints"] == [
        {"timestamp": 100, "waypoint": {"kind": "JOINTS", "joints": Q}}
    ]
    await streamer.close()


async def test_motion_error_fails_the_next_target():
    streamer, chunk_api, now = await _open()
    chunk_api.replies.put_nowait(
        SimpleNamespace(kind="MOTION_ERROR", message="controller dropped")
    )
    await _flush()

    with pytest.raises(RuntimeError, match="controller dropped"):
        streamer.send_target(Q, sampled_s=now.t)
    await streamer.close()


async def test_session_that_dies_at_start_raises():
    class Broken(FakeChunkApi):
        async def execute_action_chunks(self, **_kw):
            raise Exception({"message": "Not Found"})

    streamer = ActionChunkStreamer(
        Broken(),
        cell="cell",
        controller="ur5e",
        motion_group_id=MG,
        tcp="",
        clock=ControllerClock(),
        lead_ms=100,
    )
    with pytest.raises(RuntimeError, match="Not Found"):
        await streamer.open()


def test_lead_below_the_minimum_is_rejected():
    with pytest.raises(ValueError, match="lead_ms"):
        ActionChunkStreamer(
            None,
            cell="",
            controller="",
            motion_group_id=MG,
            tcp="",
            clock=ControllerClock(),
            lead_ms=5,
        )
