"""SqlMemoryRepository conversions only. The SQL itself is checked with EXPLAIN (step 10)."""

import pytest

from metanoia_ai.application.ports.memory_repository import MemoryRepository
from metanoia_ai.domain.memory_text import MemoryOutcome, StopReason
from metanoia_ai.infrastructure.db.memory_repository import (
    OUTCOME_TO_DB,
    STOP_REASON_FROM_DB,
    SqlMemoryRepository,
    to_pgvector,
)

# --- T23: list of floats -> pgvector text ---


def test_t23_vector_becomes_pgvector_text():
    assert to_pgvector([0.1, -0.25, 3]) == "[0.1,-0.25,3.0]"


def test_t23_full_precision_is_kept():
    value = 0.12345678901234568
    assert to_pgvector([value]) == f"[{value!r}]"
    assert float(to_pgvector([value])[1:-1]) == value


def test_t23_1536_numbers():
    text = to_pgvector([0.5] * 1536)
    assert text.startswith("[") and text.endswith("]")
    assert len(text[1:-1].split(",")) == 1536


@pytest.mark.parametrize(
    "vector",
    [
        pytest.param([], id="empty"),
        pytest.param([0.1, float("nan")], id="nan"),
        pytest.param([float("inf")], id="infinity"),
    ],
)
def test_t23_bad_vectors_are_rejected(vector):
    with pytest.raises(ValueError):
        to_pgvector(vector)


# --- DB strings ---


def test_outcome_uses_db_values():
    # DB CHECK allows Completed / Partial / NotDone (plan #23).
    assert OUTCOME_TO_DB == {
        MemoryOutcome.DONE: "Completed",
        MemoryOutcome.SKIPPED: "NotDone",
    }


def test_every_db_stop_reason_is_known():
    assert set(STOP_REASON_FROM_DB) == {"Completed", "Interrupted", "Blocked", "Other"}
    assert set(STOP_REASON_FROM_DB.values()) == set(StopReason)


def test_repository_matches_the_port():
    repo: MemoryRepository = SqlMemoryRepository(session=None)  # type check only
    assert repo is not None
