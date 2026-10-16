"""Entry point: ``python -m dummy_policy``."""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

from dummy_policy.policy import DummyPolicy
from dummy_policy.server import PolicyServer
from dummy_policy.settings import settings


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level.upper())
    server = PolicyServer(DummyPolicy(dof=settings.dof), settings.host, settings.port)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")


if __name__ == "__main__":
    main()
