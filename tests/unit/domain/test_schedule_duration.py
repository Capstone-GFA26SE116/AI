from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.models import ActualTimeSource, FocusSessionRecord
from metanoia_ai.domain.schedule_duration import (
    minutes_to_schedule,
    remaining_estimate,
    scaled_minutes,
    sessions_of,
    worked_minutes,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=SAIGON)  # Monday 08:00
K = Decimal("1.3")


def session(
    minutes: int,
    *,
    days_ago: int = 1,
    running: bool = False,
    subtask_id=None,
    remaining: int | None = None,
) -> FocusSessionRecord:
    """A session that started `days_ago` days before NOW and lasted `minutes`."""
    start = NOW - timedelta(days=days_ago)
    if running:
        start = NOW - timedelta(minutes=minutes)
    return FocusSessionRecord(
        started_at=start,
        ended_at=None if running else start + timedelta(minutes=minutes),
        source=ActualTimeSource.TIMER,
        subtask_id=subtask_id,
        remaining_minutes=remaining,
    )


def test_t1_paid_plan_multiplies_by_k():
    assert minutes_to_schedule(60, [], K, NOW) == 78


def test_t2_free_plan_keeps_estimate():
    assert minutes_to_schedule(60, [], None, NOW) == 60


def test_t35_rounds_up():
    assert minutes_to_schedule(45, [], Decimal("1.34"), NOW) == 61  # 60.3


@pytest.mark.parametrize(
    ("minutes", "k", "expected"),
    [(60, "1.3", 78), (10, "1.1", 11), (100, "1.25", 125), (1, "0.5", 1)],
)
def test_scaled_minutes_has_no_float_error(minutes, k, expected):
    assert scaled_minutes(minutes, Decimal(k)) == expected


def test_t16_started_task_schedules_the_rest_times_k():
    assert minutes_to_schedule(120, [session(90)], K, NOW) == 39  # 30 * 1.3


def test_t16_remaining_minutes_is_used_as_is():
    sessions = [session(60, days_ago=2), session(30, remaining=20)]
    assert minutes_to_schedule(120, sessions, K, NOW) == 20


def test_only_the_latest_session_remaining_counts():
    # An older estimate of "20 left" is out of date once a newer session has none.
    sessions = [session(30, days_ago=2, remaining=20), session(60)]
    assert minutes_to_schedule(120, sessions, K, NOW) == 39  # max(120-90, 30) * 1.3


def test_started_task_gets_at_least_30_minutes():
    # Worked more than estimated: still 30 more minutes, times K.
    assert minutes_to_schedule(60, [session(80)], K, NOW) == 39


def test_started_task_on_free_plan():
    assert minutes_to_schedule(120, [session(90)], None, NOW) == 30


def test_t17_running_session_counts_up_to_now():
    sessions = [session(30), session(40, running=True)]
    assert worked_minutes(sessions, NOW) == 70
    assert minutes_to_schedule(120, sessions, K, NOW) == 65  # max(50, 30) * 1.3


def test_session_shorter_than_half_a_minute_counts_as_not_started():
    short = FocusSessionRecord(
        started_at=NOW - timedelta(seconds=20),
        ended_at=NOW - timedelta(seconds=5),
        source=ActualTimeSource.TIMER,
    )
    assert minutes_to_schedule(20, [short], K, NOW) == 26  # 20 * 1.3, not 30 * 1.3


def test_t36_subtask_uses_only_its_own_sessions():
    subtask = uuid4()
    sessions = [
        session(50, subtask_id=subtask),
        session(100, subtask_id=uuid4()),  # another subtask
        session(15),  # task-level session, no subtask
    ]
    own = sessions_of(sessions, subtask)
    assert minutes_to_schedule(60, own, K, NOW) == 39  # max(60-50, 30) * 1.3


def test_task_without_subtasks_uses_all_sessions():
    sessions = [session(50, subtask_id=uuid4()), session(15)]
    assert sessions_of(sessions, None) == sessions


# --- Step 17: the user's own estimate, before K (T54) ---


def estimate_and_planned(estimated, sessions, k):
    return (
        remaining_estimate(estimated, sessions, NOW),
        minutes_to_schedule(estimated, sessions, k, NOW),
    )


def test_t54_paid_plan_estimate_and_planned():
    assert estimate_and_planned(60, [], K) == (60, 78)


def test_t54_free_plan_estimate_equals_planned():
    assert estimate_and_planned(60, [], None) == (60, 60)


def test_t54_remaining_minutes_estimate_equals_planned():
    sessions = [session(60, days_ago=2), session(30, remaining=20)]
    assert estimate_and_planned(120, sessions, K) == (20, 20)


def test_started_task_estimate_is_the_rest_before_k():
    assert estimate_and_planned(120, [session(90)], K) == (30, 39)
