"""FR-09: find similar past tasks. Pure functions, no I/O."""

import re

from metanoia_ai.domain.memory_text import (
    CLIENT_MASK,
    EMAIL_MASK,
    PHONE_MASK,
    mask_contact,
)

# Gemini Embedding 2 uses prefixes instead of task_type (FR-19 stores "title: … | text: …").
QUERY_PREFIX = "task: search result | query: "
MASKS = (EMAIL_MASK, PHONE_MASK, CLIENT_MASK)
# Letters and digits only (any language); "_" is not a letter.
WORD = re.compile(r"[^\W_]+")
MIN_TERM_LENGTH = 2


def build_search_query(title: str, domain_name: str, client_name: str | None) -> str:
    """The text to embed: masked like the stored memories, so "[client]" matches on both sides."""
    return f"{QUERY_PREFIX}{mask_contact(title, client_name)}. {domain_name}"


def search_terms(title: str, domain_name: str, client_name: str | None) -> str | None:
    """Full-text query "word1 | word2 | …" (OR), or None when no word is left.

    Built from the masked title and the domain name only (not the prefix).
    Mask tokens and 1-letter words are dropped; only letters and digits stay,
    so the result is always safe for to_tsquery.
    """
    text = f"{mask_contact(title, client_name)} {domain_name}"
    for mask in MASKS:
        text = text.replace(mask, " ")
    words: list[str] = []
    for word in WORD.findall(text.lower()):
        if len(word) >= MIN_TERM_LENGTH and word not in words:
            words.append(word)
    return " | ".join(words) or None
