"""FR-08b-10: spreadsheets as documents (plans/FR-08b-10.md T1-T3)."""

import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook

# Excel stores dates without a time zone, so the test values have none too.
DAY = datetime(2026, 12, 1)  # noqa: DTZ001
DAY_0930 = datetime(2026, 12, 1, 9, 30)  # noqa: DTZ001

from metanoia_ai.domain.document_text import (
    DocumentError,
    DocumentErrorCode,
    DocumentKind,
    cell_text,
    detect_kind,
    extract_text,
    row_line,
)


def xlsx(*sheets) -> bytes:
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def read(data: bytes, path: str) -> str:
    return extract_text(data, name=path, path=path, max_bytes=10**7, max_pdf_pages=50)


# --- T1: cells and rows ---


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        (4.0, "4"),
        (2.5, "2.5"),
        (DAY, "2026-12-01"),
        (DAY_0930, "2026-12-01 09:30"),
        (date(2026, 12, 1), "2026-12-01"),
        ("  Trang   chủ \n", "Trang chủ"),
        (True, "TRUE"),
    ],
)
def test_cell_text(value, text):
    assert cell_text(value) == text


def test_row_line_keeps_inner_blanks_and_drops_trailing_ones():
    assert row_line(3, ["Logo", None, 4, None, ""]) == "Row 3: Logo |  | 4"
    assert row_line(4, [None, ""]) is None


# --- T2: xlsx ---


def test_xlsx_sheets_and_rows_with_their_numbers():
    data = xlsx(
        (
            "Dự án",
            [("Hạng mục", "Hạn", "Giờ"), (), ("Trang chủ", DAY, 4)],
        ),
        ("Trống", []),
        ("Giá", [("Tổng", 12000000)]),
    )
    assert detect_kind(data, ".xlsx") is DocumentKind.XLSX
    assert detect_kind(data, ".bin") is DocumentKind.XLSX  # content wins
    assert read(data, "brief.xlsx") == (
        "## Sheet: Dự án\n"
        "Row 1: Hạng mục | Hạn | Giờ\n"
        "Row 3: Trang chủ | 2026-12-01 | 4\n\n"
        "## Sheet: Giá\n"
        "Row 1: Tổng | 12000000"
    )


def test_empty_workbook_has_no_text():
    with pytest.raises(DocumentError) as error:
        read(xlsx(("A", [])), "empty.xlsx")
    assert error.value.code is DocumentErrorCode.NO_TEXT


def test_broken_xlsx_is_unreadable():
    data = xlsx(("A", [("x",)]))
    broken = data[:200] + b"\x00" * 50 + data[250:]
    with pytest.raises(DocumentError) as error:
        read(broken, "broken.xlsx")
    assert error.value.code in {
        DocumentErrorCode.UNREADABLE,
        DocumentErrorCode.UNSUPPORTED_TYPE,
    }


# --- T3: csv ---


@pytest.mark.parametrize("sep", [",", ";", "\t"])
def test_csv_any_common_separator(sep):
    data = (
        sep.join(["Hạng mục", "Giờ"]).encode()
        + b"\n"
        + sep.join(["Logo", "4"]).encode()
    )
    assert detect_kind(data, ".csv") is DocumentKind.CSV
    assert read(data, "a.csv") == "Row 1: Hạng mục | Giờ\nRow 2: Logo | 4"


def test_csv_with_quotes_and_bom():
    data = '﻿Tên,Ghi chú\n"Logo","màu nâu, tối giản"\n'.encode()
    assert (
        read(data, "a.csv") == "Row 1: Tên | Ghi chú\nRow 2: Logo | màu nâu, tối giản"
    )


def test_a_txt_file_is_still_text():
    assert detect_kind(b"a,b\n1,2", ".txt") is DocumentKind.TEXT
