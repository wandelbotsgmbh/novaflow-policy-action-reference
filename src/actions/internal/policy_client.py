"""Client for a policy server speaking openpi's websocket protocol.

The protocol (openpi ``WebsocketPolicyServer`` / ``WebsocketClientPolicy``):

1. The client opens a websocket to the server.
2. The server sends its **metadata** (one binary frame, a msgpack map).
3. Then, per inference: the client sends an **observation** (msgpack map), the
   server answers with an **action** map. If inference raises, the server sends
   the traceback as a *text* frame and closes the connection.

All frames are msgpack with numpy arrays (:mod:`openpi_msgpack`). One
connection is one episode; a server that keeps per-episode state resets it
per connection.

What this repo puts in those maps (the only contract a policy server has to
meet; ``policies/dummy`` is a complete example):

========================  =====================================================
metadata ``dof``          int, joints the policy drives
metadata ``dt_ms``        float, the policy's step time: one inference per step
observation               ``observation/joint_position`` float32 ``[dof]``
                          ``observation/joint_velocity`` float32 ``[dof]``
                          ``prompt`` str, only if the action was given one
action ``actions``        float32 ``[horizon, dof]``: absolute joint targets in
                          rad. Only the first row is executed, so a horizon-1
                          policy returns ``[1, dof]``.
========================  =====================================================

The key names follow openpi's DROID example.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import loguru
import numpy as np
import websockets
from numpy.typing import NDArray

from src.actions.internal import openpi_msgpack

logger = loguru.logger


class PolicyError(RuntimeError):
    """The policy server is unreachable, misbehaves, or failed to infer."""


@dataclass(frozen=True)
class PolicyMetadata:
    dof: int
    dt_ms: float
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: Any) -> PolicyMetadata:
        if not isinstance(raw, dict):
            raise PolicyError(
                f"Policy metadata must be a map, got {type(raw).__name__}"
            )
        try:
            dof, dt_ms = int(raw["dof"]), float(raw["dt_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyError(
                f"Policy metadata needs 'dof' and 'dt_ms', got {raw}"
            ) from exc
        if dof <= 0 or dt_ms <= 0:
            raise PolicyError(f"Policy metadata out of range: dof={dof}, dt_ms={dt_ms}")
        return cls(dof=dof, dt_ms=dt_ms, raw=raw)


class WebsocketPolicyClient:
    """One episode's connection to an openpi-style policy server.

    Args:
        url: ``ws://host:port``, e.g. ``ws://app-dummy-policy:8080`` in-cluster.
        timeout_s: Deadline for connecting and for each inference.
    """

    def __init__(self, url: str, *, timeout_s: float = 5.0) -> None:
        self._url = url
        self._timeout_s = timeout_s
        self._ws: Any = None
        self.metadata: PolicyMetadata | None = None

    async def connect(self) -> PolicyMetadata:
        try:
            self._ws = await websockets.connect(
                self._url,
                compression=None,
                max_size=None,
                open_timeout=self._timeout_s,
                close_timeout=1,  # not websockets' 10 s: ending an episode waited on it
            )
            self.metadata = PolicyMetadata.parse(
                openpi_msgpack.unpackb(await self._recv())
            )
        except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as exc:
            raise PolicyError(
                f"Cannot reach policy server at {self._url}: {exc}"
            ) from exc
        logger.info(f"Policy server {self._url}: {self.metadata.raw}")
        return self.metadata

    async def infer(self, observation: dict[str, Any]) -> NDArray[np.float32]:
        """Send one observation, return the first action row (``[dof]``)."""
        assert self.metadata is not None, "connect() first"
        await self._ws.send(openpi_msgpack.packb(observation))
        response = await self._recv()
        if isinstance(
            response, str
        ):  # openpi: a text frame carries the server's traceback
            raise PolicyError(f"Policy server failed to infer:\n{response}")
        actions = np.asarray(
            openpi_msgpack.unpackb(response)["actions"], dtype=np.float32
        )
        if actions.ndim == 1:
            actions = actions[None, :]
        if actions.ndim != 2 or actions.shape[1] != self.metadata.dof:
            raise PolicyError(
                f"Policy returned actions of shape {actions.shape}, expected (horizon, {self.metadata.dof})"
            )
        return actions[0]

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _recv(self) -> bytes | str:
        try:
            return await asyncio.wait_for(self._ws.recv(), self._timeout_s)
        except TimeoutError as exc:
            raise PolicyError(
                f"Policy server did not answer within {self._timeout_s}s"
            ) from exc
        except websockets.exceptions.ConnectionClosed as exc:
            raise PolicyError(f"Policy server closed the connection: {exc}") from exc
