"""FR-10: the user clicks "Schedule" → propose slots for their Flexible tasks.

1. Plan + quota check (plan limit) → out of quota: error.
2. Read all data once.
3. Minutes to schedule per task / subtask.
4. Free time in the window.
5. Planner (pure code) → slots + warnings.
6. LLM writes one reason per slot (with retries; failure → no reasons).
7. Save in one transaction: old Pending → Superseded, new proposal + slots,
   charge 1 call (only if the LLM worked), one AIUsageLog row per LLM call.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

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
    ProposalWarning,
    ScheduleRepository,
    TaskStatus,
    TaskToSchedule,
)
from metanoia_ai.application.reason_writer import LLMCall, ReasonResult, ReasonWriter
from metanoia_ai.domain.free_time import (
    BusyBlock,
    busy_blocks,
    free_ranges,
    running_timer_busy,
)
from metanoia_ai.domain.models import (
    BusyKind,
    Language,
    PlannedSlot,
    ScheduleUnit,
    ScheduleWarning,
    TimeRange,
    WarningType,
)
from metanoia_ai.domain.planner import PlanResult, plan_schedule, top_productive_hours
from metanoia_ai.domain.schedule_duration import (
    minutes_to_schedule,
    remaining_estimate,
    sessions_of,
)
from metanoia_ai.domain.schedule_text import (
    SlotFacts,
    earlier_blocked_by,
    priority_ranks,
    slot_facts,
    summary,
    warning_message,
)

SCHEDULABLE = {TaskStatus.TODO, TaskStatus.IN_PROGRESS}


class NoSubscriptionError(Exception):
    """The user has no active plan. Bad data: every account starts on Free (FR-02)."""


class QuotaExceededError(Exception):
    """No AI calls left in the plan for this period (BR-86)."""


class _LostLastCallError(Exception):
    """Another request used the last call while this one was running."""


@dataclass(frozen=True)
class ProposedSlot:
    slot_id: UUID
    task_id: UUID
    subtask_id: UUID | None
    start: datetime
    end: datetime
    proposed_minutes: int
    explanation: str | None


@dataclass(frozen=True)
class ProposalResult:
    proposal_id: UUID | None  # None: nothing to schedule, nothing saved
    is_feasible: bool
    missing_minutes: int | None
    explanation: str
    slots: tuple[ProposedSlot, ...]
    warnings: tuple[ProposalWarning, ...]


@dataclass(frozen=True)
class UnitInfo:
    """What the reason facts need about a unit, besides its slot."""

    title: str
    deadline: datetime
    user_k: Decimal  # the user's K, also on the Free plan (then only a hint)
    estimate_minutes: int  # the user's own estimate of what is left
    planned_minutes: int  # all minutes kept for the unit


class ProposeSchedule:
    def __init__(
        self,
        schedule_repo: ScheduleRepository,
        quota_repo: QuotaRepository,
        reason_writer: ReasonWriter,
        *,
        initial_k: Decimal,
        window_days: int,
        min_chunk_minutes: int,
        round_minutes: int,
        top_productive_hours: int,
        clock: Callable[[], datetime],
    ) -> None:
        self.repo = schedule_repo
        self.quota = quota_repo
        self.reason_writer = reason_writer
        self.initial_k = initial_k
        self.window_days = window_days
        self.min_chunk_minutes = min_chunk_minutes
        self.round_minutes = round_minutes
        self.top_hours = top_productive_hours
        self.clock = clock

    async def execute(
        self, user_id: UUID, task_ids: Sequence[UUID] | None, language: Language
    ) -> ProposalResult:
        now = self.clock()
        subscription = await self._check_quota(user_id, now)

        window_end = now + timedelta(days=self.window_days)
        data = await self.repo.get_planning_data(user_id, now, window_end)
        units, infos, timers = self._units(data, task_ids, subscription, now)
        if not units:
            return ProposalResult(
                proposal_id=None,
                is_feasible=True,
                missing_minutes=None,
                explanation=summary(0, 0, [], language),
                slots=(),
                warnings=(),
            )

        blocks = busy_blocks(
            data.fixed_tasks, data.calendar_events, data.approved_slots, timers
        )
        plan = self._plan(data, units, blocks, now, window_end)
        warnings = list(plan.warnings)
        if not data.calendar_synced:
            warnings.append(ScheduleWarning(WarningType.CALENDAR_NOT_SYNCED))

        slot_ids = {slot: uuid4() for slot in plan.slots}
        facts = self._facts(plan.slots, slot_ids, infos, units, blocks, data, now)
        reasons = await self.reason_writer.write(facts, language)

        proposal = self._proposal(plan, slot_ids, units, warnings, reasons, language)
        await self._save(user_id, subscription, proposal, reasons.calls)
        zone = data.hours.time_zone  # times go back in the user's time zone
        return ProposalResult(
            proposal_id=proposal.proposal_id,
            is_feasible=proposal.is_feasible,
            missing_minutes=proposal.missing_minutes,
            explanation=proposal.explanation,
            slots=tuple(
                ProposedSlot(
                    s.slot_id,
                    s.task_id,
                    s.subtask_id,
                    s.start.astimezone(zone),
                    s.end.astimezone(zone),
                    s.proposed_minutes,
                    s.explanation,
                )
                for s in proposal.slots
            ),
            warnings=proposal.warnings,
        )

    # --- 1. Quota ---

    async def _check_quota(self, user_id: UUID, now: datetime) -> Subscription:
        subscription = await self.quota.get_subscription(user_id, now)
        if subscription is None:
            raise NoSubscriptionError(str(user_id))
        if not subscription.has_calls_left:
            raise QuotaExceededError("plan limit reached")
        return subscription

    # --- 3. Units to schedule ---

    def _units(
        self,
        data: PlanningData,
        task_ids: Sequence[UUID] | None,
        subscription: Subscription,
        now: datetime,
    ) -> tuple[
        list[ScheduleUnit], dict[tuple[UUID, UUID | None], UnitInfo], list[TimeRange]
    ]:
        """Units to place, facts info per unit, and busy time of running timers."""
        wanted = None if task_ids is None else set(task_ids)
        units: list[ScheduleUnit] = []
        infos: dict[tuple[UUID, UUID | None], UnitInfo] = {}
        timers: list[TimeRange] = []

        for task in data.tasks:
            user_k = data.k_by_domain.get(task.domain_id, self.initial_k)
            k = None if subscription.is_free else user_k
            if running := self._running_timer(task, k, now):
                timers.append(running)
                continue  # a task with a running timer is not scheduled
            if wanted is not None and task.task_id not in wanted:
                continue
            if task.status not in SCHEDULABLE or task.has_approved_slot:
                continue
            for unit in self._task_units(task, k, now):
                units.append(unit)
                infos[(unit.task_id, unit.subtask_id)] = UnitInfo(
                    unit.title,
                    unit.deadline,
                    user_k,
                    estimate_minutes=unit.estimate_minutes or unit.minutes,
                    planned_minutes=unit.minutes,
                )
        return units, infos, timers

    @staticmethod
    def _task_units(
        task: TaskToSchedule, k: Decimal | None, now: datetime
    ) -> list[ScheduleUnit]:
        """One unit per open subtask, or one unit for a task without subtasks."""
        if not task.subtasks:
            return [
                ScheduleUnit(
                    task.task_id,
                    None,
                    task.title,
                    task.deadline,
                    minutes_to_schedule(task.estimated_minutes, task.sessions, k, now),
                    k,
                    estimate_minutes=remaining_estimate(
                        task.estimated_minutes, task.sessions, now
                    ),
                )
            ]
        units = []
        for sub in task.subtasks:
            if not sub.is_open:
                continue
            sessions = sessions_of(task.sessions, sub.subtask_id)
            units.append(
                ScheduleUnit(
                    task_id=task.task_id,
                    subtask_id=sub.subtask_id,
                    title=f"{task.title}: {sub.title}",
                    deadline=task.deadline,
                    minutes=minutes_to_schedule(
                        sub.estimated_minutes, sessions, k, now
                    ),
                    k_bias_used=k,
                    order_index=sub.order_index,
                    estimate_minutes=remaining_estimate(
                        sub.estimated_minutes, sessions, now
                    ),
                )
            )
        return units

    @staticmethod
    def _running_timer(
        task: TaskToSchedule, k: Decimal | None, now: datetime
    ) -> TimeRange | None:
        """Busy time from now for a task whose timer is running (same rule as a started task)."""
        running = [s for s in task.sessions if s.ended_at is None]
        if not running:
            return None
        subtask_id = running[0].subtask_id
        estimate = task.estimated_minutes
        if subtask_id is not None:
            estimate = next(
                (
                    s.estimated_minutes
                    for s in task.subtasks
                    if s.subtask_id == subtask_id
                ),
                estimate,
            )
        minutes = minutes_to_schedule(
            estimate, sessions_of(task.sessions, subtask_id), k, now
        )
        return running_timer_busy(now, minutes)

    # --- 4-5. Free time and planner ---

    def _plan(
        self,
        data: PlanningData,
        units: list[ScheduleUnit],
        blocks: list[BusyBlock],
        now: datetime,
        window_end: datetime,
    ) -> PlanResult:
        busy = [b.time for b in blocks]
        free = free_ranges(data.hours, now, self.window_days, busy, self.round_minutes)
        return plan_schedule(
            units,
            free,
            data.hours,
            top_productive_hours(data.productive_scores, self.top_hours),
            now=now,
            window_end=window_end,
            round_minutes=self.round_minutes,
            min_chunk_minutes=self.min_chunk_minutes,
        )

    # --- 6. Facts for the LLM ---

    def _facts(
        self,
        slots: Sequence[PlannedSlot],
        slot_ids: dict[PlannedSlot, UUID],
        infos: dict[tuple[UUID, UUID | None], UnitInfo],
        units: Sequence[ScheduleUnit],
        blocks: Sequence[BusyBlock],
        data: PlanningData,
        now: datetime,
    ) -> dict[UUID, SlotFacts]:
        """Facts per slot, including why it starts when it does (plan #32)."""
        productive = top_productive_hours(data.productive_scores, self.top_hours)
        ranks = priority_ranks(units)
        by_task: dict[UUID, list[PlannedSlot]] = {}
        for slot in sorted(slots, key=lambda s: s.start):
            by_task.setdefault(slot.task_id, []).append(slot)

        result = {}
        for slot in slots:
            info = infos[(slot.task_id, slot.subtask_id)]
            own = by_task[slot.task_id]
            index = own.index(slot)
            # Soonest start: now, or the end of the previous part / subtask.
            earliest = own[index - 1].end if index else now
            others = [
                BusyBlock(TimeRange(s.start, s.end), BusyKind.OTHER_TASK)
                for s in slots
                if s.task_id != slot.task_id
            ]
            result[slot_ids[slot]] = slot_facts(
                slot,
                info.title,
                info.deadline,
                info.user_k,
                productive,
                data.hours.time_zone,
                blocked_by=earlier_blocked_by(
                    slot, earliest, [*blocks, *others], data.hours, self.round_minutes
                ),
                priority=(ranks[slot.task_id], len(ranks)),
                estimate_minutes=info.estimate_minutes,
                planned_minutes=info.planned_minutes,
                task_end=own[-1].end,
            )
        return result

    # --- 7. Save ---

    def _proposal(
        self,
        plan: PlanResult,
        slot_ids: dict[PlannedSlot, UUID],
        units: list[ScheduleUnit],
        warnings: list[ScheduleWarning],
        reasons: ReasonResult,
        language: Language,
    ) -> NewProposal:
        missing = missing_minutes(warnings, units)
        task_count = len({u.task_id for u in units})
        placed_count = len({s.task_id for s in plan.slots})
        texts = reasons.reasons or {}
        return NewProposal(
            proposal_id=uuid4(),
            is_feasible=missing == 0,
            missing_minutes=missing or None,  # DB: required (> 0) when not feasible
            explanation=summary(task_count, placed_count, warnings, language),
            warnings=tuple(
                ProposalWarning(w, warning_message(w, language)) for w in warnings
            ),
            slots=tuple(
                NewSlot(
                    slot_id=slot_ids[slot],
                    task_id=slot.task_id,
                    subtask_id=slot.subtask_id,
                    start=slot.start,
                    end=slot.end,
                    proposed_minutes=slot.proposed_minutes,
                    k_bias_used=slot.k_bias_used,
                    explanation=texts.get(slot_ids[slot]),
                )
                for slot in plan.slots
            ),
        )

    async def _save(
        self,
        user_id: UUID,
        subscription: Subscription,
        proposal: NewProposal,
        calls: Sequence[LLMCall],
    ) -> None:
        """Proposal, charge and usage logs in one transaction.

        The call is charged only when the LLM wrote the reasons. If another
        request used the last call meanwhile: nothing is saved, the LLM calls
        are still logged (not charged), and the user gets "out of quota".
        """
        llm_worked = any(call.succeeded for call in calls)
        try:
            async with self.repo.transaction():
                charged = llm_worked and await self._charge(subscription)
                await self._log(subscription, calls, charged)
                await self.repo.save_proposal(user_id, proposal)
        except _LostLastCallError:
            async with self.repo.transaction():
                await self._log(subscription, calls, charged=False)
            raise QuotaExceededError("plan limit reached") from None

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
                    feature=AIFeature.SCHEDULE,
                    model=call.model,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cost=call.cost,
                    latency_ms=call.latency_ms,
                    succeeded=call.succeeded,
                    quota_charged=charged and call.succeeded,
                )
            )


def missing_minutes(
    warnings: Sequence[ScheduleWarning], units: Sequence[ScheduleUnit]
) -> int:
    """Minutes short. 0 means feasible.

    - Not enough time: needed − free. If the free time is there but in pieces
      too small to use (free ≥ needed), the whole task counts as missing.
    - Overdue: all minutes of the task.
    """
    by_task: dict[UUID, int] = {}
    for unit in units:
        by_task[unit.task_id] = by_task.get(unit.task_id, 0) + unit.minutes
    total = 0
    for warning in warnings:
        if warning.type is WarningType.NOT_ENOUGH_TIME:
            needed = warning.needed_minutes or 0
            short = needed - (warning.free_minutes or 0)
            total += short if short > 0 else needed
        elif warning.type is WarningType.OVERDUE and warning.task_id is not None:
            total += by_task.get(warning.task_id, 0)
    return total
