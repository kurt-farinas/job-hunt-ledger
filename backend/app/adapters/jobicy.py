"""Jobicy's official public remote-jobs JSON API.

Reference: https://github.com/Jobicy/remote-jobs-api
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, RequestPacer, get_json
from app.adapters.parsing import employment_type_from_values, number, plain_text, posted_at, safe_url, salary_period, text
from app.schemas.jobs import NormalizedJob

_PACER = RequestPacer(2.5)
_HOSTS = frozenset({"jobicy.com", "www.jobicy.com"})


def parse_jobicy_job(item: Any) -> NormalizedJob:
    if not isinstance(item, dict) or not text(item.get("jobTitle")).strip():
        raise SourceError("invalid_response", "A Jobicy listing is missing its title.")
    lower = number(item.get("salaryMin"), positive=True)
    upper = number(item.get("salaryMax"), positive=True)
    if lower is not None and upper is not None and upper < lower:
        raise SourceError("invalid_response", "A Jobicy listing contains an invalid salary range.")
    currency = text(item.get("salaryCurrency")).strip().upper()
    identifier = item.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        identifier = text(item.get("jobSlug")) or None
    industries = item.get("jobIndustry") if isinstance(item.get("jobIndustry"), list) else []
    try:
        return NormalizedJob(
            source="jobicy", external_job_id=str(identifier) if identifier is not None else None,
            title=item["jobTitle"], company=text(item.get("companyName")), location=text(item.get("jobGeo")),
            source_url=safe_url(item.get("url"), allowed_hosts=_HOSTS), posted_at=posted_at(item.get("pubDate")),
            job_description=plain_text(item.get("jobDescription") or item.get("jobExcerpt")),
            employment_type=employment_type_from_values(item.get("jobType")),
            work_arrangement="Remote", remote_flag=True,
            salary_min=lower, salary_max=upper,
            salary_currency=currency if re.fullmatch(r"[A-Z]{3}", currency) else None,
            salary_period=salary_period(item.get("salaryPeriod")),
            raw_source_metadata={"job_level": text(item.get("jobLevel")) or None,
                                 "industries": [value for value in industries if isinstance(value, str)][:50]},
        )
    except ValidationError:
        raise SourceError("invalid_response", "A Jobicy listing contains invalid field values.") from None


class JobicyAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.sleep = sleep

    async def fetch(self) -> list[NormalizedJob]:
        payload = await get_json(
            self.client, "https://jobicy.com/api/v2/remote-jobs",
            params={"count": self.config.max_items, "industry": self.config.industry},
            allowed_hosts=frozenset({"jobicy.com"}), timeout_seconds=self.settings.http_timeout_seconds,
            max_retries=self.settings.http_max_retries, backoff_seconds=self.settings.http_backoff_seconds,
            pacer=self.limiter, min_interval_seconds=self.config.min_request_interval_seconds, sleep=self.sleep,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise SourceError("invalid_response", "Jobicy returned an invalid jobs collection.")
        return [parse_jobicy_job(item) for item in payload["jobs"][:self.config.max_items]]
