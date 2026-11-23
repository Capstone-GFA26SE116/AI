"""FR-23 domain: free minutes in a window and the member ranking (T1–T4)."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.assignee_ranking import (
    Candidate,
    GroupRole,
    free_minutes_in_window,
    hours,
    needed_minutes,
    overlaps,
    rank_candidates,
    reason_facts,
)
from metanoia_ai.domain.models import TimeRange, WorkingHours

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
