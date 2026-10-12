"""Basic domain types shared by the AI service."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


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
