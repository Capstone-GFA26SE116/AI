"""Try FR-10 and FR-17 by hand: create test data on Supabase, print a token, clean up on Enter.

Usage (from the repo root, with the API running in another terminal):
    uv run python scripts/try_schedule.py

Creates 1 test user with a plan, a project and 6 tasks (4 to schedule, 2 done with
focus sessions to learn from). Prints a JWT (valid 60 min) to paste into
http://127.0.0.1:8000/docs → Authorize. Press Enter (or Ctrl+C) to delete exactly
the rows it created. Never prints DATABASE_URL, the JWT secret or API key.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from uuid import uuid4
from zoneinfo import ZoneInfo

import jwt
from sqlalchemy import text

from metanoia_ai.config import get_settings
from metanoia_ai.infrastructure.db.engine import create_engine

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAIN_CODING = "a7b1eb7a-e137-4a50-851b-199273b98c6f"
DOMAIN_WRITING = "494703e0-10de-4457-b245-7c24f088deeb"
PLAN_STANDARD = "2c7e4b3f-1d5e-4a9b-8f32-6e8d9c0b1a02"  # "Tiêu chuẩn", 300 calls

IDS = {
    name: uuid4()
    for name in [
        "user",
        "subscription",
        "project",
        "report",
        "landing",
        "overdue",
        "meeting",
        "done_coding",
        "done_writing",
        "sub_1",
        "sub_2",
    ]
}
TASKS = {
    "report": "Viết báo cáo tuần cho khách (Flexible, 60', hạn sau 2 ngày)",
    "landing": "Làm landing page (Flexible, 2 subtask × 90', hạn sau 4 ngày)",
    "overdue": "Gửi báo giá (Flexible, 30', ĐÃ QUÁ HẠN)",
    "meeting": "Họp với khách (Fixed, ngày mai 10:00-11:00)",
    "done_coding": "Sửa lỗi đăng nhập (Coding, Done, dự kiến 60', làm thật 90' bằng Timer)",
    "done_writing": "Viết email chào hàng (Writing, Done, dự kiến 40', tự khai 50')",
}


def local(days: int, hour: int, minute: int = 0) -> datetime:
    day = datetime.now(ZONE).date() + timedelta(days=days)
    return datetime.combine(day, clock_time(hour, minute), tzinfo=ZONE)


SETUP = [
    (
        """INSERT INTO "User" ("Id", "RoleId", "Email", "FullName", "GoogleSubjectId",
            "TimeZone", "WorkDayStart", "WorkDayEnd", "WorksOnWeekend",
            "DefaultBufferMinutes")
        VALUES (:user, :role, :email, 'FR10 Try User', :google,
            'Asia/Ho_Chi_Minh', '09:00', '17:00', false, 10)""",
        {
            "user": IDS["user"],
            "role": ROLE_FREELANCER,
            "email": f"fr10-try-{RUN}@example.invalid",
            "google": f"fr10-try-{RUN}",
        },
    ),
    (
        """INSERT INTO "UserSubscription" ("Id", "UserId", "PlanId", "StartAt", "Status")
        VALUES (:id, :user, :plan, now() - interval '1 day', 'Active')""",
        {"id": IDS["subscription"], "user": IDS["user"], "plan": PLAN_STANDARD},
    ),
    # Coding has a profile (K 1.45, 3 samples); Writing has none → FR-17 creates it.
    (
        """INSERT INTO "UserDomainProfile" ("UserId", "DomainId", "KBias", "SampleCount")
        VALUES (:user, :domain, 1.450, 3)""",
        {"user": IDS["user"], "domain": DOMAIN_CODING},
    ),
    (
        """INSERT INTO "UserCircadianProfile" ("UserId", "HourOfDay", "Score")
        VALUES (:user, 14, 0.900), (:user, 15, 0.800), (:user, 16, 0.700)""",
        {"user": IDS["user"]},
    ),
    (
        """INSERT INTO "Project" ("Id", "UserId", "Name")
        VALUES (:id, :user, 'FR10 try project')""",
        {"id": IDS["project"], "user": IDS["user"]},
    ),
    (
        """INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "Deadline", "EstimatedMinutes")
        VALUES
        (:report, :project, :writing, 'Viết báo cáo tuần cho khách', 'Flexible', 'ToDo',
            :d_report, 60),
        (:landing, :project, :coding, 'Làm landing page', 'Flexible', 'ToDo',
            :d_landing, 180),
        (:overdue, :project, :writing, 'Gửi báo giá', 'Flexible', 'ToDo',
            :d_overdue, 30)""",
        {
            "report": IDS["report"],
            "landing": IDS["landing"],
            "overdue": IDS["overdue"],
            "project": IDS["project"],
            "writing": DOMAIN_WRITING,
            "coding": DOMAIN_CODING,
            "d_report": local(2, 17),
            "d_landing": local(4, 17),
            "d_overdue": local(-1, 17),
        },
    ),
    (
        """INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "FixedStart", "FixedEnd")
        VALUES (:id, :project, :writing, 'Họp với khách', 'Fixed', 'ToDo', :s, :e)""",
        {
            "id": IDS["meeting"],
            "project": IDS["project"],
            "writing": DOMAIN_WRITING,
            "s": local(1, 10),
            "e": local(1, 11),
        },
    ),
    (
        """INSERT INTO "SubTask" ("Id", "TaskId", "Title", "EstimatedMinutes",
            "OrderIndex", "Status")
        VALUES (:s1, :task, 'Dựng khung HTML', 90, 1, 'ToDo'),
               (:s2, :task, 'Làm responsive', 90, 2, 'ToDo')""",
        {"s1": IDS["sub_1"], "s2": IDS["sub_2"], "task": IDS["landing"]},
    ),
    # FR-17: two finished tasks, each with one confirmed focus session yesterday.
    (
        """INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "Deadline", "EstimatedMinutes", "CompletedAt")
        VALUES
        (:coding_task, :project, :coding, 'Sửa lỗi đăng nhập', 'Flexible', 'Done',
            :deadline, 60, :done_at),
        (:writing_task, :project, :writing, 'Viết email chào hàng', 'Flexible', 'Done',
            :deadline, 40, :done_at)""",
        {
            "coding_task": IDS["done_coding"],
            "writing_task": IDS["done_writing"],
            "project": IDS["project"],
            "coding": DOMAIN_CODING,
            "writing": DOMAIN_WRITING,
            "deadline": local(1, 17),
            "done_at": local(-1, 16),
        },
    ),
    (
        """INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt", "StopReason",
            "CaptureMethod", "IsConfirmed")
        VALUES
        (:coding_task, :c_start, :c_end, 'Completed', 'Timer', true),
        (:writing_task, :w_start, :w_end, 'Completed', 'SelfReported', true)""",
        {
            "coding_task": IDS["done_coding"],
            "writing_task": IDS["done_writing"],
            "c_start": local(-1, 9),
            "c_end": local(-1, 10, 30),
            "w_start": local(-1, 14),
            "w_end": local(-1, 14, 50),
        },
    ),
]

TASK_IDS = [str(IDS[name]) for name in TASKS]
CLEANUP = [
    ("AIUsageLog", '"UserSubscriptionId" = :subscription'),
    ("KBiasUpdateLog", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("FocusSession", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("ScheduleSlot", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("ScheduleProposal", '"UserId" = :user'),
    ("SubTask", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("Task", '"Id" = ANY(CAST(:tasks AS uuid[]))'),
    ("Project", '"Id" = :project'),
    ("UserCircadianProfile", '"UserId" = :user'),
    ("UserDomainProfile", '"UserId" = :user'),
    ("UserSubscription", '"Id" = :subscription'),
    ("User", '"Id" = :user'),
]
CLEANUP_PARAMS = {
    "subscription": IDS["subscription"],
    "user": IDS["user"],
    "project": IDS["project"],
    "tasks": TASK_IDS,
}


async def setup() -> None:
    engine = create_engine(get_settings())
    try:
        async with engine.begin() as conn:
            for sql, params in SETUP:
                await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


async def cleanup() -> None:
    engine = create_engine(get_settings())
    try:
        async with engine.begin() as conn:
            for table, where in CLEANUP:
                await conn.execute(
                    text(f'DELETE FROM "{table}" WHERE {where}'), CLEANUP_PARAMS
                )
        async with engine.connect() as conn:
            left = 0
            for table, where in CLEANUP:
                count = text(f'SELECT count(*) FROM "{table}" WHERE {where}')
                left += (await conn.execute(count, CLEANUP_PARAMS)).scalar_one()
        print(f"Đã xoá dữ liệu thử. Còn lại: {left} dòng.")
    finally:
        await engine.dispose()


def make_token() -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(IDS["user"]),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=60),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def print_guide() -> None:
    print("\nĐã tạo dữ liệu thử. Các task:")
    for name, label in TASKS.items():
        print(f"  {IDS[name]}  {label}")

    print("\n--- FR-10: POST /schedule/propose ---")
    print('  Body: {"language": "vi"}')
    print(
        "  Mong đợi: báo cáo + 2 subtask landing page được xếp, né cuộc họp ngày mai;"
    )
    print("  'Gửi báo giá' có cảnh báo overdue; 2 task Done không bị xếp.")

    print("\n--- FR-17: POST /learning/task-completed ---")
    print(f'  {{"task_id": "{IDS["done_coding"]}"}}')
    print("    → learned, K Coding 1.45 → 1.46, sample_index 4")
    print(f'  {{"task_id": "{IDS["done_writing"]}"}}')
    print("    → learned, tạo profile Writing: K 1.3 → 1.29, sample_index 1")
    print("  Gọi lại task trên → already_learned (không học 2 lần)")
    print(f'  {{"task_id": "{IDS["report"]}"}}  → task_not_done')
    print(f'  {{"task_id": "{IDS["meeting"]}"}}  → fixed_task')
    print(
        "  Sau khi học xong, gọi lại /schedule/propose: slot Coding dài hơn một chút (K mới)."
    )

    print("\nToken (dán vào nút Authorize trên /docs, hết hạn sau 60 phút):\n")
    print(make_token())
    print("\nThử xong thì quay lại đây và bấm Enter để xoá dữ liệu thử.")


def main() -> None:
    asyncio.run(setup())
    try:
        print_guide()
        input()
    except KeyboardInterrupt:
        print()
    finally:
        asyncio.run(cleanup())


main()
