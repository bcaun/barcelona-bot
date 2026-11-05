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


class Settings(BaseModel):
    timezone: str = "Europe/Madrid"
    channel: ChannelSettings
    i18n: I18nSettings = Field(default_factory=I18nSettings)
    throttle: ThrottleSettings = Field(default_factory=ThrottleSettings)
    legal: LegalSettings
    admin: AdminSettings = Field(default_factory=AdminSettings)
    calc: CalcSettings = Field(default_factory=CalcSettings)


def load_yaml(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate(load_yaml("settings.yaml"))


@lru_cache
def get_env() -> Env:
    return Env()  # type: ignore[call-arg]
