"""FR-16b domain: extend the late slot or move the ones after it. T1–T4."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TimeRange, WorkingHours
from metanoia_ai.domain.overrun_plan import (
    NotPlaced,
    OtherSlot,
    OverrunKind,
    new_end,
    plan_overrun,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
HOURS = WorkingHours(VN, time(8), time(18), False, 15)
NOW = datetime(2026, 11, 10, 10, 5, tzinfo=VN)  # a Tuesday
LATE = TimeRange(
    datetime(2026, 11, 10, 9, tzinfo=VN), datetime(2026, 11, 10, 10, tzinfo=VN)
)
TOMORROW_NIGHT = datetime(2026, 11, 11, 23, 0, tzinfo=VN)


def at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=VN) + timedelta(days=days)


def span(h1: int, m1: int, h2: int, m2: int, days: int = 0) -> TimeRange:
    return TimeRange(at(h1, m1, days), at(h2, m2, days))


def slot(
    n: int, start: TimeRange, movable: bool = True, deadline: datetime | None = None
) -> OtherSlot:
    return OtherSlot(
        UUID(int=n),
        f"Việc {n}",
        start.start,
        start.end,
        deadline if deadline is not None else TOMORROW_NIGHT,
        movable,
    )


def plan(others=(), blocked=(), extra=30, now=NOW, late=LATE):
    return plan_overrun(
        late,
        list(others),
        list(blocked),
        HOURS,
        now=now,
        extra_minutes=extra,
        round_minutes=5,
        window_days=7,
    )


def times(moves) -> list[tuple[int, str, str]]:
    return [
        (m.slot_id.int, m.new.start.strftime("%d %H:%M"), m.new.end.strftime("%H:%M"))
        for m in moves
    ]


# --- T1: extend or replan ---


def test_new_end_counts_from_now_when_the_slot_is_already_over() -> None:
    assert new_end(LATE.end, NOW, 30, HOURS, 5) == at(10, 35)  # 10:05 + 30
    assert new_end(at(11), NOW, 30, HOURS, 5) == at(11, 30)  # not over yet


def test_nothing_after_extends() -> None:
    result = plan()

    assert result.kind is OverrunKind.EXTEND
    assert result.extended == TimeRange(LATE.start, at(10, 35))
    assert (result.moves, result.not_placed, result.past_day_end) == ((), (), False)


def test_room_before_the_next_slot_extends() -> None:
    # 10:35 + 15' buffer = 10:50 ≤ 11:00
    assert plan([slot(1, span(11, 0, 12, 0))]).kind is OverrunKind.EXTEND


def test_exactly_enough_room_extends() -> None:
    assert plan([slot(1, span(10, 50, 11, 30))]).kind is OverrunKind.EXTEND


def test_five_minutes_short_replans() -> None:
    assert plan([slot(1, span(10, 45, 11, 30))]).kind is OverrunKind.REPLAN


def test_a_meeting_in_the_way_replans() -> None:
    assert plan(blocked=[span(10, 30, 11, 0)]).kind is OverrunKind.REPLAN


def test_past_the_working_day() -> None:
    late = span(17, 0, 18, 0)

    result = plan(now=at(17, 50), late=late)

    assert (result.kind, result.past_day_end) == (OverrunKind.EXTEND, True)
    assert result.extended.end == at(18, 30)  # not over yet: 18:00 + 30'


# --- T2: replan keeps the order, the lengths, Fixed and Google time ---


def test_replan_moves_the_later_slots_around_fixed_and_google_time() -> None:
    others = [
        slot(1, span(10, 45, 11, 45)),
        slot(2, span(13, 30, 14, 30)),
        slot(3, span(16, 0, 17, 0)),
    ]
    blocked = [span(12, 0, 13, 0), span(15, 0, 15, 30)]  # a meeting, a Google event

    result = plan(others, blocked)

    assert result.kind is OverrunKind.REPLAN
    assert times(result.moves) == [
        (1, "10 10:50", "11:50"),  # after the new end 10:35 + 15' buffer
        (2, "10 13:00", "14:00"),  # after the meeting
        (3, "10 15:30", "16:30"),  # 14:15–15:00 is too short, after the event
    ]
    assert [m.old for m in result.moves] == [TimeRange(o.start, o.end) for o in others]
    assert result.not_placed == ()


# --- T3: what is never moved ---


def test_slots_that_cannot_move_stay_and_block() -> None:
    started = slot(1, span(10, 45, 11, 15), movable=False)  # a timer was started on it
    later = slot(2, span(11, 30, 12, 0))

    result = plan([started, later])

    assert result.kind is OverrunKind.REPLAN
    assert times(result.moves) == [(2, "10 11:15", "11:45")]


def test_slots_of_other_days_and_earlier_slots_are_not_moved() -> None:
    earlier = slot(1, span(8, 0, 8, 30))
    next_day = slot(2, span(9, 0, 10, 0, days=1))
    blocker = slot(3, span(10, 45, 11, 0))

    result = plan([earlier, next_day, blocker])

    assert [m.slot_id.int for m in result.moves] == [3]


# --- T4: no room today, deadline ---


def test_no_room_today_goes_to_the_next_working_day_or_is_not_placed() -> None:
    meeting = span(11, 0, 18, 0)
    to_tomorrow = slot(1, span(10, 40, 11, 0))
    due_today = slot(2, span(10, 50, 11, 10), deadline=at(17))

    result = plan([to_tomorrow, due_today], [meeting])

    assert times(result.moves) == [(1, "11 08:00", "08:20")]
    assert result.not_placed == (NotPlaced(UUID(int=2), "Việc 2"),)


def test_friday_overflow_skips_the_weekend() -> None:
    friday = timedelta(days=3)  # 2026-11-13
    late = TimeRange(LATE.start + friday, LATE.end + friday)
    after_late = slot(1, span(10, 45, 11, 45, days=3), deadline=at(23, days=6))

    result = plan(
        [after_late],
        [span(11, 0, 18, 0, days=3)],
        now=NOW + friday,
        late=late,
    )

    assert times(result.moves) == [(1, "16 08:00", "09:00")]  # Monday
