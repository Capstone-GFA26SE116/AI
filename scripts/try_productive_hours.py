"""FR-18 step 9 (T14): real run on Supabase, then delete exactly the rows it made.

Usage (from the repo root; no need to start the API):
    uv run python scripts/try_productive_hours.py

1 test user ("Cao cấp" plan, work day 06:00-23:00), 1 project, 1 Done task holding the
focus sessions and 1 ToDo task for FR-10. Calls the endpoints in-process (no startup
scan), checks the results, times recompute with 1,000 sessions (AC7), and always
cleans up. FR-10 calls Gemini once (1 AI call of the test user).
Never prints DATABASE_URL, the JWT secret or API key.
"""

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy import text

from metanoia_ai.api.deps import get_recompute_productive_hours
from metanoia_ai.api.main import app
from metanoia_ai.config import get_settings
from metanoia_ai.domain.productive_hours import is_learnable, learned_scores
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory
from metanoia_ai.infrastructure.db.productive_hours_repository import (
    GET_HOURS,
    GET_SESSIONS,
    GET_USER,
    UPSERT_HOURS,
    SqlProductiveHoursRepository,
    hours_params,
)

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAIN_CODING = "a7b1eb7a-e137-4a50-851b-199273b98c6f"
TOP_PLAN = "Cao cấp"
IDS = {name: uuid4() for name in ["user", "subscription", "project", "history", "todo"]}
TASKS = [str(IDS["history"]), str(IDS["todo"])]
PERF_SESSIONS = 1000
PERF_RUNS = 20

# T14: 8 good sessions in the evening, 4 interrupted in the morning (30' each).
SESSIONS = [(19, d, "Completed") for d in range(1, 5)]
SESSIONS += [(20, d, "Completed") for d in range(1, 5)]
SESSIONS += [(8, d, "Interrupted") for d in range(1, 3)]
SESSIONS += [(9, d, "Interrupted") for d in range(1, 3)]

CLEANUP = [
    ("AIUsageLog", '"UserSubscriptionId" = :subscription'),
    ("ScheduleSlot", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("ScheduleProposal", '"UserId" = :user'),
    ("FocusSession", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("Task", '"Id" = ANY(CAST(:tasks AS uuid[]))'),
    ("Project", '"Id" = :project'),
    ("UserCircadianProfile", '"UserId" = :user'),
    ("UserSubscription", '"Id" = :subscription'),
    ("User", '"Id" = :user'),
]
CLEANUP_PARAMS = {
    "subscription": IDS["subscription"],
    "user": IDS["user"],
    "project": IDS["project"],
    "tasks": TASKS,
}


def local(days: int, hour: int, minute: int = 0) -> datetime:
    day = datetime.now(ZONE).date() + timedelta(days=days)
    return datetime.combine(day, clock_time(hour, minute), tzinfo=ZONE)


async def setup(conn) -> None:
    await conn.execute(
        text("""INSERT INTO "User" ("Id", "RoleId", "Email", "FullName",
            "GoogleSubjectId", "TimeZone", "WorkDayStart", "WorkDayEnd",
            "WorksOnWeekend", "DefaultBufferMinutes")
        VALUES (:user, :role, :email, 'FR18 Try User', :google,
            'Asia/Ho_Chi_Minh', '06:00', '23:00', true, 10)"""),
        {
            "user": IDS["user"],
            "role": ROLE_FREELANCER,
            "email": f"fr18-try-{RUN}@example.invalid",
            "google": f"fr18-try-{RUN}",
        },
    )
    await conn.execute(
        text("""INSERT INTO "UserSubscription" ("Id", "UserId", "PlanId", "StartAt", "Status")
        SELECT :id, :user, "Id", now() - interval '1 day', 'Active'
        FROM "SubscriptionPlan" WHERE "Name" = :plan"""),
        {"id": IDS["subscription"], "user": IDS["user"], "plan": TOP_PLAN},
    )
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "Name")
        VALUES (:id, :user, 'FR18 try project')"""),
        {"id": IDS["project"], "user": IDS["user"]},
    )
    await conn.execute(
        text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "Deadline", "EstimatedMinutes", "CompletedAt")
        VALUES
        (:history, :project, :domain, 'FR18 lịch sử timer', 'Flexible', 'Done',
            :d_history, 60, now()),
        (:todo, :project, :domain, 'FR18 việc cần xếp', 'Flexible', 'ToDo',
            :d_todo, 60, NULL)"""),
        {
            "history": IDS["history"],
            "todo": IDS["todo"],
            "project": IDS["project"],
            "domain": DOMAIN_CODING,
            "d_history": local(0, 23),
            "d_todo": local(3, 17),
        },
    )


async def add_sessions(conn, sessions) -> None:
    await conn.execute(
        text("""INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt",
            "StopReason", "CaptureMethod", "IsConfirmed")
        VALUES (:task, :start, :end, :reason, 'Timer', true)"""),
        [
            {
                "task": IDS["history"],
                "start": local(-days, hour),
                "end": local(-days, hour, 30),
                "reason": reason,
            }
            for hour, days, reason in sessions
        ],
    )


async def add_perf_sessions(conn, count: int) -> None:
    """Spread over the last 89 days and all hours; 1 in 3 interrupted."""
    await conn.execute(
        text("""INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt",
            "StopReason", "CaptureMethod", "IsConfirmed")
        SELECT :task, s, s + interval '30 minutes',
               CASE WHEN g % 3 = 0 THEN 'Interrupted' ELSE 'Completed' END,
               'Timer', true
        FROM generate_series(1, :count) AS g,
             LATERAL (SELECT now() - (g % 89 + 1) * interval '1 day'
                                   - (g % 24) * interval '1 hour' AS s) AS t"""),
        {"task": IDS["history"], "count": count},
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


def check(label: str, ok: bool, detail: object = "") -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {label} {detail}")
    return ok


def scores(body: dict) -> dict[int, float]:
    return {h["hour"]: h["score"] for h in body["hours"]}


async def run_checks(engine, client: httpx.AsyncClient) -> bool:
    ok = True
    print("\n1) GET trước khi onboarding")
    body = (await client.get("/profile/productive-hours")).json()
    ok &= check(
        "onboarded false, hours rỗng",
        body["onboarded"] is False and body["hours"] == [],
        body,
    )

    print("\n2) PUT onboarding ['morning']")
    response = await client.put(
        "/profile/productive-hours/onboarding", json={"blocks": ["morning"]}
    )
    body = response.json()
    ok &= check(
        "200 + 24 giờ", response.status_code == 200 and len(body["hours"]) == 24
    )
    ok &= check(
        "top_hours = [6, 7, 8]", body["top_hours"] == [6, 7, 8], body["top_hours"]
    )
    before = scores(body)

    print("\n3) Thêm 12 phiên (8 tốt 19h/20h, 4 Interrupted 8h/9h) rồi recompute")
    async with engine.begin() as conn:
        await add_sessions(conn, SESSIONS)
    started = time.perf_counter()
    response = await client.post("/learning/productive-hours/recompute")
    elapsed = (time.perf_counter() - started) * 1000
    ok &= check(
        "24 giờ, 12 phiên",
        response.json() == {"hours_updated": 24, "sessions_used": 12},
        f"{response.json()} ({elapsed:.0f} ms)",
    )
    body = (await client.get("/profile/productive-hours")).json()
    after = scores(body)
    for hour in (19, 20, 8, 9):
        print(f"     giờ {hour}: {before[hour]} → {after[hour]}")
    ok &= check("tối tăng (0.611)", after[19] == after[20] == 0.611)
    ok &= check("sáng 8h/9h giảm (0.571)", after[8] == after[9] == 0.571)
    ok &= check("giờ không có phiên giữ nguyên", after[6] == 0.8 and after[12] == 0.3)
    ok &= check("top_hours mới", True, body["top_hours"])

    print("\n4) Recompute lần 2 (AC3)")
    again = (await client.post("/learning/productive-hours/recompute")).json()
    body_again = (await client.get("/profile/productive-hours")).json()
    ok &= check(
        "kết quả giống hệt",
        again == {"hours_updated": 24, "sessions_used": 12} and body_again == body,
    )

    print("\n5) FR-10 POST /schedule/propose (gọi Gemini 1 lần)")
    response = await client.post("/schedule/propose", json={"language": "vi"})
    if response.status_code != 200:
        ok &= check("FR-10 trả 200", False, response.status_code)
    else:
        proposal = response.json()
        for slot in proposal["slots"]:
            start = datetime.fromisoformat(slot["start"]).astimezone(ZONE)
            print(
                f"     slot {start:%a %d/%m %H:%M} ({slot['proposed_minutes']}') — {slot['explanation']}"
            )
        starts = [
            datetime.fromisoformat(s["start"]).astimezone(ZONE).hour
            for s in proposal["slots"]
        ]
        ok &= check("có slot", bool(starts))
        ok &= check(
            "slot bắt đầu trong top_hours",
            all(h in body["top_hours"] for h in starts),
            starts,
        )
    return ok


async def time_recompute(engine, session_factory) -> list[float]:
    """End to end from this machine: includes every network round trip."""
    async with engine.begin() as conn:
        await add_perf_sessions(conn, PERF_SESSIONS - len(SESSIONS))
    settings = get_settings()
    timings = []
    for _ in range(PERF_RUNS + 1):
        async with session_factory() as session:
            use_case = get_recompute_productive_hours(session, settings)
            started = time.perf_counter()
            result = await use_case.execute(IDS["user"])
            timings.append((time.perf_counter() - started) * 1000)
    print(f"     recompute đọc {result.sessions_used} phiên")
    return timings[1:]  # the first run warms up the pool


async def db_ms(conn, statement, params) -> float:
    """Planning + execution time inside Postgres (EXPLAIN ANALYZE runs the statement)."""
    sql = text("EXPLAIN (ANALYZE, FORMAT JSON) " + str(statement))
    plan = (await conn.execute(sql, params)).scalar_one()
    plan = json.loads(plan) if isinstance(plan, str) else plan
    return plan[0]["Planning Time"] + plan[0]["Execution Time"]


async def time_server_side(engine, session_factory) -> list[float]:
    """AC7 as agreed: DB time of the 4 statements + the Python compute, no network.

    The upsert really runs, inside a transaction that is rolled back.
    """
    settings = get_settings()
    user = {"user_id": IDS["user"]}
    since = datetime.now(UTC) - timedelta(days=settings.productive_hours_window_days)
    async with session_factory() as session:
        repo = SqlProductiveHoursRepository(session)
        profile = await repo.get_profile(IDS["user"])
        found = await repo.get_sessions(IDS["user"], since)
    priors = {h: row.prior for h, row in profile.hours.items()}

    timings = []
    for _ in range(PERF_RUNS):
        started = time.perf_counter()
        sessions = [s for s in found if is_learnable(s, since)]
        hours = learned_scores(
            priors,
            sessions,
            profile.time_zone,
            Decimal(str(settings.productive_prior_weight)),
            settings.productive_min_session_minutes,
        )
        compute = (time.perf_counter() - started) * 1000
        async with engine.connect() as conn:
            transaction = await conn.begin()
            db = await db_ms(conn, GET_USER, user)
            db += await db_ms(conn, GET_HOURS, user)
            db += await db_ms(conn, GET_SESSIONS, {**user, "since": since})
            db += await db_ms(conn, UPSERT_HOURS, hours_params(IDS["user"], hours))
            await transaction.rollback()
        timings.append(db + compute)
    print(f"     {len(sessions)} phiên · phần tính Python lần cuối {compute:.1f} ms")
    return timings


async def main() -> None:
    engine = create_engine(get_settings())
    app.state.session_factory = create_session_factory(engine)
    ok = False
    try:
        async with engine.begin() as conn:
            await setup(conn)
        print(f"Đã tạo user test fr18-try-{RUN}@example.invalid")
        transport = httpx.ASGITransport(app=app)  # no lifespan → no startup scan
        headers = {"Authorization": f"Bearer {make_token()}"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fr18", headers=headers, timeout=60
        ) as client:
            ok = await run_checks(engine, client)

        print(f"\n6) Tốc độ recompute với {PERF_SESSIONS} phiên, {PERF_RUNS} lần")
        print("   a) Trọn vẹn từ máy này (có mạng) — chỉ để tham khảo")
        timings = await time_recompute(engine, app.state.session_factory)
        p95 = statistics.quantiles(timings, n=20)[-1]
        print(
            f"     trung vị {statistics.median(timings):.0f} ms · p95 {p95:.0f} ms · max {max(timings):.0f} ms"
        )
        print("   b) Phía server: thời gian DB của 4 câu SQL + phần tính Python (AC7)")
        timings = await time_server_side(engine, app.state.session_factory)
        p95 = statistics.quantiles(timings, n=20)[-1]
        print(
            f"     trung vị {statistics.median(timings):.1f} ms · p95 {p95:.1f} ms · max {max(timings):.1f} ms"
        )
        ok &= check("AC7: p95 phía server < 200 ms", p95 < 200)
    finally:
        left = await cleanup(engine)
        print(f"\nĐã xoá dữ liệu thử. Còn lại: {left} dòng.")
        await engine.dispose()
    print("\nKẾT QUẢ:", "TẤT CẢ OK" if ok else "CÓ MỤC FAIL (xem ở trên)")


asyncio.run(main())
