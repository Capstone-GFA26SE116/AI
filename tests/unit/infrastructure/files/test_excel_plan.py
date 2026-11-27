from datetime import date
from io import BytesIO
from uuid import UUID

import pytest
from openpyxl import Workbook

from metanoia_ai.domain.excel_plan import (
    SHEETS,
    WorkbookError,
    effective_deadline,
    validate,
)
from metanoia_ai.infrastructure.files.excel_plan import read_plan


def workbook(*, invalid_header: bool = False, formula: bool = False) -> bytes:
    book = Workbook()
    book.remove(book.active)
    for english, vietnamese, headers in SHEETS.values():
        book.create_sheet(vietnamese if english == "Tasks" else english).append(headers)
    book["Projects"].append(("001", "First", "", date(2027, 1, 12)))
    book["Projects"].append(("002", "Second", "", date(2027, 2, 12)))
    book["Milestones"].append(("001", "M1", "First step"))
    book["Milestones"].append(("002", "M1", "First step"))
    sheet = book["Công việc chi tiết"]
    sheet.append(("001", "M1", "Task", 60, "Coding"))
    sheet.append(())
    sheet.append(("002", "missing", "Other", "=1+1" if formula else 30, "Coding"))
    if invalid_header:
        sheet["D1"] = "hours"
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def test_reads_workbook_and_preserves_source() -> None:
    rows = read_plan(workbook())
    assert rows.projects[0].project_code == "001"
    assert rows.projects[0].deadline == "2027-01-12"
    assert rows.tasks[1].source == "Công việc chi tiết!4"
    assert effective_deadline(
        rows.tasks[0], rows.milestones[0], rows.projects[0]
    ) == date(2027, 1, 12)
    issues = validate(rows, {"coding": UUID(int=1)}, date(2026, 11, 27))
    assert [(issue.source, issue.code) for issue in issues] == [
        ("Công việc chi tiết!4", "unknown_milestone"),
    ]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"invalid_header": True}, "invalid_headers"),
        ({"formula": True}, "formula_cell"),
    ],
)
def test_rejects_ambiguous_workbook(options: dict[str, bool], message: str) -> None:
    with pytest.raises(WorkbookError, match=message):
        read_plan(workbook(**options))
