"""PolicyService — execute learned policies as NovaFlow actions."""

import loguru
from novaflow.core import ActionService

from src.actions import single_arm_policy_router

SERVICE_NAME = "PolicyService"
logger = loguru.logger

# Create the service
service = ActionService(
    name=SERVICE_NAME,
    version="1.0.0",
    icon="box",
    description="Run a remote policy (openpi websocket server) on a robot",
)

# Include all routers
service.include_router(single_arm_policy_router)

# Run service
if __name__ == "__main__":
    logger.info(f"Starting {SERVICE_NAME}...")
    service.run()
