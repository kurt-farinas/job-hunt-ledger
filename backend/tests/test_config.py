import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import JobPreferences, Settings, load_preferences, load_sources
from app.config.sources import SourcesConfig


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_checked_in_configs_have_safe_defaults():
    sources = load_sources(CONFIG_DIR / "sources.json")
    assert not sources.sources["adzuna"].enabled
    assert sources.sources["adzuna"].country == "ph"
    assert sources.sources["adzuna"].max_pages == 3
    assert sources.model_dump()["sources"]["adzuna"]["query"] == "developer"
    assert sources.sources["remoteok"].enabled
    assert sources.sources["we_work_remotely"].enabled
    assert not sources.sources["remotive"].enabled
    assert sources.sources["remotive"].category == "software-dev"
    assert sources.sources["jobicy"].enabled
    assert sources.sources["jobicy"].industry == "engineering"
    assert not sources.sources["himalayas"].enabled
    assert sources.sources["himalayas"].country == "Philippines"
    assert sources.sources["himalayas"].seniority is None
    assert len(sources.sources["himalayas"].queries) == 8
    assert sources.sources["himalayas"].employment_type == "Full Time"
    assert sources.sources["himalayas"].exclude_worldwide is True
    assert sources.sources["remoteok"].max_items == 250
    assert sources.sources["greenhouse"].enabled
    assert sources.sources["greenhouse"].company_slugs == ["gitlab", "coinbase", "stripe", "figma", "airbnb", "hubspot"]
    assert sources.sources["lever"].enabled
    assert sources.sources["lever"].company_slugs == ["coins", "Aprio", "portcast", "ciandt", "rocketpartners", "getwingapp"]
    assert sources.sources["lever"].max_pages == 5
    prefs = load_preferences(CONFIG_DIR / "job_preferences.json")
    assert prefs.stale_job_threshold_days == 30
    assert prefs.minimum_salary is None
    assert prefs.accepted_locations == ["Philippines", "Metro Manila", "Manila", "Luzon"]
    assert prefs.accepted_remote_locations == [
        "Philippines", "Metro Manila", "Manila", "Worldwide", "Global",
        "Anywhere", "Anywhere in the World", "Location Independent", "APAC",
        "Asia", "Southeast Asia", "South East Asia", "SEA",
    ]
    assert "Makati" in prefs.accepted_hybrid_on_site_locations
    assert "Cavite" in prefs.accepted_hybrid_on_site_locations
    assert {"Clark", "Angeles City", "Antipolo", "Bacoor", "Santa Rosa", "Legazpi"} <= set(prefs.accepted_hybrid_on_site_locations)
    assert prefs.accepted_work_arrangements == ["Remote", "Hybrid", "On-site"]
    assert prefs.require_work_arrangement_match is True
    assert prefs.accepted_employment_types == ["Full-time", "Contract"]
    assert prefs.require_employment_type_match is True
    assert {"junior frontend developer", "junior full-stack developer", "junior software developer", "php laravel developer"} <= set(prefs.included_title_keywords)


@pytest.mark.parametrize("config", [
    {"sources": {"linkedin": {"enabled": True}}},
    {"sources": {"adzuna": {"enabled": "true"}}},
    {"sources": {"adzuna": {"max_pages": 0}}},
    {"sources": {"adzuna": {"results_per_page": 100}}},
    {"sources": {"adzuna": {"max_pages": "3"}}},
    {"sources": {"adzuna": {"country": "../../us"}}},
    {"sources": {"adzuna": {"query": "  "}}},
    {"sources": {"adzuna": {"base_url": "https://other.example"}}},
    {"sources": {"lever": {"company_slugs": ["https://example.com"]}}},
    {"sources": {"greenhouse": {"company_slugs": ["../acme"]}}},
    {"sources": {"greenhouse": {"company_slugs": ["acme?foo=bar"]}}},
    {"sources": {"remoteok": {"max_items": 0}}},
    {"sources": {"we_work_remotely": {"max_items": "250"}}},
    {"sources": {"remotive": {"category": "../software"}}},
    {"sources": {"jobicy": {"max_items": 201}}},
    {"sources": {"himalayas": {"max_pages": 0}}},
    {"sources": {"himalayas": {"country": "../Philippines"}}},
    {"sources": {"himalayas": {"queries": []}}},
    {"sources": {"greenhouse": {"max_items_per_company": 0}}},
    {"sources": {"lever": {"results_per_page": 101}}},
    {"sources": {"lever": {"max_pages": 0}}},
    {"sources": []},
    {"sources": {}, "unexpected": True},
])
def test_malformed_source_configuration_rejected(config):
    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(config)


def test_board_slugs_editable_without_code_change():
    config = SourcesConfig.model_validate({"sources": {"greenhouse": {"enabled": True, "company_slugs": ["acme-labs", "example_co"]}}})
    assert config.sources["greenhouse"].enabled
    assert config.sources["greenhouse"].company_slugs == ["acme-labs", "example_co"]


@pytest.mark.parametrize("loader", [load_sources, load_preferences])
def test_invalid_json_fails_clearly(tmp_path, loader):
    path = tmp_path / "bad.json"
    path.write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.json"):
        loader(path)


@pytest.mark.parametrize("changes", [
    {"stale_job_threshold_days": 0}, {"included_title_keywords": [""]},
    {"excluded_keywords": ["***"]}, {"unexpected": True},
    {"minimum_salary": 30_000},
    {"minimum_salary": 30_000, "minimum_salary_currency": "PHP"},
    {"minimum_salary_currency": "pesos"}, {"minimum_salary_period": "sometimes"},
])
def test_invalid_preferences_rejected(changes):
    with pytest.raises(ValidationError):
        JobPreferences(**changes)


def test_preferences_are_editable_and_strict(tmp_path):
    config = {"included_title_keywords": ["Backend Developer"], "excluded_seniority_keywords": [], "preferred_companies": ["Acme"], "stale_job_threshold_days": 60}
    path = tmp_path / "job_preferences.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    loaded = load_preferences(path)
    assert loaded.included_title_keywords == ["Backend Developer"]
    assert loaded.excluded_seniority_keywords == []
    assert loaded.stale_job_threshold_days == 60


def test_settings_start_without_credentials_and_redact_secrets(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.adzuna_app_id.get_secret_value() == ""
    assert settings.adzuna_app_key.get_secret_value() == ""
    secret_settings = Settings(_env_file=None, adzuna_app_key="private-test-value")
    assert "private-test-value" not in repr(secret_settings)


def test_comma_separated_local_cors_and_case_insensitive_environment(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    monkeypatch.setenv("adzuna_app_id", "example-id")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert settings.adzuna_app_id.get_secret_value() == "example-id"


def test_json_origins_and_loopback_ipv6():
    assert Settings(_env_file=None, cors_origins='["http://[::1]:5173"]').cors_origins == ["http://[::1]:5173"]


@pytest.mark.parametrize("origin", ["*", "https://remote.example", "http://0.0.0.0:5173", "http://localhost.evil.test:5173", "http://user@localhost:5173", "http://localhost:5173/other", "http://localhost:bad", "http://localhost:5173?x=1", ""])
def test_cors_refuses_nonlocal_or_nonorigin_values(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=origin)


def test_only_local_sqlite_database_settings():
    with pytest.raises(ValidationError, match="local sqlite"):
        Settings(_env_file=None, database_url="postgresql://example/jobs")
