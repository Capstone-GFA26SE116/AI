"""Chat history: the search text, and the short texts shown in the list (plans/chat-history.md)."""

MAX_QUERY_CHARS = 100
PREVIEW_CHARS = 120
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
MAX_OFFSET = 1000
ELLIPSIS = "…"


class QueryTooLongError(ValueError):
    """The search text is longer than MAX_QUERY_CHARS."""


def one_line(text: str) -> str:
    """All runs of spaces and line breaks become one space."""
    return " ".join(text.split())


def clean_query(raw: str | None) -> str | None:
    """The search text with spaces tidied; None when there is nothing to search for."""
    if raw is None:
        return None
    query = one_line(raw)
    if not query:
        return None
    if len(query) > MAX_QUERY_CHARS:
        raise QueryTooLongError(f"q is longer than {MAX_QUERY_CHARS} characters")
    return query


def preview(text: str, width: int = PREVIEW_CHARS) -> str:
    """The start of a message on one line, cut at `width` characters with "…"."""
    line = one_line(text)
    if len(line) <= width:
        return line
    return line[: width - 1].rstrip() + ELLIPSIS


def snippet(text: str, query: str, width: int = PREVIEW_CHARS) -> str:
    """A piece of the message around the first match of `query` (any case).

    The match sits about a third into the piece, so the reader sees what comes
    before it too. No match (should not happen) → the start of the message.
    """
    line = one_line(text)
    found = line.lower().find(query.lower())
    if found < 0 or len(line) <= width:
        return preview(line, width)
    start = max(0, min(found - width // 3, len(line) - width))
    end = start + width
    piece = line[start:end].strip()
    if start > 0:
        piece = ELLIPSIS + piece
    if end < len(line):
        piece += ELLIPSIS
    return piece
