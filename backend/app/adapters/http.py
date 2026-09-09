"""GET-only requests with explicit timeouts, pacing, and bounded retries."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.adapters.base import SourceError

AsyncSleep = Callable[[float], Awaitable[None]]
BeforeRequest = Callable[[], Awaitable[None]]


class RequestPacer:
    """Reuse one instance across refreshes to avoid bursts between searches."""

    def __init__(
        self,
        min_interval_seconds: float = 2.5,
        *,
        sleep: AsyncSleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    def defer(self, seconds: float) -> None:
        """Keep an upstream cooldown across searches without sleeping a worker."""
        self._blocked_until = max(self._blocked_until, self._monotonic() + seconds)

    async def wait(self, minimum: float = 0.0) -> None:
        async with self._lock:
            if self._blocked_until > self._monotonic():
                raise SourceError("retry_deferred", "Source retry cooldown is still active. Try again later.")
            interval = max(self.min_interval_seconds, minimum)
            if self._last_request is not None:
                remaining = interval - (self._monotonic() - self._last_request)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_request = self._monotonic()


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        # An infinite/malicious delay must never become an immediate retry.
        return max(0.0, seconds) if math.isfinite(seconds) else math.inf
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str | int],
    allowed_hosts: frozenset[str],
    timeout_seconds: float,
    max_retries: int,
    backoff_seconds: float,
    pacer: RequestPacer,
    min_interval_seconds: float = 0.0,
    before_request: BeforeRequest | None = None,
    sleep: AsyncSleep = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_retry_delay_seconds: float = 30.0,
    max_response_bytes: int = 5_000_000,
) -> Any:
    """Fetch JSON without redirects; retry only transient GET failures.

    `before_request` reserves a persisted API-budget slot for EVERY HTTP attempt,
    including retries. A SourceError from that callback propagates unchanged.
    Large Retry-After values abort the source safely instead of retrying early.
    Error messages intentionally omit request URLs and upstream body contents.
    """
    response = await _get_response(
        client, url, params=params, allowed_hosts=allowed_hosts,
        timeout_seconds=timeout_seconds, max_retries=max_retries,
        backoff_seconds=backoff_seconds, pacer=pacer,
        min_interval_seconds=min_interval_seconds, before_request=before_request,
        sleep=sleep, now=now, max_retry_delay_seconds=max_retry_delay_seconds,
        max_response_bytes=max_response_bytes, accept="application/json",
    )
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        raise SourceError("invalid_response", "Source returned invalid JSON.") from None


async def get_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str | int],
    allowed_hosts: frozenset[str],
    timeout_seconds: float,
    max_retries: int,
    backoff_seconds: float,
    pacer: RequestPacer,
    min_interval_seconds: float = 0.0,
    before_request: BeforeRequest | None = None,
    sleep: AsyncSleep = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_retry_delay_seconds: float = 30.0,
    max_response_bytes: int = 2_000_000,
    accept: str = "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
) -> bytes:
    response = await _get_response(
        client, url, params=params, allowed_hosts=allowed_hosts,
        timeout_seconds=timeout_seconds, max_retries=max_retries,
        backoff_seconds=backoff_seconds, pacer=pacer,
        min_interval_seconds=min_interval_seconds, before_request=before_request,
        sleep=sleep, now=now, max_retry_delay_seconds=max_retry_delay_seconds,
        max_response_bytes=max_response_bytes, accept=accept,
    )
    return response.content


async def _get_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, str | int],
    allowed_hosts: frozenset[str],
    timeout_seconds: float,
    max_retries: int,
    backoff_seconds: float,
    pacer: RequestPacer,
    min_interval_seconds: float,
    before_request: BeforeRequest | None,
    sleep: AsyncSleep,
    now: Callable[[], datetime],
    max_retry_delay_seconds: float,
    max_response_bytes: int,
    accept: str,
) -> httpx.Response:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise SourceError("unapproved_endpoint", "The source endpoint is not approved.")

    for attempt in range(max_retries + 1):
        await pacer.wait(min_interval_seconds)
        if before_request is not None:
            await before_request()

        response = None
        try:
            response = await client.get(
                url,
                params=params,
                headers={"Accept": accept},
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            error = SourceError("request_timeout", "Request timed out.")
        except httpx.RequestError:
            error = SourceError("network_error", "Could not reach the source API.")
        else:
            status = response.status_code
            if status == 200:
                if len(response.content) > max_response_bytes:
                    raise SourceError("response_too_large", "Source response exceeded the configured safety limit.")
                return response
            if 300 <= status < 400:
                raise SourceError("redirect_blocked", "Source returned a redirect; no redirect was followed.")
            if status in (401, 403):
                raise SourceError("credentials_rejected", "Source rejected the configured API credentials or access.")
            if status not in (408, 425, 429) and not 500 <= status < 600:
                raise SourceError(f"http_{status}", f"Source rejected the request (HTTP {status}).")
            error = SourceError(
                "rate_limited" if status == 429 else "upstream_unavailable",
                "Source API rate limit reached." if status == 429 else f"Source API is temporarily unavailable (HTTP {status}).",
            )

        delay = backoff_seconds * (2**attempt)
        retry_after = None
        if response is not None:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"), now())
            if retry_after is not None:
                delay = max(delay, retry_after)
        if attempt == max_retries:
            if retry_after is not None:
                pacer.defer(retry_after)
            raise error from None
        if delay > max_retry_delay_seconds:
            pacer.defer(delay)
            raise SourceError(
                "retry_deferred", "Source requested a long retry delay. Try again later; no early retry was made."
            )
        await sleep(delay)

    raise AssertionError("unreachable")
