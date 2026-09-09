"""Source allowlist and strict, editable source configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    enabled: StrictBool = False


class AdzunaSourceConfig(SourceConfig):
    enabled: StrictBool = True
    country: str = "ph"
    results_per_page: int = Field(default=50, ge=1, le=50, strict=True)
    max_pages: int = Field(default=3, ge=1, le=20, strict=True)
    query: str = Field(default="developer", min_length=1, max_length=200)
    min_request_interval_seconds: float = Field(default=2.5, ge=0, le=300)

    @field_validator("country")
    @classmethod
    def valid_country(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z]{2}", value):
            raise ValueError("country must be a two-letter country code")
        return value

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class BoardSourceConfig(SourceConfig):
    company_slugs: list[str] = Field(default_factory=list)
    min_request_interval_seconds: float = Field(default=1.0, ge=0, le=300)

    @field_validator("company_slugs")
    @classmethod
    def valid_slugs(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
                raise ValueError("company slugs must be 1–100 letters, digits, underscores or hyphens; URLs are not slugs")
            if value not in result:
                result.append(value)
        return result


class FeedSourceConfig(SourceConfig):
    max_items: int = Field(default=250, ge=1, le=1000, strict=True)
    min_request_interval_seconds: float = Field(default=2.5, ge=0, le=300)


class RemotiveSourceConfig(FeedSourceConfig):
    max_items: int = Field(default=200, ge=1, le=1000, strict=True)
    category: str = Field(default="software-dev", min_length=1, max_length=100)

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", value):
            raise ValueError("category must be a documented Remotive category slug")
        return value


class JobicySourceConfig(FeedSourceConfig):
    max_items: int = Field(default=200, ge=1, le=200, strict=True)
    industry: str = Field(default="engineering", min_length=1, max_length=100)

    @field_validator("industry")
    @classmethod
    def valid_industry(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", value):
            raise ValueError("industry must be a documented Jobicy industry slug")
        return value


class HimalayasSourceConfig(SourceConfig):
    max_pages: int = Field(default=1, ge=1, le=10, strict=True)
    query: str = Field(default="developer", min_length=1, max_length=100)
    queries: list[str] = Field(default_factory=lambda: ["developer"], min_length=1, max_length=12)
    country: str = Field(default="Philippines", min_length=2, max_length=100)
    seniority: str | None = Field(default="Entry-level", min_length=2, max_length=50)
    employment_type: str = Field(default="Full Time", min_length=2, max_length=50)
    exclude_worldwide: StrictBool = True
    min_request_interval_seconds: float = Field(default=2.5, ge=0, le=300)

    @field_validator("query", "country", "employment_type")
    @classmethod
    def safe_search_value(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+/#-]{0,99}", value):
            raise ValueError("Himalayas search values contain unsupported characters")
        return value

    @field_validator("seniority")
    @classmethod
    def safe_seniority(cls, value: str | None) -> str | None:
        return cls.safe_search_value(value) if value is not None else None

    @field_validator("queries")
    @classmethod
    def safe_queries(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            query = cls.safe_search_value(value)
            if query not in cleaned:
                cleaned.append(query)
        return cleaned


class GreenhouseSourceConfig(BoardSourceConfig):
    max_items_per_company: int = Field(default=500, ge=1, le=2000, strict=True)


class LeverSourceConfig(BoardSourceConfig):
    results_per_page: int = Field(default=100, ge=1, le=100, strict=True)
    max_pages: int = Field(default=5, ge=1, le=20, strict=True)


SOURCE_MODELS = {
    "adzuna": AdzunaSourceConfig,
    "remoteok": FeedSourceConfig,
    "we_work_remotely": FeedSourceConfig,
    "remotive": RemotiveSourceConfig,
    "jobicy": JobicySourceConfig,
    "himalayas": HimalayasSourceConfig,
    "greenhouse": GreenhouseSourceConfig,
    "lever": LeverSourceConfig,
}


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: dict[str, AdzunaSourceConfig | FeedSourceConfig | RemotiveSourceConfig | JobicySourceConfig | HimalayasSourceConfig | GreenhouseSourceConfig | LeverSourceConfig | SourceConfig]

    @field_validator("sources", mode="before")
    @classmethod
    def known_typed_sources(cls, value: object) -> dict:
        if not isinstance(value, dict):
            raise ValueError("sources must be an object keyed by approved source name")
        unknown = set(value) - SOURCE_MODELS.keys()
        if unknown:
            raise ValueError(f"unapproved source names: {', '.join(sorted(unknown))}")
        return {name: SOURCE_MODELS[name].model_validate(config) for name, config in value.items()}


def load_sources(path: Path) -> SourcesConfig:
    """Read and validate the whole document; never silently use a malformed file."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid source configuration from {path.name}") from exc
    return SourcesConfig.model_validate(document)
