"""FR-08b-2 T16: the Backend download client, with httpx.MockTransport (no network)."""

import asyncio
import logging
from uuid import uuid4

import httpx
import pytest

from metanoia_ai.application.ports.document_source import (
    DocumentFetchError,
    DocumentNotFoundError,
)
from metanoia_ai.infrastructure.backend.document_client import BackendDocumentClient

BASE = "https://backend.example.test/"
AUTH = "Bearer user-token-123"
DOC = uuid4()


def client_for(handler, max_bytes: int = 1000) -> BackendDocumentClient:
    return BackendDocumentClient(
        BASE,
        timeout_seconds=1,
        max_bytes=max_bytes,
        transport=httpx.MockTransport(handler),
    )


def download(client: BackendDocumentClient) -> bytes:
    return asyncio.run(client.download(DOC, AUTH))


def test_t16_200_returns_the_bytes_and_sends_the_user_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, content=b"%PDF-1.7 file body")

    assert download(client_for(handler)) == b"%PDF-1.7 file body"
    assert seen["url"] == f"https://backend.example.test/api/documents/{DOC}/download"
    assert seen["auth"] == AUTH


@pytest.mark.parametrize("status", [404, 403])
def test_t16_404_and_403_are_not_found(status):
    client = client_for(lambda request: httpx.Response(status))
    with pytest.raises(DocumentNotFoundError):
        download(client)


@pytest.mark.parametrize("status", [500, 503, 401, 302])
def test_t16_other_statuses_are_fetch_errors(status):
    client = client_for(lambda request: httpx.Response(status))
    with pytest.raises(DocumentFetchError):
        download(client)


def test_t16_timeout_is_a_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(DocumentFetchError):
        download(client_for(handler))


def test_t16_connection_error_is_a_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(DocumentFetchError):
        download(client_for(handler))


def test_a_large_file_is_read_only_up_to_max_plus_one_byte():
    client = client_for(lambda request: httpx.Response(200, content=b"x" * 5000), 100)
    assert len(download(client)) == 101


def test_the_token_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(DocumentFetchError):
        download(client_for(lambda request: httpx.Response(500)))
    assert "user-token-123" not in caplog.text
    assert str(DOC) in caplog.text
