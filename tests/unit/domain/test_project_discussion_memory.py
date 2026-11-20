"""FR-08b-6: shorten AI texts and find the newest checklist (plans/FR-08b-6.md T1)."""

from metanoia_ai.domain.project_discussion import (
    HISTORY_AI_TEXT_CHARS,
    DiscussionNotes,
    latest_notes,
    shorten,
)

OLD = DiscussionNotes(("Trang chủ",), (), (), True)
NEW = DiscussionNotes(("Trang chủ", "Trang blog"), (), (), True)


def test_short_text_is_kept_on_one_line():
    assert shorten("Chào bạn,\n\n- Giá: 12 triệu") == "Chào bạn, - Giá: 12 triệu"


def test_long_text_is_cut_with_an_ellipsis():
    text = shorten("x" * 2000)
    assert len(text) == HISTORY_AI_TEXT_CHARS and text.endswith("…")


def test_the_newest_checklist_wins():
    assert latest_notes([None, OLD, None, NEW, None]) == NEW


def test_no_checklist():
    assert latest_notes([None, None]) is None
    assert latest_notes([]) is None
