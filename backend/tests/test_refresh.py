import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import httpx
import pytest

from app.adapters.base import SourceError
from app.adapters.http import RequestPacer
from app.config.preferences import load_preferences
from app.config.sources import load_sources
from app.db.database import Database
from app.scheduler.jobs import build_scheduler, scheduled_refresh
from app.services.matching import match_job
from app.services.refresh import RefreshConflict, RefreshService, scope_fingerprint
from conftest import enable_sources


FIXTURES = Path(__file__).parent / "fixtures"


class FixtureAdapter:
    def __init__(self, jobs=None, error=None, entered=None, release=None):
        self.jobs = jobs or []
        self.error = error
        self.entered, self.release = entered, release

    async def fetch(self):
        if self.entered:
            self.entered.set()
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return self.jobs


@pytest.fixture
def db(api_settings):
    database = Database(api_settings.database_url, api_settings.project_root)
    database.initialize()
    yield database
    database.close()


async def test_manual_and_scheduler_share_pipeline_preserve_tracking(db, api_settings, example_job):
    async with httpx.AsyncClient() as client:
        service = RefreshService(db, api_settings, client, {"adzuna": lambda config: FixtureAdapter([example_job, example_job])})
        first = await service.start("manual")
        await service.wait()
        assert db.get_sync_run(first)["new_jobs_count"] == 1
        job = db.list_jobs({})["items"][0]
        db.patch_job(job["id"], status="Applied", notes="Applied personally; follow up next week.")
        second = await scheduled_refresh(service)
        await service.wait()
        third = await service.start("manual")
        await service.wait()
        result = db.get_job(job["id"])
        assert db.list_jobs({})["total"] == 1
        assert result["status"] == "Applied"
        assert result["notes"] == "Applied personally; follow up next week."
        assert len(result["status_history"]) == 1
        assert result["first_seen_at"] == job["first_seen_at"]
        assert result["last_seen_at"] >= job["last_seen_at"]
        assert db.get_sync_run(second)["trigger_type"] == "scheduled"
        assert db.get_sync_run(third)["existing_jobs_count"] == 1


async def test_all_milestone_three_adapters_share_pipeline_dedupe_and_preserve_tracking(db, api_settings, monkeypatch):
    import app.adapters.greenhouse as greenhouse_module
    import app.adapters.himalayas as himalayas_module
    import app.adapters.lever as lever_module
    import app.adapters.remoteok as remoteok_module
    import app.adapters.remotive as remotive_module
    import app.adapters.jobicy as jobicy_module
    import app.adapters.we_work_remotely as wwr_module

    for module in (greenhouse_module, lever_module, remoteok_module, wwr_module, remotive_module, jobicy_module, himalayas_module):
        monkeypatch.setattr(module, "_PACER", RequestPacer(0))

    source_path = api_settings.config_dir / "sources.json"
    source_document = json.loads(source_path.read_text(encoding="utf-8"))
    source_document["sources"]["adzuna"]["enabled"] = False
    for source in ("remoteok", "we_work_remotely", "remotive", "jobicy", "himalayas", "greenhouse", "lever"):
        source_document["sources"][source]["enabled"] = True
        source_document["sources"][source]["min_request_interval_seconds"] = 0
    source_document["sources"]["greenhouse"]["company_slugs"] = ["acme"]
    source_document["sources"]["lever"]["company_slugs"] = ["acme"]
    source_path.write_text(json.dumps(source_document), encoding="utf-8")

    remoteok = json.loads((FIXTURES / "remoteok.json").read_text(encoding="utf-8"))
    greenhouse_board = json.loads((FIXTURES / "greenhouse_board.json").read_text(encoding="utf-8"))
    greenhouse_jobs = json.loads((FIXTURES / "greenhouse_jobs.json").read_text(encoding="utf-8"))
    lever_jobs = json.loads((FIXTURES / "lever_jobs.json").read_text(encoding="utf-8"))
    wwr = (FIXTURES / "we_work_remotely.rss").read_bytes()
    remotive = json.loads((FIXTURES / "remotive.json").read_text(encoding="utf-8"))
    jobicy = json.loads((FIXTURES / "jobicy.json").read_text(encoding="utf-8"))
    himalayas = json.loads((FIXTURES / "himalayas.json").read_text(encoding="utf-8"))

    def handler(request):
        if request.url.host == "remoteok.com":
            return httpx.Response(200, json=remoteok)
        if request.url.host == "weworkremotely.com":
            return httpx.Response(200, content=wwr)
        if request.url.host == "remotive.com":
            return httpx.Response(200, json=remotive)
        if request.url.host == "jobicy.com":
            return httpx.Response(200, json=jobicy)
        if request.url.host == "himalayas.app":
            return httpx.Response(200, json=himalayas)
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json=greenhouse_jobs if request.url.path.endswith("/jobs") else greenhouse_board)
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=lever_jobs)
        pytest.fail(f"Unexpected source host: {request.url.host}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = RefreshService(db, api_settings, client)
        first = await service.start("manual")
        await service.wait()
        first_run = db.get_sync_run(first)
        assert first_run["status"] == "completed"
        assert first_run["sources_checked"] == ["remoteok", "we_work_remotely", "remotive", "jobicy", "himalayas", "greenhouse", "lever"]
        assert first_run["new_jobs_count"] == 7
        tracked = db.list_jobs({"source": "remoteok"})["items"][0]
        db.patch_job(tracked["id"], status="Applied", notes="Manual test note")
        second = await service.start("scheduled")
        await service.wait()

    second_run = db.get_sync_run(second)
    assert second_run["new_jobs_count"] == 0
    assert second_run["existing_jobs_count"] == 7
    assert db.list_jobs({})["total"] == 7
    tracked_again = db.get_job(tracked["id"])
    assert tracked_again["status"] == "Applied"
    assert tracked_again["notes"] == "Manual test note"
    assert len(tracked_again["status_history"]) == 1


async def test_one_source_failure_does_not_stop_other_sources_or_mark_its_jobs_stale(db, api_settings, example_job):
    enable_sources(api_settings, remoteok=True)
    prefs = load_preferences(api_settings.config_dir / "job_preferences.json")
    config = load_sources(api_settings.config_dir / "sources.json")
    scope = scope_fingerprint(config.sources["adzuna"], prefs)
    db.ingest([(example_job, match_job(example_job, prefs))], "adzuna", scope, 30,
              now=datetime.now(timezone.utc) - timedelta(days=40))
    remote = example_job.model_copy(update={"source": "remoteok", "source_url": "https://jobs.example.test/remote"})
    async with httpx.AsyncClient() as client:
        service = RefreshService(db, api_settings, client, {
            "adzuna": lambda cfg: FixtureAdapter(error=SourceError("timeout", "Request timed out.")),
            "remoteok": lambda cfg: FixtureAdapter([remote]),
        })
        run_id = await service.start()
        await service.wait()
    run = db.get_sync_run(run_id)
    assert run["status"] == "partial_failure"
    assert run["sources_checked"] == ["adzuna", "remoteok"]
    assert run["new_jobs_count"] == 1
    assert run["stale_jobs_count"] == 0
    assert run["errors"][0]["source"] == "adzuna"
    assert not db.list_jobs({"source": "adzuna"})["items"][0]["is_stale"]


async def test_successful_relevant_refresh_marks_stale_and_rediscovery_revives(db, api_settings, example_job):
    prefs = load_preferences(api_settings.config_dir / "job_preferences.json")
    config = load_sources(api_settings.config_dir / "sources.json")
    scope = scope_fingerprint(config.sources["adzuna"], prefs)
    db.ingest([(example_job, match_job(example_job, prefs))], "adzuna", scope, 30,
              now=datetime.now(timezone.utc) - timedelta(days=31))
    adapter = FixtureAdapter([])
    async with httpx.AsyncClient() as client:
        service = RefreshService(db, api_settings, client, {"adzuna": lambda cfg: adapter})
        first = await service.start()
        await service.wait()
        assert db.get_sync_run(first)["stale_jobs_count"] == 1
        adapter.jobs = [example_job]
        second = await service.start()
        await service.wait()
    assert db.get_sync_run(second)["existing_jobs_count"] == 1
    assert db.list_jobs({"stale": False})["total"] == 1


async def test_concurrent_manual_and_scheduled_refreshes_conflict_then_release(db, api_settings, example_job):
    entered, release = asyncio.Event(), asyncio.Event()
    async with httpx.AsyncClient() as client:
        service = RefreshService(db, api_settings, client, {"adzuna": lambda cfg: FixtureAdapter([example_job], entered=entered, release=release)})
        run_id = await service.start()
        await entered.wait()
        with pytest.raises(RefreshConflict) as conflict:
            await service.start()
        assert conflict.value.run_id == run_id
        assert await scheduled_refresh(service) is None
        release.set()
        await service.wait()
        assert service.active_run_id is None
        await service.start()
        await service.wait()
    assert db.list_jobs({})["total"] == 1


async def test_missing_credentials_only_fail_on_attempt_and_release_lock(db, api_settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("Credentials missing: no HTTP request permitted"))) as client:
        service = RefreshService(db, api_settings, client)
        run_id = await service.start()
        await service.wait()
        run = db.get_sync_run(run_id)
        assert run["status"] == "failed"
        assert "ADZUNA_APP" in run["errors"][0]["message"]
        assert service.active_run_id is None


async def test_unknown_exception_is_sanitized_and_missing_test_factory_is_reported(db, api_settings):
    enable_sources(api_settings, lever=True)
    async with httpx.AsyncClient() as client:
        service = RefreshService(db, api_settings, client, {"adzuna": lambda cfg: FixtureAdapter(error=RuntimeError("private-api-key"))})
        run_id = await service.start()
        await service.wait()
        run = db.get_sync_run(run_id)
    assert run["status"] == "failed"
    assert len(run["errors"]) == 2
    assert "private-api-key" not in str(run)
    assert run["errors"][1]["code"] == "adapter_unavailable"


async def test_configuration_changes_take_effect_next_run_and_invalid_json_is_recorded(db, api_settings):
    async with httpx.AsyncClient() as client:
        service = RefreshService(db, api_settings, client, {})
        enable_sources(api_settings, adzuna=False)
        first = await service.start()
        await service.wait()
        assert db.get_sync_run(first)["status"] == "completed"
        assert db.get_sync_run(first)["sources_checked"] == []
        (api_settings.config_dir / "sources.json").write_text("{bad", encoding="utf-8")
        second = await service.start()
        await service.wait()
        assert db.get_sync_run(second)["status"] == "failed"
        assert service.active_run_id is None


def test_daily_scheduler_has_manila_eight_am_and_same_service(db, api_settings):
    service = object()
    scheduler = build_scheduler(service)
    job = scheduler.get_job("daily_job_refresh")
    assert job.func is scheduled_refresh
    assert job.args == (service,)
    assert str(job.trigger.timezone) == "Asia/Manila"
    next_run = job.trigger.get_next_fire_time(None, datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc))
    assert next_run.isoformat() == "2026-01-02T08:00:00+08:00"


def test_scope_changes_only_when_source_fetch_coverage_changes(api_settings):
    sources = load_sources(api_settings.config_dir / "sources.json")
    prefs = load_preferences(api_settings.config_dir / "job_preferences.json")
    original = scope_fingerprint(sources.sources["adzuna"], prefs)

    changed_preferences = prefs.model_copy(update={
        "stale_job_threshold_days": 60,
        "preferred_companies": ["Example Company"],
    })
    pacing_only = sources.sources["adzuna"].model_copy(update={
        "enabled": False,
        "min_request_interval_seconds": 10,
    })
    assert scope_fingerprint(sources.sources["adzuna"], changed_preferences) == original
    assert scope_fingerprint(pacing_only, prefs) == original

    query_change = sources.sources["adzuna"].model_copy(update={"query": "react developer"})
    page_change = sources.sources["adzuna"].model_copy(update={"max_pages": 1})
    assert scope_fingerprint(query_change, prefs) != original
    assert scope_fingerprint(page_change, prefs) != original
