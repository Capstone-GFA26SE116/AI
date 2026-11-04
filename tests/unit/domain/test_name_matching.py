from uuid import UUID

import pytest

from metanoia_ai.domain.name_matching import (
    MatchKind,
    NamedItem,
    fold,
    match_name,
    score,
)


def items(*names: str) -> list[NamedItem]:
    return [NamedItem(UUID(int=i), name) for i, name in enumerate(names, start=1)]


PROJECTS = items(
    "Landing page — Client A",
    "Landing page — Client B",
    "Viết bài blog tháng 11",
    "App đặt lịch spa",
)


def test_fold_removes_accents_case_and_signs() -> None:
    assert fold("  Viết BÀI blog—tháng 11! ") == "viet bai blog thang 11"
    assert fold("Đặt lịch") == "dat lich"


@pytest.mark.parametrize(
    ("spoken", "name", "expected"),
    [
        ("bài blog", "Viết bài blog tháng 11", 1.0),  # whole phrase inside the name
        ("blog tháng mười một", "Viết bài blog tháng 11", 0.5),  # 2 of 4 words
        ("spa", "App đặt lịch spa", 1.0),
        ("", "App", 0.0),
        ("!!!", "App", 0.0),
    ],
)
def test_score(spoken: str, name: str, expected: float) -> None:
    assert score(spoken, name) == pytest.approx(expected)


# T1
def test_one_clear_match() -> None:
    result = match_name("dự án spa", PROJECTS)

    # "du an" is not in the name: 1 of 3 words → not found; the LLM passes the name only.
    assert result.kind is MatchKind.NONE
    result = match_name("app spa", PROJECTS)
    assert result.kind is MatchKind.FOUND
    assert result.found.name == "App đặt lịch spa"


def test_two_close_names_ask_the_user() -> None:
    result = match_name("landing page", PROJECTS)

    assert result.kind is MatchKind.AMBIGUOUS
    assert [i.name for i in result.options] == [
        "Landing page — Client A",
        "Landing page — Client B",
    ]
    assert result.found is None


def test_without_accents_still_matches() -> None:
    result = match_name("viet bai blog", PROJECTS)

    assert result.kind is MatchKind.FOUND
    assert result.found.id == UUID(int=3)


def test_nothing_close_is_not_found() -> None:
    assert match_name("báo cáo thuế", PROJECTS).kind is MatchKind.NONE
    assert match_name("blog", []).kind is MatchKind.NONE


def test_clear_lead_wins_even_if_another_name_shares_words() -> None:
    result = match_name("landing page client a", PROJECTS)

    # Client A: 1.0 (whole phrase); Client B: 3 of 4 words = 0.75 → gap 0.25.
    assert result.kind is MatchKind.FOUND
    assert result.found.name == "Landing page — Client A"


def test_at_most_three_options_best_first() -> None:
    many = items("Blog A", "Blog B", "Blog C", "Blog D", "Báo cáo")

    result = match_name("blog", many)

    assert result.kind is MatchKind.AMBIGUOUS
    assert [i.name for i in result.options] == ["Blog A", "Blog B", "Blog C"]


def test_same_name_twice_is_ambiguous() -> None:
    result = match_name("blog", items("Blog", "Blog"))

    assert result.kind is MatchKind.AMBIGUOUS
    assert len(result.options) == 2


def test_thresholds_from_arguments() -> None:
    loose = match_name("blog tháng mười một", PROJECTS, min_score=0.4)
    assert loose.kind is MatchKind.FOUND

    strict_gap = match_name("landing page client a", PROJECTS, min_gap=0.3)
    assert strict_gap.kind is MatchKind.AMBIGUOUS
    assert (
        len(match_name("blog", items("Blog A", "Blog B"), max_options=1).options) == 1
    )
