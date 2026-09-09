"""One source-independent representation, plus the deliberately small write API."""

from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JobStatus = Literal["New", "Applied", "Not Interested", "Declined"]

_TRUSTED_SOURCE_HOSTS = {
    "linkedin": ("linkedin.com",),
    "jobstreet": ("jobstreet.com",),
    "indeed": ("indeed.com",),
    "prosple": ("prosple.com",),
    "glassdoor": ("glassdoor.com",),
}


def trusted_source_for_url(value: str) -> str | None:
    """Return the user-approved job-board source for a safe HTTP(S) URL."""
    host = (urlsplit(value).hostname or "").lower().rstrip(".")
    for source, domains in _TRUSTED_SOURCE_HOSTS.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return source
    return None


class NormalizedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    external_job_id: str | None = None
    title: str = Field(min_length=1)
    company: str = ""
    location: str = ""
    source_url: str
    posted_at: datetime | None = None
    job_description: str | None = None
    employment_type: str | None = None
    work_arrangement: str | None = None
    remote_flag: bool | None = None
    salary_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    salary_max: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    salary_currency: str | None = None
    salary_period: str | None = None
    raw_source_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def meaningful_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value

    @field_validator("source_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Source URL must be an HTTP(S) URL without credentials")
        if any(ord(char) < 32 for char in value):
            raise ValueError("Source URL contains control characters")
        return value

    @field_validator("posted_at")
    @classmethod
    def utc_posting_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)

    @model_validator(mode="after")
    def salary_range(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("Salary minimum must not exceed maximum")
        return self


class JobPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: JobStatus | None = None
    notes: str | None = Field(default=None, max_length=100_000)

    @model_validator(mode="after")
    def explicit_fields(self):
        if not self.model_fields_set:
            raise ValueError("Provide status or notes")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ManualJobCreate(BaseModel):
    """A job deliberately saved by the user from an approved job board."""

    model_config = ConfigDict(extra="forbid")
    source_url: str
    title: str = Field(min_length=1, max_length=500)
    company: str = Field(min_length=1, max_length=500)

    @field_validator("source_url")
    @classmethod
    def approved_source_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Provide an HTTP(S) job listing URL without credentials")
        if any(ord(char) < 32 for char in value):
            raise ValueError("Source URL contains control characters")
        if trusted_source_for_url(value) is None:
            raise ValueError("Use a LinkedIn, JobStreet, Indeed, Prosple, or Glassdoor listing URL")
        return value

    @field_validator("title", "company")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field must not be blank")
        return value


class JobFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: JobStatus | None = None
    source: str | None = Field(default=None, max_length=100)
    date_from: date | None = None
    date_to: date | None = None
    stale: bool | None = None
    work_arrangement: str | None = Field(default=None, max_length=100)
    employment_type: str | None = Field(default=None, max_length=100)
    preferred_company: bool | None = None
    search: str | None = Field(default=None, max_length=500)
    sort_by: Literal["date_found", "posted_at", "source", "status", "company", "title"] = "date_found"
    sort_order: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=25, ge=1, le=100)
