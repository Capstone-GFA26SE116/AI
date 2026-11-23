"""FR-24 use case: access, quota, discuss / plan, assignments, privacy, applied (T3–T7)."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.group_assignee_repository import (
    GroupMember,
    GroupWork,
)
from metanoia_ai.application.ports.group_chat_repository import ChatGroup
from metanoia_ai.application.ports.project_chat_repository import ExistingWork
from metanoia_ai.application.ports.quota_repository import AIFeature, Subscription
from metanoia_ai.application.ports.schedule_repository import PlanningData
from metanoia_ai.application.project_chat_writer import ProjectChatWriter
from metanoia_ai.application.use_cases.group_chat import (
    DraftAlreadyAppliedError,
    GroupChat,
    GroupChatNotFoundError,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    PlanFeatureError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
)
from metanoia_ai.application.use_cases.suggest_group_assignee import (
    GroupArchivedError,
    NotGroupLeaderError,
)
from metanoia_ai.domain.assignee_ranking import GroupRole
from metanoia_ai.domain.models import Language, WorkingHours
from metanoia_ai.domain.project_breakdown import ProjectLimits
from metanoia_ai.domain.project_discussion import ChatMode
from tests.unit.application.fakes import (
    FakeGroupAssigneeRepository,
    FakeGroupChatRepository,
    FakeLLMClient,
    FakeQuotaRepository,
    FakeScheduleRepository,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 23, 1, tzinfo=UTC)  # Monday 08:00 in Vietnam
LEADER, MEMBER, OTHER = UUID(int=1), UUID(int=2), UUID(int=9)
GROUP = UUID(int=100)
CODING, DESIGN = UUID(int=301), UUID(int=302)
HOURS = WorkingHours(VN, time(8), time(17), False, 15)
LIMITS = ProjectLimits(8, 15, 40, 15, 480, 150)


def run(coro):
    return asyncio.run(coro)


def task(title: str, domain: UUID = CODING, minutes: int = 120) -> dict:
    return {
        "title": title,
        "domain_id": str(domain),
        "estimated_minutes": minutes,
        "reason": "r",
    }


def draft_answer() -> str:
    return json.dumps(
        {
            "kind": "draft",
            "language": "vi",
            "reply": "Mình chia 2 giai đoạn cho nhóm.",
            "milestones": [
                {
                    "title": "Thiết kế",
                    "deadline": "2026-11-25",
                    "tasks": [task("Wireframe trang chủ", DESIGN, 120)],
                },
                {
                    "title": "Lập trình",
                    "deadline": "2026-11-27",
                    "tasks": [
                        task("Dựng trang chủ", CODING, 120),
                        task("Kết nối API", CODING, 120),
                    ],
                },
            ],
            "reminders": [],
        },
        ensure_ascii=False,
    )


DISCUSS = json.dumps(
    {
        "reply": "Mình đã nắm phần chính.",
        "clear": ["Landing page 3 phần"],
        "missing": ["Bản tiếng Anh?"],
        "questions": ["Mấy vòng góp ý?"],
        "risks": ["Chưa có nội dung"],
        "ready_to_plan": True,
    },
    ensure_ascii=False,
)


def planning() -> PlanningData:
    return PlanningData(HOURS, (), {}, {}, (), (), True, ())


class World:
    def __init__(self, *answers: object, price: int = 99_000, calls: int = 10) -> None:
        self.quota = FakeQuotaRepository(NOW)
        self.chats = FakeGroupChatRepository(self.quota, NOW)
        self.members = FakeGroupAssigneeRepository(self.quota)
        self.schedules = FakeScheduleRepository(self.quota)
        self.llm = FakeLLMClient(*answers)  # type: ignore[arg-type]
        self.chats.groups[GROUP] = ChatGroup(
            GROUP,
            "Website cho anh.nguyen@example.com",
            None,
            date(2026, 11, 30),
            False,
            None,
            VN,
        )
        self.chats.roles[(GROUP, LEADER)] = GroupRole.LEADER
        self.chats.roles[(GROUP, MEMBER)] = GroupRole.MEMBER
        self.chats.domains = {CODING: "Coding", DESIGN: "Design"}
        self.members.members[GROUP] = [
            GroupMember(LEADER, GroupRole.LEADER),
            GroupMember(MEMBER, GroupRole.MEMBER),
        ]
        for user in (LEADER, MEMBER):
            self.schedules.planning[user] = planning()
        self.plan = Subscription(UUID(int=500), price, calls, 0)
        self.quota.add_subscription(LEADER, self.plan, NOW - timedelta(days=1))

    def use_case(self) -> GroupChat:
        writer = ProjectChatWriter(
            self.llm,
            limits=LIMITS,
            max_retries=1,
            input_price_per_1m=0.1,
            output_price_per_1m=0.4,
            retry_budget_seconds=30,
        )
        return GroupChat(
            self.chats,
            self.members,
            self.schedules,
            self.quota,
            writer,
            initial_k=Decimal("1.3"),
            history_limit=20,
            max_message_chars=4000,
            existing_titles_limit=50,
            daily_work_hours=8,
            window_days=7,
            max_candidates=20,
            now=lambda: NOW,
        )

    def start(self, caller: UUID = LEADER) -> UUID:
        chat = run(self.use_case().start(caller, GROUP, None, Language.VI))
        return chat.conversation_id

    def send(self, chat: UUID, text: str, mode=ChatMode.PLAN, caller: UUID = LEADER):
        return run(self.use_case().send(caller, chat, text, mode))

    @property
    def used(self) -> int:
        return self.quota.calls_used[self.plan.subscription_id]


# --- T3: access and quota ---


def test_only_the_leader_can_start_and_use_a_chat():
    world = World(draft_answer())
    with pytest.raises(GroupChatNotFoundError):
        world.start(OTHER)
    with pytest.raises(NotGroupLeaderError):
        world.start(MEMBER)
    chat = world.start()
    with pytest.raises(NotGroupLeaderError):
        world.send(chat, "Brief", caller=MEMBER)
    with pytest.raises(GroupChatNotFoundError):
        run(world.use_case().get(OTHER, chat))
    with pytest.raises(GroupChatNotFoundError):
        world.send(UUID(int=77), "Brief")


def test_archived_group_takes_no_new_chat_or_message():
    world = World(draft_answer())
    chat = world.start()
    world.chats.groups[GROUP] = ChatGroup(GROUP, "G", None, None, True, None, VN)
    with pytest.raises(GroupArchivedError):
        world.start()
    with pytest.raises(GroupArchivedError):
        world.send(chat, "Brief")


def test_free_plan_and_no_calls_are_refused_before_the_llm():
    free = World(draft_answer(), price=0)
    with pytest.raises(PlanFeatureError):
        free.send(free.start(), "Brief")
    empty = World(draft_answer(), calls=0)
    with pytest.raises(QuotaExceededError):
        empty.send(empty.start(), "Brief")
    assert free.llm.prompts == [] and empty.llm.prompts == []


def test_bad_messages_are_refused():
    world = World()
    chat = world.start()
    with pytest.raises(InvalidMessageError):
        world.send(chat, "   ", ChatMode.DISCUSS)
    with pytest.raises(InvalidMessageError):
        world.send(chat, "x" * 4001)


# --- T4: discuss and plan ---


def test_discuss_saves_a_checklist_and_charges_once():
    world = World(DISCUSS)
    chat = world.start()
    turn = world.send(chat, "Khách cần landing page", ChatMode.DISCUSS)
    assert turn.ai_message.notes is not None and turn.ai_message.draft is None
    assert world.used == 1
    ((_, _, log),) = world.quota.logs
    assert log.feature is AIFeature.CHAT and log.quota_charged


def test_plan_gives_a_draft_with_a_member_for_every_task():
    world = World(draft_answer())
    world.members.work[LEADER] = GroupWork((), 600)  # the Leader is busier
    chat = world.start()
    turn = world.send(chat, "Brief của khách")
    ai = turn.ai_message
    assert ai.draft is not None and len(ai.draft.draft.tasks) == 3
    keys = [t.key for t in ai.draft.draft.tasks]
    assert [a.task_key for a in ai.assignments] == keys
    assert all(a.user_id in (LEADER, MEMBER) and a.reason for a in ai.assignments)
    assert ai.assignments[0].user_id == MEMBER  # more free time than the Leader
    assert world.used == 1
    messages = run(world.use_case().get(LEADER, chat)).messages
    assert [m.role for m in messages] == [ChatRole.USER, ChatRole.ASSISTANT]


def test_equal_members_share_the_draft_tasks():
    world = World(draft_answer())
    turn = world.send(world.start(), "Brief của khách")
    owners = [a.user_id for a in turn.ai_message.assignments]
    assert set(owners) == {LEADER, MEMBER}


def test_plan_button_without_text_uses_the_button_label():
    world = World(DISCUSS, draft_answer())
    chat = world.start()
    world.send(chat, "Khách cần trang web bán hàng", ChatMode.DISCUSS)
    turn = world.send(chat, "")
    assert turn.user_message.content == "Lên kế hoạch"
    assert world.used == 2


# --- T5: LLM failure ---


def test_llm_failure_is_502_without_charge_but_logged():
    world = World("not json", "still bad")
    chat = world.start()
    with pytest.raises(AIUnavailableError):
        world.send(chat, "Brief")
    assert world.used == 0
    assert [log.quota_charged for _, _, log in world.quota.logs] == [False, False]
    assert world.chats.messages[chat] == []


def test_losing_the_last_call_saves_nothing():
    world = World(draft_answer(), calls=1)
    world.quota.lose_next_charge = True
    chat = world.start()
    with pytest.raises(QuotaExceededError):
        world.send(chat, "Brief")
    assert world.chats.messages[chat] == []


# --- T6: privacy ---


def test_prompt_masks_contacts_and_has_no_member_data():
    world = World(draft_answer())
    world.chats.work[GROUP] = ExistingWork(("Khởi động",), ("Họp với 0901234567",))
    world.send(world.start(), "Brief, gọi 0912345678 hoặc mail b@example.com")
    prompt = world.llm.prompts[0]
    for secret in (
        "anh.nguyen@example.com",
        "0912345678",
        "b@example.com",
        "0901234567",
        str(LEADER),
        str(MEMBER),
    ):
        assert secret not in prompt


# --- T7: applied ---


def test_a_draft_is_marked_applied_once():
    world = World(draft_answer())
    chat = world.start()
    turn = world.send(chat, "Brief")
    use_case = world.use_case()
    run(use_case.mark_applied(LEADER, chat, turn.ai_message.message_id))
    messages = run(use_case.get(LEADER, chat)).messages
    assert messages[1].is_applied
    with pytest.raises(DraftAlreadyAppliedError):
        run(use_case.mark_applied(LEADER, chat, turn.ai_message.message_id))
    with pytest.raises(DraftAlreadyAppliedError):  # a user message is no draft
        run(use_case.mark_applied(LEADER, chat, turn.user_message.message_id))


def test_list_chats_newest_first():
    world = World()
    first = world.start()
    world.chats.now = NOW + timedelta(hours=1)
    second = world.start()
    chats = run(world.use_case().list_chats(LEADER, GROUP))
    assert [c.conversation_id for c in chats] == [second, first]
    assert chats[0].title == "Lập kế hoạch nhóm"
