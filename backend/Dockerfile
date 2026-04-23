FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# OpenCV headless needs libgl
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.8.0

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

ENV PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data \
    LIBRARY_DIR=/app/library \
    EXPORTS_DIR=/app/exports \
    DB_PATH=/app/db/keyselector.db

# Single source of truth for the backend port is the BACKEND_PORT env var.
# Override via docker-compose or `docker run -e BACKEND_PORT=...`.
ENV BACKEND_PORT=8000
EXPOSE ${BACKEND_PORT}

# Shell form so $BACKEND_PORT is expanded at container start.
CMD uvicorn main:app --host 0.0.0.0 --port ${BACKEND_PORT}
