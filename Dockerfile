# syntax=docker/dockerfile:1
# Багатоетапна збірка за шаблоном https://docs.astral.sh/uv/guides/integration/docker/
#   docker build -t findex .
#   docker run --rm -p 8000:8000 findex                       # індекс уже в образі
#   docker run --rm -p 8000:8000 -e LOG_JSON=false -e LOG_LEVEL=DEBUG findex
#   docker run --rm -p 8000:8000 -v "$PWD/my.bin:/data/my.bin:ro" -e INDEX_PATH=/data/my.bin findex

# --- builder: залежності з uv.lock, потім сам проєкт, потім індекс
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

# bytecode компілюється тут, а не при першому імпорті в контейнері; copy — бо кеш на іншому тому
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Шар залежностей змінюється лише разом з uv.lock — правки коду його не інвалідовують.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# --no-editable: пакет копіюється у .venv, тож runtime-образу src/ не потрібен
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Індекс будується тут, тим самим Python, що й у runtime (pickle), і з POSIX-шляхами в DocMeta.
COPY data/dota2wiki.jsonl ./data/
RUN .venv/bin/findex index data/dota2wiki.jsonl --out index.bin --positions

# Lab 08: ембединги — з репозиторію (findex embed: 4.4 тис. шматків ≈ 3 хв CPU, на безкоштовному
# збирачі — довше), а модель (≈ 90 МБ ONNX) — запікається в образ: runtime стартує без мережі.
# Модель — саме та, якою рахували вектори (записана у файлі); відбиток корпусу перевіряє lifespan.
COPY data/dota2wiki.emb.npz ./data/
RUN .venv/bin/python -c "from findex.semantic import FastEmbedder, SemanticIndex; \
    FastEmbedder(SemanticIndex.load('data/dota2wiki.emb.npz').model, cache_dir='/app/models')"

# --- runtime: лише інтерпретатор, .venv, індекс і корпус (для сніпетів і /docs/{id})
FROM python:3.12-slim

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/index.bin /app/index.bin
COPY --from=builder --chown=app:app /app/data/dota2wiki.jsonl /app/data/dota2wiki.jsonl
COPY --from=builder --chown=app:app /app/data/dota2wiki.emb.npz /app/data/dota2wiki.emb.npz
COPY --from=builder --chown=app:app /app/models /app/models

# Конфіг — лише змінні оточення (12-factor III); будь-яку можна перекрити `docker run -e`.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    INDEX_PATH=/app/index.bin \
    CORPUS_PATH=/app/data/dota2wiki.jsonl \
    EMBEDDINGS_PATH=/app/data/dota2wiki.emb.npz \
    FASTEMBED_CACHE_PATH=/app/models \
    HF_HUB_OFFLINE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    LOG_LEVEL=INFO \
    LOG_JSON=true

USER app
EXPOSE 8000

# Платформи мають власну пробу (/health у render.yaml); це — для `docker run` і compose.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=2)"]

# exec-форма: findex — PID 1 і сам отримує SIGTERM; uvicorn дочікує запити, lifespan закриває індекс.
# PORT читає Settings, тож платформа, що задає свій PORT (Render, Railway), нічого не ламає.
CMD ["findex", "serve"]
