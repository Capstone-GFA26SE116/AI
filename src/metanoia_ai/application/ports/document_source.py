"""Where project documents come from (FR-08b-2, B2). The Backend client implements it."""

from typing import Protocol
from uuid import UUID


class DocumentNotFoundError(Exception):
    """No such document for this user (the Backend answered 404 or 403) → API 404."""


class DocumentFetchError(Exception):
    """The Backend could not be reached or failed (timeout, 5xx, ...) → API 502."""


class DocumentSource(Protocol):
    async def download(self, document_id: UUID, auth_header: str) -> bytes:
        """The file's bytes. `auth_header` is the user's `Authorization` header,
        passed on as is; never logged or stored (#18).

        May stop reading early: at most max size + 1 byte comes back, so a file
        that is too large is still seen as too large.
        """
        ...
