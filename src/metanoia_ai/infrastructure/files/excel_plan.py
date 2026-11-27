"""Read the three plan tables without sending workbook contents to a model."""

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from metanoia_ai.domain.excel_plan import (
    LIMIT,
    SHEETS,
    PlanRow,
    PlanRows,
    WorkbookError,
    code,
)

VI_HEADERS = {
    "projects": {
        "Mã dự án": "project_code",
        "Tên dự án": "name",
        "Mô tả tổng quan & Mục tiêu cốt lõi": "description",
        "Hạn hoàn thành (Deadline)": "deadline",
    },
    "milestones": {
        "Mã dự án": "project_code",
        "Mã cột mốc": "milestone_code",
        "Tên cột mốc (Milestone)": "name",
        "Hạn cột mốc": "deadline",
    },
    "tasks": {
        "Mã cột mốc": "milestone_code",
        "Dự án": "project_name",
        "Tên công việc (Task)": "name",
        "Thời lượng ước tính (giờ)": "estimated_hours",
        "Hạn chót (Due Date)": "deadline",
    },
}


def column_indexes(
    header: list[str], kind: str, canonical: tuple[str, ...]
) -> dict[int, str]:
    for mapping in (dict(zip(canonical, canonical, strict=True)), VI_HEADERS[kind]):
        if all(header.count(title) == 1 for title in mapping):
            columns = {header.index(title): field for title, field in mapping.items()}
            if mapping is VI_HEADERS["tasks"] and header.count("Loại việc") == 1:
                columns[header.index("Loại việc")] = "domain"
            return columns
    return {}


def hours_to_minutes(value: str) -> str:
    try:
        amount = Decimal(value)
        if amount.is_finite() and 0 < amount <= Decimal(2_147_483_647) / 60:
            total = amount * 60
            if total == total.to_integral_value():
                return str(int(total))
    except InvalidOperation:
        pass
    return ""  # The regular row validator prompts the user for minutes.


def link_task_projects(
    tables: dict[str, list[PlanRow]],
    project_names: dict[str, str],
) -> list[PlanRow]:
    projects_by_name: dict[str, set[str]] = defaultdict(set)
    for project in tables["projects"]:
        projects_by_name[code(project.name)].add(code(project.project_code))
    milestone_parents: dict[str, set[str]] = defaultdict(set)
    for milestone in tables["milestones"]:
        if milestone.project_code and milestone.milestone_code:
            milestone_parents[code(milestone.milestone_code)].add(
                code(milestone.project_code)
            )
    project_codes = {
        code(row.project_code): row.project_code for row in tables["projects"]
    }
    tasks = []
    for task in tables["tasks"]:
        if task.source in project_names:
            parents = milestone_parents[code(task.milestone_code)]
            named = projects_by_name[code(project_names[task.source])]
            if named:
                parents = parents & named
            if len(parents) == 1:
                parent = next(iter(parents))
                task = replace(task, project_code=project_codes.get(parent, parent))
        tasks.append(task)
    return tasks


def cell_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value).strip()


def read_plan(data: bytes) -> PlanRows:
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 100 * 1024 * 1024:
                raise WorkbookError("workbook_too_large")
        book = load_workbook(BytesIO(data), read_only=True)
        try:
            tables: dict[str, list[PlanRow]] = {}
            project_names: dict[str, str] = {}
            for kind, (english, vietnamese, headers) in SHEETS.items():
                matches = [
                    name
                    for name in (english, vietnamese, f"{vietnamese} ({english})")
                    if name in book.sheetnames
                ]
                if len(matches) != 1:
                    raise WorkbookError(f"expected_sheet: {english}")
                sheet = book[matches[0]]
                if sheet.max_column is not None and sheet.max_column > 64:
                    raise WorkbookError(f"column_limit: {sheet.title}")
                sheet.reset_dimensions()
                rows = sheet.iter_rows()
                header_cells = next(rows, ())
                header = [cell_value(cell.value) for cell in header_cells]
                indexes = column_indexes(header, kind, headers)
                if len(header) > 64 or not indexes:
                    raise WorkbookError(f"invalid_headers: {sheet.title}")
                parsed = []
                for number, cells in enumerate(rows, 2):
                    if number > LIMIT + 1 or len(cells) > 64:
                        raise WorkbookError(f"sheet_limit: {sheet.title}")
                    values = {}
                    for index, name in indexes.items():
                        if index >= len(cells):
                            continue
                        cell = cells[index]
                        if cell.data_type == "f":
                            raise WorkbookError(
                                f"formula_cell: {sheet.title}!{cell.coordinate}"
                            )
                        values[name] = cell_value(cell.value)
                    if any(values.values()):
                        source = f"{sheet.title}!{number}"
                        if "project_name" in indexes.values():
                            project_names[source] = values.pop("project_name", "")
                            values["estimated_minutes"] = hours_to_minutes(
                                values.pop("estimated_hours", "")
                            )
                        parsed.append(PlanRow(source=source, **values))
                tables[kind] = parsed
            if project_names:
                tables["tasks"] = link_task_projects(tables, project_names)
            return PlanRows(**tables)
        finally:
            book.close()
    except WorkbookError:
        raise
    except (
        BadZipFile,
        InvalidFileException,
        KeyError,
        OSError,
        ParseError,
        ValueError,
    ) as error:
        raise WorkbookError("unreadable_workbook") from error
