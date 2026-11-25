"""FR-25 domain: parts to place and texts (T1)."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.group_slot_plan import (
    MAX_REASON_CHARS,
    GroupPart,
    parts_to_place,
    slot_reason,
    summary_text,
)
from metanoia_ai.domain.models import Language

VN = ZoneInfo("Asia/Ho_Chi_Minh")
ME, OTHER, TASK = UUID(int=1), UUID(int=2), UUID(int=100)
DEADLINE = datetime(2026, 11, 27, 17, tzinfo=VN)


def part(n, assignee=ME, *, open_=True, est=60, booked=0, order=None):
    return GroupPart(UUID(int=n), f"T: S{n}", assignee, open_, est, booked, order)


def test_only_my_open_parts_in_order_minus_booked_minutes():
    todo = parts_to_place(
        TASK,
        [
            part(3, order=2, booked=20),  # 40 left
            part(4, OTHER, order=0),  # someone else's
            part(5, open_=False, order=1),  # done
            part(6, order=1),
            part(7, order=3, booked=60),  # already fully booked
        ],
        ME,
        DEADLINE,
    )
    assert [(u.subtask_id.int, u.minutes) for u in todo.units] == [(6, 60), (3, 40)]
    assert todo.mine == 3
    assert all(u.k_bias_used is None and u.deadline == DEADLINE for u in todo.units)


def test_parts_without_estimate_are_reported():
    todo = parts_to_place(TASK, [part(3, est=None), part(4, est=0)], ME, DEADLINE)
    assert todo.units == ()
    assert [p.subtask_id.int for p in todo.no_estimate] == [3, 4]
    assert todo.mine == 2


def test_nothing_given_to_me():
    todo = parts_to_place(TASK, [part(3, OTHER), part(4, None)], ME, DEADLINE)
    assert todo.mine == 0 and todo.units == ()


def test_reason_names_the_deadline_and_fits_the_backend_limit():
    vi = slot_reason(DEADLINE, VN, Language.VI)
    assert vi == "Khoảng trống trong lịch của bạn, xong trước hạn Thứ Sáu 27/11, 17:00."
    assert "Fri 27/11, 17:00" in slot_reason(DEADLINE, VN, Language.EN)
    assert "sớm nhất" in slot_reason(None, VN, Language.VI)
    assert len(vi) <= MAX_REASON_CHARS


def test_summary():
    assert (
        summary_text("Báo cáo", 2, Language.VI) == "Mình gợi ý 2 khung giờ cho Báo cáo."
    )
    assert summary_text("Report", 0, Language.EN) == "No free time found for Report."


def test_due_at_prefers_the_task_deadline():
    from datetime import date, time

    from metanoia_ai.domain.group_slot_plan import due_at
    from metanoia_ai.domain.models import WorkingHours

    hours = WorkingHours(VN, time(8), time(17), False, 15)
    assert due_at(DEADLINE, date(2026, 12, 1), hours) == DEADLINE
    assert due_at(None, date(2026, 12, 1), hours) == datetime(
        2026, 12, 1, 17, tzinfo=VN
    )
    assert due_at(None, None, hours) is None
