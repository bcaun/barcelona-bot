"""FastAPI app: Mini App API (/api/...) + the built mini app (webapp/dist) on the same domain.

Run: uvicorn app.api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.quiz_api import QuizDeps
from app.api.quiz_api import router as quiz_router
from app.config import ROOT_DIR, get_env, get_settings, load_yaml
from app.core.quiz import load_bank

WEBAPP_DIST = ROOT_DIR / "webapp" / "dist"


class CachedStatic(StaticFiles):
    """Hashed assets are cached for a year; index.html is always revalidated."""

    async def get_response(self, path, scope):  # noqa: ANN001, ANN201
        resp = await super().get_response(path, scope)
        if path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(quiz: QuizDeps | None = None, static_dir: Path | None = WEBAPP_DIST) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        resources = []
        if quiz is None:
            app.state.quiz, resources = await _deps_from_env()
        yield
        for close in resources:
            await close()

    app = FastAPI(title="barcelona.ru quiz API", docs_url=None, redoc_url=None, lifespan=lifespan)
    if quiz is not None:
        app.state.quiz = quiz

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(quiz_router)
    if static_dir is not None and static_dir.is_dir():
        app.mount("/", CachedStatic(directory=static_dir, html=True), name="webapp")
    return app


async def _deps_from_env():  # noqa: ANN202
    from aiogram import Bot

    from app.core.db import make_engine, make_session_factory
    from app.integrations.telegram_channel import SubscriptionChecker
    from app.logging_setup import setup_logging

    env, settings = get_env(), get_settings()
    setup_logging(env.log_level)
    engine = make_engine(env.database_url)
    token = env.bot_token.get_secret_value()
    bot = Bot(token)
    me = await bot.get_me()
    subs = SubscriptionChecker(bot, env.channel_id, settings.channel.subscription_cache_seconds)
    deps = QuizDeps(
        settings=settings,
        bot_token=token,
        session_factory=make_session_factory(engine),
        bank=load_bank(load_yaml("quiz.yaml")),
        is_subscribed=subs.is_subscribed,
        bot_username=me.username or "",
    )
    return deps, [bot.session.close, engine.dispose]


app = create_app()
