import re
from pathlib import Path

import yaml

from app.i18n import I18N_DIR, flatten, t


def _load(lang):
    return flatten(yaml.safe_load((I18N_DIR / f"{lang}.yaml").read_text(encoding="utf-8")))


def test_ru_en_have_same_keys():
    ru, en = _load("ru"), _load("en")
    assert set(ru) == set(en), set(ru) ^ set(en)


def test_placeholders_match():
    ru, en = _load("ru"), _load("en")
    ph = re.compile(r"\{(\w+)\}")
    for k in ru:
        assert set(ph.findall(ru[k])) == set(ph.findall(en[k])), k


def test_t_format_and_fallback():
    assert "Анна" in t("ru", "start.welcome", name="Анна")
    assert t("en", "no.such.key") == "no.such.key"


def test_all_used_keys_exist():
    """Every t(lang, 'x.y') literal in the code must exist in ru.yaml."""
    ru = _load("ru")
    src = Path(__file__).resolve().parent.parent / "app"
    used = set()
    for p in src.rglob("*.py"):
        used |= set(re.findall(r"""\bt\(\s*[\w.]+\s*,\s*["']([\w.]+)["']""", p.read_text()))
    missing = {k for k in used if k not in ru}
    assert not missing, missing
