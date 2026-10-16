"""FR-17: when a task is done, update the user's K_bias for its work domain."""

from dataclasses import dataclass
from uuid import UUID

from metanoia_ai.application.ports.learning_repository import (
    KBiasLogEntry,
    LearningRepository,
    TaskAlreadyLearnedError,
    TaskForLearning,
)
from metanoia_ai.domain.actual_time import (
    ActualTime,
    confidence_tier,
    summarize_actual_time,
)
from metanoia_ai.domain.kbias import skip_reason, update_k_bias
from metanoia_ai.domain.models import EstimateSource, SkipReason, TaskType


class TaskNotFoundError(Exception):
    """The task does not exist, or it belongs to another user."""


@dataclass(frozen=True)
class LearningResult:
    """What happened: learned (with old/new K) or skipped (with a reason)."""

    learned: bool
    reason: SkipReason | None = None
    k_old: float | None = None
    k_new: float | None = None
    sample_index: int | None = None


def skipped(reason: SkipReason) -> LearningResult:
    return LearningResult(learned=False, reason=reason)


def already_learned(entry: KBiasLogEntry | None) -> LearningResult:
    """Repeat call: return the earlier result instead of learning twice."""
    if entry is None:
        return skipped(SkipReason.ALREADY_LEARNED)
    return LearningResult(
        learned=False,
        reason=SkipReason.ALREADY_LEARNED,
        k_old=entry.old_k,
        k_new=entry.new_k,
        sample_index=entry.sample_index,
    )


class RecordTaskCompletion:
    """Learn from one finished task. No LLM call, no quota used."""

    def __init__(
        self,
        repo: LearningRepository,
        alpha: float,
        initial_k: float,
        outlier_ratio: float,
    ) -> None:
        self.repo = repo
        self.alpha = alpha
        self.initial_k = initial_k
        self.outlier_ratio = outlier_ratio

    async def execute(self, user_id: UUID, task_id: UUID) -> LearningResult:
        """Check the task, then update K_bias and write the log in one transaction."""
        try:
            async with self.repo.transaction():
                return await self._learn(user_id, task_id)
        except TaskAlreadyLearnedError:
            # Another request learned this task at the same moment.
            return already_learned(await self.repo.get_log(user_id, task_id))

    async def _learn(self, user_id: UUID, task_id: UUID) -> LearningResult:
        task = await self.repo.get_task(user_id, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))

        previous = await self.repo.get_log(user_id, task_id)
        if previous is not None:
            return already_learned(previous)
        if task.task_type is TaskType.FIXED:
            return skipped(SkipReason.FIXED_TASK)
        if not task.is_done:
            return skipped(SkipReason.TASK_NOT_DONE)
        if task.estimate_source is EstimateSource.AI:
            # K measures the user's own estimates, not the AI's (FR-17b).
            return skipped(SkipReason.AI_ESTIMATE)

        actual = summarize_actual_time(task.sessions)
        if isinstance(actual, SkipReason):
            return skipped(actual)

        t_est = self._estimate(task)
        reason = skip_reason(task.task_type, t_est, actual.minutes, self.outlier_ratio)
        if reason is not None:
            return skipped(reason)

        entry = await self._build_entry(user_id, task, t_est, actual)
        await self.repo.save(user_id, task.domain_id, entry)
        return LearningResult(
            learned=True,
            k_old=entry.old_k,
            k_new=entry.new_k,
            sample_index=entry.sample_index,
        )

    @staticmethod
    def _estimate(task: TaskForLearning) -> int:
        """T_est of a Flexible task. The DB requires it, so None means bad data."""
        if task.estimated_minutes is None:
            raise ValueError(f"Flexible task {task.task_id} has no estimate")
        return task.estimated_minutes

    async def _build_entry(
        self, user_id: UUID, task: TaskForLearning, t_est: int, actual: ActualTime
    ) -> KBiasLogEntry:
        """Lock the profile (created with the initial K if new) and compute the new K."""
        profile = await self.repo.lock_profile(user_id, task.domain_id, self.initial_k)
        k_old = profile.k_bias
        # The DB keeps 3 decimals; round here so the API returns what is stored.
        k_new = round(update_k_bias(k_old, t_est, actual.minutes, self.alpha), 3)
        return KBiasLogEntry(
            task_id=task.task_id,
            old_k=k_old,
            new_k=k_new,
            alpha=self.alpha,
            estimated_minutes=t_est,
            actual_minutes=actual.minutes,
            observed_ratio=round(actual.minutes / t_est, 3),
            source=actual.source,
            confidence=confidence_tier(actual.source),
            sample_index=profile.sample_count + 1,
        )
