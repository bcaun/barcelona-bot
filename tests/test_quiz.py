import random

import pytest

from app.config import load_yaml
from app.core.quiz import (
    InvalidAnswer,
    QuizConfigError,
    answer,
    current_question,
    load_bank,
    new_game,
    percentile,
)

BANK = load_bank(load_yaml("quiz.yaml"))


def test_quiz_yaml_is_valid_and_bilingual():
    assert len(BANK) >= 40  # pool bigger than 25 -> different games for different players
    for q in BANK.values():
        for part in (q.text, q.explain, *q.options):
            assert part.get("ru") and part.get("en"), q.id
        assert len({o["ru"] for o in q.options}) == 4, f"duplicate options in {q.id}"


def test_load_bank_rejects_bad_questions():
    bad = {"questions": [{"id": "x", "q": {"ru": "?"}, "correct": {"ru": "a"}, "wrong": []}]}
    with pytest.raises(QuizConfigError):
        load_bank(bad)


def test_new_game_samples_unique_and_shuffles():
    g = new_game(BANK, 25, 3, random.Random(1))
    assert g.total == 25 and len(set(g.question_ids)) == 25
    assert all(sorted(o) == [0, 1, 2, 3] for o in g.orders)
    g2 = new_game(BANK, 25, 3, random.Random(2))
    assert g.question_ids != g2.question_ids  # different players get different sets
    assert any(o[0] != 0 for o in g.orders)  # correct answer is not always first


def test_public_question_hides_answer_but_contains_options():
    g = new_game(BANK, 25, 3, random.Random(3))
    pq = current_question(g, BANK, "ru")
    q = BANK[pq.id]
    assert pq.number == 1 and pq.total == 25 and len(pq.options) == 4
    assert set(pq.options) == {o["ru"] for o in q.options}
    assert "correct" not in pq.__dataclass_fields__


def _right(g):
    return g.orders[g.position].index(0)


def _wrong(g):
    return next(i for i, v in enumerate(g.orders[g.position]) if v != 0)


def test_win_with_two_mistakes():
    g = new_game(BANK, 25, 3, random.Random(4))
    for i in range(25):
        choice = _wrong(g) if i in (3, 10) else _right(g)
        r = answer(g, BANK, g.position, choice, "en")
    assert r.finished and r.won and r.lives == 1 and r.score == 23


def test_lose_after_three_mistakes():
    g = new_game(BANK, 25, 3, random.Random(5))
    answer(g, BANK, 0, _right(g), "ru")
    for _ in range(3):
        r = answer(g, BANK, g.position, _wrong(g), "ru")
    assert r.finished and not r.won and r.lives == 0 and r.score == 1
    assert current_question(g, BANK, "ru") is None
    with pytest.raises(InvalidAnswer):
        answer(g, BANK, g.position, 0, "ru")


def test_answer_reports_correct_index_and_guards_position():
    g = new_game(BANK, 25, 3, random.Random(6))
    right = _right(g)
    r = answer(g, BANK, 0, _wrong(g), "ru")
    assert not r.correct and r.correct_index == right and r.explain
    with pytest.raises(InvalidAnswer):
        answer(g, BANK, 0, 0, "ru")  # same question twice
    with pytest.raises(InvalidAnswer):
        answer(g, BANK, 1, 7, "ru")


def test_game_roundtrip():
    from app.core.quiz import Game

    g = new_game(BANK, 25, 3, random.Random(7))
    answer(g, BANK, 0, 1, "ru")
    assert Game.from_dict(g.to_dict()) == g


def test_percentile():
    assert percentile(20, [5, 10, 20, 25]) == 50
    assert percentile(25, []) == 100
