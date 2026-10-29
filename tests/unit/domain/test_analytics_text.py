from decimal import Decimal
from uuid import uuid4

import pytest

from metanoia_ai.domain.analytics import (
    BreakdownItem,
    CompletionStats,
    DomainBias,
    Insight,
    InsightCode,
    classify_bias,
)
from metanoia_ai.domain.analytics_text import (
    _say,
    completion_text,
    difference_text,
    domain_text,
    insight_text,
    item_name,
    rounded_percent,
)
from metanoia_ai.domain.forbidden_terms import InternalNameError, has_internal_name
from metanoia_ai.domain.models import Language

VI, EN = Language.VI, Language.EN


def domain(k: str, samples: int, name: str = "Writing") -> DomainBias:
    return DomainBias(uuid4(), name, classify_bias(Decimal(k), samples))


def item(percent: int, name: str | None = "Landing page ABC") -> BreakdownItem:
    return BreakdownItem(
        None if name is None else uuid4(), name, 6, 1, 100, 100 + percent, percent
    )


def stats(total: int, done: int, on_time: int) -> CompletionStats:
    rate = Decimal(done) / total if total else None
    return CompletionStats(total, done, 0, total - done, on_time, rate, None)


@pytest.mark.parametrize(
    ("percent", "rounded"), [(31, 30), (33, 35), (12, 10), (-27, 25), (10, 10)]
)
def test_rounded_percent(percent: int, rounded: int) -> None:
    assert rounded_percent(percent) == rounded


def test_completion_sentences() -> None:
    assert completion_text(stats(24, 18, 14), VI) == (
        "Bạn hoàn thành 18/24 việc (75%), trong đó 14 việc xong đúng hạn."
    )
    assert completion_text(stats(24, 18, 14), EN) == (
        "You finished 18 of 24 tasks (75%), 14 of them on time."
    )
    assert "chưa hoàn thành việc nào trong 5" in completion_text(stats(5, 0, 0), VI)
    assert completion_text(stats(0, 0, 0), EN) == "No tasks were due in this period."


def test_domain_sentences_follow_plan_examples() -> None:
    assert domain_text(domain("1.31", 12), VI) == (
        "Bạn thường cần thêm khoảng 30% thời gian cho Writing."
    )
    assert domain_text(domain("0.70", 12), VI) == (
        "Bạn thường làm Writing nhanh hơn dự kiến khoảng 30%."
    )
    assert domain_text(domain("1.05", 12), VI) == "Bạn ước lượng Writing khá sát."
    assert (
        domain_text(domain("1.31", 2), VI)
        == "Chưa đủ dữ liệu cho Writing (mới có 2 việc)."
    )
    assert domain_text(domain("1.31", 7), VI) == (
        "Bạn thường cần thêm khoảng 30% thời gian cho Writing (đang học, dựa trên 7 việc)."
    )
    assert (
        domain_text(domain("1.31", 1), EN)
        == "Not enough data for Writing yet (only 1 task)."
    )
    assert domain_text(domain("1.31", 7), EN) == (
        "You usually need about 30% more time for Writing (still learning, based on 7 tasks)."
    )


def test_difference_sentences() -> None:
    assert difference_text(25, VI) == "Tốn nhiều hơn dự kiến khoảng 25%."
    assert difference_text(-12, VI) == "Nhanh hơn dự kiến khoảng 10%."
    assert difference_text(9, VI) == "Gần đúng dự kiến."
    assert difference_text(-10, EN) == "About 10% faster than planned."


# T8 (name part)
def test_no_client_group_name() -> None:
    assert item_name(item(0, name=None), VI) == "Không có khách hàng"
    assert item_name(item(0, name=None), EN) == "No client"
    assert item_name(item(0), EN) == "Landing page ABC"


def test_project_insight_uses_name_and_rounding() -> None:
    insight = Insight(InsightCode.PROJECT_OVER, project=item(26))

    assert (
        insight_text(insight, VI)
        == "Dự án Landing page ABC tốn nhiều hơn dự kiến khoảng 25%."
    )


def test_domain_insight_reuses_domain_sentence() -> None:
    writing = domain("1.31", 7)

    assert insight_text(
        Insight(InsightCode.DOMAIN_SLOWER, domain=writing), EN
    ) == domain_text(writing, EN)


def test_insight_without_its_subject_is_a_bug() -> None:
    with pytest.raises(ValueError):
        insight_text(Insight(InsightCode.DOMAIN_FASTER), VI)
    with pytest.raises(ValueError):
        insight_text(Insight(InsightCode.PROJECT_UNDER), VI)


def every_sentence(lang: Language) -> list[str]:
    domains = [domain(k, n) for k in ("1.31", "0.70", "1.05") for n in (2, 7, 12)]
    insights = [
        Insight(InsightCode.DOMAIN_SLOWER, domain=domains[2]),
        Insight(InsightCode.DOMAIN_FASTER, domain=domains[5]),
        Insight(InsightCode.PROJECT_OVER, project=item(30)),
        Insight(InsightCode.PROJECT_UNDER, project=item(-30, name=None)),
        Insight(InsightCode.LOW_COMPLETION),
        Insight(InsightCode.NOT_ENOUGH_DATA),
    ]
    return [
        *(domain_text(d, lang) for d in domains),
        *(insight_text(i, lang) for i in insights),
        *(difference_text(p, lang) for p in (40, 0, -40)),
        *(
            completion_text(s, lang)
            for s in (stats(10, 7, 5), stats(5, 0, 0), stats(0, 0, 0))
        ),
    ]


# T9
@pytest.mark.parametrize("lang", [VI, EN])
def test_no_sentence_shows_an_internal_name(lang: Language) -> None:
    sentences = every_sentence(lang)

    assert len(sentences) == 21
    for text in sentences:
        assert not has_internal_name(text), text
        for word in (
            "k_bias",
            "kbias",
            "t_est",
            "t_act",
            "t_proposed",
            "ratio",
            "factor",
        ):
            assert word not in text.lower(), text


def test_user_names_are_not_checked() -> None:
    named = domain("1.31", 12, name="Ratio study")

    assert (
        domain_text(named, EN)
        == "You usually need about 30% more time for Ratio study."
    )


def test_template_with_internal_name_raises() -> None:
    with pytest.raises(InternalNameError):
        _say("Your K_bias is {k}.", k=1.3)
    with pytest.raises(InternalNameError):
        _say("difference_percent = {p}", p=3)
