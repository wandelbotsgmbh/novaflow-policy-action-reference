# syntax=docker/dockerfile:1.6
# Public base image, no Wandelbots registry access needed. The NovaFlow SDK
# and types install from PyPI via the exact pins in pyproject.toml, which
# alone carry compatibility with the NovaFlow instance (ADR-034/029).
# (This is the same foundation the former novaflow/base image was built on.)
ARG BASE_IMAGE=astral/uv:python3.12-trixie-slim
FROM ${BASE_IMAGE}
WORKDIR /app

# Reproducible builds: dependencies install with `uv sync --frozen` from the
# committed uv.lock. Transitive versions are pinned with hashes and not
# re-resolved at build time, so two builds of the same commit produce the
# same environment. Run `uv sync` after editing pyproject.toml and commit
# uv.lock; uv fails fast here if the lockfile is missing or out of date.
COPY pyproject.toml uv.lock* /app/

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --frozen --no-dev

COPY . /app

# --no-sync: the environment was built above from the lockfile; never
# re-resolve at container start.
CMD ["uv", "run", "--no-sync", "run.py"]
