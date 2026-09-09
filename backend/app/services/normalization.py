"""Source-independent, deterministic normalization before matching and storage.

Only derived/filterable fields are canonicalized on a copied model. Display
identity and location text stay exactly as supplied by an approved adapter.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from app.schemas.jobs import NormalizedJob


REMOTE_LABELS = ("remote", "anywhere", "worldwide", "location independent")
_ALIASES = (
    (r"\bfront\s*end\b", "front end"),
    (r"\bfull\s*stack\b", "full stack"),
    (r"\bentry\s*level\b", "entry level"),
    (r"\bfull\s*time\b", "full time"),
    (r"\bpart\s*time\b", "part time"),
    (r"\bon\s*site\b", "on site"),
    (r"\bworld\s*wide\b", "worldwide"),
    (r"\breact\s+js\b", "react"),
    (r"\bjr\b", "junior"),
    (r"\bsr\b", "senior"),
    (r"\bwfh\b|\bwork from home\b", "remote"),
)


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = "".join(character if character.isalnum() else " " for character in text)
    text = " ".join(text.split())
    for pattern, replacement in _ALIASES:
        text = re.sub(pattern, replacement, text)
    return " ".join(text.split())


def dedupe_hash(company: str | None, title: str | None, source_url: str | None) -> str:
    # Preserve structural URL punctuation so /a-b and /a/b do not collide.
    url = " ".join(unicodedata.normalize("NFKC", source_url or "").casefold().split())
    identity = "|".join((normalize_text(company), normalize_text(title), url))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def work_arrangement_for_job(job: NormalizedJob) -> str | None:
    supplied = normalize_text(job.work_arrangement)
    if supplied:
        return {
            "remote": "Remote", "fully remote": "Remote", "100 remote": "Remote",
            "hybrid": "Hybrid", "on site": "On-site", "in person": "On-site",
        }.get(supplied, job.work_arrangement.strip())
    location = normalize_text(job.location)
    if _contains(location, "hybrid"):
        return "Hybrid"
    if _contains(location, "on site"):
        return "On-site"
    if job.remote_flag is True or any(_contains(location, label) for label in REMOTE_LABELS):
        return "Remote"
    # Non-remote is not evidence of on-site: it could describe a hybrid role.
    return None


def canonical_employment_type(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    return {
        "full time": "Full-time", "full time employee": "Full-time",
        "part time": "Part-time", "part time employee": "Part-time", "contract": "Contract",
        "freelance": "Freelance", "internship": "Internship",
    }.get(normalized, value.strip())


def canonical_salary_period(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    return {
        "annual": "year", "annually": "year", "yearly": "year", "per year": "year",
        "per annum": "year", "annum": "year", "monthly": "month", "per month": "month",
        "weekly": "week", "per week": "week", "daily": "day", "per day": "day",
        "hourly": "hour", "per hour": "hour",
    }.get(normalized, normalized)


def normalize_job(job: NormalizedJob) -> NormalizedJob:
    """Return a new model with consistent filter fields and untouched raw text."""
    return job.model_copy(update={
        "work_arrangement": work_arrangement_for_job(job),
        "employment_type": canonical_employment_type(job.employment_type),
        "salary_period": canonical_salary_period(job.salary_period),
    })
