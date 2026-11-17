"""Renzo Pomodoro loader (plans/K-renzo.md §1)."""

from datetime import date

from research.kbias_renzo.load import filter_rows


def row(tags="@bbc", done="1", day="2010-01-02", est="2", act="3"):
    return {"X.words": tags, "DONE": done, "date": day, "estimate": est, "actual": act}


def test_filters_follow_the_plan():
    rows = [
        row(),  # kept
        row(est="NA"),  # no estimate
        row(done="0"),  # not finished
        row(act="0"),  # not worked
        row(tags="@bbc,@meeting"),  # Fixed: meeting
        row(tags="@uswitch", day="2010-01-01", est="1", act="1"),  # kept, earlier
    ]
    tasks, funnel = filter_rows(rows)
    assert (
        funnel.rows,
        funnel.with_numbers,
        funnel.done,
        funnel.positive,
        funnel.not_meeting,
    ) == (
        6,
        5,
        4,
        3,
        2,
    )
    assert [t.sub_category for t in tasks] == ["@uswitch", "@bbc"]  # sorted by date
    first = tasks[1]
    assert (first.t_est, first.t_act, first.category, first.developer_id) == (
        50,
        75,
        "all",
        1,
    )
    assert first.estimate_on == date(2010, 1, 2)
