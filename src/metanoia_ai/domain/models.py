"""Basic domain types shared by the AI service."""

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
