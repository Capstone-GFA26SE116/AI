"""Basic domain types shared by the AI service."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo


class TaskType(StrEnum):
    """Flexible tasks are measured and learned from; Fixed tasks are blocked time only."""

    FLEXIBLE = "flexible"
    FIXED = "fixed"


class ActualTimeSource(StrEnum):
    """Where an actual duration (T_act) came from."""

    TIMER = "timer"
    SELF_REPORTED = "self_reported"
    AUTO_TIMEOUT = "auto_timeout"


class ConfidenceTier(StrEnum):
    """How much we trust one learning sample, based on its time source."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SkipReason(StrEnum):
    """Why a finished task was not used to update K_bias. Values are sent in the API."""

    TASK_NOT_DONE = "task_not_done"
    ALREADY_LEARNED = "already_learned"
    FIXED_TASK = "fixed_task"
    NO_ACTUAL_TIME = "no_actual_time"
    SESSION_RUNNING = "session_running"
    UNCONFIRMED_TIME = "unconfirmed_time"
    OUTLIER = "outlier"
    AI_ESTIMATE = "ai_estimate"  # the estimate was set by the AI (FR-17b)


class EstimateSource(StrEnum):
    """Who set a task's estimate. Only the user's own estimates teach K_bias."""

    USER = "user"
    AI = "ai"


def estimate_source(
    estimated_minutes: int | None, ai_estimated_minutes: int | None
) -> EstimateSource:
    """AI when the task still has the AI's number; a changed number is the user's."""
    if ai_estimated_minutes is not None and estimated_minutes == ai_estimated_minutes:
        return EstimateSource.AI
    return EstimateSource.USER


@dataclass(frozen=True)
class FocusSessionRecord:
    """One work session on a task (timer, self-report or auto-stopped timer)."""

    started_at: datetime
    ended_at: datetime | None  # None: the session is still running
    source: ActualTimeSource
    is_confirmed: bool = True
    subtask_id: UUID | None = None
    remaining_minutes: int | None = None  # user's own "about X minutes left"


# --- Schedule proposal (FR-10) ---


def _require_aware(*values: datetime) -> None:
    """Every datetime in the planner must carry a timezone."""
    for value in values:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")


class Language(StrEnum):
    """Language of the text the user sees (summary, warnings, reasons)."""

    VI = "vi"
    EN = "en"


class WarningType(StrEnum):
    """Why part of a schedule could not be made. Values are sent in the API."""

    NOT_ENOUGH_TIME = "not_enough_time"
    OVERDUE = "overdue"
    CALENDAR_NOT_SYNCED = "calendar_not_synced"


class Placement(StrEnum):
    """How the planner chose a slot's start. Values are sent to the LLM."""

    EARLIEST_FREE = "earliest_free"
    WAITED_FOR_FOCUS_HOUR = "waited_for_focus_hour"  # earlier free time that day


class BusyKind(StrEnum):
    """What makes a time busy. Sent to the LLM instead of the event's name (privacy)."""

    MEETING = "meeting"  # a Fixed task
    CALENDAR_EVENT = "calendar_event"
    APPROVED_SLOT = "approved_slot"
    OTHER_TASK = "other_task"  # a running timer, or a slot of a task placed earlier


class BlockedBy(StrEnum):
    """Why a slot does not start earlier. Values are sent to the LLM."""

    MEETING = "meeting"
    CALENDAR_EVENT = "calendar_event"
    APPROVED_SLOT = "approved_slot"
    OTHER_TASK = "other_task"
    OUTSIDE_WORK_HOURS = "outside_work_hours"  # the work day was over
    NONE = "none"  # starts as early as it can, or waited for a focus hour


class SplitReason(StrEnum):
    """Why a unit was split into parts. Values are sent to the LLM."""

    NO_LONG_GAP = "no_long_gap"  # no free time long enough before the deadline


@dataclass(frozen=True)
class TimeRange:
    """A period of time [start, end), e.g. a busy block or a free gap."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, self.end)
        if self.end <= self.start:
            raise ValueError("end must be after start")

    @property
    def minutes(self) -> int:
        """Length in whole minutes."""
        return int((self.end - self.start) // timedelta(minutes=1))


@dataclass(frozen=True)
class WorkingHours:
    """When the user works, in their own time zone (from the User table)."""

    time_zone: ZoneInfo
    day_start: time
    day_end: time
    works_on_weekend: bool
    buffer_minutes: int  # break right after each slot; not put on the calendar

    def __post_init__(self) -> None:
        if self.day_end <= self.day_start:
            raise ValueError("day_end must be after day_start")
        if self.buffer_minutes < 0:
            raise ValueError("buffer_minutes must be >= 0")


@dataclass(frozen=True)
class ScheduleUnit:
    """One thing to place on the calendar: a task, or one unfinished subtask.

    `minutes` is already final (T_est × K, or the remaining part of a started task).
    `estimate_minutes` is the user's own estimate of what is left, before K
    (only used to explain the slot).
    """

    task_id: UUID
    subtask_id: UUID | None
    title: str
    deadline: datetime
    minutes: int
    k_bias_used: Decimal | None  # None on the Free plan
    order_index: int | None = None  # subtask order inside its task
    estimate_minutes: int | None = None

    def __post_init__(self) -> None:
        _require_aware(self.deadline)
        if self.minutes <= 0:
            raise ValueError("minutes must be > 0")
        if self.estimate_minutes is not None and self.estimate_minutes <= 0:
            raise ValueError("estimate_minutes must be > 0")


@dataclass(frozen=True)
class PlannedSlot:
    """One slot the planner chose. A split unit has several parts (1 of 2, 2 of 2)."""

    task_id: UUID
    subtask_id: UUID | None
    start: datetime
    end: datetime
    k_bias_used: Decimal | None
    part_index: int = 1
    part_count: int = 1
    placement: Placement = Placement.EARLIEST_FREE

    def __post_init__(self) -> None:
        _require_aware(self.start, self.end)
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if not 1 <= self.part_index <= self.part_count:
            raise ValueError("part_index must be in 1..part_count")

    @property
    def proposed_minutes(self) -> int:
        """Work minutes of this slot (the buffer is not included)."""
        return int((self.end - self.start) // timedelta(minutes=1))


@dataclass(frozen=True)
class ScheduleWarning:
    """A problem to show the user. The text is written later (vi/en)."""

    type: WarningType
    task_id: UUID | None = None  # None only for calendar_not_synced
    needed_minutes: int | None = None
    free_minutes: int | None = None

    def __post_init__(self) -> None:
        about_a_task = self.type is not WarningType.CALENDAR_NOT_SYNCED
        if about_a_task and self.task_id is None:
            raise ValueError(f"{self.type} warning needs a task_id")
