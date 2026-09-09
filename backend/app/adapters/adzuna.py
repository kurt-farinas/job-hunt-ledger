"""Adzuna's official documented JSON search API (no listing-page requests).

Reference: https://developer.adzuna.com/docs/search
Limits: https://developer.adzuna.com/docs/terms_of_service
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, BeforeRequest, RequestPacer, get_json
from app.adapters.parsing import number as _number, plain_text as _plain_text, posted_at as _posted_at, safe_url as _safe_url, text as _text
from app.schemas.jobs import NormalizedJob

_ADZUNA_PACER = RequestPacer(2.5)


def parse_adzuna_job(item: Any) -> NormalizedJob:
    if not isinstance(item, dict) or not _text(item.get("title")).strip():
        raise SourceError("invalid_response", "A source listing is missing its title.")
    company = item.get("company") or {}
    location = item.get("location") or {}
    if not isinstance(company, dict) or not isinstance(location, dict):
        raise SourceError("invalid_response", "A source listing has invalid company or location data.")
    contract_time = _text(item.get("contract_time")).lower()
    contract_type = _text(item.get("contract_type")).lower()
    employment_type = {"full_time": "Full-time", "part_time": "Part-time"}.get(contract_time)
    if employment_type is None:
        employment_type = {"contract": "Contract", "freelance": "Freelance", "internship": "Internship"}.get(contract_type)
    remote_value = item.get("remote")
    remote_flag = None
    if remote_value is True or remote_value in (1, "1", "Yes", "Remote"):
        remote_flag = True
    elif remote_value is False or remote_value in (0, "0", "No", "Non-Remote"):
        remote_flag = False
    # Missing remote information does not imply on-site; non-remote may be hybrid.
    work_arrangement = "Remote" if remote_flag else None
    explicit_arrangement = _text(item.get("work_arrangement")).strip().casefold()
    arrangements = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "on_site": "On-site", "onsite": "On-site"}
    if explicit_arrangement in arrangements:
        work_arrangement = arrangements[explicit_arrangement]
    salary_min, salary_max = _number(item.get("salary_min")), _number(item.get("salary_max"))
    if salary_min is not None and salary_max is not None and salary_max < salary_min:
        raise SourceError("invalid_response", "A source listing contains an invalid salary range.")
    currency = _text(item.get("salary_currency")).strip().upper()
    period = _text(item.get("salary_period")).strip().lower()
    metadata = {
        "country": None,
        "contract_time": contract_time or None,
        "contract_type": contract_type or None,
        "description_is_excerpt": True,
    }
    predicted = item.get("salary_is_predicted")
    if predicted in (0, 1, "0", "1"):
        metadata["salary_is_predicted"] = predicted in (1, "1")
    external_id = item.get("id")
    if isinstance(external_id, bool) or not isinstance(external_id, (str, int)):
        external_id = None
    try:
        return NormalizedJob(
            source="adzuna",
            external_job_id=str(external_id) if external_id is not None else None,
            title=item["title"],
            company=_text(company.get("display_name")),
            location=_text(location.get("display_name")),
            source_url=_safe_url(item.get("redirect_url")),
            posted_at=_posted_at(item.get("created")),
            job_description=_plain_text(item.get("description")),
            employment_type=employment_type,
            work_arrangement=work_arrangement,
            remote_flag=remote_flag,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency if re.fullmatch(r"[A-Z]{3}", currency) else None,
            salary_period=period if period in ("hour", "day", "week", "month", "year") else None,
            raw_source_metadata=metadata,
        )
    except ValidationError:
        raise SourceError("invalid_response", "A source listing contains invalid field values.") from None


class AdzunaAdapter:
    def __init__(
        self,
        config: Any,
        settings: Any,
        client: httpx.AsyncClient,
        *,
        limiter: RequestPacer | None = None,
        before_request: BeforeRequest | None = None,
        sleep: AsyncSleep = asyncio.sleep,
    ) -> None:
        self.config = config
        self.settings = settings
        self.client = client
        self.limiter = limiter if limiter is not None else _ADZUNA_PACER
        self.before_request = before_request
        self.sleep = sleep

    async def fetch(self) -> list[NormalizedJob]:
        app_id = self.settings.adzuna_app_id.get_secret_value().strip()
        app_key = self.settings.adzuna_app_key.get_secret_value().strip()
        if not app_id or not app_key:
            raise SourceError(
                "missing_credentials", "Set ADZUNA_APP_ID and ADZUNA_APP_KEY in your local .env before searching Adzuna."
            )
        config = self.config
        jobs: list[NormalizedJob] = []
        for page in range(1, config.max_pages + 1):
            try:
                payload = await get_json(
                    self.client,
                    f"https://api.adzuna.com/v1/api/jobs/{config.country}/search/{page}",
                    params={"app_id": app_id, "app_key": app_key, "what": config.query, "results_per_page": config.results_per_page, "sort_by": "date"},
                    allowed_hosts=frozenset({"api.adzuna.com"}),
                    timeout_seconds=self.settings.http_timeout_seconds,
                    max_retries=self.settings.http_max_retries,
                    backoff_seconds=self.settings.http_backoff_seconds,
                    pacer=self.limiter,
                    min_interval_seconds=config.min_request_interval_seconds,
                    before_request=self.before_request,
                    sleep=self.sleep,
                )
            except SourceError as exc:
                if exc.code in ("http_400", "http_404"):
                    raise SourceError(
                        "country_or_query_rejected",
                        "Adzuna rejected the configured country or query. Verify country coverage and sources.json; the configured market was not changed.",
                    ) from None
                raise
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise SourceError("invalid_response", "Adzuna returned an invalid results collection.")
            results = payload["results"]
            if not results:
                break
            if len(results) > config.results_per_page:
                raise SourceError("invalid_response", "Adzuna returned more results than requested.")
            for item in results:
                job = parse_adzuna_job(item)
                job.raw_source_metadata["country"] = config.country
                jobs.append(job)
            count = payload.get("count")
            if len(results) < config.results_per_page or (
                isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= len(jobs)
            ):
                break
        return jobs
