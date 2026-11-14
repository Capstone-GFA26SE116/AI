"""FR-08b-1 use cases: open a project chat, send a message (fake DB, fake LLM)."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.project_chat_repository import (
    ChatProject,
    ChatRole,
    ConversationStatus,
)
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.application.project_chat_writer import ProjectChatWriter
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.send_project_chat_message import (
    SendProjectChatMessage,
    pick_history,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
)
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
    ConversationNotFoundError,
    ListProjectChats,
    ProjectClosedError,
    ProjectNotFoundError,
    StartProjectChat,
)
from metanoia_ai.domain.project_breakdown import DomainNumbers, ProjectLimits
from tests.unit.application.fakes import (
    FakeLLMClient,
    FakeProjectChatRepository,
    FakeQuotaRepository,
)

SAIGON = ZoneInfo("Asia/Saigon")
NOW = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)  # 2026-10-16 03:00 in Saigon
TODAY = date(2026, 10, 16)
USER, OTHER = uuid4(), uuid4()
DESIGN, CODING, OLD = UUID(int=1), UUID(int=2), UUID(int=3)
LIMITS = ProjectLimits(8, 15, 40, 15, 480, 150)


class Clock:
    def __init__(self, step: float = 0.1) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class World:
    """One user with an Active project, a project chat and a paid plan."""

    def __init__(self, project_status="Active", deadline=date(2026, 10, 30), user=USER):
        self.user = user
        self.repo = FakeProjectChatRepository(now=NOW)
        self.quota = FakeQuotaRepository(now=NOW)
        self.plan = Subscription(uuid4(), price=99, ai_call_limit=50, ai_calls_used=0)
        self.quota.add_subscription(user, self.plan, start_at=NOW)
        self.project = ChatProject(
            uuid4(),
            "Landing page ACME",
            "Khách ACME, liên hệ ceo@acme.vn",
            deadline,
            project_status,
            "ACME",
            SAIGON,
        )
        self.repo.add_project(user, self.project)
        self.repo.add_domain(DESIGN, "Design")
        self.repo.add_domain(CODING, "Coding")
        self.repo.add_domain(OLD, "Old", is_active=False)
        self.repo.k_bias[(user, CODING)] = Decimal("1.5")
        self.chat = run(
            self.repo.create_conversation(user, self.project.project_id, "Brief")
        )

    @property
    def chat_id(self) -> UUID:
        return self.chat.conversation_id

    def use_case(self, llm, budget=40.0, step=0.1, prompt_max=30000, history=10):
        writer = ProjectChatWriter(
            llm,
            limits=LIMITS,
            max_retries=1,
            input_price_per_1m=0.1,
            output_price_per_1m=0.4,
            retry_budget_seconds=budget,
            clock=Clock(step),
        )
        return SendProjectChatMessage(
            self.repo,
            self.quota,
            writer,
            initial_k=Decimal("1.3"),
            history_limit=history,
            max_message_chars=8000,
            max_prompt_chars=prompt_max,
            existing_titles_limit=50,
            daily_work_hours=6,
            now=lambda: NOW,
        )

    def send(self, llm, message="Brief của khách", chat_id=None, **kw):
        return run(
            self.use_case(llm, **kw).execute(
                self.user, chat_id or self.chat_id, message
            )
        )

    @property
    def calls_used(self) -> int:
        return self.quota.calls_used[self.plan.subscription_id]

    def messages(self):
        return run(self.repo.list_messages(self.user, self.chat_id))


def run(coro):
    return asyncio.run(coro)


def task(title, domain=DESIGN, minutes=120):
    return {
        "title": title,
        "domain_id": str(domain),
        "estimated_minutes": minutes,
        "reason": "r",
    }


def draft_answer(milestones=None, reminders=(), reply="Mình chia 2 giai đoạn."):
    if milestones is None:
        milestones = [
            {
                "title": "Thiết kế",
                "deadline": "2026-10-22",
                "tasks": [task("Wireframe", minutes=240)],
            },
            {
                "title": "Lập trình",
                "deadline": None,
                "tasks": [task("Dựng trang", CODING, 300)],
            },
        ]
    return json.dumps(
        {
            "kind": "draft",
            "language": "vi",
            "reply": reply,
            "milestones": milestones,
            "reminders": list(reminders),
        }
    )


QUESTION = json.dumps(
    {
        "kind": "question",
        "language": "vi",
        "reply": "Bạn cần bàn giao trước ngày nào?",
        "milestones": [],
        "reminders": [],
    }
)


# --- T10: question ---


def test_t10_question_saves_two_messages_and_charges_once():
    world = World()

    turn = world.send(FakeLLMClient(QUESTION))

    assert turn.ai_message.content == "Bạn cần bàn giao trước ngày nào?"
    assert turn.ai_message.draft is None
    assert [m.role for m in world.messages()] == [ChatRole.USER, ChatRole.ASSISTANT]
    assert world.calls_used == 1
    assert [e.quota_charged for _, _, e in world.quota.logs] == [True]


# --- T11: draft ---


def test_t11_draft_is_saved_with_keys_deadlines_and_numbers():
    world = World()

    turn = world.send(FakeLLMClient(draft_answer(reminders=["Họp chốt giao diện"])))

    saved = turn.ai_message.draft
    m1, m2 = saved.draft.milestones
    assert (m1.key, m1.deadline) == ("m1", date(2026, 10, 22))
    assert (m2.key, m2.deadline) == ("m2", date(2026, 10, 30))  # project deadline
    assert m2.tasks[0].key == "m2-t1"
    assert saved.draft.reminders == ("Họp chốt giao diện",)
    # Design: no K yet → 1.3: 240 → 312. Coding K 1.5: 300 → 450.
    assert saved.numbers.by_domain == (
        DomainNumbers("Design", 240, 312),
        DomainNumbers("Coding", 300, 450),
    )
    assert saved.numbers.days_left == 14
    assert saved.numbers.adjusted_is_hint is False
    assert world.calls_used == 1
    assert len(world.quota.logs) == 1


def test_free_plan_numbers_are_a_hint():
    world = World()
    world.quota.subscriptions[USER] = []
    free = Subscription(uuid4(), price=0, ai_call_limit=50, ai_calls_used=0)
    world.quota.add_subscription(USER, free, start_at=NOW)
    turn = world.send(FakeLLMClient(draft_answer()))
    assert turn.ai_message.draft.numbers.adjusted_is_hint is True


def test_overload_warning_is_added_to_the_draft_warnings():
    world = World(deadline=date(2026, 10, 17))  # 1 day left, 6 h
    big = [
        {
            "title": "M",
            "deadline": None,
            "tasks": [task(f"T{i}", minutes=480) for i in range(3)],
        }
    ]
    turn = world.send(FakeLLMClient(draft_answer(big)))
    assert any("chỉ còn 1 ngày" in w for w in turn.ai_message.draft.draft.warnings)


def test_user_message_is_saved_as_written():
    world = World()
    world.send(FakeLLMClient(QUESTION), message="  Gửi ceo@acme.vn bản ACME  ")
    assert world.messages()[0].content == "Gửi ceo@acme.vn bản ACME"


# --- T12: retries (use case part) ---


def test_t12_bad_once_then_good_logs_two_calls_and_charges_one():
    world = World()
    world.send(FakeLLMClient("not json", QUESTION))
    assert world.calls_used == 1
    assert [e.quota_charged for _, _, e in world.quota.logs] == [False, True]


def test_t12_bad_twice_is_502_and_charges_nothing():
    world = World()
    with pytest.raises(AIUnavailableError):
        world.send(FakeLLMClient("bad", "still bad"))
    assert world.calls_used == 0
    assert world.messages() == []
    assert [e.quota_charged for _, _, e in world.quota.logs] == [False, False]


def test_t12_over_the_time_budget_is_502_without_retry():
    world = World()
    llm = FakeLLMClient("bad", QUESTION)
    with pytest.raises(AIUnavailableError):
        world.send(llm, step=41.0)
    assert len(llm.prompts) == 1
    assert world.calls_used == 0


# --- T13: refused before any LLM call ---


def test_t13_applied_chat():
    world = World()
    msg = run(world.repo.add_message(USER, world.chat_id, ChatRole.ASSISTANT, "ok"))
    run(world.repo.mark_applied(USER, world.chat_id, msg.message_id))
    llm = FakeLLMClient(QUESTION)
    with pytest.raises(ConversationAppliedError):
        world.send(llm)
    assert llm.prompts == []


@pytest.mark.parametrize("status", ["Completed", "Archived"])
def test_t13_closed_project(status):
    world = World(project_status=status)
    llm = FakeLLMClient(QUESTION)
    with pytest.raises(ProjectClosedError):
        world.send(llm)
    assert llm.prompts == []


def test_on_hold_project_can_still_chat():
    world = World(project_status="OnHold")
    world.send(FakeLLMClient(QUESTION))
    assert world.calls_used == 1


def test_t13_no_active_plan():
    world = World()
    world.quota.subscriptions[USER] = []
    llm = FakeLLMClient(QUESTION)
    with pytest.raises(NoSubscriptionError):
        world.send(llm)
    assert llm.prompts == []


def test_t13_out_of_calls():
    world = World()
    world.quota.calls_used[world.plan.subscription_id] = 50
    llm = FakeLLMClient(QUESTION)
    with pytest.raises(QuotaExceededError):
        world.send(llm)
    assert llm.prompts == []


@pytest.mark.parametrize("message", ["", "   ", "x" * 8001])
def test_bad_message_is_rejected(message):
    world = World()
    llm = FakeLLMClient(QUESTION)
    with pytest.raises(InvalidMessageError):
        world.send(llm, message=message)
    assert llm.prompts == []


def test_unknown_task_or_other_users_chat_is_not_found():
    world = World()
    world.repo.task_chat_ids.add(world.chat_id)  # a task chat (FR-08a)
    with pytest.raises(ConversationNotFoundError):
        world.send(FakeLLMClient(QUESTION))
    with pytest.raises(ConversationNotFoundError):
        world.send(FakeLLMClient(QUESTION), chat_id=uuid4())


# --- T14: the last call is taken meanwhile ---


def test_t14_charge_fails_after_the_answer():
    world = World()
    world.quota.lose_next_charge = True
    with pytest.raises(QuotaExceededError):
        world.send(FakeLLMClient(QUESTION))
    assert world.messages() == []
    assert [e.quota_charged for _, _, e in world.quota.logs] == [False]


def test_applied_in_another_tab_while_the_ai_answers():
    world = World()

    def apply_meanwhile(prompt: str) -> str:
        chat = world.repo.chats[world.chat_id]
        chat.conversation = replace(
            chat.conversation, status=ConversationStatus.APPLIED
        )
        return QUESTION

    with pytest.raises(ConversationAppliedError):
        world.send(FakeLLMClient(apply_meanwhile))
    assert world.calls_used == 0
    assert [e.quota_charged for _, _, e in world.quota.logs] == [False]


# --- T15: privacy ---


def test_t15_prompt_hides_emails_phones_and_the_client():
    world = World()
    world.repo.add_old_task(world.project.project_id, "Gọi ACME chốt màu")
    llm = FakeLLMClient(QUESTION)

    world.send(llm, message="Brief từ ACME: gửi mẫu tới ceo@acme.vn hoặc 0901 234 567")

    prompt = llm.prompts[0]
    for secret in ("ACME", "acme", "ceo@", "0901 234 567"):
        assert secret not in prompt
    for mask in ("[client]", "[email]", "[phone]"):
        assert mask in prompt


# --- T16: long history ---


def test_pick_history_keeps_the_brief_and_the_newest():
    messages = list(range(30))
    assert pick_history(messages, 10) == [0, *range(20, 30)]
    assert pick_history(messages[:5], 10) == list(range(5))


def add_history(world: World, count: int, size: int = 20) -> None:
    for i in range(count):
        role = ChatRole.USER if i % 2 == 0 else ChatRole.ASSISTANT
        run(
            world.repo.add_message(
                USER, world.chat_id, role, f"tin {i:02d} " + "x" * size
            )
        )


def test_t16_brief_and_last_10_messages_are_sent():
    world = World()
    add_history(world, 30)
    llm = FakeLLMClient(QUESTION)

    world.send(llm, message="tin mới")

    prompt = llm.prompts[0]
    assert "tin 00 " in prompt  # the brief
    assert "tin 01 " not in prompt and "tin 19 " not in prompt
    assert all(f"tin {i:02d} " in prompt for i in range(20, 30))


def test_t16_long_history_is_cut_to_fit_the_prompt_limit():
    world = World()
    add_history(world, 12, size=1500)
    llm = FakeLLMClient(QUESTION)

    world.send(llm, message="tin mới", prompt_max=12000)

    prompt = llm.prompts[0]
    assert len(prompt) <= 12000
    assert "tin 00 " in prompt  # the brief is never dropped
    assert "tin 11 " in prompt  # the newest stays


# --- T17: domains in the schema ---


def test_t17_schema_lists_only_active_domains():
    world = World()
    llm = FakeLLMClient(QUESTION)
    world.send(llm)
    enum = llm.schemas[0]["$defs"]["ProjectTaskItem"]["properties"]["domain_id"]["enum"]
    assert sorted(enum) == sorted([str(DESIGN), str(CODING)])


def test_existing_tasks_are_not_repeated():
    world = World()
    world.repo.add_old_task(world.project.project_id, "Wireframe")
    turn = world.send(FakeLLMClient(draft_answer()))
    titles = [t.title for t in turn.ai_message.draft.draft.tasks]
    assert titles == ["Dựng trang"]


# --- T18: open a chat ---


def test_t18_new_chat_title_uses_the_user_date():
    world = World()
    chat = run(
        StartProjectChat(world.repo, lambda: NOW).execute(
            USER, world.project.project_id
        )
    )
    assert chat.title == "Brief 2026-10-16"  # 03:00 in Saigon, still the 15th in UTC
    assert chat.status is ConversationStatus.OPEN


def test_t18_other_users_project_is_not_found():
    world = World()
    with pytest.raises(ProjectNotFoundError):
        run(
            StartProjectChat(world.repo, lambda: NOW).execute(
                OTHER, world.project.project_id
            )
        )


def test_closed_project_cannot_open_a_chat():
    world = World(project_status="Completed")
    with pytest.raises(ProjectClosedError):
        run(
            StartProjectChat(world.repo, lambda: NOW).execute(
                USER, world.project.project_id
            )
        )


def test_list_chats_newest_first_and_owner_only():
    world = World()
    second = run(
        StartProjectChat(world.repo, lambda: NOW).execute(
            USER, world.project.project_id
        )
    )
    chats = run(ListProjectChats(world.repo).execute(USER, world.project.project_id))
    assert [c.conversation_id for c in chats] == [second.conversation_id, world.chat_id]
    with pytest.raises(ProjectNotFoundError):
        run(ListProjectChats(world.repo).execute(OTHER, world.project.project_id))


# --- #35, #36 ---


def test_vietnamese_brief_asks_for_vietnamese():
    world = World()
    llm = FakeLLMClient(QUESTION)
    world.send(llm, message="Làm landing page 3 trang cho khách")
    assert "Vietnamese only." in llm.prompts[0]


def test_english_chat_stays_english_on_a_short_message():
    world = World()
    world.send(FakeLLMClient(QUESTION), message="Client wants a two page site")
    llm = FakeLLMClient(QUESTION)
    world.send(llm, message="Ok")
    assert "English only." in llm.prompts[0]


def test_english_chat_warnings_are_english():
    world = World()
    world.repo.add_old_task(world.project.project_id, "Wireframe")
    turn = world.send(FakeLLMClient(draft_answer()), message="Plan this site please")
    assert turn.ai_message.draft.draft.warnings == (
        'Removed "Wireframe": the project already has it.',
    )


def test_reminders_show_the_client_name():
    world = World()
    turn = world.send(FakeLLMClient(draft_answer(reminders=["Họp chốt với [client]"])))
    assert turn.ai_message.draft.draft.reminders == ("Họp chốt với ACME",)
