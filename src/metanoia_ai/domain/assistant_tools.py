"""FR-22 phase 1: the read-only tools the voice assistant may pick, and the LLM answer check.

The LLM only picks a tool and copies words from the sentence (a name, a part of the day).
It never sees IDs and never writes the reply (#2). Anything else → `unsupported`.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from metanoia_ai.domain.spoken_period import SpokenPeriod

MIN_CONFIDENCE = 0.5
MAX_NAME_CHARS = 200


class ToolName(StrEnum):
    """Values sent in the API (`Turn.tool`) and in the LLM schema."""

    TODAY_BRIEFING = "today_briefing"
    AT_RISK_DEADLINES = "at_risk_deadlines"
    SCHEDULE_FOR = "schedule_for"
    RUNNING_TIMER = "running_timer"
    ESTIMATION_HABITS = "estimation_habits"
    PRODUCTIVE_HOURS = "productive_hours"
    SIMILAR_TASK_TIME = "similar_task_time"
    PROJECT_PROGRESS = "project_progress"
    LIST_PROJECTS = "list_projects"
    AI_QUOTA = "ai_quota"
    PENDING_CONFIRMATIONS = "pending_confirmations"
    UNREAD_NOTIFICATIONS = "unread_notifications"
    PENDING_PROPOSAL = "pending_proposal"
    UPCOMING_MILESTONES = "upcoming_milestones"
    # FR-22c: commands that change data. The AI only proposes; the app asks the user
    # and calls the Backend after a "yes".
    START_TIMER = "start_timer"
    STOP_TIMER = "stop_timer"
    FINISH_TIMER = "finish_timer"
    EXTEND_TIMER = "extend_timer"
    APPROVE_PROPOSAL = "approve_proposal"
    # FR-22d: schedule changes. The code computes a proposal; the app applies it after a "yes".
    INSERT_BLOCK = "insert_block"
    POSTPONE_TASK = "postpone_task"
    CATCH_UP = "catch_up"
    UNSUPPORTED = "unsupported"
    SMALLTALK = "smalltalk"


# FR-22c: commands the app runs after the user confirms.
WRITE_TOOLS = frozenset(
    {
        ToolName.START_TIMER,
        ToolName.STOP_TIMER,
        ToolName.FINISH_TIMER,
        ToolName.EXTEND_TIMER,
        ToolName.APPROVE_PROPOSAL,
    }
)
# FR-22d: proposals to move slots; always picked by the LLM (they need times or a name).
RESHUFFLE_TOOLS = frozenset(
    {ToolName.INSERT_BLOCK, ToolName.POSTPONE_TASK, ToolName.CATCH_UP}
)
# The tools that read data (#3). Smalltalk and unsupported never call anything.
READ_TOOLS = (
    frozenset(ToolName)
    - {ToolName.UNSUPPORTED, ToolName.SMALLTALK}
    - WRITE_TOOLS
    - RESHUFFLE_TOOLS
)
# Tools that take a spoken task / project name, matched by code (#6).
# For upcoming_milestones the name is optional: no name → all active projects.
NAMED_TOOLS = frozenset(
    {
        ToolName.SIMILAR_TASK_TIME,
        ToolName.PROJECT_PROGRESS,
        ToolName.UPCOMING_MILESTONES,
        ToolName.START_TIMER,
    }
)
# FR-22c #3: extra minutes asked for, like FR-16b.
MIN_EXTRA_MINUTES = 5
MAX_EXTRA_MINUTES = 480
DEFAULT_EXTRA_MINUTES = 15
# FR-22d: an urgent block said with a length ("2 tiếng") instead of an end time.
MAX_BLOCK_MINUTES = 720
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


class SmalltalkKind(StrEnum):
    GREET = "greet"
    THANKS = "thanks"
    HELP = "help"


@dataclass(frozen=True)
class ToolChoice:
    tool: ToolName
    confidence: float
    period: SpokenPeriod | None = None  # schedule_for only; missing → today
    name: str | None = None  # named tools only; None when the user said no name
    smalltalk: SmalltalkKind | None = None  # smalltalk only; missing → help
    minutes: int | None = None  # extend_timer (FR-22c #3); insert_block length (FR-22d)
    start_time: time | None = None  # insert_block only (FR-22d), on the user's clock
    end_time: time | None = None  # insert_block only
    low_confidence: bool = False  # the LLM picked something else, but too unsure (#12)


class BadToolChoiceError(ValueError):
    """The LLM answer does not follow the schema. The turn fails (502), no charge."""


def router_schema() -> dict[str, object]:
    """JSON Schema of the LLM answer (sent to Gemini as the response schema)."""
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [t.value for t in ToolName]},
            "period": {"type": "string", "enum": [p.value for p in SpokenPeriod]},
            "name": {"type": "string"},
            "smalltalk": {"type": "string", "enum": [s.value for s in SmalltalkKind]},
            "minutes": {"type": "integer", "minimum": 1},
            "start_time": {"type": "string"},  # "HH:MM", 24 h (FR-22d)
            "end_time": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["tool", "confidence"],
    }


def _enum(enum: type[StrEnum], value: object, field: str) -> StrEnum | None:
    if value is None:
        return None
    try:
        return enum(value)
    except ValueError as error:
        raise BadToolChoiceError(f"unknown {field}") from error


def parse_choice(raw: object, min_confidence: float = MIN_CONFIDENCE) -> ToolChoice:
    """Check the LLM answer and keep only what the picked tool uses.

    A confidence below `min_confidence` turns any tool into `unsupported` (#12).
    """
    if not isinstance(raw, Mapping):
        raise BadToolChoiceError("answer is not an object")
    tool = _enum(ToolName, raw.get("tool"), "tool")
    if tool is None:
        raise BadToolChoiceError("tool missing")
    confidence = raw.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise BadToolChoiceError("confidence must be a number in [0, 1]")
    confidence = float(confidence)

    if confidence < min_confidence and tool is not ToolName.UNSUPPORTED:
        return ToolChoice(ToolName.UNSUPPORTED, confidence, low_confidence=True)

    period = _enum(SpokenPeriod, raw.get("period"), "period")
    smalltalk = _enum(SmalltalkKind, raw.get("smalltalk"), "smalltalk")
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise BadToolChoiceError("name must be text")
    name = (name or "").strip()[:MAX_NAME_CHARS] or None

    match tool:
        case ToolName.SCHEDULE_FOR:
            return ToolChoice(tool, confidence, period=period or SpokenPeriod.TODAY)
        case _ if tool in NAMED_TOOLS:
            return ToolChoice(tool, confidence, name=name)
        case ToolName.SMALLTALK:
            return ToolChoice(
                tool, confidence, smalltalk=smalltalk or SmalltalkKind.HELP
            )
        case ToolName.EXTEND_TIMER:
            return ToolChoice(
                tool, confidence, minutes=clamp_minutes(raw.get("minutes"))
            )
        case ToolName.INSERT_BLOCK:
            return ToolChoice(
                tool,
                confidence,
                period=period or SpokenPeriod.TODAY,
                name=name,
                minutes=block_minutes(raw.get("minutes")),
                start_time=clock_time(raw.get("start_time")),
                end_time=clock_time(raw.get("end_time")),
            )
        case ToolName.POSTPONE_TASK:
            return ToolChoice(tool, confidence, name=name)
        case _:
            return ToolChoice(tool, confidence)


def clock_time(value: object) -> time | None:
    """ "14:30" → 14:30. Anything else → None (the assistant asks again, FR-22d #12)."""
    if not isinstance(value, str):
        return None
    match = _CLOCK.fullmatch(value.strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def block_minutes(value: object) -> int | None:
    """An urgent block's length when no end time is said; None when missing or odd."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not MIN_EXTRA_MINUTES <= value <= MAX_BLOCK_MINUTES:
        return None
    return round(value)


def clamp_minutes(value: object) -> int:
    """Extra minutes in [5, 480]; anything missing or not a number → 15 (FR-22c #3)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_EXTRA_MINUTES
    if not math.isfinite(value):
        return DEFAULT_EXTRA_MINUTES
    return max(MIN_EXTRA_MINUTES, min(MAX_EXTRA_MINUTES, round(value)))
