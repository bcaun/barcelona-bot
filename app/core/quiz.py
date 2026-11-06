"""Quiz «Do you know housing in Barcelona?» — pure game logic (no web, no DB).

The correct option is always index 0 in config; each game gets its own random order of
options per question, so the client never learns which index is right before answering.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Question:
    id: str
    topic: str
    text: dict[str, str]
    options: tuple[dict[str, str], ...]  # options[0] is correct
    explain: dict[str, str]
    tool: str | None = None

    def t(self, part: dict[str, str], lang: str) -> str:
        return part.get(lang) or part.get("ru") or next(iter(part.values()), "")


class QuizConfigError(ValueError):
    pass


def load_bank(data: dict[str, Any]) -> dict[str, Question]:
    bank: dict[str, Question] = {}
    for raw in data.get("questions") or []:
        qid = str(raw.get("id", ""))
        wrong = raw.get("wrong") or []
        if not qid or qid in bank:
            raise QuizConfigError(f"quiz.yaml: empty or duplicate id {qid!r}")
        if len(wrong) != 3 or not raw.get("correct"):
            raise QuizConfigError(f"quiz.yaml: {qid} needs 1 correct and exactly 3 wrong options")
        if raw.get("tool") not in (None, "calc", "check"):
            raise QuizConfigError(f"quiz.yaml: {qid} tool must be calc or check")
        bank[qid] = Question(
            id=qid,
            topic=str(raw.get("topic", "")),
            text=dict(raw["q"]),
            options=(dict(raw["correct"]), *[dict(w) for w in wrong]),
            explain=dict(raw.get("explain") or {}),
            tool=raw.get("tool"),
        )
    return bank


@dataclass
class Game:
    """Serializable game state (stored as JSON in the DB)."""

    question_ids: list[str]
    orders: list[list[int]]  # orders[i][display_index] = config option index (0 = correct)
    lives: int
    position: int = 0
    correct: int = 0
    answers: list[int] = field(default_factory=list)  # chosen display indexes

    @property
    def total(self) -> int:
        return len(self.question_ids)

    @property
    def finished(self) -> bool:
        return self.lives <= 0 or self.position >= self.total

    @property
    def won(self) -> bool:
        return self.position >= self.total and self.lives > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_ids": self.question_ids,
            "orders": self.orders,
            "lives": self.lives,
            "position": self.position,
            "correct": self.correct,
            "answers": self.answers,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Game:
        return cls(**d)


def new_game(bank: dict[str, Question], n: int, lives: int, rng: random.Random) -> Game:
    ids = rng.sample(sorted(bank), k=min(n, len(bank)))
    orders = []
    for _ in ids:
        order = [0, 1, 2, 3]
        rng.shuffle(order)
        orders.append(order)
    return Game(question_ids=ids, orders=orders, lives=lives)


@dataclass(frozen=True)
class PublicQuestion:
    """What the client may see before answering: no hint of the correct option."""

    number: int  # 1-based
    total: int
    id: str
    topic: str
    text: str
    options: list[str]


def current_question(game: Game, bank: dict[str, Question], lang: str) -> PublicQuestion | None:
    if game.finished:
        return None
    q = bank[game.question_ids[game.position]]
    order = game.orders[game.position]
    return PublicQuestion(
        number=game.position + 1, total=game.total, id=q.id, topic=q.topic,
        text=q.t(q.text, lang), options=[q.t(q.options[i], lang) for i in order],
    )  # fmt: skip


@dataclass(frozen=True)
class AnswerResult:
    correct: bool
    correct_index: int  # display index of the right option
    explain: str
    tool: str | None
    lives: int
    score: int
    finished: bool
    won: bool


class InvalidAnswer(ValueError):
    pass


def answer(game: Game, bank: dict[str, Question], position: int, choice: int,
           lang: str) -> AnswerResult:  # fmt: skip
    """Apply an answer. `position` guards against double submits and stale screens."""
    if game.finished:
        raise InvalidAnswer("game finished")
    if position != game.position:
        raise InvalidAnswer("wrong position")
    if not 0 <= choice <= 3:
        raise InvalidAnswer("choice out of range")
    order = game.orders[game.position]
    q = bank[game.question_ids[game.position]]
    is_correct = order[choice] == 0
    game.answers.append(choice)
    if is_correct:
        game.correct += 1
    else:
        game.lives -= 1
    game.position += 1
    return AnswerResult(
        correct=is_correct, correct_index=order.index(0), explain=q.t(q.explain, lang),
        tool=q.tool, lives=game.lives, score=game.correct, finished=game.finished, won=game.won,
    )  # fmt: skip


def percentile(score: int, all_scores: list[int]) -> int:
    """Share of players (0–100) with a strictly lower score — 'better than N%'."""
    if not all_scores:
        return 100
    lower = sum(1 for s in all_scores if s < score)
    return round(100 * lower / len(all_scores))
