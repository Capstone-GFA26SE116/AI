import pytest

from metanoia_ai.domain.briefing import Reason, ReasonCode
from metanoia_ai.domain.briefing_text import (
    InternalNameError,
    checked,
    duration,
    has_internal_name,
    productive_hint_text,
    reason_text,
    similar_ratio_text,
    similar_skipped_text,
)
from metanoia_ai.domain.models import Language

VI, EN = Language.VI, Language.EN


# --- #9 rounding ---


@pytest.mark.parametrize(
    "minutes, vi, en",
    [
        (1, "khoảng 5 phút", "about 5 minutes"),
        (43, "khoảng 45 phút", "about 45 minutes"),
        (47, "khoảng 45 phút", "about 45 minutes"),
        (48, "khoảng 50 phút", "about 50 minutes"),
        (58, "khoảng 1 giờ", "about 1 hour"),  # 58 → 60 minutes → hours
        (60, "khoảng 1 giờ", "about 1 hour"),
        (75, "khoảng 1,5 giờ", "about 1.5 hours"),
        (100, "khoảng 1,5 giờ", "about 1.5 hours"),
        (105, "khoảng 2 giờ", "about 2 hours"),
        (180, "khoảng 3 giờ", "about 3 hours"),
        (610, "khoảng 10 giờ", "about 10 hours"),
    ],
)
def test_duration_is_rounded(minutes, vi, en):
    assert duration(minutes, VI) == vi
    assert duration(minutes, EN) == en


# --- each reason, as in plan §2 #6 ---


@pytest.mark.parametrize(
    "reason, vi, en",
    [
        (
            Reason(ReasonCode.OVERDUE, days=1),
            "Đã quá hạn 1 ngày.",
            "Overdue by 1 day.",
        ),
        (
            Reason(ReasonCode.OVERDUE, days=3),
            "Đã quá hạn 3 ngày.",
            "Overdue by 3 days.",
        ),
        (
            Reason(ReasonCode.OVERDUE, days=0),
            "Đã quá hạn từ hôm nay.",
            "Overdue since earlier today.",
        ),
        (
            Reason(ReasonCode.AT_RISK, needed_minutes=180, available_minutes=120),
            "Còn cần khoảng 3 giờ nhưng chỉ còn khoảng 2 giờ rảnh trước hạn.",
            "Needs about 3 hours but only about 2 hours free before the deadline.",
        ),
        (
            Reason(ReasonCode.AT_RISK, needed_minutes=90, available_minutes=0),
            "Còn cần khoảng 1,5 giờ nhưng không còn giờ rảnh nào trước hạn.",
            "Needs about 1.5 hours but no free time is left before the deadline.",
        ),
        (
            Reason(ReasonCode.IN_PROGRESS, needed_minutes=45),
            "Đang làm dở, còn khoảng 45 phút.",
            "In progress, about 45 minutes left.",
        ),
        (Reason(ReasonCode.DEADLINE_SOON, days=0), "Hạn hôm nay.", "Due today."),
        (Reason(ReasonCode.DEADLINE_SOON, days=1), "Hạn ngày mai.", "Due tomorrow."),
        (
            Reason(ReasonCode.DEADLINE_SOON, days=2),
            "Hạn trong 2 ngày.",
            "Due in 2 days.",
        ),
        (
            Reason(ReasonCode.BIAS, extra_percent=40, domain_name="Coding"),
            "Bạn thường cần thêm 40% thời gian cho Coding.",
            "You usually need 40% more time for Coding.",
        ),
    ],
)
def test_reason_sentences(reason, vi, en):
    assert reason_text(reason, VI) == vi
    assert reason_text(reason, EN) == en


def test_similar_ratio_sentence_uses_one_decimal():
    assert similar_ratio_text(1.4, VI) == (
        "Các việc tương tự trước đây thường mất gấp 1,4 lần dự kiến."
    )
    assert similar_ratio_text(1.45, EN) == (
        "Similar tasks in the past usually took 1.5 times longer than planned."
    )
    assert "gấp 2 lần" in similar_ratio_text(2.0, VI)


def test_similar_skipped_sentence():
    assert similar_skipped_text(VI) == "Trước đây bạn từng bỏ dở việc tương tự."
    assert similar_skipped_text(EN) == "You have dropped a similar task before."


def test_productive_hint_sentence():
    ranges = [(14, 16), (20, 21)]
    assert productive_hint_text(ranges, VI) == (
        "Khung giờ bạn làm tốt còn lại hôm nay: 14h–16h, 20h–21h."
    )
    assert productive_hint_text(ranges, EN) == (
        "Your best working hours left today: 14:00–16:00, 20:00–21:00."
    )


# --- T8: no internal names in any sentence, both languages ---


def all_sentences(lang):
    reasons = (
        [Reason(ReasonCode.OVERDUE, days=d) for d in (0, 1, 5)]
        + [
            Reason(ReasonCode.AT_RISK, needed_minutes=n, available_minutes=a)
            for n, a in ((30, 0), (200, 45), (900, 600))
        ]
        + [Reason(ReasonCode.IN_PROGRESS, needed_minutes=n) for n in (10, 45, 300)]
        + [Reason(ReasonCode.DEADLINE_SOON, days=d) for d in (0, 1, 2, 14)]
        + [
            Reason(ReasonCode.BIAS, extra_percent=p, domain_name="Writing")
            for p in (10, 40, 200)
        ]
    )
    texts = [reason_text(r, lang) for r in reasons]
    texts += [similar_ratio_text(1.3, lang), similar_skipped_text(lang)]
    texts.append(productive_hint_text([(9, 11)], lang))
    return texts


@pytest.mark.parametrize("lang", [VI, EN])
def test_t8_no_sentence_shows_an_internal_name(lang):
    for text in all_sentences(lang):
        assert not has_internal_name(text), text
        assert text.endswith(".")


@pytest.mark.parametrize(
    "text", ["K_bias is 1.3", "your T_est", "score 0.8", "K=1.4", "needed_minutes"]
)
def test_internal_names_are_caught(text):
    assert has_internal_name(text)
    with pytest.raises(InternalNameError):
        checked(text)


def test_plain_text_passes_the_check():
    assert checked("Hạn hôm nay.") == "Hạn hôm nay."
