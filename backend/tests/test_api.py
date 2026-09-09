import asyncio
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from test_refresh import FixtureAdapter


@pytest.fixture
def client(api_settings, example_job):
    app = create_app(api_settings, adapter_factories={"adzuna": lambda cfg: FixtureAdapter([example_job])})
    with TestClient(app, base_url="http://localhost:8000") as test_client:
        yield test_client


def refresh_and_wait(client):
    response = client.post("/api/refresh")
    assert response.status_code == 202
    client.portal.call(client.app.state.refresh.wait)
    return client.get(response.json()["status_url"]).json()


def test_start_without_credentials_health_and_no_automatic_fetch(api_settings):
    app = create_app(api_settings)
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert client.get("/api/health").json()["database"] == "ok"
        assert client.get("/api/sync-runs").json()["items"] == []
        assert client.get("/api/jobs").json()["total"] == 0
        assert refresh_and_wait(client)["state"] == "failed"


def test_manual_refresh_crud_history_and_no_duplicates(client):
    result = refresh_and_wait(client)
    assert result["state"] == "completed"
    assert result["new_jobs_count"] == 1
    row = client.get("/api/jobs").json()["items"][0]
    path = f"/api/jobs/{row['id']}"
    updated = client.patch(path, json={"status": "Applied", "notes": "Sent application myself."})
    assert updated.status_code == 200
    assert updated.json()["status"] == "Applied"
    assert updated.json()["status_history"][0]["change_source"] == "manual_dashboard"
    assert client.patch(path, json={"status": "Applied"}).status_code == 200
    assert len(client.get(path).json()["status_history"]) == 1
    assert client.patch(path, json={"notes": "Interview on Friday"}).json()["status"] == "Applied"
    second = refresh_and_wait(client)
    assert second["new_jobs_count"] == 0
    assert second["existing_jobs_count"] == 1
    detail = client.get(path).json()
    assert detail["notes"] == "Interview on Friday"
    assert detail["status"] == "Applied"
    assert detail["match_reasons"]
    assert detail["salary_min"] == 30_000
    assert client.get("/api/jobs").json()["summary"]["Applied"] == 1
    assert len(client.get("/api/sync-runs").json()["items"]) == 2


@pytest.mark.parametrize(("url", "source"), [
    ("https://www.linkedin.com/jobs/view/1", "linkedin"),
    ("https://ph.jobstreet.com/job/1", "jobstreet"),
    ("https://ph.indeed.com/viewjob?jk=1", "indeed"),
    ("https://ph.prosple.com/graduate-employers/example/jobs-internships/1", "prosple"),
    ("https://www.glassdoor.com/job-listing/example-JV_IC1.htm", "glassdoor"),
])
def test_manual_trusted_jobs_are_saved_without_automated_matching(client, url, source):
    response = client.post("/api/jobs/manual", json={"source_url": url, "title": "Frontend Developer", "company": source.title()})
    assert response.status_code == 201
    job = response.json()
    assert job["source"] == source and job["status"] == "New"
    assert job["location"] == "" and job["work_arrangement"] is None
    assert job["match_reasons"] == ["Manually saved from a trusted source"]


def test_manual_trusted_job_rejects_unapproved_and_duplicate_urls_without_overwriting(client):
    payload = {"source_url": "https://www.linkedin.com/jobs/view/123", "title": "Frontend Developer", "company": "Example Co"}
    created = client.post("/api/jobs/manual", json=payload)
    assert created.status_code == 201
    job_id = created.json()["id"]
    client.patch(f"/api/jobs/{job_id}", json={"status": "Applied", "notes": "Already applied."})
    duplicate = client.post("/api/jobs/manual", json=payload)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "duplicate_job"
    unchanged = client.get(f"/api/jobs/{job_id}").json()
    assert unchanged["status"] == "Applied" and unchanged["notes"] == "Already applied."
    rejected = client.post("/api/jobs/manual", json={**payload, "source_url": "https://example.com/jobs/123"})
    assert rejected.status_code == 422


@pytest.mark.parametrize("payload", [
    {"status": "Interview"}, {"status": None}, {"notes": None}, {},
    {"dedupe_hash": "different"}, {"source_url": "https://example.test"},
    {"title": "Different"}, {"match_reasons": []}, {"source": "fake"},
])
def test_write_api_only_allows_valid_explicit_status_and_notes(client, payload):
    refresh_and_wait(client)
    row = client.get("/api/jobs").json()["items"][0]
    response = client.patch(f"/api/jobs/{row['id']}", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert client.get(f"/api/jobs/{row['id']}").json()["status"] == "New"


@pytest.mark.parametrize("query", ["page=0", "page=9999999999999999999999999999999", "page_size=101", "sort_by=notes", "sort_order=sideways", "status=Maybe", "date_from=bad", "date_from=2026-02-01&date_to=2026-01-01"])
def test_filter_validation_structured_errors(client, query):
    assert client.get(f"/api/jobs?{query}").status_code == 422


def test_jobs_filters_and_pagination(client):
    refresh_and_wait(client)
    assert client.get("/api/jobs?status=New&source=adzuna&stale=false&work_arrangement=Hybrid&employment_type=Full-time&search=example&sort_by=company&sort_order=asc").json()["total"] == 1
    assert client.get("/api/jobs?status=Applied").json()["total"] == 0
    assert client.get("/api/jobs?preferred_company=true").json()["total"] == 0
    page = client.get("/api/jobs?page=2&page_size=1").json()
    assert page["total"] == 1 and page["items"] == [] and page["page"] == 2
    assert client.get("/api/jobs?search=%25").json()["total"] == 0


def test_not_found_errors(client):
    for path in ("/api/jobs/999", "/api/refresh/999", "/api/missing"):
        response = client.get(path)
        assert response.status_code == 404
        assert "error" in response.json()
    assert client.patch("/api/jobs/999", json={"notes": "Hi"}).status_code == 404
    assert client.get("/api/jobs/999999999999999999999999999").status_code == 422


def test_external_origins_cannot_start_refresh_or_read_via_cors(client):
    denied = client.post("/api/refresh", headers={"Origin": "https://untrusted.example"})
    assert denied.status_code == 403
    assert client.get("/api/sync-runs").json()["items"] == []
    preflight = client.options("/api/refresh", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/jobs", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in other.headers
    assert client.get("/api/jobs", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "[::1]:8000"}).status_code == 200
    assert client.get("/api/health", headers={"Host": "localhost:invalid"}).status_code == 400
    assert client.get("/api/docs").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/api/jobs").headers["cache-control"] == "no-store"


def test_api_refresh_conflict_returns_existing_run_id(api_settings, example_job):
    release = asyncio.Event()
    app = create_app(api_settings, adapter_factories={"adzuna": lambda cfg: FixtureAdapter([example_job], release=release)})
    with TestClient(app, base_url="http://localhost:8000") as client:
        first = client.post("/api/refresh")
        conflict = client.post("/api/refresh")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["run_id"] == first.json()["run_id"]
        client.portal.call(release.set)
        client.portal.call(app.state.refresh.wait)


def test_malformed_configuration_fails_startup(api_settings):
    (api_settings.config_dir / "sources.json").write_text('{"sources":{"unknown":{"enabled":true}}}', encoding="utf-8")
    with pytest.raises(ValueError):
        with TestClient(create_app(api_settings), base_url="http://localhost:8000"):
            pass


def test_scheduler_lifespan_starts_and_stops_cleanly(api_settings):
    settings = api_settings.model_copy(update={"scheduler_enabled": True})
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost:8000") as client:
        health = client.get("/api/health").json()
        assert health["scheduler_running"]
        assert health["next_refresh_at"].endswith("+08:00")
    assert not app.state.scheduler.running


def test_location_arrangement_is_filterable_after_normalization(api_settings, example_job):
    job = example_job.model_copy(update={"location": "Remote", "work_arrangement": None})
    app = create_app(api_settings, adapter_factories={"adzuna": lambda cfg: FixtureAdapter([job])})
    with TestClient(app, base_url="http://localhost:8000") as client:
        assert refresh_and_wait(client)["new_jobs_count"] == 1
        result = client.get("/api/jobs?work_arrangement=Remote").json()
        assert result["total"] == 1
        assert result["items"][0]["location"] == "Remote"
        assert "Work arrangement: Remote" in result["items"][0]["match_reasons"]


def test_saved_views_crud_validation_and_conflicts(client):
    payload = {"name": "New remote jobs", "filters": {"status": "New", "work_arrangement": "Remote"},
               "sort_by": "date_found", "sort_order": "desc"}
    created = client.post("/api/saved-views", json=payload)
    assert created.status_code == 201
    view = created.json()
    assert client.get("/api/saved-views").json()["items"] == [view]
    assert client.post("/api/saved-views", json={**payload, "name": "NEW REMOTE JOBS"}).status_code == 409
    renamed = client.patch(f"/api/saved-views/{view['id']}", json={"name": "Remote shortlist"})
    assert renamed.status_code == 200
    assert renamed.json()["filters"] == payload["filters"]
    assert client.patch(f"/api/saved-views/{view['id']}", json={"filters": {"date_from": "2026-02-02", "date_to": "2026-01-01"}}).status_code == 422
    assert client.delete(f"/api/saved-views/{view['id']}").status_code == 204
    assert client.delete(f"/api/saved-views/{view['id']}").status_code == 404


def test_filtered_csv_export_is_safe_and_complete(client):
    refresh_and_wait(client)
    row = client.get("/api/jobs").json()["items"][0]
    client.patch(f"/api/jobs/{row['id']}", json={"notes": "=HYPERLINK(\"https://bad.example\")"})
    response = client.get("/api/export/jobs.csv?status=New&sort_by=company&sort_order=asc")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    parsed = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(parsed) == 1
    assert parsed[0]["Title"] == "Junior React Developer"
    assert parsed[0]["Notes"].startswith("'=")
    assert "Title: react developer" in parsed[0]["Match reasons"]
    assert client.get("/api/export/jobs.csv?status=Applied").text.count("\n") <= 2


def test_local_database_backup_api(client, api_settings):
    refresh_and_wait(client)
    response = client.post("/api/backup")
    assert response.status_code == 201
    result = response.json()
    destination = api_settings.backup_dir / result["filename"]
    assert destination.exists()
    assert destination.resolve() == api_settings.backup_dir.resolve() / result["filename"]
    assert result["path"] == str(destination)
