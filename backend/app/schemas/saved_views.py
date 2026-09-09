from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.jobs import JobStatus


class SavedViewFilters(BaseModel):
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

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class SavedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    filters: SavedViewFilters = Field(default_factory=SavedViewFilters)
    sort_by: Literal["date_found", "posted_at", "source", "status", "company", "title"] = "date_found"
    sort_order: Literal["asc", "desc"] = "desc"


class SavedViewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    filters: SavedViewFilters | None = None
    sort_by: Literal["date_found", "posted_at", "source", "status", "company", "title"] | None = None
    sort_order: Literal["asc", "desc"] | None = None

    @model_validator(mode="after")
    def explicit_non_null_change(self):
        if not self.model_fields_set:
            raise ValueError("Provide at least one saved-view change")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Saved-view changes cannot be null")
        return self
