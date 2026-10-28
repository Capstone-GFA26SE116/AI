"""The FR-10 fakes must behave like the real code, or use case tests prove nothing."""

import asyncio
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.llm_client import LLMClient, LLMError
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    QuotaRepository,
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.ports.schedule_repository import (
    NewProposal,
    NewSlot,
    PlanningData,
    ScheduleRepository,
)
from metanoia_ai.domain.models import WorkingHours
from tests.unit.application.fakes import (
    FakeLLMClient,
    FakeQuotaRepository,
    FakeScheduleRepository,
)

SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=SAIGON)
USER = uuid4()
OTHER_USER = uuid4()


def subscription(limit: int | None = 10, used: int = 0, price: int = 0) -> Subscription:
    return Subscription(uuid4(), price, limit, used)


def usage(sub: Subscription, charged: bool = True) -> UsageLogEntry:
    return UsageLogEntry(
        sub.subscription_id,
        AIFeature.SCHEDULE,
        "fake-model",
        100,
        50,
        Decimal("0.00003"),
        1200,
        succeeded=True,
        quota_charged=charged,
    )


def proposal(*slot_ids) -> NewProposal:
    start = NOW + timedelta(hours=1)
    slots = tuple(
        NewSlot(sid, uuid4(), None, start, start + timedelta(hours=1), 60, None, None)
        for sid in slot_ids
    )
    return NewProposal(uuid4(), True, None, "ok", (), slots)


def repos():
    quota = FakeQuotaRepository(NOW)
    return quota, FakeScheduleRepository(quota)


def test_fakes_match_the_ports():
    quota, schedule = repos()
    # Type check only: the fakes must have every method of the ports.
    checks: tuple[QuotaRepository, ScheduleRepository, LLMClient] = (
        quota,
        schedule,
        FakeLLMClient(),
    )
    assert all(checks)


# --- Quota ---


def test_no_subscription_returns_none():
    quota, _ = repos()
    assert asyncio.run(quota.get_subscription(USER, NOW)) is None


def test_newest_active_subscription_is_used():
    quota, _ = repos()
    old, new = subscription(limit=5), subscription(limit=100)
    quota.add_subscription(USER, old, NOW - timedelta(days=30))
    quota.add_subscription(USER, new, NOW - timedelta(days=1))
    assert asyncio.run(quota.get_subscription(USER, NOW)).subscription_id == (
        new.subscription_id
    )


def test_future_subscription_is_not_used_yet():
    quota, _ = repos()
    quota.add_subscription(USER, subscription(), NOW + timedelta(days=1))
    assert asyncio.run(quota.get_subscription(USER, NOW)) is None


def test_try_charge_stops_at_the_limit():
    quota, _ = repos()
    sub = subscription(limit=2, used=1)
    quota.add_subscription(USER, sub, NOW - timedelta(days=1))
    assert asyncio.run(quota.try_charge(sub)) is True
    assert asyncio.run(quota.try_charge(sub)) is False
    assert quota.calls_used[sub.subscription_id] == 2


def test_try_charge_without_limit_always_works():
    quota, _ = repos()
    sub = subscription(limit=None, used=999)
    quota.add_subscription(USER, sub, NOW - timedelta(days=1))
    assert asyncio.run(quota.try_charge(sub)) is True


def test_lose_next_charge_simulates_another_request():
    quota, _ = repos()
    sub = subscription()
    quota.add_subscription(USER, sub, NOW - timedelta(days=1))
    quota.lose_next_charge = True
    assert asyncio.run(quota.try_charge(sub)) is False
    assert asyncio.run(quota.try_charge(sub)) is True


# --- Schedule ---


def test_planning_data_is_per_user():
    _, schedule = repos()
    data = PlanningData(
        WorkingHours(SAIGON, time(9), time(17), False, 10),
        (),
        {},
        {},
        (),
        (),
        True,
        (),
    )
    schedule.planning[USER] = data
    assert asyncio.run(schedule.get_planning_data(USER, NOW, NOW)) == data
    with pytest.raises(KeyError):
        asyncio.run(schedule.get_planning_data(OTHER_USER, NOW, NOW))


def test_save_supersedes_the_old_pending_proposal():
    _, schedule = repos()
    old_slot, new_slot = uuid4(), uuid4()
    old, new = proposal(old_slot), proposal(new_slot)
    asyncio.run(schedule.save_proposal(USER, old))
    asyncio.run(schedule.save_proposal(USER, new))
    assert schedule.pending(USER) == [new]
    assert schedule.proposals[old.proposal_id][1] == "Superseded"
    assert schedule.slot_status == {old_slot: "Cancelled", new_slot: "Proposed"}


def test_save_keeps_other_users_proposals():
    _, schedule = repos()
    theirs = proposal(uuid4())
    asyncio.run(schedule.save_proposal(OTHER_USER, theirs))
    asyncio.run(schedule.save_proposal(USER, proposal(uuid4())))
    assert schedule.pending(OTHER_USER) == [theirs]


def test_transaction_rolls_back_proposals_and_quota():
    quota, schedule = repos()
    sub = subscription()
    quota.add_subscription(USER, sub, NOW - timedelta(days=1))
    old = proposal(uuid4())
    asyncio.run(schedule.save_proposal(USER, old))
    schedule.fail_on_save = True

    async def save_and_fail():
        async with schedule.transaction():
            await quota.try_charge(sub)
            await quota.log_usage(usage(sub))
            await schedule.save_proposal(USER, proposal(uuid4()))

    with pytest.raises(RuntimeError):
        asyncio.run(save_and_fail())
    assert schedule.pending(USER) == [old]  # old one is Pending again
    assert quota.calls_used[sub.subscription_id] == 0
    assert quota.logs == []


# --- LLM ---


def test_fake_llm_returns_answers_in_order_and_records_prompts():
    llm = FakeLLMClient('{"a": 1}', LLMError("timeout"), '{"b": 2}')
    first = asyncio.run(llm.generate_json("p1", {"type": "object"}))
    assert first.text == '{"a": 1}'
    assert first.input_tokens == 100
    with pytest.raises(LLMError):
        asyncio.run(llm.generate_json("p2", {}))
    assert asyncio.run(llm.generate_json("p3", {})).text == '{"b": 2}'
    assert llm.prompts == ["p1", "p2", "p3"]


def test_fake_llm_fails_when_out_of_answers():
    with pytest.raises(LLMError):
        asyncio.run(FakeLLMClient().generate_json("p", {}))
