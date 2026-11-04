"""FR-22 #6: find the project or task the user named by voice. No LLM; the LLM never sees IDs.

Names are compared without accents and case ("viet bai blog" = "Viết bài blog").
Score = share of the spoken words found in the name; a name that holds the whole
spoken phrase scores 1.0. One clear winner → found; several close → ask; none → not found.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

MIN_SCORE = 0.8
MIN_GAP = 0.2
MAX_OPTIONS = 3
_NOT_WORD = re.compile(r"[^0-9a-z]+")


class MatchKind(StrEnum):
    FOUND = "found"
    AMBIGUOUS = "ambiguous"  # ask the user to pick (FR-22 "clarify")
    NONE = "none"


@dataclass(frozen=True)
class NamedItem:
    id: UUID
    name: str


@dataclass(frozen=True)
class MatchResult:
    kind: MatchKind
    found: NamedItem | None = None
    options: tuple[NamedItem, ...] = ()  # only when AMBIGUOUS, best first


def fold(text: str) -> str:
    """Lower case, no accents, only letters and digits split by single spaces."""
    text = text.lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(_NOT_WORD.sub(" ", text).split())


def score(spoken: str, name: str) -> float:
    """1.0 when the name holds the whole spoken phrase; else share of spoken words in the name."""
    said, target = fold(spoken), fold(name)
    if not said or not target:
        return 0.0
    if f" {said} " in f" {target} ":
        return 1.0
    said_words = said.split()
    name_words = set(target.split())
    return sum(word in name_words for word in said_words) / len(said_words)


def match_name(
    spoken: str,
    items: Sequence[NamedItem],
    min_score: float = MIN_SCORE,
    min_gap: float = MIN_GAP,
    max_options: int = MAX_OPTIONS,
) -> MatchResult:
    """Pick the item the user meant, or say it is unclear / missing. Never guesses."""
    ranked = sorted(
        ((score(spoken, item.name), item) for item in items),
        key=lambda pair: (-pair[0], fold(pair[1].name), str(pair[1].id)),
    )
    if not ranked or ranked[0][0] < min_score:
        return MatchResult(MatchKind.NONE)
    best = ranked[0][0]
    close = [item for value, item in ranked if value > 0 and best - value < min_gap]
    if len(close) == 1:
        return MatchResult(MatchKind.FOUND, found=close[0])
    return MatchResult(MatchKind.AMBIGUOUS, options=tuple(close[:max_options]))
