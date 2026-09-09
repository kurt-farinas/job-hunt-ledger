"""Himalayas' official public remote-jobs search API.

Reference: https://himalayas.app/api
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, RequestPacer, get_json
from app.adapters.parsing import employment_type, number, plain_text, posted_at, safe_url, salary_period, text
from app.schemas.jobs import NormalizedJob
from app.services.normalization import dedupe_hash

_PACER = RequestPacer(2.5)
_HOSTS = frozenset({"himalayas.app", "www.himalayas.app"})
_ENDPOINT = "https://himalayas.app/jobs/api/search"


def _string_list(value: Any, *, limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()][:limit]


def parse_himalayas_job(item: Any) -> NormalizedJob:
    if not isinstance(item, dict) or not text(item.get("title")).strip():
        raise SourceError("invalid_response", "A Himalayas listing is missing its title.")
    lower = number(item.get("minSalary"), positive=True)
    upper = number(item.get("maxSalary"), positive=True)
    if lower is not None and upper is not None and upper < lower:
        raise SourceError("invalid_response", "A Himalayas listing contains an invalid salary range.")
    restrictions = _string_list(item.get("locationRestrictions"))
    location = ", ".join(restrictions)
    currency = text(item.get("currency")).strip().upper()
    url = item.get("applicationLink") or item.get("guid")
    try:
        return NormalizedJob(
            source="himalayas",
            external_job_id=text(item.get("guid")).strip() or None,
            title=item["title"],
            company=text(item.get("companyName")),
            location=location,
            source_url=safe_url(url, allowed_hosts=_HOSTS),
            posted_at=posted_at(item.get("pubDate")),
            job_description=plain_text(item.get("description") or item.get("excerpt")),
            employment_type=employment_type(item.get("employmentType")),
            work_arrangement="Remote",
            remote_flag=True,
            salary_min=lower,
            salary_max=upper,
            salary_currency=currency if re.fullmatch(r"[A-Z]{3}", currency) else None,
            salary_period=salary_period(item.get("salaryPeriod")),
            raw_source_metadata={
                "company_slug": text(item.get("companySlug")) or None,
                "seniority": _string_list(item.get("seniority")),
                "categories": _string_list(item.get("categories")),
                "timezone_restrictions": _string_list(item.get("timezoneRestrictions")),
                "expiry_date": text(item.get("expiryDate")) or None,
            },
        )
    except ValidationError:
        raise SourceError("invalid_response", "A Himalayas listing contains invalid field values.") from None


class HimalayasAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.sleep = sleep

    async def fetch(self) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        queries = getattr(self.config, "queries", None) or [self.config.query]
        for query in queries:
            for page in range(1, self.config.max_pages + 1):
                params = {
                    "q": query,
                    "country": self.config.country,
                    "exclude_worldwide": str(self.config.exclude_worldwide).lower(),
                    "employment_type": self.config.employment_type,
                    "sort": "recent",
                    "page": page,
                }
                if self.config.seniority:
                    params["seniority"] = self.config.seniority
                payload = await get_json(
                    self.client, _ENDPOINT, params=params,
                    allowed_hosts=frozenset({"himalayas.app"}),
                    timeout_seconds=self.settings.http_timeout_seconds,
                    max_retries=self.settings.http_max_retries,
                    backoff_seconds=self.settings.http_backoff_seconds,
                    pacer=self.limiter, min_interval_seconds=self.config.min_request_interval_seconds,
                    sleep=self.sleep,
                )
                if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                    raise SourceError("invalid_response", "Himalayas returned an invalid jobs collection.")
                page_jobs = payload["jobs"]
                jobs.extend(parse_himalayas_job(item) for item in page_jobs)
                limit = payload.get("limit")
                total = payload.get("totalCount")
                offset = payload.get("offset")
                if not page_jobs or not all(isinstance(value, int) and not isinstance(value, bool) for value in (limit, total, offset)):
                    break
                if offset + len(page_jobs) >= total:
                    break
        seen: set[str] = set()
        unique: list[NormalizedJob] = []
        for job in jobs:
            identity = dedupe_hash(job.company, job.title, job.source_url)
            if identity not in seen:
                seen.add(identity)
                unique.append(job)
        return unique
