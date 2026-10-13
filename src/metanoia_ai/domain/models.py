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
    """

    task_id: UUID
    subtask_id: UUID | None
    title: str
    deadline: datetime
    minutes: int
    k_bias_used: Decimal | None  # None on the Free plan
    order_index: int | None = None  # subtask order inside its task

    def __post_init__(self) -> None:
        _require_aware(self.deadline)
        if self.minutes <= 0:
            raise ValueError("minutes must be > 0")


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
