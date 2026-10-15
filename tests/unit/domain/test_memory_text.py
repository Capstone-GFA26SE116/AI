from datetime import UTC, datetime, timedelta

import pytest

from metanoia_ai.domain.memory_text import (
    MemoryDraft,
    MemoryOutcome,
    MemorySession,
    StopReason,
    build_memory_draft,
    build_memory_text,
    mask_contact,
)

MONDAY_9AM = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def session(
    day: int,
    minutes: float | None,
    reason: StopReason | None,
    note: str | None = None,
) -> MemorySession:
    start = MONDAY_9AM + timedelta(days=day)
    end = None if minutes is None else start + timedelta(minutes=minutes)
    return MemorySession(start, end, reason, note)


def text(
    sessions,
    title="Viết landing page cho Acme",
    outcome=MemoryOutcome.DONE,
    client="Acme",
    max_chars=1000,
):
    return build_memory_text(title, "Coding", outcome, sessions, client, max_chars)


# --- T3: emails and phone numbers ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail an.nguyen+work@acme.com.vn now", "mail [email] now"),
        ("call 0901 234 567 today", "call [phone] today"),
        ("call +84 90-123-4567 today", "call [phone] today"),
        ("call 0901.234.567", "call [phone]"),
        ("call 0901234567", "call [phone]"),
    ],
)
def test_t3_emails_and_phones_are_masked(text, expected):
    assert mask_contact(text, client_name=None) == expected


def test_t3_title_and_note_together():
    text = "Gửi file cho a@b.co, gọi 0901 234 567 hoặc +84 90-123-4567"
    assert mask_contact(text, None) == "Gửi file cho [email], gọi [phone] hoặc [phone]"


# --- T4: client name, any case ---


@pytest.mark.parametrize(
    "text",
    ["Landing page cho Acme Corp", "landing page cho ACME CORP", "acme corp: sửa logo"],
)
def test_t4_client_name_is_masked_ignoring_case(text):
    assert "[client]" in mask_contact(text, client_name="Acme Corp")
    assert "acme" not in mask_contact(text, client_name="Acme Corp").lower()


def test_t4_vietnamese_client_name():
    result = mask_contact("Làm logo cho Công ty Hoa Mai", client_name="công ty hoa mai")
    assert result == "Làm logo cho [client]"


def test_client_name_is_matched_as_a_whole_word():
    # Client "An" must not break words that only contain the letters "an".
    assert (
        mask_contact("Analytics cho An", client_name="An") == "Analytics cho [client]"
    )


def test_email_with_client_name_is_masked_as_one_email():
    assert mask_contact("gửi bob@acme.com", client_name="Acme") == "gửi [email]"


def test_client_name_with_special_characters():
    assert (
        mask_contact("Việc cho A.B (VN)", client_name="A.B (VN)") == "Việc cho [client]"
    )


# --- T5: no client (Personal project) ---


@pytest.mark.parametrize("client_name", [None, "", "   "])
def test_t5_no_client_changes_nothing(client_name):
    text = "Viết blog cá nhân"
    assert mask_contact(text, client_name) == text


# --- T7: dates and short numbers are not phone numbers ---


@pytest.mark.parametrize(
    "text",
    [
        "Hạn 14-10-2026",
        "Hạn 14/10/2026",
        "Sửa 3 lần",
        "Version 2.1.4",
        "Năm 2026, mã 12345678",
    ],
)
def test_t7_dates_and_short_numbers_stay(text):
    assert mask_contact(text, None) == text


# --- T1: full example ---


def test_t1_example_from_plan():
    # Notes are given out of order on purpose: the text must sort them by time.
    sessions = [
        session(2, 60, StopReason.INTERRUPTED, "sửa responsive lâu"),
        session(0, 50, StopReason.INTERRUPTED, "chờ khách gửi logo"),
        session(1, 30, StopReason.BLOCKED),
        session(3, 40, StopReason.COMPLETED),
    ]
    assert text(sessions) == (
        "title: Viết landing page cho [client] | text: Coding. Done. "
        "Interrupted 2 times, blocked 1 time. "
        "Notes: chờ khách gửi logo; sửa responsive lâu."
    )


def test_t1_other_reason_is_counted_and_completed_is_not():
    sessions = [session(0, 30, StopReason.OTHER), session(1, 30, StopReason.COMPLETED)]
    assert text(sessions).endswith("Coding. Done. Other 1 time.")


def test_running_session_reason_is_not_counted_but_note_is_kept():
    sessions = [session(0, 30, StopReason.BLOCKED), session(1, None, None, "đang làm")]
    assert text(sessions).endswith("Blocked 1 time. Notes: đang làm.")


def test_notes_are_masked():
    sessions = [
        session(0, 30, StopReason.COMPLETED, "gọi 0901 234 567, mail a@acme.com")
    ]
    assert text(sessions).endswith("Notes: gọi [phone], mail [email].")


def test_skipped_outcome():
    assert text([], outcome=MemoryOutcome.SKIPPED).endswith("Coding. Skipped.")


# --- T2: no reasons, no notes ---


def test_t2_no_reasons_and_no_notes():
    sessions = [session(0, 45, StopReason.COMPLETED, "   ")]
    assert (
        text(sessions) == "title: Viết landing page cho [client] | text: Coding. Done."
    )


# --- T6: long notes ---


def test_t6_long_notes_drop_the_oldest_first():
    sessions = [
        session(0, 30, StopReason.COMPLETED, "OLD " + "a" * 500),
        session(1, 30, StopReason.COMPLETED, "MID " + "b" * 400),
        session(2, 30, StopReason.COMPLETED, "NEW " + "c" * 100),
    ]
    result = text(sessions)
    assert len(result) <= 1000
    assert result.startswith("title: Viết landing page cho [client] | text: ")
    assert "OLD" not in result
    assert "MID" in result and "NEW" in result


def test_t6_all_notes_dropped_when_needed():
    sessions = [session(0, 30, StopReason.COMPLETED, "x" * 2000)]
    assert (
        text(sessions) == "title: Viết landing page cho [client] | text: Coding. Done."
    )


def test_t6_custom_limit():
    sessions = [
        session(d, 30, StopReason.COMPLETED, f"note {d} " + "z" * 40) for d in range(5)
    ]
    assert len(text(sessions, max_chars=200)) <= 200


# --- MemoryDraft ---


def test_draft_numbers():
    sessions = [
        session(0, 50.4, StopReason.INTERRUPTED),
        session(1, 40.4, StopReason.COMPLETED),
        session(2, None, None),  # still running: not counted
    ]
    draft = build_memory_draft(
        "Landing", "Coding", MemoryOutcome.DONE, 60, sessions, None, 1000
    )
    assert draft == MemoryDraft(
        content="title: Landing | text: Coding. Done. Interrupted 1 time.",
        outcome=MemoryOutcome.DONE,
        estimated_minutes=60,
        actual_minutes=91,  # 50.4 + 40.4 = 90.8 -> 91
    )


def test_draft_without_sessions_has_zero_actual_minutes():
    draft = build_memory_draft(
        "Landing", "Coding", MemoryOutcome.SKIPPED, 60, [], None, 1000
    )
    assert draft.actual_minutes == 0
