"""FR-12 sentences, built from fixed templates (vi / en). No LLM (#1 of plan §1).

Numbers are rounded so they read easily (#9). No sentence may show an
internal name (K_bias, T_est, Score…): `checked` raises if one slips in (#10).
"""

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from metanoia_ai.domain.briefing import Reason, ReasonCode
from metanoia_ai.domain.models import Language

MINUTE_STEP = 5
# Lower-case words that must never reach the user (same idea as reason_writer.py).
FORBIDDEN_WORDS = (
    "k_bias",
    "kbias",
    "t_est",
    "t_act",
    "t_proposed",
    "score",
    "k=",
    "needed_minutes",
    "available_minutes",
    "extra_percent",
    "sample_count",
    "deadline_soon",
    "at_risk",
    "in_progress",
)


class InternalNameError(ValueError):
    """A sentence shows an internal name. A bug in the templates, never user input."""


def has_internal_name(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in FORBIDDEN_WORDS)


def checked(text: str) -> str:
    if has_internal_name(text):
        raise InternalNameError("briefing text shows an internal name")
    return text


def _number(value: Decimal, lang: Language) -> str:
    """2.5 → "2,5" in Vietnamese, "2.5" in English; 3.0 → "3"."""
    text = f"{value.normalize():f}"
    return text.replace(".", ",") if lang is Language.VI else text


def duration(minutes: int, lang: Language) -> str:
    """Under 1 hour: "about N minutes" (step 5). Else "about N hours" (step 0.5) (#9)."""
    rounded = max(MINUTE_STEP, math.floor(minutes / MINUTE_STEP + 0.5) * MINUTE_STEP)
    if rounded < 60:
        return (
            f"khoảng {rounded} phút"
            if lang is Language.VI
            else f"about {rounded} minutes"
        )
    hours = Decimal(math.floor(minutes / 30 + 0.5)) / 2
    if lang is Language.VI:
        return f"khoảng {_number(hours, lang)} giờ"
    unit = "hour" if hours == 1 else "hours"
    return f"about {_number(hours, lang)} {unit}"


def _days(days: int, lang: Language) -> str:
    if lang is Language.VI:
        return f"{days} ngày"
    return "1 day" if days == 1 else f"{days} days"


def reason_text(reason: Reason, lang: Language) -> str:
    """The sentence for one reason code (#6)."""
    vi = lang is Language.VI
    match reason.code:
        case ReasonCode.OVERDUE:
            if reason.days == 0:
                text = (
                    "Đã quá hạn từ hôm nay." if vi else "Overdue since earlier today."
                )
            else:
                late = _days(reason.days, lang)
                text = f"Đã quá hạn {late}." if vi else f"Overdue by {late}."
        case ReasonCode.AT_RISK:
            need = duration(reason.needed_minutes, lang)
            if reason.available_minutes <= 0:
                text = (
                    f"Còn cần {need} nhưng không còn giờ rảnh nào trước hạn."
                    if vi
                    else f"Needs {need} but no free time is left before the deadline."
                )
            else:
                free = duration(reason.available_minutes, lang)
                text = (
                    f"Còn cần {need} nhưng chỉ còn {free} rảnh trước hạn."
                    if vi
                    else f"Needs {need} but only {free} free before the deadline."
                )
        case ReasonCode.IN_PROGRESS:
            left = duration(reason.needed_minutes, lang)
            text = f"Đang làm dở, còn {left}." if vi else f"In progress, {left} left."
        case ReasonCode.DEADLINE_SOON:
            if reason.days == 0:
                text = "Hạn hôm nay." if vi else "Due today."
            elif reason.days == 1:
                text = "Hạn ngày mai." if vi else "Due tomorrow."
            else:
                days = _days(reason.days, lang)
                text = f"Hạn trong {days}." if vi else f"Due in {days}."
        case ReasonCode.BIAS:
            p, domain = reason.extra_percent, reason.domain_name
            text = (
                f"Bạn thường cần thêm {p}% thời gian cho {domain}."
                if vi
                else f"You usually need {p}% more time for {domain}."
            )
    return checked(text)


def similar_ratio_text(average_ratio: float, lang: Language) -> str:
    """Past similar tasks took longer than planned (#11). Ratio with 1 decimal."""
    ratio = Decimal(str(average_ratio)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    times = _number(ratio, lang)
    text = (
        f"Các việc tương tự trước đây thường mất gấp {times} lần dự kiến."
        if lang is Language.VI
        else f"Similar tasks in the past usually took {times} times longer than planned."
    )
    return checked(text)


def similar_skipped_text(lang: Language) -> str:
    """A similar past task was dropped (#11)."""
    text = (
        "Trước đây bạn từng bỏ dở việc tương tự."
        if lang is Language.VI
        else "You have dropped a similar task before."
    )
    return checked(text)


def productive_hint_text(ranges: Sequence[tuple[int, int]], lang: Language) -> str:
    """Productive hours left today, as [start hour, end hour) ranges (#13)."""
    if lang is Language.VI:
        parts = ", ".join(f"{start}h–{end}h" for start, end in ranges)
        return checked(f"Khung giờ bạn làm tốt còn lại hôm nay: {parts}.")
    parts = ", ".join(f"{start:02d}:00–{end:02d}:00" for start, end in ranges)
    return checked(f"Your best working hours left today: {parts}.")
