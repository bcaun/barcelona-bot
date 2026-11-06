"""Configuration: secrets from .env, non-secret settings from config/*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"


class Env(BaseSettings):
    """Secrets and deployment-specific values (from environment / .env)."""

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    bot_token: SecretStr
    admin_ids: str = ""
    admin_chat_id: int | None = None
    channel_id: str = ""
    database_url: str = f"sqlite+aiosqlite:///{ROOT_DIR / 'data' / 'bot.db'}"
    log_level: str = "INFO"
    webapp_domain: str = ""
    # Telegram Payments provider token (Stripe), from @BotFather → Payments. Empty = no online pay.
    payment_provider_token: SecretStr | None = None

    @field_validator("payment_provider_token", mode="before")
    @classmethod
    def _empty_token_to_none(cls, v: Any) -> Any:
        return None if v in ("", None) else v

    @property
    def payments_enabled(self) -> bool:
        return self.payment_provider_token is not None

    @field_validator("admin_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return None if v in ("", None) else v

    @property
    def admin_id_set(self) -> frozenset[int]:
        return frozenset(int(x) for x in self.admin_ids.replace(" ", "").split(",") if x)


class ChannelSettings(BaseModel):
    url: str
    subscription_cache_seconds: int = 600


class I18nSettings(BaseModel):
    supported: list[str] = ["ru", "en"]
    default: str = "en"
    ru_like: list[str] = ["ru"]


class ThrottleSettings(BaseModel):
    max_events_per_second: float = 2


class LegalController(BaseModel):
    name: str = ""
    tax_id: str = ""
    address: str = ""
    email: str = ""


class LegalSettings(BaseModel):
    privacy_policy_url: str
    controller: LegalController = Field(default_factory=LegalController)


class AdminSettings(BaseModel):
    language: str = "ru"


class CalcSettings(BaseModel):
    require_subscription: bool = False
    municipality: str = "barcelona"
    mortgage_share_default: int = 60
    mortgage_share_options: list[int] = [50, 60, 70, 80]
    price_min_eur: int = 10_000
    price_max_eur: int = 100_000_000


class NotaSimpleSettings(BaseModel):
    price_eur: int = 29
    currency: str = "EUR"
    official_fee_eur: str = "9,02 € + IVA"


class CatastroSettings(BaseModel):
    base_url: str = "https://ovc.catastro.meh.es/OVCServWeb/OVCWcfCallejero"
    user_agent: str = "barcelona-bot/1.0"
    timeout_seconds: float = 10
    list_timeout_seconds: float = 30
    list_retries: int = 1
    retries: int = 2
    retry_pause_seconds: float = 1.5
    cache_days: int = 7
    default_province: str = "BARCELONA"
    default_municipality: str = "BARCELONA"
    card_url_template: str = ""
    units_page_size: int = 8


class Settings(BaseModel):
    timezone: str = "Europe/Madrid"
    channel: ChannelSettings
    i18n: I18nSettings = Field(default_factory=I18nSettings)
    throttle: ThrottleSettings = Field(default_factory=ThrottleSettings)
    legal: LegalSettings
    admin: AdminSettings = Field(default_factory=AdminSettings)
    calc: CalcSettings = Field(default_factory=CalcSettings)
    catastro: CatastroSettings = Field(default_factory=CatastroSettings)
    nota_simple: NotaSimpleSettings = Field(default_factory=NotaSimpleSettings)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load config/<name> and overlay config/<name>.local.yaml (owner's values, not in git)."""
    folder = config_dir or CONFIG_DIR
    with (folder / name).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    local = folder / name.replace(".yaml", ".local.yaml")
    if local.exists():
        with local.open(encoding="utf-8") as f:
            data = deep_merge(data, yaml.safe_load(f) or {})
    return data


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate(load_yaml("settings.yaml"))


class LocalizedText(BaseModel):
    ru: str = ""
    en: str = ""

    def get(self, lang: str) -> str:
        return getattr(self, lang, "") or self.ru


class Regulation(BaseModel):
    verified_at: str = ""
    stressed_market_municipalities: list[str] = []
    texts: dict[str, LocalizedText] = {}
    links: dict[str, str] = {}
    ite_age_threshold_years: int = 45
    tourist_ban_municipalities: list[str] = ["barcelona"]
    residential_uses: list[str] = ["residencial"]
    area_mismatch_warn_pct: float = 10

    def check_rules(self):  # noqa: ANN201 — app.core.cadastre.CheckRules
        from decimal import Decimal

        from app.core.cadastre import CheckRules

        return CheckRules(
            residential_uses=frozenset(u.lower() for u in self.residential_uses),
            area_warn_pct=Decimal(str(self.area_mismatch_warn_pct)) / 100,
            ite_age_years=self.ite_age_threshold_years,
            stressed=frozenset(m.lower() for m in self.stressed_market_municipalities),
            tourist_ban=frozenset(m.lower() for m in self.tourist_ban_municipalities),
        )

    def text(self, key: str, lang: str) -> str:
        item = self.texts.get(key)
        return item.get(lang) if item else ""

    def is_stressed(self, municipality: str) -> bool:
        return municipality.lower() in {m.lower() for m in self.stressed_market_municipalities}


@lru_cache
def get_regulation() -> Regulation:
    return Regulation.model_validate(load_yaml("regulation.yaml"))


@lru_cache
def get_tax_config():  # noqa: ANN201 — returns app.core.calc.TaxConfig
    from app.core.calc import TaxConfig

    return TaxConfig.from_dict(load_yaml("taxes.yaml"))


@lru_cache
def get_env() -> Env:
    return Env()  # type: ignore[call-arg]
