"""FR-23 domain: free minutes in a window and the member ranking (T1–T4)."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.assignee_ranking import (
    Candidate,
    GroupRole,
    best_free_times,
    calendar_gaps,
    format_moment,
    format_range,
    free_minutes_in_window,
    hours,
    hours_text,
    needed_minutes,
    overlaps,
    rank_candidates,
    reason_facts,
)
from metanoia_ai.domain.models import Language, TimeRange, WorkingHours

VN = ZoneInfo("Asia/Ho_Chi_Minh")
HOURS = WorkingHours(
    time_zone=VN,
    day_start=time(8),
    day_end=time(17),
    works_on_weekend=False,
    buffer_minutes=15,
)
MONDAY_8 = datetime(2026, 11, 23, 8, tzinfo=VN)


def candidate(n: int, **changes: object) -> Candidate:
    values: dict[object, object] = {
        "user_id": UUID(int=n),
        "role": GroupRole.MEMBER,
        "is_current_assignee": False,
        "free_minutes": 600,
        "busy_at_fixed_time": False,
        "group_work_minutes": 0,
        "needed_minutes": 120,
    }
    values.update(changes)
    return Candidate(**values)  # type: ignore[arg-type]


def ids(ranked) -> list[int]:
    return [r.candidate.user_id.int for r in ranked]


# --- T4: free minutes ---


def test_free_minutes_counts_working_hours_in_the_window():
    end = MONDAY_8 + timedelta(days=1)  # Monday 08:00 → Tuesday 08:00
    assert free_minutes_in_window(HOURS, MONDAY_8, end, [], 0) == 9 * 60


def test_free_minutes_subtracts_busy_time_and_committed_work():
    end = MONDAY_8 + timedelta(days=1)
    busy = [TimeRange(MONDAY_8 + timedelta(hours=1), MONDAY_8 + timedelta(hours=3))]
    assert free_minutes_in_window(HOURS, MONDAY_8, end, busy, 60) == 9 * 60 - 120 - 60


def test_free_minutes_skips_weekend_and_cuts_at_window_end():
    friday_15 = datetime(2026, 11, 27, 15, tzinfo=VN)
    monday_10 = datetime(2026, 11, 30, 10, tzinfo=VN)
    # Friday 15–17 (120) + Monday 08–10 (120); Saturday and Sunday are off.
    assert free_minutes_in_window(HOURS, friday_15, monday_10, [], 0) == 240


def test_free_minutes_is_never_negative_and_zero_for_an_empty_window():
    end = MONDAY_8 + timedelta(hours=2)
    assert free_minutes_in_window(HOURS, MONDAY_8, end, [], 500) == 0
    assert free_minutes_in_window(HOURS, MONDAY_8, MONDAY_8, [], 0) == 0


def test_overlaps_ignores_touching_ends():
    task = TimeRange(MONDAY_8, MONDAY_8 + timedelta(hours=1))
    before = TimeRange(MONDAY_8 - timedelta(hours=1), MONDAY_8)
    inside = TimeRange(MONDAY_8 + timedelta(minutes=30), MONDAY_8 + timedelta(hours=2))
    assert not overlaps(task, [before])
    assert overlaps(task, [before, inside])


# --- T3: needed minutes and fits ---


def test_needed_minutes_uses_k_and_rounds_up():
    assert needed_minutes(100, Decimal("1.3")) == 130
    assert needed_minutes(61, Decimal("1.25")) == 77  # 76.25 → 77
    assert needed_minutes(None, Decimal("1.3")) is None


def test_fits_follows_free_and_needed_minutes():
    ranked = rank_candidates(
        [
            candidate(1, free_minutes=100, needed_minutes=150),
            candidate(2, free_minutes=200, needed_minutes=150),
            candidate(3, needed_minutes=None),
        ]
    )
    fits = {r.candidate.user_id.int: r.fits for r in ranked}
    assert fits == {1: False, 2: True, 3: None}


# --- T1, T2: order ---


def test_busy_at_fixed_time_goes_after_free_members():
    ranked = rank_candidates(
        [candidate(1, busy_at_fixed_time=True, free_minutes=2000), candidate(2)]
    )
    assert ids(ranked) == [2, 1]
    assert [r.rank for r in ranked] == [1, 2]


def test_fits_then_most_spare_then_least_group_work_then_id():
    ranked = rank_candidates(
        [
            candidate(1, free_minutes=100, needed_minutes=200),  # does not fit
            candidate(2, needed_minutes=None, free_minutes=5000),  # unknown
            candidate(3, free_minutes=500),  # fits, spare 380
            candidate(4, free_minutes=900),  # fits, spare 780
            candidate(
                5, free_minutes=500, group_work_minutes=60
            ),  # same spare, more work
            candidate(6, free_minutes=500),  # same as 3 → id decides
        ]
    )
    assert ids(ranked) == [4, 3, 6, 5, 2, 1]


def test_viewers_are_never_ranked():
    ranked = rank_candidates([candidate(1, role=GroupRole.VIEWER), candidate(2)])
    assert ids(ranked) == [2]


def test_reason_facts_mark_the_best_free_time_and_least_work():
    ranked = rank_candidates(
        [
            candidate(1, free_minutes=600, group_work_minutes=90),
            candidate(2, free_minutes=300, group_work_minutes=0),
        ]
    )
    facts = reason_facts(ranked)
    first, second = facts[UUID(int=1)], facts[UUID(int=2)]
    assert (first.rank, first.candidates) == (1, 2)
    assert first.most_free_time and not first.least_group_work
    assert second.least_group_work and not second.most_free_time
    assert first.free_hours == 10.0 and first.group_work_hours == 1.5
    assert reason_facts([]) == {}


def test_hours_rounds_to_one_decimal():
    assert hours(90) == 1.5
    assert hours(100) == 1.7


# --- FR-23 #7: concrete free times ---


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=VN)


def test_calendar_gaps_and_the_two_longest_in_time_order():
    busy = [TimeRange(at(23, 9), at(23, 10)), TimeRange(at(24, 8), at(24, 16, 40))]
    gaps = calendar_gaps(HOURS, MONDAY_8, at(25, 8), busy)
    assert [(g.start, g.end) for g in gaps] == [
        (at(23, 8), at(23, 9)),
        (at(23, 10), at(23, 17)),
        (at(24, 16, 40), at(24, 17)),  # 20 min: too short to show
    ]
    best = best_free_times(gaps)
    assert [(g.start, g.end) for g in best] == [
        (at(23, 8), at(23, 9)),
        (at(23, 10), at(23, 17)),
    ]
    assert best_free_times(gaps, count=1) == (gaps[1],)
    assert calendar_gaps(HOURS, MONDAY_8, MONDAY_8, []) == []


def test_times_are_written_in_the_language_and_clock():
    utc = ZoneInfo("UTC")
    moment = at(24, 8)
    assert format_moment(moment, VN, Language.VI) == "Thứ Ba 24/11, 08:00"
    assert format_moment(moment, VN, Language.EN) == "Tue 24/11, 08:00"
    assert format_moment(moment, utc, Language.EN) == "Tue 24/11, 01:00"
    same_day = TimeRange(at(24, 8), at(24, 12))
    assert format_range(same_day, VN, Language.VI) == "Thứ Ba 24/11, 08:00–12:00"
    overnight = TimeRange(at(24, 22), at(25, 1))
    assert format_range(overnight, VN, Language.EN) == (
        "Tue 24/11, 22:00 – Wed 25/11, 01:00"
    )


def test_reason_facts_carry_free_times_deadline_and_assignee():
    c = candidate(
        1,
        free_times=(TimeRange(at(24, 8), at(24, 12)),),
        time_zone=VN,
        is_current_assignee=True,
    )
    facts = reason_facts(rank_candidates([c]), Language.VI, at(25, 17))[UUID(int=1)]
    assert facts.free_times == ("Thứ Ba 24/11, 08:00–12:00",)
    assert facts.first_free == "Thứ Ba 24/11, 08:00"
    assert facts.deadline == "Thứ Tư 25/11, 17:00"
    assert facts.is_current_assignee
    bare = reason_facts(rank_candidates([candidate(2)]))[UUID(int=2)]
    assert bare.free_times == () and bare.first_free is None and bare.deadline is None


def test_hours_text_has_no_point_zero_and_uses_the_language():
    assert hours_text(570, Language.VI) == "9,5 giờ"
    assert hours_text(720, Language.VI) == "12 giờ"
    assert hours_text(570, Language.EN) == "9.5 hours"
    assert hours_text(60, Language.EN) == "1 hour"
    assert hours_text(0, Language.EN) == "0 hours"


def test_a_past_deadline_becomes_overdue_without_a_date():
    ranked = rank_candidates([candidate(1, time_zone=VN)])
    past = reason_facts(ranked, Language.VI, at(22, 5), now=at(23, 8))[UUID(int=1)]
    assert past.overdue and past.deadline is None
    future = reason_facts(ranked, Language.VI, at(25, 17), now=at(23, 8))[UUID(int=1)]
    assert not future.overdue and future.deadline == "Thứ Tư 25/11, 17:00"
