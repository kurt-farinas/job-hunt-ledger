"""Remotive's official public remote-jobs JSON API.

Reference: https://github.com/remotive-com/remote-jobs-api
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, BeforeRequest, RequestPacer, get_json
from app.adapters.parsing import employment_type, plain_text, posted_at, safe_url, text
from app.schemas.jobs import NormalizedJob

_PACER = RequestPacer(2.5)
_HOSTS = frozenset({"remotive.com", "www.remotive.com"})


def _salary(value: Any) -> tuple[float | None, float | None, str | None, str | None]:
    if not isinstance(value, str):
        return None, None, None, None
    values: list[float] = []
    for amount, suffix in re.findall(r"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*([kK]?)", value):
        # Providers occasionally write 31,2k as a decimal-comma amount while
        # also using 40,000 for thousands. The k suffix makes that distinction
        # deterministic without guessing at unmarked numbers.
        decimal_comma = bool(suffix and re.fullmatch(r"\d+,\d{1,2}", amount))
        number = float(amount.replace(",", "." if decimal_comma else "")) * (1000 if suffix else 1)
        if number > 0:
            values.append(number)
    lower = values[0] if values else None
    upper = values[1] if len(values) > 1 else None
    currency = "USD" if "$" in value or re.search(r"\bUSD\b", value, re.I) else None
    normalized = value.casefold()
    period = next((period for words, period in ((('hour', 'hourly'), 'hour'), (('month', 'monthly'), 'month'), (('year', 'yearly', 'annual'), 'year')) if any(word in normalized for word in words)), None)
    return lower, upper, currency, period


def parse_remotive_job(item: Any) -> NormalizedJob:
    if not isinstance(item, dict) or not text(item.get("title")).strip():
        raise SourceError("invalid_response", "A Remotive listing is missing its title.")
    lower, upper, currency, period = _salary(item.get("salary"))
    if lower is not None and upper is not None and upper < lower:
        raise SourceError("invalid_response", "A Remotive listing contains an invalid salary range.")
    identifier = item.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        identifier = None
    try:
        return NormalizedJob(
            source="remotive", external_job_id=str(identifier) if identifier is not None else None,
            title=item["title"], company=text(item.get("company_name")),
            location=text(item.get("candidate_required_location")),
            source_url=safe_url(item.get("url"), allowed_hosts=_HOSTS),
            posted_at=posted_at(item.get("publication_date")),
            job_description=plain_text(item.get("description")),
            employment_type=employment_type(item.get("job_type")),
            work_arrangement="Remote", remote_flag=True,
            salary_min=lower, salary_max=upper, salary_currency=currency, salary_period=period,
            raw_source_metadata={"category": text(item.get("category")) or None, "salary_text": text(item.get("salary")) or None},
        )
    except ValidationError:
        raise SourceError("invalid_response", "A Remotive listing contains invalid field values.") from None


class RemotiveAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, before_request: BeforeRequest | None = None,
                 sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.before_request, self.sleep = before_request, sleep

    async def fetch(self) -> list[NormalizedJob]:
        payload = await get_json(
            self.client, "https://remotive.com/api/remote-jobs",
            params={"category": self.config.category, "limit": self.config.max_items},
            allowed_hosts=frozenset({"remotive.com"}), timeout_seconds=self.settings.http_timeout_seconds,
            max_retries=self.settings.http_max_retries, backoff_seconds=self.settings.http_backoff_seconds,
            pacer=self.limiter, min_interval_seconds=self.config.min_request_interval_seconds,
            before_request=self.before_request, sleep=self.sleep,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise SourceError("invalid_response", "Remotive returned an invalid jobs collection.")
        # The live API may ignore its documented limit parameter. Enforce the
        # local processing cap even when the provider returns a larger array.
        return [parse_remotive_job(item) for item in payload["jobs"][:self.config.max_items]]
