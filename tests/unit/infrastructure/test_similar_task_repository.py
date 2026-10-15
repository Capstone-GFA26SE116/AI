"""SqlSimilarTaskRepository conversions only. The SQL itself is checked with EXPLAIN (step 7)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from metanoia_ai.application.ports.similar_task_repository import (
    SimilarTaskRepository,
)
from metanoia_ai.domain.similar_tasks import SimilarOutcome
from metanoia_ai.infrastructure.db.similar_task_repository import (
    OUTCOME_FROM_DB,
    SqlSimilarTaskRepository,
    clean_tsquery,
    to_past_task,
)

# --- T21: DB strings ---


def test_t21_outcome_from_db():
    assert OUTCOME_FROM_DB["Completed"] is SimilarOutcome.DONE
    assert OUTCOME_FROM_DB["NotDone"] is SimilarOutcome.SKIPPED


def test_every_db_outcome_is_known():
    # DB CHECK: Completed / Partial / NotDone. An unknown value would crash the search.
    assert set(OUTCOME_FROM_DB) == {"Completed", "Partial", "NotDone"}


def test_row_becomes_past_task():
    task_id = uuid4()
    finished = datetime(2026, 10, 10, 10, tzinfo=UTC)
    row = SimpleNamespace(
        TaskId=task_id,
        Title="Landing page cho Acme",
        DomainName="Coding",
        Outcome="Completed",
        EstimatedMinutes=120,
        ActualMinutes=180,
        CompletedAt=finished,
    )
    task = to_past_task(row)
    assert task.task_id == task_id
    assert task.title == "Landing page cho Acme"  # as stored, not masked (#9)
    assert task.outcome is SimilarOutcome.DONE
    assert (task.estimated_minutes, task.actual_minutes) == (120, 180)
    assert task.finished_at == finished


# --- T21: tsquery cleaning ---


def test_t21_clean_terms_pass_unchanged():
    assert clean_tsquery("landing | page | sửa | v2") == "landing | page | sửa | v2"


@pytest.mark.parametrize(
    ("terms", "clean"),
    [
        pytest.param("landing & page", None, id="and-operator"),
        pytest.param("landing | !page", "landing", id="not-operator"),
        pytest.param("land:* | page", "page", id="prefix-operator"),
        pytest.param("(landing) | page", "page", id="brackets"),
        pytest.param("a <-> b", None, id="followed-by"),
        pytest.param('x\'; DROP TABLE "Task"; --', None, id="injection"),
        pytest.param("  landing  ||  page ", "landing | page", id="extra-spaces"),
        pytest.param("", None, id="empty"),
    ],
)
def test_t21_unsafe_parts_are_dropped(terms, clean):
    assert clean_tsquery(terms) == clean


def test_repository_matches_the_port():
    repo: SimilarTaskRepository = SqlSimilarTaskRepository(session=None)  # type only
    assert repo is not None
