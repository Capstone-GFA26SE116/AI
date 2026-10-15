import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.chat_writer import ChatWriter
from metanoia_ai.application.ports.chat_repository import ChatRole, ChatTask
from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.use_cases.find_similar_tasks import (
    SearchMode,
    SimilarTasksResult,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.record_task_completion import (
    TaskNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
    InvalidMessageError,
    SendTaskChatMessage,
    TaskNotSplittableError,
)
from metanoia_ai.domain.models import TaskType
from metanoia_ai.domain.similar_tasks import (
    MatchSource,
    PastTask,
    SimilarOutcome,
    SimilarSummary,
    SimilarTask,
)
from metanoia_ai.domain.task_breakdown import DraftLimits
from tests.unit.application.fakes import (
    FakeChatRepository,
    FakeLLMClient,
    FakeQuotaRepository,
)

USER = uuid4()
OTHER_USER = uuid4()
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
SAIGON = ZoneInfo("Asia/Ho_Chi_Minh")
LIMITS = DraftLimits(min_subtasks=2, max_subtasks=10, min_minutes=5, max_minutes=120)


def answer(kind="draft", reply="Mình chia 4 bước.", subtasks=None, language="vi"):
    if subtasks is None:
        subtasks = [("Dựng khung", 60), ("Giao diện", 90), ("API", 90), ("Test", 60)]
    if kind == "question":
        subtasks = []
    return json.dumps(
        {
            "kind": kind,
            "language": language,
            "reply": reply,
            "subtasks": [
                {"title": t, "estimated_minutes": m, "reason": "r"} for t, m in subtasks
            ],
        },
        ensure_ascii=False,
    )


DRAFT = answer()
QUESTION = answer("question", reply="Trang có mấy section vậy bạn?")


def make_task(**changes) -> ChatTask:
    values = {
        "task_id": uuid4(),
        "project_id": uuid4(),
        "task_type": TaskType.FLEXIBLE,
        "status": "ToDo",
        "title": "Làm landing page cho Acme, gửi a.b@acme.com",
        "description": "Acme muốn có form liên hệ, gọi 0901 234 567",
        "domain_id": uuid4(),
        "domain_name": "Coding",
        "client_name": "Acme",
        "estimated_minutes": 240,
        "deadline": datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
        "time_zone": SAIGON,
        "subtask_count": 0,
    }
    return ChatTask(**(values | changes))


class StubSimilar:
    """FindSimilarTasks stand-in: returns `result`, or raises it."""

    def __init__(self, result=None) -> None:
        self.result = result
        self.calls: list[tuple] = []

    async def execute(self, user_id, task_id, limit):
        self.calls.append((user_id, task_id, limit))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or SimilarTasksResult(task_id, SearchMode.HYBRID, SAIGON)


def similar_result(task_id, title="Landing page cho Acme cũ") -> SimilarTasksResult:
    item = SimilarTask(
        PastTask(uuid4(), title, "Coding", SimilarOutcome.DONE, 120, 168, NOW),
        0.0328,
        (MatchSource.VECTOR, MatchSource.TEXT),
    )
    return SimilarTasksResult(
        task_id, SearchMode.TEXT_ONLY, SAIGON, [item], SimilarSummary(1, 1, 1.4)
    )


class Setup:
    def __init__(
        self,
        *answers,
        task=None,
        price=50_000,
        used=7,
        limit=300,
        with_plan=True,
        similar=None,
    ):
        self.repo = FakeChatRepository(now=NOW)
        self.quota = FakeQuotaRepository(now=NOW)
        self.subscription = Subscription(uuid4(), price, limit, used)
        if with_plan:
            self.quota.add_subscription(USER, self.subscription, start_at=NOW)
        self.task = task or make_task()
        self.repo.add_task(USER, self.task)
        self.llm = FakeLLMClient(*answers)
        self.similar = StubSimilar(similar)
        self.use_case = SendTaskChatMessage(
            self.repo,
            self.quota,
            ChatWriter(
                self.llm,
                limits=LIMITS,
                max_retries=2,
                input_price_per_1m=0.1,
                output_price_per_1m=0.4,
            ),
            self.similar,
            initial_k=Decimal("1.3"),
            history_limit=20,
            max_message_chars=2000,
            similar_limit=5,
            warning_ratio=0.3,
            quota_daily_max=50,
            now=lambda: NOW,
        )

    def send(self, message="Chia nhỏ giúp mình", user_id=USER, task_id=None):
        return asyncio.run(
            self.use_case.execute(user_id, task_id or self.task.task_id, message)
        )

    def messages(self):
        conversation = asyncio.run(self.repo.find_conversation(USER, self.task.task_id))
        if conversation is None:
            return []
        return asyncio.run(self.repo.list_messages(USER, conversation))

    def used(self):
        return self.quota.calls_used[self.subscription.subscription_id]

    def logs(self):
        return [entry for _, _, entry in self.quota.logs]


# --- T8: a draft ---


def test_t8_draft_is_saved_charged_and_logged():
    s = Setup(DRAFT)

    turn = s.send()

    user_msg, ai_msg = s.messages()
    assert (user_msg.role, ai_msg.role) == (ChatRole.USER, ChatRole.ASSISTANT)
    assert user_msg.content == "Chia nhỏ giúp mình"
    assert ai_msg.content == "Mình chia 4 bước."
    assert turn.ai_message == ai_msg
    saved = ai_msg.draft
    assert [st.order_index for st in saved.draft.subtasks] == [1, 2, 3, 4]
    assert (saved.numbers.your_estimate_minutes, saved.numbers.adjusted_minutes) == (
        240,
        312,
    )
    assert saved.numbers.subtask_total_minutes == 300
    assert saved.numbers.warning is None
    assert s.used() == 8  # one call charged
    [log] = s.logs()
    assert log.feature is AIFeature.CHAT
    assert (log.succeeded, log.quota_charged) == (True, True)


def test_second_message_reuses_the_chat_and_sends_the_history():
    s = Setup(DRAFT, answer(subtasks=[("A", 60), ("B", 60), ("C", 60)]))
    s.send()
    s.send("Bớt 1 bước được không")

    assert len(s.repo.conversations) == 1
    assert len(s.messages()) == 4
    prompt = s.llm.prompts[1]
    assert '"role": "assistant"' in prompt
    assert '"title": "Dựng khung"' in prompt  # the earlier draft is in the history


def test_task_row_is_locked_while_saving():
    s = Setup(DRAFT)
    s.send()
    assert s.repo.locked == [s.task.task_id]


# --- T9: a question ---


def test_t9_question_is_saved_and_charged_without_draft():
    s = Setup(QUESTION)
    s.send()
    _, ai_msg = s.messages()
    assert ai_msg.draft is None
    assert ai_msg.content == "Trang có mấy section vậy bạn?"
    assert s.used() == 8


# --- T10, T11: bad answers ---


def test_t10_bad_then_good_charges_once():
    s = Setup(answer(subtasks=[("A", 60)]), DRAFT)
    s.send()
    assert [(log.succeeded, log.quota_charged) for log in s.logs()] == [
        (False, False),
        (True, True),
    ]
    assert s.used() == 8


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param(("not json", "not json", "not json"), id="bad-3-times"),
        pytest.param((LLMError("down"),) * 3, id="llm-error"),
    ],
)
def test_t11_every_try_fails(answers):
    s = Setup(*answers)
    with pytest.raises(AIUnavailableError):
        s.send()
    assert s.messages() == []  # nothing saved
    assert s.repo.conversations == {}
    assert s.used() == 7  # not charged
    assert [(log.succeeded, log.quota_charged) for log in s.logs()] == [
        (False, False)
    ] * 3


# --- T12: cannot be split ---


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"task_type": TaskType.FIXED, "estimated_minutes": None}, "fixed"),
        ({"status": "Done"}, "finished"),
        ({"status": "Skipped"}, "finished"),
    ],
)
def test_t12_task_that_cannot_be_split(changes, reason):
    s = Setup(DRAFT, task=make_task(**changes))
    with pytest.raises(TaskNotSplittableError) as error:
        s.send()
    assert error.value.reason == reason
    assert s.llm.prompts == []
    assert s.logs() == []


def test_t12_task_with_subtasks():
    s = Setup(DRAFT)
    s.repo.subtasks[s.task.task_id] = ["already one"]
    with pytest.raises(TaskNotSplittableError) as error:
        s.send()
    assert error.value.reason == "has_subtasks"
    assert s.llm.prompts == []


@pytest.mark.parametrize("message", ["", "   ", "x" * 2001])
def test_bad_message_is_rejected_before_anything(message):
    s = Setup(DRAFT)
    with pytest.raises(InvalidMessageError):
        s.send(message)
    assert s.llm.prompts == []


def test_unknown_or_other_users_task_is_not_found():
    s = Setup(DRAFT)
    with pytest.raises(TaskNotFoundError):
        s.send(task_id=uuid4())
    with pytest.raises(TaskNotFoundError):
        s.send(user_id=OTHER_USER)
    assert s.llm.prompts == []


# --- T13: quota ---


def test_t13_no_active_plan():
    s = Setup(DRAFT, with_plan=False)
    with pytest.raises(NoSubscriptionError):
        s.send()
    assert s.llm.prompts == []


def test_t13_plan_limit_reached():
    s = Setup(DRAFT, used=300, limit=300)
    with pytest.raises(QuotaExceededError):
        s.send()
    assert s.llm.prompts == []


def test_t13_daily_limit_reached():
    s = Setup(DRAFT)
    for _ in range(50):
        asyncio.run(s.quota.log_usage(charged_entry(s)))
    with pytest.raises(QuotaExceededError):
        s.send()
    assert s.llm.prompts == []


def charged_entry(s: Setup) -> UsageLogEntry:
    """A charged call made earlier today (any feature counts for the 50/day)."""
    return UsageLogEntry(
        s.subscription.subscription_id,
        AIFeature.SCHEDULE,
        "m",
        1,
        1,
        Decimal(0),
        1,
        True,
        True,
    )


# --- T14: someone took the last call meanwhile ---


def test_t14_lost_last_call_saves_nothing():
    s = Setup(DRAFT)
    s.quota.lose_next_charge = True
    with pytest.raises(QuotaExceededError):
        s.send()
    assert s.messages() == []
    assert s.used() == 7
    [log] = s.logs()
    assert (log.succeeded, log.quota_charged) == (True, False)


def test_database_error_while_saving_charges_nothing():
    s = Setup(DRAFT)
    s.repo.fail_on_add_message = True
    with pytest.raises(RuntimeError):
        s.send()
    assert s.used() == 7
    assert s.messages() == []


# --- T15: privacy ---


def test_t15_prompt_has_no_contact_details():
    s = Setup(DRAFT, similar=similar_result(uuid4()))
    s.send("Gửi file cho lan@acme.com, gọi +84 90-123-4567, nhớ hỏi Acme")

    prompt = s.llm.prompts[0]
    for secret in (
        "a.b@acme.com",
        "lan@acme.com",
        "0901 234 567",
        "90-123-4567",
        "Acme",
    ):
        assert secret not in prompt
    assert "[email]" in prompt and "[phone]" in prompt and "[client]" in prompt
    assert "Landing page cho [client] cũ" in prompt  # similar title masked too
    # The user's own message is still saved as written (#16).
    assert "lan@acme.com" in s.messages()[0].content


# --- T16: history limit ---


def test_t16_only_the_newest_20_messages_go_to_the_llm():
    s = Setup(DRAFT)
    conversation = asyncio.run(s.repo.create_conversation(USER, s.task))
    for n in range(25):
        asyncio.run(
            s.repo.add_message(USER, conversation, ChatRole.USER, f"tin cũ số {n}")
        )
    s.send()
    prompt = s.llm.prompts[0]
    assert "tin cũ số 4" not in prompt
    assert "tin cũ số 5" in prompt and "tin cũ số 24" in prompt


# --- T17: K ---


def test_t17_no_profile_uses_the_initial_k():
    s = Setup(DRAFT)
    s.send()
    assert '"usually_extra_percent": 30' in s.llm.prompts[0]
    assert s.messages()[1].draft.numbers.adjusted_minutes == 312


def test_learned_k_is_used():
    s = Setup(DRAFT)
    s.repo.k_bias[(USER, s.task.domain_id)] = Decimal("1.5")
    s.send()
    assert '"usually_extra_percent": 50' in s.llm.prompts[0]
    assert s.messages()[1].draft.numbers.adjusted_minutes == 360


def test_free_plan_numbers_are_a_hint():
    s = Setup(DRAFT, price=0)
    s.send()
    assert s.messages()[1].draft.numbers.adjusted_is_hint is True


def test_warning_follows_the_reply_language():
    s = Setup(answer(subtasks=[("A", 30), ("B", 30)], language="en"))
    s.send("Please split this")
    assert s.messages()[1].draft.numbers.warning.startswith("The steps add up to 60")


# --- T18: similar tasks ---


def test_t18_text_only_similar_results_are_used():
    s = Setup(DRAFT, similar=similar_result(uuid4()))
    s.send()
    assert s.messages()[1].draft.similar == SimilarSummary(1, 1, 1.4)
    assert s.similar.calls == [(USER, s.task.task_id, 5)]


def test_t18_no_similar_tasks_gives_none():
    s = Setup(DRAFT)
    s.send()
    assert s.messages()[1].draft.similar is None


def test_similar_search_error_does_not_stop_the_chat():
    s = Setup(DRAFT, similar=RuntimeError("db down"))
    s.send()
    assert s.messages()[1].draft.similar is None
    assert s.used() == 8
