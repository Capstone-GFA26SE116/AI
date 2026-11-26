"""FR-10g: the one-line reason of each slot, written by code from its facts (no LLM).

Same facts and rules as prompt `schedule_reason_v4`: at most 2 short sentences,
≤ 250 characters, "mình" / "bạn", no clock times, no internal names, the extra time
only on the first part of a task. The wording rotates between slots so reasons do
not all start the same way.

Sentence 1: why this time (what blocks earlier, focus hour, spread out, next part).
Sentence 2: the most useful other fact (extra time, deadline, split), if any.
"""

from collections.abc import Sequence

from metanoia_ai.domain.models import BlockedBy, Language, Placement, SplitReason
from metanoia_ai.domain.schedule_text import SlotFacts, short_title

MAX_REASON_LENGTH = 250
VI, EN = 0, 1

WHY_BLOCKED: dict[BlockedBy, tuple[tuple[str, str], ...]] = {
    BlockedBy.MEETING: (
        (
            "Trước giờ này bạn có cuộc họp, nên {t} bắt đầu ngay sau đó.",
            "You have a meeting before this, so {t} starts right after it.",
        ),
        (
            "Khoảng trống trước đó bị cuộc họp chiếm, mình đặt {t} vào đây.",
            "A meeting takes the earlier time, so {t} goes here.",
        ),
    ),
    BlockedBy.CALENDAR_EVENT: (
        (
            "Trước đó đã có lịch trên Google Calendar, nên {t} làm lúc này.",
            "Your calendar already has an event before this, so {t} goes here.",
        ),
        (
            "Lịch của bạn đã bận trước giờ này, mình xếp {t} vào khoảng trống kế tiếp.",
            "Your calendar is busy before this, so {t} takes the next free time.",
        ),
    ),
    BlockedBy.APPROVED_SLOT: (
        (
            "Trước đó là việc bạn đã duyệt, nên {t} làm tiếp sau.",
            "Work you already approved comes first, so {t} follows it.",
        ),
        (
            "Giờ sớm hơn đã có việc bạn duyệt rồi, {t} xếp ngay sau.",
            "Earlier time is taken by work you approved, so {t} comes next.",
        ),
    ),
    BlockedBy.OTHER_TASK: (
        (
            "Việc có hạn gần hơn được làm trước, {t} theo sau.",
            "A task due sooner goes first, then {t}.",
        ),
        (
            "Mình ưu tiên việc gấp hơn trước, rồi mới tới {t}.",
            "I put the more urgent task first, then {t}.",
        ),
    ),
    BlockedBy.OUTSIDE_WORK_HOURS: (
        (
            "Khung giờ làm trước đó đã kín, nên {t} bắt đầu ở khung giờ làm kế tiếp.",
            "The earlier work hours were already full, so {t} starts in the next ones.",
        ),
        (
            "Giờ làm trước đó không còn chỗ, mình dời {t} sang khung giờ làm sau.",
            "There was no room in the earlier work hours, so {t} moves to the next ones.",
        ),
    ),
}
WHY_FOCUS = (
    (
        "Đây là lúc bạn tập trung tốt nhất, nên mình để {t} vào giờ này.",
        "This is when you focus best, so I kept {t} for this time.",
    ),
    (
        "{t} cần tập trung, mình chờ tới giờ bạn làm hiệu quả nhất.",
        "{t} needs focus, so it waits for your most productive hours.",
    ),
)
WHY_SPREAD = (
    (
        "Mình rải việc ra các ngày để không ngày nào quá nặng.",
        "I spread the work over the days so no day gets too heavy.",
    ),
    (
        "Đổi sang việc khác cho đỡ mỏi, {t} được xếp ở đây.",
        "Switching jobs keeps you fresh, so {t} goes here.",
    ),
)
WHY_EARLIEST = (
    (
        "Đây là khoảng trống sớm nhất vừa đủ cho {t}.",
        "This is the earliest free time that fits {t}.",
    ),
    ("{t} được xếp vào giờ trống sớm nhất.", "{t} takes the earliest free time."),
)
NEXT_PART = (
    ("Làm tiếp phần còn lại của {t}.", "The next block of {t}."),
    ("Phần tiếp theo của {t}.", "Continuing {t}."),
)
SPLIT = {
    SplitReason.LONG_WORK: (
        "Việc dài nên mình chia thành các khối tối đa 3 tiếng, có nghỉ giữa.",
        "It is long work, so I cut it into blocks of at most 3 hours with breaks.",
    ),
    SplitReason.NO_LONG_GAP: (
        "Không có khoảng trống đủ dài nên làm thành nhiều phần.",
        "There is no free time long enough, so it is done in parts.",
    ),
}


def _extra(facts: SlotFacts, lang: int) -> str | None:
    """The extra time, only on the first part (v4 rule)."""
    if facts.part_index != 1 or facts.extra_percent == 0:
        return None
    pct = abs(facts.extra_percent)
    if not facts.extra_applied:
        if facts.extra_percent > 0:
            return (
                f"Mẹo: bạn thường cần thêm khoảng {pct}% thời gian so với dự kiến.",
                f"Tip: you usually need about {pct}% more time than planned.",
            )[lang]
        return (
            f"Mẹo: bạn thường xong nhanh hơn dự kiến khoảng {pct}%.",
            f"Tip: you are usually about {pct}% faster than planned.",
        )[lang]
    est, kept = facts.estimate_minutes, facts.planned_minutes
    if facts.extra_percent > 0:
        vi = f"Dự kiến {est} phút, mình giữ {kept} phút vì bạn thường cần thêm khoảng {pct}%."
        en = f"Planned {est} min; I kept {kept} because you usually need about {pct}% more."
    else:
        vi = f"Dự kiến {est} phút, mình giữ {kept} phút vì bạn thường nhanh hơn khoảng {pct}%."
        en = f"Planned {est} min; I kept {kept} because you are usually about {pct}% faster."
    return (vi, en)[lang]


def _deadline(facts: SlotFacts, lang: int) -> str | None:
    if facts.spare_hours <= 0:
        return ("Xong là vừa sát hạn chót.", "It ends right before the deadline.")[lang]
    if facts.days_to_deadline == 0:
        return (
            "Hạn chót trong ngày nên cần làm sớm.",
            "It is due the same day, so it goes early.",
        )[lang]
    if facts.spare_hours >= 24:
        days = facts.spare_hours // 24
        return (
            f"Xong vẫn còn dư khoảng {days} ngày trước hạn.",
            f"You still have about {days} day{'s' if days > 1 else ''} to spare.",
        )[lang]
    return (
        f"Xong còn khoảng {facts.spare_hours} giờ trước hạn.",
        f"About {facts.spare_hours} h are left before the deadline.",
    )[lang]


def _why(facts: SlotFacts, lang: int, turn: int, title: str) -> str:
    if facts.part_index > 1:
        options = NEXT_PART
    elif facts.earlier_blocked_by in WHY_BLOCKED:
        options = WHY_BLOCKED[facts.earlier_blocked_by]
    elif facts.placement is Placement.WAITED_FOR_FOCUS_HOUR:
        options = WHY_FOCUS
    elif facts.placement is Placement.SPREAD_OUT:
        options = WHY_SPREAD
    else:
        options = WHY_EARLIEST
    sentence = options[turn % len(options)][lang].format(t=title)
    return sentence[0].upper() + sentence[1:]


def template_reason(facts: SlotFacts, language: Language, turn: int = 0) -> str:
    """Two short sentences at most. `turn` = the slot's place, to vary the wording."""
    lang = EN if language is Language.EN else VI
    title = short_title(facts.task_title)
    why = _why(facts, lang, turn, title)
    second = _extra(facts, lang)
    if second is None and facts.part_count > 1 and facts.split_reason is not None:
        second = SPLIT[facts.split_reason][lang] if facts.part_index == 1 else None
    if second is None:
        second = _deadline(facts, lang)
    text = f"{why} {second}" if second else why
    if len(text) > MAX_REASON_LENGTH:
        text = (
            why if len(why) <= MAX_REASON_LENGTH else why[: MAX_REASON_LENGTH - 1] + "…"
        )
    return text


def template_reasons(facts: Sequence[SlotFacts], language: Language) -> list[str]:
    """Reasons in slot order; the wording turns over from one slot to the next."""
    return [template_reason(f, language, i) for i, f in enumerate(facts)]
