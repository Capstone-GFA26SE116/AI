"""FR-08c: the import rules (plans/FR-08c.md T1)."""

from datetime import date

import pytest

from metanoia_ai.domain.project_import import (
    MAX_BRIEF_CHARS,
    ImportedProject,
    ImportInput,
    check_import,
    check_preview,
    first_message,
    projects_left,
)
from metanoia_ai.domain.task_breakdown import BadDraftError

TODAY = date(2026, 11, 20)


def item(name="Website tiệm bánh", brief="Row 2: Trang chủ", **kw):
    return ImportInput(name, brief, **kw)


def test_preview_cleans_texts_and_drops_past_deadlines():
    projects, dropped = check_preview(
        [
            item(
                "  Website   tiệm bánh ",
                "Row 1: A\n\n  Row 2:  B ",
                deadline=date(2026, 12, 15),
            ),
            item("Logo", "x", deadline=date(2026, 10, 1), source=" Sheet 1, rows 4-5 "),
        ],
        TODAY,
    )
    assert projects[0] == ImportedProject(
        "Website tiệm bánh", "Row 1: A\nRow 2: B", None, date(2026, 12, 15), None
    )
    assert projects[1].deadline is None and projects[1].source == "Sheet 1, rows 4-5"
    assert dropped == 1


def test_long_texts_are_cut():
    [project], _ = check_preview([item("x" * 300, "y" * 9000)], TODAY)
    assert len(project.name) == 150 and len(project.brief) == MAX_BRIEF_CHARS


@pytest.mark.parametrize(
    "items",
    [
        [],
        [item()] * 21,
        [item(""), item("B")],
        [item("A", "  ")],
        [item("Logo"), item(" logo ")],  # same name
    ],
)
def test_bad_lists_are_refused(items):
    with pytest.raises(BadDraftError):
        check_preview(items, TODAY)
    with pytest.raises(BadDraftError):
        check_import(items, TODAY)


def test_import_refuses_what_preview_would_fix():
    with pytest.raises(BadDraftError):
        check_import([item(deadline=date(2026, 11, 19))], TODAY)
    with pytest.raises(BadDraftError):
        check_import([item("x" * 151)], TODAY)
    assert check_import([item(deadline=TODAY)], TODAY)[0].deadline == TODAY


def test_projects_left_like_the_backend():
    assert projects_left(None, 99) is None
    assert projects_left(3, 1) == 2
    assert projects_left(3, 5) == 0


def test_first_message_names_the_file_and_the_part():
    project = ImportedProject("Logo", "Row 4: 3 phương án", source="Sheet 1, dòng 4")
    assert first_message("viec.xlsx", project, "vi") == (
        "Nội dung từ file viec.xlsx (Sheet 1, dòng 4):\n\nRow 4: 3 phương án"
    )
    plain = ImportedProject("Logo", "x")
    assert first_message("a.csv", plain, "en") == "From the file a.csv:\n\nx"
