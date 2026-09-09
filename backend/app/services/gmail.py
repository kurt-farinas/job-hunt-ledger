"""Read-only Gmail OAuth and deterministic application-email suggestions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.services.matching import normalize_text

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
SEARCH_QUERY = '{"application" "thank you for applying" "regarding your application" "thank you for your interest" unfortunately "regret to inform" "next steps" interview assessment recruiter}'

_PATTERNS = (
    ("regret to inform", "Declined"), ("unfortunately", "Declined"),
    ("not moving forward", "Declined"), ("thank you for applying", "Applied"),
    ("application received", "Applied"), ("received your application", "Applied"),
    ("regarding your application", "Applied"), ("thank you for your interest", "Applied"),
    ("next steps", "Applied"), ("interview", "Applied"), ("assessment", "Applied"),
    ("application", "Applied"), ("recruiter", None),
)


class GmailError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def detect_application_signal(text: str) -> tuple[str, str | None] | None:
    normalized = normalize_text(text)
    for phrase, status in _PATTERNS:
        if normalize_text(phrase) in normalized:
            return phrase, status
    return None


def sanitized_excerpt(value: str, limit: int = 280) -> str:
    value = re.sub(r"<[^>]+>", " ", html.unescape(value))
    value = re.sub(r"https?://\S+", "[link]", value, flags=re.I)
    value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", value)
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip() + ("…" if len(value) > limit else "")


def _decode_message_part(part: dict, remaining: int = 262_144) -> str:
    if remaining <= 0:
        return ""
    mime = str(part.get("mimeType", "")).lower()
    data = part.get("body", {}).get("data")
    chunks = []
    if data and mime in {"text/plain", "text/html"}:
        try:
            chunks.append(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))[:remaining].decode("utf-8", "replace"))
        except (ValueError, TypeError):
            pass
    for child in part.get("parts", []):
        current = sum(len(item) for item in chunks)
        chunks.append(_decode_message_part(child, remaining - current))
        if sum(len(item) for item in chunks) >= remaining:
            break
    return " ".join(chunks)


class GmailService:
    def __init__(self, db, settings, client: httpx.AsyncClient):
        self.db, self.settings, self.client = db, settings, client
        self._states: dict[str, tuple[str, datetime]] = {}
        self._lock = asyncio.Lock()
        self.last_checked_at: str | None = None
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.gmail_client_id.strip() and self.settings.gmail_client_secret.get_secret_value())

    @property
    def connected(self) -> bool:
        return bool(self.settings.gmail_token_path and self.settings.gmail_token_path.is_file())

    def status(self) -> dict:
        return {"configured": self.configured, "connected": self.connected, "checking": self._lock.locked(),
                "scope": GMAIL_READONLY_SCOPE, "last_checked_at": self.last_checked_at, "last_error": self.last_error}

    def authorization_url(self) -> str:
        if not self.configured:
            raise GmailError("gmail_not_configured", "Add Gmail Desktop OAuth credentials to .env first.")
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        self._states[state] = (verifier, datetime.now(timezone.utc) + timedelta(minutes=10))
        params = {"client_id": self.settings.gmail_client_id, "redirect_uri": self.settings.gmail_redirect_uri,
                  "response_type": "code", "scope": GMAIL_READONLY_SCOPE, "access_type": "offline",
                  "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return AUTH_URL + "?" + urlencode(params)

    async def complete_authorization(self, state: str, code: str) -> None:
        pending = self._states.pop(state, None)
        if pending is None or pending[1] < datetime.now(timezone.utc):
            raise GmailError("invalid_oauth_state", "The Gmail authorization request expired. Start again.")
        response = await self.client.post(TOKEN_URL, data={"client_id": self.settings.gmail_client_id,
            "client_secret": self.settings.gmail_client_secret.get_secret_value(), "code": code,
            "code_verifier": pending[0], "grant_type": "authorization_code", "redirect_uri": self.settings.gmail_redirect_uri})
        if response.status_code != 200:
            raise GmailError("oauth_exchange_failed", "Google did not accept the authorization response.")
        token = response.json()
        granted = set(str(token.get("scope", GMAIL_READONLY_SCOPE)).split())
        if granted != {GMAIL_READONLY_SCOPE}:
            raise GmailError("unexpected_gmail_scope", "Google returned permissions other than Gmail read-only.")
        token["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600)))).isoformat()
        token["scope"] = GMAIL_READONLY_SCOPE
        self._write_token(token)

    def _write_token(self, token: dict) -> None:
        path: Path = self.settings.gmail_token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(token), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)

    async def _access_token(self) -> str:
        if not self.connected:
            raise GmailError("gmail_not_connected", "Connect Gmail before checking messages.")
        try:
            token = json.loads(self.settings.gmail_token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise GmailError("gmail_token_invalid", "The local Gmail token could not be read. Connect Gmail again.") from None
        expires = datetime.fromisoformat(token.get("expires_at", "1970-01-01T00:00:00+00:00"))
        if expires > datetime.now(timezone.utc) + timedelta(seconds=60):
            return token["access_token"]
        if not token.get("refresh_token"):
            raise GmailError("gmail_token_expired", "The Gmail session expired. Connect Gmail again.")
        response = await self.client.post(TOKEN_URL, data={"client_id": self.settings.gmail_client_id,
            "client_secret": self.settings.gmail_client_secret.get_secret_value(), "refresh_token": token["refresh_token"],
            "grant_type": "refresh_token"})
        if response.status_code != 200:
            raise GmailError("gmail_refresh_failed", "Google could not refresh the local Gmail session.")
        refreshed = response.json(); token.update(refreshed)
        token["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=int(refreshed.get("expires_in", 3600)))).isoformat()
        token["scope"] = GMAIL_READONLY_SCOPE
        self._write_token(token)
        return token["access_token"]

    async def check(self) -> dict:
        if self._lock.locked():
            raise GmailError("gmail_check_running", "A Gmail check is already running.")
        async with self._lock:
            try:
                access = await self._access_token()
                headers = {"Authorization": f"Bearer {access}", "Accept": "application/json"}
                found = created = duplicates = 0
                page_token = None
                for _page in range(5):
                    params = {"q": SEARCH_QUERY, "maxResults": 100, "includeSpamTrash": "false"}
                    if page_token:
                        params["pageToken"] = page_token
                    response = await self.client.get(MESSAGES_URL, params=params, headers=headers)
                    if response.status_code != 200:
                        raise GmailError("gmail_api_failed", "Gmail could not be checked right now.")
                    document = response.json()
                    for item in document.get("messages", [])[:100]:
                        message_id = str(item.get("id", ""))
                        if not message_id:
                            continue
                        found += 1
                        if self.db.gmail_message_exists(message_id):
                            duplicates += 1; continue
                        detail = await self.client.get(f"{MESSAGES_URL}/{message_id}", params={"format": "full"}, headers=headers)
                        if detail.status_code != 200:
                            continue
                        suggestion = self._suggestion(detail.json())
                        if suggestion and self.db.create_gmail_suggestion(suggestion):
                            created += 1
                        self.db.mark_gmail_message_processed(message_id)
                    page_token = document.get("nextPageToken")
                    if not page_token:
                        break
                self.last_checked_at = datetime.now(timezone.utc).isoformat(); self.last_error = None
                return {"messages_found": found, "suggestions_created": created, "duplicates_skipped": duplicates,
                        "checked_at": self.last_checked_at}
            except GmailError as exc:
                self.last_error = str(exc)
                raise

    def _suggestion(self, message: dict) -> dict | None:
        headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in message.get("payload", {}).get("headers", [])}
        sender, subject = headers.get("from", "")[:500], headers.get("subject", "")[:500]
        body = _decode_message_part(message.get("payload", {}))
        signal = detect_application_signal(f"{sender} {subject} {body}")
        if signal is None:
            return None
        excerpt = sanitized_excerpt(body or subject)
        job_id = None; company = None
        haystack = f" {normalize_text(sender + ' ' + subject + ' ' + excerpt)} "
        matches = []
        for job in self.db.gmail_company_jobs():
            candidate = job["normalized_company"]
            if len(candidate) >= 3 and f" {candidate} " in haystack:
                matches.append(job)
        unique_companies = {job["normalized_company"] for job in matches}
        if len(unique_companies) == 1:
            best = matches[0]; job_id, company = best["id"], best["company"]
        received = datetime.now(timezone.utc)
        if message.get("internalDate"):
            received = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc)
        elif headers.get("date"):
            try: received = parsedate_to_datetime(headers["date"])
            except (TypeError, ValueError): pass
        return {"gmail_message_id": str(message["id"]), "sender": sender, "subject": subject,
                "received_at": received, "inferred_company": company, "suggested_status": signal[1],
                "sanitized_excerpt": excerpt, "matched_pattern": signal[0], "job_id": job_id}
