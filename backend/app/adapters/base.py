"""Small ingestion boundary shared by approved job sources."""

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.jobs import NormalizedJob


class SourceError(Exception):
    """Public, sanitized failure; never attach URLs, keys, or response bodies."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class JobSourceAdapter(Protocol):
    async def fetch(self) -> list["NormalizedJob"]:
        """Return a complete bounded result or raise SourceError."""
        ...
