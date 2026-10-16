"""The server against a plain websocket client, the way openpi-client talks to it."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import urllib.request

import numpy as np
import pytest
import websockets

from dummy_policy import openpi_msgpack
from dummy_policy.policy import JOINT_POSITION, DummyPolicy
from dummy_policy.server import PolicyServer


def _get(url: str) -> bytes:
    return urllib.request.urlopen(url, timeout=2).read()


@pytest.fixture
async def url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    task = asyncio.create_task(PolicyServer(DummyPolicy(dof=3), "127.0.0.1", port).run())
    for _ in range(100):  # ready once the health check answers
        try:
            await asyncio.to_thread(_get, f"http://127.0.0.1:{port}/healthz")
            break
        except OSError:
            await asyncio.sleep(0.02)
    yield f"ws://127.0.0.1:{port}"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_metadata_then_actions(url):
    async with websockets.connect(url) as ws:
        metadata = openpi_msgpack.unpackb(await ws.recv())
        assert metadata["dof"] == 3

        await ws.send(openpi_msgpack.packb({JOINT_POSITION: np.array([0.1, 0.2, 0.3], dtype=np.float32)}))
        actions = openpi_msgpack.unpackb(await ws.recv())["actions"]
        np.testing.assert_allclose(actions, [[0.1, 0.2, 0.3]], atol=1e-6)


async def test_inference_error_is_a_traceback_text_frame_then_close(url):
    async with websockets.connect(url) as ws:
        await ws.recv()
        await ws.send(openpi_msgpack.packb({}))
        reply = await ws.recv()
        assert isinstance(reply, str) and "missing" in reply
        with pytest.raises(websockets.ConnectionClosedError) as closed:
            await ws.recv()
        assert closed.value.rcvd.code == 1011


async def test_health_check(url):
    assert await asyncio.to_thread(_get, url.replace("ws://", "http://") + "/healthz") == b"OK\n"
