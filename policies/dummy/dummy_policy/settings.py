"""Deployment settings (env vars). The motion itself is constants in ``policy.py``."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = Field(default="0.0.0.0", description="Interface to listen on.")
    port: int = Field(
        default=8000,
        description="Must match `port` in .nova (the App's container port).",
    )
    dof: int = Field(
        default=6,
        gt=0,
        description="Joints of the robot it drives; the action checks this.",
    )
    log_level: str = Field(default="INFO")

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
