"""FR-09b: why past similar tasks stopped (W2-14). T1, T2."""

from metanoia_ai.domain.memory_text import StopReason
from metanoia_ai.domain.similar_tasks import (
    MAX_NOTE_CHARS,
    PastSession,
    StopReasons,
    summarize_stop_reasons,
)


def s(reason: StopReason, note: str | None = None, client: str | None = None):
    return PastSession(reason, note, client)


def test_t1_counts_problems_not_completed() -> None:
    result = summarize_stop_reasons(
        [
            s(StopReason.INTERRUPTED),
            s(StopReason.INTERRUPTED),
            s(StopReason.BLOCKED),
            s(StopReason.OTHER),
            s(StopReason.COMPLETED),
        ]
    )

    assert (result.interrupted, result.blocked, result.other) == (2, 1, 1)
    assert result.notes == ()


def test_no_sessions() -> None:
    assert summarize_stop_reasons([]) == StopReasons()


def test_t2_notes_newest_first_no_blanks_no_repeats_at_most_two() -> None:
    result = summarize_stop_reasons(
        [
            s(StopReason.BLOCKED, "  Chờ khách   duyệt thiết kế "),
            s(StopReason.INTERRUPTED, ""),
            s(StopReason.INTERRUPTED, None),
            s(StopReason.BLOCKED, "Chờ khách duyệt thiết kế"),
            s(StopReason.OTHER, "Đổi yêu cầu"),
            s(StopReason.COMPLETED, "Thêm ghi chú cũ"),
        ]
    )

    assert result.notes == ("Chờ khách duyệt thiết kế", "Đổi yêu cầu")


def test_t2_notes_hide_contacts_and_the_client_name() -> None:
    result = summarize_stop_reasons(
        [s(StopReason.BLOCKED, "Gọi Shop Hoa 0901234567, mail a@b.vn", "Shop Hoa")]
    )

    [note] = result.notes
    assert "0901234567" not in note
    assert "a@b.vn" not in note
    assert "Shop Hoa" not in note


def test_t2_long_notes_are_cut() -> None:
    result = summarize_stop_reasons([s(StopReason.OTHER, "x" * 500)])

    [note] = result.notes
    assert len(note) == MAX_NOTE_CHARS
    assert note.endswith("…")
