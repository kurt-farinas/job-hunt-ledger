"""Environment settings. Credentials stay in memory and are redacted by Pydantic."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    project_root: Path = PROJECT_ROOT
    config_dir: Path = PROJECT_ROOT / "backend" / "config"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/job_dashboard.db"
    backup_dir: Path | None = None
    cors_origins: list[str] = ["http://localhost:5173"]
    adzuna_app_id: SecretStr = SecretStr("")
    adzuna_app_key: SecretStr = SecretStr("")
    gmail_client_id: str = ""
    gmail_client_secret: SecretStr = SecretStr("")
    gmail_redirect_uri: str = "http://localhost:8000/api/gmail/auth/callback"
    gmail_token_path: Path | None = None
    scheduler_enabled: bool = True
    http_timeout_seconds: float = Field(default=15, gt=0, le=120)
    http_max_retries: int = Field(default=2, ge=0, le=3)
    http_backoff_seconds: float = Field(default=1, ge=0, le=30)

    @field_validator("database_url")
    @classmethod
    def local_database_only(cls, value: str) -> str:
        if not value.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL must be a local sqlite:/// URL")
        return value

    @model_validator(mode="after")
    def resolve_local_paths(self):
        value = self.backup_dir or Path("backups")
        self.backup_dir = (value if value.is_absolute() else self.project_root / value).resolve()
        token = self.gmail_token_path
        if token is None:
            base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
            token = base / "JobSearchDashboard" / "gmail_token.json"
        token = token.expanduser().resolve()
        try:
            token.relative_to(self.project_root.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("GMAIL_TOKEN_PATH must be outside the project directory")
        self.gmail_token_path = token
        return self

    @field_validator("gmail_token_path", mode="before")
    @classmethod
    def blank_token_path(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("gmail_redirect_uri")
    @classmethod
    def local_gmail_redirect(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username or parsed.password or parsed.path != "/api/gmail/auth/callback"
                or parsed.query or parsed.fragment):
            raise ValueError("GMAIL_REDIRECT_URI must be the local Gmail callback URL")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def loopback_origins_only(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("CORS_ORIGINS must contain at least one local frontend origin")
        result = []
        for value in values:
            try:
                parsed = urlsplit(value)
                parsed.port  # Reject malformed ports too.
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS contains an invalid origin") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS permits loopback HTTP(S) origins only")
            origin = value.rstrip("/")
            if origin not in result:
                result.append(origin)
        return result
