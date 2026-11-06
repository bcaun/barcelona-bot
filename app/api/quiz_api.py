"""Mini App API: quiz «Do you know housing in Barcelona?».

Auth: every request carries `Authorization: tma <initData>` — Telegram WebApp init data,
validated with HMAC by the bot token and max age (SPEC 7.4). The server keeps the game
state; the client never receives which option is correct before it answers.
"""

from __future__ import annotations

import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import structlog
from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core import quiz as qz
from app.core import repo
from app.core.models import Lead, QuizSession, User, utcnow
from app.i18n import pick_language

log = structlog.get_logger(__name__)
INIT_DATA_MAX_AGE = timedelta(hours=24)


@dataclass
class QuizDeps:
    settings: Settings
    bot_token: str
    session_factory: async_sessionmaker[AsyncSession]
    bank: dict[str, qz.Question]
    is_subscribed: Any  # async (user_id) -> bool
    bot_username: str
    rng: random.Random = field(default_factory=random.SystemRandom)
    clock: Any = time.monotonic
    _answers: dict[int, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def allow_answer(self, user_id: int) -> bool:
        now = self.clock()
        hits = self._answers[user_id]
        while hits and now - hits[0] >= 3600:
            hits.popleft()
        if len(hits) >= self.settings.quiz.max_answers_per_hour:
            return False
        hits.append(now)
        return True


def deps(request: Request) -> QuizDeps:
    return request.app.state.quiz


async def db(d: Annotated[QuizDeps, Depends(deps)]):  # noqa: ANN201
    """One session per request. Endpoints commit explicitly before responding: code after
    `yield` may run after the response is sent, so it must not be relied on for writes."""
    async with d.session_factory() as session:
        yield session


def verify_init_data(init_data: str, bot_token: str, now: datetime | None = None) -> WebAppInitData:
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as e:
        raise HTTPException(401, "invalid init data") from e
    auth_date = data.auth_date if data.auth_date.tzinfo else data.auth_date.replace(tzinfo=UTC)
    if (now or datetime.now(UTC)) - auth_date > INIT_DATA_MAX_AGE:
        raise HTTPException(401, "init data expired")
    if data.user is None:
        raise HTTPException(401, "no user")
    return data


@dataclass
class Player:
    user: User
    lang: str
    display_name: str


async def player(
    d: Annotated[QuizDeps, Depends(deps)],
    session: Annotated[AsyncSession, Depends(db)],
    authorization: Annotated[str, Header()] = "",
) -> Player:
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "tma" or not raw:
        raise HTTPException(401, "missing init data")
    data = verify_init_data(raw, d.bot_token)
    tg = data.user
    assert tg is not None
    i18n = d.settings.i18n
    lang = pick_language(tg.language_code, i18n.supported, i18n.ru_like, i18n.default)
    user, created = await repo.get_or_create_user(session, tg.id, tg.username, lang)
    if data.start_param:  # t.me/<bot>/<app>?startapp=ref_123 — invitations count as source
        repo.apply_source(user, repo.clean_source(data.start_param))
    if created:
        await repo.log_event(session, user.id, "game:first_open", None, source=user.source)
    name = (tg.first_name or "").strip()[:30] or "—"
    if tg.last_name:
        name += f" {tg.last_name.strip()[:1]}."
    return Player(user=user, lang=user.language if user.language_manual else lang,
                  display_name=name)  # fmt: skip


def today(settings: Settings) -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")


def week_start_utc(settings: Settings) -> datetime:
    local = datetime.now(ZoneInfo(settings.timezone))
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday.astimezone(UTC)


def _question_json(pq: qz.PublicQuestion | None) -> dict[str, Any] | None:
    if pq is None:
        return None
    return {"number": pq.number, "total": pq.total, "id": pq.id, "topic": pq.topic,
            "text": pq.text, "options": pq.options}  # fmt: skip


async def _prize_claimed(session: AsyncSession, user_id: int) -> bool:
    n = await session.scalar(
        select(func.count()).select_from(Lead).where(Lead.user_id == user_id,
                                                     Lead.lead_type == "game")
    )  # fmt: skip
    return bool(n)


def share_url(d: QuizDeps, user_id: int) -> str:
    return f"https://t.me/{d.bot_username}/{d.settings.quiz.app_short_name}?startapp=ref_{user_id}"


def claim_url(d: QuizDeps) -> str:
    return f"https://t.me/{d.bot_username}?start=prize"


router = APIRouter(prefix="/api")


class AnswerIn(BaseModel):
    position: int
    choice: int


@router.get("/me")
async def me(
    d: Annotated[QuizDeps, Depends(deps)],
    p: Annotated[Player, Depends(player)],
    session: Annotated[AsyncSession, Depends(db)],
) -> dict[str, Any]:
    uid = p.user.id
    sessions = (await session.scalars(select(QuizSession).where(QuizSession.user_id == uid))).all()
    day = today(d.settings)
    today_sessions = [s for s in sessions if s.local_date == day]
    active = next((s for s in today_sessions if s.finished_at is None), None)
    finished = [s for s in sessions if s.finished_at is not None]
    won_ever = any(s.won for s in finished)
    claimed = await _prize_claimed(session, uid)
    subscribed = await d.is_subscribed(uid)
    await session.commit()  # user registration / source
    return {
        "lang": p.lang,
        "name": p.display_name,
        "total": d.settings.quiz.questions_per_game,
        "lives": d.settings.quiz.lives,
        "attempts_left": max(
            0, d.settings.quiz.attempts_per_day - sum(1 for s in today_sessions if s.finished_at)
        ),
        "active_session": active.id if active else None,
        "best_score": max((s.score for s in finished), default=None),
        "games_played": len(finished),
        "won_ever": won_ever,
        "prize_claimed": claimed,
        "claim_url": claim_url(d) if won_ever and not claimed else None,
        "subscribed": subscribed,
        "channel_url": d.settings.channel.url,
        "share_url": share_url(d, uid),
        "prize_requires_subscription": d.settings.quiz.prize_requires_subscription,
    }


@router.post("/quiz/start")
async def start(
    d: Annotated[QuizDeps, Depends(deps)],
    p: Annotated[Player, Depends(player)],
    session: Annotated[AsyncSession, Depends(db)],
) -> dict[str, Any]:
    uid, day = p.user.id, today(d.settings)
    today_sessions = (await session.scalars(
        select(QuizSession).where(QuizSession.user_id == uid, QuizSession.local_date == day)
    )).all()  # fmt: skip
    active = next((s for s in today_sessions if s.finished_at is None), None)
    if active is None:
        if len(today_sessions) >= d.settings.quiz.attempts_per_day:
            raise HTTPException(409, "already_played_today")
        game = qz.new_game(d.bank, d.settings.quiz.questions_per_game, d.settings.quiz.lives,
                           d.rng)  # fmt: skip
        active = QuizSession(id=str(uuid.uuid4()), user_id=uid, local_date=day,
                             state=game.to_dict(), started_at=utcnow(),
                             display_name=p.display_name)  # fmt: skip
        session.add(active)
        await repo.log_event(session, uid, "game:start", {"session": active.id},
                             source=p.user.last_source or p.user.source)  # fmt: skip
    game = qz.Game.from_dict(active.state)
    await session.commit()
    return {
        "session_id": active.id,
        "total": game.total,
        "lives": game.lives,
        "score": game.correct,
        "question": _question_json(qz.current_question(game, d.bank, p.lang)),
    }


@router.post("/quiz/{session_id}/answer")
async def submit_answer(
    session_id: str,
    body: AnswerIn,
    d: Annotated[QuizDeps, Depends(deps)],
    p: Annotated[Player, Depends(player)],
    session: Annotated[AsyncSession, Depends(db)],
) -> dict[str, Any]:
    qs = await session.get(QuizSession, session_id)
    if qs is None or qs.user_id != p.user.id:
        raise HTTPException(404, "session not found")
    if not d.allow_answer(p.user.id):
        raise HTTPException(429, "too many answers")
    game = qz.Game.from_dict(qs.state)
    try:
        r = qz.answer(game, d.bank, body.position, body.choice, p.lang)
    except qz.InvalidAnswer as e:
        raise HTTPException(409, str(e)) from e
    qs.state = game.to_dict()
    qs.score = game.correct
    out: dict[str, Any] = {
        "correct": r.correct,
        "correct_index": r.correct_index,
        "explain": r.explain,
        "tool": r.tool,
        "lives": r.lives,
        "score": r.score,
        "finished": r.finished,
        "next": _question_json(qz.current_question(game, d.bank, p.lang)),
    }
    if r.finished:
        qs.finished_at = utcnow()
        qs.won = r.won
        qs.subscribed_at_finish = await d.is_subscribed(p.user.id)
        await session.flush()
        week = (await session.scalars(
            select(QuizSession.score).where(QuizSession.finished_at >= week_start_utc(d.settings))
        )).all()  # fmt: skip
        claimed = await _prize_claimed(session, p.user.id)
        out["result"] = {
            "score": r.score,
            "total": game.total,
            "won": r.won,
            "better_than_pct": qz.percentile(r.score, list(week)),
            "in_leaderboard": qs.subscribed_at_finish,
            "claim_url": claim_url(d) if r.won and not claimed else None,
            "share_url": share_url(d, p.user.id),
        }
        await repo.log_event(session, p.user.id, "game:finish",
                             {"session": qs.id, "score": r.score, "won": r.won},
                             source=p.user.last_source or p.user.source)  # fmt: skip
        log.info("quiz_finished", user_id=p.user.id, score=r.score, won=r.won)
    await session.commit()
    return out


@router.get("/leaderboard")
async def leaderboard(
    d: Annotated[QuizDeps, Depends(deps)],
    p: Annotated[Player, Depends(player)],
    session: Annotated[AsyncSession, Depends(db)],
) -> dict[str, Any]:
    """This week's best result per player; only channel subscribers are ranked (SPEC 7.2)."""
    rows = (await session.scalars(
        select(QuizSession).where(
            QuizSession.finished_at >= week_start_utc(d.settings),
            QuizSession.subscribed_at_finish.is_(True),
        )
    )).all()  # fmt: skip
    best: dict[int, QuizSession] = {}

    def key(s: QuizSession) -> tuple[int, float]:
        assert s.finished_at is not None
        return (-s.score, (s.finished_at - s.started_at).total_seconds())

    for s in rows:
        if s.user_id not in best or key(s) < key(best[s.user_id]):
            best[s.user_id] = s
    ranked = sorted(best.values(), key=key)
    top = [
        {"rank": i + 1, "name": s.display_name or "—", "score": s.score, "won": s.won,
         "me": s.user_id == p.user.id}
        for i, s in enumerate(ranked[: d.settings.quiz.leaderboard_size])
    ]  # fmt: skip
    my_rank = next((i + 1 for i, s in enumerate(ranked) if s.user_id == p.user.id), None)
    return {"top": top, "my_rank": my_rank, "players": len(ranked),
            "total": d.settings.quiz.questions_per_game}  # fmt: skip
