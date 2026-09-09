"""RemoteOK's official public JSON jobs feed.

Reference: https://remoteok.com/faq#feeds-sponsorship
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, RequestPacer, get_json
from app.adapters.parsing import employment_type_from_values, number, plain_text, posted_at, safe_url, text
from app.schemas.jobs import NormalizedJob

_PACER = RequestPacer(2.5)
_HOSTS = frozenset({"remoteok.com", "www.remoteok.com"})


def parse_remoteok_job(item: Any) -> NormalizedJob:
    if not isinstance(item, dict) or not text(item.get("position")).strip():
        raise SourceError("invalid_response", "A RemoteOK listing is missing its title.")
    lower, upper = number(item.get("salary_min"), positive=True), number(item.get("salary_max"), positive=True)
    if lower is not None and upper is not None and upper < lower:
        raise SourceError("invalid_response", "A RemoteOK listing contains an invalid salary range.")
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    identifier = item.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        identifier = item.get("slug") if isinstance(item.get("slug"), str) else None
    try:
        return NormalizedJob(
            source="remoteok",
            external_job_id=str(identifier) if identifier is not None else None,
            title=item["position"],
            company=text(item.get("company")),
            location=text(item.get("location")),
            source_url=safe_url(item.get("url") or item.get("apply_url"), allowed_hosts=_HOSTS),
            posted_at=posted_at(item.get("date") if item.get("date") is not None else item.get("epoch")),
            job_description=plain_text(item.get("description")),
            employment_type=employment_type_from_values(tags),
            work_arrangement="Remote",
            remote_flag=True,
            salary_min=lower,
            salary_max=upper,
            raw_source_metadata={
                "slug": text(item.get("slug")) or None,
                "tags": [value for value in tags if isinstance(value, str)][:50],
                "salary_currency_unspecified": lower is not None or upper is not None,
            },
        )
    except ValidationError:
        raise SourceError("invalid_response", "A RemoteOK listing contains invalid field values.") from None


class RemoteOKAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.sleep = sleep

    async def fetch(self) -> list[NormalizedJob]:
        payload = await get_json(
            self.client, "https://remoteok.com/api", params={}, allowed_hosts=frozenset({"remoteok.com"}),
            timeout_seconds=self.settings.http_timeout_seconds, max_retries=self.settings.http_max_retries,
            backoff_seconds=self.settings.http_backoff_seconds, pacer=self.limiter,
            min_interval_seconds=self.config.min_request_interval_seconds, sleep=self.sleep,
        )
        if not isinstance(payload, list):
            raise SourceError("invalid_response", "RemoteOK returned an invalid jobs collection.")
        listings = [item for item in payload if not (isinstance(item, dict) and "legal" in item)]
        return [parse_remoteok_job(item) for item in listings[:self.config.max_items]]
