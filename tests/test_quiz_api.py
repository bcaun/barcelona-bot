"""Mini App API end-to-end through ASGI (no network)."""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import select

from app.api.main import create_app
from app.api.quiz_api import QuizDeps
from app.config import load_yaml
from app.core.models import Event, Lead, QuizSession, User
from app.core.quiz import load_bank

TOKEN = "42:TEST"
BANK = load_bank(load_yaml("quiz.yaml"))


def init_data(user_id=500, first="Ana", last="García", lang="ru", start_param=None,
              auth_date=None, token=TOKEN, tamper=False):  # fmt: skip
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAH",
        "user": json.dumps(
            {"id": user_id, "first_name": first, "last_name": last, "language_code": lang},
            separators=(",", ":"),
        ),  # fmt: skip
    }
    if start_param:
        fields["start_param"] = start_param
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = fields["user"].replace(str(user_id), str(user_id + 1))
    return urlencode(fields)


class World:
    def __init__(self, session_factory, settings):
        self.subscribed: set[int] = set()

        async def is_subscribed(uid):
            return uid in self.subscribed

        self.deps = QuizDeps(settings=settings, bot_token=TOKEN, session_factory=session_factory,
                             bank=BANK, is_subscribed=is_subscribed, bot_username="bcn_bot",
                             rng=random.Random(42))  # fmt: skip
        self.app = create_app(self.deps, static_dir=None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url="http://test")  # fmt: skip

    async def call(self, method, path, uid=500, json_body=None, **kw):
        h = {"Authorization": f"tma {init_data(user_id=uid, **kw)}"}
        return await self.client.request(method, path, headers=h, json=json_body)

    def right(self, sid_state, pos):
        return sid_state["orders"][pos].index(0)


@pytest.fixture
async def world(session_factory, settings):
    w = World(session_factory, settings)
    yield w
    await w.client.aclose()


async def _state(session_factory, sid):
    async with session_factory() as s:
        return (await s.get(QuizSession, sid)).state


async def play(world, session_factory, uid=500, mistakes=(), stop_after=None):
    r = await world.call("POST", "/api/quiz/start", uid)
    assert r.status_code == 200, r.text
    sid, q = r.json()["session_id"], r.json()["question"]
    last = None
    while q is not None:
        st = await _state(session_factory, sid)
        pos = q["number"] - 1
        right = world.right(st, pos)
        choice = (right + 1) % 4 if pos in mistakes else right
        last = (await world.call("POST", f"/api/quiz/{sid}/answer", uid,
                                 {"position": pos, "choice": choice})).json()  # fmt: skip
        q = last["next"]
        if stop_after is not None and pos + 1 >= stop_after:
            break
    return sid, last


async def test_auth_required_and_validated(world):
    assert (await world.client.get("/api/me")).status_code == 401
    assert (await world.call("GET", "/api/me", tamper=True)).status_code == 401
    assert (await world.call("GET", "/api/me", token="1:OTHER")).status_code == 401
    old = time.time() - 25 * 3600
    assert (await world.call("GET", "/api/me", auth_date=old)).status_code == 401
    r = await world.call("GET", "/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Ana G." and body["lang"] == "ru" and body["attempts_left"] == 1
    assert body["share_url"] == "https://t.me/bcn_bot/quiz?startapp=ref_500"


async def test_question_never_reveals_answer(world):
    r = await world.call("POST", "/api/quiz/start")
    q = r.json()["question"]
    assert set(q) == {"number", "total", "id", "topic", "text", "options"}
    assert q["number"] == 1 and q["total"] == 25 and len(q["options"]) == 4
    raw = r.text.lower()
    assert "correct" not in raw and "orders" not in raw


async def test_full_win_flow(world, session_factory):
    world.subscribed.add(500)
    sid, last = await play(world, session_factory, mistakes={2, 7})
    res = last["result"]
    assert last["finished"] and res["won"] and res["score"] == 23 and res["total"] == 25
    assert res["claim_url"] == "https://t.me/bcn_bot?start=prize" and res["in_leaderboard"]
    me = (await world.call("GET", "/api/me")).json()
    assert (
        me["won_ever"] and me["claim_url"] and me["attempts_left"] == 0 and me["best_score"] == 23
    )
    lb = (await world.call("GET", "/api/leaderboard")).json()
    assert lb["top"][0] == {"rank": 1, "name": "Ana G.", "score": 23, "won": True, "me": True}
    async with session_factory() as s:
        types = [e.event_type for e in (await s.scalars(select(Event))).all()]
        assert "game:start" in types and "game:finish" in types


async def test_lose_after_three_mistakes(world, session_factory):
    sid, last = await play(world, session_factory, mistakes={0, 1, 2})
    assert last["finished"] and last["lives"] == 0 and not last["result"]["won"]
    assert last["result"]["claim_url"] is None
    assert last["next"] is None


async def test_answer_feedback_and_double_submit(world, session_factory):
    r = await world.call("POST", "/api/quiz/start")
    sid = r.json()["session_id"]
    st = await _state(session_factory, sid)
    right = world.right(st, 0)
    a = (await world.call("POST", f"/api/quiz/{sid}/answer", json_body={
        "position": 0, "choice": (right + 1) % 4})).json()  # fmt: skip
    assert a["correct"] is False and a["correct_index"] == right and a["lives"] == 2
    assert a["explain"] and a["next"]["number"] == 2
    again = await world.call("POST", f"/api/quiz/{sid}/answer",
                             json_body={"position": 0, "choice": right})  # fmt: skip
    assert again.status_code == 409
    other = await world.call("POST", f"/api/quiz/{sid}/answer", uid=501,
                             json_body={"position": 1, "choice": 0})  # fmt: skip
    assert other.status_code == 404  # someone else's session


async def test_resume_and_daily_limit(world, session_factory):
    r1 = await world.call("POST", "/api/quiz/start")
    r2 = await world.call("POST", "/api/quiz/start")  # resume the unfinished one
    assert r1.json()["session_id"] == r2.json()["session_id"]
    await play(world, session_factory, uid=600, mistakes={0, 1, 2})
    assert (await world.call("POST", "/api/quiz/start", uid=600)).status_code == 409


async def test_rate_limit(world, settings):
    settings.quiz.max_answers_per_hour = 2
    r = await world.call("POST", "/api/quiz/start")
    sid = r.json()["session_id"]
    codes = [(await world.call("POST", f"/api/quiz/{sid}/answer",
                               json_body={"position": i, "choice": 0})).status_code
             for i in range(3)]  # fmt: skip
    assert codes[2] == 429


async def test_leaderboard_only_subscribers(world, session_factory):
    world.subscribed.add(700)
    await play(world, session_factory, uid=700, mistakes={0, 1, 2})
    await play(world, session_factory, uid=701, mistakes={5})  # better, but not subscribed
    lb = (await world.call("GET", "/api/leaderboard", uid=701)).json()
    assert [row["score"] for row in lb["top"]] == [0] and lb["my_rank"] is None


async def test_referral_source(world, session_factory):
    await world.call("GET", "/api/me", uid=800, start_param="ref_500")
    async with session_factory() as s:
        assert (await s.get(User, 800)).source == "ref_500"


async def test_prize_claimed_hides_claim_url(world, session_factory):
    await play(world, session_factory, uid=900)
    async with session_factory() as s:
        from app.core.models import utcnow

        s.add(Lead(user_id=900, lead_type="game", consent_at=utcnow()))
        await s.commit()
    me = (await world.call("GET", "/api/me", uid=900)).json()
    assert me["won_ever"] and me["prize_claimed"] and me["claim_url"] is None
