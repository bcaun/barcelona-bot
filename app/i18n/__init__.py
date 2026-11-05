"""Simple YAML-based i18n. Keys are dotted paths: t('ru', 'menu.calc')."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

I18N_DIR = Path(__file__).resolve().parent


@lru_cache
def _catalog(lang: str) -> dict[str, str]:
    with (I18N_DIR / f"{lang}.yaml").open(encoding="utf-8") as f:
        return flatten(yaml.safe_load(f) or {})


def flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in tree.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key] = str(v)
    return out


def t(lang: str, key: str, /, **kwargs: Any) -> str:
    """Translate; falls back to Russian, then to the key itself (so missing keys are visible)."""
    text = _catalog(lang).get(key) or _catalog("ru").get(key) or key
    return text.format(**kwargs) if kwargs else text


def pick_language(
    language_code: str | None, supported: list[str], ru_like: list[str], default: str
) -> str:
    code = (language_code or "").split("-")[0].lower()
    if code in supported:
        return code
    if code in ru_like and "ru" in supported:
        return "ru"
    return default
