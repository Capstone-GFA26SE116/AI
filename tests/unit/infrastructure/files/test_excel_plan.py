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


def test_vietnamese_workbook_links_tasks_and_converts_hours() -> None:
    book = Workbook()
    book.remove(book.active)
    projects = book.create_sheet("Danh sách dự án")
    projects.append(
        (
            "Mã dự án",
            "Tên dự án",
            "Mô tả tổng quan & Mục tiêu cốt lõi",
            "Hạn hoàn thành (Deadline)",
        )
    )
    projects.append(("P1", "First", "Description", date(2027, 1, 12)))
    projects.append(("P2", "Second", "", date(2027, 2, 12)))
    milestones = book.create_sheet("Cột mốc (Milestones)")
    milestones.append(
        ("Mã cột mốc", "Mã dự án", "Tên cột mốc (Milestone)", "Hạn cột mốc")
    )
    milestones.append(("M1", "P1", "First step"))
    milestones.append(("M1", "P2", "Second step"))
    tasks = book.create_sheet("Công việc chi tiết (Tasks)")
    tasks.append(
        (
            "Mã cột mốc",
            "Dự án",
            "Tên công việc (Task)",
            "Thời lượng ước tính (giờ)",
            "Hạn chót (Due Date)",
        )
    )
    tasks.append(("M1", "Second", "Linked", 1.5, date(2027, 1, 13)))
    tasks.append(("M1", "Unknown", "Ambiguous", 1, date(2027, 1, 13)))
    stream = BytesIO()
    book.save(stream)

    rows = read_plan(stream.getvalue())
    assert rows.projects[0].description == "Description"
    assert rows.tasks[0].project_code == "P2"
    assert rows.tasks[0].estimated_minutes == "90"
    assert rows.tasks[0].domain == ""
    assert rows.tasks[1].project_code == ""
    assert rows.tasks[1].source == "Công việc chi tiết (Tasks)!3"
