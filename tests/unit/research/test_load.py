from datetime import date
from pathlib import Path

import pytest

from research.kbias_sip.load import (
    Funnel,
    SipTask,
    check_funnel,
    count_pairs,
    domain_of,
    format_funnel,
    load_tasks,
)

TASK_HEADER = (
    "TaskNumber,Summary,Priority,RaisedByID,AssignedToID,AuthorisedByID,StatusCode,"
    "ProjectCode,ProjectBreakdownCode,Category,SubCategory,HoursEstimate,HoursActual,"
    "DeveloperID,DeveloperHoursActual,TaskPerformance,DeveloperPerformance\n"
)
DATES_HEADER = "TaskNumber,EstimateOn,StartedOn,CompletedOn\n"


def task_row(
    number: int,
    dev: int,
    status: str = "FINISHED",
    est: float = 2,
    act: float = 3,
    summary: str = "Fix it",
) -> str:
    return (
        f'{number},"{summary}",1,1,{dev},6,"{status}","PC2","PBC1","Development",'
        f'"Bug",{est},{act},{dev},{act},0,0\n'
    )


def write_dataset(folder: Path, rows: list[str], dates: list[str]) -> Path:
    (folder / "Sip-task-info.csv").write_bytes(
        (TASK_HEADER + "".join(rows)).encode("cp1252")
    )
    (folder / "est-act-dates.csv").write_bytes(
        (DATES_HEADER + "".join(dates)).encode("cp1252")
    )
    return folder


def make_task(number: int, dev: int = 1, category: str = "Development") -> SipTask:
    return SipTask(number, dev, category, "Bug", 1.0, 1.0, date(2005, 1, 1))


# T1
def test_reads_cp1252_bytes(tmp_path: Path) -> None:
    data = write_dataset(
        tmp_path,
        [task_row(1, 58, summary="Client’s report")],  # ’ is byte 0x92 in cp1252
        ["1,02-Nov-04,02-Nov-04,16-Nov-04\n"],
    )
    assert b"\x92" in (data / "Sip-task-info.csv").read_bytes()

    tasks, _ = load_tasks(data)

    assert tasks == [SipTask(1, 58, "Development", "Bug", 2.0, 3.0, date(2004, 11, 2))]


# T2
def test_drops_multi_dev_cancelled_and_chronicle_tasks(tmp_path: Path) -> None:
    data = write_dataset(
        tmp_path,
        [
            task_row(1, 58),  # kept
            task_row(2, 58, status="COMPLETED"),  # kept
            task_row(3, 58, status="RELEASED"),  # kept
            task_row(4, 58),  # two developers -> dropped
            task_row(4, 42),
            task_row(5, 58, status="CANCELLED"),
            task_row(6, 58, status="CHRONICLE"),
            task_row(7, 58, est=0),  # no positive estimate
        ],
        [f"{n},02-Nov-04,,\n" for n in (1, 2, 3, 4, 4, 5, 6, 7)],
    )

    tasks, funnel = load_tasks(data)

    assert [t.task_number for t in tasks] == [1, 2, 3]
    assert funnel == Funnel(
        rows=8,
        multi_dev_tasks=1,
        single_dev_tasks=6,
        done_tasks=4,
        positive_estimate_tasks=3,
    )


# T3
def test_sorts_by_estimate_date_then_task_number(tmp_path: Path) -> None:
    data = write_dataset(
        tmp_path,
        [task_row(30, 58), task_row(10, 58), task_row(20, 58)],
        ["30,05-Nov-04,,\n", "10,05-Nov-04,,\n", "20,01-Nov-04,,\n"],
    )

    tasks, _ = load_tasks(data)

    assert [t.task_number for t in tasks] == [20, 10, 30]


def test_count_pairs_by_category_and_sub_category() -> None:
    tasks = [make_task(n, dev=1) for n in range(10)] + [
        make_task(99, dev=2, category="Management")
    ]

    assert count_pairs(tasks, "category") == (2, 1, 10)
    assert count_pairs(tasks, "sub_category") == (2, 1, 10)


def test_domain_of_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        domain_of(make_task(1), "project")


def test_format_funnel_lists_every_step() -> None:
    funnel = Funnel(5, 1, 4, 3, 3)

    text = format_funnel(funnel, [make_task(1)])

    assert "All rows:" in text
    assert "Pairs (developer, sub_category)" in text


def test_check_funnel_stops_on_mismatch() -> None:
    with pytest.raises(SystemExit):
        check_funnel(Funnel(1, 0, 1, 1, 1), [make_task(1)])
