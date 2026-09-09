from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.db.database import Database
from app.schemas.jobs import NormalizedJob
from app.services.matching import MatchResult


NOW = datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc)
MATCH = MatchResult(True, ["Title: React developer", "Location: Manila"])


@pytest.fixture
def database(tmp_path):
    db = Database("sqlite:///./data/test.db", tmp_path)
    db.initialize()
    yield db
    db.close()


def job(identifier="one", **changes):
    values = {
        "source": "adzuna", "external_job_id": identifier,
        "title": "React developer", "company": "Example Co", "location": "Manila",
        "source_url": f"https://jobs.example.test/{identifier}",
        "work_arrangement": "Hybrid", "employment_type": "Full-time",
    }
    values.update(changes)
    return NormalizedJob(**values)


def ingest(db, listings=None, now=NOW, source="adzuna", scope="scope-a", match=MATCH):
    if listings is None:
        listings = [job()]
    return db.ingest([(listing, match) for listing in listings], source, scope, 30, now=now)


def test_database_init_restart_is_repeatable_and_preserves_data(database, tmp_path):
    assert database.path == (tmp_path / "data" / "test.db").resolve()
    assert database.health()
    ingest(database)
    database.patch_job(1, notes="Local private note", status="Applied")
    database.initialize()
    reloaded = Database("sqlite:///./data/test.db", tmp_path)
    reloaded.initialize()
    assert reloaded.get_job(1)["notes"] == "Local private note"
    assert reloaded.get_job(1)["status"] == "Applied"
    assert len(reloaded.get_job(1)["status_history"]) == 1
    with reloaded.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "job_status_history", "sync_runs", "source_sync_state", "saved_views", "source_request_log", "gmail_suggestions", "gmail_processed_messages"} <= tables


def test_memory_database_has_shared_lifetime(tmp_path):
    db = Database("sqlite:///:memory:", tmp_path)
    db.initialize()
    ingest(db)
    assert db.list_jobs({})["total"] == 1
    db.close()


@pytest.mark.parametrize("url", [
    "postgresql://localhost/jobs", "sqlite://", "sqlite:///", "sqlite:///file:db.sqlite",
    "sqlite://///host/share/db.sqlite", "sqlite:///\\\\host\\share\\db.sqlite",
    "sqlite:///x.sqlite?mode=ro", "sqlite:///http://example.test/db.sqlite",
])
def test_only_local_file_urls_allowed(tmp_path, url):
    with pytest.raises(ValueError):
        Database(url, tmp_path)


def test_newer_schema_fails_without_altering_it(database):
    with database.connection() as conn:
        conn.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="newer schema"):
        database.initialize()
    with database.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 999


def test_transaction_rolls_back_on_failure(database):
    with pytest.raises(RuntimeError):
        with database.transaction() as conn:
            conn.execute("INSERT INTO source_request_log(source, requested_at) VALUES ('adzuna', 'test')")
            raise RuntimeError("Simulated interruption")
    with database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_request_log").fetchone()[0] == 0


def test_deduplication_counts_unique_jobs_and_preserves_first_seen(database):
    assert ingest(database, [job(), job()]) == {"new_jobs_count": 1, "existing_jobs_count": 0, "stale_jobs_count": 0}
    initial = database.get_job(1)
    second = NOW + timedelta(days=1)
    result = ingest(database, [job(company="  EXAMPLE Co  ", title=" REACT  developer "), job()], now=second)
    assert result == {"new_jobs_count": 0, "existing_jobs_count": 1, "stale_jobs_count": 0}
    assert database.list_jobs({})["total"] == 1
    updated = database.get_job(1)
    assert updated["first_seen_at"] == initial["first_seen_at"]
    assert updated["date_found"] == initial["date_found"]
    assert datetime.fromisoformat(updated["last_seen_at"]) == second
    assert updated["normalized_title"] == "react developer"
    assert len(updated["dedupe_hash"]) == 64


def test_concurrent_ingestion_and_database_unique_constraint(database):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: ingest(database), range(12)))
    assert sum(result["new_jobs_count"] for result in results) == 1
    assert sum(result["existing_jobs_count"] for result in results) == 11
    assert database.list_jobs({})["total"] == 1
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        with database.transaction() as conn:
            columns = [row["name"] for row in conn.execute("PRAGMA table_info(jobs)") if row["name"] != "id"]
            fields = ", ".join(columns)
            conn.execute(f"INSERT INTO jobs ({fields}) SELECT {fields} FROM jobs WHERE id = 1")


def test_refresh_preserves_manual_status_notes_and_history(database):
    ingest(database)
    before = database.patch_job(1, status="Applied", notes="Sent portfolio manually")
    ingest(database, [job(job_description="Refreshed source description", salary_min=35000, salary_currency="PHP")], now=NOW + timedelta(days=3))
    after = database.get_job(1)
    assert after["status"] == "Applied"
    assert after["notes"] == "Sent portfolio manually"
    assert after["status_history"] == before["status_history"]
    assert after["first_seen_at"] == before["first_seen_at"]
    assert after["job_description"] == "Refreshed source description"
    assert after["salary_min"] == 35000
    assert after["salary_currency"] == "PHP"


def test_unqualified_new_jobs_not_inserted_but_known_jobs_are_seen(database):
    rejected = MatchResult(False, rejection_reason="Excluded company")
    assert ingest(database, match=rejected)["new_jobs_count"] == 0
    ingest(database)
    original = database.get_job(1)
    counts = ingest(database, [job(job_description="Do not update rejected metadata"), job("new")],
                    now=NOW + timedelta(days=31), match=rejected)
    assert counts["existing_jobs_count"] == 1
    assert counts["new_jobs_count"] == counts["stale_jobs_count"] == 0
    updated = database.get_job(1)
    assert updated["job_description"] == original["job_description"]
    assert updated["match_reasons"] == original["match_reasons"]
    assert datetime.fromisoformat(updated["last_seen_at"]) == NOW + timedelta(days=31)


def test_stale_marking_scoped_threshold_and_rediscovery(database):
    ingest(database, [job("a")])
    ingest(database, [job("b")], scope="scope-b")
    ingest(database, [job("c", source="remoteok")], source="remoteok")
    before = database.patch_job(1, status="Not Interested", notes="Keep this note")
    assert ingest(database, [], now=NOW + timedelta(days=30))["stale_jobs_count"] == 0
    assert ingest(database, [], now=NOW + timedelta(days=31))["stale_jobs_count"] == 1
    assert database.get_job(1)["is_stale"] is True
    assert database.get_job(2)["is_stale"] is False
    assert database.get_job(3)["is_stale"] is False
    assert ingest(database, [], now=NOW + timedelta(days=32))["stale_jobs_count"] == 0
    assert database.list_jobs({"stale": True})["total"] == 1
    assert ingest(database, [job("a")], now=NOW + timedelta(days=33))["existing_jobs_count"] == 1
    after = database.get_job(1)
    assert after["is_stale"] is False
    assert after["stale_marked_at"] is None
    assert after["status"] == before["status"]
    assert after["notes"] == before["notes"]
    assert after["status_history"] == before["status_history"]


def test_configurable_stale_threshold(database):
    ingest(database)
    counts = database.ingest([], "adzuna", "scope-a", stale_days=7, now=NOW + timedelta(days=8))
    assert counts["stale_jobs_count"] == 1
    with pytest.raises(ValueError):
        database.ingest([], "adzuna", "scope-a", stale_days=0)


def test_seen_scope_updates_and_cross_source_identity_stays_consistent(database):
    ingest(database)
    before = database.get_job(1)
    ingest(database, scope="new-scope", now=NOW + timedelta(days=31))
    assert database.get_job(1)["source_scope_key"] == "new-scope"
    ingest(database, [job(source="remoteok", external_job_id="different")], source="remoteok", scope="remote-scope", now=NOW + timedelta(days=32))
    after = database.get_job(1)
    assert after["source_scope_key"] == "new-scope"
    assert after["source"] == before["source"] == "adzuna"
    assert after["source_url"] == before["source_url"]
    assert after["external_job_id"] == before["external_job_id"]
    assert after["dedupe_hash"] == before["dedupe_hash"]


def test_job_fields_and_preferred_reasons_round_trip(database):
    listing = job(location="Metro Manila", work_arrangement="On-site", employment_type="Contract", remote_flag=False,
                  salary_min=30000, salary_max=40000, salary_currency="PHP", salary_period="month",
                  raw_source_metadata={"field": "safe fixture"}, posted_at=NOW - timedelta(days=1))
    match = MatchResult(True, ["Title: React developer", "Location: Metro Manila", "Preferred company"], True)
    ingest(database, [listing], match=match)
    row = database.get_job(1)
    assert row["title"] == listing.title
    assert row["location"] == "Metro Manila"
    assert row["match_reasons"] == match.reasons
    assert row["preferred_company"] is True
    assert row["raw_source_metadata"] == {"field": "safe fixture"}
    assert row["remote_flag"] is False
    assert row["salary_min"] == 30000
    assert row["salary_max"] == 40000
    assert row["salary_period"] == "month"
    assert row["status"] == "New"
    assert row["notes"] == ""


def test_status_validation_and_history_only_on_actual_changes(database):
    ingest(database)
    with pytest.raises(ValueError, match="status"):
        database.patch_job(1, status="Interviewing")
    with pytest.raises(ValueError, match="Notes"):
        database.patch_job(1, notes=None)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as conn:
            conn.execute("UPDATE jobs SET status = 'invalid' WHERE id = 1")
    assert database.get_job(1)["status"] == "New"
    assert database.patch_job(1, status="New")["status_history"] == []
    database.patch_job(1, notes="Initial note")
    database.patch_job(1, status="Applied")
    database.patch_job(1, status="Applied", notes="Revised note")
    result = database.patch_job(1, status="Declined")
    assert result["notes"] == "Revised note"
    history = result["status_history"]
    assert [(entry["previous_status"], entry["new_status"]) for entry in history] == [("New", "Applied"), ("Applied", "Declined")]
    assert all(entry["change_source"] == "manual_dashboard" for entry in history)
    assert database.patch_job(999, status="Applied") is None
    assert database.get_job(999) is None


@pytest.mark.parametrize("statement", [
    "UPDATE job_status_history SET new_status = 'Declined' WHERE id = 1",
    "DELETE FROM job_status_history WHERE id = 1",
    "INSERT OR REPLACE INTO job_status_history SELECT id, job_id, previous_status, 'Declined', change_source, changed_at FROM job_status_history WHERE id = 1",
])
def test_status_history_cannot_be_changed_or_deleted_even_with_direct_sql(database, statement):
    ingest(database)
    original = database.patch_job(1, status="Applied")["status_history"]
    database.initialize()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with database.transaction() as conn:
            conn.execute(statement)
    assert database.get_job(1)["status_history"] == original
    assert len(database.patch_job(1, status="Declined")["status_history"]) == 2


def test_filtering_sorting_pagination_and_global_summary(database):
    ingest(database, [job("a", company="Alpha"), job("b", company="Beta", work_arrangement="Remote", location="Remote", employment_type="Part-time")])
    ingest(database, [job("c", company="Gamma", source="remoteok", work_arrangement="On-site", employment_type="Contract")], source="remoteok", match=MatchResult(True, ["Preferred company"], True))
    database.patch_job(2, status="Applied")
    filters_and_ids = [
        ({"status": "Applied"}, [2]), ({"source": "remoteok"}, [3]),
        ({"work_arrangement": "Remote"}, [2]), ({"employment_type": "Contract"}, [3]),
        ({"preferred_company": True}, [3]), ({"preferred_company": False}, [2, 1]),
        ({"search": "ALPHA"}, [1]), ({"search": "React developer"}, [3, 2, 1]),
        ({"search": "Manila"}, [3, 1]), ({"stale": True}, []),
        ({"source": "adzuna", "work_arrangement": "Remote", "status": "Applied"}, [2]),
    ]
    for filters, expected in filters_and_ids:
        result = database.list_jobs(filters)
        assert [row["id"] for row in result["items"]] == expected
        assert result["total"] == len(expected)
        assert result["summary"] == {"New": 2, "Applied": 1, "Not Interested": 0, "Declined": 0, "Stale": 0}
    result = database.list_jobs({"page": 2, "page_size": 1, "sort_by": "company", "sort_order": "asc"})
    assert result["items"][0]["company"] == "Beta"
    assert result["total_pages"] == 3
    assert result["page"] == 2
    assert result["page_size"] == 1
    assert database.list_jobs({"page": 5})["items"] == []
    for field in ("date_found", "posted_at", "company", "title", "source", "status"):
        asc = [row["id"] for row in database.list_jobs({"sort_by": field, "sort_order": "asc"})["items"]]
        desc = [row["id"] for row in database.list_jobs({"sort_by": field, "sort_order": "desc"})["items"]]
        assert asc == list(reversed(desc))


def test_posting_date_sort_falls_back_to_date_found(database):
    ingest(database, [job("dated", posted_at=NOW - timedelta(days=1))])
    ingest(database, [job("unknown")], now=NOW + timedelta(days=1))
    assert [row["external_job_id"] for row in database.list_jobs({"sort_by": "posted_at", "sort_order": "desc"})["items"]] == ["unknown", "dated"]


def test_date_filters_use_manila_calendar_boundaries(database):
    local_day_start = datetime(2026, 9, 5, 16, tzinfo=timezone.utc)
    for key, instant in (("before", local_day_start - timedelta(microseconds=1)),
                         ("start", local_day_start),
                         ("end", local_day_start + timedelta(days=1, microseconds=-1)),
                         ("after", local_day_start + timedelta(days=1))):
        ingest(database, [job(key)], now=instant)
    rows = database.list_jobs({"date_from": date(2026, 9, 6), "date_to": "2026-09-06"})["items"]
    assert {row["external_job_id"] for row in rows} == {"start", "end"}


@pytest.mark.parametrize("filters, expected", [
    ({"date_to": "9999-12-31"}, 1),
    ({"date_from": "0001-01-01", "date_to": date.max}, 1),
    ({"date_to": "0001-01-01"}, 0),
    ({"date_from": "9999-12-31"}, 0),
])
def test_date_filter_extremes_do_not_overflow(database, filters, expected):
    ingest(database)
    assert database.list_jobs(filters)["total"] == expected


def test_search_treats_wildcards_and_sql_as_literal(database):
    ingest(database, [job("one", company="100%_Studio"), job("two", company="Other")])
    assert database.list_jobs({"search": "%_"})["total"] == 1
    assert database.list_jobs({"search": "%' OR 1=1 --"})["total"] == 0
    assert database.list_jobs({})["total"] == 2
    for filters in ({"sort_by": "title; DROP TABLE jobs"}, {"sort_order": "desc;--"}, {"page": 0}, {"page_size": 999}):
        with pytest.raises(ValueError):
            database.list_jobs(filters)


def test_sync_run_lifecycle_recovery_and_source_state(database):
    run_id = database.create_sync_run("manual")
    assert database.get_sync_run(run_id)["status"] == "running"
    failure = {"source": "remoteok", "code": "timeout", "message": "Request timed out"}
    database.update_sync_run(run_id, status="partial_failure", completed_at=NOW, sources_checked=["adzuna", "remoteok"], new_jobs_count=2, existing_jobs_count=3, stale_jobs_count=1, errors=[failure])
    result = database.get_sync_run(run_id)
    assert result["sources_checked"] == ["adzuna", "remoteok"]
    assert result["errors"] == [failure]
    assert result["new_jobs_count"] == 2
    scheduled = database.create_sync_run("scheduled")
    assert database.recover_interrupted_runs() == 1
    assert database.recover_interrupted_runs() == 0
    assert database.get_sync_run(scheduled)["status"] == "failed"
    assert database.get_sync_run(run_id)["status"] == "partial_failure"
    assert database.list_sync_runs(1)[0]["id"] == scheduled
    assert database.get_sync_run(999) is None
    database.set_source_state("adzuna", "scope-a", True)
    with database.connection() as conn:
        successful = dict(conn.execute("SELECT * FROM source_sync_state").fetchone())
    database.set_source_state("adzuna", "scope-a", False, error=failure)
    with database.connection() as conn:
        failed = dict(conn.execute("SELECT * FROM source_sync_state").fetchone())
    assert failed["last_success_at"] == successful["last_success_at"]
    assert json.loads(failed["last_error"]) == failure
    with pytest.raises(ValueError):
        database.create_sync_run("unapproved")
    with pytest.raises(ValueError):
        database.update_sync_run(run_id, status="invalid")
    with pytest.raises(ValueError):
        database.update_sync_run(run_id, trigger_type="scheduled")
    with pytest.raises(ValueError):
        database.update_sync_run(run_id, new_jobs_count=-1)


@pytest.mark.parametrize("count,age,window", [(25, timedelta(seconds=0), timedelta(seconds=60)), (250, timedelta(hours=2), timedelta(days=1)), (1000, timedelta(days=2), timedelta(days=7)), (2500, timedelta(days=8), timedelta(days=31))])
def test_persisted_request_quota_windows(database, tmp_path, count, age, window):
    with database.transaction() as conn:
        conn.executemany("INSERT INTO source_request_log(source, requested_at) VALUES (?, ?)",
                         [("adzuna", (NOW - age).isoformat(timespec="microseconds"))] * count)
    restarted = Database("sqlite:///./data/test.db", tmp_path)
    restarted.initialize()
    assert restarted.reserve_source_request("adzuna", NOW) is False
    assert restarted.reserve_source_request("other-source", NOW) is True
    assert restarted.reserve_source_request("adzuna", NOW - age + window) is True


def test_quota_reservations_are_atomic(database):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: database.reserve_source_request("adzuna", NOW), range(40)))
    assert sum(results) == 25
    with database.connection() as conn:
        rows = conn.execute("SELECT source, requested_at FROM source_request_log").fetchall()
    assert len(rows) == 25
    assert all(row["source"] == "adzuna" for row in rows)


def test_saved_view_crud_is_persistent_and_names_are_case_insensitive(database):
    created = database.create_saved_view(
        "New remote jobs", {"status": "New", "work_arrangement": "Remote"}, "date_found", "desc"
    )
    assert created["filters"] == {"status": "New", "work_arrangement": "Remote"}
    assert database.list_saved_views()[0]["name"] == "New remote jobs"
    with pytest.raises(ValueError, match="already exists"):
        database.create_saved_view("new REMOTE jobs", {}, "company", "asc")
    changed = database.update_saved_view(created["id"], name="Remote shortlist", sort_by="company", sort_order="asc")
    assert changed["name"] == "Remote shortlist"
    assert changed["filters"] == created["filters"]
    assert database.delete_saved_view(created["id"])
    assert database.list_saved_views() == []
    assert not database.delete_saved_view(created["id"])


def test_export_jobs_returns_all_filtered_rows_without_pagination(database):
    for index in range(3):
        ingest(database, [job(source_url=f"https://example.test/jobs/{index}", company=f"Company {index}")])
    database.patch_job(2, status="Applied", notes="=HYPERLINK(\"bad\")")
    rows = database.export_jobs({"status": "New", "sort_by": "company", "sort_order": "asc"})
    assert [row["company"] for row in rows] == ["Company 0", "Company 2"]


def test_database_backup_is_consistent_and_does_not_overwrite(database, tmp_path):
    ingest(database, [job()])
    destination = database.backup(tmp_path / "private-backups", NOW)
    assert destination.name.startswith("job_dashboard_backup_")
    assert destination.suffix == ".sqlite3"
    with sqlite3.connect(destination) as copy:
        assert copy.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    with pytest.raises(ValueError, match="already exists"):
        database.backup(tmp_path / "private-backups", NOW)
