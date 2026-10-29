"""Internal names that must never reach the user, shared by every text the user reads.

Checked without case. Each feature adds its own extra names (fact or field names).
"""

import re
from collections.abc import Iterable

# Formula names: matched anywhere in the text.
INTERNAL_NAMES = ("k_bias", "kbias", "t_est", "t_act", "t_proposed", "k=")
# Plain words: matched as whole words only, so "duration" or "integration" are fine.
INTERNAL_WORDS = ("ratio",)
_WORDS = re.compile(r"\b(?:" + "|".join(INTERNAL_WORDS) + r")\b")


class InternalNameError(ValueError):
    """A sentence shows an internal name. A bug in the templates, never user input."""


def has_internal_name(text: str, extra_names: Iterable[str] = ()) -> bool:
    """True when the text shows a shared internal name or one of `extra_names`."""
    lowered = text.lower()
    if any(name in lowered for name in (*INTERNAL_NAMES, *extra_names)):
        return True
    return _WORDS.search(lowered) is not None
