import pytest

from metanoia_ai.domain.assistant_tools import (
    NAMED_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    BadToolChoiceError,
    SmalltalkKind,
    ToolChoice,
    ToolName,
    parse_choice,
    router_schema,
)
from metanoia_ai.domain.spoken_period import SpokenPeriod


def test_fourteen_read_tools_five_commands_plus_two() -> None:
    assert len(READ_TOOLS) == 14
    assert len(WRITE_TOOLS) == 5
    assert not READ_TOOLS & WRITE_TOOLS
    assert ToolName.UNSUPPORTED not in READ_TOOLS
    assert NAMED_TOOLS == {
        ToolName.SIMILAR_TASK_TIME,
        ToolName.PROJECT_PROGRESS,
        ToolName.UPCOMING_MILESTONES,
        ToolName.START_TIMER,
    }


@pytest.mark.parametrize(
    ("value", "minutes"),
    [(15, 15), (30.4, 30), (2, 5), (999, 480), (None, 15), ("x", 15), (True, 15)],
)
def test_extend_minutes_are_clamped(value, minutes) -> None:
    choice = parse_choice({"tool": "extend_timer", "confidence": 0.9, "minutes": value})

    assert choice == ToolChoice(ToolName.EXTEND_TIMER, 0.9, minutes=minutes)


def test_milestones_keep_an_optional_project_name() -> None:
    named = parse_choice(
        {"tool": "upcoming_milestones", "confidence": 0.9, "name": " spa "}
    )
    unnamed = parse_choice({"tool": "upcoming_milestones", "confidence": 0.9})

    assert named == ToolChoice(ToolName.UPCOMING_MILESTONES, 0.9, name="spa")
    assert unnamed == ToolChoice(ToolName.UPCOMING_MILESTONES, 0.9)


def test_schema_lists_every_choice() -> None:
    schema = router_schema()

    assert schema["required"] == ["tool", "confidence"]
    assert set(schema["properties"]["tool"]["enum"]) == {t.value for t in ToolName}
    assert schema["properties"]["period"]["enum"] == [
        "morning",
        "afternoon",
        "evening",
        "today",
        "tomorrow",
    ]


def test_plain_tool() -> None:
    choice = parse_choice({"tool": "running_timer", "confidence": 0.9, "name": "x"})

    assert choice == ToolChoice(ToolName.RUNNING_TIMER, 0.9)  # unused fields dropped


def test_schedule_period_defaults_to_today() -> None:
    assert (
        parse_choice({"tool": "schedule_for", "confidence": 1}).period
        is SpokenPeriod.TODAY
    )
    choice = parse_choice(
        {"tool": "schedule_for", "period": "tomorrow", "confidence": 0.8}
    )
    assert choice.period is SpokenPeriod.TOMORROW


def test_named_tools_keep_a_clean_name() -> None:
    choice = parse_choice(
        {"tool": "project_progress", "name": "  Landing page  ", "confidence": 0.7}
    )
    assert choice.name == "Landing page"
    empty = parse_choice({"tool": "similar_task_time", "name": "  ", "confidence": 0.7})
    assert empty.name is None
    long = parse_choice(
        {"tool": "similar_task_time", "name": "a" * 500, "confidence": 0.7}
    )
    assert len(long.name) == 200


def test_smalltalk_kind_defaults_to_help() -> None:
    assert (
        parse_choice({"tool": "smalltalk", "confidence": 0.9}).smalltalk
        is SmalltalkKind.HELP
    )
    thanks = parse_choice(
        {"tool": "smalltalk", "smalltalk": "thanks", "confidence": 0.9}
    )
    assert thanks.smalltalk is SmalltalkKind.THANKS


# T5 (domain part): an unsure pick is not trusted
def test_low_confidence_becomes_unsupported() -> None:
    choice = parse_choice({"tool": "today_briefing", "confidence": 0.3})

    assert choice == ToolChoice(ToolName.UNSUPPORTED, 0.3, low_confidence=True)
    assert (
        parse_choice({"tool": "unsupported", "confidence": 0.1}).low_confidence is False
    )
    assert (
        parse_choice({"tool": "ai_quota", "confidence": 0.3}, min_confidence=0.2).tool
        is ToolName.AI_QUOTA
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"confidence": 0.9},
        {"tool": "delete_task", "confidence": 0.9},  # not a tool
        {"tool": "today_briefing"},
        {"tool": "today_briefing", "confidence": "high"},
        {"tool": "today_briefing", "confidence": True},
        {"tool": "today_briefing", "confidence": 1.5},
        {"tool": "today_briefing", "confidence": float("nan")},
        {"tool": "schedule_for", "period": "midnight", "confidence": 0.9},
        {"tool": "smalltalk", "smalltalk": "joke", "confidence": 0.9},
        {"tool": "project_progress", "name": 42, "confidence": 0.9},
    ],
)
def test_bad_answers_are_rejected(raw) -> None:
    with pytest.raises(BadToolChoiceError):
        parse_choice(raw)
