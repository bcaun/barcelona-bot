from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core import repo
from app.core.models import User
from app.core.money import format_eur
from app.i18n import pick_language


def test_format_eur():
    assert format_eur(82000) == "82 000 €"
    assert format_eur(Decimal("1234567.5")) == "1 234 568 €"
    assert format_eur(0) == "0 €"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("post_123", "post_123"),
        ("site", "site"),
        (None, None),
        ("", None),
        ("bad source!<>", "badsource"),
        ("x" * 100, "x" * 64),
        ("!!!", None),
    ],
)
def test_clean_source(raw, expected):
    assert repo.clean_source(raw) == expected


def test_apply_source_first_and_last_touch():
    u = User(id=1, language="ru")
    repo.apply_source(u, "post_1")
    repo.apply_source(u, None)
    repo.apply_source(u, "site")
    assert u.source == "post_1"
    assert u.last_source == "site"


@pytest.mark.parametrize(
    ("code", "expected"),
    [("ru", "ru"), ("en", "en"), ("en-US", "en"), ("uk", "ru"), ("de", "en"), (None, "en")],
)
def test_pick_language(code, expected):
    assert pick_language(code, ["ru", "en"], ["ru", "uk"], "en") == expected


def test_period_starts_madrid():
    # 2026-11-05 00:30 Madrid (CET, UTC+1) == 2026-11-04 23:30 UTC
    now = datetime(2026, 11, 4, 23, 30, tzinfo=UTC)
    day, week = repo.period_starts(now, "Europe/Madrid")
    assert day == datetime(2026, 11, 4, 23, 0, tzinfo=UTC)
    assert (day - week).days == 6


async def test_user_leads_stats_csv_and_delete(session_factory):
    async with session_factory() as s:
        u, created = await repo.get_or_create_user(s, 1, "alice", "ru")
        assert created
        repo.apply_source(u, "post_7")
        u2, _ = await repo.get_or_create_user(s, 2, None, "en")
        await repo.log_event(s, 1, "start")
        await repo.create_lead(s, u, "contact", name="Алиса", phone="+34600000000")
        await repo.create_lead(s, u2, "contact", name="Bob", phone=None, data={"budget": 500000})
        await s.commit()

    async with session_factory() as s:
        _, created = await repo.get_or_create_user(s, 1, "alice_new", "ru")
        assert not created
        st = await repo.get_stats(s, "Europe/Madrid")
        assert (st.users_total, st.users_today, st.leads_total, st.leads_week) == (2, 2, 2, 2)
        assert st.leads_by_type == {"contact": 2}
        assert st.leads_by_source == {"post_7": 1, "—": 1}

        csv_bytes = await repo.leads_csv(s, "Europe/Madrid")
        text = csv_bytes.decode("utf-8-sig")
        lines = text.strip().splitlines()
        assert lines[0].split(";")[:3] == ["id", "created_at", "lead_type"]
        assert "Алиса" in lines[1] and "post_7" in lines[1] and "+34600000000" in lines[1]
        assert '"{""budget"": 500000}"' in lines[2]

        deleted = await repo.delete_user_data(s, 1)
        await s.commit()
        assert len(deleted) == 1

    async with session_factory() as s:
        st = await repo.get_stats(s, "Europe/Madrid")
        assert (st.users_total, st.leads_total) == (1, 1)


def test_format_range_and_pct():
    from app.core.money import format_eur_range, format_pct

    assert format_eur_range(Decimal(1600), Decimal(4000)) == "1 600–4 000 €"
    assert format_eur_range(Decimal(300), Decimal("300.2")) == "300 €"
    assert format_pct(Decimal("0.015")) == "1,5%"
    assert format_pct(Decimal("0.10"), "en") == "10%"
    assert format_pct(Decimal("0.04321"), "ru", 1) == "4,3%"
