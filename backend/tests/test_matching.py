import hashlib
from pathlib import Path

import pytest

from app.config.preferences import JobPreferences
from app.schemas.jobs import NormalizedJob
from app.services.matching import dedupe_hash, match_job, normalize_text


def job(**overrides):
    values = {
        "source": "adzuna", "title": "React Developer", "company": "Example Co",
        "location": "Metro Manila, Philippines", "source_url": "https://jobs.example/roles/123",
    }
    values.update(overrides)
    return NormalizedJob(**values)


@pytest.mark.parametrize("raw,expected", [
    (None, ""), ("  ACME   Corp. ", "acme corp"), ("Front‑End Developer", "front end developer"),
    ("Frontend Developer", "front end developer"), ("FullStack Developer", "full stack developer"),
    ("Full–Stack Developer", "full stack developer"), ("Sr. React.js Developer", "senior react developer"),
    ("JR. PHP Developer", "junior php developer"), ("Ｆｕｌｌ－ｔｉｍｅ", "full time"),
    ("Onsite", "on site"), ("Work From Home", "remote"),
])
def test_text_normalization(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize("title", [
    "Junior Developer", "Entry-level Developer", "Associate Developer", "Frontend Developer",
    "Front End Developer", "Full-stack Developer", "Fullstack Developer", "React.js Developer",
    "Laravel Developer", "PHP Developer", "Web Developer", "Software Developer", "Jr. Developer",
    "Junior Full Stack Developer (React / Laravel)", "Frontend Engineer", "Front End Engineer",
    "Full-Stack Engineer", "Software Engineer", "Web Engineer", "React Engineer",
    "Full-Stack Product Engineer", "Fresh Graduate - Technology", "New Grad Program",
    "Graduate Developer", "Graduate Software Engineer", "Developer Trainee",
])
def test_included_title_variants(title):
    result = match_job(job(title=title), JobPreferences())
    assert result.qualified, result.rejection_reason
    assert result.reasons[0].startswith("Title: ")


@pytest.mark.parametrize("seniority", JobPreferences().excluded_seniority_keywords)
def test_excluded_seniority(seniority):
    result = match_job(job(title=f"{seniority} React Developer"), JobPreferences())
    assert not result.qualified
    assert result.rejection_reason.startswith("Excluded seniority:")


def test_phrase_boundaries_and_configurable_seniority():
    assert match_job(job(title="React Developer at Leadership Labs"), JobPreferences()).qualified
    assert not match_job(job(title="React Developership"), JobPreferences()).qualified
    prefs = JobPreferences(excluded_seniority_keywords=[])
    assert match_job(job(title="Senior React Developer"), prefs).qualified


@pytest.mark.parametrize("location", ["Metro Manila", "Manila, Philippines", "Remote", "Anywhere", "Anywhere in the World", "Worldwide", "Location Independent", "APAC", "Fully Remote", "Remote - Worldwide", "Remote (Philippines)"])
def test_accepted_locations(location):
    result = match_job(job(location=location), JobPreferences())
    assert result.qualified, result.rejection_reason
    assert any(reason.startswith("Location: ") for reason in result.reasons)


@pytest.mark.parametrize("location", ["", "US", "Singapore", "Remote - US only", "Remote (Europe)", "Hybrid", "On-site", "US / Anywhere within the US", "Worldwide except Philippines", "Remote not available in the Philippines"])
def test_missing_or_ineligible_locations_not_invented(location):
    result = match_job(job(location=location), JobPreferences())
    assert not result.qualified


def test_remote_flag_alone_does_not_invent_geography():
    assert not match_job(job(location="", remote_flag=True, work_arrangement="Remote"), JobPreferences()).qualified
    assert not match_job(job(location="United States", remote_flag=True, work_arrangement="Remote"), JobPreferences()).qualified
    prefs = JobPreferences(accepted_locations=["United States", "Remote"])
    assert match_job(job(location="Remote, United States", work_arrangement="Remote"), prefs).qualified


@pytest.mark.parametrize("arrangement", ["Remote", "Hybrid", "On-site"])
def test_work_arrangement_tags(arrangement):
    result = match_job(job(work_arrangement=arrangement), JobPreferences())
    assert result.qualified
    assert f"Work arrangement: {arrangement}" in result.reasons


@pytest.mark.parametrize("location,tag", [("Remote", "Remote"), ("Manila (hybrid)", "Hybrid"), ("Manila - onsite", "On-site")])
def test_arrangement_from_explicit_source_location(location, tag):
    result = match_job(job(location=location), JobPreferences())
    assert result.qualified
    assert f"Work arrangement: {tag}" in result.reasons


def test_arrangement_preferences_restrict_explicit_arrangement():
    prefs = JobPreferences(accepted_work_arrangements=["Remote"])
    assert not match_job(job(work_arrangement="Hybrid"), prefs).qualified
    assert match_job(job(work_arrangement="Remote"), prefs).qualified


def test_philippines_only_remote_requires_both_location_and_arrangement():
    prefs = JobPreferences(
        accepted_locations=["Metro Manila", "Manila", "Philippines"],
        accepted_work_arrangements=["Remote"],
        require_work_arrangement_match=True,
    )
    assert match_job(job(location="Remote - Philippines", work_arrangement="Remote"), prefs).qualified
    assert not match_job(job(location="Philippines", work_arrangement=None), prefs).qualified
    assert not match_job(job(location="Manila", work_arrangement="Hybrid"), prefs).qualified
    assert not match_job(job(location="Worldwide", work_arrangement="Remote"), prefs).qualified
    assert not match_job(job(location="Remote - United States", work_arrangement="Remote"), prefs).qualified


@pytest.mark.parametrize("location", ["Metro Manila", "Makati", "Cavite", "Baguio", "Pampanga"])
def test_full_time_hybrid_and_on_site_are_limited_to_configured_luzon_locations(location):
    prefs = JobPreferences(
        accepted_locations=["Philippines", "Luzon"],
        accepted_remote_locations=["Philippines"],
        accepted_hybrid_on_site_locations=["Luzon", "Metro Manila", "Makati", "Cavite", "Baguio", "Pampanga"],
        accepted_work_arrangements=["Remote", "Hybrid", "On-site"],
        require_work_arrangement_match=True,
        accepted_employment_types=["Full-time"],
        require_employment_type_match=True,
    )
    assert match_job(job(location=location, work_arrangement="Hybrid", employment_type="Full-time"), prefs).qualified
    assert match_job(job(location=location, work_arrangement="On-site", employment_type="Full-time"), prefs).qualified
    assert not match_job(job(location="Cebu", work_arrangement="Hybrid", employment_type="Full-time"), prefs).qualified
    assert not match_job(job(location=location, work_arrangement="Hybrid", employment_type="Contract"), prefs).qualified
    assert not match_job(job(location=location, work_arrangement="Hybrid", employment_type=None), prefs).qualified


@pytest.mark.parametrize("location", [
    "Clark Freeport Zone", "Angeles City", "Antipolo City", "Bacoor City",
    "Santa Rosa City", "Legazpi City", "Puerto Princesa City",
])
def test_checked_in_preferences_accept_luzon_city_only_locations(location):
    prefs = JobPreferences.model_validate_json((Path(__file__).resolve().parents[1] / "config" / "job_preferences.json").read_text(encoding="utf-8"))
    result = match_job(job(location=location, work_arrangement="On-site", employment_type="Full-time"), prefs)
    assert result.qualified, result.rejection_reason


@pytest.mark.parametrize("location", [
    "Remote - Philippines", "Remote - Worldwide", "Global Remote",
    "Anywhere in the World", "Location Independent", "Remote - APAC",
    "Remote - Asia", "Remote - Southeast Asia", "Remote - South East Asia",
    "Remote - SEA",
])
@pytest.mark.parametrize("employment_type", ["Full-time", "Contract"])
def test_checked_in_preferences_accept_ph_eligible_remote_roles(location, employment_type):
    prefs = JobPreferences.model_validate_json((Path(__file__).resolve().parents[1] / "config" / "job_preferences.json").read_text(encoding="utf-8"))
    result = match_job(
        job(location=location, work_arrangement="Remote", employment_type=employment_type),
        prefs,
    )
    assert result.qualified, result.rejection_reason


@pytest.mark.parametrize("location", [
    "Remote", "Remote - US only", "Remote (Europe)", "Remote - Australia only",
    "Worldwide except Philippines", "Remote - Asia excluding Philippines",
])
def test_checked_in_preferences_reject_remote_roles_without_ph_eligibility(location):
    prefs = JobPreferences.model_validate_json((Path(__file__).resolve().parents[1] / "config" / "job_preferences.json").read_text(encoding="utf-8"))
    result = match_job(job(location=location, work_arrangement="Remote", employment_type="Full-time"), prefs)
    assert not result.qualified


@pytest.mark.parametrize("location", ["Philippines", "Cebu", "Davao", "Singapore", ""])
@pytest.mark.parametrize("arrangement", ["Hybrid", "On-site"])
def test_checked_in_preferences_reject_non_luzon_or_unknown_non_remote_locations(location, arrangement):
    prefs = JobPreferences.model_validate_json((Path(__file__).resolve().parents[1] / "config" / "job_preferences.json").read_text(encoding="utf-8"))
    result = match_job(job(location=location, work_arrangement=arrangement, employment_type="Full-time"), prefs)
    assert not result.qualified


@pytest.mark.parametrize("employment_type", ["Part-time", "Freelance", "Internship", None])
def test_checked_in_preferences_reject_other_or_missing_employment_types(employment_type):
    prefs = JobPreferences.model_validate_json((Path(__file__).resolve().parents[1] / "config" / "job_preferences.json").read_text(encoding="utf-8"))
    result = match_job(
        job(location="Remote - Philippines", work_arrangement="Remote", employment_type=employment_type),
        prefs,
    )
    assert not result.qualified


def test_remote_full_time_is_limited_to_philippines():
    prefs = JobPreferences(
        accepted_locations=["Philippines", "Luzon"],
        accepted_remote_locations=["Philippines", "Metro Manila", "Manila"],
        accepted_hybrid_on_site_locations=["Luzon"],
        accepted_work_arrangements=["Remote", "Hybrid", "On-site"],
        require_work_arrangement_match=True,
        accepted_employment_types=["Full-time"],
        require_employment_type_match=True,
    )
    assert match_job(job(location="Remote - Philippines", work_arrangement="Remote", employment_type="Full-time"), prefs).qualified
    assert not match_job(job(location="Remote - Worldwide", work_arrangement="Remote", employment_type="Full-time"), prefs).qualified
    assert not match_job(job(location="Remote - Philippines", work_arrangement="Remote", employment_type="Part-time"), prefs).qualified


@pytest.mark.parametrize("employment", JobPreferences().accepted_employment_types)
def test_employment_tags(employment):
    result = match_job(job(employment_type=employment), JobPreferences())
    assert result.qualified
    assert f"Employment type: {employment}" in result.reasons


def test_unknown_fields_do_not_generate_tags_and_unaccepted_employment_is_rejected():
    result = match_job(job(), JobPreferences())
    assert not any(reason.startswith(("Employment type:", "Work arrangement:", "Salary:")) for reason in result.reasons)
    assert not match_job(job(employment_type="Volunteer"), JobPreferences()).qualified


def test_preferred_company_visible_without_score():
    result = match_job(job(company="EXAMPLE Co."), JobPreferences(preferred_companies=["Example Co"]))
    assert result.qualified and result.preferred_company
    assert "Preferred company" in result.reasons
    assert not hasattr(result, "score")


@pytest.mark.parametrize("field,value", [("company", "Example Co"), ("title", "React Developer at Example Co"), ("job_description", "Hiring for EXAMPLE Co.")])
def test_excluded_company_prevents_insertion_candidate(field, value):
    candidate = job(company="Another business", **{field: value}) if field != "company" else job(company=value)
    assert not match_job(candidate, JobPreferences(excluded_companies=["Example Co"])).qualified


@pytest.mark.parametrize("field,value", [("company", "Casino Labs"), ("title", "React Developer - Casino"), ("job_description", "Build CASINO products")])
def test_excluded_keywords(field, value):
    assert not match_job(job(**{field: value}), JobPreferences(excluded_keywords=["casino"])).qualified


def test_salary_tag_is_explainable_and_not_default_exclusion():
    candidate = job(salary_min=20_000, salary_max=35_000, salary_currency="PHP", salary_period="month")
    result = match_job(candidate, JobPreferences())
    assert result.qualified
    assert "Salary: PHP 20,000–35,000 / month" in result.reasons


def test_hourly_salary_tag_keeps_cents():
    result = match_job(job(salary_min=25.5, salary_currency="USD", salary_period="hour"), JobPreferences())
    assert "Salary: USD from 25.5 / hour" in result.reasons


def test_minimum_salary_only_compares_known_currency_and_period():
    prefs = JobPreferences(minimum_salary=30_000, minimum_salary_currency="PHP", minimum_salary_period="month")
    assert match_job(job(), prefs).qualified
    assert not match_job(job(salary_max=25_000, salary_currency="PHP", salary_period="month"), prefs).qualified
    assert match_job(job(salary_max=40_000, salary_currency="PHP", salary_period="month"), prefs).qualified
    assert match_job(job(salary_max=25_000, salary_currency="USD", salary_period="month"), prefs).qualified
    assert match_job(job(salary_max=25_000, salary_currency="PHP", salary_period="year"), prefs).qualified
    assert match_job(job(salary_max=25_000, salary_currency="PHP"), prefs).qualified
    assert match_job(job(salary_max=25_000, salary_period="month"), prefs).qualified


def test_match_reasons_are_stable_and_raw_values_preserved():
    candidate = job(title="REACT Developer", location="Remote", work_arrangement="Remote", employment_type="fulltime")
    before = candidate.model_dump()
    expected = ["Title: react developer", "Location: Remote", "Work arrangement: Remote", "Employment type: Full-time"]
    assert match_job(candidate, JobPreferences()).reasons == expected
    assert candidate.model_dump() == before


def test_dedupe_hash_is_stable_sha256():
    expected = hashlib.sha256(b"example co|front end developer|https://jobs.example/job/1").hexdigest()
    assert dedupe_hash("EXAMPLE Co.", "Frontend Developer", "  HTTPS://jobs.example/job/1 ") == expected
    assert dedupe_hash(" Example  Co ", "Front-end Developer", "https://jobs.example/job/1") == expected
    assert len(dedupe_hash(None, None, None)) == 64
    assert dedupe_hash(None, None, None) == dedupe_hash("", "", "")


def test_dedupe_preserves_url_identity_distinctions():
    assert dedupe_hash("Co", "React Developer", "https://jobs.example/a-b") != dedupe_hash("Co", "React Developer", "https://jobs.example/a/b")
    assert dedupe_hash("Co", "React Developer", "https://jobs.example/1") != dedupe_hash("Co", "React Developer", "https://jobs.example/2")
