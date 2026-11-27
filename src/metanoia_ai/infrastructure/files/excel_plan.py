"""Read the three plan tables without sending workbook contents to a model."""

from datetime import date, datetime
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
)


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
            tables = {}
            for kind, (english, vietnamese, headers) in SHEETS.items():
                matches = [
                    name for name in (english, vietnamese) if name in book.sheetnames
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
                if len(header) > 64 or any(header.count(name) != 1 for name in headers):
                    raise WorkbookError(f"invalid_headers: {sheet.title}")
                indexes = {header.index(name): name for name in headers}
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
                        parsed.append(
                            PlanRow(source=f"{sheet.title}!{number}", **values)
                        )
                tables[kind] = parsed
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
