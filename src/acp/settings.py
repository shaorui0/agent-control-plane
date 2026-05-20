"""ACP runtime settings, loaded from environment / .env via pydantic-settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # Core paths/ports use ACP_ prefix; LLM keys keep upstream env names.
    db_path: Path = Path("./acp.db")
    registry_dir: Path = Path("./agents")
    port: int = 8080
    llm_judge_enabled: bool = False

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # W5A — server lifespan + human interface.
    slack_webhook_url: str | None = None
    session_secret: str = ""  # used as HMAC seed for SessionAuth bearer signing
    dashboard_refresh_seconds: int = 30

    @classmethod
    def _env_map(cls) -> dict[str, str]:
        # Map ACP_* env vars to fields explicitly.
        return {
            "ACP_DB_PATH": "db_path",
            "ACP_REGISTRY_DIR": "registry_dir",
            "ACP_PORT": "port",
            "ACP_LLM_JUDGE_ENABLED": "llm_judge_enabled",
            "ACP_SLACK_WEBHOOK_URL": "slack_webhook_url",
            "ACP_SESSION_SECRET": "session_secret",
            "ACP_DASHBOARD_REFRESH_SECONDS": "dashboard_refresh_seconds",
            "ANTHROPIC_API_KEY": "anthropic_api_key",
            "OPENAI_API_KEY": "openai_api_key",
        }

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        import os
        src = env if env is not None else os.environ
        kwargs: dict[str, object] = {}
        for env_key, field in cls._env_map().items():
            if env_key in src and src[env_key] != "":
                kwargs[field] = src[env_key]
        return cls(**kwargs)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
