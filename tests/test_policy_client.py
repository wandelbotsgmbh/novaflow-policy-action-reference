"""``WebsocketPolicyClient`` against a real in-process websocket server speaking
openpi's protocol: metadata on connect, observation in, actions out, a text
frame with the traceback on a server error."""

import numpy as np
import pytest
import websockets

from src.actions.internal import openpi_msgpack
from src.actions.internal.policy_client import (
    PolicyError,
    PolicyMetadata,
    WebsocketPolicyClient,
)


async def _serve(metadata, respond):
    async def handler(ws):
        await ws.send(openpi_msgpack.packb(metadata))
        async for raw in ws:
            await ws.send(respond(openpi_msgpack.unpackb(raw)))

    server = await websockets.serve(
        handler, "127.0.0.1", 0, compression=None, max_size=None
    )
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


async def test_round_trip_returns_the_first_action_row():
    seen = []

    def respond(obs):
        seen.append(obs)
        q = obs["observation/joint_position"]
        return openpi_msgpack.packb({"actions": np.stack([q + 0.1, q + 0.2])})

    server, url = await _serve({"dof": 3, "dt_ms": 66.7, "name": "test"}, respond)
    client = WebsocketPolicyClient(url)
    try:
        metadata = await client.connect()
        assert (metadata.dof, metadata.dt_ms) == (3, 66.7)

        q = np.array([0.0, 1.0, 2.0], dtype=np.float32)
        target = await client.infer({"observation/joint_position": q, "prompt": "wave"})

        np.testing.assert_allclose(target, q + 0.1, rtol=1e-6)
        assert seen[0]["prompt"] == "wave"
        assert seen[0]["observation/joint_position"].dtype == np.float32
    finally:
        await client.close()
        server.close()


async def test_server_traceback_becomes_a_policy_error():
    server, url = await _serve({"dof": 1, "dt_ms": 50}, lambda obs: "Traceback: boom")
    client = WebsocketPolicyClient(url)
    try:
        await client.connect()
        with pytest.raises(PolicyError, match="boom"):
            await client.infer(
                {"observation/joint_position": np.zeros(1, dtype=np.float32)}
            )
    finally:
        await client.close()
        server.close()


async def test_wrong_action_width_is_rejected():
    server, url = await _serve(
        {"dof": 2, "dt_ms": 50},
        lambda obs: openpi_msgpack.packb({"actions": np.zeros((1, 3))}),
    )
    client = WebsocketPolicyClient(url)
    try:
        await client.connect()
        with pytest.raises(PolicyError, match="expected"):
            await client.infer(
                {"observation/joint_position": np.zeros(2, dtype=np.float32)}
            )
    finally:
        await client.close()
        server.close()


async def test_unreachable_server_is_a_policy_error():
    with pytest.raises(PolicyError, match="Cannot reach"):
        await WebsocketPolicyClient("ws://127.0.0.1:9", timeout_s=1).connect()


@pytest.mark.parametrize(
    "raw", [{"dt_ms": 50}, {"dof": 6}, {"dof": 0, "dt_ms": 50}, [1, 2]]
)
def test_metadata_needs_dof_and_dt(raw):
    with pytest.raises(PolicyError):
        PolicyMetadata.parse(raw)


def test_codec_matches_openpi_byte_layout():
    """openpi-client packs arrays as {b"__ndarray__", b"data", b"dtype", b"shape"}."""
    import msgpack

    packed = openpi_msgpack.packb({"a": np.arange(3, dtype=np.float32)})
    raw = msgpack.unpackb(packed)["a"]
    assert (
        raw[b"__ndarray__"] is True and raw[b"dtype"] == "<f4" and raw[b"shape"] == [3]
    )
    np.testing.assert_array_equal(
        openpi_msgpack.unpackb(packed)["a"], np.arange(3, dtype=np.float32)
    )
