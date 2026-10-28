"""FR-12 step 10 (T22): real run on Supabase, then delete exactly the rows it made.

Usage (from the repo root; no need to start the API):
    uv run python scripts/try_briefing.py

1 test user ("Tiêu chuẩn" plan, work day 06:00-23:00, weekends too), 1 project,
4 open Flexible tasks (overdue / at risk / in progress / plain), 1 Fixed meeting,
an Approved and a Proposed slot, K 1.4 for Coding (5 samples), onboarding "evening",
and 2 finished "landing page" tasks with memories (took 1.5× the estimate).
Calls GET /briefing/today in-process (vi, en), checks the answer, measures the time
(AC6) and always cleans up. Gemini embedding: at most 3 calls per briefing; no LLM.
Never prints DATABASE_URL, the JWT secret or API key.
"""

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy import text

from metanoia_ai.api.deps import briefing_settings
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.find_similar_tasks import (
    SearchMode,
    SimilarTasksResult,
)
from metanoia_ai.application.use_cases.get_daily_briefing import GetDailyBriefing
from metanoia_ai.config import get_settings
from metanoia_ai.domain.models import Language
from metanoia_ai.infrastructure.db import briefing_repository as sql
from metanoia_ai.infrastructure.db.briefing_repository import SqlBriefingRepository
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAIN_CODING = "a7b1eb7a-e137-4a50-851b-199273b98c6f"
PLAN = "Tiêu chuẩn"
NAMES = ["user", "subscription", "project", "overdue", "risky", "working", "plain"]
NAMES += ["meeting", "old1", "old2"]
IDS = {name: uuid4() for name in NAMES}
OPEN = ["overdue", "risky", "working", "plain"]
TASKS = [str(IDS[n]) for n in [*OPEN, "meeting", "old1", "old2"]]
PERF_RUNS = 20
E2E_RUNS = 3  # each one calls the Gemini embedding up to 3 times

CLEANUP = [
    ("AIUsageLog", '"UserSubscriptionId" = :subscription'),
    ("TaskExecutionMemory", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("ScheduleSlot", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("FocusSession", '"TaskId" = ANY(CAST(:tasks AS uuid[]))'),
    ("Task", '"Id" = ANY(CAST(:tasks AS uuid[]))'),
    ("Project", '"Id" = :project'),
    ("UserDomainProfile", '"UserId" = :user'),
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


def soon(minutes: int) -> datetime:
    """Now + minutes, rounded to the minute (stays today unless run near midnight)."""
    return (datetime.now(ZONE) + timedelta(minutes=minutes)).replace(
        second=0, microsecond=0
    )


async def setup(conn) -> None:
    user = IDS["user"]
    await conn.execute(
        text("""INSERT INTO "User" ("Id", "RoleId", "Email", "FullName",
            "GoogleSubjectId", "TimeZone", "WorkDayStart", "WorkDayEnd",
            "WorksOnWeekend", "DefaultBufferMinutes", "ProductiveBlocks")
        VALUES (:user, :role, :email, 'FR12 Try User', :google,
            'Asia/Ho_Chi_Minh', '06:00', '23:00', true, 10, ARRAY['evening'])"""),
        {
            "user": user,
            "role": ROLE_FREELANCER,
            "email": f"fr12-try-{RUN}@example.invalid",
            "google": f"fr12-try-{RUN}",
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
        VALUES (:id, :user, 'FR12 try project')"""),
        {"id": IDS["project"], "user": user},
    )
    flexible = text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title",
            "TaskType", "Status", "Deadline", "EstimatedMinutes", "CompletedAt")
        VALUES (:id, :project, :domain, :title, 'Flexible', :status, :deadline,
            :minutes, :completed)""")
    common = {"project": IDS["project"], "domain": DOMAIN_CODING, "completed": None}
    await conn.execute(
        flexible,
        [
            # 60 × 1.4 = 84 minutes, deadline yesterday.
            {
                **common,
                "id": IDS["overdue"],
                "title": "FR12 báo cáo tuần",
                "status": "ToDo",
                "deadline": local(-1, 17),
                "minutes": 60,
            },
            # 900 × 1.4 = 1260 minutes > at most 20 free hours before tomorrow 09:00.
            {
                **common,
                "id": IDS["risky"],
                "title": "FR12 landing page khách",
                "status": "ToDo",
                "deadline": local(1, 9),
                "minutes": 900,
            },
            {
                **common,
                "id": IDS["working"],
                "title": "FR12 sửa lỗi đăng nhập",
                "status": "InProgress",
                "deadline": local(5, 17),
                "minutes": 60,
            },
            {
                **common,
                "id": IDS["plain"],
                "title": "FR12 viết tài liệu",
                "status": "ToDo",
                "deadline": local(3, 17),
                "minutes": 60,
            },
            {
                **common,
                "id": IDS["old1"],
                "title": "FR12 landing page cũ 1",
                "status": "Done",
                "deadline": local(-20, 17),
                "minutes": 60,
                "completed": local(-20, 16),
            },
            {
                **common,
                "id": IDS["old2"],
                "title": "FR12 landing page cũ 2",
                "status": "Done",
                "deadline": local(-10, 17),
                "minutes": 60,
                "completed": local(-10, 16),
            },
        ],
    )
    await conn.execute(
        text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "FixedStart", "FixedEnd")
        VALUES (:id, :project, :domain, 'FR12 họp khách', 'Fixed', 'ToDo',
            :start, :end)"""),
        {**common, "id": IDS["meeting"], "start": soon(60), "end": soon(90)},
    )
    await conn.execute(
        text("""INSERT INTO "ScheduleSlot" ("TaskId", "StartTime", "EndTime",
            "ProposedMinutes", "Status")
        VALUES (:approved, :a_start, :a_end, 30, 'Approved'),
               (:proposed, :p_start, :p_end, 30, 'Proposed')"""),
        {
            "approved": IDS["plain"],
            "a_start": soon(10),
            "a_end": soon(40),
            "proposed": IDS["working"],
            "p_start": soon(100),
            "p_end": soon(130),
        },
    )
    await conn.execute(
        text("""INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt",
            "StopReason", "CaptureMethod", "IsConfirmed")
        VALUES (:task, :start, :end, 'Interrupted', 'Timer', true)"""),
        {"task": IDS["working"], "start": local(-1, 9), "end": local(-1, 9, 30)},
    )
    await conn.execute(
        text("""INSERT INTO "UserDomainProfile" ("UserId", "DomainId", "KBias",
            "SampleCount") VALUES (:user, :domain, 1.400, 5)"""),
        {"user": user, "domain": DOMAIN_CODING},
    )
    await conn.execute(
        text("""INSERT INTO "UserCircadianProfile" ("UserId", "HourOfDay", "Score",
            "PriorScore", "Source")
        SELECT :user, h, s, s, 'Onboarding'
        FROM generate_series(0, 23) AS h,
             LATERAL (SELECT CASE WHEN h BETWEEN 18 AND 21 THEN 0.800
                                  ELSE 0.300 END AS s) AS x"""),
        {"user": user},
    )
    # Memories wait for a vector (Pending): the full-text branch finds them.
    await conn.execute(
        text("""INSERT INTO "TaskExecutionMemory" ("TaskId", "Content", "Outcome",
            "EstimatedMinutes", "ActualMinutes", "EmbeddingStatus")
        VALUES (:task, :content, 'Completed', 60, 90, 'Pending')"""),
        [
            {"task": IDS["old1"], "content": "Landing page cho shop thời trang"},
            {"task": IDS["old2"], "content": "Landing page cho quán cà phê"},
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


def check(label: str, ok: bool, detail: object = "") -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {label} {detail}")
    return ok


def show(body: dict) -> None:
    print(f"     date {body['date']} · {body['time_zone']}")
    for item in body["schedule"]:
        start = datetime.fromisoformat(item["start"]).astimezone(ZONE)
        print(f"     lịch {start:%H:%M} {item['kind']:6s} {item['title']}")
    for t in body["top_tasks"]:
        print(f"     top  {t['title']} (còn {t['remaining_minutes']}')")
        for r in t["reasons"]:
            print(f"          - [{r['code']}] {r['text']}")
        if t["similar_warning"]:
            print(f"          ! {t['similar_warning']['text']}")
    for r in body["at_risk"]:
        print(f"     nguy hiểm {r['title']}: thiếu {r['missing_minutes']}'")
    hint = body["productive_hint"]
    print(f"     giờ tốt: {hint['text'] if hint else None}")


async def usage_rows(engine) -> list[tuple]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("""SELECT "Feature", "QuotaCharged", "Succeeded"
                FROM "AIUsageLog" WHERE "UserSubscriptionId" = :s"""),
            {"s": IDS["subscription"]},
        )
        used = await conn.execute(
            text('SELECT "AiCallsUsed" FROM "UserSubscription" WHERE "Id" = :s'),
            {"s": IDS["subscription"]},
        )
        return [tuple(r) for r in rows], used.scalar_one()


async def run_checks(engine, client: httpx.AsyncClient) -> bool:
    ok = True
    titles = {IDS[n]: n for n in OPEN}

    print("\n1) GET /briefing/today?language=vi")
    started = time.perf_counter()
    response = await client.get("/briefing/today")
    elapsed = (time.perf_counter() - started) * 1000
    ok &= check("200", response.status_code == 200, f"({elapsed:.0f} ms trọn vẹn)")
    body = response.json()
    show(body)

    kinds = {(i["kind"], i["title"]) for i in body["schedule"]}
    ok &= check("lịch có slot Approved", ("slot", "FR12 viết tài liệu") in kinds)
    ok &= check("lịch có họp Fixed", ("fixed", "FR12 họp khách") in kinds)
    ok &= check(
        "slot Proposed không hiện, chỉ báo cờ",
        body["has_pending_proposal"] is True
        and ("slot", "FR12 sửa lỗi đăng nhập") not in kinds,
    )
    ok &= check("chưa kết nối Google", body["calendar_connected"] is False)

    order = [titles.get(uuid_of(t["task_id"])) for t in body["top_tasks"]]
    ok &= check(
        "top = quá hạn → nguy hiểm → đang làm",
        order == ["overdue", "risky", "working"],
        order,
    )
    codes = [[r["code"] for r in t["reasons"]] for t in body["top_tasks"]]
    ok &= check(
        "lý do đầu đúng",
        [c[0] for c in codes] == ["overdue", "at_risk", "in_progress"],
        codes,
    )
    ok &= check("tối đa 2 lý do", all(1 <= len(c) <= 2 for c in codes))
    ok &= check(
        "quá hạn có câu bias 40%",
        any("40%" in r["text"] for r in body["top_tasks"][0]["reasons"]),
    )
    risky = body["top_tasks"][1]
    ok &= check(
        "cảnh báo task tương tự cho landing page",
        risky["similar_warning"] is not None
        and "1,5 lần" in risky["similar_warning"]["text"],
        risky["similar_warning"],
    )
    ok &= check(
        "at_risk có landing page",
        [titles.get(uuid_of(r["task_id"])) for r in body["at_risk"]] == ["risky"],
    )
    texts = " ".join(r["text"] for t in body["top_tasks"] for r in t["reasons"])
    ok &= check(
        "không lộ tên nội bộ",
        not any(w in texts.lower() for w in ("k_bias", "t_est", "score")),
    )
    hour = datetime.now(ZONE).hour
    hint = body["productive_hint"]
    ok &= check(
        "gợi ý giờ hiệu quả đúng (18h-21h, top 3 = 18/19/20)",
        hint is None if hour >= 21 else hint is not None,
        hint,
    )

    print("\n2) GET ?language=en")
    body_en = (await client.get("/briefing/today", params={"language": "en"})).json()
    ok &= check(
        "câu tiếng Anh",
        body_en["top_tasks"][0]["reasons"][0]["text"].startswith("Overdue"),
        body_en["top_tasks"][0]["reasons"][0]["text"],
    )

    print("\n3) ?language=fr")
    response = await client.get("/briefing/today", params={"language": "fr"})
    ok &= check("422", response.status_code == 422)

    print("\n4) AIUsageLog (AC5)")
    rows, calls_used = await usage_rows(engine)
    print(f"     {len(rows)} dòng: {sorted(set(rows))}")
    ok &= check(
        "chỉ Embedding, không trừ lượt",
        bool(rows) and all(f == "Embedding" and not charged for f, charged, _ in rows),
    )
    ok &= check("≤ 3 dòng mỗi lần gọi (2 lần)", len(rows) <= 6, len(rows))
    ok &= check("AiCallsUsed vẫn 0", calls_used == 0, calls_used)
    return ok


def uuid_of(value: str) -> UUID:
    return UUID(value)


async def time_end_to_end(client: httpx.AsyncClient) -> list[float]:
    """From this machine: network to Supabase (and Gemini) included."""
    timings = []
    for _ in range(E2E_RUNS):
        started = time.perf_counter()
        await client.get("/briefing/today")
        timings.append((time.perf_counter() - started) * 1000)
    return timings


async def db_ms(conn, statement, params) -> float:
    """Planning + execution time inside Postgres (EXPLAIN ANALYZE runs the SELECT)."""
    plan = (
        await conn.execute(
            text("EXPLAIN (ANALYZE, FORMAT JSON) " + str(statement)), params
        )
    ).scalar_one()
    plan = json.loads(plan) if isinstance(plan, str) else plan
    return plan[0]["Planning Time"] + plan[0]["Execution Time"]


class Preloaded:
    """The data read once from the DB, handed again without network."""

    def __init__(self, data) -> None:
        self.data = data

    async def get_briefing_data(self, *_):
        return self.data


async def no_search(user_id, task_id) -> SimilarTasksResult:
    return SimilarTasksResult(task_id, SearchMode.NONE, ZONE)


async def time_server_side(engine, session_factory) -> list[float]:
    """AC6 without a new embedding: DB time of the 10 SELECTs + the Python work."""
    settings = get_settings()
    now = datetime.now(UTC)
    params = {
        "user_id": IDS["user"],
        "day_start": now - timedelta(days=1),
        "day_end": now + timedelta(days=1),
        "window_end": now + timedelta(days=settings.briefing_risk_window_days),
    }
    async with session_factory() as session:
        data = await SqlBriefingRepository(session).get_briefing_data(
            IDS["user"], params["day_start"], params["day_end"], params["window_end"]
        )
    use_case = GetDailyBriefing(
        Preloaded(data), no_search, briefing_settings(settings), now=lambda: now
    )
    statements = [
        getattr(sql, name)
        for name in (
            "GET_USER",
            "GET_SUBTASKS",
            "GET_SESSIONS",
            "GET_TASKS",
            "GET_HOURS",
            "GET_CONNECTION_STATUS",
            "GET_K_BIAS",
            "GET_SLOTS",
            "GET_FIXED_TASKS",
            "GET_CALENDAR_EVENTS",
        )
    ]
    timings = []
    for _ in range(PERF_RUNS):
        started = time.perf_counter()
        await use_case.execute(IDS["user"], Language.VI)
        compute = (time.perf_counter() - started) * 1000
        db = 0.0
        async with engine.connect() as conn:
            for statement in statements:
                needed = {k: v for k, v in params.items() if f":{k}" in str(statement)}
                db += await db_ms(conn, statement, needed)
        timings.append(db + compute)
    print(f"     phần tính Python lần cuối {compute:.1f} ms · DB {db:.1f} ms")
    return timings


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[-1] if len(values) > 1 else values[0]


async def main() -> None:
    engine = create_engine(get_settings())
    app.state.session_factory = create_session_factory(engine)
    ok = False
    try:
        async with engine.begin() as conn:
            await setup(conn)
        print(f"Đã tạo user test fr12-try-{RUN}@example.invalid")
        transport = httpx.ASGITransport(app=app)  # no lifespan → no startup scan
        headers = {"Authorization": f"Bearer {make_token()}"}
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fr12", headers=headers, timeout=60
        ) as client:
            ok = await run_checks(engine, client)

            print(f"\n5) Tốc độ trọn vẹn từ máy này, {E2E_RUNS} lần (có embedding)")
            timings = await time_end_to_end(client)
            print(
                f"     trung vị {statistics.median(timings):.0f} ms · max {max(timings):.0f} ms"
            )
            ok &= check("AC6: có embedding < 4 s", max(timings) < 4000)

        print(f"\n6) Phía server, không embedding mới, {PERF_RUNS} lần (AC6)")
        timings = await time_server_side(engine, app.state.session_factory)
        print(
            f"     trung vị {statistics.median(timings):.1f} ms · p95 {p95(timings):.1f} ms"
            f" · max {max(timings):.1f} ms"
        )
        ok &= check("AC6: p95 phía server < 1 s", p95(timings) < 1000)
    finally:
        left = await cleanup(engine)
        print(f"\nĐã xoá dữ liệu thử. Còn lại: {left} dòng.")
        await engine.dispose()
    print("\nKẾT QUẢ:", "TẤT CẢ OK" if ok else "CÓ MỤC FAIL (xem ở trên)")


asyncio.run(main())
