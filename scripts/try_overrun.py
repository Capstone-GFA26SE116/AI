"""FR-16b step 6 (T9): POST /schedule/overrun with the real Supabase (no LLM, no Backend).

Usage (from the repo root; no need to start the API):
    uv run python scripts/try_overrun.py

1 test user (works 08:00–18:00 every day, 15' break) with one day of slots tomorrow:
  09:00–10:00 "Viết blog" runs over (a timer ran on it)
  10:15–11:15 "Thiết kế"   movable
  12:00–13:00 "Họp khách"  Fixed task
  13:30–14:30 "Báo cáo"    movable
  14:45–15:45 "Sửa lỗi"    a timer was started on it → never moved
  16:00–17:00 "Gửi khách"  movable, deadline 17:00
"Now" is set to 10:05 tomorrow and the user asks for 60 more minutes. The endpoint runs
in-process with the real SQL repository. Checks the proposal (AC1–AC4), that nothing
was written (AC5) and the time (AC8), then always deletes exactly the rows it made.
Never prints DATABASE_URL, the JWT secret or keys.
"""

import asyncio
import statistics
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy import text

from metanoia_ai.api.deps import get_plan_overrun
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.plan_overrun import OverrunSettings, PlanOverrun
from metanoia_ai.config import get_settings
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory
from metanoia_ai.infrastructure.db.overrun_repository import SqlOverrunRepository

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAIN_WRITING = "494703e0-10de-4457-b245-7c24f088deeb"
TASK_NAMES = ["blog", "design", "meeting", "report", "fix", "send"]
SLOT_NAMES = ["s_blog", "s_design", "s_report", "s_fix", "s_send"]
IDS = {n: uuid4() for n in ["user", "project", *TASK_NAMES, *SLOT_NAMES]}
TASKS = [str(IDS[n]) for n in TASK_NAMES]
CALLS = 20
MAX_P95_MS = 1000

CLEANUP = [
    ("FocusSession", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("ScheduleSlot", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("Task", '"Id" = ANY(CAST(:tasks AS uuid[]))'),
    ("Project", '"Id" = :project'),
    ("User", '"Id" = :user'),
]
CLEANUP_PARAMS = {"user": IDS["user"], "project": IDS["project"], "tasks": TASKS}
# What the user's rows look like, to show nothing changed (AC5).
SNAPSHOT = text("""
    SELECT s."Id", s."StartTime", s."EndTime", s."Status"
    FROM "ScheduleSlot" s WHERE s."TaskId" = ANY(CAST(:tasks AS uuid[]))
    ORDER BY s."Id"
""")


def tomorrow(hour: int, minute: int = 0) -> datetime:
    day = datetime.now(ZONE).date() + timedelta(days=1)
    return datetime.combine(day, clock_time(hour, minute), tzinfo=ZONE)


NOW = tomorrow(10, 5)


async def setup(conn) -> None:
    await conn.execute(
        text("""INSERT INTO "User" ("Id", "RoleId", "Email", "FullName",
            "GoogleSubjectId", "TimeZone", "WorkDayStart", "WorkDayEnd",
            "WorksOnWeekend", "DefaultBufferMinutes")
        VALUES (:user, :role, :email, 'FR16b Try User', :google,
            'Asia/Ho_Chi_Minh', '08:00', '18:00', true, 15)"""),
        {
            "user": IDS["user"],
            "role": ROLE_FREELANCER,
            "email": f"fr16b-try-{RUN}@example.invalid",
            "google": f"fr16b-try-{RUN}",
        },
    )
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "Name")
        VALUES (:project, :user, 'FR16b Dự án thử')"""),
        {"project": IDS["project"], "user": IDS["user"]},
    )
    flexible = text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title",
            "TaskType", "Status", "Deadline", "EstimatedMinutes")
        VALUES (:id, :project, :domain, :title, 'Flexible', 'Scheduled', :deadline, 60)""")
    later = tomorrow(23) + timedelta(days=3)
    await conn.execute(
        flexible,
        [
            {
                "id": IDS[n],
                "project": IDS["project"],
                "domain": DOMAIN_WRITING,
                "title": f"FR16b {title}",
                "deadline": deadline,
            }
            for n, title, deadline in [
                ("blog", "Viết blog", later),
                ("design", "Thiết kế", later),
                ("report", "Báo cáo", later),
                ("fix", "Sửa lỗi", later),
                ("send", "Gửi khách", tomorrow(17)),
            ]
        ],
    )
    await conn.execute(
        text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "FixedStart", "FixedEnd")
        VALUES (:id, :project, :domain, 'FR16b Họp khách', 'Fixed', 'ToDo', :start, :end)"""),
        {
            "id": IDS["meeting"],
            "project": IDS["project"],
            "domain": DOMAIN_WRITING,
            "start": tomorrow(12),
            "end": tomorrow(13),
        },
    )
    await conn.execute(
        text("""INSERT INTO "ScheduleSlot" ("Id", "TaskId", "StartTime", "EndTime",
            "ProposedMinutes", "Status")
        VALUES (:id, :task, :start, :end, 60, :status)"""),
        [
            {
                "id": IDS[slot],
                "task": IDS[task],
                "start": tomorrow(*start),
                "end": tomorrow(*end),
                "status": status,
            }
            for slot, task, start, end, status in [
                ("s_blog", "blog", (9, 0), (10, 0), "Overrun"),
                ("s_design", "design", (10, 15), (11, 15), "Approved"),
                ("s_report", "report", (13, 30), (14, 30), "Approved"),
                ("s_fix", "fix", (14, 45), (15, 45), "Approved"),
                ("s_send", "send", (16, 0), (17, 0), "Approved"),
            ]
        ],
    )
    await conn.execute(
        text("""INSERT INTO "FocusSession" ("TaskId", "ScheduleSlotId", "StartedAt",
            "EndedAt", "StopReason", "CaptureMethod", "IsConfirmed")
        VALUES (:task, :slot, :start, :end, :reason, 'Timer', true)"""),
        [
            {
                "task": IDS["blog"],
                "slot": IDS["s_blog"],
                "start": tomorrow(9),
                "end": None,  # still running
                "reason": None,
            },
            {
                "task": IDS["fix"],
                "slot": IDS["s_fix"],
                "start": tomorrow(8),
                "end": tomorrow(8, 20),
                "reason": "Interrupted",
            },  # started early on purpose → this slot stays
        ],
    )


async def cleanup(engine) -> int:
    async with engine.begin() as conn:
        for table, where in CLEANUP:
            await conn.execute(
                text(f'DELETE FROM "{table}" WHERE {where}'), CLEANUP_PARAMS
            )
    left = 0
    async with engine.connect() as conn:
        for table, where in CLEANUP:
            count = text(f'SELECT count(*) FROM "{table}" WHERE {where}')
            left += (await conn.execute(count, CLEANUP_PARAMS)).scalar_one()
    return left


async def snapshot(engine) -> list[tuple]:
    async with engine.connect() as conn:
        return [tuple(r) for r in await conn.execute(SNAPSHOT, CLEANUP_PARAMS)]


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


def hm(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(ZONE).strftime("%d/%m %H:%M")


def check(answer: dict) -> list[str]:
    """What is wrong with the proposal (empty list: all right)."""
    problems = []
    moves = {
        m["slot_id"]: (hm(m["new_start"]), hm(m["new_end"])) for m in answer["moves"]
    }
    day = tomorrow(0).strftime("%d/%m")
    expected = {
        str(IDS["s_design"]): (f"{day} 13:00", f"{day} 14:00"),  # 11:20–12:00 too short
        str(IDS["s_report"]): (f"{day} 15:45", f"{day} 16:45"),  # after "Sửa lỗi"
    }
    if answer["kind"] != "replan":
        problems.append(f"kind {answer['kind']} (mong đợi replan)")
    if hm(answer["extended"]["end"]) != f"{day} 11:05":
        problems.append(
            f"giờ kết thúc mới {hm(answer['extended']['end'])} (mong đợi 11:05)"
        )
    if moves != expected:
        problems.append(f"moves {moves} (mong đợi {expected})")
    not_placed = [n["slot_id"] for n in answer["not_placed"]]
    if not_placed != [str(IDS["s_send"])]:
        problems.append(f"not_placed {not_placed} (mong đợi Gửi khách)")
    return problems


async def main() -> None:
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)
    settings = get_settings()

    async def plan_overrun_at_fixed_now():
        # The real SQL repository; only "now" is fixed so the test day is predictable.
        async with factory() as session:
            yield PlanOverrun(
                SqlOverrunRepository(session),
                OverrunSettings(
                    settings.schedule_round_minutes, settings.schedule_window_days
                ),
                now=lambda: NOW,
            )

    app.dependency_overrides[get_plan_overrun] = plan_overrun_at_fixed_now
    ok = False
    try:
        async with engine.begin() as conn:
            await setup(conn)
        print(
            f"Đã tạo user test fr16b-try-{RUN}@example.invalid (ngày thử: {NOW:%d/%m})"
        )
        before = await snapshot(engine)
        transport = httpx.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {make_token()}"}
        body = {"slot_id": str(IDS["s_blog"]), "extra_minutes": 60, "language": "vi"}
        timings, answer = [], None
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fr16b", headers=headers, timeout=30
        ) as client:
            for _ in range(CALLS):
                started = time.perf_counter()
                response = await client.post("/schedule/overrun", json=body)
                timings.append((time.perf_counter() - started) * 1000)
                response.raise_for_status()
                answer = response.json()
            fixed = await client.post(
                "/schedule/overrun",
                json={
                    **body,
                    "slot_id": str(IDS["s_fix"]),
                },  # not running over: Approved
            )
        print(f"\n{answer['kind']} · đến {hm(answer['extended']['end'])}")
        for m in answer["moves"]:
            print(
                f"  dời {m['task_title']}: {hm(m['old_start'])} → {hm(m['new_start'])}–{hm(m['new_end'])[-5:]}"
            )
        for n in answer["not_placed"]:
            print(f"  không kịp: {n['task_title']}")
        print(f'  "{answer["summary"]}"')
        problems = check(answer)
        unchanged = await snapshot(engine) == before
        p95 = statistics.quantiles(timings, n=20)[-1]
        print(
            f"\nSlot khác (Approved, chưa lố) cũng được đề xuất: HTTP {fixed.status_code}"
        )
        print(f"DB không đổi sau {CALLS + 1} lần gọi: {unchanged}")
        print(
            f"Thời gian (in-process, máy này → Supabase): trung vị {statistics.median(timings):.0f} ms · p95 {p95:.0f} ms"
        )
        for p in problems:
            print(f"SAI: {p}")
        ok = not problems and unchanged and p95 < MAX_P95_MS
    finally:
        app.dependency_overrides.clear()
        left = await cleanup(engine)
        print(f"Đã xoá dữ liệu test, còn lại: {left} dòng")
        await engine.dispose()
    print("ĐẠT" if ok and left == 0 else "CHƯA ĐẠT")


if __name__ == "__main__":
    asyncio.run(main())
