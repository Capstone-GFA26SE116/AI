"""FR-22 step 10 (T11): 20 spoken questions with the real Gemini, Supabase and Backend.

Usage (from the repo root; no need to start the API):
    uv run python scripts/try_assistant.py

1 test user ("Tiêu chuẩn" plan, 300 AI calls) with 3 projects (two "Landing page …"
to test "clarify"), open tasks, a meeting this afternoon and this evening, an approved
slot tomorrow, a running timer, a learned K for Writing, onboarding "morning + evening"
and 2 finished "viết bài blog" tasks with memories (FR-09).
Asks the 20 questions of prompts/assistant_router_samples.json through
POST /assistant/turn in-process, prints each reply, checks the picked tool
(≥ 18/20, AC1 / AC2), the AI call count (AC4) and the time (AC8), then always
deletes exactly the rows it made. Never prints DATABASE_URL, the JWT secret or keys.
"""

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy import text

from metanoia_ai.api.main import app
from metanoia_ai.config import get_settings
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory
from metanoia_ai.prompts import load_samples

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAIN_WRITING = "494703e0-10de-4457-b245-7c24f088deeb"
DOMAIN_CODING = "a7b1eb7a-e137-4a50-851b-199273b98c6f"
PLAN = "Tiêu chuẩn"
NAMES = ["user", "subscription", "landing_a", "landing_b", "spa"]
NAMES += ["blog", "report", "meeting", "dinner", "old1", "old2"]
IDS = {name: uuid4() for name in NAMES}
PROJECTS = [str(IDS[n]) for n in ("landing_a", "landing_b", "spa")]
TASKS = [str(IDS[n]) for n in ("blog", "report", "meeting", "dinner", "old1", "old2")]
MIN_CORRECT = 18
PERIOD_STARTS = {
    "morning": ("Sáng nay", "This morning"),
    "afternoon": ("Chiều nay", "This afternoon"),
    "evening": ("Tối nay", "This evening"),
    "today": ("Hôm nay", "Today"),
    "tomorrow": ("Ngày mai", "Tomorrow"),
}
MAX_P95_MS = 5000

CLEANUP = [
    ("AIUsageLog", '"UserSubscriptionId" = :subscription'),
    ("TaskExecutionMemory", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("ScheduleSlot", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("FocusSession", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("Task", '"Id" = ANY(CAST(:tasks AS uuid[]))'),
    ("Project", '"Id" = ANY(CAST(:projects AS uuid[]))'),
    ("UserDomainProfile", '"UserId" = :user'),
    ("UserCircadianProfile", '"UserId" = :user'),
    ("UserSubscription", '"Id" = :subscription'),
    ("User", '"Id" = :user'),
]
CLEANUP_PARAMS = {
    "subscription": IDS["subscription"],
    "user": IDS["user"],
    "projects": PROJECTS,
    "tasks": TASKS,
}


def local(days: int, hour: int, minute: int = 0) -> datetime:
    day = datetime.now(ZONE).date() + timedelta(days=days)
    return datetime.combine(day, clock_time(hour, minute), tzinfo=ZONE)


async def setup(conn) -> None:
    user = IDS["user"]
    await conn.execute(
        text("""INSERT INTO "User" ("Id", "RoleId", "Email", "FullName",
            "GoogleSubjectId", "TimeZone", "WorkDayStart", "WorkDayEnd",
            "WorksOnWeekend", "ProductiveBlocks")
        VALUES (:user, :role, :email, 'FR22 Try User', :google,
            'Asia/Ho_Chi_Minh', '06:00', '23:00', true, ARRAY['morning', 'evening'])"""),
        {
            "user": user,
            "role": ROLE_FREELANCER,
            "email": f"fr22-try-{RUN}@example.invalid",
            "google": f"fr22-try-{RUN}",
        },
    )
    await conn.execute(
        text("""INSERT INTO "UserSubscription" ("Id", "UserId", "PlanId", "StartAt", "Status")
        SELECT :id, :user, "Id", now() - interval '1 day', 'Active'
        FROM "SubscriptionPlan" WHERE "Name" = :plan"""),
        {"id": IDS["subscription"], "user": user, "plan": PLAN},
    )
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "Name")
        VALUES (:landing_a, :user, 'FR22 Landing page Client A'),
               (:landing_b, :user, 'FR22 Landing page Client B'),
               (:spa, :user, 'FR22 App đặt lịch spa')"""),
        {**IDS, "user": user},
    )
    flexible = text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title",
            "TaskType", "Status", "Deadline", "EstimatedMinutes", "CompletedAt")
        VALUES (:id, :project, :domain, :title, 'Flexible', :status, :deadline,
            :minutes, :completed)""")
    await conn.execute(
        flexible,
        [
            # Open: the running timer is on this one; a slot tomorrow morning.
            {
                "id": IDS["blog"],
                "project": IDS["spa"],
                "domain": DOMAIN_WRITING,
                "title": "FR22 Viết bài blog tháng 11",
                "status": "InProgress",
                "deadline": local(3, 17),
                "minutes": 120,
                "completed": None,
            },
            # 10 hours of work due in 4 hours: at risk.
            {
                "id": IDS["report"],
                "project": IDS["landing_a"],
                "domain": DOMAIN_CODING,
                "title": "FR22 Báo cáo tiến độ",
                "status": "ToDo",
                "deadline": datetime.now(ZONE) + timedelta(hours=4),
                "minutes": 600,
                "completed": None,
            },
            {
                "id": IDS["old1"],
                "project": IDS["spa"],
                "domain": DOMAIN_WRITING,
                "title": "FR22 Viết bài blog tháng 10",
                "status": "Done",
                "deadline": local(-20, 17),
                "minutes": 60,
                "completed": local(-20, 16),
            },
            {
                "id": IDS["old2"],
                "project": IDS["spa"],
                "domain": DOMAIN_WRITING,
                "title": "FR22 Viết bài blog tháng 9",
                "status": "Done",
                "deadline": local(-50, 17),
                "minutes": 60,
                "completed": local(-50, 16),
            },
        ],
    )
    await conn.execute(
        text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "FixedStart", "FixedEnd")
        VALUES (:meeting, :project, :domain, 'FR22 Họp khách', 'Fixed', 'ToDo',
                :m_start, :m_end),
               (:dinner, :project, :domain, 'FR22 Ăn tối với đối tác', 'Fixed', 'ToDo',
                :d_start, :d_end)"""),
        {
            "meeting": IDS["meeting"],
            "dinner": IDS["dinner"],
            "project": IDS["landing_a"],
            "domain": DOMAIN_CODING,
            "m_start": local(0, 15),
            "m_end": local(0, 16),
            "d_start": local(0, 19),
            "d_end": local(0, 20, 30),
        },
    )
    await conn.execute(
        text("""INSERT INTO "ScheduleSlot" ("TaskId", "StartTime", "EndTime",
            "ProposedMinutes", "Status")
        VALUES (:task, :start, :end, 120, 'Approved')"""),
        {"task": IDS["blog"], "start": local(1, 9), "end": local(1, 11)},
    )
    # Running timer (EndedAt NULL) started 40 minutes ago.
    await conn.execute(
        text("""INSERT INTO "FocusSession" ("TaskId", "StartedAt", "CaptureMethod",
            "IsConfirmed") VALUES (:task, :start, 'Timer', true)"""),
        {"task": IDS["blog"], "start": datetime.now(UTC) - timedelta(minutes=40)},
    )
    await conn.execute(
        text("""INSERT INTO "UserDomainProfile" ("UserId", "DomainId", "KBias",
            "SampleCount") VALUES (:user, :domain, 1.350, 12)"""),
        {"user": user, "domain": DOMAIN_WRITING},
    )
    await conn.execute(
        text("""INSERT INTO "UserCircadianProfile" ("UserId", "HourOfDay", "Score",
            "PriorScore", "Source")
        SELECT :user, h, s, s, 'Onboarding'
        FROM generate_series(0, 23) AS h,
             LATERAL (SELECT CASE WHEN h BETWEEN 8 AND 10 OR h BETWEEN 19 AND 21
                                  THEN 0.800 ELSE 0.300 END AS s) AS x"""),
        {"user": user},
    )
    # Memories wait for a vector (Pending): the full-text branch of FR-09 finds them.
    await conn.execute(
        text("""INSERT INTO "TaskExecutionMemory" ("TaskId", "Content", "Outcome",
            "EstimatedMinutes", "ActualMinutes", "EmbeddingStatus")
        VALUES (:task, :content, 'Completed', 60, :actual, 'Pending')"""),
        [
            {"task": IDS["old1"], "content": "Viết bài blog tháng 10", "actual": 90},
            {"task": IDS["old2"], "content": "Viết bài blog tháng 9", "actual": 80},
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


def is_right(sample: dict, body: dict) -> bool:
    """The picked tool (and period) is what the sample expects."""
    if sample["tool"] == "unsupported":
        return body["kind"] == "unsupported"
    if sample["tool"] == "smalltalk":
        return body["kind"] == "smalltalk"
    if body["tool"] != sample["tool"]:
        return False
    # The reply starts with the spoken period ("Chiều nay …"), the API has no period field.
    return "period" not in sample or body["reply"].startswith(
        PERIOD_STARTS[sample["period"]]
    )


async def usage(engine) -> tuple[int, list[int], int]:
    """AIUsageLog rows, their LLM latencies (ms) and AiCallsUsed of the test plan."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""SELECT "LatencyMs" FROM "AIUsageLog"
                WHERE "UserSubscriptionId" = :subscription AND "Feature" = 'Chat'"""),
                CLEANUP_PARAMS,
            )
        ).all()
        used = (
            await conn.execute(
                text(
                    'SELECT "AiCallsUsed" FROM "UserSubscription" WHERE "Id" = :subscription'
                ),
                CLEANUP_PARAMS,
            )
        ).scalar_one()
    return len(rows), [r[0] for r in rows], used


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[-1]


async def main() -> None:
    engine = create_engine(get_settings())
    app.state.session_factory = create_session_factory(engine)
    samples = json.loads(load_samples("assistant_router"))
    ok = False
    try:
        async with engine.begin() as conn:
            await setup(conn)
        print(f"Đã tạo user test fr22-try-{RUN}@example.invalid")
        transport = httpx.ASGITransport(app=app)  # no lifespan → no startup scan
        headers = {"Authorization": f"Bearer {make_token()}"}
        right, timings, statuses = 0, [], []
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fr22", headers=headers, timeout=60
        ) as client:
            for i, sample in enumerate(samples, start=1):
                body = {
                    "text": sample["text"],
                    "language": sample["language"],
                    "history": sample.get("history", []),
                }
                started = time.perf_counter()
                response = await client.post("/assistant/turn", json=body)
                ms = (time.perf_counter() - started) * 1000
                timings.append(ms)
                statuses.append(response.status_code)
                if response.status_code != 200:
                    print(f"{i:2}. [FAIL HTTP {response.status_code}] {sample['text']}")
                    continue
                answer = response.json()
                good = is_right(sample, answer)
                right += good
                print(
                    f"{i:2}. [{'OK' if good else 'SAI'}] {sample['text']}\n"
                    f"     → {answer['kind']}/{answer['tool']} ({answer['emotion']}, {ms:.0f} ms)"
                    f' mong đợi {sample["tool"]}\n     "{answer["reply"]}"'
                    + (
                        f"\n     lựa chọn: {[o['title'] for o in answer['options']]}"
                        if answer["options"]
                        else ""
                    )
                )

        rows, llm_ms, used = await usage(engine)
        print(f"\nChọn đúng công cụ: {right}/{len(samples)} (cần ≥ {MIN_CORRECT})")
        print(
            f"AIUsageLog (Chat): {rows} dòng · AiCallsUsed: {used} · HTTP 200: {statuses.count(200)}"
        )
        print(
            f"Thời gian trọn vẹn từ máy này: trung vị {statistics.median(timings):.0f} ms · "
            f"p95 {p95(timings):.0f} ms · max {max(timings):.0f} ms"
        )
        if llm_ms:
            print(
                f"Riêng Gemini (LatencyMs): trung vị {statistics.median(llm_ms):.0f} ms · "
                f"p95 {p95(llm_ms) if len(llm_ms) > 1 else llm_ms[0]:.0f} ms"
            )
        ok = right >= MIN_CORRECT
        print(f"  [{'OK' if ok else 'FAIL'}] AC1/AC2: ≥ {MIN_CORRECT}/20 đúng công cụ")
        charge_ok = used == statuses.count(200) and rows >= used
        print(f"  [{'OK' if charge_ok else 'FAIL'}] AC4: mỗi lượt 200 trừ đúng 1 lượt")
        speed_ok = p95(timings) < MAX_P95_MS
        print(
            f"  [{'OK' if speed_ok else 'FAIL'}] AC8: p95 < 5 s (đo từ máy dev, có mạng)"
        )
        ok = ok and charge_ok
    finally:
        left = await cleanup(engine)
        print(f"\nĐã xoá dữ liệu thử. Còn lại: {left} dòng.")
        await engine.dispose()
    print("\nKẾT QUẢ:", "OK" if ok else "CÓ MỤC FAIL (xem ở trên)")


asyncio.run(main())
