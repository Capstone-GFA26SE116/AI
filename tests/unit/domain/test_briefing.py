from datetime import UTC, datetime, time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.briefing import (
    BiasRule,
    Candidate,
    DeadlineRisk,
    FixedEntry,
    GoogleEntry,
    OpenSubtask,
    Reason,
    ReasonCode,
    RiskTask,
    ScheduleKind,
    SlotEntry,
    SlotStatus,
    at_risk_tasks,
    deadline_risks,
    falls_on,
    free_minutes_before,
    needed_minutes,
    productive_hint,
    risk_free_ranges,
    select_top,
    today_range,
    todays_schedule,
)
from metanoia_ai.domain.free_time import FixedTaskTime
from metanoia_ai.domain.models import (
    ActualTimeSource,
    FocusSessionRecord,
    TimeRange,
    WorkingHours,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """October 2026 in Saigon time."""
    return datetime(2026, 10, day, hour, minute, tzinfo=SAIGON)


NOW = at(17, 8)  # Saturday 17 Oct, 08:00 in Saigon


def slot(start, end, status=SlotStatus.APPROVED, title="Viết landing page"):
    return SlotEntry(uuid4(), title, start, end, status)


def fixed(start, end, *, skipped=False, title="Họp khách"):
    return FixedEntry(uuid4(), title, start, end, skipped)


def google(start, end, *, all_day=False, cancelled=False, metanoia=False, title="Ev"):
    return GoogleEntry(title, start, end, all_day, cancelled, metanoia)


def schedule(slots=(), fixed_tasks=(), events=(), now=NOW):
    return todays_schedule(slots, fixed_tasks, events, now, SAIGON)


# --- today_range ---


def test_today_is_midnight_to_midnight_on_the_user_clock():
    # 20:00 UTC on 16 Oct is already 03:00 on 17 Oct in Saigon.
    now = datetime(2026, 10, 16, 20, tzinfo=UTC)
    assert today_range(now, SAIGON) == TimeRange(at(17, 0), at(18, 0))


def test_falls_on_uses_half_open_range():
    day = today_range(NOW, SAIGON)
    assert not falls_on(at(16, 23), at(17, 0), day)  # ends at midnight: yesterday
    assert falls_on(at(16, 23), at(17, 1), day)  # crosses midnight
    assert not falls_on(at(18, 0), at(18, 1), day)  # tomorrow
    assert falls_on(at(17, 9), at(17, 9), day)  # zero-length counts by its start
    assert not falls_on(at(18, 0), at(18, 0), day)


# --- T1: today's schedule in the user's time zone ---


def test_t1_slot_late_utc_yesterday_is_today_in_saigon():
    # 23:30 UTC on 16 Oct = 06:30 on 17 Oct in Saigon.
    start = datetime(2026, 10, 16, 23, 30, tzinfo=UTC)
    end = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)
    result = schedule(slots=[slot(start, end)])
    assert len(result.items) == 1
    item = result.items[0]
    assert item.start == at(17, 6, 30)
    assert item.start.tzinfo == SAIGON  # given back in the user's zone
    assert item.end.utcoffset() == at(17, 0).utcoffset()


def test_items_from_other_days_are_left_out():
    result = schedule(
        slots=[slot(at(16, 9), at(16, 10)), slot(at(18, 9), at(18, 10))],
        fixed_tasks=[fixed(at(18, 14), at(18, 15))],
        events=[google(at(16, 20), at(16, 21))],
    )
    assert result.items == ()


def test_all_kinds_sorted_by_start():
    s = slot(at(17, 9), at(17, 10, 30))
    f = fixed(at(17, 14), at(17, 15))
    g = google(at(17, 11), at(17, 12), title="Sinh nhật bạn")
    result = schedule(slots=[s], fixed_tasks=[f], events=[g])
    assert [i.kind for i in result.items] == [
        ScheduleKind.SLOT,
        ScheduleKind.GOOGLE,
        ScheduleKind.FIXED,
    ]
    assert result.items[0].task_id == s.task_id
    assert result.items[0].status is SlotStatus.APPROVED
    assert result.items[1].task_id is None
    assert result.items[1].title == "Sinh nhật bạn"
    assert result.items[2].task_id == f.task_id


def test_done_slot_is_shown():
    result = schedule(slots=[slot(at(17, 7), at(17, 7, 45), SlotStatus.DONE)])
    assert result.items[0].status is SlotStatus.DONE


# --- T17 (domain part): Proposed slots are not shown, only flagged ---


def test_proposed_slot_is_not_shown_but_flagged():
    result = schedule(slots=[slot(at(17, 15), at(17, 16), SlotStatus.PROPOSED)])
    assert result.items == ()
    assert result.has_pending_proposal is True


@pytest.mark.parametrize(
    "start, end",
    [
        pytest.param(at(17, 6), at(17, 7), id="already-over-today"),
        pytest.param(at(18, 9), at(18, 10), id="tomorrow"),
    ],
)
def test_proposed_slot_in_the_past_or_other_day_is_not_pending(start, end):
    result = schedule(slots=[slot(start, end, SlotStatus.PROPOSED)])
    assert result.has_pending_proposal is False


def test_no_slots_means_no_pending_proposal():
    assert schedule().has_pending_proposal is False


# --- #3: what is left out ---


def test_skipped_fixed_task_is_not_shown():
    assert (
        schedule(fixed_tasks=[fixed(at(17, 9), at(17, 10), skipped=True)]).items == ()
    )


@pytest.mark.parametrize("flag", ["cancelled", "metanoia"])
def test_cancelled_and_metanoia_google_events_are_not_shown(flag):
    event = google(at(17, 9), at(17, 10), **{flag: True})
    result = schedule(events=[event])
    assert result.items == ()
    assert result.all_day == ()


def test_all_day_event_goes_to_all_day_list():
    # Stored as local midnight to midnight (17:00 UTC → 17:00 UTC next day).
    start = datetime(2026, 10, 16, 17, tzinfo=UTC)
    end = datetime(2026, 10, 17, 17, tzinfo=UTC)
    result = schedule(events=[google(start, end, all_day=True, title="Nghỉ lễ")])
    assert result.items == ()
    assert result.all_day == ("Nghỉ lễ",)


def test_all_day_event_of_yesterday_is_left_out():
    start = datetime(2026, 10, 15, 17, tzinfo=UTC)
    end = datetime(2026, 10, 16, 17, tzinfo=UTC)  # ends at 00:00 today
    assert schedule(events=[google(start, end, all_day=True)]).all_day == ()


# --- minutes a task still needs ---


def test_task_without_subtasks_is_scaled_by_k():
    assert needed_minutes(60, [], False, [], Decimal("1.3"), NOW) == 78


def test_free_plan_is_not_scaled():
    assert needed_minutes(60, [], False, [], None, NOW) == 60


def test_task_with_subtasks_sums_the_open_ones():
    a, b = OpenSubtask(uuid4(), 30), OpenSubtask(uuid4(), 40)
    # A session of 25' on subtask a: 30 − 25 → at least 30 left (FR-10 rule).
    session = FocusSessionRecord(
        at(17, 7), at(17, 7, 25), ActualTimeSource.TIMER, subtask_id=a.subtask_id
    )
    assert needed_minutes(999, [a, b], True, [session], None, NOW) == 30 + 40


def test_task_with_all_subtasks_closed_needs_nothing():
    assert needed_minutes(120, [], True, [], Decimal("1.3"), NOW) == 0


def test_remaining_minutes_from_the_user_is_used_as_is():
    session = FocusSessionRecord(
        at(17, 7), at(17, 7, 30), ActualTimeSource.TIMER, remaining_minutes=45
    )
    assert needed_minutes(120, [], False, [session], Decimal("1.5"), NOW) == 45


# --- deadline risk, counted together ---

# Every day 09:00-17:00 (480 free minutes a day), weekends too.
HOURS = WorkingHours(SAIGON, time(9), time(17), True, 10)
WINDOW = 14


def free(fixed_tasks=(), now=NOW):
    return risk_free_ranges(HOURS, now, WINDOW, fixed_tasks, [], round_minutes=5)


def risks(tasks, now=NOW):
    return deadline_risks(tasks, free(now=now), now, WINDOW)


def task(deadline, minutes):
    return RiskTask(uuid4(), deadline, minutes)


def test_free_minutes_before_cuts_at_the_deadline():
    ranges = free()
    assert free_minutes_before(ranges, at(17, 12)) == 180
    assert free_minutes_before(ranges, at(18, 17)) == 960
    assert free_minutes_before(ranges, at(17, 8)) == 0


def test_one_task_that_fits_is_safe():
    [result] = risks([task(at(17, 17), 480)])
    assert result.available_minutes == 480
    assert not result.is_at_risk
    assert result.missing_minutes == 0


def test_t2_three_tasks_same_deadline_counted_together():
    # Each fits alone in today's 480 minutes; together they do not.
    big, mid, small = (
        task(at(17, 17), 400),
        task(at(17, 17), 350),
        task(at(17, 17), 300),
    )
    result = risks([small, big, mid])
    assert [r.task_id for r in result] == [big.task_id, mid.task_id, small.task_id]
    assert [r.is_at_risk for r in result] == [False, True, True]
    assert [r.available_minutes for r in result] == [480, 80, 0]
    assert [r.missing_minutes for r in result] == [0, 270, 300]


def test_t3_deadline_after_the_window_is_safe_and_not_counted():
    far = task(datetime(2026, 11, 6, 9, tzinfo=SAIGON), 10_000)  # 20 days later
    near = task(at(17, 17), 400)
    result = risks([far, near])
    assert [r.task_id for r in result] == [near.task_id]
    assert not result[0].is_at_risk


def test_t9_approved_slots_are_not_busy():
    # Free time only comes from Fixed tasks and Google events: an approved slot
    # of the task being checked must not make its own deadline look at risk.
    assert free_minutes_before(free(), at(17, 17)) == 480
    meeting = FixedTaskTime(at(17, 10), at(17, 11), is_skipped=False)
    assert free_minutes_before(free([meeting]), at(17, 17)) == 420


def test_t10_overdue_task_takes_free_time_first():
    overdue = task(at(16, 17), 480)
    tomorrow = task(at(18, 17), 600)  # 960 free minutes until then: fits alone
    result = risks([tomorrow, overdue])
    assert [r.task_id for r in result] == [overdue.task_id, tomorrow.task_id]
    assert result[0].is_overdue and not result[0].is_at_risk
    assert result[1].available_minutes == 480
    assert result[1].is_at_risk
    assert result[1].missing_minutes == 120


def test_overdue_tasks_are_ordered_by_deadline():
    older, newer = task(at(15, 9), 60), task(at(16, 9), 60)
    assert [r.task_id for r in risks([newer, older])] == [older.task_id, newer.task_id]


def test_task_needing_nothing_is_left_out():
    assert risks([task(at(17, 17), 0)]) == []


# --- top tasks and reason codes ---

RULE = BiasRule(min_k=Decimal("1.1"), min_samples=3)


def candidate(deadline, minutes=60, *, in_progress=False, k=None, samples=0):
    return Candidate(
        task_id=uuid4(),
        title="T",
        project_name="P",
        domain_name="Coding",
        deadline=deadline,
        needed_minutes=minutes,
        in_progress=in_progress,
        k_bias=k,
        sample_count=samples,
    )


def risk_of(c, available, *, overdue=False):
    return DeadlineRisk(c.task_id, c.deadline, c.needed_minutes, available, overdue)


def top(candidates, risk_list=(), count=3, now=NOW):
    return select_top(candidates, risk_list, now, SAIGON, count, RULE)


def codes(top_task):
    return [r.code for r in top_task.reasons]


def test_t4_order_is_overdue_then_at_risk_then_in_progress():
    overdue = candidate(at(16, 17))
    at_risk = candidate(at(17, 17), 300)
    working = candidate(at(20, 17), in_progress=True)
    soonest = candidate(at(17, 12))  # nearest deadline, but nothing else
    risk_list = [risk_of(overdue, 0, overdue=True), risk_of(at_risk, 100)]
    result = top([soonest, working, at_risk, overdue], risk_list)
    assert [t.candidate.task_id for t in result] == [
        overdue.task_id,
        at_risk.task_id,
        working.task_id,
    ]
    assert codes(result[0])[0] is ReasonCode.OVERDUE
    assert codes(result[1])[0] is ReasonCode.AT_RISK
    assert codes(result[2])[0] is ReasonCode.IN_PROGRESS


def test_without_special_tasks_nearest_deadline_wins():
    later, sooner = candidate(at(20, 17)), candidate(at(18, 17))
    result = top([later, sooner], count=1)
    assert [t.candidate.task_id for t in result] == [sooner.task_id]


def test_t5_tie_on_deadline_longer_task_first():
    short, long = candidate(at(18, 17), 60), candidate(at(18, 17), 180)
    result = top([short, long])
    assert [t.candidate.task_id for t in result] == [long.task_id, short.task_id]


def test_top_count_is_respected_and_empty_is_fine():
    many = [candidate(at(18 + i, 17)) for i in range(5)]
    assert len(top(many)) == 3
    assert top([]) == []


def test_task_needing_nothing_is_not_in_top():
    assert top([candidate(at(18, 17), 0)]) == []


def test_t6_default_k_without_samples_gives_no_bias_reason():
    result = top([candidate(at(18, 17), k=Decimal("1.3"), samples=0)])
    assert ReasonCode.BIAS not in codes(result[0])


@pytest.mark.parametrize(
    "k, samples",
    [
        pytest.param(Decimal("1.05"), 10, id="k-below-min"),
        pytest.param(Decimal("1.4"), 2, id="too-few-samples"),
        pytest.param(None, 0, id="no-profile"),
    ],
)
def test_no_bias_reason_below_the_rule(k, samples):
    result = top([candidate(at(18, 17), k=k, samples=samples)])
    assert ReasonCode.BIAS not in codes(result[0])


def test_t7_learned_k_gives_bias_reason():
    result = top([candidate(at(18, 17), k=Decimal("1.4"), samples=5)])
    assert result[0].reasons == (
        Reason(ReasonCode.DEADLINE_SOON, days=1),
        Reason(ReasonCode.BIAS, extra_percent=40, domain_name="Coding"),
    )


def test_bias_percent_is_rounded_half_up():
    result = top([candidate(at(18, 17), k=Decimal("1.125"), samples=3)])
    assert result[0].reasons[-1].extra_percent == 13


def test_t11_free_plan_minutes_not_scaled_but_bias_still_shown():
    # Free plan: K is not applied to the minutes (k = None in needed_minutes)...
    minutes = needed_minutes(60, [], False, [], None, NOW)
    assert minutes == 60
    # ...but the user's learned K is still shown as a hint.
    c = candidate(at(18, 17), minutes, k=Decimal("1.5"), samples=4)
    result = top([c])
    assert result[0].candidate.needed_minutes == 60
    assert result[0].reasons[-1] == Reason(
        ReasonCode.BIAS, extra_percent=50, domain_name="Coding"
    )


def test_at_most_two_reasons_in_priority_order():
    c = candidate(at(17, 17), 300, in_progress=True, k=Decimal("1.5"), samples=9)
    result = top([c], [risk_of(c, 120)])
    assert result[0].reasons == (
        Reason(ReasonCode.AT_RISK, needed_minutes=300, available_minutes=120),
        Reason(ReasonCode.IN_PROGRESS, needed_minutes=300),
    )


def test_overdue_reason_counts_calendar_days_and_has_no_deadline_soon():
    c = candidate(at(15, 23))  # 2 days ago on the user's clock
    result = top([c], [risk_of(c, 0, overdue=True)])
    assert result[0].reasons == (Reason(ReasonCode.OVERDUE, days=2),)


def test_deadline_today_is_zero_days():
    result = top([candidate(at(17, 23))])
    assert result[0].reasons == (Reason(ReasonCode.DEADLINE_SOON, days=0),)


def test_at_risk_list_sorted_by_deadline_and_limited():
    a, b, c = candidate(at(19, 17)), candidate(at(18, 17)), candidate(at(20, 17))
    safe = candidate(at(17, 17))
    risk_list = [risk_of(a, 0), risk_of(b, 0), risk_of(c, 0), risk_of(safe, 60)]
    result = at_risk_tasks(risk_list, limit=2)
    assert [r.task_id for r in result] == [b.task_id, a.task_id]


# --- T12: productive hours left today ---

# Onboarding "afternoon" + "evening": 14-21h score 0.8, the rest 0.3.
SCORES = {h: Decimal("0.8") if 14 <= h <= 21 else Decimal("0.3") for h in range(24)}
WORK_8_TO_22 = WorkingHours(SAIGON, time(8), time(22), True, 10)


AFTERNOON_NOW = at(17, 15, 10)


def hint(scores=SCORES, *, answered=True, hours=WORK_8_TO_22, now=AFTERNOON_NOW, n=3):
    return productive_hint(scores, answered, hours, now, n)


def test_t12_keeps_hours_left_today_and_joins_them():
    # Top 3 = 14, 15, 16 (ties: earlier hour). At 15:10, 14h is over.
    assert hint() == [(15, 17)]


def test_t12_not_onboarded_gives_none():
    assert hint(answered=False) is None


def test_separate_hours_make_separate_ranges():
    scores = {**SCORES, 9: Decimal("0.9"), 20: Decimal("0.95")}
    # Top 3 = 20, 9, then 14. At 08:00 all are ahead.
    assert hint(scores, now=at(17, 8)) == [(9, 10), (14, 15), (20, 21)]


def test_current_hour_still_counts():
    assert hint(now=at(17, 16, 59)) == [(16, 17)]


def test_nothing_left_today_gives_none():
    assert hint(now=at(17, 17)) is None


def test_hours_outside_working_hours_are_dropped():
    office = WorkingHours(SAIGON, time(8), time(15), True, 10)
    assert hint(hours=office, now=at(17, 8)) == [(14, 15)]


def test_weekend_off_gives_none():
    weekdays = WorkingHours(SAIGON, time(8), time(22), False, 10)
    assert hint(hours=weekdays, now=at(17, 8)) is None  # 17 Oct is a Saturday
    assert hint(hours=weekdays, now=at(19, 8)) == [(14, 17)]  # Monday


def test_no_scores_gives_none():
    assert hint({}) is None
