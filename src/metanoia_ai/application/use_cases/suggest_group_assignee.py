"""FR-23: the Leader asks who should do a group task.

1. Read the task and check the caller is its group's Leader (404 / 403 / 409).
2. Window: now → the task's deadline (at most `window_days`), else now + window_days.
3. For each Leader / Member: personal busy time and unscheduled work, group work,
   their K for the chosen work category → free minutes, busy, needed minutes.
4. Rank with code (domain/assignee_ranking.py).
5. Quota of the Leader: calls left → the LLM writes reasons for the top members;
   charged only when it worked. No calls left → ranking without reasons.
Nothing is written to the group: the Leader assigns through the Backend.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from metanoia_ai.application.assignee_reason_writer import AssigneeReasonWriter
from metanoia_ai.application.group_capacity import member_candidate
from metanoia_ai.application.ports.group_assignee_repository import (
    GroupAssigneeRepository,
    GroupMember,
    GroupTaskInfo,
    GroupTaskStatus,
    GroupWork,
)
from metanoia_ai.application.ports.quota_repository import (
    AIFeature,
    QuotaRepository,
    Subscription,
    UsageLogEntry,
)
from metanoia_ai.application.ports.schedule_repository import (
    PlanningData,
    ScheduleRepository,
    TaskStatus,
)
from metanoia_ai.application.reason_writer import LLMCall
from metanoia_ai.domain.assignee_ranking import (
    Candidate,
    GroupRole,
    RankedCandidate,
    rank_candidates,
    reason_facts,
)
from metanoia_ai.domain.free_time import DailyBreak
from metanoia_ai.domain.memory_text import mask_contact
from metanoia_ai.domain.models import Language, TimeRange

OPEN = {GroupTaskStatus.TODO, GroupTaskStatus.IN_PROGRESS}
OPEN_PERSONAL = {TaskStatus.TODO, TaskStatus.SCHEDULED, TaskStatus.IN_PROGRESS}


class GroupTaskNotFoundError(Exception):
    """No such task, or the caller is not in its group (404, BR-131)."""


class NotGroupLeaderError(Exception):
    """Only a Leader may ask (403, BR-151)."""


class GroupArchivedError(Exception):
    """An archived group is read-only (409, BR-140)."""


class GroupTaskClosedError(Exception):
    """The task is Done or Skipped (409)."""


class ReasonSkip(StrEnum):
    """Why the suggestions come without reasons. Values are sent in the API."""

    QUOTA = "quota"  # no plan or no AI calls left
    LLM_FAILED = "llm_failed"


@dataclass(frozen=True)
class Suggestion:
    user_id: UUID
    role: GroupRole
    rank: int
    is_current_assignee: bool
    busy_at_fixed_time: bool
    free_minutes: int
    group_work_minutes: int
    fits: bool | None
    reason: str | None
    free_times: tuple[TimeRange, ...] = ()  # longest gaps, in the member's clock


@dataclass(frozen=True)
class SuggestionResult:
    task_id: UUID
    suggestions: tuple[Suggestion, ...]
    reasons_skipped: ReasonSkip | None


class _LostLastCallError(Exception):
    """Another request used the last call while this one was running."""


class SuggestGroupAssignee:
    def __init__(
        self,
        group_repo: GroupAssigneeRepository,
        schedule_repo: ScheduleRepository,
        quota_repo: QuotaRepository,
        reason_writer: AssigneeReasonWriter,
        *,
        initial_k: Decimal,
        window_days: int,
        max_candidates: int,
        reason_count: int,
        clock: Callable[[], datetime],
        breaks: Sequence[DailyBreak] = (),
    ) -> None:
        self.groups = group_repo
        self.schedules = schedule_repo
        self.quota = quota_repo
        self.reason_writer = reason_writer
        self.initial_k = initial_k
        self.window_days = window_days
        self.max_candidates = max_candidates
        self.reason_count = reason_count
        self.clock = clock
        self.breaks = tuple(breaks)

    async def execute(
        self,
        caller_id: UUID,
        task_id: UUID,
        domain_id: UUID | None,
        language: Language,
    ) -> SuggestionResult:
        now = self.clock()
        task = await self._check_access(caller_id, task_id)
        window_end = self._window_end(task, now)

        members = await self.groups.get_members(task.group_id, self.max_candidates)
        work = await self.groups.get_group_work(
            [m.user_id for m in members], now, window_end, task.task_id
        )
        candidates = []
        for member in members:
            planning = await self.schedules.get_planning_data(
                member.user_id, now, window_end
            )
            candidates.append(
                self._candidate(
                    member,
                    task,
                    planning,
                    work.get(member.user_id, GroupWork((), 0)),
                    domain_id,
                    now,
                    window_end,
                )
            )
        ranked = rank_candidates(candidates)
        reasons, skipped = await self._reasons(caller_id, task, ranked, language, now)
        return SuggestionResult(
            task_id=task.task_id,
            suggestions=tuple(
                Suggestion(
                    user_id=r.candidate.user_id,
                    role=r.candidate.role,
                    rank=r.rank,
                    is_current_assignee=r.candidate.is_current_assignee,
                    busy_at_fixed_time=r.candidate.busy_at_fixed_time,
                    free_minutes=r.candidate.free_minutes,
                    group_work_minutes=r.candidate.group_work_minutes,
                    fits=r.fits,
                    reason=reasons.get(r.candidate.user_id),
                    free_times=tuple(
                        TimeRange(
                            t.start.astimezone(r.candidate.time_zone),
                            t.end.astimezone(r.candidate.time_zone),
                        )
                        for t in r.candidate.free_times
                    ),
                )
                for r in ranked
            ),
            reasons_skipped=skipped,
        )

    # --- 1-2. Access and window ---

    async def _check_access(self, caller_id: UUID, task_id: UUID) -> GroupTaskInfo:
        task = await self.groups.get_task(task_id, caller_id)
        if task is None or task.caller_role is None:
            raise GroupTaskNotFoundError(str(task_id))
        if task.caller_role is not GroupRole.LEADER:
            raise NotGroupLeaderError(str(task_id))
        if task.group_archived:
            raise GroupArchivedError(str(task.group_id))
        if task.status not in OPEN:
            raise GroupTaskClosedError(str(task_id))
        return task

    def _window_end(self, task: GroupTaskInfo, now: datetime) -> datetime:
        longest = now + timedelta(days=self.window_days)
        if task.deadline is not None and now < task.deadline < longest:
            return task.deadline
        return longest

    # --- 3. One member ---

    def _candidate(
        self,
        member: GroupMember,
        task: GroupTaskInfo,
        planning: PlanningData,
        work: GroupWork,
        domain_id: UUID | None,
        now: datetime,
        window_end: datetime,
    ) -> Candidate:
        return member_candidate(
            member,
            planning,
            work,
            now=now,
            window_end=window_end,
            initial_k=self.initial_k,
            breaks=self.breaks,
            estimate_minutes=task.estimate_minutes,
            domain_id=domain_id,
            fixed_time=task.fixed_time,
            assignee_id=task.assignee_id,
        )

    # --- 5. Reasons and quota ---

    async def _reasons(
        self,
        caller_id: UUID,
        task: GroupTaskInfo,
        ranked: list[RankedCandidate],
        language: Language,
        now: datetime,
    ) -> tuple[dict[UUID, str], ReasonSkip | None]:
        if not ranked:
            return {}, None
        subscription = await self.quota.get_subscription(caller_id, now)
        if subscription is None or not subscription.has_calls_left:
            return {}, ReasonSkip.QUOTA

        facts = reason_facts(ranked, language, task.deadline, now)
        top = {r.candidate.user_id: facts[r.candidate.user_id] for r in ranked}
        top = dict(list(top.items())[: self.reason_count])
        title = mask_contact(task.title, None)
        result = await self.reason_writer.write(title, top, language)
        try:
            async with self.groups.transaction():
                charged = result.succeeded and await self._charge(subscription)
                await self._log(subscription, result.calls, charged)
        except _LostLastCallError:
            async with self.groups.transaction():
                await self._log(subscription, result.calls, charged=False)
            return {}, ReasonSkip.QUOTA
        if not result.succeeded:
            return {}, ReasonSkip.LLM_FAILED
        return result.reasons or {}, None

    async def _charge(self, subscription: Subscription) -> bool:
        if not await self.quota.try_charge(subscription):
            raise _LostLastCallError
        return True

    async def _log(
        self, subscription: Subscription, calls: Sequence[LLMCall], charged: bool
    ) -> None:
        for call in calls:
            await self.quota.log_usage(
                UsageLogEntry(
                    subscription_id=subscription.subscription_id,
                    feature=AIFeature.SCHEDULE,  # DB CHECK has no group value (plan #2)
                    model=call.model,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cost=call.cost,
                    latency_ms=call.latency_ms,
                    succeeded=call.succeeded,
                    quota_charged=charged and call.succeeded,
                )
            )
