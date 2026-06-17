# syntax=docker/dockerfile:1.7
# =====================================================================
# Fund Quant Platform — Backend Dockerfile (multi-stage)
#
# Stage layout:
#   1. base      — shared Python runtime (matches pyproject requires-python)
#   2. builder   — compile/install deps into a virtualenv (fast rebuilds)
#   3. runtime   — slim production image (API / worker / beat / CLI)
#   4. dev       — runtime + dev/test extras, used by docker-compose.dev.yml
#
# Same image is reused for api / worker / beat — the compose file picks
# the command (`uvicorn ...` vs `celery ... worker` vs `celery ... beat`).
# =====================================================================

ARG PYTHON_VERSION=3.11

# ---------------------------------------------------------------------
# 1. Base — small Python image with system certs + tzdata + build tools
# ---------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Minimal runtime deps: tzdata for TZ, libpq for psycopg, curl for healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tzdata \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------
# 2. Builder — install Python dependencies into a dedicated virtualenv
# ---------------------------------------------------------------------
FROM base AS builder

# Build-time system packages needed to compile wheels (numpy/pandas/scipy
# usually ship manylinux wheels, but lxml/selectolax/psycopg fall back to
# source on some architectures).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libpq-dev \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Upgrade pip + install a modern build backend before resolving deps.
RUN pip install --upgrade pip setuptools wheel

# Copy dependency descriptor and source together. setuptools'
# packages.find in pyproject requires the `app/` tree to be present in
# order for `pip install .` to succeed.
COPY pyproject.toml ./
COPY app ./app

# Install the project with test + ai extras into the venv. The test
# extras carry dev-only tooling we intentionally keep in builder so the
# final runtime stage can simply copy the venv. Dev stage later extends
# this with editable install + dev extras.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[test,ai]"

# ---------------------------------------------------------------------
# 3. Runtime — minimal image used in production compose
# ---------------------------------------------------------------------
FROM base AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    APP_ENV=production

# Create unprivileged user and required runtime directories up-front so
# volume mounts inherit correct ownership.
RUN groupadd --system --gid 1000 fundquant \
    && useradd --system --uid 1000 --gid fundquant --home-dir /app --shell /usr/sbin/nologin fundquant \
    && mkdir -p /app /app/local_data/snapshots /app/local_data/backups /app/logs \
    && chown -R fundquant:fundquant /app

WORKDIR /app

# Copy the populated virtualenv from builder (no toolchain, no caches).
COPY --from=builder /opt/venv /opt/venv

# Copy the application source.
COPY --chown=fundquant:fundquant app ./app
COPY --chown=fundquant:fundquant pyproject.toml ./

USER fundquant

EXPOSE 8000

# Default command runs the API. Worker / beat containers override this
# via docker-compose `command:`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Healthcheck targets the FastAPI /health endpoint (implemented in
# task 0.3). For worker / beat containers, compose supplies its own
# lightweight healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1

# ---------------------------------------------------------------------
# 4. Dev — layered on top of runtime, adds dev dependencies and loosens
#          permissions so bind-mounted source is usable.
# ---------------------------------------------------------------------
FROM runtime AS dev

USER root

# Re-install the project in editable mode so bind-mounted source
# changes are picked up without a rebuild.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e ".[dev,test,ai]"

# Dev containers keep running as root to avoid bind-mount ownership
# surprises on Windows / WSL. Compose explicitly switches to the app
# user for production runs.
ENV APP_ENV=development

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
