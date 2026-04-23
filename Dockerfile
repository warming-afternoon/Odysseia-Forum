FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY bot_main.py ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY docs ./docs
COPY config.example.json ./
COPY migrate.py ./
COPY migrate_to_multi_server.py ./
COPY start.sh ./
COPY entrypoint.sh ./

RUN uv sync --locked --no-dev

RUN mkdir -p /app/data

RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
