from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from metanoia_ai.domain.models import EstimateSource, Language, estimate_source
from metanoia_ai.domain.project_breakdown import (
    DomainNumbers,
    DroppedTask,
    DuplicateOf,
    MilestoneInput,
    ProjectDraft,
    ProjectLimits,
    TaskInput,
    ai_minutes_of,
    check_project_draft,
    check_project_reply,
    drop_duplicates,
    project_numbers,
    resolve_deadlines,
    split_duplicates,
    task_deadline,
)
from metanoia_ai.domain.task_breakdown import BadDraftError, ReplyKind

DESIGN = UUID(int=1)
CODING = UUID(int=2)
DOMAINS = {DESIGN: "Design", CODING: "Coding"}
AI_LIMITS = ProjectLimits(8, 15, 40, 15, 480, 150)
MANUAL_LIMITS = ProjectLimits(8, 15, 40, 5, 960, 150)
TODAY = date(2026, 10, 16)
SAIGON = ZoneInfo("Asia/Saigon")


def task(title="Vẽ wireframe", domain=DESIGN, minutes=120, key=None) -> TaskInput:
    return TaskInput(title, str(domain), minutes, "1 dòng lý do", key)


def milestone(title="Thiết kế", tasks=None, deadline=None, key=None) -> MilestoneInput:
    return MilestoneInput(
        title, deadline, tasks if tasks is not None else [task()], key
    )


def check(milestones, limits=AI_LIMITS, assign_keys=True):
    return check_project_draft(milestones, DOMAINS, limits, assign_keys=assign_keys)


# --- T1: a valid draft ---


def test_t1_valid_draft_gets_keys_in_order():
    milestones = [
        milestone("Thiết kế", [task("Wireframe"), task("Mockup", minutes=240)]),
        milestone(
            "  Làm   web ",
            [task("Header", CODING), task("Footer", CODING), task("Form", CODING)],
        ),
    ]

    checked = check(milestones)

    assert [m.key for m in checked] == ["m1", "m2"]
    assert [t.key for t in checked[1].tasks] == ["m2-t1", "m2-t2", "m2-t3"]
    assert checked[1].title == "Làm web"  # extra spaces squeezed
    first = checked[0].tasks[1]
    assert (first.title, first.domain_id, first.domain_name) == (
        "Mockup",
        DESIGN,
        "Design",
    )
    assert first.estimated_minutes == 240
    assert first.reason == "1 dòng lý do"


def test_manual_edit_keeps_the_keys_it_was_sent_with():
    milestones = [
        milestone(key="m2", tasks=[task(key="m2-t1"), task("Task mới", key=None)])
    ]
    checked = check(milestones, MANUAL_LIMITS, assign_keys=False)
    assert checked[0].key == "m2"
    assert [t.key for t in checked[0].tasks] == ["m2-t1", None]


def test_domain_id_may_be_a_uuid_object():
    checked = check([milestone(tasks=[TaskInput("A", CODING, 60)])])
    assert checked[0].tasks[0].domain_name == "Coding"


# --- T2: rules broken ---


def many_tasks(n: int) -> list[TaskInput]:
    return [task(f"Task {i}") for i in range(n)]


@pytest.mark.parametrize(
    "milestones",
    [
        pytest.param([], id="0-milestones"),
        pytest.param([milestone(f"M{i}") for i in range(9)], id="9-milestones"),
        pytest.param([milestone(tasks=many_tasks(16))], id="16-tasks-in-one"),
        pytest.param([milestone(tasks=[])], id="milestone-without-tasks"),
        pytest.param(
            [milestone(f"M{i}", many_tasks(14)) for i in range(3)], id="41-tasks"
        ),
        pytest.param([milestone(tasks=[task(minutes=14)])], id="14-minutes"),
        pytest.param([milestone(tasks=[task(minutes=481)])], id="481-minutes"),
        pytest.param([milestone(tasks=[task(minutes=60.0)])], id="float-minutes"),
        pytest.param([milestone(tasks=[task(minutes=True)])], id="bool-minutes"),
        pytest.param([milestone(tasks=[task(title="   ")])], id="empty-task-title"),
        pytest.param([milestone(tasks=[task(title="x" * 201)])], id="201-char-task"),
        pytest.param([milestone(title="")], id="empty-milestone-title"),
        pytest.param([milestone(title="x" * 151)], id="151-char-milestone"),
        pytest.param(
            [milestone(tasks=[task(domain=UUID(int=9))])], id="unknown-domain"
        ),
        pytest.param(
            [milestone(tasks=[TaskInput("A", "not-a-uuid", 60)])], id="bad-domain-id"
        ),
        pytest.param([milestone(tasks=[TaskInput("A", None, 60)])], id="no-domain"),
    ],
)
def test_t2_bad_drafts_are_rejected(milestones):
    with pytest.raises(BadDraftError):
        check(milestones)


def test_t2_limits_themselves_pass():
    milestones = [milestone(f"M{i}", many_tasks(5)) for i in range(8)]  # 8 × 5 = 40
    milestones[0] = milestone("M0", [task(title="x" * 200, minutes=15)])
    milestones[1] = milestone("x" * 150, [task(minutes=480)])
    assert len(check(milestones)) == 8


def test_error_message_has_no_user_text():
    with pytest.raises(BadDraftError) as error:
        check([milestone("Dự án ACME bí mật", [task(minutes=1)])])
    assert "ACME" not in str(error.value)


# --- T3: question vs draft ---


def reply(kind, milestones, reminders=()):
    return check_project_reply(kind, milestones, reminders, DOMAINS, AI_LIMITS)


def test_t3_question_with_milestones_is_rejected():
    with pytest.raises(BadDraftError):
        reply(ReplyKind.QUESTION, [milestone()])


def test_t3_draft_without_milestones_is_rejected():
    with pytest.raises(BadDraftError):
        reply(ReplyKind.DRAFT, [])


def test_question_has_no_draft():
    assert reply(ReplyKind.QUESTION, [], ["Họp khách"]) is None


def test_draft_keeps_clean_reminders():
    draft = reply(ReplyKind.DRAFT, [milestone()], ["  Họp   chốt ", "", "   ", 3])
    assert draft.reminders == ("Họp chốt",)
    assert draft.milestones[0].key == "m1"


# --- T4: deadlines ---


def resolve(milestones, project_deadline, language=Language.VI) -> ProjectDraft:
    draft = ProjectDraft(check(milestones))
    return resolve_deadlines(draft, project_deadline, TODAY, language)


def test_t4_empty_milestone_deadline_takes_the_project_deadline():
    draft = resolve([milestone(deadline=None)], date(2026, 10, 25))
    assert draft.milestones[0].deadline == date(2026, 10, 25)
    assert draft.warnings == ()


def test_t4_own_deadline_is_kept():
    draft = resolve([milestone(deadline=date(2026, 10, 20))], date(2026, 10, 25))
    assert draft.milestones[0].deadline == date(2026, 10, 20)


def test_t4_no_deadline_anywhere_is_an_error_that_asks_the_user():
    with pytest.raises(BadDraftError, match="ask the user"):
        resolve([milestone(deadline=None)], None)


def test_t4_past_deadline_is_an_error():
    with pytest.raises(BadDraftError):
        resolve([milestone(deadline=TODAY - timedelta(days=1))], date(2026, 10, 25))


def test_t4_deadline_today_is_fine():
    draft = resolve([milestone(deadline=TODAY)], None)
    assert draft.milestones[0].deadline == TODAY


def test_t4_after_the_project_deadline_passes_with_a_warning():
    draft = resolve(
        [milestone("Bàn giao", deadline=date(2026, 10, 30))], date(2026, 10, 25)
    )
    assert draft.milestones[0].deadline == date(2026, 10, 30)
    assert draft.warnings == (
        'Hạn của "Bàn giao" (30/10/2026) sau hạn dự án (25/10/2026).',
    )


def test_late_warning_in_english():
    draft = resolve(
        [milestone("Handover", deadline=date(2026, 10, 30))],
        date(2026, 10, 25),
        Language.EN,
    )
    expected = (
        'The deadline of "Handover" (2026-10-30) is after the project deadline '
        "(2026-10-25)."
    )
    assert draft.warnings == (expected,)


# --- T5: day → 23:59 in the user's zone ---


def test_t5_task_deadline_is_end_of_day_in_the_user_zone():
    deadline = task_deadline(date(2026, 10, 25), SAIGON)
    assert deadline == datetime(2026, 10, 25, 23, 59, tzinfo=SAIGON)
    assert deadline.utcoffset() == timedelta(hours=7)
    assert deadline.isoformat() == "2026-10-25T23:59:00+07:00"


# --- T9: manual edits allow 5-960 minutes ---


@pytest.mark.parametrize(
    ("minutes", "ok"), [(5, True), (960, True), (4, False), (961, False)]
)
def test_t9_manual_minutes(minutes, ok):
    milestones = [milestone(tasks=[task(minutes=minutes)])]
    if ok:
        assert check(milestones, MANUAL_LIMITS, assign_keys=False)
    else:
        with pytest.raises(BadDraftError):
            check(milestones, MANUAL_LIMITS, assign_keys=False)


# --- T6: duplicates ---


def draft_of(*milestones: MilestoneInput) -> ProjectDraft:
    return ProjectDraft(check(list(milestones)))


def titles(draft: ProjectDraft) -> list[list[str]]:
    return [[t.title for t in m.tasks] for m in draft.milestones]


def test_t6_task_like_an_existing_one_is_dropped():
    draft = draft_of(milestone(tasks=[task(" viết   api "), task("Viết test")]))

    result = drop_duplicates(draft, ["Viết API"])

    assert titles(result) == [["Viết test"]]
    assert result.warnings == ('Đã bỏ "viết api": dự án đã có việc này.',)


def test_t6_second_same_title_in_the_draft_is_dropped():
    draft = draft_of(
        milestone("M1", [task("Header")]),
        milestone("M2", [task("HEADER"), task("Footer")]),
    )

    kept, dropped = split_duplicates(draft, [])

    assert titles(kept) == [["Header"], ["Footer"]]
    assert dropped == [DroppedTask("HEADER", DuplicateOf.IN_DRAFT)]


def test_t6_milestone_left_empty_is_dropped_and_keys_stay():
    draft = draft_of(milestone("M1", [task("Logo")]), milestone("M2", [task("Form")]))

    result = drop_duplicates(draft, ["logo"])

    assert [m.key for m in result.milestones] == ["m2"]
    assert result.milestones[0].tasks[0].key == "m2-t1"


def test_t6_nothing_left_is_an_error():
    draft = draft_of(milestone(tasks=[task("Logo")]))
    with pytest.raises(BadDraftError):
        drop_duplicates(draft, ["LOGO"])


def test_split_may_leave_nothing_without_error():
    # Used at Apply (#22): everything skipped is reported, not an error here.
    kept, dropped = split_duplicates(
        draft_of(milestone(tasks=[task("Logo")])), ["Logo"]
    )
    assert kept.milestones == ()
    assert dropped == [DroppedTask("Logo", DuplicateOf.EXISTING_TASK)]


def test_duplicate_warning_in_english():
    draft = draft_of(milestone(tasks=[task("Logo"), task("Form")]))
    result = drop_duplicates(draft, ["logo"], Language.EN)
    assert result.warnings == ('Removed "Logo": the project already has it.',)


def test_duplicate_reason_values_are_stable_api_strings():
    assert [r.value for r in DuplicateOf] == ["existing_task", "in_draft"]


# --- T7: numbers ---


def numbers_draft(deadline=None) -> ProjectDraft:
    return draft_of(
        milestone(
            "M1",
            [task("A", DESIGN, 240), task("B", CODING, 300), task("C", DESIGN, 360)],
            deadline=deadline,
        )
    )


def numbers(draft, project_deadline, k=None, free=False, language=Language.VI):
    return project_numbers(
        draft,
        k if k is not None else {CODING: Decimal("1.5")},
        Decimal("1.3"),
        project_deadline,
        TODAY,
        free,
        6,
        language,
    )


def test_t7_numbers_per_domain_with_initial_k_for_a_new_domain():
    result = numbers(numbers_draft(), date(2026, 10, 25))

    # Design has no K yet → 1.3: 600 → 780. Coding K 1.5: 300 → 450.
    assert result.by_domain == (
        DomainNumbers("Design", 600, 780),
        DomainNumbers("Coding", 300, 450),
    )
    assert (result.total_estimated_minutes, result.total_adjusted_minutes) == (
        900,
        1230,
    )
    assert result.days_left == 9
    assert result.adjusted_is_hint is False
    assert result.warning is None  # 1230' < 9 days × 6 h


def test_t7_overload_warning():
    result = numbers(numbers_draft(), date(2026, 10, 18))  # 2 days × 6 h = 720'
    assert result.days_left == 2
    assert result.warning == (
        "Khoảng 20.5 giờ làm việc, nhưng chỉ còn 2 ngày "
        "(~12 giờ nếu làm 6 giờ/ngày). Cân nhắc dời hạn hoặc bớt việc."
    )


def test_overload_warning_in_english():
    result = numbers(numbers_draft(), date(2026, 10, 18), language=Language.EN)
    assert result.warning.startswith("About 20.5 hours of work, but only 2 days left")


def test_t7_free_plan_numbers_are_a_hint():
    assert numbers(numbers_draft(), date(2026, 10, 25), free=True).adjusted_is_hint


def test_days_left_counts_to_the_latest_milestone_without_project_deadline():
    draft = draft_of(
        milestone("M1", [task("A")], deadline=date(2026, 10, 20)),
        milestone("M2", [task("B")], deadline=date(2026, 10, 30)),
    )
    assert numbers(draft, None).days_left == 14


def test_no_deadline_at_all_gives_no_days_and_no_warning():
    result = numbers(numbers_draft(), None)
    assert (result.days_left, result.warning) == (None, None)


# --- T8: the AI's number for each task ---


def test_t8_ai_minutes_and_estimate_source():
    saved = draft_of(milestone(tasks=[task("A", minutes=240), task("B", minutes=60)]))
    kept_task, changed_task = saved.tasks
    changed_task = replace(changed_task, estimated_minutes=90)

    cases = [
        (kept_task.key, 240, 240, EstimateSource.AI),  # AI number kept
        (changed_task.key, 90, 60, EstimateSource.USER),  # user changed it
        (None, 120, None, EstimateSource.USER),  # new task, no key
        ("m9-t9", 120, None, EstimateSource.USER),  # key not in the draft
    ]
    for key, minutes, ai_minutes, source in cases:
        assert ai_minutes_of(key, saved) == ai_minutes
        assert estimate_source(minutes, ai_minutes_of(key, saved)) is source
