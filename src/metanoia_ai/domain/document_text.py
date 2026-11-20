"""FR-08b-2: read the text of a document the user attached to the project chat.

Pure code: bytes in, text out. No network, no DB (the file is fetched elsewhere).
Kinds: PDF, DOCX, plain text (.txt / .md), and FR-08b-10 spreadsheets (.xlsx, .csv).
No OCR (#5).
"""

import csv
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from pathlib import PurePosixPath

from docx import Document as open_docx
from docx.table import Table
from openpyxl import load_workbook
from pypdf import PasswordType, PdfReader

from metanoia_ai.domain.models import Language

TEXT_EXTENSIONS = {".txt", ".md"}
# Fewer non-space characters than this in a PDF = scanned or empty (#12).
PDF_MIN_TEXT_CHARS = 50
DOCX_MAIN_PART = "word/document.xml"
XLSX_MAIN_PART = "xl/workbook.xml"  # FR-08b-10
CSV_EXTENSIONS = {".csv"}
# FR-08b-10: a sheet with more rows is read up to here (the text budget cuts later anyway).
MAX_SHEET_ROWS = 5000
# A cut at a paragraph or sentence end must keep at least this share of the limit.
MIN_BOUNDARY_SHARE = 0.5
SENTENCE_END = re.compile(r"[.!?…](?=\s)")


# Shown with the AI answer when a document was cut (#17).
TRUNCATION_WARNINGS = {
    Language.VI: "AI chỉ đọc phần đầu của {name}.",
    Language.EN: "The AI only read the first part of {name}.",
}


def truncation_warning(name: str, language: Language) -> str:
    return TRUNCATION_WARNINGS[language].format(name=name)


class DocumentKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"
    XLSX = "xlsx"  # FR-08b-10
    CSV = "csv"


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
        if _has_part(data, DOCX_MAIN_PART):
            return DocumentKind.DOCX
        return DocumentKind.XLSX if _has_part(data, XLSX_MAIN_PART) else None
    if extension in CSV_EXTENSIONS and _decode_text(data) is not None:
        return DocumentKind.CSV
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
    elif kind is DocumentKind.XLSX:
        text = _xlsx_text(data, name)
        min_chars = 1
    elif kind is DocumentKind.CSV:
        text = _csv_text(_decode_text(data) or "")
        min_chars = 1
    else:
        text = _decode_text(data) or ""
        min_chars = 1
    text = normalize_text(text)
    if len(re.sub(r"\s", "", text)) < min_chars:
        raise DocumentError(DocumentErrorCode.NO_TEXT, name)
    return text


@dataclass(frozen=True)
class FittedText:
    text: str
    is_truncated: bool


def fit_to_budget(texts: Sequence[str], budget: int) -> list[FittedText]:
    """Share `budget` characters between the documents, same order back (#13).

    Each gets an equal share; a short one gives what it does not use to the
    others (shortest first, so no document takes all of it).
    """
    limits = [0] * len(texts)
    left = max(budget, 0)
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    for done, i in enumerate(order):
        share = left // (len(texts) - done)
        limits[i] = min(len(texts[i]), share)
        left -= limits[i]
    return [
        FittedText(cut_text(text, limit), len(text) > limit)
        for text, limit in zip(texts, limits, strict=True)
    ]


def cut_text(text: str, limit: int) -> str:
    """At most `limit` characters, ending at a paragraph or sentence end if one is
    close enough, else at a space: never in the middle of a word."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    if not text[limit].isspace():  # the limit falls inside a word
        window = window[: max(window.rfind(" "), window.rfind("\n"), 0)] or window
    floor = int(limit * MIN_BOUNDARY_SHARE)
    paragraph = window.rfind("\n\n")
    if paragraph >= floor:
        return window[:paragraph].rstrip()
    sentence_ends = [m.end() for m in SENTENCE_END.finditer(window)]
    if sentence_ends and sentence_ends[-1] >= floor:
        return window[: sentence_ends[-1]].rstrip()
    return window.rstrip()


def normalize_text(text: str) -> str:
    """One newline style, no trailing spaces, at most one empty line in a row."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _has_part(data: bytes, part: str) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return part in archive.namelist()
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


# --- FR-08b-10: spreadsheets ---


def cell_text(value: object) -> str:
    """One cell as short text: whole numbers without ".0", dates as YYYY-MM-DD."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return (
            value.date().isoformat()
            if value.time() == time(0)
            else value.isoformat(" ", "minutes")
        )
    if isinstance(value, date):
        return value.isoformat()
    return " ".join(str(value).split())


def row_line(number: int, values: Sequence[object]) -> str | None:
    """'Row 3: Logo | 2026-12-01 | 4' — empty cells at the end dropped; empty row → None.

    Row numbers are the sheet's own, so the AI (and the user) can point at rows.
    """
    cells = [cell_text(v) for v in values]
    while cells and not cells[-1]:
        cells.pop()
    if not cells:
        return None
    return f"Row {number}: " + " | ".join(cells)


def _xlsx_text(data: bytes, name: str) -> str:
    """Every sheet: a '## Sheet: <name>' line, then its non-empty rows."""
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        blocks: list[str] = []
        for sheet in book.worksheets:
            lines = [f"## Sheet: {sheet.title}"]
            for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                if number > MAX_SHEET_ROWS:
                    lines.append(
                        f"(more rows after row {MAX_SHEET_ROWS} were not read)"
                    )
                    break
                line = row_line(number, values)
                if line:
                    lines.append(line)
            if len(lines) > 1:
                blocks.append("\n".join(lines))
        book.close()
    except Exception as error:  # broken zip / XML, or a locked workbook
        raise DocumentError(DocumentErrorCode.UNREADABLE, name) from error
    return "\n\n".join(blocks)


def _csv_text(text: str) -> str:
    """Rows of a CSV file; the separator (, ; tab) is guessed from the first lines."""
    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, ",;\t")
    except csv.Error:
        dialect = csv.excel
    lines = []
    for number, values in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
        if number > MAX_SHEET_ROWS:
            lines.append(f"(more rows after row {MAX_SHEET_ROWS} were not read)")
            break
        line = row_line(number, values)
        if line:
            lines.append(line)
    return "\n".join(lines)
