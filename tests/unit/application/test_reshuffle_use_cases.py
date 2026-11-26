"""FR-22d use cases with a fake repository. T9."""

import asyncio
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.reshuffle_repository import (
    CalendarData,
    CalendarSlot,
    FixedTask,
    GoogleEvent,
)
from metanoia_ai.application.use_cases.plan_overrun import SlotNotFoundError
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.reshuffle import (
    BlockInvalidError,
    CatchUp,
    InsertBlock,
    PostponeSlot,
    ReshuffleSettings,
    SlotNotMovableError,
    to_calendar,
)
from metanoia_ai.domain.free_time import DailyBreak
from metanoia_ai.domain.models import Language, TimeRange, WorkingHours
from metanoia_ai.domain.overrun_plan import SlotState
from metanoia_ai.domain.reshuffle import Choice, ConflictKind

VN = ZoneInfo("Asia/Ho_Chi_Minh")
HOURS = WorkingHours(VN, time(8), time(18), False, 15)
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=VN)  # a Tuesday
USER = UUID(int=1)
PROJECT, DOMAIN = UUID(int=500), UUID(int=600)
SETTINGS = ReshuffleSettings(5, 7, (DailyBreak(time(12), time(13)),))


def at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=VN) + timedelta(days=days)


def row(
    n: int, start: datetime, end: datetime, state=SlotState.APPROVED, **kw
) -> CalendarSlot:
    values = {
        "slot_id": UUID(int=n),
        "title": f"Việc {n}",
        "start": start,
        "end": end,
        "state": state,
        "is_fixed": False,
        "deadline": at(23, days=3),
        "started": False,
        "task_id": UUID(int=100 + n),
    }
    values.update(kw)
    return CalendarSlot(**values)


class FakeRepo:
    """Read only, like the real one (AC6): there is no write method to call."""

    def __init__(self, data: CalendarData | None, user: UUID = USER) -> None:
        self.data = data
        self.user = user
        self.calls: list[tuple] = []

    async def get_calendar(self, user_id, window_start, window_end, slot_id=None):
        self.calls.append((user_id, window_start, window_end, slot_id))
        if self.data is None or user_id != self.user:
            return None
        target = next((s for s in self.data.slots if s.slot_id == slot_id), None)
        return CalendarData(
            self.data.hours,
            self.data.slots,
            self.data.fixed_tasks,
            self.data.events,
            target,
            self.data.default_project_id,
            self.data.recent_domain_id,
        )


def data(slots=(), fixed=(), events=()) -> CalendarData:
    return CalendarData(
        HOURS, tuple(slots), tuple(fixed), tuple(events), None, PROJECT, DOMAIN
    )


def use(cls, d: CalendarData | None, now=NOW):
    repo = FakeRepo(d)
    return cls(repo, SETTINGS, lambda: now), repo


# --- to_calendar: what moves, what is in the way ---


def test_to_calendar_sorts_slots() -> None:
    d = data(
        slots=[
            row(1, at(9), at(10)),
            row(2, at(10), at(11), SlotState.DUE),
            row(3, at(11), at(12), started=True),
            row(4, at(13), at(14), SlotState.IN_PROGRESS),
            row(5, at(14), at(15), is_group=True),
            row(6, at(15), at(16), SlotState.DONE),
            row(7, at(16), at(17), SlotState.PROPOSED),
            row(8, at(16), at(17), SlotState.CANCELLED),
        ],
        fixed=[
            FixedTask(UUID(int=9), "Họp", at(8), at(9), False, True),
            FixedTask(UUID(int=10), "Họp hủy", at(8), at(9), True, False),
        ],
        events=[
            GoogleEvent("Ăn tối", at(17), at(18), False, False, False),
            GoogleEvent("Slot Metanoia", at(9), at(10), False, False, True),
            GoogleEvent("Sinh nhật", at(0), at(23), True, False, False),
        ],
    )
    cal = to_calendar(d, [])

    assert [s.slot_id.int for s in cal.movable] == [1, 2]
    assert [(b.kind, b.title) for b in cal.blockers] == [
        (ConflictKind.STARTED_SLOT, "Việc 3"),
        (ConflictKind.STARTED_SLOT, "Việc 4"),
        (ConflictKind.GROUP_SLOT, "Việc 5"),
        (ConflictKind.FIXED_TASK, "Họp"),
        (ConflictKind.CALENDAR_EVENT, "Ăn tối"),
    ]
    assert cal.other_busy == (TimeRange(at(15), at(16)),)


# --- InsertBlock ---


def test_insert_moves_the_covered_slot_and_suggests_project_and_domain() -> None:
    uc, repo = use(
        InsertBlock, data([row(1, at(14), at(15)), row(2, at(15, 15), at(16))])
    )
    result = asyncio.run(uc.execute(USER, "Sửa lỗi gấp", at(14), at(15), Language.VI))

    assert [(m.slot_id.int, m.new.start) for m in result.plan.moves] == [
        (1, at(16)),  # after slot 2, which stays (plan #3)
    ]
    assert (result.project_id, result.domain_id, result.time_zone) == (
        PROJECT,
        DOMAIN,
        "Asia/Ho_Chi_Minh",
    )
    assert result.summary.startswith(
        "Thêm Sửa lỗi gấp từ 14 giờ đến 15 giờ và dời 1 việc"
    )
    assert repo.calls[0][0] == USER


def test_insert_with_client_meeting_suggests_other_time() -> None:
    meeting = FixedTask(UUID(int=9), "Họp khách", at(14, 30), at(15, 30), False, True)
    uc, _ = use(InsertBlock, data(fixed=[meeting]))
    result = asyncio.run(uc.execute(USER, "Gấp", at(14), at(16), Language.VI))

    assert result.plan.recommendation is Choice.OTHER_TIME
    assert result.plan.other_time == TimeRange(at(9), at(11))
    assert result.plan.skip_task_ids == (UUID(int=9),)
    assert "trùng với Họp khách (việc với khách)" in result.summary


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (at(14), at(14)),  # empty
        (at(8, 50), at(10)),  # more than 5' ago
        (at(9), at(21, 1)),  # longer than 12 h
        (at(9, days=7), at(10, days=7)),  # after the 7-day window
    ],
)
def test_insert_rejects_bad_blocks(start: datetime, end: datetime) -> None:
    uc, repo = use(InsertBlock, data())
    with pytest.raises(BlockInvalidError):
        asyncio.run(uc.execute(USER, "Gấp", start, end, Language.VI))
    assert repo.calls == []


def test_insert_accepts_a_start_a_few_minutes_ago() -> None:
    uc, _ = use(InsertBlock, data())
    result = asyncio.run(uc.execute(USER, "Gấp", at(8, 56), at(10), Language.VI))
    assert result.plan.block.start == at(8, 56)


def test_unknown_user() -> None:
    uc, _ = use(InsertBlock, None)
    with pytest.raises(UserNotFoundError):
        asyncio.run(uc.execute(USER, "Gấp", at(14), at(15), Language.VI))


# --- PostponeSlot ---


def test_postpone_moves_to_tomorrow() -> None:
    uc, repo = use(PostponeSlot, data([row(1, at(14), at(15))]))
    result = asyncio.run(uc.execute(USER, UUID(int=1), Language.VI))

    assert result.move is not None and result.move.new == TimeRange(
        at(8, days=1), at(9, days=1)
    )
    assert result.summary == "Dời Việc 1 sang 8 giờ ngày mai nhé?"
    assert repo.calls[0][3] == UUID(int=1)


def test_postpone_unknown_slot_is_404() -> None:
    uc, _ = use(PostponeSlot, data())
    with pytest.raises(SlotNotFoundError):
        asyncio.run(uc.execute(USER, UUID(int=1), Language.VI))


@pytest.mark.parametrize(
    "kw",
    [
        {"started": True},
        {"is_fixed": True},
        {"is_group": True},
        {"state": SlotState.DONE},
        {"state": SlotState.MISSED},
    ],
)
def test_postpone_refuses_slots_that_do_not_move(kw: dict) -> None:
    uc, _ = use(PostponeSlot, data([row(1, at(14), at(15), **kw)]))
    with pytest.raises(SlotNotMovableError):
        asyncio.run(uc.execute(USER, UUID(int=1), Language.VI))


def test_postpone_without_room_before_the_deadline() -> None:
    uc, _ = use(PostponeSlot, data([row(1, at(14), at(15), deadline=at(17))]))
    result = asyncio.run(uc.execute(USER, UUID(int=1), Language.EN))

    assert result.move is None
    assert result.summary.startswith("There is no free time")


# --- CatchUp ---


def test_catch_up_picks_missed_and_past_unstarted_slots() -> None:
    now = at(13, 2)
    slots = [
        row(1, at(8), at(8, 45), SlotState.MISSED),
        row(2, at(10), at(11), SlotState.APPROVED),  # past, never started
        row(3, at(9), at(10), SlotState.MISSED, task_finished=True),
        row(4, at(11), at(12), SlotState.APPROVED, started=True),
        row(5, at(9, days=-3), at(10, days=-3), SlotState.MISSED),  # too old
        row(6, at(14), at(15)),  # still to come: stays
    ]
    uc, _ = use(CatchUp, data(slots), now)
    result = asyncio.run(uc.execute(USER, Language.VI))

    assert [
        (m.move.slot_id.int, m.move.new.start, m.task_id) for m in result.moves
    ] == [
        (1, at(13, 5), UUID(int=101)),
        (2, at(15), UUID(int=102)),
    ]
    assert result.summary.startswith("Có 2 việc bị lỡ.")


def test_catch_up_with_nothing_missed() -> None:
    uc, _ = use(CatchUp, data([row(1, at(14), at(15))]))
    result = asyncio.run(uc.execute(USER, Language.VI))

    assert (result.moves, result.not_placed) == ((), ())
    assert result.summary == "Không có việc nào bị lỡ, lịch của bạn vẫn ổn."
