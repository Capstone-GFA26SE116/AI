"""FR-08b-1: show a project chat and Apply its newest draft (fake DB, fake LLM)."""

from dataclasses import replace
from datetime import date, datetime
from uuid import uuid4

import pytest

from metanoia_ai.application.ports.chat_repository import ChatRole
from metanoia_ai.application.ports.project_chat_repository import ConversationStatus
from metanoia_ai.application.use_cases.apply_project_breakdown import (
    ApplyProjectBreakdown,
    DomainInactiveError,
)
from metanoia_ai.application.use_cases.apply_task_breakdown import (
    MessageNotFoundError,
    NotLatestDraftError,
)
from metanoia_ai.application.use_cases.get_project_chat import GetProjectChat
from metanoia_ai.application.use_cases.start_project_chat import (
    ConversationAppliedError,
    ConversationNotFoundError,
    ProjectClosedError,
    StartProjectChat,
)
from metanoia_ai.domain.models import EstimateSource
from metanoia_ai.domain.project_breakdown import (
    DroppedTask,
    DuplicateOf,
    MilestoneInput,
    ProjectLimits,
    TaskInput,
)
from metanoia_ai.domain.task_breakdown import BadDraftError
from tests.unit.application.fakes import FakeLLMClient
from tests.unit.application.test_project_chat import (
    CODING,
    DESIGN,
    NOW,
    QUESTION,
    SAIGON,
    USER,
    World,
    draft_answer,
    run,
)

MANUAL = ProjectLimits(8, 15, 40, 5, 960, 150)
AI, USER_SOURCE = EstimateSource.AI, EstimateSource.USER


def drafted(world: World | None = None):
    """A world whose chat has one AI draft (2 milestones: Wireframe 240', Dựng trang 300')."""
    world = world or World()
    turn = world.send(FakeLLMClient(draft_answer()))
    return world, turn.ai_message.message_id


def apply(world: World, message_id, edited=None, chat_id=None):
    use_case = ApplyProjectBreakdown(world.repo, MANUAL, now=lambda: NOW)
    return run(use_case.execute(USER, chat_id or world.chat_id, message_id, edited))


# --- T19: Apply the draft as is ---


def test_t19_apply_creates_milestones_and_tasks_from_the_draft():
    world, message_id = drafted()

    result = apply(world, message_id)

    design, coding = result.milestones
    assert (design.title, design.deadline) == ("Thiết kế", date(2026, 10, 22))
    assert (coding.title, coding.deadline) == ("Lập trình", date(2026, 10, 30))
    assert [
        (t.title, t.estimated_minutes, t.estimate_source) for t in design.tasks
    ] == [("Wireframe", 240, AI)]
    assert result.skipped == []

    rows = world.repo.new_tasks
    assert [r.task.ai_estimated_minutes for r in rows] == [240, 300]
    assert [r.task.estimated_minutes for r in rows] == [240, 300]
    assert rows[0].task.deadline == datetime(2026, 10, 22, 23, 59, tzinfo=SAIGON)
    assert rows[1].task.domain_id == CODING
    assert [m.milestone.order_index for m in world.repo.new_milestones] == [1, 2]
    assert world.repo.new_tasks[0].task.milestone_id == design.milestone_id

    chat = run(world.repo.get_conversation(USER, world.chat_id))
    assert chat.status is ConversationStatus.APPLIED
    assert world.project.project_id in world.repo.locked


def test_apply_uses_no_quota():
    world, message_id = drafted()
    used = world.calls_used
    apply(world, message_id)
    assert world.calls_used == used


# --- T20: Apply with hand edits ---


def test_t20_edits_set_the_estimate_source_per_task():
    world, message_id = drafted()
    edited = [
        MilestoneInput(
            "Thiết kế (sửa)",
            date(2026, 10, 24),
            [
                TaskInput(
                    "Wireframe", str(DESIGN), 300, key="m1-t1"
                ),  # minutes changed
                TaskInput("Logo", str(DESIGN), 5, key=None),  # added by the user
            ],
            key="m1",
        )
        # milestone m2 removed
    ]

    result = apply(world, message_id, edited)

    (milestone,) = result.milestones
    assert (milestone.title, milestone.deadline) == (
        "Thiết kế (sửa)",
        date(2026, 10, 24),
    )
    assert [
        (t.title, t.estimated_minutes, t.estimate_source) for t in milestone.tasks
    ] == [
        ("Wireframe", 300, USER_SOURCE),
        ("Logo", 5, USER_SOURCE),
    ]
    # The AI's number is kept for the edited task, empty for the new one (#11).
    assert [r.task.ai_estimated_minutes for r in world.repo.new_tasks] == [240, None]


def test_edit_keeping_the_ai_number_stays_ai():
    world, message_id = drafted()
    edited = [
        MilestoneInput("M", None, [TaskInput("Wireframe", DESIGN, 240, key="m1-t1")])
    ]
    result = apply(world, message_id, edited)
    assert result.milestones[0].tasks[0].estimate_source is AI
    assert result.milestones[0].deadline == date(2026, 10, 30)  # project deadline


def test_renamed_task_with_the_ai_minutes_stays_ai():
    world, message_id = drafted()
    # m2-t1 is "Dựng trang" 300' in the draft; the user only renames it.
    edited = [
        MilestoneInput("M", None, [TaskInput("Việc khác", CODING, 300, key="m2-t1")])
    ]
    result = apply(world, message_id, edited)
    # The minutes are still the AI's number, so K does not learn from it (FR-17b).
    assert result.milestones[0].tasks[0].estimate_source is AI
    assert world.repo.new_tasks[0].task.ai_estimated_minutes == 300


@pytest.mark.parametrize(
    "edited",
    [
        pytest.param([], id="no-milestones"),
        pytest.param(
            [MilestoneInput("M", None, [TaskInput("A", DESIGN, 4)])], id="4-minutes"
        ),
        pytest.param(
            [MilestoneInput("M", None, [TaskInput("A", DESIGN, 961)])], id="961-minutes"
        ),
        pytest.param(
            [MilestoneInput("M", date(2026, 10, 1), [TaskInput("A", DESIGN, 60)])],
            id="past-deadline",
        ),
        pytest.param(
            [MilestoneInput("M", None, [TaskInput("A", uuid4(), 60)])], id="bad-domain"
        ),
    ],
)
def test_bad_edits_are_rejected_and_nothing_is_written(edited):
    world, message_id = drafted()
    with pytest.raises(BadDraftError):
        apply(world, message_id, edited)
    assert world.repo.new_milestones == [] and world.repo.new_tasks == []
    chat = run(world.repo.get_conversation(USER, world.chat_id))
    assert chat.status is ConversationStatus.OPEN


# --- T21: refused ---


def test_t21_apply_twice():
    world, message_id = drafted()
    apply(world, message_id)
    with pytest.raises(ConversationAppliedError):
        apply(world, message_id)
    assert len(world.repo.new_milestones) == 2  # nothing more


def test_t21_older_draft():
    world, first = drafted()
    world.send(FakeLLMClient(draft_answer()), message="Sửa lại")
    with pytest.raises(NotLatestDraftError):
        apply(world, first)


def test_question_message_is_not_a_draft():
    world = World()
    turn = world.send(FakeLLMClient(QUESTION))
    with pytest.raises(NotLatestDraftError):
        apply(world, turn.ai_message.message_id)


def test_t21_message_of_another_chat():
    world, message_id = drafted()
    other = run(
        StartProjectChat(world.repo, lambda: NOW).execute(
            USER, world.project.project_id
        )
    )
    with pytest.raises(MessageNotFoundError):
        apply(world, message_id, chat_id=other.conversation_id)


def test_t21_domain_turned_off_since_the_draft():
    world, message_id = drafted()
    world.repo.add_domain(CODING, "Coding", is_active=False)
    with pytest.raises(DomainInactiveError):
        apply(world, message_id)
    assert world.repo.new_tasks == []


def test_closed_project_cannot_apply():
    world, message_id = drafted()
    owner, project = world.repo.projects[world.project.project_id]
    world.repo.projects[project.project_id] = (
        owner,
        replace(project, status="Archived"),
    )
    with pytest.raises(ProjectClosedError):
        apply(world, message_id)


def test_task_chat_or_other_user_is_not_found():
    world, message_id = drafted()
    world.repo.task_chat_ids.add(world.chat_id)
    with pytest.raises(ConversationNotFoundError):
        apply(world, message_id)


# --- T22: a same-title task was made after the draft ---


def test_t22_task_made_meanwhile_is_skipped():
    world, message_id = drafted()
    world.repo.add_old_task(world.project.project_id, "  wireframe ")

    result = apply(world, message_id)

    assert result.skipped == [DroppedTask("Wireframe", DuplicateOf.EXISTING_TASK)]
    assert [m.title for m in result.milestones] == [
        "Lập trình"
    ]  # empty milestone left out
    assert [r.task.title for r in world.repo.new_tasks] == ["Dựng trang"]


# --- T23: existing work is never touched ---


def test_t23_existing_milestones_and_tasks_stay_and_order_continues():
    world = World()
    world.repo.add_old_milestone(world.project.project_id, 1, "Giai đoạn cũ 1")
    world.repo.add_old_milestone(world.project.project_id, 2, "Giai đoạn cũ 2")
    world.repo.add_old_task(world.project.project_id, "Việc cũ")
    world, message_id = drafted(world)

    apply(world, message_id)

    assert world.repo.old_milestones[world.project.project_id] == [
        (1, "Giai đoạn cũ 1"),
        (2, "Giai đoạn cũ 2"),
    ]
    assert world.repo.old_tasks[world.project.project_id] == ["Việc cũ"]
    assert [m.milestone.order_index for m in world.repo.new_milestones] == [3, 4]


# --- GetProjectChat ---


def test_get_chat_shows_messages_latest_draft_and_applied():
    world, message_id = drafted()
    view = run(GetProjectChat(world.repo).execute(USER, world.chat_id))
    assert [m.role for m in view.messages] == [ChatRole.USER, ChatRole.ASSISTANT]
    assert view.latest_draft.message_id == message_id
    assert view.applied is False
    assert view.time_zone == SAIGON

    apply(world, message_id)
    assert run(GetProjectChat(world.repo).execute(USER, world.chat_id)).applied is True


def test_get_chat_of_a_task_chat_is_not_found():
    world = World()
    world.repo.task_chat_ids.add(world.chat_id)
    with pytest.raises(ConversationNotFoundError):
        run(GetProjectChat(world.repo).execute(USER, world.chat_id))


def test_get_new_chat_has_no_draft():
    world = World()
    view = run(GetProjectChat(world.repo).execute(USER, world.chat_id))
    assert (view.messages, view.latest_draft) == ([], None)


# --- #32, #33 ---


def test_client_name_is_restored_before_saving_and_dedup():
    world = World()
    milestones = [
        {
            "title": "Bàn giao cho [client]",
            "deadline": None,
            "tasks": [
                {
                    "title": "Họp [client]",
                    "domain_id": str(DESIGN),
                    "estimated_minutes": 60,
                    "reason": "r",
                },
                {
                    "title": "Gửi file cho [client]",
                    "domain_id": str(DESIGN),
                    "estimated_minutes": 30,
                    "reason": "r",
                },
            ],
        }
    ]
    turn = world.send(FakeLLMClient(draft_answer(milestones)))
    # Made by hand after the draft, with the real name: matched once restored.
    world.repo.add_old_task(world.project.project_id, "Họp ACME")

    result = apply(world, turn.ai_message.message_id)

    assert result.milestones[0].title == "Bàn giao cho ACME"
    assert [r.task.title for r in world.repo.new_tasks] == ["Gửi file cho ACME"]
    assert result.skipped == [DroppedTask("Họp ACME", DuplicateOf.EXISTING_TASK)]


def test_nothing_left_after_dedup_still_applies():
    world, message_id = drafted()
    world.repo.add_old_task(world.project.project_id, "Wireframe")
    world.repo.add_old_task(world.project.project_id, "Dựng trang")

    result = apply(world, message_id)

    assert result.milestones == []
    assert len(result.skipped) == 2
    assert world.repo.new_milestones == []
    chat = run(world.repo.get_conversation(USER, world.chat_id))
    assert chat.status is ConversationStatus.APPLIED
