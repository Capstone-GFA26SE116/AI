from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.free_time import (
    BusyBlock,
    CalendarEvent,
    FixedTaskTime,
    busy_blocks,
    free_ranges,
)
from metanoia_ai.domain.models import (
    BlockedBy,
    BusyKind,
    Language,
    Placement,
    PlannedSlot,
    ScheduleUnit,
    ScheduleWarning,
    SplitReason,
    TimeRange,
    WarningType,
    WorkingHours,
)
from metanoia_ai.domain.planner import plan_schedule
from metanoia_ai.domain.schedule_text import (
    earlier_blocked_by,
    extra_percent,
    priority_ranks,
    short_title,
    slot_facts,
    spare_hours,
    split_reason,
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


# The "why this time" arguments, when a test is not about them.
WHY = {
    "blocked_by": BlockedBy.NONE,
    "priority": (1, 1),
    "estimate_minutes": 60,
    "planned_minutes": 78,
    "task_end": at(19, 15, 18),
}


def test_slot_facts_paid_plan():
    facts = slot_facts(
        slot(at(19, 14), at(19, 15, 18), part=1, parts=2),
        title="Write report",
        deadline=at(21, 17),
        user_k=Decimal("1.3"),
        productive_hours=frozenset({14, 15}),
        time_zone=SAIGON,
        **WHY,
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
        **WHY,
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
        **WHY,
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


# --- Step 18: why this time (plan #32) ---

# Common example from the plan: 9:00-17:00, no weekends, buffer 10'. 19 = Monday.
HOURS = WorkingHours(SAIGON, time(9), time(17), False, 10)
MONDAY_8AM = at(19, 8)
TASK_B = UUID(int=2)


def unit(task_id=TASK, minutes=60, deadline=None) -> ScheduleUnit:
    return ScheduleUnit(task_id, None, "Task", deadline or at(23, 17), minutes, None)


def blocker_of(units, *, now=MONDAY_8AM, fixed=(), events=(), productive=frozenset()):
    """Plan like the use case does, then explain the first slot of TASK."""
    blocks = busy_blocks(fixed, events, [])
    free = free_ranges(HOURS, now, 7, [b.time for b in blocks], round_minutes=5)
    result = plan_schedule(
        units,
        free,
        HOURS,
        productive,
        now=now,
        window_end=now + timedelta(days=7),
        round_minutes=5,
        min_chunk_minutes=30,
    )
    mine = next(s for s in result.slots if s.task_id == TASK)
    others = [
        BusyBlock(TimeRange(s.start, s.end), BusyKind.OTHER_TASK)
        for s in result.slots
        if s.task_id != TASK
    ]
    blocked = earlier_blocked_by(mine, now, blocks + others, HOURS, round_minutes=5)
    return mine, blocked


def test_t47_waited_for_focus_hour_is_not_blocked():
    mine, blocked = blocker_of([unit()], productive=frozenset({14}))
    assert mine.placement is Placement.WAITED_FOR_FOCUS_HOUR
    assert blocked is BlockedBy.NONE


def test_t49_meeting_before_the_slot():
    meeting = [FixedTaskTime(at(19, 9), at(19, 10), False)]
    mine, blocked = blocker_of([unit()], fixed=meeting)
    assert mine.start == at(19, 10)
    assert blocked is BlockedBy.MEETING


def test_t50_google_event_before_the_slot():
    events = [CalendarEvent(at(19, 9), at(19, 10), False, False, False)]
    mine, blocked = blocker_of([unit()], events=events)
    assert mine.start == at(19, 10)
    assert blocked is BlockedBy.CALENDAR_EVENT


def test_t51_task_with_a_nearer_deadline_takes_the_morning():
    mine_unit = unit(TASK, deadline=at(21, 17))
    urgent = unit(TASK_B, deadline=at(20, 17))
    mine, blocked = blocker_of([mine_unit, urgent])
    assert mine.start == at(19, 10, 10)  # after B (09:00-10:00) + 10' buffer
    assert blocked is BlockedBy.OTHER_TASK
    assert priority_ranks([mine_unit, urgent]) == {TASK_B: 1, TASK: 2}


def test_t52_work_day_over_moves_to_tomorrow_morning():
    mine, blocked = blocker_of([unit()], now=at(19, 20))
    assert mine.start == at(20, 9)
    assert blocked is BlockedBy.OUTSIDE_WORK_HOURS


def test_t53_starts_as_soon_as_possible():
    mine, blocked = blocker_of([unit()], now=at(19, 9, 7))
    assert mine.start == at(19, 9, 10)
    assert blocked is BlockedBy.NONE


def test_before_work_starts_today_is_not_outside_work_hours():
    # Now 08:00, slot 09:00 the same day: nothing to explain.
    _, blocked = blocker_of([unit()])
    assert blocked is BlockedBy.NONE


def test_day_too_short_left_over_the_weekend_is_outside_work_hours():
    # Friday 16:50: 10' left, the 60' task goes to Monday 09:00.
    mine, blocked = blocker_of([unit(deadline=at(28, 17))], now=at(23, 16, 50))
    assert mine.start == at(26, 9)
    assert blocked is BlockedBy.OUTSIDE_WORK_HOURS


def test_last_block_before_the_slot_wins():
    fixed = [FixedTaskTime(at(19, 9), at(19, 9, 30), False)]
    events = [CalendarEvent(at(19, 9, 30), at(19, 10), False, False, False)]
    _, blocked = blocker_of([unit()], fixed=fixed, events=events)
    assert blocked is BlockedBy.CALENDAR_EVENT


def test_t55_spare_hours_until_the_deadline():
    assert spare_hours(at(19, 15), at(20, 17)) == 26
    assert spare_hours(at(19, 15, 30), at(19, 17)) == 1  # whole hours only
    assert spare_hours(at(19, 17), at(19, 16)) == 0  # never negative


def test_t56_split_reason_only_for_split_units():
    assert split_reason(slot(at(19, 9), at(19, 12), part=1, parts=2)) is (
        SplitReason.NO_LONG_GAP
    )
    assert split_reason(slot(at(19, 9), at(19, 10))) is None


def test_slot_facts_v2_fields():
    facts = slot_facts(
        PlannedSlot(
            TASK,
            None,
            at(19, 14),
            at(19, 15, 18),
            Decimal("1.3"),
            placement=Placement.WAITED_FOR_FOCUS_HOUR,
        ),
        title="Write report",
        deadline=at(21, 17),
        user_k=Decimal("1.3"),
        productive_hours=frozenset({14}),
        time_zone=SAIGON,
        blocked_by=BlockedBy.NONE,
        priority=(1, 3),
        estimate_minutes=60,
        planned_minutes=78,
        task_end=at(19, 15, 18),
    )
    assert facts.placement is Placement.WAITED_FOR_FOCUS_HOUR
    assert facts.earlier_blocked_by is BlockedBy.NONE
    assert (facts.priority_rank, facts.priority_total) == (1, 3)
    assert (facts.estimate_minutes, facts.planned_minutes) == (60, 78)
    assert facts.spare_hours == 49  # Mon 15:18 → Wed 17:00
    assert facts.split_reason is None
