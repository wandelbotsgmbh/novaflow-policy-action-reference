# syntax=docker/dockerfile:1.6
# Build context is this directory only: the policy server is its own container
# and must not reach into the action service's tree.
ARG BASE_IMAGE=astral/uv:python3.12-trixie-slim
FROM ${BASE_IMAGE}
WORKDIR /app

# Reproducible builds: dependencies install with `uv sync --frozen` from the
# committed uv.lock. Transitive versions are pinned with hashes and not
# re-resolved at build time. Run `uv sync` after editing pyproject.toml and
# commit uv.lock; uv fails fast here if the lockfile is missing or out of date.

# Dependencies first so a code edit does not re-resolve the environment.
COPY pyproject.toml uv.lock* README.md /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY dummy_policy /app/dummy_policy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8000

# --no-sync: the environment was built above from the lockfile; never
# re-resolve at container start.
CMD ["uv", "run", "--no-sync", "python", "-m", "dummy_policy"]
