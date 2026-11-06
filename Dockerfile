# --- 1. Mini App frontend (Vite) ---
FROM node:22-alpine AS webapp
WORKDIR /w
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webapp/ ./
RUN npm run build

# --- 2. Python: bot + API ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install -e .

COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY --from=webapp /w/dist ./webapp/dist

RUN useradd --uid 1000 --create-home bot && mkdir -p /data && chown bot:bot /data
USER bot

CMD ["sh", "-c", "alembic upgrade head && python -m app.bot"]
