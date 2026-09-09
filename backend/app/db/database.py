"""Small, transactional SQLite data-access layer.

Every mutation opens its own transaction, so scheduler and request threads can
share a Database instance. SQLite uniqueness remains the final dedupe safeguard.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


STATUSES = ("New", "Applied", "Not Interested", "Declined")
_MISSING = object()
_UTC = timezone.utc
_MANILA = ZoneInfo("Asia/Manila")
_SCHEMA_VERSION = 2


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(_UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=_UTC)
    return value.astimezone(_UTC)


def _stamp(value: datetime | str | None = None) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    return vars(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("raw_source_metadata", "match_reasons", "errors", "sources_checked", "filters"):
        if key in result:
            result[key] = json.loads(result[key]) if result[key] else None
    for key in ("preferred_company", "is_stale", "remote_flag"):
        if key in result and result[key] is not None:
            result[key] = bool(result[key])
    return result


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    dedupe_hash TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    external_job_id TEXT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    company TEXT NOT NULL,
    normalized_company TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    normalized_location TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL,
    posted_at TEXT,
    date_found TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    match_reasons TEXT NOT NULL DEFAULT '[]',
    job_description TEXT,
    work_arrangement TEXT,
    employment_type TEXT,
    remote_flag INTEGER CHECK(remote_flag IS NULL OR remote_flag IN (0, 1)),
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    salary_period TEXT,
    raw_source_metadata TEXT NOT NULL DEFAULT '{}',
    source_scope_key TEXT NOT NULL,
    preferred_company INTEGER NOT NULL DEFAULT 0 CHECK(preferred_company IN (0, 1)),
    is_stale INTEGER NOT NULL DEFAULT 0 CHECK(is_stale IN (0, 1)),
    stale_marked_at TEXT,
    status TEXT NOT NULL DEFAULT 'New' CHECK(status IN ('New', 'Applied', 'Not Interested', 'Declined')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_source_scope_seen ON jobs(source, source_scope_key, last_seen_at);
CREATE INDEX IF NOT EXISTS ix_jobs_date_found ON jobs(date_found);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS job_status_history (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    previous_status TEXT NOT NULL CHECK(previous_status IN ('New', 'Applied', 'Not Interested', 'Declined')),
    new_status TEXT NOT NULL CHECK(new_status IN ('New', 'Applied', 'Not Interested', 'Declined')),
    change_source TEXT NOT NULL CHECK(change_source IN ('manual_dashboard', 'gmail_suggestion_confirmed')),
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_status_history_job ON job_status_history(job_id, changed_at);
CREATE TRIGGER IF NOT EXISTS status_history_no_update
BEFORE UPDATE ON job_status_history
BEGIN
    SELECT RAISE(ABORT, 'Status history is immutable.');
END;
CREATE TRIGGER IF NOT EXISTS status_history_no_delete
BEFORE DELETE ON job_status_history
BEGIN
    SELECT RAISE(ABORT, 'Status history is immutable.');
END;
CREATE TRIGGER IF NOT EXISTS status_history_no_replace
BEFORE INSERT ON job_status_history
WHEN EXISTS (SELECT 1 FROM job_status_history WHERE id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'Status history is immutable.');
END;
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('scheduled', 'manual')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'partial_failure', 'failed')),
    sources_checked TEXT NOT NULL DEFAULT '[]',
    new_jobs_count INTEGER NOT NULL DEFAULT 0,
    existing_jobs_count INTEGER NOT NULL DEFAULT 0,
    stale_jobs_count INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS source_sync_state (
    source TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    last_success_at TEXT,
    last_attempt_at TEXT NOT NULL,
    last_error TEXT,
    PRIMARY KEY(source, scope_key)
);
CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '{}',
    sort_by TEXT NOT NULL DEFAULT 'date_found',
    sort_order TEXT NOT NULL DEFAULT 'desc',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_saved_views_name ON saved_views(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS gmail_suggestions (
    id INTEGER PRIMARY KEY,
    gmail_message_id TEXT NOT NULL UNIQUE,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    received_at TEXT NOT NULL,
    inferred_company TEXT,
    suggested_status TEXT CHECK(suggested_status IS NULL OR suggested_status IN ('Applied', 'Declined')),
    sanitized_excerpt TEXT NOT NULL,
    matched_pattern TEXT NOT NULL,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'confirmed', 'dismissed')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_gmail_suggestions_state ON gmail_suggestions(state, received_at);
CREATE INDEX IF NOT EXISTS ix_gmail_suggestions_job ON gmail_suggestions(job_id, received_at);
CREATE TABLE IF NOT EXISTS gmail_processed_messages (
    gmail_message_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_request_log (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    requested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_source_request_window ON source_request_log(source, requested_at);
"""


class Database:
    def __init__(self, database_url: str, project_root: Path):
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must use sqlite:/// with a local file path.")
        raw_path = database_url[len(prefix):]
        if not raw_path or "?" in raw_path or "#" in raw_path or "\x00" in raw_path:
            raise ValueError("DATABASE_URL must identify one local SQLite file.")
        self._keeper: sqlite3.Connection | None = None
        self.path: Path | None = None
        if raw_path == ":memory:":
            self._target = f"file:job_dashboard_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
            self._keeper = self._connect()
        else:
            if raw_path.startswith(("//", "\\\\", "file:")) or "://" in raw_path:
                raise ValueError("DATABASE_URL must reference a local file, not a URI or network share.")
            path = Path(raw_path)
            if path.drive and not path.is_absolute():
                raise ValueError("DATABASE_URL cannot use a drive-relative path.")
            self.path = (path if path.is_absolute() else project_root / path).resolve()
            self._target = str(self.path)
            self._uri = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._target, timeout=30, isolation_level=None, uri=self._uri)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > _SCHEMA_VERSION:
                raise ValueError("This database uses a newer schema; upgrade the application to open it.")
            connection.execute("PRAGMA journal_mode = WAL")
            # executescript normally commits first; wrap the entire schema in
            # its own explicit transaction so interrupted setup is repeatable.
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + f"\nPRAGMA user_version = {_SCHEMA_VERSION};\nCOMMIT;")
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def close(self) -> None:
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None

    def health(self) -> bool:
        try:
            with self.connection() as connection:
                return connection.execute("SELECT COUNT(*) FROM jobs").fetchone() is not None
        except sqlite3.Error:
            return False

    def ingest(self, jobs: list[tuple[Any, Any]], source: str, scope_key: str,
               stale_days: int, now: datetime | None = None) -> dict[str, int]:
        from app.services.matching import dedupe_hash, normalize_text

        if stale_days < 1:
            raise ValueError("Stale threshold must be at least one day.")
        timestamp = _stamp(now)
        threshold = _stamp(_utc(now) - timedelta(days=stale_days))
        counts = {"new_jobs_count": 0, "existing_jobs_count": 0, "stale_jobs_count": 0}
        seen: set[str] = set()
        with self.transaction() as connection:
            for raw_job, raw_match in jobs:
                job, match = _mapping(raw_job), _mapping(raw_match)
                identity = dedupe_hash(job.get("company"), job.get("title"), job.get("source_url"))
                if identity in seen:
                    continue
                seen.add(identity)
                existing = connection.execute("SELECT id, source FROM jobs WHERE dedupe_hash = ?", (identity,)).fetchone()
                if existing is not None:
                    connection.execute(
                        "UPDATE jobs SET last_seen_at = ?, updated_at = ?, is_stale = 0, stale_marked_at = NULL, "
                        "source_scope_key = CASE WHEN source = ? THEN ? ELSE source_scope_key END WHERE id = ?",
                        (timestamp, timestamp, source, scope_key, existing["id"]),
                    )
                    counts["existing_jobs_count"] += 1
                    if not match.get("qualified", False):
                        continue
                elif not match.get("qualified", False):
                    continue

                values = {
                    "title": job.get("title") or "",
                    "normalized_title": normalize_text(job.get("title")),
                    "company": job.get("company") or "",
                    "normalized_company": normalize_text(job.get("company")),
                    "location": job.get("location") or "",
                    "normalized_location": normalize_text(job.get("location")),
                    "posted_at": _stamp(job["posted_at"]) if job.get("posted_at") else None,
                    "match_reasons": _json(match.get("reasons", match.get("match_reasons", []))),
                    "job_description": job.get("job_description"),
                    "work_arrangement": job.get("work_arrangement"),
                    "employment_type": job.get("employment_type"),
                    "remote_flag": job.get("remote_flag"),
                    "salary_min": job.get("salary_min"),
                    "salary_max": job.get("salary_max"),
                    "salary_currency": job.get("salary_currency"),
                    "salary_period": job.get("salary_period"),
                    "raw_source_metadata": _json(job.get("raw_source_metadata") or {}),
                    "preferred_company": bool(match.get("preferred_company", False)),
                }
                if existing is not None:
                    assignments = ", ".join(f"{field} = ?" for field in values)
                    connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", [*values.values(), existing["id"]])
                else:
                    values.update({
                        "dedupe_hash": identity,
                        "source": source,
                        "external_job_id": str(job["external_job_id"]) if job.get("external_job_id") is not None else None,
                        "source_url": job.get("source_url") or "",
                        "source_scope_key": scope_key,
                        "date_found": timestamp,
                        "first_seen_at": timestamp,
                        "last_seen_at": timestamp,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    })
                    columns = ", ".join(values)
                    placeholders = ", ".join("?" for _ in values)
                    connection.execute(f"INSERT INTO jobs ({columns}) VALUES ({placeholders})", list(values.values()))
                    counts["new_jobs_count"] += 1
            cursor = connection.execute(
                "UPDATE jobs SET is_stale = 1, stale_marked_at = ?, updated_at = ? "
                "WHERE source = ? AND source_scope_key = ? AND last_seen_at < ? AND is_stale = 0",
                (timestamp, timestamp, source, scope_key, threshold),
            )
            counts["stale_jobs_count"] = cursor.rowcount
        return counts

    def create_manual_job(self, *, source: str, title: str, company: str, source_url: str,
                          now: datetime | None = None) -> dict[str, Any] | None:
        """Create one user-selected record without applying automated matching or staleness."""
        from app.services.matching import dedupe_hash, normalize_text

        timestamp = _stamp(now)
        identity = dedupe_hash(company, title, source_url)
        with self.transaction() as connection:
            existing = connection.execute("SELECT id FROM jobs WHERE dedupe_hash = ?", (identity,)).fetchone()
            if existing is not None:
                return None
            cursor = connection.execute(
                "INSERT INTO jobs (dedupe_hash, source, title, normalized_title, company, normalized_company, "
                "location, normalized_location, source_url, date_found, first_seen_at, last_seen_at, "
                "match_reasons, raw_source_metadata, source_scope_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, '{}', 'manual_saved', ?, ?)",
                (identity, source, title, normalize_text(title), company, normalize_text(company), source_url,
                 timestamp, timestamp, timestamp, _json(["Manually saved from a trusted source"]), timestamp, timestamp),
            )
            job_id = cursor.lastrowid
        return self.get_job(job_id)

    @staticmethod
    def _filters(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for field in ("status", "source", "work_arrangement", "employment_type"):
            if filters.get(field) is not None:
                clauses.append(f"{field} = ?")
                parameters.append(filters[field])
        for field, column in (("stale", "is_stale"), ("preferred_company", "preferred_company")):
            if filters.get(field) is not None:
                value = filters[field]
                if isinstance(value, str):
                    if value.lower() not in ("true", "false", "0", "1"):
                        raise ValueError(f"{field} must be a boolean.")
                    value = value.lower() in ("true", "1")
                clauses.append(f"{column} = ?")
                parameters.append(int(bool(value)))
        for field, operator, advance in (("date_from", ">=", 0), ("date_to", "<", 1)):
            if filters.get(field) is not None:
                value = filters[field]
                local_date = value if isinstance(value, date) else date.fromisoformat(value)
                if isinstance(local_date, datetime):
                    local_date = local_date.date()
                # Python cannot represent the day after 9999-12-31. Extreme
                # calendar dates instead mean there is no bound on that side.
                if (field == "date_to" and local_date == date.max) or (field == "date_from" and local_date == date.min):
                    continue
                boundary = datetime.combine(local_date + timedelta(days=advance), time.min, tzinfo=_MANILA)
                clauses.append(f"date_found {operator} ?")
                parameters.append(_stamp(boundary))
        if filters.get("search"):
            # Search text is literal: percent and underscore are not wildcards.
            value = str(filters["search"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(title LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\')")
            parameters.extend([f"%{value}%"] * 3)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters

    def list_jobs(self, filters: dict[str, Any]) -> dict[str, Any]:
        where, parameters = self._filters(filters)
        sorting = {
            "date_found": "date_found", "posted_at": "COALESCE(posted_at, date_found)",
            "source": "source COLLATE NOCASE", "status": "status COLLATE NOCASE",
            "company": "company COLLATE NOCASE", "title": "title COLLATE NOCASE",
        }
        sort_by = filters.get("sort_by", "date_found")
        sort_order = str(filters.get("sort_order", "desc")).lower()
        if sort_by not in sorting or sort_order not in ("asc", "desc"):
            raise ValueError("Unsupported sort setting.")
        page, page_size = int(filters.get("page", 1)), int(filters.get("page_size", 50))
        if page < 1 or not 1 <= page_size <= 200:
            raise ValueError("page must be positive and page_size must be between 1 and 200.")
        with self.connection() as connection:
            # One read transaction keeps count, rows, and summary coherent.
            connection.execute("BEGIN")
            total = connection.execute("SELECT COUNT(*) FROM jobs" + where, parameters).fetchone()[0]
            rows = connection.execute(
                "SELECT * FROM jobs" + where + f" ORDER BY {sorting[sort_by]} {sort_order}, id {sort_order} LIMIT ? OFFSET ?",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
            summary = dict.fromkeys(STATUSES, 0)
            for row in connection.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"):
                summary[row["status"]] = row["count"]
            summary["Stale"] = connection.execute("SELECT COUNT(*) FROM jobs WHERE is_stale = 1").fetchone()[0]
        return {"items": [_row(row) for row in rows], "total": total, "page": page,
                "page_size": page_size, "total_pages": math.ceil(total / page_size), "summary": summary}

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("BEGIN")
            result = _row(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
            if result is not None:
                result["status_history"] = [dict(row) for row in connection.execute(
                    "SELECT * FROM job_status_history WHERE job_id = ? ORDER BY changed_at ASC, id ASC", (job_id,)
                )]
            return result

    def gmail_message_exists(self, message_id: str) -> bool:
        with self.connection() as connection:
            return connection.execute(
                "SELECT 1 FROM gmail_suggestions WHERE gmail_message_id = ? UNION ALL "
                "SELECT 1 FROM gmail_processed_messages WHERE gmail_message_id = ? LIMIT 1", (message_id, message_id)
            ).fetchone() is not None

    def mark_gmail_message_processed(self, message_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO gmail_processed_messages (gmail_message_id, processed_at) VALUES (?, ?)",
                (message_id, _stamp()),
            )

    def gmail_company_jobs(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT id, company, normalized_company, title, status FROM jobs "
                "WHERE normalized_company <> '' ORDER BY length(normalized_company) DESC, id"
            )]

    def create_gmail_suggestion(self, suggestion: dict[str, Any]) -> bool:
        now = _stamp()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO gmail_suggestions "
                "(gmail_message_id, sender, subject, received_at, inferred_company, suggested_status, "
                "sanitized_excerpt, matched_pattern, job_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (suggestion["gmail_message_id"], suggestion["sender"], suggestion["subject"],
                 _stamp(suggestion["received_at"]), suggestion.get("inferred_company"),
                 suggestion.get("suggested_status"), suggestion["sanitized_excerpt"],
                 suggestion["matched_pattern"], suggestion.get("job_id"), now),
            )
            return cursor.rowcount == 1

    def list_gmail_suggestions(self, state: str | None = None, linked: bool | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if state is not None:
            clauses.append("g.state = ?"); params.append(state)
        if linked is not None:
            clauses.append("g.job_id IS NOT NULL" if linked else "g.job_id IS NULL")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT g.*, j.title AS job_title, j.company AS job_company FROM gmail_suggestions g "
                "LEFT JOIN jobs j ON j.id = g.job_id" + where + " ORDER BY g.received_at DESC, g.id DESC", params
            ).fetchall()
            return [_row(row) for row in rows]

    def get_gmail_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            return _row(connection.execute(
                "SELECT g.*, j.title AS job_title, j.company AS job_company FROM gmail_suggestions g "
                "LEFT JOIN jobs j ON j.id = g.job_id WHERE g.id = ?", (suggestion_id,)
            ).fetchone())

    def dismiss_gmail_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE gmail_suggestions SET state = 'dismissed', resolved_at = ? WHERE id = ? AND state = 'pending'",
                (_stamp(), suggestion_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_gmail_suggestion(suggestion_id)

    def confirm_gmail_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        now = _stamp()
        with self.transaction() as connection:
            suggestion = connection.execute(
                "SELECT * FROM gmail_suggestions WHERE id = ? AND state = 'pending'", (suggestion_id,)
            ).fetchone()
            if suggestion is None or suggestion["job_id"] is None or suggestion["suggested_status"] is None:
                return None
            job = connection.execute("SELECT status FROM jobs WHERE id = ?", (suggestion["job_id"],)).fetchone()
            if job is None:
                return None
            if job["status"] != suggestion["suggested_status"]:
                connection.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                                   (suggestion["suggested_status"], now, suggestion["job_id"]))
                connection.execute(
                    "INSERT INTO job_status_history (job_id, previous_status, new_status, change_source, changed_at) "
                    "VALUES (?, ?, ?, 'gmail_suggestion_confirmed', ?)",
                    (suggestion["job_id"], job["status"], suggestion["suggested_status"], now),
                )
            connection.execute("UPDATE gmail_suggestions SET state = 'confirmed', resolved_at = ? WHERE id = ?",
                               (now, suggestion_id))
        return self.get_gmail_suggestion(suggestion_id)

    def export_jobs(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Return one consistent, unpaginated filtered snapshot for local export."""
        where, parameters = self._filters(filters)
        sorting = {
            "date_found": "date_found", "posted_at": "COALESCE(posted_at, date_found)",
            "source": "source COLLATE NOCASE", "status": "status COLLATE NOCASE",
            "company": "company COLLATE NOCASE", "title": "title COLLATE NOCASE",
        }
        sort_by = filters.get("sort_by", "date_found")
        sort_order = str(filters.get("sort_order", "desc")).lower()
        if sort_by not in sorting or sort_order not in ("asc", "desc"):
            raise ValueError("Unsupported sort setting.")
        with self.connection() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM jobs" + where + f" ORDER BY {sorting[sort_by]} {sort_order}, id {sort_order}",
                parameters,
            ).fetchall()
            return [_row(row) for row in rows]

    def list_saved_views(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [_row(row) for row in connection.execute(
                "SELECT * FROM saved_views ORDER BY name COLLATE NOCASE ASC, id ASC"
            )]

    def create_saved_view(self, name: str, filters: dict[str, Any], sort_by: str, sort_order: str) -> dict[str, Any]:
        name = " ".join(name.split())
        if not name:
            raise ValueError("Saved view name must not be blank.")
        timestamp = _stamp()
        with self.transaction() as connection:
            if connection.execute("SELECT 1 FROM saved_views WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
                raise ValueError("A saved view with that name already exists.")
            cursor = connection.execute(
                "INSERT INTO saved_views (name, filters, sort_by, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, _json(filters), sort_by, sort_order, timestamp, timestamp),
            )
            view_id = int(cursor.lastrowid)
        return self.get_saved_view(view_id)

    def get_saved_view(self, view_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            return _row(connection.execute("SELECT * FROM saved_views WHERE id = ?", (view_id,)).fetchone())

    def update_saved_view(self, view_id: int, *, name: Any = _MISSING, filters: Any = _MISSING,
                          sort_by: Any = _MISSING, sort_order: Any = _MISSING) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        if name is not _MISSING:
            name = " ".join(name.split())
            if not name:
                raise ValueError("Saved view name must not be blank.")
            updates["name"] = name
        if filters is not _MISSING:
            updates["filters"] = _json(filters)
        if sort_by is not _MISSING:
            updates["sort_by"] = sort_by
        if sort_order is not _MISSING:
            updates["sort_order"] = sort_order
        if not updates:
            raise ValueError("Provide at least one saved-view change.")
        updates["updated_at"] = _stamp()
        with self.transaction() as connection:
            if connection.execute("SELECT 1 FROM saved_views WHERE id = ?", (view_id,)).fetchone() is None:
                return None
            if "name" in updates and connection.execute(
                "SELECT 1 FROM saved_views WHERE name = ? COLLATE NOCASE AND id != ?", (updates["name"], view_id)
            ).fetchone():
                raise ValueError("A saved view with that name already exists.")
            assignments = ", ".join(f"{field} = ?" for field in updates)
            connection.execute(f"UPDATE saved_views SET {assignments} WHERE id = ?", [*updates.values(), view_id])
        return self.get_saved_view(view_id)

    def delete_saved_view(self, view_id: int) -> bool:
        with self.transaction() as connection:
            return connection.execute("DELETE FROM saved_views WHERE id = ?", (view_id,)).rowcount == 1

    def backup(self, destination_dir: Path, now: datetime | None = None) -> Path:
        """Create a consistent SQLite copy in an application-controlled directory."""
        if self.path is None:
            raise ValueError("An in-memory database cannot be backed up to a user database copy.")
        destination_dir = destination_dir.resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        stamp = _utc(now).strftime("%Y%m%dT%H%M%S_%fZ")
        destination = destination_dir / f"job_dashboard_backup_{stamp}.sqlite3"
        if destination.exists():
            raise ValueError("A backup with that timestamp already exists.")
        with self.connection() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    def patch_job(self, job_id: int, *, status: Any = _MISSING, notes: Any = _MISSING) -> dict[str, Any] | None:
        if status is not _MISSING and status not in STATUSES:
            raise ValueError("Invalid application status.")
        if notes is not _MISSING and not isinstance(notes, str):
            raise ValueError("Notes must be text.")
        timestamp = _stamp()
        with self.transaction() as connection:
            current = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if current is None:
                return None
            if status is not _MISSING and status != current["status"]:
                connection.execute(
                    "INSERT INTO job_status_history (job_id, previous_status, new_status, change_source, changed_at) VALUES (?, ?, ?, 'manual_dashboard', ?)",
                    (job_id, current["status"], status, timestamp),
                )
                connection.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", (status, timestamp, job_id))
            if notes is not _MISSING:
                connection.execute("UPDATE jobs SET notes = ?, updated_at = ? WHERE id = ?", (notes, timestamp, job_id))
        return self.get_job(job_id)

    def create_sync_run(self, trigger_type: str) -> int:
        if trigger_type not in ("manual", "scheduled"):
            raise ValueError("Unknown refresh trigger.")
        with self.transaction() as connection:
            cursor = connection.execute("INSERT INTO sync_runs (trigger_type, started_at, status) VALUES (?, ?, 'running')", (trigger_type, _stamp()))
            return int(cursor.lastrowid)

    def update_sync_run(self, run_id: int, **kwargs: Any) -> None:
        allowed = {"completed_at", "status", "sources_checked", "new_jobs_count", "existing_jobs_count", "stale_jobs_count", "errors"}
        if set(kwargs) - allowed:
            raise ValueError("Unsupported refresh field.")
        if "status" in kwargs and kwargs["status"] not in ("running", "completed", "partial_failure", "failed"):
            raise ValueError("Invalid refresh status.")
        for field in ("new_jobs_count", "existing_jobs_count", "stale_jobs_count"):
            if field in kwargs and (not isinstance(kwargs[field], int) or kwargs[field] < 0):
                raise ValueError("Refresh counts must be nonnegative integers.")
        if not kwargs:
            return
        values = dict(kwargs)
        for field in ("errors", "sources_checked"):
            if field in values:
                values[field] = _json(values[field])
        if values.get("completed_at") is not None:
            values["completed_at"] = _stamp(values["completed_at"])
        assignments = ", ".join(f"{field} = ?" for field in values)
        with self.transaction() as connection:
            connection.execute(f"UPDATE sync_runs SET {assignments} WHERE id = ?", [*values.values(), run_id])

    def get_sync_run(self, run_id: int) -> dict[str, Any] | None:
        with self.connection() as connection:
            return _row(connection.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone())

    def list_sync_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("History limit must be between 1 and 200.")
        with self.connection() as connection:
            return [_row(row) for row in connection.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))]

    def recover_interrupted_runs(self) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE sync_runs SET status = 'failed', completed_at = ?, errors = ? WHERE status = 'running'",
                (_stamp(), _json([{"source": "app", "code": "interrupted", "message": "Search was interrupted before the application restarted."}])),
            )
            return cursor.rowcount

    def set_source_state(self, source: str, scope_key: str, success: bool, error: dict[str, Any] | None = None) -> None:
        timestamp = _stamp()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO source_sync_state (source, scope_key, last_success_at, last_attempt_at, last_error) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source, scope_key) DO UPDATE SET last_success_at = COALESCE(excluded.last_success_at, source_sync_state.last_success_at), "
                "last_attempt_at = excluded.last_attempt_at, last_error = excluded.last_error",
                (source, scope_key, timestamp if success else None, timestamp, _json(error) if error is not None else None),
            )

    def reserve_source_request(self, source: str, now: datetime | None = None,
                               windows: tuple[tuple[timedelta, int], ...] | None = None) -> bool:
        """Reserve an attempt before HTTP, including retries, without logging data.

        Persisted sliding windows conservatively enforce Adzuna's default limits
        across restarts and repeated manual searches. Failed attempts also count.
        """
        current = _utc(now)
        windows = windows or ((timedelta(seconds=60), 25), (timedelta(days=1), 250),
                              (timedelta(days=7), 1000), (timedelta(days=31), 2500))
        with self.transaction() as connection:
            for window, limit in windows:
                count = connection.execute(
                    "SELECT COUNT(*) FROM source_request_log WHERE source = ? AND requested_at > ?",
                    (source, _stamp(current - window)),
                ).fetchone()[0]
                if count >= limit:
                    return False
            connection.execute("INSERT INTO source_request_log (source, requested_at) VALUES (?, ?)", (source, _stamp(current)))
            longest_window = max((window for window, _ in windows), default=timedelta(days=31))
            connection.execute("DELETE FROM source_request_log WHERE source = ? AND requested_at <= ?", (source, _stamp(current - longest_window)))
        return True
