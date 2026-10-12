"""The fake must behave like the real repository, or use case tests prove nothing."""

import asyncio
from uuid import uuid4

import pytest

from metanoia_ai.application.ports.learning_repository import (
    DomainProfile,
    KBiasLogEntry,
    LearningRepository,
    TaskAlreadyLearnedError,
    TaskForLearning,
)
from metanoia_ai.domain.models import ActualTimeSource, ConfidenceTier, TaskType
from tests.unit.application.fakes import FakeLearningRepository

USER = uuid4()
OTHER_USER = uuid4()
DOMAIN = uuid4()


def make_task() -> TaskForLearning:
    return TaskForLearning(
        task_id=uuid4(),
        domain_id=DOMAIN,
        task_type=TaskType.FLEXIBLE,
        is_done=True,
        estimated_minutes=60,
        sessions=(),
    )


def make_entry(task_id, new_k=1.34, sample_index=1) -> KBiasLogEntry:
    return KBiasLogEntry(
        task_id=task_id,
        old_k=1.3,
        new_k=new_k,
        alpha=0.2,
        estimated_minutes=60,
        actual_minutes=90,
        observed_ratio=1.5,
        source=ActualTimeSource.TIMER,
        confidence=ConfidenceTier.HIGH,
        sample_index=sample_index,
    )


def test_fake_matches_the_port():
    repo: LearningRepository = FakeLearningRepository()  # type check only
    assert repo is not None


def test_get_task_only_returns_the_owners_task():
    repo = FakeLearningRepository()
    task = make_task()
    repo.add_task(USER, task)
    assert asyncio.run(repo.get_task(USER, task.task_id)) == task
    assert asyncio.run(repo.get_task(OTHER_USER, task.task_id)) is None


def test_save_creates_then_updates_the_profile():
    repo = FakeLearningRepository()
    first, second = uuid4(), uuid4()
    asyncio.run(repo.save(USER, DOMAIN, make_entry(first, 1.34, 1)))
    asyncio.run(repo.save(USER, DOMAIN, make_entry(second, 1.37, 2)))
    assert repo.profiles[(USER, DOMAIN)] == DomainProfile(k_bias=1.37, sample_count=2)
    assert set(repo.logs) == {first, second}


def test_save_twice_for_one_task_raises():
    repo = FakeLearningRepository()
    task_id = uuid4()
    asyncio.run(repo.save(USER, DOMAIN, make_entry(task_id)))
    with pytest.raises(TaskAlreadyLearnedError):
        asyncio.run(repo.save(USER, DOMAIN, make_entry(task_id, 1.4, 2)))


def test_transaction_rolls_back_on_error():
    repo = FakeLearningRepository()

    async def save_then_fail():
        async with repo.transaction():
            await repo.save(USER, DOMAIN, make_entry(uuid4()))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(save_then_fail())
    assert repo.profiles == {}
    assert repo.logs == {}


def test_get_log_only_returns_the_owners_log():
    repo = FakeLearningRepository()
    task_id = uuid4()
    asyncio.run(repo.save(USER, DOMAIN, make_entry(task_id)))
    assert asyncio.run(repo.get_log(USER, task_id)).task_id == task_id
    assert asyncio.run(repo.get_log(OTHER_USER, task_id)) is None


def test_lock_profile_creates_a_missing_profile_with_initial_k():
    repo = FakeLearningRepository()
    profile = asyncio.run(repo.lock_profile(USER, DOMAIN, initial_k=1.3))
    assert profile == DomainProfile(k_bias=1.3, sample_count=0)
    assert repo.profiles[(USER, DOMAIN)] == profile


def test_lock_profile_keeps_an_existing_profile():
    repo = FakeLearningRepository()
    repo.profiles[(USER, DOMAIN)] = DomainProfile(k_bias=1.6, sample_count=7)
    profile = asyncio.run(repo.lock_profile(USER, DOMAIN, initial_k=1.3))
    assert profile == DomainProfile(k_bias=1.6, sample_count=7)
