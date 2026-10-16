"""Websocket front end, speaking openpi's policy-server protocol.

Mirrors openpi's ``WebsocketPolicyServer`` (``openpi/serving/websocket_policy_server.py``):

1. on connect, send the policy's metadata;
2. then for every observation received, send back the action;
3. if inference raises, send the traceback as a text frame and close with
   code 1011 (internal error), so the client fails loudly instead of timing out.

Every frame is msgpack with numpy arrays (:mod:`openpi_msgpack`). Each
connection is one episode. ``GET /healthz`` answers 200 for probes.

A real policy server keeps this file and replaces ``DummyPolicy``.
"""

from __future__ import annotations

import http
import traceback

import websockets
from loguru import logger
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response

from dummy_policy import openpi_msgpack
from dummy_policy.policy import DummyPolicy


class PolicyServer:
    def __init__(self, policy: DummyPolicy, host: str, port: int) -> None:
        self._policy = policy
        self._host = host
        self._port = port

    async def run(self) -> None:
        async with serve(
            self._handle,
            self._host,
            self._port,
            compression=None,
            max_size=None,
            process_request=_health_check,
        ) as server:
            logger.info(
                f"Serving {self._policy.metadata()} on ws://{self._host}:{self._port}"
            )
            await server.serve_forever()

    async def _handle(self, ws: ServerConnection) -> None:
        logger.info(f"Episode start: {ws.remote_address}")
        episode = self._policy.new_episode()
        await ws.send(openpi_msgpack.packb(self._policy.metadata()))
        try:
            async for raw in ws:
                action = episode.infer(openpi_msgpack.unpackb(raw))
                await ws.send(openpi_msgpack.packb(action))
        except websockets.ConnectionClosed:
            pass
        except Exception:
            logger.exception("Inference failed")
            await ws.send(traceback.format_exc())
            await ws.close(
                code=websockets.frames.CloseCode.INTERNAL_ERROR,
                reason="Internal server error",
            )
            return
        logger.info(f"Episode end after {episode.steps} steps: {ws.remote_address}")


def _health_check(connection: ServerConnection, request: Request) -> Response | None:
    if request.path == "/healthz":
        return connection.respond(http.HTTPStatus.OK, "OK\n")
    return None  # continue with the websocket handshake
