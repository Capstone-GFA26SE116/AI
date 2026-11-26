"""FR-10g: slot reasons written by code (vi / en), and the use case without quota."""

import asyncio
import itertools
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from metanoia_ai.application.reason_writer import (
    FORBIDDEN_WORDS,
    TemplateReasonWriter,
)
from metanoia_ai.application.use_cases.propose_schedule import ProposeSchedule
from metanoia_ai.domain.forbidden_terms import has_internal_name
from metanoia_ai.domain.models import BlockedBy, Language, Placement, SplitReason
from metanoia_ai.domain.schedule_reasons import (
    MAX_REASON_LENGTH,
    template_reason,
    template_reasons,
)
from metanoia_ai.domain.schedule_text import SlotFacts
from tests.unit.application.test_propose_schedule import (
    NOW,
    USER,
    World,
    planning,
    task,
)

VI, EN = Language.VI, Language.EN


def facts(**kw) -> SlotFacts:
    values = {
        "task_title": "Viết báo cáo",
        "days_to_deadline": 2,
        "in_productive_hour": False,
        "extra_percent": 30,
        "extra_applied": True,
        "part_index": 1,
        "part_count": 1,
        "placement": Placement.EARLIEST_FREE,
        "earlier_blocked_by": BlockedBy.NONE,
        "priority_rank": 1,
        "priority_total": 1,
        "estimate_minutes": 60,
        "planned_minutes": 78,
        "spare_hours": 50,
        "split_reason": None,
    }
    values.update(kw)
    return SlotFacts(**values)


def test_earliest_free_with_extra_time() -> None:
    assert template_reason(facts(), VI) == (
        "Đây là khoảng trống sớm nhất vừa đủ cho Viết báo cáo. "
        "Dự kiến 60 phút, mình giữ 78 phút vì bạn thường cần thêm khoảng 30%."
    )
    assert template_reason(facts(), EN) == (
        "This is the earliest free time that fits Viết báo cáo. "
        "Planned 60 min; I kept 78 because you usually need about 30% more."
    )


def test_extra_time_only_on_the_first_part() -> None:
    later = template_reason(facts(part_index=2, part_count=3), VI)
    assert "Dự kiến" not in later
    assert later.startswith("Làm tiếp phần còn lại của Viết báo cáo.")


def test_free_plan_gives_a_tip_and_faster_users_are_told() -> None:
    assert "Mẹo: bạn thường cần thêm khoảng 30%" in template_reason(
        facts(extra_applied=False), VI
    )
    assert "nhanh hơn khoảng 20%" in template_reason(facts(extra_percent=-20), VI)


def test_why_this_time() -> None:
    meeting = template_reason(
        facts(earlier_blocked_by=BlockedBy.MEETING, extra_percent=0), VI
    )
    assert meeting.startswith("Trước giờ này bạn có cuộc họp")
    focus = template_reason(facts(placement=Placement.WAITED_FOR_FOCUS_HOUR), VI)
    assert focus.startswith("Đây là lúc bạn tập trung tốt nhất")
    spread = template_reason(facts(placement=Placement.SPREAD_OUT), EN)
    assert spread.startswith("I spread the work over the days")


def test_deadline_and_split_sentences() -> None:
    no_extra = {"extra_percent": 0}
    assert template_reason(facts(**no_extra, spare_hours=0), VI).endswith(
        "Xong là vừa sát hạn chót."
    )
    assert template_reason(
        facts(**no_extra, spare_hours=5, days_to_deadline=0), VI
    ).endswith("Hạn chót trong ngày nên cần làm sớm.")
    assert template_reason(facts(**no_extra, spare_hours=50), VI).endswith(
        "Xong vẫn còn dư khoảng 2 ngày trước hạn."
    )
    split = facts(**no_extra, part_count=3, split_reason=SplitReason.LONG_WORK)
    assert template_reason(split, VI).endswith("các khối tối đa 3 tiếng, có nghỉ giữa.")


def test_wording_turns_over_between_slots() -> None:
    same = [facts(earlier_blocked_by=BlockedBy.OTHER_TASK)] * 2
    first, second = template_reasons(same, VI)
    assert first.split()[0] != second.split()[0]


ALL = [
    facts(
        task_title=title,
        placement=placement,
        earlier_blocked_by=blocked,
        part_index=part,
        part_count=3,
        split_reason=split,
        extra_percent=extra,
        extra_applied=applied,
        spare_hours=spare,
        days_to_deadline=0 if spare < 24 else 3,
    )
    for title, placement, blocked, part, split, extra, applied, spare in itertools.product(
        ["Viết", "Thiết kế giao diện trang quản trị " * 4],
        list(Placement),
        list(BlockedBy),
        [1, 2],
        [None, *SplitReason],
        [0, 45, -15],
        [True, False],
        [0, 5, 80],
    )
]


@pytest.mark.parametrize("lang", [VI, EN])
def test_every_reason_follows_the_rules(lang: Language) -> None:
    for text in template_reasons(ALL, lang):
        assert 0 < len(text) <= MAX_REASON_LENGTH
        assert not has_internal_name(text, FORBIDDEN_WORDS)
        assert "\n" not in text
        if lang is VI:
            assert " tôi " not in f" {text.lower()} "


# --- the use case: no AI call, no quota ---


def template_world(limit=5, used=5) -> World:
    world = World(limit=limit, used=used)
    world.use_case = ProposeSchedule(
        world.repo,
        world.quota,
        TemplateReasonWriter(),
        initial_k=Decimal("1.3"),
        window_days=7,
        min_chunk_minutes=30,
        round_minutes=5,
        top_productive_hours=3,
        clock=lambda: NOW,
    )
    return world


def test_template_reasons_need_no_quota_and_log_nothing() -> None:
    world = template_world(limit=5, used=5)  # out of AI calls

    result = world.run(planning(task(minutes=60)))

    (slot,) = result.slots
    assert slot.explanation.startswith("Đây là khoảng trống sớm nhất")
    assert world.calls_used == 5  # nothing charged
    assert world.log_rows == []  # no AIUsageLog
    assert world.llm.prompts == []  # no LLM call
    assert world.repo.pending(USER)[0].slots[0].explanation == slot.explanation


def test_template_writer_keeps_every_slot_id() -> None:
    ids = [uuid4(), uuid4()]
    result = asyncio.run(
        TemplateReasonWriter().write(
            dict(zip(ids, [facts(), replace(facts(), part_index=2)])), EN
        )
    )
    assert list(result.reasons) == ids and result.calls == ()
    assert result.succeeded


def test_config_picks_the_writer() -> None:
    from metanoia_ai.api.deps import schedule_reason_writer
    from metanoia_ai.application.reason_writer import ReasonWriter
    from tests.unit.api.test_auth import make_settings

    template = make_settings()
    assert template.schedule_reasons == "template"  # the default
    assert isinstance(schedule_reason_writer(template, None), TemplateReasonWriter)
    llm = template.model_copy(
        update={
            "schedule_reasons": "llm",
            "llm_model": "m",
            "gemini_api_key": SecretStr("fake-key"),
            "llm_input_price_per_1m": 0.1,
            "llm_output_price_per_1m": 0.4,
        }
    )
    assert isinstance(schedule_reason_writer(llm, object()), ReasonWriter)
