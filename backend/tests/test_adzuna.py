"""Synthetic fixtures based on the documented API; tests make no network calls."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.adzuna import AdzunaAdapter, parse_adzuna_job
from app.adapters.base import SourceError
from app.adapters.http import RequestPacer, get_json


@pytest.fixture
def payload():
    return json.loads((Path(__file__).parent / "fixtures" / "adzuna_search.json").read_text(encoding="utf-8"))


def config(**overrides):
    return SimpleNamespace(**({"country": "ph", "results_per_page": 50, "max_pages": 3, "query": "developer", "min_request_interval_seconds": 0} | overrides))


def settings(**overrides):
    return SimpleNamespace(**({"adzuna_app_id": SecretStr("test-id"), "adzuna_app_key": SecretStr("private-test-key"), "http_timeout_seconds": 15, "http_max_retries": 2, "http_backoff_seconds": 1} | overrides))


async def no_sleep(seconds):
    pass


def adapter(client, *, source_config=None, app_settings=None, **kwargs):
    return AdzunaAdapter(source_config or config(), app_settings or settings(), client, limiter=RequestPacer(0, sleep=no_sleep), sleep=no_sleep, **kwargs)


@pytest.mark.asyncio
async def test_fixture_parsing_and_documented_get_request(payload):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.adzuna.com"
        assert request.url.path == "/v1/api/jobs/ph/search/1"
        assert request.headers["accept"] == "application/json"
        assert request.url.params["app_id"] == "test-id"
        assert request.url.params["app_key"] == "private-test-key"
        assert request.url.params["sort_by"] == "date"
        assert request.url.params["results_per_page"] == "50"
        assert request.extensions["timeout"]["read"] == 15
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await adapter(client).fetch()
    assert len(requests) == 1
    first, remote, unknown = jobs
    assert first.source == "adzuna"
    assert first.external_job_id == "example-1001"
    assert first.title == " Junior React Developer "
    assert first.company == "Example Software Co."
    assert first.location == "Makati, Metro Manila"
    assert first.posted_at == datetime(2026, 9, 1, 10, 15, tzinfo=timezone.utc)
    assert first.job_description == "Build React & Laravel apps. Hybrid work."
    assert first.employment_type == "Full-time"
    assert first.work_arrangement == "Hybrid"
    assert (first.salary_min, first.salary_max, first.salary_currency, first.salary_period) == (30000, 45000, "PHP", "month")
    assert first.raw_source_metadata["salary_is_predicted"] is False
    assert first.raw_source_metadata["description_is_excerpt"] is True
    assert "description" not in first.raw_source_metadata
    assert "redirect_url" not in first.raw_source_metadata
    assert remote.remote_flag is True
    assert remote.work_arrangement == "Remote"
    assert remote.employment_type == "Contract"
    assert remote.external_job_id == "1002"
    assert remote.posted_at == datetime(2026, 9, 1, 3, 30, tzinfo=timezone.utc)
    assert remote.raw_source_metadata["salary_is_predicted"] is True
    assert remote.salary_currency is None  # No guessed currency or annual unit.
    assert remote.salary_period is None
    assert unknown.location == ""
    assert unknown.company == ""
    assert unknown.remote_flag is None
    assert unknown.work_arrangement is None
    assert unknown.employment_type is None  # Permanent does not imply full-time.
    assert unknown.posted_at is None


@pytest.mark.asyncio
async def test_missing_credentials_are_only_checked_when_fetch_is_attempted():
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        source = adapter(client, app_settings=settings(adzuna_app_key=SecretStr("")))
        with pytest.raises(SourceError) as error:
            await source.fetch()
    assert error.value.code == "missing_credentials"
    assert "ADZUNA_APP_KEY" in error.value.message
    assert requests == []


@pytest.mark.asyncio
async def test_maximum_pages_is_enforced(payload):
    requests = []
    one = payload["results"][0]

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"count": 1000, "results": [one]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await adapter(client, source_config=config(results_per_page=1, max_pages=2)).fetch()
    assert len(jobs) == 2
    assert [request.url.path.rsplit("/", 1)[1] for request in requests] == ["1", "2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "short", "count"])
async def test_pagination_stops_when_results_exhausted(payload, kind):
    requests = []
    content = {"results": [] if kind == "empty" else [payload["results"][0]]}
    if kind == "count":
        content["count"] = 1

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await adapter(client, source_config=config(results_per_page=1 if kind == "count" else 50)).fetch()
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 401, 403])
async def test_permanent_failures_are_sanitized_and_not_retried(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "private-test-key and a private payload"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client).fetch()
    assert len(requests) == 1
    assert error.value.code == ("country_or_query_rejected" if status in (400, 404) else "credentials_rejected")
    assert "private-test-key" not in str(error.value)
    assert "api.adzuna.com" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "network", "429", "503"])
async def test_transient_get_failures_retry_exponentially_and_reserve_each_attempt(failure):
    requests, sleeps, reservations = [], [], []

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            if failure == "timeout":
                raise httpx.ReadTimeout("private-test-key", request=request)
            if failure == "network":
                raise httpx.ConnectError("private-test-key", request=request)
            return httpx.Response(int(failure))
        return httpx.Response(200, json={"results": []})

    async def sleep(seconds):
        sleeps.append(seconds)

    async def reserve():
        reservations.append(True)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = AdzunaAdapter(config(), settings(), client, limiter=RequestPacer(0), before_request=reserve, sleep=sleep)
        assert await source.fetch() == []
    assert len(requests) == len(reservations) == 3
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_timeout_after_retries_has_safe_public_error():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("GET https://api.adzuna.com/?app_key=private-test-key", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client).fetch()
    assert len(requests) == 3
    assert error.value.code == "request_timeout"
    assert str(error.value) == "Request timed out."


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["3600", "99999999999999999", "inf", "Fri, 01 Jan 2100 00:00:00 GMT"])
async def test_large_retry_after_aborts_without_early_retry(retry_after):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": retry_after})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client).fetch()
    assert error.value.code == "retry_deferred"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_retry_after_is_honored():
    requests, sleeps = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "7"}) if len(requests) == 1 else httpx.Response(200, json={"results": []})

    async def sleep(seconds):
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await AdzunaAdapter(config(), settings(), client, limiter=RequestPacer(0), sleep=sleep).fetch()
    assert sleeps == [7]


@pytest.mark.asyncio
async def test_retry_after_http_date_is_honored():
    requests, sleeps = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "Mon, 07 Sep 2026 08:00:09 GMT"}) if len(requests) == 1 else httpx.Response(200, json={"results": []})

    async def sleep(seconds):
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await get_json(client, "https://api.adzuna.com/test", params={}, allowed_hosts=frozenset({"api.adzuna.com"}), timeout_seconds=15, max_retries=1, backoff_seconds=1, pacer=RequestPacer(0), sleep=sleep, now=lambda: datetime(2026, 9, 7, 8, tzinfo=timezone.utc))
    assert sleeps == [9]


@pytest.mark.asyncio
async def test_long_retry_after_also_blocks_next_manual_fetch_until_cooldown_expires():
    requests = []
    clock = [100.0]
    limiter = RequestPacer(0, monotonic=lambda: clock[0])

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            with pytest.raises(SourceError) as error:
                await AdzunaAdapter(config(), settings(), client, limiter=limiter, sleep=no_sleep).fetch()
            assert error.value.code == "retry_deferred"
        assert len(requests) == 1
        clock[0] += 3601
        with pytest.raises(SourceError):
            await AdzunaAdapter(config(), settings(), client, limiter=limiter, sleep=no_sleep).fetch()
        assert len(requests) == 2


@pytest.mark.asyncio
async def test_redirects_are_never_followed_even_with_client_redirects_enabled():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://not-approved.example/collect"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client).fetch()
    assert error.value.code == "redirect_blocked"
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], {}, {"results": None}, {"results": "not-a-list"}, {"results": [{}]}, {"results": [None]}])
async def test_malformed_payload_fails_source(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client).fetch()
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_invalid_json_is_safe():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="private-test-key"))) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client).fetch()
    assert error.value.code == "invalid_response"
    assert "private-test-key" not in str(error.value)


@pytest.mark.asyncio
async def test_oversized_page_rejected(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client, source_config=config(results_per_page=1)).fetch()
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_later_page_failure_does_not_return_partial_results(payload):
    def handler(request):
        if request.url.path.endswith("/1"):
            return httpx.Response(200, json={"results": [payload["results"][0]], "count": 2})
        return httpx.Response(200, json={"results": "invalid"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError):
            await adapter(client, source_config=config(results_per_page=1)).fetch()


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//example.com/job", "https://user:pass@example.com/job", "https://example.com:invalid/job", "https://example.com/\njob", ""])
def test_unsafe_job_urls_rejected(payload, url):
    item = payload["results"][0] | {"redirect_url": url}
    with pytest.raises(SourceError) as error:
        parse_adzuna_job(item)
    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("values, expected", [({"contract_time": "part_time"}, "Part-time"), ({"contract_time": "", "contract_type": "freelance"}, "Freelance"), ({"contract_time": "", "contract_type": "internship"}, "Internship")])
def test_employment_types(payload, values, expected):
    assert parse_adzuna_job(payload["results"][0] | values).employment_type == expected


def test_optional_malformed_values_are_not_invented(payload):
    item = payload["results"][0] | {"salary_min": "NaN", "salary_max": -10, "salary_currency": "pesos", "salary_period": "unknown", "created": "bad-date", "work_arrangement": "on_site", "remote": False}
    job = parse_adzuna_job(item)
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_currency is None
    assert job.salary_period is None
    assert job.posted_at is None
    assert job.work_arrangement == "On-site"
    assert job.remote_flag is False


def test_inverted_salary_range_rejected(payload):
    with pytest.raises(SourceError) as error:
        parse_adzuna_job(payload["results"][0] | {"salary_min": 60000, "salary_max": 30000})
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_pacing_survives_repeat_adapter_fetches():
    clock = [100.0]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    pacer = RequestPacer(2.5, monotonic=lambda: clock[0], sleep=sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": []}))) as client:
        await AdzunaAdapter(config(), settings(), client, limiter=pacer, sleep=sleep).fetch()
        await AdzunaAdapter(config(), settings(), client, limiter=pacer, sleep=sleep).fetch()
    assert sleeps == [2.5]


@pytest.mark.asyncio
async def test_pacer_serializes_concurrent_requests():
    clock = [100.0]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        await asyncio.sleep(0)

    pacer = RequestPacer(2.5, monotonic=lambda: clock[0], sleep=sleep)
    await asyncio.gather(pacer.wait(), pacer.wait(), pacer.wait())
    assert sleeps == [2.5, 2.5]


@pytest.mark.asyncio
async def test_budget_callback_can_prevent_request():
    requests = []

    async def deny():
        raise SourceError("api_budget_exhausted", "Local API request budget reached.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        with pytest.raises(SourceError) as error:
            await adapter(client, before_request=deny).fetch()
    assert error.value.code == "api_budget_exhausted"
    assert requests == []


@pytest.mark.asyncio
async def test_http_helper_refuses_unapproved_hosts():
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceError) as error:
            await get_json(client, "https://not-approved.example/jobs", params={}, allowed_hosts=frozenset({"api.adzuna.com"}), timeout_seconds=15, max_retries=0, backoff_seconds=1, pacer=RequestPacer(0))
    assert error.value.code == "unapproved_endpoint"
