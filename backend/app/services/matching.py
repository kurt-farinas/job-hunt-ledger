"""Deterministic matching and identity normalization; no remote inference service.

Word boundaries prevent exclusions such as ``lead`` from rejecting ``Bleeding
Edge``. Remote eligibility is deliberately conservative for restricted locations:
``Remote - US only`` does not mean the applicant can work from the Philippines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.config.preferences import JobPreferences
from app.services.normalization import (
    REMOTE_LABELS,
    canonical_employment_type,
    canonical_salary_period,
    dedupe_hash,
    normalize_text,
    work_arrangement_for_job,
)

if TYPE_CHECKING:
    from app.schemas.jobs import NormalizedJob


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def _first_match(text: str, candidates: list[str] | tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if _contains(text, normalize_text(candidate))), None)


def _location_reason(location: str, arrangement: str | None, prefs: JobPreferences) -> str | None:
    if not location:
        return None
    arrangement_key = normalize_text(arrangement)
    accepted_locations = prefs.accepted_locations
    if arrangement_key == "remote" and prefs.accepted_remote_locations:
        accepted_locations = prefs.accepted_remote_locations
    elif arrangement_key in {"hybrid", "on site"} and prefs.accepted_hybrid_on_site_locations:
        accepted_locations = prefs.accepted_hybrid_on_site_locations
    geographic_preferences = [
        candidate for candidate in accepted_locations
        if normalize_text(candidate) not in REMOTE_LABELS
    ]
    # A named location in an explicit exclusion is not eligibility evidence.
    # Keep this small and auditable; ambiguous restrictions otherwise remain
    # unqualified instead of relying on a language model.
    for candidate in geographic_preferences:
        geography_pattern = re.escape(normalize_text(candidate))
        if re.search(
            r"\b(?:except|excluding|not in|not available in|not open to|unavailable in)\s+(?:the\s+)?"
            + geography_pattern + r"\b",
            location,
        ):
            return None
    geography = _first_match(location, geographic_preferences)
    if geography:
        return f"Location: {geography}"
    # Generic Hybrid/On-site labels contain no geographic eligibility evidence.
    if normalize_text(arrangement) != "remote":
        return None
    remote_preference = _first_match(location, [
        candidate for candidate in accepted_locations
        if normalize_text(candidate) in REMOTE_LABELS
    ])
    if remote_preference is None:
        return None
    residual = location
    for label in REMOTE_LABELS:
        residual = re.sub(r"\b" + re.escape(label) + r"\b", " ", residual)
    # Only labels with no geographic restriction can qualify without a country
    # match. Do not guess at abbreviations, permitted regions or work visas.
    residual = re.sub(r"\b(?:and|or|in|the|world|work|working|from|fully|any|location|100)\b", " ", residual)
    if residual.strip():
        return None
    return f"Location: {remote_preference}"


def _salary_reason(job: "NormalizedJob") -> str | None:
    if job.salary_min is None and job.salary_max is None:
        return None
    def number(value: float) -> str:
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    if job.salary_min is not None and job.salary_max is not None:
        amount = f"{number(job.salary_min)}–{number(job.salary_max)}"
    elif job.salary_min is not None:
        amount = f"from {number(job.salary_min)}"
    else:
        amount = f"up to {number(job.salary_max)}"
    currency = f"{job.salary_currency} " if job.salary_currency else ""
    period = f" / {job.salary_period}" if job.salary_period else ""
    estimate = " (source estimate)" if job.raw_source_metadata.get("salary_is_predicted") is True else ""
    return f"Salary: {currency}{amount}{period}{estimate}"


@dataclass(frozen=True)
class MatchResult:
    qualified: bool
    reasons: list[str] = field(default_factory=list)
    preferred_company: bool = False
    rejection_reason: str | None = None


def match_job(job: "NormalizedJob", prefs: JobPreferences) -> MatchResult:
    title = normalize_text(job.title)
    matched_title = _first_match(title, prefs.included_title_keywords)
    if not matched_title:
        return MatchResult(False, rejection_reason="Title does not match an included keyword")
    seniority = _first_match(title, prefs.excluded_seniority_keywords)
    if seniority:
        return MatchResult(False, rejection_reason=f"Excluded seniority: {seniority}")

    combined = normalize_text(" ".join((job.company or "", job.title or "", job.job_description or "")))
    company_exclusion = _first_match(combined, prefs.excluded_companies)
    if company_exclusion:
        return MatchResult(False, rejection_reason=f"Excluded company: {company_exclusion}")
    keyword_exclusion = _first_match(combined, prefs.excluded_keywords)
    if keyword_exclusion:
        return MatchResult(False, rejection_reason=f"Excluded keyword: {keyword_exclusion}")

    location = normalize_text(job.location)
    arrangement = work_arrangement_for_job(job)
    if prefs.require_work_arrangement_match and not arrangement:
        return MatchResult(False, rejection_reason="Work arrangement is missing")
    if arrangement and not _first_match(normalize_text(arrangement), prefs.accepted_work_arrangements):
        return MatchResult(False, rejection_reason=f"Work arrangement is not accepted: {arrangement}")
    location_reason = _location_reason(location, arrangement, prefs)
    if not location_reason:
        return MatchResult(False, rejection_reason="Location is missing or does not establish an accepted work location")
    if prefs.require_employment_type_match and not job.employment_type:
        return MatchResult(False, rejection_reason="Employment type is missing")
    if job.employment_type and not _first_match(normalize_text(job.employment_type), prefs.accepted_employment_types):
        return MatchResult(False, rejection_reason=f"Employment type is not accepted: {job.employment_type}")

    salary_upper = job.salary_max if job.salary_max is not None else job.salary_min
    if (
        prefs.minimum_salary is not None
        and salary_upper is not None
        and normalize_text(job.salary_currency) == normalize_text(prefs.minimum_salary_currency)
        and canonical_salary_period(job.salary_period) == prefs.minimum_salary_period
        and salary_upper < prefs.minimum_salary
    ):
        return MatchResult(False, rejection_reason="Provided salary is below the configured minimum in the same currency and period")

    preferred = _first_match(normalize_text(job.company), prefs.preferred_companies) is not None
    reasons = [f"Title: {matched_title}", location_reason]
    if arrangement:
        reasons.append(f"Work arrangement: {arrangement}")
    if job.employment_type:
        reasons.append(f"Employment type: {canonical_employment_type(job.employment_type)}")
    salary = _salary_reason(job)
    if salary:
        reasons.append(salary)
    if preferred:
        reasons.append("Preferred company")
    return MatchResult(True, reasons=reasons, preferred_company=preferred)
