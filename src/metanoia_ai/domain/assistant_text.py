"""FR-22 replies read aloud by the 3D character, from fixed templates (vi / en). No LLM.

Every reply is at most 60 words (AC5): long lists are cut ("and N more"), long names
are shortened. Times read like speech ("9 giờ 30", "9:30 am"), never ISO; no "task(s)".
Templates are checked for internal names before the user's own names are filled in.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from metanoia_ai.domain.assistant_tools import SmalltalkKind, ToolName
from metanoia_ai.domain.briefing_text import duration
from metanoia_ai.domain.forbidden_terms import InternalNameError, has_internal_name
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.spoken_period import SpokenPeriod

MAX_WORDS = 60
MAX_NAME_WORDS = 6  # a spoken name longer than this is cut with "…"
SCHEDULE_ITEMS = 4  # read at most this many lines (3–5 in the plan)
AT_RISK_ITEMS = 2
TOP_ITEMS = 3
PROJECT_ITEMS = 5
OPTION_ITEMS = 3
SIMILAR_SLOWER_RATIO = 1.1  # below this, "took X times the plan" is not worth saying
# Field and tool names that must never be read aloud, on top of the shared ones.
FORBIDDEN_WORDS = (
    "score",
    "elapsed",
    "overrun",
    "progress_percent",
    "ai_call",
    *(t.value for t in ToolName),
)
MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class Emotion(StrEnum):
    """How the 3D character looks while reading the reply. Sent in the API."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    THINKING = "thinking"
    SAD = "sad"


@dataclass(frozen=True)
class TimedLine:
    """A schedule item or a deadline: what and when."""

    title: str
    at: datetime


def word_count(text: str) -> int:
    return len(text.split())


def _say(template: str, **values: object) -> str:
    """Check the template, then fill in numbers and the user's own names."""
    if has_internal_name(template, FORBIDDEN_WORDS):
        raise InternalNameError("assistant text shows an internal name")
    text = template.format(**values)
    if word_count(text) > MAX_WORDS:
        raise ValueError(f"reply is longer than {MAX_WORDS} words")
    return text


def short_name(name: str) -> str:
    """At most MAX_NAME_WORDS words, so a list of names stays speakable."""
    words = name.split()
    if len(words) <= MAX_NAME_WORDS:
        return " ".join(words)
    return " ".join(words[:MAX_NAME_WORDS]) + "…"


def _count(n: int, noun_vi: str, noun_en: str, lang: Language) -> str:
    """ "3 việc" / "1 task", "3 tasks"."""
    if lang is Language.VI:
        return f"{n} {noun_vi}"
    return f"{n} {noun_en}" if n == 1 else f"{n} {noun_en}s"


def _join(parts: Sequence[str], lang: Language) -> str:
    """ "a, b và c" / "a, b and c". Parts are used as given (shorten names first)."""
    if len(parts) <= 1:
        return "".join(parts)
    last = " và " if lang is Language.VI else " and "
    return ", ".join(parts[:-1]) + last + parts[-1]


def _more(left: int, noun_vi: str, noun_en: str, lang: Language) -> str:
    """ ", và còn 2 việc khác" / ", and 2 more tasks"; empty when nothing is left."""
    if left <= 0:
        return ""
    if lang is Language.VI:
        return f", và còn {left} {noun_vi} khác"
    return f", and {left} more {noun_en if left == 1 else noun_en + 's'}"


def spoken_time(moment: datetime, lang: Language) -> str:
    """ "9 giờ 30" / "14 giờ"; "9:30 am" / "2 pm". `moment` already in the user's zone."""
    hour, minute = moment.hour, moment.minute
    if lang is Language.VI:
        return f"{hour} giờ" + (f" {minute}" if minute else "")
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}" if minute else f"{hour12} {suffix}"


def spoken_day(moment: datetime, now: datetime, lang: Language) -> str:
    """ "hôm nay" / "ngày mai" / "ngày 3/11"; "today" / "tomorrow" / "on Nov 3".

    Both datetimes already in the user's zone.
    """
    days = (moment.date() - now.date()).days
    vi = lang is Language.VI
    if days == 0:
        return "hôm nay" if vi else "today"
    if days == 1:
        return "ngày mai" if vi else "tomorrow"
    if vi:
        return f"ngày {moment.day}/{moment.month}"
    return f"on {MONTHS[moment.month - 1]} {moment.day}"


# --- the 10 read tools ---


def today_briefing_text(
    scheduled: int, top_titles: Sequence[str], at_risk: int, lang: Language
) -> str:
    """FR-12 in one breath: how many items today, what to do first, how many at risk."""
    vi = lang is Language.VI
    if scheduled:
        template = (
            "Hôm nay bạn có {items} trên lịch."
            if vi
            else "You have {items} on your calendar today."
        )
    else:
        template = (
            "Hôm nay lịch của bạn còn trống." if vi else "Your calendar is free today."
        )
    if top_titles:
        template += " Nên làm trước: {top}." if vi else " Start with {top}."
    elif not scheduled:
        template += (
            " Bạn chưa có việc nào cần làm." if vi else " You have no open tasks."
        )
    if at_risk:
        template += (
            " Có {risk} có nguy cơ trễ hạn." if vi else " {risk} may miss the deadline."
        )
    return _say(
        template,
        items=_count(scheduled, "việc", "item", lang),
        top=_join([short_name(t) for t in top_titles[:TOP_ITEMS]], lang),
        risk=_count(at_risk, "việc", "task", lang),
    )


def at_risk_text(
    items: Sequence[TimedLine], now: datetime, zone: ZoneInfo, lang: Language
) -> str:
    """At most 2 names with their deadlines, then "and N more"."""
    vi = lang is Language.VI
    if not items:
        return _say(
            "Hiện không có việc nào có nguy cơ trễ hạn."
            if vi
            else "No task is at risk of missing its deadline right now."
        )
    now = now.astimezone(zone)
    parts = []
    for item in items[:AT_RISK_ITEMS]:
        at = item.at.astimezone(zone)
        when = f"{spoken_time(at, lang)} {spoken_day(at, now, lang)}"
        parts.append(f"{short_name(item.title)} ({'hạn' if vi else 'due'} {when})")
    template = (
        "Có {n} có nguy cơ trễ hạn: {items}{more}."
        if vi
        else "{n} may miss the deadline: {items}{more}."
    )
    return _say(
        template,
        n=_count(len(items), "việc", "task", lang),
        items=_join(parts, lang),
        more=_more(len(items) - AT_RISK_ITEMS, "việc", "task", lang),
    )


PERIOD_NAMES = {
    SpokenPeriod.MORNING: ("Sáng nay", "This morning"),
    SpokenPeriod.AFTERNOON: ("Chiều nay", "This afternoon"),
    SpokenPeriod.EVENING: ("Tối nay", "This evening"),
    SpokenPeriod.TODAY: ("Hôm nay", "Today"),
    SpokenPeriod.TOMORROW: ("Ngày mai", "Tomorrow"),
}


def schedule_text(
    period: SpokenPeriod, items: Sequence[TimedLine], zone: ZoneInfo, lang: Language
) -> str:
    """The first items of the period with their start time, then "and N more"."""
    vi = lang is Language.VI
    when = PERIOD_NAMES[period][0 if vi else 1]
    if not items:
        template = (
            "{when} bạn chưa có lịch nào."
            if vi
            else "{when} you have nothing scheduled."
        )
        return _say(template, when=when)
    lines = [
        f"{spoken_time(i.at.astimezone(zone), lang)} {short_name(i.title)}"
        for i in items[:SCHEDULE_ITEMS]
    ]
    template = (
        "{when} bạn có {n}: {lines}{more}."
        if vi
        else "{when} you have {n}: {lines}{more}."
    )
    return _say(
        template,
        when=when,
        n=_count(len(items), "việc", "item", lang),
        lines=_join(lines, lang),
        more=_more(len(items) - SCHEDULE_ITEMS, "việc", "item", lang),
    )


def running_timer_text(
    title: str | None, elapsed_minutes: int, overrun: bool, lang: Language
) -> str:
    """`title` None: no timer is running."""
    vi = lang is Language.VI
    if title is None:
        return _say(
            "Hiện bạn không bấm giờ việc nào."
            if vi
            else "You are not timing any task right now."
        )
    template = (
        "Bạn đang bấm giờ cho {title}, được {spent} rồi."
        if vi
        else "You are timing {title}, {spent} so far."
    )
    if overrun:
        template += (
            " Việc này đã vượt thời gian dự kiến."
            if vi
            else " It has gone past the planned time."
        )
    return _say(
        template, title=short_name(title), spent=duration(max(elapsed_minutes, 1), lang)
    )


def estimation_habits_text(insights: Sequence[str], lang: Language) -> str:
    """FR-20 insight sentences (already checked there), at most 2."""
    if not insights:
        return not_enough_data_text(lang)
    return _say("{text}", text=" ".join(insights[:2]))


def _hour(hour: int, lang: Language) -> str:
    hour %= 24
    if lang is Language.VI:
        return f"{hour} giờ"
    return f"{hour % 12 or 12} {'am' if hour < 12 else 'pm'}"


def productive_hours_text(ranges: Sequence[tuple[int, int]], lang: Language) -> str:
    """Up to 2 [start hour, end hour) ranges from FR-18."""
    vi = lang is Language.VI
    if not ranges:
        return not_enough_data_text(lang)
    parts = [
        f"{_hour(start, lang)} {'đến' if vi else 'to'} {_hour(end, lang)}"
        for start, end in ranges[:2]
    ]
    template = (
        "Bạn thường làm việc tốt nhất khoảng {parts}."
        if vi
        else "You usually work best around {parts}."
    )
    return _say(template, parts=_join(parts, lang))


def similar_task_text(
    title: str,
    count: int,
    typical_minutes: int | None,
    average_ratio: float | None,
    lang: Language,
) -> str:
    """FR-09 summary for one task: how long similar past tasks took."""
    vi = lang is Language.VI
    if count == 0 or typical_minutes is None:
        template = (
            "Mình chưa thấy việc nào giống {title} trước đây."
            if vi
            else "I have not seen any task like {title} before."
        )
        return _say(template, title=short_name(title))
    template = (
        "Có {n} giống {title} trước đây, thường mất {spent}."
        if vi
        else "{n} in the past were like {title}; they usually took {spent}."
    )
    times = ""
    if average_ratio is not None and average_ratio >= SIMILAR_SLOWER_RATIO:
        value = Decimal(str(average_ratio)).quantize(Decimal("0.1"), ROUND_HALF_UP)
        times = f"{value}".replace(".", ",") if vi else f"{value}"
        template += (
            " Thường lâu gấp {times} lần dự kiến."
            if vi
            else " That is about {times} times the plan."
        )
    return _say(
        template,
        n=_count(count, "việc", "task", lang),
        title=short_name(title),
        spent=duration(typical_minutes, lang),
        times=times,
    )


def project_progress_text(
    name: str,
    done: int,
    total: int,
    percent: float,
    deadline: datetime | None,
    now: datetime,
    zone: ZoneInfo,
    lang: Language,
) -> str:
    """Tasks done / total and the project deadline (a local date), if any."""
    vi = lang is Language.VI
    if total == 0:
        template = (
            "Dự án {name} chưa có việc nào."
            if vi
            else "Project {name} has no tasks yet."
        )
        return _say(template, name=short_name(name))
    template = (
        "Dự án {name} đã xong {done}/{total} việc, khoảng {p}%."
        if vi
        else "Project {name}: {done} of {total} tasks done, about {p}%."
    )
    day = ""
    if deadline is not None:
        day = spoken_day(deadline.astimezone(zone), now.astimezone(zone), lang)
        template += " Hạn là {day}." if vi else " It is due {day}."
    p = int(Decimal(str(percent)).quantize(Decimal(1), ROUND_HALF_UP))
    return _say(template, name=short_name(name), done=done, total=total, p=p, day=day)


def list_projects_text(names: Sequence[str], lang: Language) -> str:
    """At most 5 active project names, then "and N more"."""
    vi = lang is Language.VI
    if not names:
        return _say(
            "Bạn chưa có dự án nào đang làm." if vi else "You have no active projects."
        )
    template = (
        "Bạn đang làm {n}: {names}{more}." if vi else "You have {n}: {names}{more}."
    )
    return _say(
        template,
        n=_count(len(names), "dự án", "active project", lang),
        names=_join([short_name(n) for n in names[:PROJECT_ITEMS]], lang),
        more=_more(len(names) - PROJECT_ITEMS, "dự án", "project", lang),
    )


def ai_quota_text(left: int | None, lang: Language) -> str:
    """`left` None: the plan has no limit."""
    vi = lang is Language.VI
    if left is None:
        return _say(
            "Gói của bạn dùng AI không giới hạn."
            if vi
            else "Your plan has unlimited AI use."
        )
    template = (
        "Bạn còn {left} lượt dùng AI trong kỳ này."
        if vi
        else "You have {left} AI uses left this period."
    )
    return _say(template, left=max(left, 0))


# --- other kinds of turn ---


def unsupported_text(lang: Language) -> str:
    return _say(
        "Mình chưa làm được việc này. Tạo, sửa hay xoá thì bạn làm trên màn hình nhé."
        if lang is Language.VI
        else "I can't do that yet. Please create, change or delete things on the screen."
    )


def smalltalk_text(kind: SmalltalkKind, lang: Language) -> str:
    vi = lang is Language.VI
    match kind:
        case SmalltalkKind.GREET:
            text = (
                "Chào bạn! Mình có thể giúp bạn xem lịch, deadline và tiến độ dự án."
                if vi
                else "Hi! I can help you check your schedule, deadlines and project progress."
            )
        case SmalltalkKind.THANKS:
            text = (
                "Không có gì, cần gì cứ hỏi mình nhé."
                if vi
                else "You're welcome, just ask anytime."
            )
        case SmalltalkKind.HELP:
            text = (
                "Bạn có thể hỏi mình về lịch hôm nay, deadline gấp, "
                "tiến độ dự án hay giờ làm việc hiệu quả."
                if vi
                else "You can ask me about today's plan, urgent deadlines, "
                "project progress or your best working hours."
            )
    return _say(text)


def clarify_text(
    kind_vi: str, kind_en: str, names: Sequence[str], lang: Language
) -> str:
    """Several names fit (#6): ask which one. `kind`: "dự án" / "project", "việc" / "task"."""
    vi = lang is Language.VI
    options = [short_name(n) for n in names[:OPTION_ITEMS]]
    joiner = " hay " if vi else " or "
    listed = (
        ", ".join(options[:-1]) + joiner + options[-1]
        if len(options) > 1
        else options[0]
    )
    template = (
        "Bạn muốn hỏi {kind} nào: {listed}?"
        if vi
        else "Which {kind} do you mean: {listed}?"
    )
    return _say(template, kind=kind_vi if vi else kind_en, listed=listed)


def not_found_text(kind_vi: str, kind_en: str, name: str | None, lang: Language) -> str:
    """No name was said, or nothing close to it was found (#6)."""
    vi = lang is Language.VI
    kind = kind_vi if vi else kind_en
    if name is None:
        template = (
            "Bạn nói rõ tên {kind} giúp mình nhé."
            if vi
            else "Please tell me the {kind} name."
        )
        return _say(template, kind=kind)
    template = (
        "Mình không tìm thấy {kind} nào tên {name}."
        if vi
        else "I couldn't find a {kind} called {name}."
    )
    return _say(template, kind=kind, name=short_name(name))


def source_error_text(lang: Language) -> str:
    """A data source failed or was too slow (#13)."""
    return _say(
        "Mình chưa lấy được thông tin này, bạn thử lại sau nhé."
        if lang is Language.VI
        else "I couldn't get that information right now, please try again later."
    )


def not_enough_data_text(lang: Language) -> str:
    return _say(
        "Mình chưa đủ dữ liệu, bạn làm thêm vài việc rồi hỏi lại nhé."
        if lang is Language.VI
        else "I don't have enough data yet. Finish a few more tasks and ask again."
    )


SUGGESTIONS = {
    Language.VI: (
        "Hôm nay tôi nên làm gì?",
        "Deadline nào đang gấp?",
        "Chiều nay tôi có lịch gì?",
    ),
    Language.EN: (
        "What should I do today?",
        "Which deadlines are urgent?",
        "What's on my schedule this afternoon?",
    ),
}


def suggestions(lang: Language) -> tuple[str, ...]:
    """2–3 example questions for `unsupported` and `smalltalk` help."""
    return SUGGESTIONS[lang]
