# syntax=docker/dockerfile:1

# ── Build: resolve dependencies into a self-contained virtualenv ─────────────
FROM python:3.14-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first: this layer is reused until uv.lock changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


# ── Runtime: no compilers, no uv, no source tree; just the virtualenv ────────
FROM python:3.14-slim-trixie AS runtime

RUN groupadd --system --gid 10001 hub \
    && useradd --system --uid 10001 --gid hub --no-create-home --shell /usr/sbin/nologin hub \
    && mkdir -p /app/data \
    && chown hub:hub /app/data

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VISION_HUB_APP__HOST=0.0.0.0 \
    VISION_HUB_APP__PORT=8000 \
    VISION_HUB_STORAGE__SNAPSHOTS_DIR=/app/data/snapshots

WORKDIR /app
USER hub
EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]

# Exec form: the server is PID 1 and receives SIGTERM directly for a graceful shutdown.
CMD ["vision-hub", "serve"]
