from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from metanoia_ai.domain.models import (
    ActualTimeSource,
    FocusSessionRecord,
    SkipReason,
)


def test_skip_reason_values_are_stable_api_strings():
    # The Backend reads these strings; changing one breaks the contract.
    assert [reason.value for reason in SkipReason] == [
        "task_not_done",
        "already_learned",
        "fixed_task",
        "no_actual_time",
        "session_running",
        "unconfirmed_time",
        "outlier",
    ]


def test_session_is_confirmed_by_default():
    session = FocusSessionRecord(
        started_at=datetime(2026, 10, 13, 13, 0, tzinfo=UTC),
        ended_at=None,
        source=ActualTimeSource.TIMER,
    )
    assert session.is_confirmed is True


def test_session_cannot_be_changed():
    session = FocusSessionRecord(
        started_at=datetime(2026, 10, 13, 13, 0, tzinfo=UTC),
        ended_at=None,
        source=ActualTimeSource.TIMER,
    )
    with pytest.raises(FrozenInstanceError):
        session.ended_at = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
