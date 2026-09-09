import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.scheduler.jobs import build_scheduler
from app.services.gmail import GMAIL_READONLY_SCOPE, GmailService, detect_application_signal, sanitized_excerpt
from app.db.database import Database
from test_database import ingest


@pytest.fixture
def database(tmp_path):
    db = Database("sqlite:///data/test.db", tmp_path)
    db.initialize()
    yield db
    db.close()


def gmail_settings(token_path):
    return SimpleNamespace(gmail_client_id="desktop-client", gmail_client_secret=SimpleNamespace(get_secret_value=lambda: "secret"),
        gmail_redirect_uri="http://localhost:8000/api/gmail/auth/callback", gmail_token_path=token_path)


def encoded(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def message(message_id="m-1", body="Thank you for applying to Example Co.", subject="Regarding your application"):
    return {"id": message_id, "internalDate": "1788746400000", "payload": {"mimeType": "text/plain",
        "headers": [{"name": "From", "value": "Example Co Recruiting <jobs@example.test>"},
                    {"name": "Subject", "value": subject}],
        "body": {"data": encoded(body)}}}


def write_token(path):
    path.write_text(json.dumps({"access_token": "access", "refresh_token": "refresh", "scope": GMAIL_READONLY_SCOPE,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}), encoding="utf-8")


def test_oauth_requests_only_readonly_scope(database, tmp_path):
    service = GmailService(database, gmail_settings(tmp_path / "token.json"), httpx.AsyncClient())
    query = parse_qs(urlsplit(service.authorization_url()).query)
    assert query["scope"] == [GMAIL_READONLY_SCOPE]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0]


@pytest.mark.parametrize(("content", "status"), [
    ("Thank you for applying", "Applied"), ("We regret to inform you", "Declined"),
    ("Unfortunately we are moving forward", "Declined"), ("Recruiter introduction", None),
])
def test_pattern_detection(content, status):
    assert detect_application_signal(content)[1] == status


def test_excerpt_redacts_addresses_links_and_discards_html():
    excerpt = sanitized_excerpt("<b>Hello</b> kurt@example.com https://tracker.test/x")
    assert excerpt == "Hello [email] [link]"


@pytest.mark.asyncio
async def test_check_generates_linked_suggestion_and_deduplicates_message(database, tmp_path):
    ingest(database)
    token = tmp_path / "token.json"; write_token(token)
    calls = []
    async def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m-1"}]})
        return httpx.Response(200, json=message())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = GmailService(database, gmail_settings(token), client)
        first = await service.check(); second = await service.check()
    assert first["suggestions_created"] == 1
    assert second["duplicates_skipped"] == 1
    items = database.list_gmail_suggestions()
    assert len(items) == 1 and items[0]["job_id"] == 1 and items[0]["suggested_status"] == "Applied"
    assert sum(url.endswith("/m-1?format=full") for url in calls) == 1


@pytest.mark.asyncio
async def test_unmatched_suggestion_remains_unlinked(database, tmp_path):
    token = tmp_path / "token.json"; write_token(token)
    async def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m-2"}]})
        return httpx.Response(200, json=message("m-2", "Thank you for applying to Unknown Studio."))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GmailService(database, gmail_settings(token), client).check()
    item = database.list_gmail_suggestions()[0]
    assert item["job_id"] is None and item["state"] == "pending"


@pytest.mark.asyncio
async def test_non_signal_message_id_is_recorded_to_avoid_repeat_body_processing(database, tmp_path):
    token = tmp_path / "token.json"; write_token(token)
    detail_calls = 0
    async def handler(request):
        nonlocal detail_calls
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m-no-signal"}]})
        detail_calls += 1
        return httpx.Response(200, json=message("m-no-signal", "Your weekly newsletter is ready.", "Weekly newsletter"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = GmailService(database, gmail_settings(token), client)
        assert (await service.check())["suggestions_created"] == 0
        assert (await service.check())["duplicates_skipped"] == 1
    assert detail_calls == 1
    assert database.list_gmail_suggestions() == []


def test_confirmation_changes_status_with_history_and_dismissal_does_not(database):
    ingest(database)
    base = {"sender": "recruiter@example.test", "subject": "Application received", "received_at": datetime.now(timezone.utc),
            "inferred_company": "Example Co", "suggested_status": "Applied", "sanitized_excerpt": "Application received.",
            "matched_pattern": "application received", "job_id": 1}
    database.create_gmail_suggestion({**base, "gmail_message_id": "confirm"})
    database.create_gmail_suggestion({**base, "gmail_message_id": "dismiss", "suggested_status": "Declined"})
    assert database.confirm_gmail_suggestion(1)["state"] == "confirmed"
    detail = database.get_job(1)
    assert detail["status"] == "Applied"
    assert detail["status_history"][-1]["change_source"] == "gmail_suggestion_confirmed"
    assert database.dismiss_gmail_suggestion(2)["state"] == "dismissed"
    assert database.get_job(1)["status"] == "Applied"


def test_gmail_api_confirmation_is_explicit_and_audited(api_settings):
    app = create_app(api_settings)
    with TestClient(app, base_url="http://localhost:8000") as client:
        ingest(client.app.state.db)
        base = {"sender": "jobs@example.test", "subject": "Application received", "received_at": datetime.now(timezone.utc),
                "inferred_company": "Example Co", "suggested_status": "Applied", "sanitized_excerpt": "Application received.",
                "matched_pattern": "application received", "job_id": 1, "gmail_message_id": "api-confirm"}
        client.app.state.db.create_gmail_suggestion(base)
        assert client.get("/api/gmail/suggestions?state=pending&linked=true").json()["items"][0]["state"] == "pending"
        assert client.get("/api/jobs/1").json()["status"] == "New"
        response = client.post("/api/gmail/suggestions/1/confirm")
        assert response.status_code == 200 and response.json()["state"] == "confirmed"
        job = client.get("/api/jobs/1").json()
        assert job["status"] == "Applied"
        assert job["status_history"][-1]["change_source"] == "gmail_suggestion_confirmed"
        assert client.post("/api/gmail/suggestions/1/confirm").status_code == 409


def test_scheduler_registers_manila_quarter_hour_gmail_check(database, tmp_path):
    from app.services.refresh import RefreshService
    settings = gmail_settings(tmp_path / "token.json")
    settings.scheduler_enabled = True
    gmail = GmailService(database, settings, httpx.AsyncClient())
    refresh = SimpleNamespace()
    scheduler = build_scheduler(refresh, gmail)
    job = scheduler.get_job("gmail_readonly_check")
    assert str(job.trigger.fields[6]) == "*/15"
    assert str(job.trigger.timezone) == "Asia/Manila"
