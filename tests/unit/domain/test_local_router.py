"""FR-22b local router (no LLM). T1–T5."""

import json

import pytest

from metanoia_ai.domain.assistant_tools import (
    READ_TOOLS,
    SmalltalkKind,
    ToolChoice,
    ToolName,
)
from metanoia_ai.domain.local_router import (
    Example,
    LocalRouter,
    LocalSettings,
    is_follow_up,
    is_write_request,
    load_examples,
    local_choice,
    ngrams,
    spoken_minutes,
    spoken_period,
)
from metanoia_ai.domain.spoken_period import SpokenPeriod
from metanoia_ai.prompts import load_data

SETTINGS = LocalSettings(min_score=0.45, min_gap=0.05)
EXAMPLES = load_examples(json.loads(load_data("assistant_router_examples")))
CHECKS = json.loads(load_data("assistant_router_checks"))
ROUTER = LocalRouter(EXAMPLES)


def label_of(choice: ToolChoice) -> str:
    if choice.tool is ToolName.SMALLTALK:
        return f"smalltalk_{choice.smalltalk.value}"
    return choice.tool.value


def ask(text: str, history: bool = False) -> ToolChoice | None:
    return local_choice(ROUTER, text, history, SETTINGS)


# --- T1 ---


def test_ngrams_ignore_accents_case_and_the_name_slot() -> None:
    assert ngrams("Hôm  NAY") == ngrams("hom nay")
    assert ngrams("Dự án {x} tới đâu") == ngrams("du an toi dau")
    assert " ho" in ngrams("hôm")


def test_examples_file_is_checked() -> None:
    with pytest.raises(ValueError):
        load_examples({"no_such_tool": ["x"]})
    with pytest.raises(TypeError):
        load_examples(["x"])
    with pytest.raises(ValueError):
        LocalRouter([])


def test_other_writes_are_unsupported_locally() -> None:
    assert ask("Xóa milestone thiết kế").tool is ToolName.UNSUPPORTED


@pytest.mark.parametrize(
    ("text", "tool"),
    [
        ("Dừng lại", ToolName.STOP_TIMER),
        ("Tạm dừng", ToolName.STOP_TIMER),
        ("Xong rồi", ToolName.FINISH_TIMER),
        ("I'm done", ToolName.FINISH_TIMER),
        ("Duyệt lịch đề xuất", ToolName.APPROVE_PROPOSAL),
    ],
)
def test_commands(text: str, tool: ToolName) -> None:
    assert ask(text).tool is tool


def test_start_timer_keeps_the_task_name() -> None:
    choice = ask("Bắt đầu bấm giờ việc báo cáo")
    assert (choice.tool, choice.name) == (ToolName.START_TIMER, "bao cao")


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("Thêm 15 phút", 15),
        ("Cần thêm 30 phút", 30),
        ("Cho thêm nửa tiếng", 30),
        ("Thêm một tiếng nữa", 60),
        ("Extend by 2 hours", 120),
        ("Tôi cần thêm thời gian", 15),
        ("Thêm 900 phút", 480),
    ],
)
def test_spoken_minutes(text: str, minutes: int) -> None:
    assert spoken_minutes(text) == minutes


def test_extend_choice_carries_minutes() -> None:
    choice = ask("Cần thêm 30 phút")
    assert (choice.tool, choice.minutes) == (ToolName.EXTEND_TIMER, 30)


def test_a_question_about_the_timer_is_not_a_command() -> None:
    choice = ask("Tôi đang bấm giờ việc gì vậy?")
    assert choice is None or choice.tool is ToolName.RUNNING_TIMER


def test_every_label_has_examples() -> None:
    labels = {e.label for e in EXAMPLES}
    tools = {t.value for t in ToolName} - {"smalltalk"}
    assert labels == tools | {"smalltalk_greet", "smalltalk_thanks", "smalltalk_help"}


# --- T2: an example left out is never answered wrongly ---


def test_leave_one_out_is_never_sure_and_wrong() -> None:
    answered = wrong = 0
    for i, example in enumerate(EXAMPLES):
        router = LocalRouter(EXAMPLES[:i] + EXAMPLES[i + 1 :])
        text = example.text.replace("{x}", "landing page")
        choice = local_choice(router, text, False, SETTINGS)
        if choice is None:
            continue
        answered += 1
        wrong += label_of(choice) != example.label
    assert answered >= 0.3 * len(EXAMPLES)  # the rest goes to the LLM
    assert wrong == 0


# --- T3: AC1 on sentences that are not examples ---


def test_checks_answered_locally_and_right() -> None:
    texts = {e.text for e in EXAMPLES}
    answered = right = 0
    for check in CHECKS:
        assert check["text"] not in texts  # a check is never an example
        choice = ask(check["text"], check.get("history", False))
        if choice is None:
            continue
        answered += 1
        right += label_of(choice) == check["label"]
    assert answered >= 0.7 * len(CHECKS)
    assert right / answered >= 0.98


# --- T4: write requests never become a read tool (AC2) ---


@pytest.mark.parametrize(
    "text",
    [
        "Bắt đầu bấm giờ cho việc viết blog giúp tôi",
        "Dừng timer đi",
        "Xóa task họp khách giúp tôi",
        "Tạo task mới tên thiết kế logo",
        "Duyệt lịch đề xuất giúp tôi",
        "Đánh dấu task này đã xong nhé",
        "Bạn bắt đầu timer giúp tôi được không?",
        "Thêm milestone cho dự án spa",
        "Sửa deadline của báo cáo sang thứ sáu",
        "Hủy cuộc họp chiều nay",
        "Nâng cấp gói giúp tôi",
        "Thanh toán gói tiêu chuẩn",
        "Create a new task called design logo",
        "Start the timer for my report please",
        "Delete my meeting tomorrow",
        "Approve the proposed schedule",
        "Can you stop the timer?",
        "Mark this task as done",
    ],
)
def test_write_requests_are_never_a_read_tool(text: str) -> None:
    # FR-22c: one of the 5 commands (the app asks before running it), "unsupported",
    # or the LLM decides — never a read answer to a write request.
    choice = ask(text)
    assert choice is None or choice.tool not in READ_TOOLS


def test_questions_with_a_write_word_go_to_the_llm() -> None:
    assert not is_write_request("Tôi đã bắt đầu việc gì hôm nay?")
    assert ask("Sửa lỗi đăng nhập thường mất bao lâu?") is None  # a task name


# --- T5: part of the day, follow-ups, names ---


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("Chiều nay tôi có lịch gì?", SpokenPeriod.AFTERNOON),
        ("Sáng nay có họp không?", SpokenPeriod.MORNING),
        ("Tối nay tôi bận gì?", SpokenPeriod.EVENING),
        ("Mai tôi có lịch gì?", SpokenPeriod.TOMORROW),
        ("What's on my calendar tomorrow?", SpokenPeriod.TOMORROW),
        ("Lịch hôm nay có gì?", SpokenPeriod.TODAY),
    ],
)
def test_spoken_period(text: str, period: SpokenPeriod) -> None:
    assert spoken_period(text) is period


def test_schedule_choice_carries_the_period() -> None:
    choice = ask("Ngày mai tôi có lịch gì?")
    assert (choice.tool, choice.period) == (
        ToolName.SCHEDULE_FOR,
        SpokenPeriod.TOMORROW,
    )


def test_follow_ups_go_to_the_llm() -> None:
    assert is_follow_up("Còn ngày mai thì sao?", has_history=True)
    assert not is_follow_up("Còn ngày mai thì sao?", has_history=False)
    assert ask("Còn ngày mai thì sao?", history=True) is None


def test_named_tools_keep_what_is_left_as_the_name() -> None:
    progress = ask("Dự án app đặt lịch spa tới đâu rồi?")
    assert progress.tool is ToolName.PROJECT_PROGRESS
    assert progress.name == "app dat lich spa"

    similar = ask("Viết bài blog thường mất bao lâu?")
    assert similar.tool is ToolName.SIMILAR_TASK_TIME
    assert similar.name == "viet bai blog"


def test_milestones_without_a_name_mean_every_project() -> None:
    choice = ask("Milestone nào sắp đến hạn?")
    assert (choice.tool, choice.name) == (ToolName.UPCOMING_MILESTONES, None)


def test_smalltalk_kinds() -> None:
    assert ask("Cảm ơn bạn nhiều").smalltalk is SmalltalkKind.THANKS
    assert ask("Chào bạn nhé").smalltalk is SmalltalkKind.GREET


def test_off_topic_without_an_anchor_goes_to_the_llm() -> None:
    # Shares "hôm nay" with the briefing examples, but asks something else.
    assert ask("Hôm nay trời có mưa không?") is None


def test_a_tiny_router_works_on_its_own_examples() -> None:
    router = LocalRouter(
        [
            Example("ai_quota", "Tôi còn bao nhiêu lượt AI?"),
            Example("list_projects", "Tôi có dự án nào?"),
        ]
    )
    choice = local_choice(router, "Tôi còn bao nhiêu lượt AI?", False, SETTINGS)
    assert choice.tool is ToolName.AI_QUOTA
