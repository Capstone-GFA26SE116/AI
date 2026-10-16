from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from metanoia_ai.domain.models import EstimateSource
from metanoia_ai.domain.similar_tasks import (
    MatchSource,
    PastTask,
    SimilarOutcome,
    SimilarSummary,
    SimilarTask,
    build_search_query,
    rrf_merge,
    search_terms,
    summarize_similar,
)

VECTOR, TEXT = MatchSource.VECTOR, MatchSource.TEXT
AI = EstimateSource.AI
OCT_10 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def past(
    n: int,
    days_ago: int | None = 1,
    outcome: SimilarOutcome = SimilarOutcome.DONE,
    estimated: int = 60,
    actual: int = 90,
) -> PastTask:
    """Task number n; the id is fixed so ties are easy to check."""
    return PastTask(
        task_id=UUID(int=n),
        title=f"Task {n}",
        domain_name="Coding",
        outcome=outcome,
        estimated_minutes=estimated,
        actual_minutes=actual,
        finished_at=None if days_ago is None else OCT_10 - timedelta(days=days_ago),
    )


def ids(results: list[SimilarTask]) -> list[int]:
    return [r.task.task_id.int for r in results]


# --- T1: the text to embed ---


def test_t1_search_query_is_masked_and_prefixed():
    assert (
        build_search_query("Landing page cho ACME", "Coding", "ACME")
        == "task: search result | query: Landing page cho [client]. Coding"
    )


def test_search_query_without_client():
    assert (
        build_search_query("Viết báo cáo tuần", "Writing", None)
        == "task: search result | query: Viết báo cáo tuần. Writing"
    )


def test_search_query_masks_email_and_phone():
    query = build_search_query("Gửi a.b@acme.com, gọi 0901 234 567", "Sales", None)
    assert query == "task: search result | query: Gửi [email], gọi [phone]. Sales"


# --- T2: full-text terms ---


def test_t2_terms_keep_only_meaningful_words():
    terms = search_terms(
        "Landing page (v2) cho ACME, gửi a.b@acme.com - 0901 234 567 & a!",
        "Coding",
        "ACME",
    )
    assert terms == "landing | page | v2 | cho | gửi | coding"


def test_t2_vietnamese_letters_are_kept():
    assert search_terms("Sửa lỗi đăng nhập", "Coding", None) == (
        "sửa | lỗi | đăng | nhập | coding"
    )


def test_terms_are_not_repeated():
    assert search_terms("Coding coding CODING", "Coding", None) == "coding"


def test_terms_have_no_tsquery_operators():
    terms = search_terms("a & b | c ! d:* (e) 'f' <-> g_h", "Ops", None)
    assert terms == "ops"  # every 1-letter word dropped; "_" splits g_h
    assert not set("&!():*'<>") & set(terms)


# --- T3: nothing left ---


def test_t3_only_symbols_gives_no_terms():
    assert search_terms("!!! --- ???", "", None) is None


def test_t3_only_masks_gives_no_terms():
    assert search_terms("ACME a.b@acme.com", "", "ACME") is None


# --- T4: RRF adds the scores of both lists ---


def test_t4_task_in_both_lists_wins():
    a, b, c, d = past(1), past(2), past(3), past(4)

    results = rrf_merge(vector=[a], text=[b, c, a], k=60, limit=5)

    assert ids(results) == [1, 2, 3]
    first, second, _ = results
    assert first.score == round(1 / 61 + 1 / 63, 4) == 0.0323
    assert first.matched_by == (VECTOR, TEXT)
    assert second.score == round(1 / 61, 4) == 0.0164
    assert second.matched_by == (TEXT,)
    assert d.task_id not in [r.task.task_id for r in results]


# --- T5: ties ---


def test_t5_tie_newer_task_first():
    older, newer = past(1, days_ago=5), past(2, days_ago=1)
    # Both rank 1 in one list: the same score.
    results = rrf_merge(vector=[older], text=[newer], k=60, limit=5)
    assert ids(results) == [2, 1]


def test_t5_tie_task_without_finish_time_last():
    no_time, with_time = past(1, days_ago=None), past(2, days_ago=30)
    results = rrf_merge(vector=[no_time], text=[with_time], k=60, limit=5)
    assert ids(results) == [2, 1]


def test_t5_full_tie_by_task_id():
    results = rrf_merge(vector=[past(2)], text=[past(1)], k=60, limit=5)
    assert ids(results) == [1, 2]


# --- T6: text only (Gemini failed) ---


def test_t6_text_only_keeps_the_text_order():
    results = rrf_merge(vector=[], text=[past(3), past(1), past(2)], k=60, limit=5)
    assert ids(results) == [3, 1, 2]
    assert all(r.matched_by == (TEXT,) for r in results)


def test_both_lists_empty():
    assert rrf_merge(vector=[], text=[], k=60, limit=5) == []


# --- T7: limit ---


def test_t7_cut_at_limit():
    text = [past(n) for n in range(1, 21)]
    results = rrf_merge(vector=[], text=text, k=60, limit=5)
    assert ids(results) == [1, 2, 3, 4, 5]


# --- ratio of one result ---


@pytest.mark.parametrize(
    ("task", "ratio"),
    [
        pytest.param(past(1, estimated=60, actual=90), 1.5, id="done"),
        pytest.param(past(1, estimated=90, actual=100), 1.11, id="rounded"),
        pytest.param(past(1, actual=0), None, id="no-time"),
        pytest.param(
            past(1, outcome=SimilarOutcome.SKIPPED, actual=30), None, id="skipped"
        ),
    ],
)
def test_ratio(task, ratio):
    assert SimilarTask(task, score=0.0164, matched_by=(TEXT,)).ratio == ratio


# --- T8, T9: summary ---


def result(task: PastTask) -> SimilarTask:
    return SimilarTask(task, score=0.0164, matched_by=(TEXT,))


def test_t8_summary_averages_done_tasks_with_time():
    results = [
        result(past(1, estimated=60, actual=90)),  # 1.5
        result(past(2, estimated=50, actual=60)),  # 1.2
        result(past(3, estimated=40, actual=0)),  # done, no time: not averaged
        result(past(4, outcome=SimilarOutcome.SKIPPED, actual=30)),
    ]
    assert summarize_similar(results) == SimilarSummary(
        count=4, done_count=3, average_ratio=1.35
    )


def test_t9_no_done_task_gives_no_average():
    results = [result(past(1, outcome=SimilarOutcome.SKIPPED, actual=0))]
    assert summarize_similar(results) == SimilarSummary(
        count=1, done_count=0, average_ratio=None
    )


def test_summary_of_no_results():
    assert summarize_similar([]) == SimilarSummary(0, 0, None)


# --- FR-17b T4: AI-estimated tasks have no ratio ---


def test_fr17b_t4_ai_estimate_has_no_ratio_and_is_not_averaged():
    ai_task = replace(past(1, estimated=60, actual=180), estimate_source=AI)
    results = [
        result(ai_task),  # 3.0, but the AI's number: left out
        result(past(2, estimated=60, actual=90)),  # 1.5
        result(past(3, estimated=50, actual=60)),  # 1.2
    ]

    assert results[0].ratio is None
    assert results[1].ratio == 1.5
    assert summarize_similar(results) == SimilarSummary(
        count=3, done_count=3, average_ratio=1.35
    )


def test_fr17b_only_ai_estimates_gives_no_average():
    results = [result(replace(past(1), estimate_source=AI))]
    assert summarize_similar(results) == SimilarSummary(
        count=1, done_count=1, average_ratio=None
    )


def test_past_task_is_the_users_estimate_by_default():
    assert past(1).estimate_source is EstimateSource.USER
