from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.briefing import (
    FixedEntry,
    GoogleEntry,
    ScheduleKind,
    SlotEntry,
    SlotStatus,
    falls_on,
    today_range,
    todays_schedule,
)
from metanoia_ai.domain.models import TimeRange

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
