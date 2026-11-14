"""FR-12: the daily "Today" briefing. Code picks and writes everything; no LLM.

1. Read all data once (user's time zone → today).
2. Today's schedule (approved / done slots, Fixed tasks, Google events).
3. Candidate tasks and the minutes they still need (Free plan: no × K).
4. Deadline risk counted together over the next days.
5. Top tasks + reasons (sentences from templates).
6. Similar task warnings (FR-09) for the top tasks, in parallel, with one time limit.
7. Productive hours left today.

No quota is used. FR-09 logs each embedding call in AIUsageLog itself.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from metanoia_ai.application.ports.briefing_repository import (
    BriefingData,
    BriefingRepository,
    BriefingTask,
)
from metanoia_ai.application.ports.schedule_repository import TaskStatus
from metanoia_ai.application.use_cases.find_similar_tasks import SimilarTasksResult
from metanoia_ai.domain.briefing import (
    BiasRule,
    Candidate,
    DeadlineRisk,
    ReasonCode,
    RiskTask,
    ScheduleItem,
    SimilarWarningKind,
    TopTask,
    at_risk_tasks,
    deadline_risks,
    needed_minutes,
    productive_hint,
    risk_free_ranges,
    select_top,
    similar_warning,
    today_range,
    todays_schedule,
)
from metanoia_ai.domain.briefing_text import (
    productive_hint_text,
    reason_text,
    similar_ratio_text,
    similar_skipped_text,
)
from metanoia_ai.domain.free_time import CalendarEvent, FixedTaskTime
from metanoia_ai.domain.models import Language

logger = logging.getLogger(__name__)

# FR-09 search for one task of the user. The real one opens its own DB session,
# so several can run at the same time (plan #12).
SimilarFinder = Callable[[UUID, UUID], Awaitable[SimilarTasksResult]]


class UserNotFoundError(Exception):
    """The user from the token does not exist (any more)."""


@dataclass(frozen=True)
class BriefingReason:
    code: ReasonCode
    text: str


@dataclass(frozen=True)
class BriefingTopTask:
    task_id: UUID
    title: str
    project_name: str
    deadline: datetime  # in the user's time zone
    remaining_minutes: int
    reasons: tuple[BriefingReason, ...]
    similar_warning: str | None  # None: no warning, or the search failed / was slow


@dataclass(frozen=True)
class BriefingAtRisk:
    task_id: UUID
    title: str
    deadline: datetime  # in the user's time zone
    missing_minutes: int


@dataclass(frozen=True)
class BriefingHint:
    ranges: tuple[tuple[int, int], ...]  # [start hour, end hour)
    text: str


@dataclass(frozen=True)
class Briefing:
    date: date
    time_zone: ZoneInfo
    calendar_connected: bool
    has_pending_proposal: bool
    schedule: tuple[ScheduleItem, ...]
    all_day: tuple[str, ...]
    top_tasks: tuple[BriefingTopTask, ...]
    at_risk: tuple[BriefingAtRisk, ...]
    productive_hint: BriefingHint | None


@dataclass(frozen=True)
class BriefingSettings:
    initial_k: Decimal
    top_count: int
    risk_window_days: int
    at_risk_limit: int
    bias_rule: BiasRule
    similar_min_count: int
    similar_min_ratio: float
    similar_timeout_seconds: float
    productive_top_hours: int
    round_minutes: int


OPEN_STATUSES = {TaskStatus.TODO, TaskStatus.SCHEDULED, TaskStatus.IN_PROGRESS}


class GetDailyBriefing:
    def __init__(
        self,
        repo: BriefingRepository,
        find_similar: SimilarFinder,
        settings: BriefingSettings,
        now: Callable[[], datetime],
    ) -> None:
        self.repo = repo
        self.find_similar = find_similar
        self.settings = settings
        self.now = now

    async def execute(self, user_id: UUID, language: Language) -> Briefing:
        now = self.now()
        data = await self._read(user_id, now)
        zone = data.hours.time_zone
        today = todays_schedule(data.slots, data.fixed_tasks, data.events, now, zone)

        candidates = self._candidates(data, now)
        risks = self._risks(data, candidates, now)
        top = select_top(
            candidates.values(),
            risks,
            now,
            zone,
            self.settings.top_count,
            self.settings.bias_rule,
        )
        warnings = await self._similar_warnings(user_id, top, language)

        return Briefing(
            date=today_range(now, zone).start.date(),
            time_zone=zone,
            calendar_connected=data.calendar_connected,
            has_pending_proposal=today.has_pending_proposal,
            schedule=today.items,
            all_day=today.all_day,
            top_tasks=tuple(
                self._top_task(t, warnings.get(t.candidate.task_id), zone, language)
                for t in top
            ),
            at_risk=tuple(
                BriefingAtRisk(
                    r.task_id,
                    candidates[r.task_id].title,
                    r.deadline.astimezone(zone),
                    r.missing_minutes,
                )
                for r in at_risk_tasks(risks, self.settings.at_risk_limit)
            ),
            productive_hint=self._hint(data, now, language),
        )

    # --- 1. Read ---

    async def _read(self, user_id: UUID, now: datetime) -> BriefingData:
        # The time zone is in the data, so the window is first built in UTC and
        # wide enough (±1 day) to cover any zone; the domain then cuts it exactly.
        rough_start = now - timedelta(days=1)
        rough_end = now + timedelta(days=1)
        window_end = now + timedelta(days=self.settings.risk_window_days)
        data = await self.repo.get_briefing_data(
            user_id, rough_start, rough_end, max(window_end, rough_end)
        )
        if data is None:
            raise UserNotFoundError(str(user_id))
        return data

    # --- 3. Candidates ---

    def _candidates(self, data: BriefingData, now: datetime) -> dict[UUID, Candidate]:
        result: dict[UUID, Candidate] = {}
        for task in data.tasks:
            if task.status not in OPEN_STATUSES:
                continue
            if task.deadline is None or task.estimated_minutes is None:
                logger.warning(
                    "Flexible task %s has no deadline or estimate", task.task_id
                )
                continue
            result[task.task_id] = self._candidate(task, data, now)
        return result

    def _candidate(
        self, task: BriefingTask, data: BriefingData, now: datetime
    ) -> Candidate:
        assert task.deadline is not None and task.estimated_minutes is not None
        profile = data.k_by_domain.get(task.domain_id)
        user_k = profile.k_bias if profile else self.settings.initial_k
        k = None if data.is_free_plan else user_k  # Free plan: K only as a hint (#8)
        return Candidate(
            task_id=task.task_id,
            title=task.title,
            project_name=task.project_name,
            domain_name=task.domain_name,
            deadline=task.deadline,
            needed_minutes=needed_minutes(
                task.estimated_minutes,
                task.open_subtasks,
                task.has_subtasks,
                task.sessions,
                k,
                now,
            ),
            in_progress=task.status is TaskStatus.IN_PROGRESS or bool(task.sessions),
            k_bias=profile.k_bias if profile else None,
            sample_count=profile.sample_count if profile else 0,
        )

    # --- 4. Risk ---

    def _risks(
        self, data: BriefingData, candidates: dict[UUID, Candidate], now: datetime
    ) -> list[DeadlineRisk]:
        window = self.settings.risk_window_days
        free = risk_free_ranges(
            data.hours,
            now,
            window,
            [FixedTaskTime(f.start, f.end, f.is_skipped) for f in data.fixed_tasks],
            [
                CalendarEvent(
                    e.start,
                    e.end,
                    e.is_all_day,
                    e.is_cancelled,
                    e.is_created_by_metanoia,
                )
                for e in data.events
            ],
            self.settings.round_minutes,
        )
        tasks = [
            RiskTask(c.task_id, c.deadline, c.needed_minutes)
            for c in candidates.values()
        ]
        return deadline_risks(tasks, free, now, window)

    # --- 5. Top task texts ---

    @staticmethod
    def _top_task(
        top: TopTask, warning: str | None, zone: ZoneInfo, language: Language
    ) -> BriefingTopTask:
        c = top.candidate
        return BriefingTopTask(
            task_id=c.task_id,
            title=c.title,
            project_name=c.project_name,
            deadline=c.deadline.astimezone(zone),
            remaining_minutes=c.needed_minutes,
            reasons=tuple(
                BriefingReason(r.code, reason_text(r, language)) for r in top.reasons
            ),
            similar_warning=warning,
        )

    # --- 6. Similar tasks ---

    async def _similar_warnings(
        self, user_id: UUID, top: Sequence[TopTask], language: Language
    ) -> dict[UUID, str]:
        """One search per top task, all at once, at most `similar_timeout_seconds` in total.

        A search that fails or is not done in time gives no warning (AC4).
        """
        if not top:
            return {}
        jobs = {
            asyncio.create_task(self.find_similar(user_id, t.candidate.task_id)): (
                t.candidate.task_id
            )
            for t in top
        }
        done, pending = await asyncio.wait(
            jobs, timeout=self.settings.similar_timeout_seconds
        )
        for job in pending:
            job.cancel()
        if pending:
            logger.warning("Similar task search too slow for %d task(s)", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)

        warnings: dict[UUID, str] = {}
        for job in done:
            task_id = jobs[job]
            if job.exception() is not None:
                logger.warning(
                    "Similar task search failed for task %s: %s",
                    task_id,
                    type(job.exception()).__name__,
                )
                continue
            if text := self._warning_text(job.result(), language):
                warnings[task_id] = text
        return warnings

    def _warning_text(
        self, result: SimilarTasksResult, language: Language
    ) -> str | None:
        warning = similar_warning(
            result.summary,
            self.settings.similar_min_count,
            self.settings.similar_min_ratio,
        )
        if warning is None:
            return None
        if warning.kind is SimilarWarningKind.SLOWER:
            assert warning.average_ratio is not None
            return similar_ratio_text(warning.average_ratio, language)
        return similar_skipped_text(language)

    # --- 7. Productive hours ---

    def _hint(
        self, data: BriefingData, now: datetime, language: Language
    ) -> BriefingHint | None:
        ranges = productive_hint(
            data.productive_scores,
            data.has_productive_answer,
            data.hours,
            now,
            self.settings.productive_top_hours,
        )
        if ranges is None:
            return None
        return BriefingHint(tuple(ranges), productive_hint_text(ranges, language))
