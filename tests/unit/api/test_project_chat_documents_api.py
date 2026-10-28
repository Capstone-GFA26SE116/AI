"""FR-08b-2 endpoints (T17, T18): messages with documents, fakes only."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from metanoia_ai.api.deps import (
    document_limits,
    get_get_project_chat,
    get_send_project_chat_message,
)
from metanoia_ai.api.main import app
from metanoia_ai.application.ports.document_source import DocumentFetchError
from metanoia_ai.application.use_cases.get_project_chat import GetProjectChat
from metanoia_ai.application.use_cases.send_project_chat_message import (
    DocumentLimits,
)
from metanoia_ai.config import get_settings
from tests.unit.api.test_auth import USER_ID, make_settings, make_token
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_chat import QUESTION, draft_answer
from tests.unit.application.test_project_chat_documents import BRIEF_TXT, PNG, DocWorld
from tests.unit.domain.test_document_text import BRIEF_LINES, make_pdf

TOKEN = make_token()
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
ATTACHMENT_KEYS = {"id", "name", "source", "document_id", "is_truncated"}


class ApiDocWorld(DocWorld):
    def __init__(self, **kw) -> None:
        super().__init__(user=USER_ID, **kw)
        self.llm = FakeLLMClient(draft_answer())


@pytest.fixture
def world():
    return ApiDocWorld()


@pytest.fixture
def client(world):
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_send_project_chat_message] = lambda: world.use_case(
        world.llm
    )
    app.dependency_overrides[get_get_project_chat] = lambda: GetProjectChat(world.repo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def send_json(client, world, body, headers=HEADERS):
    url = f"/chat/conversations/{world.chat_id}/messages"
    return client.post(url, json=body, headers=headers)


def send_files(
    client, world, message="Đọc giúp mình", ids=(), files=(), headers=HEADERS
):
    url = f"/chat/conversations/{world.chat_id}/messages/files"
    data = {"message": message, "document_ids": [str(i) for i in ids]}
    parts = [
        ("files", (name, body, "application/octet-stream")) for name, body in files
    ]
    return client.post(url, data=data, files=parts or None, headers=headers)


# --- T17: JSON with document_ids ---


def test_t17_json_with_library_documents(client, world):
    doc = world.library_file(make_pdf([BRIEF_LINES]), name="Brief khach")
    response = send_json(
        client, world, {"message": "Đọc brief", "document_ids": [str(doc)]}
    )
    assert response.status_code == 200
    [attachment] = response.json()["user_message"]["attachments"]
    assert set(attachment) == ATTACHMENT_KEYS  # never the extracted text
    assert attachment["name"] == "Brief khach"
    assert (attachment["source"], attachment["document_id"]) == ("hub", str(doc))
    assert attachment["is_truncated"] is False
    assert world.backend.calls == [(doc, f"Bearer {TOKEN}")]  # the user's own header
    assert response.json()["ai_message"]["attachments"] == []


def test_t17_old_json_body_still_works(client, world):
    response = send_json(client, world, {"message": "Brief của khách"})
    assert response.status_code == 200
    assert response.json()["user_message"]["attachments"] == []


def test_t17_json_switch_off_is_409(client):
    world = ApiDocWorld(allow=False)
    app.dependency_overrides[get_send_project_chat_message] = lambda: world.use_case(
        world.llm
    )
    doc = world.library_file(make_pdf([BRIEF_LINES]))
    response = send_json(client, world, {"message": "x", "document_ids": [str(doc)]})
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "document_access_off"}


def test_t17_unknown_document_is_404(client, world):
    body = {"message": "x", "document_ids": [str(uuid4())]}
    assert send_json(client, world, body).status_code == 404


def test_t17_backend_down_is_502(client, world):
    doc = world.library_file(b"")
    world.backend.files[doc] = DocumentFetchError("HTTP 503")
    response = send_json(client, world, {"message": "x", "document_ids": [str(doc)]})
    assert response.status_code == 502
    assert response.json()["detail"] == {"code": "document_fetch_failed"}


def test_t17_json_same_id_twice_is_422(client, world):
    doc = str(world.library_file(make_pdf([BRIEF_LINES])))
    response = send_json(client, world, {"message": "x", "document_ids": [doc, doc]})
    assert response.status_code == 422


# --- T17: multipart with uploaded files ---


def test_t17_multipart_with_a_file(client, world):
    response = send_files(client, world, files=[("yeu-cau.txt", BRIEF_TXT.encode())])
    assert response.status_code == 200
    [attachment] = response.json()["user_message"]["attachments"]
    assert (attachment["source"], attachment["document_id"]) == ("upload", None)
    assert "trang chủ và giỏ hàng" in world.llm.prompts[0]


def test_t17_multipart_library_and_upload_together(client, world):
    doc = world.library_file(make_pdf([BRIEF_LINES]))
    response = send_files(client, world, ids=[doc], files=[("a.md", b"# Notes\nBlue")])
    assert response.status_code == 200
    sources = [a["source"] for a in response.json()["user_message"]["attachments"]]
    assert sources == ["hub", "upload"]


def test_t17_multipart_documents_only(client, world):
    world.llm.answers[0] = QUESTION
    response = send_files(
        client, world, message="", files=[("a.txt", BRIEF_TXT.encode())]
    )
    assert response.status_code == 200
    assert response.json()["user_message"]["content"] == "(Gửi tài liệu)"


def test_t17_more_than_3_documents_is_422(client, world):
    files = [(f"{n}.txt", b"text") for n in range(4)]
    response = send_files(client, world, files=files)
    assert response.status_code == 422
    assert world.llm.prompts == []


def test_t17_file_over_the_size_limit_is_422_with_its_name(client):
    world = ApiDocWorld(limits=DocumentLimits(max_file_bytes=100))
    app.dependency_overrides[get_send_project_chat_message] = lambda: world.use_case(
        world.llm
    )
    response = send_files(client, world, files=[("big.txt", b"x" * 101)])
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "too_large", "file": "big.txt"}


def test_t17_unsupported_file_is_422_with_its_name(client, world):
    response = send_files(client, world, files=[("anh.png", PNG)])
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "unsupported_type", "file": "anh.png"}


def test_t17_no_message_and_no_file_is_422(client, world):
    assert send_files(client, world, message="").status_code == 422


def test_t17_multipart_without_token_is_401(client, world):
    files = [("a.txt", b"text")]
    assert send_files(client, world, files=files, headers={}).status_code == 401


# --- T18: GET shows attachments, without their text ---


def test_t18_get_chat_has_attachments_on_the_user_message(client, world):
    send_files(client, world, files=[("yeu-cau.txt", BRIEF_TXT.encode())])
    response = client.get(f"/chat/conversations/{world.chat_id}", headers=HEADERS)
    assert response.status_code == 200
    user, ai = response.json()["messages"]
    assert [a["name"] for a in user["attachments"]] == ["yeu-cau.txt"]
    assert set(user["attachments"][0]) == ATTACHMENT_KEYS
    assert ai["attachments"] == []
    assert "nam@acme.vn" not in response.text  # extracted text is never sent back


def test_document_limits_come_from_the_settings():
    assert document_limits(make_settings()) == DocumentLimits(
        max_files=3,
        max_file_bytes=20 * 1024 * 1024,
        max_chars=80_000,
        max_pdf_pages=100,
    )
