import pytest

from metanoia_ai.domain.forbidden_terms import has_internal_name


@pytest.mark.parametrize(
    "text",
    [
        "Your K_bias is 1.3",
        "KBIAS",
        "t_est = 60",
        "K=1.3",
        "the ratio is high",
        "Ratio: 2",
    ],
)
def test_internal_names_are_found(text: str) -> None:
    assert has_internal_name(text)


@pytest.mark.parametrize(
    "text",
    ["Short duration before lunch", "Good integration work", "Bạn làm khá sát."],
)
def test_normal_words_pass(text: str) -> None:
    assert not has_internal_name(text)


def test_extra_names() -> None:
    assert has_internal_name("score 0.8", extra_names=("score",))
    assert not has_internal_name("score 0.8")
