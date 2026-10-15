import pytest

from metanoia_ai.domain.task_breakdown import (
    BadDraftError,
    Draft,
    DraftLimits,
    ReplyKind,
    Subtask,
    SubtaskInput,
    check_draft,
    check_reply,
)

AI = DraftLimits(min_subtasks=2, max_subtasks=10, min_minutes=5, max_minutes=120)
MANUAL = DraftLimits(min_subtasks=1, max_subtasks=10, min_minutes=1, max_minutes=480)


def items(*minutes, titles=None) -> list[SubtaskInput]:
    titles = titles or [f"Bước {n}" for n in range(1, len(minutes) + 1)]
    return [SubtaskInput(t, m, "vì sao") for t, m in zip(titles, minutes, strict=True)]


# --- T1: a valid AI draft ---


def test_t1_valid_draft_gets_order_index():
    draft = check_draft(
        items(
            60,
            90,
            5,
            120,
            titles=["Dựng khung trang", "Viết API", "Sửa CSS", "Kiểm thử"],
        ),
        AI,
    )
    assert draft == Draft(
        (
            Subtask("Dựng khung trang", 60, 1, "vì sao"),
            Subtask("Viết API", 90, 2, "vì sao"),
            Subtask("Sửa CSS", 5, 3, "vì sao"),
            Subtask("Kiểm thử", 120, 4, "vì sao"),
        )
    )
    assert draft.total_minutes == 275


def test_titles_and_reasons_are_trimmed():
    draft = check_draft(
        [SubtaskInput("  Viết   API ", 30, "  một  dòng "), SubtaskInput("B", 30)], AI
    )
    assert draft.subtasks[0].title == "Viết API"
    assert draft.subtasks[0].reason == "một dòng"
    assert draft.subtasks[1].reason is None


# --- T2: AI drafts that break a rule ---


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(items(60), id="1-subtask"),
        pytest.param(items(*[10] * 11), id="11-subtasks"),
        pytest.param(items(60, 60, titles=["A", "   "]), id="empty-title"),
        pytest.param(items(60, 60, titles=["A", "x" * 201]), id="title-201"),
        pytest.param(
            items(60, 60, titles=["Viết API", " viết   api "]), id="same-title"
        ),
        pytest.param(items(60, 4), id="4-minutes"),
        pytest.param(items(60, 121), id="121-minutes"),
        pytest.param(items(60, 30.0), id="float-minutes"),
        pytest.param(items(60, "30"), id="text-minutes"),
        pytest.param(items(60, True), id="bool-minutes"),
        pytest.param(items(60, None), id="no-minutes"),
        pytest.param([SubtaskInput(None, 60), SubtaskInput("B", 60)], id="no-title"),
    ],
)
def test_t2_bad_ai_drafts_are_rejected(bad):
    with pytest.raises(BadDraftError):
        check_draft(bad, AI)


def test_title_of_200_chars_is_fine():
    assert check_draft(items(60, 60, titles=["A", "x" * 200]), AI)


def test_error_message_has_no_user_text():
    with pytest.raises(BadDraftError) as error:
        check_draft(items(60, 60, titles=["Gọi chị Lan", "gọi chị lan"]), AI)
    assert "Lan" not in str(error.value)


# --- T3: kind and subtasks must agree ---


def test_t3_question_with_subtasks_is_rejected():
    with pytest.raises(BadDraftError):
        check_reply(ReplyKind.QUESTION, items(60, 60), AI)


def test_t3_draft_without_subtasks_is_rejected():
    with pytest.raises(BadDraftError):
        check_reply(ReplyKind.DRAFT, [], AI)


def test_question_without_subtasks_is_fine():
    assert check_reply(ReplyKind.QUESTION, [], AI) is None


def test_draft_reply_is_checked():
    draft = check_reply(ReplyKind.DRAFT, items(60, 60), AI)
    assert draft is not None and len(draft.subtasks) == 2
    with pytest.raises(BadDraftError):
        check_reply(ReplyKind.DRAFT, items(60, 200), AI)


# --- T4: manual edits at Apply (#19) ---


@pytest.mark.parametrize(
    ("minutes", "ok"),
    [(1, True), (480, True), (0, False), (481, False)],
)
def test_t4_manual_minutes(minutes, ok):
    edited = items(minutes, 30)
    if ok:
        assert check_draft(edited, MANUAL).subtasks[0].estimated_minutes == minutes
    else:
        with pytest.raises(BadDraftError):
            check_draft(edited, MANUAL)


def test_t4_one_manual_subtask_is_fine():
    assert len(check_draft(items(30), MANUAL).subtasks) == 1


def test_t4_manual_still_needs_unique_titles():
    with pytest.raises(BadDraftError):
        check_draft(items(30, 30, titles=["A", "a"]), MANUAL)
