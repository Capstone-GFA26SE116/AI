"""Chat history texts (plans/chat-history.md T1, T2)."""

import pytest

from metanoia_ai.domain.chat_history import (
    QueryTooLongError,
    clean_query,
    preview,
    snippet,
)

# --- T1: the search text ---


@pytest.mark.parametrize("raw", [None, "", "   ", "\n\t"])
def test_nothing_to_search_for(raw):
    assert clean_query(raw) is None


def test_spaces_are_tidied():
    assert clean_query("  trang   blog\n ") == "trang blog"


def test_100_characters_is_the_limit():
    assert clean_query("a" * 100) == "a" * 100
    with pytest.raises(QueryTooLongError):
        clean_query("a" * 101)


# --- T2: preview and snippet ---


def test_short_preview_is_the_whole_message_on_one_line():
    assert preview("Xin chào\n\nbạn") == "Xin chào bạn"


def test_long_preview_is_cut_with_an_ellipsis():
    text = preview("x" * 200, width=20)
    assert len(text) == 20 and text.endswith("…")


def test_snippet_of_a_short_message_is_the_message():
    assert snippet("Khách xin thêm Blog", "blog") == "Khách xin thêm Blog"


def test_snippet_shows_the_match_in_the_middle_of_a_long_message():
    text = "a " * 100 + "trang BLOG 3 bài " + "b " * 100
    piece = snippet(text, "blog", width=60)
    assert "BLOG" in piece
    assert piece.startswith("…") and piece.endswith("…")
    assert len(piece) <= 62


def test_snippet_near_the_start_has_no_leading_ellipsis():
    piece = snippet("blog " + "x " * 100, "blog", width=40)
    assert piece.startswith("blog") and piece.endswith("…")


def test_snippet_near_the_end_has_no_trailing_ellipsis():
    piece = snippet("x " * 100 + "cuối là blog", "blog", width=40)
    assert piece.endswith("blog") and piece.startswith("…")


def test_snippet_without_a_match_falls_back_to_the_preview():
    assert snippet("x" * 200, "blog", width=20) == preview("x" * 200, width=20)
