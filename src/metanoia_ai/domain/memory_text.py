"""FR-19: the text of a task memory. Pure functions, no I/O."""

import re

EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"
CLIENT_MASK = "[client]"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 9 or more digits, optionally starting with "+", with at most one space, "."
# or "-" between digits. Dates like 14-10-2026 have only 8 digits, so they stay.
PHONE = re.compile(r"(?<![\w+])\+?\d(?:[ .\-]?\d){8,}(?!\d)")


def mask_contact(text: str, client_name: str | None) -> str:
    """Hide emails, phone numbers and the project's client name.

    Emails go first, so a client name inside an email address cannot leave
    half of the address behind. The client name is matched as a whole word,
    ignoring case.
    """
    text = EMAIL.sub(EMAIL_MASK, text)
    text = PHONE.sub(PHONE_MASK, text)
    if client_name and client_name.strip():
        # Not \b: a name may start or end with "(" or "." where \b never matches.
        name = re.compile(
            rf"(?<!\w){re.escape(client_name.strip())}(?!\w)", re.IGNORECASE
        )
        text = name.sub(CLIENT_MASK, text)
    return text
