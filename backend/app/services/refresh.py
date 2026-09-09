"""The only fetch -> normalize -> match -> persist path, for both triggers."""

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from datetime import timedelta

import httpx

from app.adapters.adzuna import AdzunaAdapter
from app.adapters.base import SourceError
from app.adapters.greenhouse import GreenhouseAdapter
from app.adapters.lever import LeverAdapter
from app.adapters.jobicy import JobicyAdapter
from app.adapters.himalayas import HimalayasAdapter
from app.adapters.remotive import RemotiveAdapter
from app.adapters.remoteok import RemoteOKAdapter
from app.adapters.we_work_remotely import WeWorkRemotelyAdapter
from app.config.preferences import load_preferences
from app.config.sources import load_sources
from app.services.matching import match_job
from app.services.normalization import normalize_job


class RefreshConflict(Exception):
    def __init__(self, run_id: int | None):
        self.run_id = run_id


def scope_fingerprint(source_config, preferences=None) -> str:
    """Fingerprint only settings that change which listings a source fetches.

    Local matching, stale thresholds, enable flags, and request pacing do not
    change source coverage. Including them would strand older rows in a scope
    that could never become stale after an unrelated preference edit.
    """
    payload = source_config.model_dump(mode="json")
    payload.pop("enabled", None)
    payload.pop("min_request_interval_seconds", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class RefreshService:
    def __init__(self, db, settings, client: httpx.AsyncClient,
                 adapter_factories: Mapping[str, Callable] | None = None):
        self.db = db
        self.settings = settings
        self.client = client
        self.factories = dict(adapter_factories) if adapter_factories is not None else {
            "adzuna": self._adzuna,
            "remoteok": self._remoteok,
            "we_work_remotely": self._we_work_remotely,
            "remotive": self._remotive,
            "jobicy": self._jobicy,
            "himalayas": self._himalayas,
            "greenhouse": self._greenhouse,
            "lever": self._lever,
        }
        self._lock = threading.Lock()
        self.active_run_id: int | None = None
        self._task: asyncio.Task | None = None
        self._closing = False

    def _adzuna(self, config):
        async def reserve():
            if not self.db.reserve_source_request("adzuna"):
                raise SourceError("rate_limit", "Adzuna local request budget reached; try again after the limit resets.")
        return AdzunaAdapter(config, self.settings, self.client, before_request=reserve)

    def _remoteok(self, config):
        return RemoteOKAdapter(config, self.settings, self.client)

    def _we_work_remotely(self, config):
        return WeWorkRemotelyAdapter(config, self.settings, self.client)

    def _remotive(self, config):
        async def reserve():
            limits = ((timedelta(minutes=1), 2), (timedelta(days=1), 4))
            if not self.db.reserve_source_request("remotive", windows=limits):
                raise SourceError("rate_limit", "Remotive's local request budget is reached; try again after the limit resets.")
        return RemotiveAdapter(config, self.settings, self.client, before_request=reserve)

    def _jobicy(self, config):
        return JobicyAdapter(config, self.settings, self.client)

    def _himalayas(self, config):
        return HimalayasAdapter(config, self.settings, self.client)

    def _greenhouse(self, config):
        return GreenhouseAdapter(config, self.settings, self.client)

    def _lever(self, config):
        return LeverAdapter(config, self.settings, self.client)

    async def start(self, trigger_type: str = "manual") -> int:
        if trigger_type not in {"manual", "scheduled"}:
            raise ValueError("Invalid refresh trigger")
        if self._closing or not self._lock.acquire(blocking=False):
            raise RefreshConflict(self.active_run_id)
        try:
            run_id = self.db.create_sync_run(trigger_type)
            self.active_run_id = run_id
            self._task = asyncio.create_task(self._run(run_id), name=f"job-refresh-{run_id}")
            return run_id
        except BaseException:
            self._lock.release()
            raise

    async def wait(self):
        """Useful for orderly shutdown and deterministic offline verification."""
        if self._task:
            await asyncio.shield(self._task)

    async def close(self):
        self._closing = True
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self, run_id: int):
        from datetime import datetime, timezone

        checked: list[str] = []
        errors: list[dict[str, str]] = []
        totals = dict(new_jobs_count=0, existing_jobs_count=0, stale_jobs_count=0)
        successes = 0
        try:
            # Read once per run. Edits take effect without code changes or restart.
            sources = load_sources(self.settings.config_dir / "sources.json")
            preferences = load_preferences(self.settings.config_dir / "job_preferences.json")
            for source, config in sources.sources.items():
                if not config.enabled:
                    continue
                checked.append(source)
                scope_key = scope_fingerprint(config, preferences)
                self.db.update_sync_run(run_id, sources_checked=checked)
                try:
                    if source not in self.factories:
                        raise SourceError("adapter_unavailable", f"No adapter factory is available for {source}.")
                    jobs = [normalize_job(job) for job in await self.factories[source](config).fetch()]
                    if any(job.source != source for job in jobs):
                        raise SourceError("invalid_response", "Source returned inconsistent job identities.")
                    matches = [(job, match_job(job, preferences)) for job in jobs]
                    counts = self.db.ingest(matches, source=source, scope_key=scope_key,
                                            stale_days=preferences.stale_job_threshold_days)
                    for key in totals:
                        totals[key] += counts[key]
                    self.db.set_source_state(source, scope_key, success=True, error=None)
                    successes += 1
                except Exception as exc:
                    # Never expose request URLs, provider payloads, secrets or notes.
                    error = {"source": source,
                             "code": exc.code if isinstance(exc, SourceError) else "source_failed",
                             "message": exc.message if isinstance(exc, SourceError) else "Source processing failed; verify its configuration or try again."}
                    errors.append(error)
                    self.db.set_source_state(source, scope_key, success=False, error=error)
                self.db.update_sync_run(run_id, sources_checked=checked, errors=errors, **totals)
            state = "completed" if not errors else ("partial_failure" if successes else "failed")
        except asyncio.CancelledError:
            errors.append({"source": "refresh", "code": "interrupted", "message": "Search interrupted by application shutdown."})
            state = "failed"
        except Exception:
            errors.append({"source": "refresh", "code": "configuration_or_storage_error",
                           "message": "Search could not complete. Check local configuration and database availability."})
            state = "failed"
        finally:
            try:
                self.db.update_sync_run(run_id, status=state, completed_at=datetime.now(timezone.utc),
                                        sources_checked=checked, errors=errors, **totals)
            finally:
                self.active_run_id = None
                self._lock.release()
