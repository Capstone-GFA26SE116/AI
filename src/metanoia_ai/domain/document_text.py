"""FR-08b-2: read the text of a document the user attached to the project chat.

Pure code: bytes in, text out. No network, no DB (the file is fetched elsewhere).
Kinds: PDF, DOCX, plain text (.txt / .md). No OCR (#5).
"""

import io
import re
import zipfile
from enum import StrEnum
from pathlib import PurePosixPath

from docx import Document as open_docx
from docx.table import Table
from pypdf import PasswordType, PdfReader

TEXT_EXTENSIONS = {".txt", ".md"}
# Fewer non-space characters than this in a PDF = scanned or empty (#12).
PDF_MIN_TEXT_CHARS = 50
DOCX_MAIN_PART = "word/document.xml"


class DocumentKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"


class DocumentErrorCode(StrEnum):
    """Sent in the API (422), together with the file name."""

    UNSUPPORTED_TYPE = "unsupported_type"
    NO_TEXT = "no_text"
    UNREADABLE = "unreadable"
    TOO_LARGE = "too_large"


class DocumentError(Exception):
    """The file cannot be used. `name` is the file name shown to the user."""

    def __init__(self, code: DocumentErrorCode, name: str) -> None:
        super().__init__(f"{code}: {name}")
        self.code = code
        self.name = name


def extension_of(path: str) -> str:
    """'docs/Brief.TXT' → '.txt'. Empty when there is none."""
    return PurePosixPath(path.replace("\\", "/")).suffix.lower()


def detect_kind(data: bytes, extension: str) -> DocumentKind | None:
    """Content first, extension last (#11): a renamed file is still found."""
    if data.startswith(b"%PDF"):
        return DocumentKind.PDF
    if zipfile.is_zipfile(io.BytesIO(data)):
        return DocumentKind.DOCX if _is_docx(data) else None
    if extension in TEXT_EXTENSIONS and _decode_text(data) is not None:
        return DocumentKind.TEXT
    return None


def extract_text(
    data: bytes,
    *,
    name: str,
    path: str,
    max_bytes: int,
    max_pdf_pages: int,
    enough_chars: int | None = None,
) -> str:
    """The document's text, whitespace cleaned. Raises DocumentError.

    `path` gives the extension (hub: StoragePath; upload: the file name).
    `enough_chars`: a PDF stops reading pages once it has this much text (#12).
    """
    if len(data) > max_bytes:
        raise DocumentError(DocumentErrorCode.TOO_LARGE, name)
    kind = detect_kind(data, extension_of(path))
    if kind is None:
        raise DocumentError(DocumentErrorCode.UNSUPPORTED_TYPE, name)
    if kind is DocumentKind.PDF:
        text = _pdf_text(data, name, max_pdf_pages, enough_chars)
        min_chars = PDF_MIN_TEXT_CHARS
    elif kind is DocumentKind.DOCX:
        text = _docx_text(data, name)
        min_chars = 1
    else:
        text = _decode_text(data) or ""
        min_chars = 1
    text = normalize_text(text)
    if len(re.sub(r"\s", "", text)) < min_chars:
        raise DocumentError(DocumentErrorCode.NO_TEXT, name)
    return text


def normalize_text(text: str) -> str:
    """One newline style, no trailing spaces, at most one empty line in a row."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return DOCX_MAIN_PART in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _decode_text(data: bytes) -> str | None:
    """UTF-8 text (BOM allowed), or None for binary data."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def _pdf_text(data: bytes, name: str, max_pages: int, enough_chars: int | None) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise DocumentError(DocumentErrorCode.UNREADABLE, name)
        parts: list[str] = []
        total = 0
        for page in reader.pages[:max_pages]:
            part = page.extract_text() or ""
            parts.append(part)
            total += len(part)
            if enough_chars is not None and total >= enough_chars:
                break
    except DocumentError:
        raise
    except Exception as error:  # pypdf raises many kinds on a broken or locked file
        raise DocumentError(DocumentErrorCode.UNREADABLE, name) from error
    return "\n\n".join(parts)


def _docx_text(data: bytes, name: str) -> str:
    """Paragraphs and tables in document order; table cells joined by ' | '."""
    try:
        document = open_docx(io.BytesIO(data))
        blocks: list[str] = []
        for block in document.iter_inner_content():
            if isinstance(block, Table):
                blocks.extend(_table_rows(block))
            else:
                blocks.append(block.text)
    except Exception as error:  # broken zip / XML
        raise DocumentError(DocumentErrorCode.UNREADABLE, name) from error
    return "\n".join(blocks)


def _table_rows(table: Table) -> list[str]:
    rows = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            text = " ".join(cell.text.split())
            if not cells or cells[-1] != text:  # a merged cell repeats itself
                cells.append(text)
        rows.append(" | ".join(cells))
    return rows
