"""FR-20 sentences, built from fixed templates (vi / en). No LLM (#2 of plan §1).

Percentages in sentences are rounded to a multiple of 5 (#5). Every template is checked
for internal names before the user's own names (project, client, domain) are filled in,
so a project called "Ratio study" is still fine.
"""

from decimal import ROUND_HALF_UP, Decimal

from metanoia_ai.domain.analytics import (
    BiasConfidence,
    BiasDirection,
    BreakdownItem,
    CompletionStats,
    DomainBias,
    Insight,
    InsightCode,
)
from metanoia_ai.domain.forbidden_terms import InternalNameError, has_internal_name
from metanoia_ai.domain.models import Language

PERCENT_STEP = 5
DIFFERENCE_BAND = 10  # |difference| below this: "close to the plan"
# Analytics field names that must never reach the user, on top of the shared ones.
FORBIDDEN_WORDS = (
    "difference_percent",
    "missing_actual",
    "on_time_rate",
    "completion_rate",
    "sample_count",
    "not_enough_data",
)


def _say(template: str, **names: object) -> str:
    """Check the template, then fill in numbers and the user's own names."""
    if has_internal_name(template, FORBIDDEN_WORDS):
        raise InternalNameError("analytics text shows an internal name")
    return template.format(**names)


def rounded_percent(percent: int) -> int:
    """31 → 30, 33 → 35: no false precision in sentences (#5)."""
    step = Decimal(abs(percent)) / PERCENT_STEP
    return int(step.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * PERCENT_STEP


def _tasks(count: int, lang: Language) -> str:
    if lang is Language.VI:
        return f"{count} việc"
    return "1 task" if count == 1 else f"{count} tasks"


def completion_text(stats: CompletionStats, lang: Language) -> str:
    vi = lang is Language.VI
    if stats.total == 0 or stats.completion_rate is None:
        return _say(
            "Chưa có việc nào tới hạn trong khoảng thời gian này."
            if vi
            else "No tasks were due in this period."
        )
    percent = int((stats.completion_rate * 100).quantize(Decimal(1), ROUND_HALF_UP))
    if stats.done == 0:
        return _say(
            "Bạn chưa hoàn thành việc nào trong {total} việc tới hạn."
            if vi
            else "You have not finished any of the {total} tasks that were due.",
            total=stats.total,
        )
    return _say(
        "Bạn hoàn thành {done}/{total} việc ({percent}%), "
        "trong đó {on_time} việc xong đúng hạn."
        if vi
        else "You finished {done} of {total} tasks ({percent}%), {on_time} of them on time.",
        done=stats.done,
        total=stats.total,
        percent=percent,
        on_time=stats.on_time_done,
    )


def domain_text(domain: DomainBias, lang: Language) -> str:
    """The bias sentence of one domain (#3, #4)."""
    vi = lang is Language.VI
    view, name = domain.view, domain.domain_name
    if view.confidence is BiasConfidence.NONE:
        return _say(
            "Chưa đủ dữ liệu cho {name} (mới có {count})."
            if vi
            else "Not enough data for {name} yet (only {count}).",
            name=name,
            count=_tasks(view.samples, lang),
        )
    if view.direction is BiasDirection.ACCURATE:
        sentence = (
            "Bạn ước lượng {name} khá sát"
            if vi
            else "Your estimates for {name} are fairly accurate"
        )
    elif view.direction is BiasDirection.SLOWER:
        sentence = (
            "Bạn thường cần thêm khoảng {percent}% thời gian cho {name}"
            if vi
            else "You usually need about {percent}% more time for {name}"
        )
    else:
        sentence = (
            "Bạn thường làm {name} nhanh hơn dự kiến khoảng {percent}%"
            if vi
            else "You usually finish {name} about {percent}% faster than planned"
        )
    if view.confidence is BiasConfidence.LEARNING:
        sentence += (
            " (đang học, dựa trên {count})"
            if vi
            else " (still learning, based on {count})"
        )
    return _say(
        sentence + ".",
        name=name,
        percent=rounded_percent(view.percent or 0),
        count=_tasks(view.samples, lang),
    )


def difference_text(percent: int, lang: Language) -> str:
    """Planned vs actual of a project, client or task."""
    vi = lang is Language.VI
    if percent >= DIFFERENCE_BAND:
        template = (
            "Tốn nhiều hơn dự kiến khoảng {p}%."
            if vi
            else "Took about {p}% longer than planned."
        )
    elif percent <= -DIFFERENCE_BAND:
        template = (
            "Nhanh hơn dự kiến khoảng {p}%."
            if vi
            else "About {p}% faster than planned."
        )
    else:
        template = "Gần đúng dự kiến." if vi else "Close to the plan."
    return _say(template, p=rounded_percent(percent))


def no_client_name(lang: Language) -> str:
    """Name of the group of projects without a client (#11)."""
    return _say("Không có khách hàng" if lang is Language.VI else "No client")


def item_name(item: BreakdownItem, lang: Language) -> str:
    """The item's own name; the "no client" group gets its fixed name."""
    return item.name if item.name is not None else no_client_name(lang)


def insight_text(insight: Insight, lang: Language) -> str:
    """One Dashboard insight (plan §2 #10). Domain insights reuse the domain sentence."""
    vi = lang is Language.VI
    match insight.code:
        case InsightCode.DOMAIN_SLOWER | InsightCode.DOMAIN_FASTER:
            if insight.domain is None:
                raise ValueError("domain insight needs a domain")
            return domain_text(insight.domain, lang)
        case InsightCode.PROJECT_OVER | InsightCode.PROJECT_UNDER:
            if insight.project is None:
                raise ValueError("project insight needs a project")
            over = insight.code is InsightCode.PROJECT_OVER
            if vi:
                template = (
                    "Dự án {name} tốn nhiều hơn dự kiến khoảng {p}%."
                    if over
                    else "Dự án {name} nhanh hơn dự kiến khoảng {p}%."
                )
            else:
                template = (
                    "Project {name} took about {p}% longer than planned."
                    if over
                    else "Project {name} went about {p}% faster than planned."
                )
            return _say(
                template,
                name=item_name(insight.project, lang),
                p=rounded_percent(insight.project.difference_percent),
            )
        case InsightCode.LOW_COMPLETION:
            return _say(
                "Gần đây nhiều việc bị trễ hạn — thử chia nhỏ việc lớn."
                if vi
                else "Many tasks missed their deadline lately — try splitting big tasks."
            )
        case InsightCode.NOT_ENOUGH_DATA:
            return _say(
                "Chưa đủ dữ liệu để nhận xét. Cứ dùng đồng hồ khi làm việc, METANOIA sẽ hiểu bạn dần."
                if vi
                else "Not enough data for insights yet. Keep using the timer and METANOIA will learn."
            )
