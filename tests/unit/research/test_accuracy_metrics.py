"""Problem #6 metrics with hand-made rows (plans/accuracy-metrics.md, T1–T5)."""

from datetime import UTC, datetime, timedelta

import pytest

from research.accuracy.metrics import (
    MIN_SAMPLES,
    FixedTask,
    LearnedTask,
    ProposedSlot,
    approval_summary,
    estimate_accuracy,
    validity,
)

T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def slot(
    start: float,
    end: float,
    proposal: str = "p1",
    status: str = "Approved",
    deadline: float | None = None,
    edited: bool = False,
    proposed_at: float = -24,
) -> ProposedSlot:
    return ProposedSlot(
        proposal_id=proposal,
        proposal_created_at=at(proposed_at),
        status=status,
        start=at(start),
        end=at(end),
        deadline=at(deadline) if deadline is not None else None,
        edited=edited,
    )


# --- M1 ---


def test_estimate_accuracy_compares_user_guess_with_k_corrected():
    result = estimate_accuracy(
        [
            LearnedTask(60, 90, 1.3),  # user 0.333, AI |90-78|/90 = 0.133 → AI closer
            LearnedTask(100, 100, 1.3),  # user 0, AI 0.3 → user closer
        ]
    )
    assert result.samples == 2
    assert result.median_user_error == pytest.approx((1 / 3 + 0) / 2)
    assert result.median_ai_error == pytest.approx((12 / 90 + 0.3) / 2)
    assert result.ai_closer_share == 0.5
    assert result.too_few


def test_a_tie_does_not_count_as_ai_closer():
    assert estimate_accuracy([LearnedTask(60, 60, 1.0)]).ai_closer_share == 0


def test_no_learned_tasks_gives_empty_result():
    result = estimate_accuracy([])
    assert result.samples == 0
    assert result.median_ai_error is None
    assert result.too_few


def test_enough_samples_clears_the_flag():
    tasks = [LearnedTask(60, 90, 1.3)] * MIN_SAMPLES
    assert not estimate_accuracy(tasks).too_few


# --- M2 ---


def test_approval_counts_only_approved_slots_on_the_calendar():
    slots = [
        slot(0, 1),  # approved, unedited
        slot(1, 2, edited=True),  # approved, edited
        slot(2, 3, status="Done"),  # ran later, still counts
        slot(3, 4, status="Cancelled"),  # thrown away
        slot(4, 5, status="Proposed"),  # never approved
        slot(5, 6, proposal="p2"),  # proposal not approved
    ]
    result = approval_summary(
        ["Approved", "Superseded", "Superseded"], slots, approved_proposals={"p1"}
    )
    assert result.proposals_by_status == {"Approved": 1, "Superseded": 2}
    assert result.approved_slots == 3
    assert result.unedited_slots == 2
    assert result.unedited_share == pytest.approx(2 / 3)
    assert result.too_few


def test_approval_with_no_slots_has_no_share():
    result = approval_summary([], [], approved_proposals=set())
    assert result.unedited_share is None


# --- M3 ---


def test_overlap_inside_one_proposal_counts_both_slots():
    result = validity([slot(0, 2), slot(1, 3), slot(3, 4)], fixed=[])
    assert result.checked_slots == 3
    assert result.overlapping_slots == 2


def test_touching_slots_and_other_proposals_do_not_overlap():
    result = validity([slot(0, 1), slot(1, 2), slot(0, 1, proposal="p2")], fixed=[])
    assert result.overlapping_slots == 0


def test_edited_slots_are_not_checked():
    result = validity([slot(0, 2), slot(1, 3, edited=True)], fixed=[])
    assert result.checked_slots == 1
    assert result.overlapping_slots == 0


def test_fixed_overlap_only_with_fixed_tasks_known_at_proposal_time():
    known = FixedTask(at(0.5), at(1.5), created_at=at(-48))
    later = FixedTask(at(2.5), at(3.5), created_at=at(-1))  # created after the proposal
    result = validity([slot(0, 1), slot(2, 3)], fixed=[known, later])
    assert result.fixed_overlaps == 1


def test_after_deadline_skips_slots_without_deadline():
    result = validity(
        [slot(0, 2, deadline=1), slot(3, 4, deadline=5), slot(5, 6)], fixed=[]
    )
    assert result.after_deadline == 1
