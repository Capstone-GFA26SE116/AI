"""FR-24 domain: give draft tasks to members, with a code-written reason (T1, T2)."""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.domain.assignee_ranking import Candidate, GroupRole
from metanoia_ai.domain.group_plan import PlanTask, assign_tasks
from metanoia_ai.domain.models import Language, TimeRange

VN = ZoneInfo("Asia/Ho_Chi_Minh")
A, B = UUID(int=1), UUID(int=2)
CODING = UUID(int=50)


def at(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=VN)


def task(key: str, minutes: int, day: int = 25) -> PlanTask:
    return PlanTask(key, CODING, minutes, at(day, 23))


def pool(free: dict[UUID, int], times: dict[UUID, tuple[TimeRange, ...]] | None = None):
    """Each member's free minutes, minus the draft work already given to them."""
    times = times or {}

    def candidates_for(t: PlanTask, given):
        return [
            Candidate(
                user_id=u,
                role=GroupRole.MEMBER,
                is_current_assignee=False,
                free_minutes=max(minutes - given.get(u, 0), 0),
                busy_at_fixed_time=False,
                group_work_minutes=0,
                needed_minutes=t.estimated_minutes,
                free_times=times.get(u, ()),
                time_zone=VN,
            )
            for u, minutes in free.items()
        ]

    return candidates_for


def test_work_is_spread_instead_of_piled_on_one_member():
    tasks = [task("m1-t1", 240), task("m1-t2", 240), task("m1-t3", 240)]
    result = assign_tasks(tasks, pool({A: 600, B: 500}), Language.VI)
    # A first (more free), then B (A has 360 left < B's 500), then A (360 vs 260).
    assert [r.user_id for r in result] == [A, B, A]
    assert all(r.fits for r in result)


def test_tasks_go_in_deadline_order_but_come_back_in_draft_order():
    tasks = [task("late", 300, day=27), task("soon", 300, day=24)]
    result = assign_tasks(tasks, pool({A: 400, B: 350}), Language.EN)
    assert [r.task_key for r in result] == ["late", "soon"]
    # "soon" is handled first and takes A; "late" then goes to B.
    assert [r.user_id for r in result] == [B, A]


def test_nobody_fits_gives_the_most_free_member_and_says_so():
    result = assign_tasks([task("m1-t1", 900)], pool({A: 300, B: 200}), Language.VI)
    (only,) = result
    assert only.user_id == A and only.fits is False
    assert only.reason == (
        "Không ai đủ giờ trước hạn Thứ Tư 25/11, 23:00; thành viên này còn "
        "nhiều giờ rảnh nhất (5 giờ)."
    )


def test_reason_has_free_hours_deadline_and_first_open_time():
    open_time = (TimeRange(at(24, 8), at(24, 12)),)
    vi = assign_tasks([task("k", 60)], pool({A: 570}, {A: open_time}), Language.VI)
    assert vi[0].reason == (
        "Còn 9,5 giờ rảnh trước hạn Thứ Tư 25/11, 23:00; "
        "lịch trống Thứ Ba 24/11, 08:00–12:00."
    )
    en = assign_tasks([task("k", 60)], pool({A: 60}), Language.EN)
    assert en[0].reason == "1 hour free before Wed 25/11, 23:00."


def test_no_members_means_no_assignment():
    result = assign_tasks([task("k", 60)], pool({}), Language.VI)
    assert result[0].user_id is None and result[0].reason is None
