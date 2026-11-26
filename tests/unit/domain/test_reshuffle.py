"""FR-22d domain: urgent block, postpone to tomorrow, catch up missed slots. T1–T7."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.models import TimeRange, WorkingHours
from metanoia_ai.domain.reshuffle import (
    Blocker,
    Calendar,
    Choice,
    ConflictKind,
    MovableSlot,
    Rules,
    Why,
    next_working_day,
    plan_catch_up,
    plan_insert,
    plan_postpone,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
HOURS = WorkingHours(VN, time(8), time(18), False, 15)
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=VN)  # a Tuesday
FAR = datetime(2026, 11, 14, 23, 0, tzinfo=VN)


def at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=VN) + timedelta(days=days)


def span(h1: int, m1: int, h2: int, m2: int, days: int = 0) -> TimeRange:
    return TimeRange(at(h1, m1, days), at(h2, m2, days))


def slot(n: int, time_range: TimeRange, deadline: datetime | None = FAR) -> MovableSlot:
    return MovableSlot(
        UUID(int=n), f"Việc {n}", time_range, deadline, UUID(int=100 + n)
    )


def blocker(
    n: int, kind: ConflictKind, time_range: TimeRange, client: bool = False
) -> Blocker:
    return Blocker(kind, UUID(int=n), f"Bận {n}", time_range, client)


def cal(movable=(), blockers=(), other_busy=(), breaks=(), hours=HOURS) -> Calendar:
    return Calendar(
        hours, tuple(movable), tuple(blockers), tuple(other_busy), tuple(breaks)
    )


RULES = Rules(NOW, 5, 7)


def times(moves) -> list[tuple[int, str, str]]:
    return [
        (m.slot_id.int, m.new.start.strftime("%d %H:%M"), m.new.end.strftime("%H:%M"))
        for m in moves
    ]


# --- T1 / T2: push back in order, stop when nothing is in the way ---


def test_free_block_moves_nothing() -> None:
    result = plan_insert(span(14, 0, 16, 0), cal([slot(1, span(10, 0, 11, 0))]), RULES)

    assert (result.moves, result.not_placed, result.conflicts) == ((), (), ())
    assert (result.options, result.recommendation, result.other_time) == (
        (),
        None,
        None,
    )


def test_covered_slot_and_the_next_ones_are_pushed_back_in_order() -> None:
    movable = [
        slot(1, span(14, 0, 15, 0)),
        slot(2, span(15, 15, 16, 0)),
        slot(3, span(16, 15, 17, 0)),
    ]
    result = plan_insert(span(14, 0, 15, 0), cal(movable), RULES)

    # each one after the previous + 15' buffer
    assert times(result.moves) == [
        (1, "10 15:15", "16:15"),
        (2, "10 16:30", "17:15"),
        (3, "11 08:00", "08:45"),  # no room today → next working day
    ]


def test_pushing_stops_when_the_next_slot_is_not_in_the_way() -> None:
    movable = [slot(1, span(14, 0, 15, 0)), slot(2, span(16, 30, 17, 30))]
    result = plan_insert(span(14, 0, 15, 0), cal(movable), RULES)

    assert times(result.moves) == [(1, "10 15:15", "16:15")]  # slot 2 stays


def test_slot_starting_before_the_block_moves_after_it() -> None:
    result = plan_insert(
        span(14, 0, 15, 0), cal([slot(1, span(13, 30, 14, 30))]), RULES
    )

    assert times(result.moves) == [(1, "10 15:15", "16:15")]


def test_moved_slots_skip_the_lunch_break_and_round_to_5_minutes() -> None:
    result = plan_insert(
        span(10, 0, 11, 2),
        cal([slot(1, span(10, 30, 11, 30))], breaks=[span(12, 0, 13, 0)]),
        RULES,
    )

    assert times(result.moves) == [
        (1, "10 13:00", "14:00")
    ]  # 11:02+15 → 11:20, too short


def test_slots_of_other_days_stay() -> None:
    result = plan_insert(
        span(14, 0, 15, 0), cal([slot(1, span(14, 0, 15, 0, days=1))]), RULES
    )

    assert result.moves == ()


def test_past_day_end_is_flagged() -> None:
    assert plan_insert(span(17, 0, 19, 0), cal(), RULES).past_day_end is True
    assert plan_insert(span(16, 0, 18, 0), cal(), RULES).past_day_end is False


# --- T3 / T4: never moved, conflicts, importance, suggestion ---


def test_things_that_never_move_stay_and_count_as_busy() -> None:
    meeting = blocker(9, ConflictKind.FIXED_TASK, span(15, 15, 16, 15))
    result = plan_insert(
        span(14, 0, 15, 0), cal([slot(1, span(14, 0, 15, 0))], [meeting]), RULES
    )

    assert result.conflicts == ()  # the meeting is after the block
    # jumps over the meeting (like FR-10, no buffer is added after a meeting)
    assert times(result.moves) == [(1, "10 16:15", "17:15")]


def test_client_meeting_in_the_block_with_free_time_later_suggests_other_time() -> None:
    meeting = blocker(9, ConflictKind.FIXED_TASK, span(14, 30, 15, 30), client=True)
    result = plan_insert(span(14, 0, 16, 0), cal(blockers=[meeting]), RULES)

    (conflict,) = result.conflicts
    assert (conflict.important, conflict.why) == (True, Why.CLIENT)
    assert result.other_time == TimeRange(at(9), at(11))  # earliest free 2 h today
    assert result.options == (Choice.OTHER_TIME, Choice.OVERWRITE, Choice.CANCEL)
    assert result.recommendation is Choice.OTHER_TIME
    assert result.skip_task_ids == (UUID(int=9),)


def test_no_free_time_left_today_suggests_overwrite() -> None:
    meeting = blocker(9, ConflictKind.FIXED_TASK, span(16, 0, 17, 0))
    busy_day = [span(8, 0, 16, 0), span(17, 0, 18, 0)]
    result = plan_insert(
        span(16, 0, 17, 0), cal(blockers=[meeting], other_busy=busy_day), RULES
    )

    (conflict,) = result.conflicts
    assert (conflict.important, conflict.why) == (False, Why.PERSONAL)
    assert result.other_time is None
    assert result.options == (Choice.OVERWRITE, Choice.CANCEL)
    assert result.recommendation is Choice.OVERWRITE


def test_other_time_ignores_movable_slots() -> None:
    meeting = blocker(9, ConflictKind.CALENDAR_EVENT, span(14, 0, 15, 0))
    result = plan_insert(
        span(14, 0, 15, 0),
        cal([slot(1, span(9, 0, 10, 0))], [meeting]),
        RULES,
    )

    assert result.other_time == TimeRange(at(9), at(10))  # slot 1 could be pushed back


def test_kinds_of_conflict() -> None:
    blockers = [
        blocker(1, ConflictKind.CALENDAR_EVENT, span(14, 0, 14, 30)),
        blocker(2, ConflictKind.GROUP_SLOT, span(14, 30, 15, 0)),
    ]
    result = plan_insert(span(14, 0, 15, 0), cal(blockers=blockers), RULES)

    assert [(c.important, c.why) for c in result.conflicts] == [
        (True, Why.OTHER_PEOPLE),
        (True, Why.GROUP),
    ]
    assert result.skip_task_ids == ()  # only Fixed tasks are set to Skipped


def test_started_slot_cannot_be_overwritten() -> None:
    running = blocker(3, ConflictKind.STARTED_SLOT, span(13, 30, 14, 30))
    busy_day = [span(8, 0, 13, 30), span(14, 30, 18, 0)]
    result = plan_insert(
        span(14, 0, 15, 0), cal(blockers=[running], other_busy=busy_day), RULES
    )

    assert result.conflicts[0].why is Why.RUNNING
    assert result.options == (Choice.CANCEL,)
    assert result.recommendation is None


# --- T5: deadline ---


def test_slot_that_would_miss_its_deadline_is_not_placed() -> None:
    movable = [
        slot(1, span(14, 0, 15, 0), deadline=at(16)),
        slot(2, span(15, 15, 16, 0)),
    ]
    result = plan_insert(span(14, 0, 15, 0), cal(movable), RULES)

    assert [n.slot_id.int for n in result.not_placed] == [1]
    assert result.moves == ()  # slot 2 is no longer in the way


# --- T6: postpone to tomorrow ---


def test_postpone_goes_to_the_earliest_free_time_tomorrow() -> None:
    target = slot(1, span(14, 0, 15, 0))
    tomorrow_busy = blocker(9, ConflictKind.FIXED_TASK, span(8, 0, 9, 0, days=1))
    move = plan_postpone(target, cal([target], [tomorrow_busy]), RULES)

    assert move is not None
    assert (move.old, move.new) == (span(14, 0, 15, 0), span(9, 0, 10, 0, days=1))


def test_postpone_skips_the_weekend() -> None:
    friday = slot(1, span(14, 0, 15, 0, days=3), deadline=None)
    move = plan_postpone(friday, cal([friday]), RULES)

    assert move is not None and move.new == span(8, 0, 9, 0, days=6)  # Monday


def test_postpone_respects_other_slots_and_the_deadline() -> None:
    target = slot(1, span(14, 0, 15, 0), deadline=at(9, 30, days=1))
    other = slot(2, span(8, 0, 9, 0, days=1))

    assert plan_postpone(target, cal([target, other]), RULES) is None


def test_next_working_day() -> None:
    assert next_working_day(HOURS, at(9).date(), 7) == at(9, days=1).date()
    assert next_working_day(HOURS, at(9, days=3).date(), 7) == at(9, days=6).date()


# --- T7: catch up ---


def test_missed_slots_are_placed_from_now_in_order() -> None:
    now = at(13, 2)
    missed = [slot(2, span(10, 0, 11, 0)), slot(1, span(8, 0, 8, 45))]
    other = slot(3, span(14, 0, 15, 0))
    moves, not_placed = plan_catch_up(missed, cal([other, *missed]), Rules(now, 5, 7))

    # slot 2 does not fit before slot 3 at 14:00 → after it
    assert times(moves) == [(1, "10 13:05", "13:50"), (2, "10 15:00", "16:00")]
    assert not_placed == ()


def test_missed_slot_past_its_deadline_is_not_placed() -> None:
    now = at(17, 0)
    missed = [slot(1, span(8, 0, 10, 0), deadline=at(18))]
    moves, not_placed = plan_catch_up(missed, cal(missed), Rules(now, 5, 7))

    assert moves == ()
    assert [n.slot_id.int for n in not_placed] == [1]
