import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.base import SourceError
from app.adapters.greenhouse import GreenhouseAdapter, parse_greenhouse_job
from app.adapters.himalayas import HimalayasAdapter, parse_himalayas_job
from app.adapters.http import RequestPacer
from app.adapters.lever import LeverAdapter, parse_lever_job
from app.adapters.jobicy import JobicyAdapter, parse_jobicy_job
from app.adapters.remotive import RemotiveAdapter, _salary as remotive_salary, parse_remotive_job
from app.adapters.remoteok import RemoteOKAdapter, parse_remoteok_job
from app.adapters.we_work_remotely import WeWorkRemotelyAdapter, parse_wwr_feed
from app.config.sources import FeedSourceConfig, GreenhouseSourceConfig, HimalayasSourceConfig, JobicySourceConfig, LeverSourceConfig, RemotiveSourceConfig

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def settings():
    return SimpleNamespace(http_timeout_seconds=5, http_max_retries=0, http_backoff_seconds=0)


@pytest.mark.asyncio
async def test_remoteok_fixture_parses_official_feed_and_skips_legal_record():
    payload = load_json("remoteok.json")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await RemoteOKAdapter(FeedSourceConfig(max_items=20), settings(), client, limiter=RequestPacer(0)).fetch()
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.source, job.external_job_id, job.title, job.company) == ("remoteok", "1001", "Junior React Developer", "Example Labs")
    assert job.location == "Anywhere"
    assert job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.employment_type == "Full-time"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (35000, 50000, None)
    assert job.posted_at == datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
    assert job.job_description == "Build accessible React interfaces."
    assert requests[0].url == "https://remoteok.com/api"
    assert requests[0].headers["accept"] == "application/json"


def test_remoteok_rejects_malformed_listing_and_non_provider_link():
    with pytest.raises(SourceError, match="missing its title"):
        parse_remoteok_job({})
    item = load_json("remoteok.json")[1] | {"url": "https://example.test/job"}
    with pytest.raises(SourceError) as error:
        parse_remoteok_job(item)
    assert error.value.code == "invalid_response"


def test_remoteok_missing_geography_and_zero_salary_are_not_invented():
    item = load_json("remoteok.json")[1]
    item = {key: value for key, value in item.items() if key != "location"} | {"salary_min": 0, "salary_max": 0}
    job = parse_remoteok_job(item)
    assert job.location == ""
    assert job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.salary_min is None and job.salary_max is None


@pytest.mark.asyncio
async def test_remotive_fixture_parses_official_api_and_filters_software_category():
    payload, requests = load_json("remotive.json"), []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, json=payload))) as client:
        jobs = await RemotiveAdapter(RemotiveSourceConfig(max_items=20), settings(), client, limiter=RequestPacer(0)).fetch()
    job = jobs[0]
    assert (job.source, job.external_job_id, job.title, job.company) == ("remotive", "7001", "Junior React Developer", "Example Remote Labs")
    assert job.location == "Worldwide" and job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.employment_type == "Full-time"
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (40000, 50000, "USD", "year")
    assert job.job_description == "Build accessible React interfaces."
    assert requests[0].url.params["category"] == "software-dev"
    assert requests[0].url.params["limit"] == "20"


@pytest.mark.asyncio
async def test_remotive_enforces_local_max_when_provider_ignores_limit():
    payload = load_json("remotive.json")
    payload["jobs"] = payload["jobs"] * 3
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        jobs = await RemotiveAdapter(RemotiveSourceConfig(max_items=2), settings(), client, limiter=RequestPacer(0)).fetch()
    assert len(jobs) == 2


def test_remotive_rejects_malformed_listing_and_provider_escape():
    with pytest.raises(SourceError, match="missing its title"):
        parse_remotive_job({})
    with pytest.raises(SourceError):
        parse_remotive_job(load_json("remotive.json")["jobs"][0] | {"url": "https://example.test/job"})


def test_remotive_salary_handles_thousands_and_decimal_comma_k_notation():
    assert remotive_salary("$40,000 - $50,000 yearly") == (40000, 50000, "USD", "year")
    assert remotive_salary("$31,2k - $52k")[:3] == (31200, 52000, "USD")


@pytest.mark.asyncio
async def test_jobicy_fixture_parses_official_api_and_filters_engineering():
    payload, requests = load_json("jobicy.json"), []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, json=payload))) as client:
        jobs = await JobicyAdapter(JobicySourceConfig(max_items=20), settings(), client, limiter=RequestPacer(0)).fetch()
    job = jobs[0]
    assert (job.source, job.external_job_id, job.title, job.company) == ("jobicy", "8001", "Junior Full Stack Developer", "Example APAC Studio")
    assert job.location == "APAC / Anywhere" and job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.employment_type == "Full-time"
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (35000, 50000, "USD", "year")
    assert job.job_description == "Build React and PHP features."
    assert requests[0].url.params["industry"] == "engineering"
    assert requests[0].url.params["count"] == "20"


def test_jobicy_rejects_inverted_salary_and_provider_escape():
    item = load_json("jobicy.json")["jobs"][0]
    with pytest.raises(SourceError, match="salary range"):
        parse_jobicy_job(item | {"salaryMin": 90000, "salaryMax": 10000})
    with pytest.raises(SourceError):
        parse_jobicy_job(item | {"url": "https://example.test/job"})


@pytest.mark.asyncio
async def test_himalayas_search_is_filtered_and_parsed():
    payload, requests = load_json("himalayas.json"), []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, json=payload))) as client:
        jobs = await HimalayasAdapter(HimalayasSourceConfig(max_pages=3, queries=["developer", "react developer"]), settings(), client, limiter=RequestPacer(0)).fetch()
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.source, job.title, job.company) == ("himalayas", "Junior Frontend Developer", "Example PH")
    assert job.location == "Philippines" and job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.employment_type == "Full-time"
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (40000, 45000, "PHP", "month")
    assert job.job_description == "Build accessible React applications."
    params = requests[0].url.params
    assert params["country"] == "Philippines"
    assert params["seniority"] == "Entry-level"
    assert params["employment_type"] == "Full Time"
    assert params["exclude_worldwide"] == "true"
    assert len(requests) == 2
    assert requests[1].url.params["q"] == "react developer"


def test_himalayas_rejects_bad_salary_and_non_provider_link():
    item = load_json("himalayas.json")["jobs"][0]
    with pytest.raises(SourceError, match="salary range"):
        parse_himalayas_job(item | {"minSalary": 50000, "maxSalary": 10000})
    with pytest.raises(SourceError):
        parse_himalayas_job(item | {"applicationLink": "https://example.test/job"})


@pytest.mark.asyncio
async def test_wwr_fixture_parses_official_programming_rss():
    document = (FIXTURES / "we_work_remotely.rss").read_bytes()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=document))) as client:
        jobs = await WeWorkRemotelyAdapter(FeedSourceConfig(max_items=20), settings(), client, limiter=RequestPacer(0)).fetch()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "we_work_remotely"
    assert job.company == "Sample Studio" and job.title == "Junior Frontend Developer"
    assert job.location == "Anywhere" and job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.posted_at == datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
    assert job.job_description == "Build usable frontend features with React."
    assert job.source_url.startswith("https://weworkremotely.com/remote-jobs/")


@pytest.mark.parametrize("document", [b"not xml", b"<rss></rss>", b"<!DOCTYPE rss><rss><channel /></rss>"])
def test_wwr_rejects_malformed_or_unsafe_xml(document):
    with pytest.raises(SourceError) as error:
        parse_wwr_feed(document, 20)
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_greenhouse_fixtures_use_board_name_content_metadata_and_pay_range():
    board, payload = load_json("greenhouse_board.json"), load_json("greenhouse_jobs.json")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload if request.url.path.endswith("/jobs") else board)

    config = GreenhouseSourceConfig(company_slugs=["acme"], max_items_per_company=20)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await GreenhouseAdapter(config, settings(), client, limiter=RequestPacer(0)).fetch()
    job = jobs[0]
    assert (job.source, job.external_job_id, job.company) == ("greenhouse", "127817", "Acme Philippines")
    assert job.location == "Metro Manila, Philippines (Hybrid)" and job.work_arrangement == "Hybrid"
    assert job.employment_type == "Full-time"
    assert (job.salary_min, job.salary_max, job.salary_currency) == (35000, 50000, "PHP")
    assert job.posted_at == datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
    assert job.job_description == "Build React interfaces with a Laravel team."
    assert [request.url.path for request in requests] == ["/v1/boards/acme", "/v1/boards/acme/jobs"]
    assert requests[1].url.params["content"] == "false"


def test_greenhouse_rejects_missing_location_and_inverted_salary():
    item = load_json("greenhouse_jobs.json")["jobs"][0]
    with pytest.raises(SourceError):
        parse_greenhouse_job(item | {"location": None}, company="Acme", board_slug="acme")
    bad = item | {"pay_input_ranges": [{"min_cents": 900, "max_cents": 100, "currency_type": "PHP"}]}
    with pytest.raises(SourceError, match="salary range"):
        parse_greenhouse_job(bad, company="Acme", board_slug="acme")


@pytest.mark.asyncio
async def test_lever_fixture_parses_and_honors_page_limit():
    payload = load_json("lever_jobs.json")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload if request.url.params["skip"] == "0" else [])

    config = LeverSourceConfig(company_slugs=["acme"], results_per_page=1, max_pages=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await LeverAdapter(config, settings(), client, limiter=RequestPacer(0)).fetch()
    job = jobs[0]
    assert job.source == "lever" and job.company == "acme"
    assert job.title == "Full Stack Developer" and job.location == "Remote"
    assert job.work_arrangement == "Remote" and job.remote_flag is True
    assert job.employment_type == "Full-time"
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == (40000, 60000, "PHP", "month")
    assert job.posted_at == datetime.fromtimestamp(1788600000, tz=timezone.utc)
    assert len(requests) == 2
    assert [request.url.params["skip"] for request in requests] == ["0", "1"]
    assert all(request.url.params["mode"] == "json" for request in requests)


def test_lever_rejects_invalid_categories_and_non_provider_link():
    item = load_json("lever_jobs.json")[0]
    with pytest.raises(SourceError):
        parse_lever_job(item | {"categories": None}, company_slug="acme")
    with pytest.raises(SourceError) as error:
        parse_lever_job(item | {"hostedUrl": "https://example.test/job"}, company_slug="acme")
    assert error.value.code == "invalid_response"


def test_lever_accepts_employee_suffix_in_employment_type():
    item = load_json("lever_jobs.json")[0]
    categories = {**item["categories"], "commitment": "Full-time Employee"}
    job = parse_lever_job(item | {"categories": categories}, company_slug="acme")
    assert job.employment_type == "Full-time"


@pytest.mark.asyncio
async def test_empty_company_slug_lists_make_no_requests():
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        assert await GreenhouseAdapter(GreenhouseSourceConfig(), settings(), client, limiter=RequestPacer(0)).fetch() == []
        assert await LeverAdapter(LeverSourceConfig(), settings(), client, limiter=RequestPacer(0)).fetch() == []
    assert requests == []


@pytest.mark.asyncio
async def test_board_failure_identifies_configured_slug_without_exposing_response_body():
    private_body = "private-upstream-response"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404, text=private_body))) as client:
        with pytest.raises(SourceError) as error:
            await GreenhouseAdapter(GreenhouseSourceConfig(company_slugs=["acme"]), settings(), client, limiter=RequestPacer(0)).fetch()
    assert error.value.code == "http_404"
    assert "acme" in error.value.message
    assert private_body not in error.value.message


@pytest.mark.asyncio
async def test_feed_response_size_limit_is_enforced_before_xml_parsing():
    oversized = b"x" * 2_000_001
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=oversized))) as client:
        with pytest.raises(SourceError) as error:
            await WeWorkRemotelyAdapter(FeedSourceConfig(), settings(), client, limiter=RequestPacer(0)).fetch()
    assert error.value.code == "response_too_large"
