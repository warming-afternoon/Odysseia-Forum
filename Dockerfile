# ============================================================
# base — 仅安装核心依赖，用于正常启动 Bot
# ============================================================
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    LD_PRELOAD=/usr/lib/libjemalloc.so.2 \
    MALLOC_CONF="narenas:2"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl procps libjemalloc2 libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/*/libjemalloc.so.2 /usr/lib/libjemalloc.so.2

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY bot_main.py ./
COPY healthcheck.py ./
COPY api_main.py ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY docs ./docs
COPY config.example.json ./
COPY migrate.py ./
COPY migrate_to_multi_server.py ./
COPY start.sh ./

RUN uv sync --locked --no-dev

RUN mkdir -p /app/data /app/logs

CMD ["uv", "run", "bot_main.py"]

# ============================================================
# dev — 基于 base，额外安装 dev 依赖（pytest、ruff 等）
# ============================================================
FROM base AS dev

RUN uv sync --locked --extra dev
