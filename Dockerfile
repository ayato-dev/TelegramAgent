# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.23-python3.13-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dependencies first: this layer is reused until uv.lock changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable


FROM python:3.13-slim-trixie
RUN groupadd --system --gid 999 app \
 && useradd --system --gid 999 --uid 999 --create-home app \
 && install -d -o app -g app /app/data
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app
# The SQLite database (the default) lives here: mount a volume on /app/data. Webhook mode listens on 8080.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/tmp/tgagent-heartbeat') > 120)"]

# Single instance: apply migrations, then replace the shell with the bot process.
CMD ["sh", "-c", "alembic upgrade head && exec python -m tgagent"]
