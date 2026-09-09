"""Small, source-neutral parsing helpers for approved structured feeds."""

from __future__ import annotations

import html
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from app.adapters.base import SourceError
from app.services.normalization import canonical_employment_type, canonical_salary_period, normalize_text


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.hidden_depth += 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.hidden_depth = max(0, self.hidden_depth - 1)
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4"):
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def plain_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parser = _PlainText()
    try:
        parser.feed(html.unescape(value))
        parser.close()
    except (ValueError, AssertionError):
        return None
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip() or None


def text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    minimum_ok = result > 0 if positive else result >= 0
    return result if math.isfinite(result) and minimum_ok else None


def posted_at(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        seconds = value / 1000 if value > 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value.strip())
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
        except (TypeError, ValueError, OverflowError):
            return None


def safe_url(value: Any, *, allowed_hosts: frozenset[str] | None = None) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise SourceError("invalid_response", "A source listing contains an invalid job URL.")
    result = value.strip()
    try:
        parsed = urlsplit(result)
        valid = parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)
        valid = valid and parsed.username is None and parsed.password is None
        valid = valid and (allowed_hosts is None or parsed.hostname.casefold() in allowed_hosts)
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise SourceError("invalid_response", "A source listing contains an invalid job URL.")
    return result


def employment_type(value: Any) -> str | None:
    candidate = canonical_employment_type(text(value))
    normalized = normalize_text(candidate)
    return candidate if normalized in {"full time", "part time", "contract", "freelance", "internship"} else None


def employment_type_from_values(values: Any) -> str | None:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return None
    for value in values:
        parsed = employment_type(value)
        if parsed:
            return parsed
    return None


def work_arrangement(value: Any) -> str | None:
    normalized = normalize_text(text(value))
    if normalized in {"remote", "fully remote", "100 remote"}:
        return "Remote"
    if normalized == "hybrid":
        return "Hybrid"
    if normalized in {"on site", "onsite", "in person"}:
        return "On-site"
    return None


def salary_period(value: Any) -> str | None:
    parsed = canonical_salary_period(text(value))
    return parsed if parsed in {"hour", "day", "week", "month", "year"} else None
