"""DocumentSource on the Backend's download endpoint (FR-08b-2, B2).

The Backend checks that the user owns the document; we only pass on their token.
"""

import logging
from uuid import UUID

import httpx

from metanoia_ai.application.ports.document_source import (
    DocumentFetchError,
    DocumentNotFoundError,
)

logger = logging.getLogger(__name__)

# 403: the Backend's owner check. Shown as "not found" so ids cannot be probed (AC2).
NOT_FOUND_STATUSES = {403, 404}


class BackendDocumentClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,  # tests only
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self.max_bytes = max_bytes
        self.transport = transport

    async def download(self, document_id: UUID, auth_header: str) -> bytes:
        url = f"{self.base_url}/api/documents/{document_id}/download"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                headers = {"Authorization": auth_header}
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in NOT_FOUND_STATUSES:
                        raise DocumentNotFoundError(str(document_id))
                    if response.status_code != 200:
                        raise DocumentFetchError(f"HTTP {response.status_code}")
                    return await self._read_capped(response)
        except (DocumentNotFoundError, DocumentFetchError) as error:
            logger.warning("Document %s download failed: %r", document_id, error)
            raise
        except httpx.HTTPError as error:  # timeout, connection, protocol
            logger.warning(
                "Document %s download failed: %s", document_id, type(error).__name__
            )
            raise DocumentFetchError(type(error).__name__) from error

    async def _read_capped(self, response: httpx.Response) -> bytes:
        """Stop after max_bytes + 1: enough to know the file is too large."""
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > self.max_bytes:
                break
        return bytes(data[: self.max_bytes + 1])
