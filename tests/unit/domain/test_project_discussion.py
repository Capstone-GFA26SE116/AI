"""FR-08b-3 checklist rules (plans/FR-08b-3.md T1)."""

import pytest

from metanoia_ai.domain.project_discussion import (
    MAX_NOTE_CHARS,
    MAX_NOTE_LINES,
    check_notes,
    clean_lines,
)
from metanoia_ai.domain.task_breakdown import BadDraftError


def test_lines_are_trimmed_and_empty_ones_dropped():
    assert clean_lines(["  Trang chủ   có banner ", "", "   "], "clear") == (
        "Trang chủ có banner",
    )


def test_too_many_lines_is_a_bad_answer():
    with pytest.raises(BadDraftError, match="clear"):
        clean_lines(["x"] * (MAX_NOTE_LINES + 1), "clear")


def test_empty_lines_do_not_count_toward_the_limit():
    lines = ["x"] * MAX_NOTE_LINES + [""]
    assert len(clean_lines(lines, "clear")) == MAX_NOTE_LINES


def test_too_long_line_is_a_bad_answer():
    with pytest.raises(BadDraftError, match="risks"):
        clean_lines(["a" * (MAX_NOTE_CHARS + 1)], "risks")


def test_non_text_line_is_a_bad_answer():
    with pytest.raises(BadDraftError):
        clean_lines([3], "missing")


def test_internal_name_is_a_bad_answer():
    with pytest.raises(BadDraftError, match="internal"):
        clean_lines(["Your K_bias is high"], "risks")


def test_ready_when_nothing_is_missing():
    notes = check_notes(["Landing page"], [], ["Chưa có logo"], ready_to_plan=True)
    assert notes.ready_to_plan
    assert notes.clear == ("Landing page",)
    assert notes.risks == ("Chưa có logo",)


def test_something_missing_means_not_ready_whatever_the_ai_says():
    notes = check_notes([], ["Deadline?"], [], ready_to_plan=True)
    assert not notes.ready_to_plan


def test_blank_missing_lines_do_not_block_ready():
    assert check_notes([], ["  "], [], ready_to_plan=True).ready_to_plan


def test_questions_do_not_block_ready():
    notes = check_notes([], [], [], ready_to_plan=True, questions=["Mấy vòng góp ý?"])
    assert notes.ready_to_plan
    assert notes.questions == ("Mấy vòng góp ý?",)


def test_ai_may_say_not_ready_with_nothing_missing():
    assert not check_notes(["x"], [], [], ready_to_plan=False).ready_to_plan
