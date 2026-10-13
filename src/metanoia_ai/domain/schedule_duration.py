"""FR-10: how many work minutes a task or subtask still needs on the calendar."""

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from metanoia_ai.domain.actual_time import round_minutes
from metanoia_ai.domain.models import FocusSessionRecord

# A started task always gets at least this much more time.
MIN_REMAINING_MINUTES = 30


def scaled_minutes(minutes: int, k_bias: Decimal | None) -> int:
    """Return ceil(minutes × K). K is None on the Free plan: minutes stay as they are.

    Decimal, not float: 60 * 1.3 is 78.00000000000001 as a float, and ceil would give 79.
    """
    if k_bias is None:
        return minutes
    return math.ceil(Decimal(minutes) * k_bias)


def sessions_of(
    sessions: Sequence[FocusSessionRecord], subtask_id: UUID | None
) -> list[FocusSessionRecord]:
    """The sessions that count for one unit.

    A subtask uses only its own sessions; a task without subtasks uses all of them.
    """
    if subtask_id is None:
        return list(sessions)
    return [s for s in sessions if s.subtask_id == subtask_id]


def worked_minutes(sessions: Sequence[FocusSessionRecord], now: datetime) -> int:
    """Minutes already worked. A running session counts up to now."""
    total_seconds = sum(
        ((s.ended_at or now) - s.started_at).total_seconds() for s in sessions
    )
    return max(0, round_minutes(total_seconds))


def minutes_to_schedule(
    estimated_minutes: int,
    sessions: Sequence[FocusSessionRecord],
    k_bias: Decimal | None,
    now: datetime,
) -> int:
    """Work minutes to place for one unit (task or subtask).

    - Not started: ceil(T_est × K).
    - Latest session has RemainingMinutes: use it as is (no × K).
    - Started: ceil(max(T_est − worked, 30) × K). Worked includes a running session.
    """
    if not sessions:
        return scaled_minutes(estimated_minutes, k_bias)

    latest = max(sessions, key=lambda s: s.started_at)
    if latest.remaining_minutes is not None:
        return latest.remaining_minutes

    worked = worked_minutes(sessions, now)
    if worked == 0:
        return scaled_minutes(estimated_minutes, k_bias)
    remaining = max(estimated_minutes - worked, MIN_REMAINING_MINUTES)
    return scaled_minutes(remaining, k_bias)
