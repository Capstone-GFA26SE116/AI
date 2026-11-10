"""FR-22 use case with fakes (no DB, no Gemini, no Backend). T4–T8."""

import asyncio
import json
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.application.llm_chat import CheckedLLMCaller
from metanoia_ai.application.ports.assistant_repository import ScheduleRows
from metanoia_ai.application.ports.backend_reader import (
    BackendReadError,
    MilestoneSummary,
    PendingProposal,
    PendingSession,
    ProjectProgress,
    ProjectSummary,
    RunningFocus,
    UnreadNotice,
)
from metanoia_ai.application.ports.llm_client import LLMError
from metanoia_ai.application.ports.quota_repository import Subscription
from metanoia_ai.application.use_cases.assistant_turn import (
    AssistantSettings,
    AssistantTurn,
    HistoryLine,
    TurnKind,
    TurnRequest,
    build_prompt,
    hour_ranges,
)
from metanoia_ai.application.use_cases.find_similar_tasks import (
    SearchMode,
    SimilarTasksResult,
)
from metanoia_ai.application.use_cases.get_daily_briefing import (
    Briefing,
    BriefingAtRisk,
    BriefingTopTask,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    QuotaExceededError,
)
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.domain.assistant_text import Emotion
from metanoia_ai.domain.assistant_tools import ToolName
from metanoia_ai.domain.briefing import (
    FixedEntry,
    ScheduleItem,
    ScheduleKind,
    SlotEntry,
    SlotStatus,
)
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.name_matching import NamedItem
from metanoia_ai.domain.similar_tasks import (
    PastTask,
    SimilarOutcome,
    SimilarSummary,
    SimilarTask,
)
from tests.unit.application.fakes import (
    FakeAssistantRepository,
    FakeAssistantUser,
    FakeBackendReader,
    FakeLLMClient,
    FakeQuotaRepository,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=VN)
USER = UUID(int=1)
SUB = UUID(int=99)
AUTH = "Bearer user-token"
TASK_BLOG = NamedItem(UUID(int=11), "Viết bài blog tháng 11")
LANDING_A = NamedItem(UUID(int=21), "Landing page Client A")
LANDING_B = NamedItem(UUID(int=22), "Landing page Client B")
SPA = NamedItem(UUID(int=23), "App đặt lịch spa")
SETTINGS = AssistantSettings(min_confidence=0.5, match_min_score=0.8, match_min_gap=0.2)


def at(hour: int, minute: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute)


def pick(tool: str, **fields) -> str:
    return json.dumps({"tool": tool, "confidence": 0.9, **fields})


class Sources:
    """The AI-owned readers, recording who was called."""

    def __init__(self) -> None:
        self.called: list[str] = []
        self.briefing_value = Briefing(
            date=NOW.date(),
            time_zone=VN,
            calendar_connected=True,
            has_pending_proposal=False,
            schedule=(
                ScheduleItem(ScheduleKind.SLOT, at(9), at(10), "Viết blog", None),
                ScheduleItem(ScheduleKind.FIXED, at(14), at(15), "Họp khách", None),
            ),
            all_day=(),
            top_tasks=(
                BriefingTopTask(UUID(int=5), "Viết blog", "Blog", at(17), 60, (), None),
            ),
            at_risk=(BriefingAtRisk(UUID(int=6), "Báo cáo", at(17), 30),),
            productive_hint=None,
        )
        self.similar_value = SimilarTasksResult(
            TASK_BLOG.id,
            SearchMode.HYBRID,
            VN,
            items=[
                SimilarTask(
                    PastTask(
                        UUID(int=31),
                        "Blog 10",
                        "Writing",
                        SimilarOutcome.DONE,
                        60,
                        90,
                        None,
                    ),
                    0.5,
                    (),
                ),
                SimilarTask(
                    PastTask(
                        UUID(int=32),
                        "Blog 9",
                        "Writing",
                        SimilarOutcome.DONE,
                        60,
                        80,
                        None,
                    ),
                    0.4,
                    (),
                ),
            ],
            summary=SimilarSummary(2, 2, 1.42),
        )

    async def briefing(self, user, lang):
        self.called.append("briefing")
        return self.briefing_value

    async def habits(self, user, lang):
        self.called.append("habits")
        return ["Bạn thường cần thêm khoảng 30% thời gian cho Writing."]

    async def hours(self, user):
        self.called.append("hours")
        return [21, 9, 10, 20]

    async def similar(self, user, task_id):
        self.called.append(f"similar:{task_id.int}")
        return self.similar_value


def setup(
    *answers,
    backend: FakeBackendReader | None = None,
    calls_used: int = 3,
    limit: int | None = 30,
    user: UUID = USER,
):
    repo = FakeAssistantRepository()
    repo.add_user(
        user,
        FakeAssistantUser(
            time_zone=VN,
            open_tasks=[TASK_BLOG],
            projects=[LANDING_A, LANDING_B, SPA],
            schedule=ScheduleRows(
                slots=(
                    SlotEntry(
                        UUID(int=41), "Viết blog", at(13), at(14), SlotStatus.APPROVED
                    ),
                    SlotEntry(
                        UUID(int=42), "Đề xuất", at(15), at(16), SlotStatus.PROPOSED
                    ),
                ),
                fixed_tasks=(
                    FixedEntry(UUID(int=43), "Họp khách", at(16), at(17), False),
                ),
                events=(),
            ),
        ),
    )
    quota = FakeQuotaRepository(NOW)
    quota.add_subscription(
        user, Subscription(SUB, 0, limit, calls_used), NOW - timedelta(days=1)
    )
    llm = FakeLLMClient(*answers)
    backend = backend or FakeBackendReader()
    sources = Sources()
    use_case = AssistantTurn(
        repo,
        quota,
        CheckedLLMCaller(
            llm, max_retries=0, input_price_per_1m=0.1, output_price_per_1m=0.4
        ),
        backend,
        briefing=sources.briefing,
        habits=sources.habits,
        hours=sources.hours,
        similar=sources.similar,
        settings=SETTINGS,
        now=lambda: NOW,
    )
    return use_case, repo, quota, llm, backend, sources


def ask(
    use_case, text="câu hỏi", lang=Language.VI, history=(), selected=None, user=USER
):
    request = TurnRequest(user, text, lang, tuple(history), AUTH, selected)
    return asyncio.run(use_case.execute(request))


def charged(quota: FakeQuotaRepository) -> int:
    return quota.calls_used[SUB]


# --- T4: the 10 read tools ---


def test_today_briefing() -> None:
    use_case, _, quota, _, backend, sources = setup(pick("today_briefing"))

    turn = ask(use_case, "Hôm nay tôi nên làm gì?")

    assert (turn.kind, turn.tool, turn.emotion) == (
        TurnKind.ANSWER,
        ToolName.TODAY_BRIEFING,
        Emotion.NEUTRAL,
    )
    assert turn.reply.startswith(
        "Hôm nay bạn có 2 việc trên lịch. Nên làm trước: Viết blog."
    )
    assert sources.called == ["briefing"] and backend.calls == []
    assert charged(quota) == 4


def test_at_risk_deadlines() -> None:
    use_case, *_ = setup(pick("at_risk_deadlines"))

    turn = ask(use_case)

    assert turn.reply == "Có 1 việc có nguy cơ trễ hạn: Báo cáo (hạn 17 giờ hôm nay)."
    assert turn.emotion is Emotion.NEUTRAL


def test_schedule_for_reads_the_period_from_the_db() -> None:
    use_case, repo, _, _, backend, _ = setup(pick("schedule_for", period="afternoon"))

    turn = ask(use_case, "Chiều nay tôi có gì?")

    assert repo.schedule_reads == [(at(12), at(18))]
    assert (
        turn.reply == "Chiều nay bạn có 2 việc: 13 giờ Viết blog và 16 giờ Họp khách."
    )
    assert turn.emotion is Emotion.HAPPY
    assert backend.calls == []  # no /api/calendar (plan §5, D)


def test_running_timer() -> None:
    focus = RunningFocus("Viết blog", NOW, 1830, True)
    use_case, *_, backend, _ = setup(
        pick("running_timer"), backend=FakeBackendReader(focus=focus)
    )

    turn = ask(use_case)

    assert turn.reply == (
        "Bạn đang bấm giờ cho Viết blog, được khoảng 30 phút rồi. Việc này đã vượt thời gian dự kiến."
    )
    assert turn.emotion is Emotion.NEUTRAL
    assert backend.calls == [("current_focus", AUTH)]


def test_no_running_timer() -> None:
    use_case, *_ = setup(pick("running_timer"), backend=FakeBackendReader(focus=None))

    assert (
        ask(use_case, lang=Language.EN).reply
        == "You are not timing any task right now."
    )


def test_estimation_habits_and_productive_hours() -> None:
    use_case, *_ = setup(
        pick("estimation_habits"), pick("productive_hours"), calls_used=0
    )

    habits = ask(use_case)
    hours = ask(use_case)

    assert habits.reply == "Bạn thường cần thêm khoảng 30% thời gian cho Writing."
    assert (
        hours.reply
        == "Bạn thường làm việc tốt nhất khoảng 9 giờ đến 11 giờ và 20 giờ đến 22 giờ."
    )


def test_similar_task_time() -> None:
    use_case, *_, sources = setup(pick("similar_task_time", name="viết bài blog"))

    turn = ask(use_case)

    assert sources.called == ["similar:11"]
    assert turn.reply == (
        "Có 2 việc giống Viết bài blog tháng 11 trước đây, thường mất khoảng 1,5 giờ. "
        "Thường lâu gấp 1,4 lần dự kiến."
    )


def test_project_progress() -> None:
    progress = {
        SPA.id: ProjectProgress("App đặt lịch spa", date(2026, 10, 31), 8, 3, 37.5)
    }
    use_case, *_, backend, _ = setup(
        pick("project_progress", name="app spa"),
        backend=FakeBackendReader(progress=progress),
    )

    turn = ask(use_case)

    assert (
        turn.reply
        == "Dự án App đặt lịch spa đã xong 3/8 việc, khoảng 38%. Hạn là ngày mai."
    )
    assert backend.calls == [("project_progress", AUTH)]


def test_list_projects_only_active() -> None:
    projects = [
        ProjectSummary(LANDING_A.id, "Landing A", "Active"),
        ProjectSummary(SPA.id, "Spa", "Completed"),
    ]
    use_case, *_ = setup(
        pick("list_projects"), backend=FakeBackendReader(projects=projects)
    )

    assert ask(use_case).reply == "Bạn đang làm 1 dự án: Landing A."


def test_ai_quota_counts_this_call() -> None:
    use_case, *_ = setup(pick("ai_quota"), calls_used=10, limit=30)

    assert ask(use_case).reply == "Bạn còn 19 lượt dùng AI trong kỳ này."


def test_ai_quota_without_limit() -> None:
    use_case, *_ = setup(pick("ai_quota"), limit=None)

    assert ask(use_case, lang=Language.EN).reply == "Your plan has unlimited AI use."


# --- T4: the 4 read tools added after phase 1 ---


def test_pending_confirmations() -> None:
    backend = FakeBackendReader(pending=[PendingSession("Viết blog", 95)])
    use_case, *_ = setup(pick("pending_confirmations"), backend=backend)

    turn = ask(use_case, "Có phiên nào cần mình xác nhận không?")

    assert (turn.tool, turn.emotion) == (
        ToolName.PENDING_CONFIRMATIONS,
        Emotion.NEUTRAL,
    )
    assert turn.reply.startswith("Có 1 phiên bấm giờ cần bạn xác nhận: Viết blog")
    assert backend.calls == [("pending_confirmations", AUTH)]


def test_no_pending_confirmations_is_good_news() -> None:
    use_case, *_ = setup(pick("pending_confirmations"))

    assert ask(use_case).emotion is Emotion.HAPPY


def test_unread_notifications() -> None:
    backend = FakeBackendReader(notices=[UnreadNotice("Sắp hết giờ")])
    use_case, *_ = setup(pick("unread_notifications"), backend=backend)

    turn = ask(use_case)

    assert turn.tool is ToolName.UNREAD_NOTIFICATIONS
    assert turn.reply == "Bạn có 1 thông báo mới: Sắp hết giờ."
    assert backend.calls == [("unread_notifications", AUTH)]


def test_pending_proposal() -> None:
    proposal = PendingProposal((at(9) + timedelta(days=1),), False, 45)
    backend = FakeBackendReader(proposal=proposal)
    use_case, *_ = setup(pick("pending_proposal"), backend=backend)

    turn = ask(use_case)

    assert (turn.tool, turn.emotion) == (ToolName.PENDING_PROPOSAL, Emotion.NEUTRAL)
    assert turn.reply.startswith(
        "Có lịch đề xuất 1 khung giờ đang chờ duyệt, bắt đầu lúc 9 giờ ngày mai."
    )
    assert backend.calls == [("pending_proposal", AUTH)]


def test_no_pending_proposal() -> None:
    use_case, *_ = setup(pick("pending_proposal"))

    assert ask(use_case).reply == "Bạn không có lịch đề xuất nào đang chờ duyệt."


MILESTONE_PROJECTS = [
    ProjectSummary(LANDING_A.id, LANDING_A.name, "Active"),
    ProjectSummary(SPA.id, SPA.name, "Active"),
    ProjectSummary(LANDING_B.id, LANDING_B.name, "Completed"),
]
MILESTONE_LISTS = {
    LANDING_A.id: [
        MilestoneSummary("Bàn giao A", date(2026, 11, 2), "InProgress"),
        MilestoneSummary("Thiết kế A", date(2026, 10, 20), "Approved"),
    ],
    SPA.id: [
        MilestoneSummary("Thiết kế spa", date(2026, 10, 28), "InRevision"),
        MilestoneSummary("Không hạn", None, "Planned"),
    ],
    LANDING_B.id: [MilestoneSummary("Cũ", date(2026, 10, 1), "Planned")],
}


def test_upcoming_milestones_of_all_active_projects() -> None:
    backend = FakeBackendReader(projects=MILESTONE_PROJECTS, milestones=MILESTONE_LISTS)
    use_case, *_ = setup(pick("upcoming_milestones"), backend=backend)

    turn = ask(use_case, "Milestone nào sắp tới hạn?")

    # Approved, undated and inactive-project milestones are left out; oldest first.
    assert turn.reply == (
        "Có 2 milestone chưa xong: Thiết kế spa (đã quá hạn) và Bàn giao A (hạn ngày 2/11)."
    )
    assert (turn.tool, turn.emotion) == (ToolName.UPCOMING_MILESTONES, Emotion.NEUTRAL)
    assert backend.calls.count(("milestones", AUTH)) == 2


def test_upcoming_milestones_of_one_named_project() -> None:
    backend = FakeBackendReader(projects=MILESTONE_PROJECTS, milestones=MILESTONE_LISTS)
    use_case, *_ = setup(
        pick("upcoming_milestones", name="landing page"), backend=backend
    )

    turn = ask(use_case)

    # Landing page B is not active, so "landing page" matches only A.
    assert turn.reply == (
        "Dự án Landing page Client A có 1 milestone chưa xong: Bàn giao A (hạn ngày 2/11)."
    )
    assert turn.emotion is Emotion.HAPPY
    assert backend.calls.count(("milestones", AUTH)) == 1


def test_upcoming_milestones_unknown_project() -> None:
    backend = FakeBackendReader(projects=MILESTONE_PROJECTS, milestones=MILESTONE_LISTS)
    use_case, *_ = setup(pick("upcoming_milestones", name="logo"), backend=backend)

    turn = ask(use_case)

    assert turn.reply == "Mình không tìm thấy dự án nào tên logo."
    assert ("milestones", AUTH) not in backend.calls


def test_upcoming_milestones_skip_a_project_the_backend_hides() -> None:
    lists = {SPA.id: MILESTONE_LISTS[SPA.id]}  # Landing A answers 404
    backend = FakeBackendReader(projects=MILESTONE_PROJECTS, milestones=lists)
    use_case, *_ = setup(pick("upcoming_milestones"), backend=backend)

    assert ask(use_case).reply == (
        "Có 1 milestone chưa xong: Thiết kế spa (đã quá hạn)."
    )


# --- AC6: names ---


def test_two_close_projects_ask_the_user() -> None:
    use_case, *_, backend, _ = setup(pick("project_progress", name="landing page"))

    turn = ask(use_case)

    assert (turn.kind, turn.emotion) == (TurnKind.CLARIFY, Emotion.THINKING)
    assert [o.id for o in turn.options] == [LANDING_A.id, LANDING_B.id]
    assert (
        turn.reply
        == "Bạn muốn hỏi dự án nào: Landing page Client A hay Landing page Client B?"
    )
    assert backend.calls == []


def test_selected_option_skips_matching() -> None:
    progress = {LANDING_B.id: ProjectProgress("Landing page Client B", None, 2, 1, 50)}
    use_case, *_ = setup(
        pick("project_progress", name="landing page"),
        backend=FakeBackendReader(progress=progress),
    )

    turn = ask(use_case, selected=LANDING_B.id)

    assert turn.reply == "Dự án Landing page Client B đã xong 1/2 việc, khoảng 50%."


def test_selected_id_of_someone_else_is_not_found() -> None:
    use_case, *_ = setup(pick("project_progress", name="landing page"))

    turn = ask(use_case, selected=UUID(int=777))

    assert (turn.kind, turn.emotion) == (TurnKind.ANSWER, Emotion.SAD)
    assert turn.reply.startswith("Mình không tìm thấy dự án")


def test_unknown_or_missing_name() -> None:
    use_case, *_ = setup(
        pick("project_progress", name="báo cáo thuế"),
        pick("similar_task_time"),
        calls_used=0,
    )

    unknown = ask(use_case)
    missing = ask(use_case)

    assert (unknown.reply, unknown.emotion) == (
        "Mình không tìm thấy dự án nào tên báo cáo thuế.",
        Emotion.SAD,
    )
    assert (missing.kind, missing.reply) == (
        TurnKind.CLARIFY,
        "Bạn nói rõ tên việc giúp mình nhé.",
    )


def test_backend_404_for_the_project_is_not_found() -> None:
    use_case, *_ = setup(
        pick("project_progress", name="app spa"), backend=FakeBackendReader(progress={})
    )

    assert ask(use_case).emotion is Emotion.SAD


# --- T5: unsupported / low confidence / smalltalk call nothing ---


@pytest.mark.parametrize(
    "answer",
    [pick("unsupported"), json.dumps({"tool": "today_briefing", "confidence": 0.3})],
)
def test_unsupported_calls_no_source(answer: str) -> None:
    use_case, repo, quota, _, backend, sources = setup(answer)

    turn = ask(use_case, "Bắt đầu bấm giờ cho việc viết blog")

    assert (turn.kind, turn.tool, turn.emotion) == (
        TurnKind.UNSUPPORTED,
        None,
        Emotion.NEUTRAL,
    )
    assert turn.reply.startswith("Mình chưa làm được việc này.")
    assert len(turn.suggestions) == 3
    assert sources.called == [] and backend.calls == [] and repo.schedule_reads == []
    assert charged(quota) == 4  # the LLM ran: 1 call (#7)


def test_smalltalk() -> None:
    use_case, *_ = setup(
        pick("smalltalk", smalltalk="thanks"),
        pick("smalltalk", smalltalk="help"),
        calls_used=0,
    )

    thanks = ask(use_case)
    help_turn = ask(use_case)

    assert (thanks.kind, thanks.emotion, thanks.suggestions) == (
        TurnKind.SMALLTALK,
        Emotion.HAPPY,
        (),
    )
    assert len(help_turn.suggestions) == 3


# --- T6: bad LLM answer / failure → 502, not charged ---


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        pick("start_timer"),
        json.dumps({"tool": "today_briefing"}),
        LLMError("down", model="m"),
    ],
)
def test_bad_or_failed_llm_is_not_charged(answer) -> None:
    use_case, _, quota, llm, _, sources = setup(answer)

    with pytest.raises(AIUnavailableError):
        ask(use_case)

    assert charged(quota) == 3
    assert len(llm.prompts) == 1  # one call, no retry
    assert [log.quota_charged for _, _, log in quota.logs] == [False]
    assert sources.called == []


def test_good_answer_is_logged_as_chat_and_charged() -> None:
    use_case, _, quota, *_ = setup(pick("ai_quota"))

    ask(use_case)

    [(_, _, log)] = quota.logs
    assert (log.feature.value, log.succeeded, log.quota_charged) == ("chat", True, True)


# --- T7: a source fails → sorry reply, already charged ---


@pytest.mark.parametrize(
    ("answer", "backend"),
    [
        (pick("running_timer"), FakeBackendReader(focus=BackendReadError("HTTP 500"))),
        (
            pick("list_projects"),
            FakeBackendReader(projects=BackendReadError("ReadTimeout")),
        ),
        (
            pick("pending_confirmations"),
            FakeBackendReader(pending=BackendReadError("HTTP 500")),
        ),
        (
            pick("unread_notifications"),
            FakeBackendReader(notices=BackendReadError("HTTP 500")),
        ),
        (
            pick("pending_proposal"),
            FakeBackendReader(proposal=BackendReadError("ReadTimeout")),
        ),
        (
            pick("upcoming_milestones"),
            FakeBackendReader(
                projects=MILESTONE_PROJECTS,
                milestones=BackendReadError("HTTP 500"),
            ),
        ),
    ],
)
def test_source_error_says_sorry(answer, backend) -> None:
    use_case, _, quota, *_ = setup(answer, backend=backend)

    turn = ask(use_case)

    assert (turn.kind, turn.emotion) == (TurnKind.ANSWER, Emotion.SAD)
    assert turn.reply == "Mình chưa lấy được thông tin này, bạn thử lại sau nhé."
    assert charged(quota) == 4


# --- T8: masking ---


def test_prompt_masks_contacts_in_text_and_history() -> None:
    use_case, _, _, llm, *_ = setup(pick("today_briefing"))
    history = [
        HistoryLine("user", "gửi cho an@example.com"),
        HistoryLine("assistant", "Được"),
    ]

    ask(use_case, "gọi 0901234567 giúp tôi", history=history)

    prompt = llm.prompts[0]
    assert "an@example.com" not in prompt and "0901234567" not in prompt
    assert "[email]" in prompt and "[phone]" in prompt
    assert "2026-10-30 09:00 (Friday)" in prompt
    assert "user: gửi cho [email]" in prompt


def test_build_prompt_puts_the_question_last() -> None:
    prompt = build_prompt("Hôm nay làm gì?", [], NOW)

    assert prompt.rstrip().endswith("latest question: Hôm nay làm gì?")


# --- quota and user errors ---


def test_no_plan_and_no_calls_left() -> None:
    use_case, _, _, llm, *_ = setup(pick("ai_quota"), calls_used=30, limit=30)
    with pytest.raises(QuotaExceededError):
        ask(use_case)
    assert llm.prompts == []

    with pytest.raises(NoSubscriptionError):
        ask(use_case, user=UUID(int=2))


def test_unknown_user() -> None:
    use_case, _, quota, *_ = setup(pick("ai_quota"))
    quota.add_subscription(
        UUID(int=3), Subscription(UUID(int=98), 0, 30, 0), NOW - timedelta(days=1)
    )

    with pytest.raises(UserNotFoundError):
        ask(use_case, user=UUID(int=3))


def test_last_call_taken_meanwhile_is_429() -> None:
    use_case, _, quota, *_ = setup(pick("ai_quota"))
    quota.lose_next_charge = True

    with pytest.raises(QuotaExceededError):
        ask(use_case)
    assert [log.quota_charged for _, _, log in quota.logs] == [False]


def test_hour_ranges() -> None:
    assert hour_ranges([21, 9, 10, 20, 10]) == [(9, 11), (20, 22)]
    assert hour_ranges([]) == []
