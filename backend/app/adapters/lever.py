"""Lever's official public Postings JSON API.

Reference: https://github.com/lever/postings-api
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, RequestPacer, get_json
from app.adapters.parsing import employment_type, number, plain_text, posted_at, safe_url, salary_period, text, work_arrangement
from app.schemas.jobs import NormalizedJob

_PACER = RequestPacer(1.0)
_API_HOST = frozenset({"api.lever.co"})
_JOB_HOSTS = frozenset({"jobs.lever.co", "jobs.eu.lever.co"})


def parse_lever_job(item: Any, *, company_slug: str) -> NormalizedJob:
    if not isinstance(item, dict) or not text(item.get("text")).strip():
        raise SourceError("invalid_response", "A Lever listing is missing its title.")
    categories = item.get("categories")
    if not isinstance(categories, dict):
        raise SourceError("invalid_response", "A Lever listing contains invalid categories.")
    location = text(categories.get("location"))
    if not location and isinstance(categories.get("allLocations"), list):
        location = ", ".join(value for value in categories["allLocations"] if isinstance(value, str))
    arrangement = work_arrangement(item.get("workplaceType"))
    salary = item.get("salaryRange") if isinstance(item.get("salaryRange"), dict) else {}
    lower, upper = number(salary.get("min"), positive=True), number(salary.get("max"), positive=True)
    if lower is not None and upper is not None and upper < lower:
        raise SourceError("invalid_response", "A Lever listing contains an invalid salary range.")
    currency = text(salary.get("currency")).strip().upper()
    identifier = item.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        identifier = None
    try:
        return NormalizedJob(
            source="lever", external_job_id=str(identifier) if identifier is not None else None,
            title=item["text"], company=company_slug, location=location,
            source_url=safe_url(item.get("hostedUrl"), allowed_hosts=_JOB_HOSTS),
            posted_at=posted_at(item.get("createdAt")),
            job_description=text(item.get("descriptionPlain")).strip() or plain_text(item.get("description")),
            employment_type=employment_type(categories.get("commitment")),
            work_arrangement=arrangement, remote_flag=True if arrangement == "Remote" else False if arrangement == "On-site" else None,
            salary_min=lower, salary_max=upper,
            salary_currency=currency if len(currency) == 3 and currency.isalpha() else None,
            salary_period=salary_period(salary.get("interval")),
            raw_source_metadata={
                "company_slug": company_slug,
                "team": text(categories.get("team")) or None,
                "department": text(categories.get("department")) or None,
                "country": text(item.get("country")) or None,
            },
        )
    except ValidationError:
        raise SourceError("invalid_response", "A Lever listing contains invalid field values.") from None


class LeverAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.sleep = sleep

    async def fetch(self) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        for slug in self.config.company_slugs:
            try:
                for page in range(self.config.max_pages):
                    payload = await get_json(
                        self.client, f"https://api.lever.co/v0/postings/{slug}",
                        params={"mode": "json", "skip": page * self.config.results_per_page, "limit": self.config.results_per_page},
                        allowed_hosts=_API_HOST, timeout_seconds=self.settings.http_timeout_seconds,
                        max_retries=self.settings.http_max_retries, backoff_seconds=self.settings.http_backoff_seconds,
                        pacer=self.limiter, min_interval_seconds=self.config.min_request_interval_seconds, sleep=self.sleep,
                    )
                    if not isinstance(payload, list):
                        raise SourceError("invalid_response", "Lever returned an invalid jobs collection.")
                    if len(payload) > self.config.results_per_page:
                        raise SourceError("invalid_response", "Lever returned more jobs than requested.")
                    jobs.extend(parse_lever_job(item, company_slug=slug) for item in payload)
                    if len(payload) < self.config.results_per_page:
                        break
            except SourceError as exc:
                raise SourceError(exc.code, f"Lever board '{slug}' failed: {exc.message}") from None
        return jobs
