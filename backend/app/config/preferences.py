"""Transparent search preferences; the checked-in JSON is the editable contract."""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JobPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    included_title_keywords: list[str] = Field(default_factory=lambda: [
        "junior developer", "entry level developer", "associate developer",
        "frontend developer", "front end developer", "full stack developer",
        "full-stack developer", "react developer", "laravel developer",
        "php developer", "web developer", "software developer",
        "frontend engineer", "front end engineer", "full stack engineer",
        "full-stack engineer", "software engineer", "web engineer", "react engineer",
        "full stack product engineer", "fresh graduate", "new graduate", "new grad",
        "graduate developer", "graduate engineer", "developer trainee", "software trainee",
    ])
    excluded_seniority_keywords: list[str] = Field(default_factory=lambda: [
        "senior", "sr.", "lead", "manager", "architect", "principal", "staff",
        "director", "head of", "vp", "vice president",
    ])
    accepted_locations: list[str] = Field(default_factory=lambda: [
        "Metro Manila", "Manila", "Philippines", "Remote", "Anywhere",
        "Worldwide", "Anywhere in the World", "Location independent", "APAC",
    ])
    accepted_remote_locations: list[str] = Field(default_factory=list)
    accepted_hybrid_on_site_locations: list[str] = Field(default_factory=list)
    accepted_work_arrangements: list[str] = Field(default_factory=lambda: ["Remote", "Hybrid", "On-site"])
    require_work_arrangement_match: bool = False
    accepted_employment_types: list[str] = Field(default_factory=lambda: ["Full-time", "Contract", "Freelance", "Internship", "Part-time"])
    require_employment_type_match: bool = False
    preferred_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    stale_job_threshold_days: int = Field(default=30, ge=1, le=3650, strict=True)
    minimum_salary: float | None = Field(default=None, ge=0)
    minimum_salary_currency: str | None = None
    minimum_salary_period: str | None = None

    @field_validator(
        "included_title_keywords", "excluded_seniority_keywords", "accepted_locations",
        "accepted_remote_locations", "accepted_hybrid_on_site_locations",
        "accepted_work_arrangements", "accepted_employment_types", "preferred_companies",
        "excluded_companies", "excluded_keywords",
    )
    @classmethod
    def nonblank_list_entries(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            value = " ".join(value.split())
            if not value or not any(character.isalnum() for character in value):
                raise ValueError("preference entries must contain letters or digits")
            if value not in result:
                result.append(value)
        return result

    @field_validator("minimum_salary_currency")
    @classmethod
    def currency_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise ValueError("minimum_salary_currency must be a three-letter currency code")
        return value

    @field_validator("minimum_salary_period")
    @classmethod
    def salary_period(cls, value: str | None) -> str | None:
        if value is None:
            return None
        aliases = {"annual": "year", "annually": "year", "yearly": "year", "monthly": "month", "weekly": "week", "daily": "day", "hourly": "hour"}
        value = aliases.get(value.strip().lower(), value.strip().lower())
        if value not in {"year", "month", "week", "day", "hour"}:
            raise ValueError("minimum_salary_period must be year, month, week, day or hour")
        return value

    @model_validator(mode="after")
    def comparable_salary_filter(self) -> "JobPreferences":
        if self.minimum_salary is not None:
            if not math.isfinite(self.minimum_salary):
                raise ValueError("minimum_salary must be finite")
            if not self.minimum_salary_currency or not self.minimum_salary_period:
                raise ValueError("a minimum salary requires both currency and period")
        return self


def load_preferences(path: Path) -> JobPreferences:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid job preferences from {path.name}") from exc
    return JobPreferences.model_validate(document)
