"""FR-22 phase 1: one spoken question → one read-only tool → one spoken reply.

0. FR-22b: the local router tries first (no LLM). A sure pick runs the tool at once:
   no quota check, nothing charged, no AIUsageLog — so familiar questions still work
   when the plan or Gemini's free tier is used up. Anything unclear goes on below.
1. Quota check (nothing charged yet) and the user's time zone.
2. One LLM call picks the tool and copies words (#1, #2). No retry (plan §5).
   Bad answer or failure → AIUnavailableError (502), not charged (AC4).
3. Good answer → charge 1 call + AIUsageLog (Feature = Chat, #9), even for
   smalltalk or "unsupported" (#7).
4. Run the tool: AI-owned data through the use cases, Backend-owned data with GET
   only, the schedule from the DB like FR-12 (plan §5, D). Nothing else is written (AC3).
5. Reply from templates (vi / en) with an emotion for the 3D character.

The spoken text is masked before the prompt and never logged (#11).
"""

import asyncio
import json
import logging
import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from string import Template
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.llm_chat import ChatQuota, CheckedLLMCaller
from metanoia_ai.application.ports.assistant_repository import AssistantRepository
from metanoia_ai.application.ports.backend_reader import (
    BackendNotFoundError,
    BackendReader,
    MilestoneSummary,
)
from metanoia_ai.application.ports.quota_repository import (
    QuotaRepository,
    Subscription,
)
from metanoia_ai.application.reason_writer import BadAnswerError, LLMCall
from metanoia_ai.application.use_cases.find_similar_tasks import SimilarTasksResult
from metanoia_ai.application.use_cases.get_daily_briefing import Briefing
from metanoia_ai.application.use_cases.propose_schedule import QuotaExceededError
from metanoia_ai.application.use_cases.recompute_productive_hours import (
    UserNotFoundError,
)
from metanoia_ai.application.use_cases.send_task_chat_message import (
    AIUnavailableError,
)
from metanoia_ai.domain import assistant_text as say
from metanoia_ai.domain.assistant_text import DueMilestone, Emotion, TimedLine
from metanoia_ai.domain.assistant_tools import (
    NAMED_TOOLS,
    BadToolChoiceError,
    SmalltalkKind,
    ToolChoice,
    ToolName,
    parse_choice,
    router_schema,
)
from metanoia_ai.domain.briefing import schedule_between
from metanoia_ai.domain.local_router import LocalRouter, LocalSettings, local_choice
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.name_matching import MatchKind, NamedItem, match_name
from metanoia_ai.domain.similar_tasks import SimilarOutcome
from metanoia_ai.domain.spoken_period import SpokenPeriod, period_range
from metanoia_ai.prompts import load_prompt

logger = logging.getLogger(__name__)

PROMPT_VERSION = "assistant_router_v3"  # v3: 4 more read tools
NOW_FORMAT = "%Y-%m-%d %H:%M"
ACTIVE_PROJECT = "Active"
# Milestones still to deliver; Delivered / Approved ones are done for the user.
OPEN_MILESTONES = frozenset({"Planned", "InProgress", "InRevision"})

# Reads of AI-owned data, built in deps.py from the existing use cases.
BriefingReader = Callable[
    [UUID, Language], Awaitable[Briefing]
]  # FR-12, no FR-09 search
HabitsReader = Callable[[UUID, Language], Awaitable[Sequence[str]]]  # FR-20 insights
HoursReader = Callable[[UUID], Awaitable[Sequence[int]]]  # FR-18 top hours
SimilarReader = Callable[[UUID, UUID], Awaitable[SimilarTasksResult]]  # FR-09


class TurnKind(StrEnum):
    """Sent in the API."""

    ANSWER = "answer"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    SMALLTALK = "smalltalk"


@dataclass(frozen=True)
class HistoryLine:
    role: str  # "user" / "assistant"
    text: str


@dataclass(frozen=True)
class TurnOption:
    id: UUID
    title: str


@dataclass(frozen=True)
class Turn:
    kind: TurnKind
    tool: ToolName | None
    reply: str
    emotion: Emotion
    options: tuple[TurnOption, ...] = ()
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssistantSettings:
    min_confidence: float
    match_min_score: float
    match_min_gap: float
    local: LocalSettings | None = None  # FR-22b; None: always ask the LLM


@dataclass(frozen=True)
class TurnRequest:
    user_id: UUID
    text: str
    language: Language
    history: tuple[HistoryLine, ...]
    auth_header: str  # the user's own token, passed on to the Backend; never logged
    selected_id: UUID | None = None  # a pick from the previous "clarify" turn


def check_answer(text: str, min_confidence: float) -> ToolChoice:
    """LLM text → ToolChoice, or BadAnswerError (no retry here: AIUnavailableError)."""
    try:
        return parse_choice(json.loads(text), min_confidence)
    except (ValueError, BadToolChoiceError) as error:
        raise BadAnswerError(f"bad tool choice: {type(error).__name__}") from error


def build_prompt(text: str, history: Sequence[HistoryLine], now: datetime) -> str:
    """Rules + tools + local time + masked earlier turns + the masked question (#11)."""
    lines = [f"{h.role}: {mask_contact(h.text, None)}" for h in history]
    lines.append(f"latest question: {mask_contact(text, None)}")
    return Template(load_prompt(PROMPT_VERSION)).substitute(
        now=now.strftime(NOW_FORMAT), weekday=now.strftime("%A"), data="\n".join(lines)
    )


def hour_ranges(hours: Sequence[int]) -> list[tuple[int, int]]:
    """Top hours (any order) → [start, end) ranges of back-to-back hours, in time order."""
    ranges: list[tuple[int, int]] = []
    for hour in sorted(set(hours)):
        if ranges and ranges[-1][1] == hour:
            ranges[-1] = (ranges[-1][0], hour + 1)
        else:
            ranges.append((hour, hour + 1))
    return ranges


class _LostLastCallError(Exception):
    """Another request took the last AI call between the check and the charge."""


class AssistantTurn:
    def __init__(
        self,
        repo: AssistantRepository,
        quota_repo: QuotaRepository,
        caller: CheckedLLMCaller,  # built with max_retries = 0
        backend: BackendReader,
        *,
        briefing: BriefingReader,
        habits: HabitsReader,
        hours: HoursReader,
        similar: SimilarReader,
        settings: AssistantSettings,
        now: Callable[[], datetime],
        router: LocalRouter | None = None,
    ) -> None:
        self.router = router
        self.repo = repo
        self.quota_repo = quota_repo
        self.quota = ChatQuota(quota_repo, now=now)
        self.caller = caller
        self.backend = backend
        self.briefing = briefing
        self.habits = habits
        self.hours = hours
        self.similar = similar
        self.settings = settings
        self.now = now

    async def execute(self, request: TurnRequest) -> Turn:
        zone = await self.repo.get_time_zone(request.user_id)
        if zone is None:
            raise UserNotFoundError(str(request.user_id))
        now = self.now().astimezone(zone)

        local = await self._local_choice(request)
        if local is not None:
            return await self._run(
                local, request, now, zone, await self._calls_left(request.user_id)
            )

        subscription = await self.quota.check(request.user_id)

        result = await self.caller.call(
            build_prompt(request.text, request.history, now),
            router_schema(),
            lambda text: check_answer(text, self.settings.min_confidence),
        )
        if result.value is None:
            await self._log(subscription, result.calls, charged=False)
            raise AIUnavailableError("assistant tool choice failed")
        await self._charge(subscription, result.calls)

        left = None
        if subscription.ai_call_limit is not None:
            left = subscription.ai_call_limit - subscription.ai_calls_used - 1
        return await self._run(result.value, request, now, zone, left)

    # --- FR-22b: local router ---

    async def _local_choice(self, request: TurnRequest) -> ToolChoice | None:
        """A sure pick without the LLM, or None. A spoken name must match one item."""
        if self.router is None or self.settings.local is None:
            return None
        choice = local_choice(
            self.router, request.text, bool(request.history), self.settings.local
        )
        if choice is None or choice.tool not in NAMED_TOOLS:
            return choice
        if choice.name is None or request.selected_id is not None:
            return choice  # milestones of every project; or the user picked from a list
        items = (
            await self.repo.open_tasks(request.user_id)
            if choice.tool is ToolName.SIMILAR_TASK_TIME
            else await self.repo.projects(request.user_id)
        )
        match = match_name(
            choice.name,
            items,
            min_score=self.settings.match_min_score,
            min_gap=self.settings.match_min_gap,
        )
        if match.kind is not MatchKind.FOUND or match.found is None:
            return None  # the leftover words may not be a name: the LLM decides
        return ToolChoice(choice.tool, choice.confidence, name=match.found.name)

    async def _calls_left(self, user_id: UUID) -> int | None:
        """For ai_quota on a local turn: nothing is charged, so this is what is left now."""
        subscription = await self.quota_repo.get_subscription(user_id, self.now())
        if subscription is None:
            return 0  # no plan: no AI use at all, not "unlimited"
        if subscription.ai_call_limit is None:
            return None
        return max(subscription.ai_call_limit - subscription.ai_calls_used, 0)

    # --- quota ---

    async def _log(
        self, subscription: Subscription, calls: Sequence[LLMCall], charged: bool
    ) -> None:
        async with self.repo.transaction():
            await self.quota.log(subscription, calls, charged)

    async def _charge(
        self, subscription: Subscription, calls: Sequence[LLMCall]
    ) -> None:
        try:
            async with self.repo.transaction():
                if not await self.quota_repo.try_charge(subscription):
                    raise _LostLastCallError
                await self.quota.log(subscription, calls, charged=True)
        except _LostLastCallError:
            await self._log(subscription, calls, charged=False)
            raise QuotaExceededError("plan limit reached") from None

    # --- tools ---

    async def _run(
        self,
        choice: ToolChoice,
        request: TurnRequest,
        now: datetime,
        zone: ZoneInfo,
        calls_left: int | None,
    ) -> Turn:
        lang = request.language
        if choice.tool is ToolName.UNSUPPORTED:
            return Turn(
                TurnKind.UNSUPPORTED,
                None,
                say.unsupported_text(lang),
                Emotion.NEUTRAL,
                suggestions=say.suggestions(lang),
            )
        if choice.tool is ToolName.SMALLTALK:
            kind = choice.smalltalk or SmalltalkKind.HELP
            return Turn(
                TurnKind.SMALLTALK,
                None,
                say.smalltalk_text(kind, lang),
                Emotion.HAPPY,
                suggestions=say.suggestions(lang) if kind is SmalltalkKind.HELP else (),
            )
        try:
            return await self._read(choice, request, now, zone, calls_left)
        except Exception:
            # One source failing spoils only this answer (#13). The call is already charged.
            logger.exception("Assistant tool %s failed", choice.tool.value)
            return Turn(
                TurnKind.ANSWER, choice.tool, say.source_error_text(lang), Emotion.SAD
            )

    async def _read(
        self,
        choice: ToolChoice,
        request: TurnRequest,
        now: datetime,
        zone: ZoneInfo,
        calls_left: int | None,
    ) -> Turn:
        user, lang, tool = request.user_id, request.language, choice.tool

        def answer(reply: str, emotion: Emotion = Emotion.HAPPY) -> Turn:
            return Turn(TurnKind.ANSWER, tool, reply, emotion)

        match tool:
            case ToolName.TODAY_BRIEFING:
                b = await self.briefing(user, lang)
                reply = say.today_briefing_text(
                    len(b.schedule),
                    [t.title for t in b.top_tasks],
                    len(b.at_risk),
                    lang,
                )
                return answer(reply, Emotion.NEUTRAL if b.at_risk else Emotion.HAPPY)
            case ToolName.AT_RISK_DEADLINES:
                b = await self.briefing(user, lang)
                lines = [TimedLine(r.title, r.deadline) for r in b.at_risk]
                reply = say.at_risk_text(lines, now, zone, lang)
                return answer(reply, Emotion.NEUTRAL if lines else Emotion.HAPPY)
            case ToolName.SCHEDULE_FOR:
                period = choice.period or SpokenPeriod.TODAY
                window = period_range(period, now, zone)
                rows = await self.repo.schedule(user, window.start, window.end)
                schedule = schedule_between(
                    rows.slots, rows.fixed_tasks, rows.events, window, now, zone
                )
                lines = [TimedLine(i.title, i.start) for i in schedule.items]
                return answer(say.schedule_text(period, lines, zone, lang))
            case ToolName.RUNNING_TIMER:
                focus = await self.backend.current_focus(request.auth_header)
                if focus is None:
                    return answer(say.running_timer_text(None, 0, False, lang))
                reply = say.running_timer_text(
                    focus.task_title,
                    focus.elapsed_seconds // 60,
                    focus.is_overrun,
                    lang,
                )
                return answer(
                    reply, Emotion.NEUTRAL if focus.is_overrun else Emotion.HAPPY
                )
            case ToolName.ESTIMATION_HABITS:
                insights = await self.habits(user, lang)
                return answer(say.estimation_habits_text(insights, lang))
            case ToolName.PRODUCTIVE_HOURS:
                ranges = hour_ranges(await self.hours(user))
                emotion = Emotion.HAPPY if ranges else Emotion.NEUTRAL
                return answer(say.productive_hours_text(ranges, lang), emotion)
            case ToolName.SIMILAR_TASK_TIME:
                picked = await self._pick(
                    choice, request, await self.repo.open_tasks(user), "việc", "task"
                )
                if isinstance(picked, Turn):
                    return picked
                return answer(await self._similar_reply(user, picked, lang))
            case ToolName.PROJECT_PROGRESS:
                picked = await self._pick(
                    choice, request, await self.repo.projects(user), "dự án", "project"
                )
                if isinstance(picked, Turn):
                    return picked
                return await self._progress(picked, request, now, zone)
            case ToolName.LIST_PROJECTS:
                projects = await self.backend.projects(request.auth_header)
                names = [p.name for p in projects if p.status == ACTIVE_PROJECT]
                return answer(say.list_projects_text(names, lang))
            case ToolName.AI_QUOTA:
                return answer(say.ai_quota_text(calls_left, lang))
            case ToolName.PENDING_CONFIRMATIONS:
                sessions = await self.backend.pending_confirmations(request.auth_header)
                reply = say.pending_confirmations_text(
                    [(s.task_title, s.system_minutes) for s in sessions], lang
                )
                return answer(reply, Emotion.NEUTRAL if sessions else Emotion.HAPPY)
            case ToolName.UNREAD_NOTIFICATIONS:
                notices = await self.backend.unread_notifications(request.auth_header)
                return answer(
                    say.unread_notifications_text([n.title for n in notices], lang)
                )
            case ToolName.PENDING_PROPOSAL:
                proposal = await self.backend.pending_proposal(request.auth_header)
                if proposal is None:
                    return answer(
                        say.pending_proposal_text(None, True, None, now, zone, lang)
                    )
                reply = say.pending_proposal_text(
                    proposal.slot_starts,
                    proposal.is_feasible,
                    proposal.missing_minutes,
                    now,
                    zone,
                    lang,
                )
                emotion = Emotion.HAPPY if proposal.is_feasible else Emotion.NEUTRAL
                return answer(reply, emotion)
            case ToolName.UPCOMING_MILESTONES:
                return await self._milestones(choice, request, now, zone)
        raise ValueError(f"tool {tool} has no reader")  # pragma: no cover

    async def _pick(
        self,
        choice: ToolChoice,
        request: TurnRequest,
        items: Sequence[NamedItem],
        kind_vi: str,
        kind_en: str,
    ) -> NamedItem | Turn:
        """The task / project the user meant (#6), or a turn that asks or says not found."""
        lang, tool = request.language, choice.tool
        if request.selected_id is not None:
            for item in items:
                if item.id == request.selected_id:
                    return item
            reply = say.not_found_text(kind_vi, kind_en, choice.name, lang)
            return Turn(TurnKind.ANSWER, tool, reply, Emotion.SAD)
        if choice.name is None:
            reply = say.not_found_text(kind_vi, kind_en, None, lang)
            return Turn(TurnKind.CLARIFY, tool, reply, Emotion.THINKING)
        match = match_name(
            choice.name,
            items,
            min_score=self.settings.match_min_score,
            min_gap=self.settings.match_min_gap,
        )
        if match.kind is MatchKind.FOUND and match.found is not None:
            return match.found
        if match.kind is MatchKind.AMBIGUOUS:
            names = [o.name for o in match.options]
            return Turn(
                TurnKind.CLARIFY,
                tool,
                say.clarify_text(kind_vi, kind_en, names, lang),
                Emotion.THINKING,
                options=tuple(TurnOption(o.id, o.name) for o in match.options),
            )
        reply = say.not_found_text(kind_vi, kind_en, choice.name, lang)
        return Turn(TurnKind.ANSWER, tool, reply, Emotion.SAD)

    async def _similar_reply(self, user: UUID, task: NamedItem, lang: Language) -> str:
        result = await self.similar(user, task.id)
        done = [
            i.task.actual_minutes
            for i in result.items
            if i.task.outcome is SimilarOutcome.DONE and i.task.actual_minutes > 0
        ]
        typical = round(statistics.median(done)) if done else None
        return say.similar_task_text(
            task.name, len(done), typical, result.summary.average_ratio, lang
        )

    async def _progress(
        self, project: NamedItem, request: TurnRequest, now: datetime, zone: ZoneInfo
    ) -> Turn:
        lang = request.language
        try:
            progress = await self.backend.project_progress(
                project.id, request.auth_header
            )
        except BackendNotFoundError:
            reply = say.not_found_text("dự án", "project", project.name, lang)
            return Turn(TurnKind.ANSWER, ToolName.PROJECT_PROGRESS, reply, Emotion.SAD)
        deadline = (
            datetime.combine(progress.deadline, time(0), tzinfo=zone)
            if progress.deadline
            else None
        )
        reply = say.project_progress_text(
            progress.name,
            progress.completed_tasks,
            progress.total_tasks,
            progress.progress_percent,
            deadline,
            now,
            zone,
            lang,
        )
        return Turn(TurnKind.ANSWER, ToolName.PROJECT_PROGRESS, reply, Emotion.HAPPY)

    async def _milestones(
        self, choice: ToolChoice, request: TurnRequest, now: datetime, zone: ZoneInfo
    ) -> Turn:
        """Open milestones of all active projects, or of the one the user named."""
        lang, auth = request.language, request.auth_header
        projects = [
            p for p in await self.backend.projects(auth) if p.status == ACTIVE_PROJECT
        ]
        project_name = None
        if choice.name is not None or request.selected_id is not None:
            items = [NamedItem(p.id, p.name) for p in projects]
            picked = await self._pick(choice, request, items, "dự án", "project")
            if isinstance(picked, Turn):
                return picked
            projects = [p for p in projects if p.id == picked.id]
            project_name = picked.name
        lists = await asyncio.gather(
            *(self._project_milestones(p.id, auth) for p in projects)
        )
        due = sorted(
            (
                DueMilestone(m.name, datetime.combine(m.due_date, time(0), tzinfo=zone))
                for milestones in lists
                for m in milestones
                if m.status in OPEN_MILESTONES and m.due_date is not None
            ),
            key=lambda m: m.due,
        )
        overdue = any(m.due.date() < now.astimezone(zone).date() for m in due)
        return Turn(
            TurnKind.ANSWER,
            ToolName.UPCOMING_MILESTONES,
            say.upcoming_milestones_text(due, project_name, now, zone, lang),
            Emotion.NEUTRAL if overdue else Emotion.HAPPY,
        )

    async def _project_milestones(
        self, project_id: UUID, auth: str
    ) -> list[MilestoneSummary]:
        """A project the Backend says is not the user's counts as having none."""
        try:
            return await self.backend.milestones(project_id, auth)
        except BackendNotFoundError:
            return []
