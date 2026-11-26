"""FR-22d spoken summaries (vi / en). T8."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.assistant_text import (
    MAX_WORDS,
    ConflictLine,
    MovedLine,
    catch_up_text,
    insert_text,
    postpone_text,
    word_count,
)
from metanoia_ai.domain.models import Language
from metanoia_ai.domain.reshuffle import Choice, Why

VN = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=VN)
VI, EN = Language.VI, Language.EN
LONG = "Viết báo cáo tổng kết dự án landing page cho khách hàng lớn"


def at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=VN) + timedelta(days=days)


def insert(
    lang=VI,
    moved=(),
    not_placed=0,
    conflicts=(),
    rec=None,
    alt=None,
    past=False,
    title="Sửa lỗi gấp",
):
    return insert_text(
        title,
        at(14),
        at(16),
        list(moved),
        not_placed,
        list(conflicts),
        rec,
        alt,
        past,
        NOW,
        VN,
        lang,
    )


def test_insert_without_conflict() -> None:
    assert (
        insert()
        == "Thêm Sửa lỗi gấp từ 14 giờ đến 16 giờ, không cần dời việc nào. Đồng ý không?"
    )
    moved = [
        MovedLine("Viết blog", at(16, 15)),
        MovedLine("Thiết kế", at(9, days=1)),
        MovedLine("Gửi", at(10, days=1)),
    ]
    assert insert(moved=moved, not_placed=1) == (
        "Thêm Sửa lỗi gấp từ 14 giờ đến 16 giờ và dời 3 việc: Viết blog sang 16 giờ 15 và "
        "Thiết kế sang 9 giờ ngày mai, và còn 1 việc khác. 1 việc sẽ không kịp hạn chót. Đồng ý không?"
    )
    assert insert(EN, past=True) == (
        "Add Sửa lỗi gấp from 2 pm to 4 pm; nothing needs to move. "
        "This goes past your working hours. OK?"
    )


def test_insert_with_conflict_suggests_other_time() -> None:
    text = insert(
        conflicts=[
            ConflictLine("Họp khách Lam Sơn", Why.CLIENT),
            ConflictLine("Gym", Why.PERSONAL),
        ],
        rec=Choice.OTHER_TIME,
        alt=(at(9), at(11)),
        moved=[MovedLine("Viết blog", at(16, 15))],
    )
    assert text == (
        "Sửa lỗi gấp từ 14 giờ đến 16 giờ trùng với Họp khách Lam Sơn (việc với khách), "
        "và còn 1 việc khác. Mình gợi ý làm từ 9 giờ đến 11 giờ, hoặc bạn ghi đè hay hủy. "
        "Sẽ cần dời 1 việc."
    )


@pytest.mark.parametrize(
    ("why", "tail"),
    [
        (Why.CLIENT, ", nhớ báo khách"),
        (Why.PERSONAL, ""),
        (Why.OTHER_PEOPLE, ", rồi tự hủy lịch đó trên Google"),
        (Why.GROUP, ", nhớ báo nhóm"),
    ],
)
def test_insert_with_conflict_suggests_overwrite(why: Why, tail: str) -> None:
    text = insert(conflicts=[ConflictLine("Họp", why)], rec=Choice.OVERWRITE)
    assert text.endswith(f"mình gợi ý ghi đè{tail}, hoặc bạn hủy.")


def test_insert_with_a_running_slot() -> None:
    text = insert(EN, conflicts=[ConflictLine("Code API", Why.RUNNING)])
    assert text.endswith("You are in the middle of it, so you can only cancel or wait.")


def test_postpone() -> None:
    assert (
        postpone_text("Viết blog", at(9, days=1), NOW, VN, VI)
        == "Dời Viết blog sang 9 giờ ngày mai nhé?"
    )
    assert (
        postpone_text("Viết blog", at(8, days=6), NOW, VN, EN)
        == "Move Viết blog to 8 am on Nov 16?"
    )
    assert postpone_text("Viết blog", None, NOW, VN, VI).startswith(
        "Không còn chỗ trống"
    )


def test_catch_up() -> None:
    assert (
        catch_up_text([], 0, NOW, VN, VI)
        == "Không có việc nào bị lỡ, lịch của bạn vẫn ổn."
    )
    assert catch_up_text([MovedLine("Viết blog", at(13, 5))], 1, NOW, VN, VI) == (
        "Có 2 việc bị lỡ. Mình xếp lại: Viết blog sang 13 giờ 5. "
        "1 việc không còn kịp hạn chót. Đồng ý không?"
    )
    assert (
        catch_up_text([], 2, NOW, VN, EN)
        == "2 tasks missed. 2 tasks can no longer meet the deadline."
    )


@pytest.mark.parametrize("lang", [VI, EN])
def test_longest_replies_stay_under_the_word_limit(lang: Language) -> None:
    moved = [
        MovedLine(LONG, at(16)),
        MovedLine(LONG, at(9, days=1)),
        MovedLine(LONG, at(10, days=2)),
    ]
    conflicts = [ConflictLine(LONG, Why.OTHER_PEOPLE), ConflictLine(LONG, Why.GROUP)]
    texts = [
        insert(lang, moved, 2, (), None, None, True, LONG),
        insert(
            lang,
            moved,
            0,
            conflicts,
            Choice.OTHER_TIME,
            (at(9, days=1), at(11, days=1)),
            False,
            LONG,
        ),
        insert(lang, moved, 0, conflicts, Choice.OVERWRITE, None, False, LONG),
        postpone_text(LONG, None, NOW, VN, lang),
        catch_up_text(moved, 3, NOW, VN, lang),
    ]
    assert all(word_count(t) <= MAX_WORDS for t in texts)
