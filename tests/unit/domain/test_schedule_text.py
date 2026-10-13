from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.models import (
    Language,
    PlannedSlot,
    ScheduleWarning,
    WarningType,
)
from metanoia_ai.domain.schedule_text import (
    extra_percent,
    short_title,
    slot_facts,
    summary,
    warning_message,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
TASK = UUID(int=1)
LATE = ScheduleWarning(WarningType.NOT_ENOUGH_TIME, TASK, 600, 400)
OVERDUE = ScheduleWarning(WarningType.OVERDUE, TASK)
CALENDAR = ScheduleWarning(WarningType.CALENDAR_NOT_SYNCED)
INTERNAL_NAMES = ["K_bias", "KBias", "T_proposed", "T_est", "K="]


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


# --- Summary ---


def test_summary_vi_matches_plan_example():
    assert summary(5, 4, [LATE], Language.VI) == (
        "Xếp được 4/5 task. 1 task không kịp deadline."
    )


def test_summary_en():
    assert summary(5, 4, [LATE], Language.EN) == (
        "Scheduled 4 of 5 tasks. 1 task can't meet the deadline."
    )


def test_summary_counts_each_warning_type():
    warnings = [LATE, LATE, OVERDUE, CALENDAR]
    assert summary(6, 3, warnings, Language.VI) == (
        "Xếp được 3/6 task. 2 task không kịp deadline. 1 task đã quá hạn."
    )
    assert summary(6, 3, warnings, Language.EN) == (
        "Scheduled 3 of 6 tasks. 2 tasks can't meet the deadline. "
        "1 task is past the deadline."
    )


def test_summary_all_placed():
    assert summary(2, 2, [], Language.VI) == "Xếp được 2/2 task."


@pytest.mark.parametrize(
    ("lang", "text"),
    [
        (Language.VI, "Không có việc nào cần xếp."),
        (Language.EN, "Nothing to schedule."),
    ],
)
def test_summary_nothing_to_schedule(lang, text):
    assert summary(0, 0, [], lang) == text


# --- Warning messages ---


def test_not_enough_time_message_has_the_numbers():
    assert warning_message(LATE, Language.VI) == (
        "Cần 600 phút, nhưng trước deadline chỉ còn trống 400 phút."
    )
    assert "600" in warning_message(LATE, Language.EN)
    assert "400" in warning_message(LATE, Language.EN)


@pytest.mark.parametrize("warning", [LATE, OVERDUE, CALENDAR])
@pytest.mark.parametrize("lang", list(Language))
def test_every_warning_has_a_message_without_internal_names(warning, lang):
    message = warning_message(warning, lang)
    assert message
    assert not any(name in message for name in INTERNAL_NAMES)


def test_messages_differ_by_language():
    for warning in [LATE, OVERDUE, CALENDAR]:
        assert warning_message(warning, Language.VI) != warning_message(
            warning, Language.EN
        )


# --- Slot facts ---


def slot(start, end, k=Decimal("1.3"), part=1, parts=1) -> PlannedSlot:
    return PlannedSlot(TASK, None, start, end, k, part, parts)


def test_slot_facts_paid_plan():
    facts = slot_facts(
        slot(at(19, 14), at(19, 15, 18), part=1, parts=2),
        title="Write report",
        deadline=at(21, 17),
        user_k=Decimal("1.3"),
        productive_hours=frozenset({14, 15}),
        time_zone=SAIGON,
    )
    assert facts.task_title == "Write report"
    assert facts.days_to_deadline == 2  # Monday → Wednesday
    assert facts.in_productive_hour is True
    assert facts.extra_percent == 30
    assert facts.extra_applied is True
    assert (facts.part_index, facts.part_count) == (1, 2)


def test_slot_facts_free_plan_is_only_a_hint():
    facts = slot_facts(
        slot(at(19, 9), at(19, 10), k=None),
        title="Write report",
        deadline=at(19, 17),
        user_k=Decimal("1.25"),
        productive_hours=frozenset({14}),
        time_zone=SAIGON,
    )
    assert facts.extra_percent == 25
    assert facts.extra_applied is False
    assert facts.in_productive_hour is False
    assert facts.days_to_deadline == 0  # same day


def test_days_to_deadline_uses_the_user_clock():
    # 23:30 Monday in Saigon is still Monday there (16:30 UTC).
    utc = ZoneInfo("UTC")
    facts = slot_facts(
        slot(at(19, 9), at(19, 10)),
        title="T",
        deadline=at(19, 23, 30).astimezone(utc),
        user_k=None,
        productive_hours=frozenset(),
        time_zone=SAIGON,
    )
    assert facts.days_to_deadline == 0


@pytest.mark.parametrize(
    ("k", "percent"),
    [("1.3", 30), ("1.345", 35), ("1.0", 0), ("0.8", -20), (None, 0)],
)
def test_extra_percent(k, percent):
    assert extra_percent(Decimal(k) if k else None) == percent


def test_long_title_is_cut_to_100_characters():
    title = short_title("x" * 150)
    assert len(title) == 100
    assert title.endswith("…")
    assert short_title("  Short  ") == "Short"
    assert short_title("y" * 100) == "y" * 100
