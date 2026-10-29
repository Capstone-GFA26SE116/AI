"""Replay one (developer, domain) pair: predict first, learn after (plan §2.2)."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from metanoia_ai.domain.kbias import proposed_minutes, skip_reason
from metanoia_ai.domain.models import TaskType
from research.kbias_sip.load import SipTask, domain_of
from research.kbias_sip.methods import Method

Pair = tuple[int, str]  # (developer_id, domain)


@dataclass(frozen=True)
class Prediction:
    """What a method predicted for the n-th task of a pair (n starts at 1)."""

    n: int
    task_number: int
    t_est: float
    t_act: float
    k: float
    pred: float
    used_for_learning: bool


def group_pairs(
    tasks: Sequence[SipTask], domain_mode: str
) -> dict[Pair, list[SipTask]]:
    """Split tasks by (developer, domain), keeping their time order; keys sorted."""
    groups: dict[Pair, list[SipTask]] = defaultdict(list)
    for task in tasks:
        groups[(task.developer_id, domain_of(task, domain_mode))].append(task)
    return dict(sorted(groups.items()))


def replay(tasks: Sequence[SipTask], method: Method) -> list[Prediction]:
    """Predict every task with k from earlier tasks, then learn it if it is clean.

    Outliers are still predicted and scored; they are only left out of learning.
    """
    predictions = []
    for n, task in enumerate(tasks, start=1):
        k = method.k()
        used = skip_reason(TaskType.FLEXIBLE, task.t_est, task.t_act) is None
        predictions.append(
            Prediction(
                n=n,
                task_number=task.task_number,
                t_est=task.t_est,
                t_act=task.t_act,
                k=k,
                pred=proposed_minutes(task.t_est, k, buffer=0.0),
                used_for_learning=used,
            )
        )
        if used:
            method = method.learn(task.t_est, task.t_act)
    return predictions
