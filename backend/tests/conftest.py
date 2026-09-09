import json
from pathlib import Path

import pytest

from app.config.preferences import JobPreferences
from app.config.settings import Settings
from app.schemas.jobs import NormalizedJob


@pytest.fixture
def api_settings(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    defaults = Path(__file__).resolve().parents[1] / "config"
    for name in ("sources.json", "job_preferences.json"):
        (config / name).write_text((defaults / name).read_text(encoding="utf-8"), encoding="utf-8")
    # Generic API and ingestion tests should not depend on the user's current
    # editable search preferences.
    (config / "job_preferences.json").write_text(
        json.dumps(JobPreferences().model_dump()), encoding="utf-8"
    )
    # Most ingestion tests use the credentialed Adzuna fixture. Keep their
    # source selection independent from the user's editable runtime defaults.
    source_path = config / "sources.json"
    source_document = json.loads(source_path.read_text(encoding="utf-8"))
    for source in source_document["sources"].values():
        source["enabled"] = False
    source_document["sources"]["adzuna"]["enabled"] = True
    source_path.write_text(json.dumps(source_document), encoding="utf-8")
    return Settings(_env_file=None, project_root=tmp_path, config_dir=config,
                    database_url="sqlite:///data/test.db", backup_dir=tmp_path / "backups", scheduler_enabled=False,
                    adzuna_app_id="", adzuna_app_key="")


@pytest.fixture
def example_job():
    return NormalizedJob(source="adzuna", external_job_id="example-1", title="Junior React Developer",
                         company="Example Company", location="Metro Manila, Philippines",
                         source_url="https://jobs.example.test/1", employment_type="Full-time",
                         work_arrangement="Hybrid", salary_min=30_000, salary_max=45_000,
                         salary_currency="PHP", salary_period="month")


def enable_sources(settings, **enabled):
    path = settings.config_dir / "sources.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    for source, value in enabled.items():
        document["sources"][source]["enabled"] = value
    path.write_text(json.dumps(document), encoding="utf-8")
