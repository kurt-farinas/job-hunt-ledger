"""Greenhouse's official public Job Board JSON endpoints.

Reference: https://docs.greenhouse.io/job-board.html
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, RequestPacer, get_json
from app.adapters.parsing import employment_type, number, plain_text, posted_at, safe_url, text
from app.schemas.jobs import NormalizedJob
from app.services.normalization import normalize_text

_PACER = RequestPacer(1.0)
_API_HOST = frozenset({"boards-api.greenhouse.io"})


def _arrangement(location: str) -> str | None:
    normalized = normalize_text(location)
    if " hybrid " in f" {normalized} ":
        return "Hybrid"
    if " on site " in f" {normalized} " or " onsite " in f" {normalized} ":
        return "On-site"
    if " remote " in f" {normalized} ":
        return "Remote"
    return None


def _metadata_employment(metadata: Any) -> str | None:
    if not isinstance(metadata, list):
        return None
    for field in metadata:
        if isinstance(field, dict) and "employment" in normalize_text(text(field.get("name"))):
            candidate = field.get("value")
            if isinstance(candidate, list):
                candidate = next((value for value in candidate if isinstance(value, str)), None)
            parsed = employment_type(candidate)
            if parsed:
                return parsed
    return None


def _salary(item: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    ranges = item.get("pay_input_ranges")
    if not isinstance(ranges, list) or not ranges or not isinstance(ranges[0], dict):
        return None, None, None
    value = ranges[0]
    lower, upper = number(value.get("min_cents"), positive=True), number(value.get("max_cents"), positive=True)
    lower = lower / 100 if lower is not None else None
    upper = upper / 100 if upper is not None else None
    currency = text(value.get("currency_type")).strip().upper()
    return lower, upper, currency if len(currency) == 3 and currency.isalpha() else None


def parse_greenhouse_job(item: Any, *, company: str, board_slug: str) -> NormalizedJob:
    if not isinstance(item, dict) or not text(item.get("title")).strip():
        raise SourceError("invalid_response", "A Greenhouse listing is missing its title.")
    location_data = item.get("location")
    if not isinstance(location_data, dict):
        raise SourceError("invalid_response", "A Greenhouse listing contains invalid location data.")
    location = text(location_data.get("name"))
    arrangement = _arrangement(location)
    lower, upper, currency = _salary(item)
    if lower is not None and upper is not None and upper < lower:
        raise SourceError("invalid_response", "A Greenhouse listing contains an invalid salary range.")
    identifier = item.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        identifier = None
    try:
        return NormalizedJob(
            source="greenhouse", external_job_id=str(identifier) if identifier is not None else None,
            title=item["title"], company=company, location=location,
            source_url=safe_url(item.get("absolute_url")),
            posted_at=posted_at(item.get("first_published")),
            job_description=plain_text(item.get("content")),
            employment_type=_metadata_employment(item.get("metadata")),
            work_arrangement=arrangement, remote_flag=True if arrangement == "Remote" else False if arrangement == "On-site" else None,
            salary_min=lower, salary_max=upper, salary_currency=currency,
            raw_source_metadata={
                "board_slug": board_slug,
                "internal_job_id": item.get("internal_job_id") if isinstance(item.get("internal_job_id"), (str, int)) and not isinstance(item.get("internal_job_id"), bool) else None,
                "updated_at": text(item.get("updated_at")) or None,
                "requisition_id": text(item.get("requisition_id")) or None,
            },
        )
    except ValidationError:
        raise SourceError("invalid_response", "A Greenhouse listing contains invalid field values.") from None


class GreenhouseAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.sleep = sleep

    async def _json(self, url: str, params: dict[str, str]) -> Any:
        return await get_json(
            self.client, url, params=params, allowed_hosts=_API_HOST,
            timeout_seconds=self.settings.http_timeout_seconds, max_retries=self.settings.http_max_retries,
            backoff_seconds=self.settings.http_backoff_seconds, pacer=self.limiter,
            min_interval_seconds=self.config.min_request_interval_seconds, sleep=self.sleep,
        )

    async def fetch(self) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        for slug in self.config.company_slugs:
            try:
                root = f"https://boards-api.greenhouse.io/v1/boards/{slug}"
                board = await self._json(root, {})
                # Full descriptions can exceed the shared response safety limit on
                # large boards. The standard endpoint still provides the fields
                # required for matching and the official application URL.
                payload = await self._json(f"{root}/jobs", {"content": "false"})
                if not isinstance(board, dict) or not text(board.get("name")).strip():
                    raise SourceError("invalid_response", "Greenhouse returned invalid job board details.")
                if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                    raise SourceError("invalid_response", "Greenhouse returned an invalid jobs collection.")
                jobs.extend(parse_greenhouse_job(item, company=board["name"], board_slug=slug)
                            for item in payload["jobs"][:self.config.max_items_per_company])
            except SourceError as exc:
                raise SourceError(exc.code, f"Greenhouse board '{slug}' failed: {exc.message}") from None
        return jobs
