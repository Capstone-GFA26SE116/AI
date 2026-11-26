"""FR-22d in the voice assistant: insert_block, postpone_task, catch_up. T12."""

from datetime import time, timedelta
from uuid import UUID

import pytest

from metanoia_ai.application.ports.reshuffle_repository import (
    CalendarData,
    CalendarSlot,
    FixedTask,
)
from metanoia_ai.application.use_cases.assistant_turn import (
    ActionType,
    Reshufflers,
    TurnKind,
    block_times,
)
from metanoia_ai.application.use_cases.reshuffle import (
    CatchUp,
    CatchUpResult,
    InsertBlock,
    InsertResult,
    PostponeResult,
    PostponeSlot,
    ReshuffleSettings,
)
from metanoia_ai.domain.assistant_tools import (
    ToolChoice,
    ToolName,
    block_minutes,
    clock_time,
    parse_choice,
)
from metanoia_ai.domain.models import Language, WorkingHours
from metanoia_ai.domain.overrun_plan import SlotState
from metanoia_ai.domain.spoken_period import SpokenPeriod
from tests.unit.application.test_assistant_turn import (
    NOW,
    TASK_BLOG,
    USER,
    VN,
    ask,
    charged,
    pick,
    setup,
)
from tests.unit.application.test_reshuffle_use_cases import FakeRepo

HOURS = WorkingHours(VN, time(8), time(18), False, 15)  # NOW is Friday 09:00
SETTINGS = ReshuffleSettings(5, 7)


def at(hour: int, minute: int = 0, days: int = 0):
    return NOW.replace(hour=hour, minute=minute) + timedelta(days=days)


def slot(
    n: int,
    start,
    end,
    state=SlotState.APPROVED,
    task: UUID | None = None,
    deadline=None,
):
    return CalendarSlot(
        UUID(int=n),
        f"Việc {n}",
        start,
        end,
        state,
        False,
        deadline or at(23, days=10),
        False,
        task_id=task or UUID(int=100 + n),
    )


def with_calendar(*answers, slots=(), fixed=()):
    use_case, _repo, quota, *_ = setup(*answers)
    data = CalendarData(
        HOURS, tuple(slots), tuple(fixed), (), None, UUID(int=500), None
    )
    fake = FakeRepo(data, USER)
    use_case.reshuffle = Reshufflers(
        InsertBlock(fake, SETTINGS, lambda: NOW),
        PostponeSlot(fake, SETTINGS, lambda: NOW),
        CatchUp(fake, SETTINGS, lambda: NOW),
    )
    return use_case, quota, fake


# --- parsing ---


def test_parse_insert_block() -> None:
    choice = parse_choice(
        {
            "tool": "insert_block",
            "confidence": 0.9,
            "start_time": "14:00",
            "end_time": "16:30",
            "name": "sửa lỗi",
        }
    )
    assert (choice.start_time, choice.end_time, choice.name, choice.period) == (
        time(14),
        time(16, 30),
        "sửa lỗi",
        SpokenPeriod.TODAY,
    )
    assert parse_choice({"tool": "postpone_task", "confidence": 0.9}).name is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14:00", time(14)),
        ("9:05", time(9, 5)),
        (" 07:30 ", time(7, 30)),
        ("24:00", None),
        ("14h", None),
        (14, None),
        (None, None),
        ("12:60", None),
    ],
)
def test_clock_time(raw, expected) -> None:
    assert clock_time(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(120, 120), (4, None), (721, None), (True, None), ("60", None), (90.4, 90)],
)
def test_block_minutes(raw, expected) -> None:
    assert block_minutes(raw) == expected


def choice(**kw) -> ToolChoice:
    return ToolChoice(ToolName.INSERT_BLOCK, 0.9, **kw)


def test_block_times() -> None:
    assert block_times(choice(), NOW, VN) is None  # no start → ask
    assert block_times(choice(start_time=time(14)), NOW, VN) == (at(14), at(15))  # 60'
    assert block_times(choice(start_time=time(14), minutes=90), NOW, VN) == (
        at(14),
        at(15, 30),
    )
    assert block_times(
        choice(start_time=time(9), end_time=time(11), period=SpokenPeriod.TOMORROW),
        NOW,
        VN,
    ) == (at(9, days=1), at(11, days=1))
    assert block_times(choice(start_time=time(14), end_time=time(13)), NOW, VN) is None


# --- insert_block ---


def test_insert_block_confirms_with_the_preview() -> None:
    use_case, quota, _ = with_calendar(
        pick("insert_block", start_time="14:00", end_time="15:00", name="sửa lỗi gấp"),
        slots=[slot(1, at(14), at(15))],
    )

    turn = ask(use_case, "Chiều nay 2 giờ tới 3 giờ có việc gấp sửa lỗi")

    assert turn.kind is TurnKind.CONFIRM and turn.tool is ToolName.INSERT_BLOCK
    assert turn.action.type is ActionType.INSERT_BLOCK
    preview = turn.action.preview
    assert isinstance(preview, InsertResult)
    assert [(m.slot_id.int, m.new.start) for m in preview.plan.moves] == [
        (1, at(15, 15))
    ]
    assert turn.reply == preview.summary
    assert turn.reply.startswith("Thêm sửa lỗi gấp từ 14 giờ đến 15 giờ và dời 1 việc")
    assert charged(quota) == 4  # 1 AI call to understand the sentence


def test_insert_block_with_a_meeting_in_the_way() -> None:
    meeting = FixedTask(UUID(int=9), "Họp khách", at(14), at(15), False, True)
    use_case, _, _ = with_calendar(
        pick("insert_block", start_time="14:00", end_time="15:00"), fixed=[meeting]
    )

    turn = ask(use_case)

    assert turn.kind is TurnKind.CONFIRM
    assert "trùng với Họp khách (việc với khách)" in turn.reply
    assert turn.reply.startswith("Việc gấp từ 14 giờ")  # default title


def test_insert_block_without_a_start_asks() -> None:
    use_case, _, fake = with_calendar(pick("insert_block"))

    turn = ask(use_case, "Tôi có việc gấp")

    assert (turn.kind, turn.action) == (TurnKind.ANSWER, None)
    assert turn.reply == "Việc gấp bắt đầu lúc mấy giờ và làm đến mấy giờ?"
    assert fake.calls == []


def test_insert_block_in_the_past_says_so() -> None:
    use_case, _, _ = with_calendar(
        pick("insert_block", start_time="07:00", end_time="08:00")
    )

    turn = ask(use_case)

    assert (turn.kind, turn.action) == (TurnKind.ANSWER, None)
    assert turn.reply.startswith("Khung giờ đó không xếp được")


# --- postpone_task ---


def test_postpone_this_task_takes_the_slot_of_today() -> None:
    use_case, _, _ = with_calendar(
        pick("postpone_task"),
        slots=[
            slot(1, at(8), at(8, 30)),
            slot(2, at(10), at(11)),
            slot(3, at(9, days=3), at(10, days=3)),
        ],
    )

    turn = ask(use_case, "Bỏ việc này, để mai làm")

    assert turn.kind is TurnKind.CONFIRM and turn.action.type is ActionType.POSTPONE
    preview = turn.action.preview
    assert isinstance(preview, PostponeResult)
    assert (preview.slot_id.int, turn.action.slot_id.int) == (2, 2)  # slot 1 is over
    assert preview.move.new.start == at(8, days=3)  # Friday → Monday
    assert turn.reply == "Dời Việc 2 sang 8 giờ ngày 2/11 nhé?"


def test_postpone_a_named_task() -> None:
    use_case, _, _ = with_calendar(
        pick("postpone_task", name="viết blog"),
        slots=[slot(1, at(10), at(11)), slot(2, at(14), at(15), task=TASK_BLOG.id)],
    )

    turn = ask(use_case, "Dời việc viết blog sang mai")

    assert turn.action.preview.slot_id.int == 2


def test_postpone_unknown_name_or_no_slot() -> None:
    use_case, _, _ = with_calendar(pick("postpone_task", name="xyz không có"))
    assert ask(use_case).action is None

    use_case, _, _ = with_calendar(pick("postpone_task"))
    turn = ask(use_case)
    assert (turn.kind, turn.reply) == (
        TurnKind.ANSWER,
        "Mình không thấy việc nào trên lịch hôm nay mà dời được.",
    )


def test_postpone_without_room_before_the_deadline_answers() -> None:
    use_case, _, _ = with_calendar(
        pick("postpone_task"), slots=[slot(1, at(10), at(11), deadline=at(17))]
    )

    turn = ask(use_case)

    assert (turn.kind, turn.action) == (TurnKind.ANSWER, None)
    assert turn.reply.startswith("Không còn chỗ trống")


# --- catch_up ---


def test_catch_up_confirms_when_something_was_missed() -> None:
    use_case, _, _ = with_calendar(
        pick("catch_up"), slots=[slot(1, at(8), at(8, 45), SlotState.MISSED)]
    )

    turn = ask(use_case, "Xếp lại mấy việc bị lỡ")

    assert turn.kind is TurnKind.CONFIRM and turn.action.type is ActionType.CATCH_UP
    assert isinstance(turn.action.preview, CatchUpResult)
    assert turn.action.preview.moves[0].move.new.start == at(9)


def test_catch_up_with_nothing_missed_answers() -> None:
    use_case, _, _ = with_calendar(pick("catch_up"))

    turn = ask(use_case, "Xếp lại việc bị lỡ")

    assert (turn.kind, turn.action) == (TurnKind.ANSWER, None)


def test_without_the_use_cases_it_is_unsupported() -> None:
    use_case, *_ = setup(pick("catch_up"))

    assert ask(use_case).kind is TurnKind.UNSUPPORTED


def test_english() -> None:
    use_case, _, _ = with_calendar(
        pick("insert_block", start_time="14:00", minutes=30), slots=[]
    )

    turn = ask(use_case, "Urgent thing at 2 pm for half an hour", Language.EN)

    assert (
        turn.reply == "Add Urgent work from 2 pm to 2:30 pm; nothing needs to move. OK?"
    )


def test_local_router_leaves_these_sentences_to_the_llm() -> None:
    use_case, *_ = setup(pick("catch_up"), local=True)
    use_case.reshuffle = None

    turn = ask(use_case, "Chiều nay 2 giờ đến 4 giờ tôi có việc gấp")

    assert turn.kind is TurnKind.UNSUPPORTED  # went to the (fake) LLM: picked catch_up


@pytest.mark.parametrize(
    "text",
    [
        "Chiều nay 2 giờ đến 4 giờ tôi có việc gấp",
        "Có việc đột xuất lúc 3 giờ",
        "Bỏ việc này, để mai làm",
        "Dời việc viết blog sang mai giúp tôi",
        "Xếp lại mấy việc bị lỡ",
        "Reschedule what I missed today",
    ],
)
def test_the_local_router_never_answers_schedule_changes(text: str) -> None:
    from metanoia_ai.domain.local_router import local_choice
    from tests.unit.domain.test_local_router import ROUTER
    from tests.unit.domain.test_local_router import SETTINGS as LOCAL

    assert local_choice(ROUTER, text, False, LOCAL) is None
