"""FR-16b use case with a fake repository (no DB). T6."""

import asyncio
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.overrun_repository import OverrunData, SlotRow
from metanoia_ai.application.use_cases.plan_overrun import (
    LOOK_BACK,
    OverrunRequest,
    OverrunSettings,
    PlanOverrun,
    SlotNotFoundError,
    SlotNotLateError,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.domain.free_time import CalendarEvent, FixedTaskTime
from metanoia_ai.domain.models import Language, WorkingHours
from metanoia_ai.domain.overrun_plan import OverrunKind, SlotState
from tests.unit.application.fakes import FakeOverrunRepository

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 10, 10, 5, tzinfo=VN)
USER = UUID(int=1)
LATE_ID = UUID(int=10)
HOURS = WorkingHours(VN, time(8), time(18), False, 15)
SETTINGS = OverrunSettings(round_minutes=5, window_days=7)


def at(hour: int, minute: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def row(
    n: int,
    start: datetime,
    end: datetime,
    state: SlotState = SlotState.APPROVED,
    *,
    is_fixed: bool = False,
    started: bool = False,
    title: str | None = None,
) -> SlotRow:
    return SlotRow(
        UUID(int=n),
        title or f"Việc {n}",
        start,
        end,
        state,
        is_fixed,
        None if is_fixed else at(23),
        started,
    )


LATE = row(10, at(9), at(10), SlotState.OVERRUN, started=True, title="Viết blog")


def setup(*slots: SlotRow, fixed=(), events=()):
    repo = FakeOverrunRepository()
    repo.users[USER] = OverrunData(
        HOURS, None, (LATE, *slots), tuple(fixed), tuple(events)
    )
    return PlanOverrun(repo, SETTINGS, now=lambda: NOW), repo


def ask(use_case, slot_id=LATE_ID, extra=30, lang=Language.VI, user=USER):
    return asyncio.run(use_case.execute(OverrunRequest(user, slot_id, extra, lang)))


def test_extend_when_there_is_room() -> None:
    use_case, repo = setup(row(1, at(11), at(12)))

    result = ask(use_case)

    assert result.plan.kind is OverrunKind.EXTEND
    assert result.plan.extended.end == at(10, 35)
    assert result.summary == "Mình đề xuất làm tiếp Viết blog đến 10 giờ 35."
    assert result.time_zone == "Asia/Ho_Chi_Minh"
    # The window starts a day back: the late slot began before now.
    assert repo.windows == [(NOW - LOOK_BACK, NOW + timedelta(days=7))]


def test_replan_moves_only_what_may_move() -> None:
    use_case, _ = setup(
        row(1, at(10, 45), at(11, 15), started=True),  # a timer ran on it: stays
        row(2, at(11, 30), at(12), SlotState.DUE),  # Due, not started: moves
        row(3, at(12, 0), at(12, 30), is_fixed=True),  # a Fixed task's slot: stays
        row(4, at(13), at(13, 30), SlotState.MISSED),  # not on the calendar: ignored
        fixed=[FixedTaskTime(at(14), at(15), False)],
        events=[CalendarEvent(at(16), at(17), False, False, False)],
    )

    result = ask(use_case)

    assert result.plan.kind is OverrunKind.REPLAN
    assert [(m.slot_id.int, m.new.start, m.new.end) for m in result.plan.moves] == [
        (2, at(11, 15), at(11, 45)),
    ]
    assert result.summary == (
        "Để làm tiếp Viết blog đến 10 giờ 35, nên dời 1 việc: Việc 2 sang 11 giờ 15."
    )


def test_english_summary() -> None:
    use_case, _ = setup()

    assert ask(use_case, lang=Language.EN).summary == (
        "Keep working on Viết blog until 10:35 am."
    )


def test_slot_of_someone_else_is_not_found() -> None:
    use_case, _ = setup()

    with pytest.raises(SlotNotFoundError):
        ask(use_case, slot_id=UUID(int=999))


def test_unknown_user() -> None:
    use_case, _ = setup()

    with pytest.raises(UserNotFoundError):
        ask(use_case, user=UUID(int=2))


@pytest.mark.parametrize(
    "late",
    [
        row(10, at(9), at(10), SlotState.DONE),
        row(10, at(9), at(10), SlotState.CANCELLED),
        row(10, at(9), at(10), SlotState.PROPOSED),
        row(10, at(9), at(10), SlotState.MISSED),
        row(10, at(9), at(10), SlotState.APPROVED, is_fixed=True),
    ],
)
def test_slots_that_cannot_run_over_are_409(late: SlotRow) -> None:
    repo = FakeOverrunRepository()
    repo.users[USER] = OverrunData(HOURS, None, (late,), (), ())
    use_case = PlanOverrun(repo, SETTINGS, now=lambda: NOW)

    with pytest.raises(SlotNotLateError):
        ask(use_case)


def test_the_port_has_no_write_method() -> None:
    from metanoia_ai.application.ports.overrun_repository import OverrunRepository

    public = {name for name in vars(OverrunRepository) if not name.startswith("_")}
    assert public == {"get_overrun_data"}
