"""We Work Remotely's official public programming RSS feed.

Reference: https://weworkremotely.com/remote-job-rss-feed
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
from pydantic import ValidationError

from app.adapters.base import SourceError
from app.adapters.http import AsyncSleep, RequestPacer, get_bytes
from app.adapters.parsing import plain_text, posted_at, safe_url
from app.schemas.jobs import NormalizedJob

_PACER = RequestPacer(2.5)
_FEED_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
_HOSTS = frozenset({"weworkremotely.com", "www.weworkremotely.com"})


def _value(item: ElementTree.Element, name: str) -> str:
    value = item.findtext(name)
    return value.strip() if isinstance(value, str) else ""


def parse_wwr_item(item: Any) -> NormalizedJob:
    if not isinstance(item, ElementTree.Element):
        raise SourceError("invalid_response", "We Work Remotely returned an invalid feed item.")
    combined_title = _value(item, "title")
    company, separator, title = combined_title.partition(":")
    if not separator:
        company, title = "", combined_title
    if not title.strip():
        raise SourceError("invalid_response", "A We Work Remotely listing is missing its title.")
    link = safe_url(_value(item, "link") or _value(item, "guid"), allowed_hosts=_HOSTS)
    slug = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1] or None
    try:
        return NormalizedJob(
            source="we_work_remotely",
            external_job_id=slug,
            title=title.strip(),
            company=company.strip(),
            location=_value(item, "region"),
            source_url=link,
            posted_at=posted_at(_value(item, "pubDate")),
            job_description=plain_text(_value(item, "description")),
            work_arrangement="Remote",
            remote_flag=True,
            raw_source_metadata={"category": _value(item, "category") or None, "guid": _value(item, "guid") or None},
        )
    except ValidationError:
        raise SourceError("invalid_response", "A We Work Remotely listing contains invalid field values.") from None


def parse_wwr_feed(document: bytes, max_items: int) -> list[NormalizedJob]:
    upper = document[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SourceError("invalid_response", "We Work Remotely returned an unsafe XML document.")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        raise SourceError("invalid_response", "We Work Remotely returned invalid RSS XML.") from None
    channel = root.find("channel") if root.tag.casefold() == "rss" else None
    if channel is None:
        raise SourceError("invalid_response", "We Work Remotely returned an invalid RSS channel.")
    return [parse_wwr_item(item) for item in channel.findall("item")[:max_items]]


class WeWorkRemotelyAdapter:
    def __init__(self, config: Any, settings: Any, client: httpx.AsyncClient, *,
                 limiter: RequestPacer | None = None, sleep: AsyncSleep = asyncio.sleep) -> None:
        self.config, self.settings, self.client = config, settings, client
        self.limiter = limiter if limiter is not None else _PACER
        self.sleep = sleep

    async def fetch(self) -> list[NormalizedJob]:
        document = await get_bytes(
            self.client, _FEED_URL, params={}, allowed_hosts=frozenset({"weworkremotely.com"}),
            timeout_seconds=self.settings.http_timeout_seconds, max_retries=self.settings.http_max_retries,
            backoff_seconds=self.settings.http_backoff_seconds, pacer=self.limiter,
            min_interval_seconds=self.config.min_request_interval_seconds, sleep=self.sleep,
        )
        return parse_wwr_feed(document, self.config.max_items)
