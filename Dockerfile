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

RUN useradd --uid 1000 --create-home bot && mkdir -p /data && chown bot:bot /data
USER bot

CMD ["sh", "-c", "alembic upgrade head && python -m app.bot"]
