from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.assistant_text import (
    MAX_WORDS,
    DueMilestone,
    TimedLine,
    _say,
    ai_quota_text,
    at_risk_text,
    clarify_text,
    estimation_habits_text,
    list_projects_text,
    not_enough_data_text,
    not_found_text,
    pending_confirmations_text,
    pending_proposal_text,
    productive_hours_text,
    project_progress_text,
    running_timer_text,
    schedule_text,
    short_name,
    similar_task_text,
    smalltalk_text,
    source_error_text,
    spoken_day,
    spoken_time,
    suggestions,
    today_briefing_text,
    unread_notifications_text,
    unsupported_text,
    upcoming_milestones_text,
    word_count,
)
from metanoia_ai.domain.assistant_tools import SmalltalkKind
from metanoia_ai.domain.forbidden_terms import InternalNameError, has_internal_name
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.spoken_period import SpokenPeriod

VI, EN = Language.VI, Language.EN
VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=VN)
LONG = "Thiết kế lại toàn bộ trang đích cho khách hàng lớn nhất năm nay"


def at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    return NOW.replace(hour=hour, minute=minute) + timedelta(days=days)


def test_spoken_time_and_day() -> None:
    assert spoken_time(at(9, 30), VI) == "9 giờ 30"
    assert spoken_time(at(14), VI) == "14 giờ"
    assert spoken_time(at(9, 30), EN) == "9:30 am"
    assert spoken_time(at(0), EN) == "12 am"
    assert spoken_time(at(14), EN) == "2 pm"
    assert spoken_day(at(17), NOW, VI) == "hôm nay"
    assert spoken_day(at(17, days=1), NOW, EN) == "tomorrow"
    assert spoken_day(at(17, days=4), NOW, VI) == "ngày 3/11"
    assert spoken_day(at(17, days=4), NOW, EN) == "on Nov 3"


def test_short_name() -> None:
    assert short_name("  Viết   bài blog ") == "Viết bài blog"
    assert short_name(LONG) == "Thiết kế lại toàn bộ trang…"


def test_today_briefing() -> None:
    assert today_briefing_text(3, ["Viết bài", "Họp khách"], 1, VI) == (
        "Hôm nay bạn có 3 việc trên lịch. Nên làm trước: Viết bài và Họp khách. "
        "Có 1 việc có nguy cơ trễ hạn."
    )
    assert (
        today_briefing_text(1, [], 0, EN) == "You have 1 item on your calendar today."
    )
    assert today_briefing_text(0, [], 0, VI) == (
        "Hôm nay lịch của bạn còn trống. Bạn chưa có việc nào cần làm."
    )


def test_at_risk_reads_two_names_and_counts_the_rest() -> None:
    items = [
        TimedLine("Báo cáo", at(17)),
        TimedLine("Landing page", at(9, days=1)),
        TimedLine("Logo", at(9, days=2)),
    ]

    assert at_risk_text(items, NOW, VN, VI) == (
        "Có 3 việc có nguy cơ trễ hạn: Báo cáo (hạn 17 giờ hôm nay) và "
        "Landing page (hạn 9 giờ ngày mai), và còn 1 việc khác."
    )
    assert at_risk_text(items[:1], NOW, VN, EN) == (
        "1 task may miss the deadline: Báo cáo (due 5 pm today)."
    )
    assert at_risk_text([], NOW, VN, VI).startswith("Hiện không có việc nào")


def test_schedule_lists_four_then_more() -> None:
    items = [TimedLine(f"Việc {i}", at(13 + i)) for i in range(6)]

    text = schedule_text(SpokenPeriod.AFTERNOON, items, VN, VI)

    assert text == (
        "Chiều nay bạn có 6 việc: 13 giờ Việc 0, 14 giờ Việc 1, 15 giờ Việc 2 "
        "và 16 giờ Việc 3, và còn 2 việc khác."
    )
    assert schedule_text(SpokenPeriod.TOMORROW, [], VN, EN) == (
        "Tomorrow you have nothing scheduled."
    )
    one = schedule_text(SpokenPeriod.MORNING, items[:1], VN, EN)
    assert one == "This morning you have 1 item: 1 pm Việc 0."


def test_running_timer() -> None:
    assert running_timer_text("Viết bài", 95, False, VI) == (
        "Bạn đang bấm giờ cho Viết bài, được khoảng 1,5 giờ rồi."
    )
    assert running_timer_text("Logo", 30, True, EN).endswith(
        "gone past the planned time."
    )
    assert running_timer_text(None, 0, False, VI) == "Hiện bạn không bấm giờ việc nào."


def test_habits_and_productive_hours() -> None:
    insights = ["Câu 1.", "Câu 2.", "Câu 3."]
    assert estimation_habits_text(insights, VI) == "Câu 1. Câu 2."
    assert estimation_habits_text([], VI) == not_enough_data_text(VI)
    assert productive_hours_text([(9, 11), (20, 22)], VI) == (
        "Bạn thường làm việc tốt nhất khoảng 9 giờ đến 11 giờ và 20 giờ đến 22 giờ."
    )
    assert (
        productive_hours_text([(9, 11)], EN)
        == "You usually work best around 9 am to 11 am."
    )
    assert productive_hours_text([], EN) == not_enough_data_text(EN)


def test_similar_task() -> None:
    assert similar_task_text("Viết blog", 3, 90, 1.46, VI) == (
        "Có 3 việc giống Viết blog trước đây, thường mất khoảng 1,5 giờ. "
        "Thường lâu gấp 1,5 lần dự kiến."
    )
    assert similar_task_text("Viết blog", 2, 45, 1.0, EN) == (
        "2 tasks in the past were like Viết blog; they usually took about 45 minutes."
    )
    assert similar_task_text("Viết blog", 0, None, None, VI).startswith(
        "Mình chưa thấy"
    )


def test_project_progress() -> None:
    assert project_progress_text(
        "Landing page", 3, 8, 37.5, at(0, days=1), NOW, VN, VI
    ) == ("Dự án Landing page đã xong 3/8 việc, khoảng 38%. Hạn là ngày mai.")
    assert project_progress_text("Logo", 1, 2, 50, None, NOW, VN, EN) == (
        "Project Logo: 1 of 2 tasks done, about 50%."
    )
    assert project_progress_text("Logo", 0, 0, 0, None, NOW, VN, VI) == (
        "Dự án Logo chưa có việc nào."
    )


def test_list_projects() -> None:
    names = [f"P{i}" for i in range(7)]
    assert list_projects_text(names, VI) == (
        "Bạn đang làm 7 dự án: P0, P1, P2, P3 và P4, và còn 2 dự án khác."
    )
    assert list_projects_text(names[:1], EN) == "You have 1 active project: P0."
    assert list_projects_text([], VI) == "Bạn chưa có dự án nào đang làm."


def test_ai_quota() -> None:
    assert ai_quota_text(12, VI) == "Bạn còn 12 lượt dùng AI trong kỳ này."
    assert ai_quota_text(-3, EN) == "You have 0 AI uses left this period."
    assert ai_quota_text(None, VI) == "Gói của bạn dùng AI không giới hạn."


def test_clarify_and_not_found() -> None:
    assert clarify_text("dự án", "project", ["Landing A", "Landing B"], VI) == (
        "Bạn muốn hỏi dự án nào: Landing A hay Landing B?"
    )
    assert clarify_text("dự án", "project", ["A", "B", "C", "D"], EN) == (
        "Which project do you mean: A, B or C?"
    )
    assert not_found_text("việc", "task", "báo cáo", VI) == (
        "Mình không tìm thấy việc nào tên báo cáo."
    )
    assert (
        not_found_text("dự án", "project", None, EN)
        == "Please tell me the project name."
    )


def test_pending_confirmations() -> None:
    assert (
        pending_confirmations_text([("Viết blog", 95)], VI)
        == "Có 1 phiên bấm giờ cần bạn xác nhận: Viết blog (khoảng 1,5 giờ). "
        "Bạn xác nhận trên màn hình nhé."
    )
    three = [("A", 30), ("B", 0), ("C", 50)]
    assert (
        pending_confirmations_text(three, EN)
        == "3 timer sessions need your confirmation: A (about 30 minutes) and "
        "B (about 5 minutes), and 1 more session. Please confirm on the screen."
    )
    assert (
        pending_confirmations_text([], VI)
        == "Không có phiên bấm giờ nào cần bạn xác nhận."
    )


def test_unread_notifications() -> None:
    assert (
        unread_notifications_text(["Sắp hết giờ", "Đã quá giờ", "Nhắc việc"], VI)
        == "Bạn có 3 thông báo mới: Sắp hết giờ và Đã quá giờ, và còn 1 thông báo khác."
    )
    assert (
        unread_notifications_text(["Time is almost up"], EN)
        == "You have 1 new notification: Time is almost up."
    )
    assert unread_notifications_text([], EN) == "You have no new notifications."


def test_pending_proposal() -> None:
    starts = [at(14, days=1), at(9, days=1)]
    assert (
        pending_proposal_text(starts, True, None, NOW, VN, VI)
        == "Có lịch đề xuất 2 khung giờ đang chờ duyệt, bắt đầu lúc 9 giờ ngày mai. "
        "Bạn duyệt trên màn hình nhé."
    )
    assert (
        pending_proposal_text(starts[:1], False, 45, NOW, VN, EN)
        == "A proposed schedule with 1 time slot is waiting, starting at 2 pm "
        "tomorrow. It is still about 45 minutes short of meeting every deadline. "
        "You can approve it on the screen."
    )
    assert (
        pending_proposal_text([], False, None, NOW, VN, VI)
        == "Có lịch đề xuất đang chờ duyệt. Lịch này chưa kịp mọi hạn chót. "
        "Bạn duyệt trên màn hình nhé."
    )
    assert (
        pending_proposal_text(None, True, None, NOW, VN, VI)
        == "Bạn không có lịch đề xuất nào đang chờ duyệt."
    )


def test_upcoming_milestones() -> None:
    items = [
        DueMilestone("Thiết kế", at(0, days=-2)),
        DueMilestone("Bàn giao", at(0, days=1)),
        DueMilestone("Sửa lỗi", at(0, days=6)),
    ]
    assert (
        upcoming_milestones_text(items, None, NOW, VN, VI)
        == "Có 3 milestone chưa xong: Thiết kế (đã quá hạn) và Bàn giao (hạn ngày mai), "
        "và còn 1 milestone khác."
    )
    assert (
        upcoming_milestones_text(items[1:2], "App spa", NOW, VN, EN)
        == "Project App spa has 1 milestone to finish: Bàn giao (due tomorrow)."
    )
    assert (
        upcoming_milestones_text([], None, NOW, VN, VI)
        == "Bạn không có milestone nào sắp tới hạn."
    )
    assert (
        upcoming_milestones_text([], "App spa", NOW, VN, VI)
        == "Dự án App spa không có milestone nào sắp tới hạn."
    )


def every_reply(lang: Language) -> list[str]:
    many = [TimedLine(LONG, at(9, days=d)) for d in range(10)]
    return [
        today_briefing_text(12, [LONG] * 5, 9, lang),
        today_briefing_text(0, [], 0, lang),
        at_risk_text(many, NOW, VN, lang),
        at_risk_text([], NOW, VN, lang),
        *(schedule_text(p, many, VN, lang) for p in SpokenPeriod),
        schedule_text(SpokenPeriod.TODAY, [], VN, lang),
        running_timer_text(LONG, 600, True, lang),
        running_timer_text(None, 0, False, lang),
        estimation_habits_text(
            ["Bạn thường cần thêm khoảng 30% thời gian cho Writing."] * 3, lang
        ),
        productive_hours_text([(9, 11), (20, 22), (14, 15)], lang),
        productive_hours_text([], lang),
        similar_task_text(LONG, 12, 600, 2.34, lang),
        similar_task_text(LONG, 0, None, None, lang),
        project_progress_text(LONG, 3, 8, 37.5, at(0, days=20), NOW, VN, lang),
        project_progress_text(LONG, 0, 0, 0, None, NOW, VN, lang),
        list_projects_text([LONG] * 12, lang),
        list_projects_text([], lang),
        ai_quota_text(300, lang),
        ai_quota_text(None, lang),
        unsupported_text(lang),
        *(smalltalk_text(k, lang) for k in SmalltalkKind),
        clarify_text("dự án", "project", [LONG] * 5, lang),
        not_found_text("việc", "task", LONG, lang),
        not_found_text("việc", "task", None, lang),
        source_error_text(lang),
        not_enough_data_text(lang),
        pending_confirmations_text([(LONG, 600)] * 9, lang),
        pending_confirmations_text([], lang),
        unread_notifications_text([LONG] * 20, lang),
        unread_notifications_text([], lang),
        pending_proposal_text(
            [at(9, days=d) for d in range(9)], False, 600, NOW, VN, lang
        ),
        pending_proposal_text([], False, None, NOW, VN, lang),
        pending_proposal_text(None, True, None, NOW, VN, lang),
        upcoming_milestones_text(
            [DueMilestone(LONG, at(0, days=d)) for d in range(-3, 9)],
            LONG,
            NOW,
            VN,
            lang,
        ),
        upcoming_milestones_text([], None, NOW, VN, lang),
        upcoming_milestones_text([], LONG, NOW, VN, lang),
    ]


# T3
@pytest.mark.parametrize("lang", [VI, EN])
def test_every_reply_is_short_and_has_no_internal_name(lang: Language) -> None:
    replies = every_reply(lang)

    assert len(replies) == 42
    for text in replies:
        assert 0 < word_count(text) <= MAX_WORDS, text
        assert not has_internal_name(text), text
        assert "(s)" not in text, text
        for word in (
            "k_bias",
            "t_est",
            "score",
            "today_briefing",
            "schedule_for",
            "T0",
        ):
            assert word not in text, text


def test_suggestions() -> None:
    assert 2 <= len(suggestions(VI)) <= 3
    assert suggestions(EN)[0] == "What should I do today?"


def test_template_guards() -> None:
    with pytest.raises(InternalNameError):
        _say("Your K_bias is {k}.", k=1.3)
    with pytest.raises(InternalNameError):
        _say("Tool: schedule_for")
    with pytest.raises(ValueError):
        _say("{text}", text="từ " * 61)
