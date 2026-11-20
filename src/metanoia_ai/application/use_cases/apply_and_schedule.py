"""FR-08b-7: "Áp dụng & xếp lịch" — Apply the draft, then propose a schedule for
the tasks just created, in one request.

Apply is saved first and stays saved: if the schedule cannot be made (no plan,
out of AI calls, an error), the answer still carries the created work plus a
short `schedule_error` code, and the user can press "Xếp lịch" later.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from metanoia_ai.application.use_cases.apply_project_breakdown import (
    AppliedProject,
    ApplyProjectBreakdown,
)
from metanoia_ai.application.use_cases.propose_schedule import (
    NoSubscriptionError,
    PlanFeatureError,
    ProposalResult,
    QuotaExceededError,
)
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.project_breakdown import MilestoneInput

logger = logging.getLogger(__name__)


class ScheduleError(StrEnum):
    """Why no schedule came with the applied work. Values are sent in the API."""

    QUOTA_EXCEEDED = "quota_exceeded"
    NO_ACTIVE_PLAN = "no_active_plan"
    PLAN_FEATURE_LOCKED = "plan_feature_locked"
    FAILED = "schedule_failed"


class Scheduler(Protocol):
    """What this use case needs from FR-10 (ProposeSchedule)."""

    async def execute(
        self, user_id: UUID, task_ids: Sequence[UUID] | None, language: Language
    ) -> ProposalResult: ...


@dataclass(frozen=True)
class AppliedAndScheduled:
    applied: AppliedProject
    schedule: ProposalResult | None  # None: nothing new to place, or an error
    schedule_error: ScheduleError | None = None


class ApplyAndSchedule:
    def __init__(self, apply: ApplyProjectBreakdown, scheduler: Scheduler) -> None:
        self.apply = apply
        self.scheduler = scheduler

    async def execute(
        self,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        language: Language,
        edited: Sequence[MilestoneInput] | None = None,
    ) -> AppliedAndScheduled:
        """Apply errors are raised as for /apply (nothing written, no schedule)."""
        applied = await self.apply.execute(user_id, conversation_id, message_id, edited)
        task_ids = [t.task_id for m in applied.milestones for t in m.tasks]
        if not task_ids:  # every task was a duplicate: nothing to place, no AI call
            return AppliedAndScheduled(applied, None)
        try:
            schedule = await self.scheduler.execute(user_id, task_ids, language)
        except QuotaExceededError:
            return AppliedAndScheduled(applied, None, ScheduleError.QUOTA_EXCEEDED)
        except NoSubscriptionError:
            return AppliedAndScheduled(applied, None, ScheduleError.NO_ACTIVE_PLAN)
        except PlanFeatureError:
            return AppliedAndScheduled(applied, None, ScheduleError.PLAN_FEATURE_LOCKED)
        except Exception:  # the work is saved; the user can schedule it later
            logger.exception("Schedule after Apply failed")
            return AppliedAndScheduled(applied, None, ScheduleError.FAILED)
        return AppliedAndScheduled(applied, schedule)
