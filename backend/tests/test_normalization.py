import pytest

from app.config import JobPreferences
from app.schemas.jobs import NormalizedJob
from app.services.matching import match_job
from app.services.normalization import normalize_job, work_arrangement_for_job


def job(**changes):
    values = {"source": "adzuna", "title": "React Developer", "company": "Example Co.", "location": "Manila", "source_url": "https://example.test/job/123"}
    values.update(changes)
    return NormalizedJob(**values)


@pytest.mark.parametrize("field,value,expected", [
    ("work_arrangement", "onsite", "On-site"),
    ("work_arrangement", "ON_SITE", "On-site"),
    ("work_arrangement", "in-person", "On-site"),
    ("work_arrangement", "HYBRID", "Hybrid"),
    ("work_arrangement", "fully remote", "Remote"),
    ("work_arrangement", "Work from home", "Remote"),
    ("employment_type", "full_time", "Full-time"),
    ("employment_type", "Parttime", "Part-time"),
    ("employment_type", "CONTRACT", "Contract"),
    ("employment_type", "INTERNSHIP", "Internship"),
    ("employment_type", "FREELANCE", "Freelance"),
    ("salary_period", "annual", "year"),
    ("salary_period", "per annum", "year"),
    ("salary_period", "PER HOUR", "hour"),
    ("salary_period", "Monthly", "month"),
    ("salary_period", "Weekly", "week"),
    ("salary_period", "Daily", "day"),
])
def test_filter_fields_canonicalized(field, value, expected):
    normalized = normalize_job(job(**{field: value}))
    assert getattr(normalized, field) == expected


@pytest.mark.parametrize("location,expected", [
    ("Manila - Hybrid", "Hybrid"), ("Philippines (on-site)", "On-site"),
    ("Worldwide", "Remote"), ("Remote - US only", "Remote"),
    ("Location Independent", "Remote"), ("Anywhere", "Remote"),
])
def test_explicit_location_arrangement_matches_stored_field(location, expected):
    candidate = job(location=location)
    normalized = normalize_job(candidate)
    assert normalized.work_arrangement == expected
    assert work_arrangement_for_job(candidate) == expected
    result = match_job(normalized, JobPreferences())
    if result.qualified:
        assert f"Work arrangement: {expected}" in result.reasons


def test_remote_flag_false_does_not_invent_on_site():
    normalized = normalize_job(job(remote_flag=False))
    assert normalized.work_arrangement is None


def test_remote_flag_true_does_not_invent_location():
    normalized = normalize_job(job(location="", remote_flag=True))
    assert normalized.work_arrangement == "Remote"
    assert normalized.location == ""
    assert not match_job(normalized, JobPreferences()).qualified


def test_restricted_remote_label_does_not_become_ph_eligibility():
    normalized = normalize_job(job(location="Remote - US only"))
    assert normalized.work_arrangement == "Remote"
    assert not match_job(normalized, JobPreferences()).qualified


def test_raw_identity_display_fields_preserved_without_mutation():
    candidate = job(title=" React  Developer ", company=" EXAMPLE Co. ", location=" Manila — HYBRID ", work_arrangement=None, employment_type="full_time", salary_period="annual")
    original = candidate.model_dump()
    normalized = normalize_job(candidate)
    assert candidate.model_dump() == original
    assert normalized is not candidate
    for field in ("title", "company", "location", "source_url"):
        assert getattr(normalized, field) == original[field]
    assert normalized.work_arrangement == "Hybrid"
    assert normalized.employment_type == "Full-time"
    assert normalized.salary_period == "year"
    assert normalize_job(normalized) == normalized


def test_explicit_hybrid_takes_precedence_over_remote_flag():
    normalized = normalize_job(job(work_arrangement="Hybrid", remote_flag=True))
    assert normalized.work_arrangement == "Hybrid"


def test_unknown_employment_is_not_rewritten_as_full_time():
    assert normalize_job(job(employment_type="Permanent")).employment_type == "Permanent"


def test_predicted_salary_visible_in_match_reason():
    normalized = normalize_job(job(salary_min=20_000, salary_currency="PHP", salary_period="monthly", raw_source_metadata={"salary_is_predicted": True}))
    result = match_job(normalized, JobPreferences())
    assert "Salary: PHP from 20,000 / month (source estimate)" in result.reasons


def test_stated_salary_does_not_get_estimate_label():
    candidate = job(salary_min=20_000, salary_currency="PHP", raw_source_metadata={"salary_is_predicted": False})
    assert not any("source estimate" in reason for reason in match_job(candidate, JobPreferences()).reasons)
