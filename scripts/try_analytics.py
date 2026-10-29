"""FR-20 step 8 (T15): real run on Supabase, then delete exactly the rows it made.

Usage (from the repo root; no need to start the API):
    uv run python scripts/try_analytics.py          # seed → call → print → clean up
    uv run python scripts/try_analytics.py --keep   # keep the demo data (plan #11)

User A (demo): 1 client, 2 projects (one without client), ~33 tasks: Writing learned
12 times (K about 1.3, "confident"), Design 7 times (faster, "learning"), Coding 2 times
("not enough data"), Done tasks without usable time, an AI estimate, overdue / skipped /
future tasks and a meeting. User B (speed, AC7): 500 tasks, 2,000 timer sessions, 200 K updates.
Calls GET /analytics/overview and /analytics/breakdown in-process for both users,
checks the answers and that each user only sees their own rows (AC6), measures the
server-side time (AC7). No LLM, no embedding. Never prints DATABASE_URL or secrets.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy import text

from metanoia_ai.api.deps import analytics_settings
from metanoia_ai.api.main import app
from metanoia_ai.application.use_cases.get_analytics_breakdown import (
    GetAnalyticsBreakdown,
)
from metanoia_ai.application.use_cases.get_analytics_overview import (
    GetAnalyticsOverview,
)
from metanoia_ai.config import get_settings
from metanoia_ai.domain.analytics import AnalyticsRange, BreakdownBy
from metanoia_ai.domain.kbias import DEFAULT_ALPHA, INITIAL_K_BIAS, update_k_bias
from metanoia_ai.domain.models import Language
from metanoia_ai.infrastructure.db import analytics_repository as sql
from metanoia_ai.infrastructure.db.analytics_repository import SqlAnalyticsRepository
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAINS = {
    "Coding": "a7b1eb7a-e137-4a50-851b-199273b98c6f",
    "Design": "4fbf52d2-705d-424d-bdbc-bc25a1113c0a",
    "Writing": "494703e0-10de-4457-b245-7c24f088deeb",
}
IDS = {n: uuid4() for n in ("user_a", "user_b", "client", "landing", "personal", "big")}
PROJECTS = [IDS["landing"], IDS["personal"], IDS["big"]]
USERS = [IDS["user_a"], IDS["user_b"]]
PERF_RUNS = 20
B_TASKS, B_DONE, B_SESSIONS_EACH, B_LOGS = 500, 400, 5, 200

TASKS_OF_RUN = (
    'SELECT "Id" FROM "Task" WHERE "ProjectId" = ANY(CAST(:projects AS uuid[]))'
)
PROFILES_OF_RUN = (
    'SELECT "Id" FROM "UserDomainProfile" WHERE "UserId" = ANY(CAST(:users AS uuid[]))'
)
CLEANUP = [
    ("KBiasUpdateLog", f'"UserDomainProfileId" IN ({PROFILES_OF_RUN})'),
    ("FocusSession", f'"TaskId" IN ({TASKS_OF_RUN})'),
    ("Task", '"ProjectId" = ANY(CAST(:projects AS uuid[]))'),
    ("Project", '"Id" = ANY(CAST(:projects AS uuid[]))'),
    ("Client", '"Id" = :client'),
    ("UserDomainProfile", '"UserId" = ANY(CAST(:users AS uuid[]))'),
    ("User", '"Id" = ANY(CAST(:users AS uuid[]))'),
]
CLEANUP_PARAMS = {"projects": PROJECTS, "users": USERS, "client": IDS["client"]}


def ago(days: float, hour: int = 10) -> datetime:
    """`days` days ago at `hour`:00, user's time."""
    day = datetime.now(ZONE).date() - timedelta(days=int(days))
    return datetime(day.year, day.month, day.day, hour, tzinfo=ZONE)


# --- seed ---

INSERT_TASK = text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title",
        "TaskType", "Status", "Deadline", "EstimatedMinutes", "AiEstimatedMinutes",
        "CompletedAt", "FixedStart", "FixedEnd")
    VALUES (:id, :project, :domain, :title, :type, :status, :deadline, :minutes,
        :ai_minutes, :completed, :fixed_start, :fixed_end)""")
INSERT_SESSION = text("""INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt",
        "StopReason", "CaptureMethod", "IsConfirmed")
    VALUES (:task, :start, :end, :reason, 'Timer', :confirmed)""")
INSERT_LOG = text("""INSERT INTO "KBiasUpdateLog" ("UserDomainProfileId", "TaskId",
        "OldKBias", "NewKBias", "Alpha", "EstimatedMinutes", "ActualMinutes",
        "ObservedRatio", "CaptureMethod", "ConfidenceTier", "SampleIndex", "CreatedAt")
    VALUES (:profile, :task, :old, :new, :alpha, :est, :act, :ratio, 'Timer', 'High',
        :index, :created)""")


def task_row(project, domain, title, status, deadline, minutes, **extra) -> dict:
    return {
        "id": uuid4(),
        "project": project,
        "domain": DOMAINS[domain],
        "title": title,
        "type": "Flexible",
        "status": status,
        "deadline": deadline,
        "minutes": minutes,
        "ai_minutes": None,
        "completed": None,
        "fixed_start": None,
        "fixed_end": None,
        **extra,
    }


async def insert_user(conn, user_id, name: str) -> None:
    await conn.execute(
        text("""INSERT INTO "User" ("Id", "RoleId", "Email", "FullName",
            "GoogleSubjectId", "TimeZone")
        VALUES (:user, :role, :email, :full_name, :google, 'Asia/Ho_Chi_Minh')"""),
        {
            "user": user_id,
            "role": ROLE_FREELANCER,
            "email": f"fr20-try-{name}-{RUN}@example.invalid",
            "full_name": f"FR20 Try {name.upper()}",
            "google": f"fr20-try-{name}-{RUN}",
        },
    )


async def learned_domain(conn, user, project, domain, ratios, first_day) -> list[dict]:
    """Done tasks of one domain, one timer session each, learned in order (FR-17 math)."""
    profile = uuid4()
    k = INITIAL_K_BIAS
    tasks, sessions, logs = [], [], []
    for index, ratio in enumerate(ratios, start=1):
        done_at = ago(first_day - 3 * (index - 1), 16)
        actual = round(60 * ratio)
        row = task_row(
            project,
            domain,
            f"FR20 {domain} #{index}",
            "Done",
            done_at,
            60,
            completed=done_at,
        )
        tasks.append(row)
        sessions.append(
            {
                "task": row["id"],
                "start": done_at - timedelta(minutes=actual),
                "end": done_at,
                "reason": "Completed",
                "confirmed": True,
            }
        )
        new_k = round(update_k_bias(k, 60, actual, DEFAULT_ALPHA), 3)
        logs.append(
            {
                "profile": profile,
                "task": row["id"],
                "old": k,
                "new": new_k,
                "alpha": DEFAULT_ALPHA,
                "est": 60,
                "act": actual,
                "ratio": round(actual / 60, 3),
                "index": index,
                "created": done_at,
            }
        )
        k = new_k
    await conn.execute(
        text("""INSERT INTO "UserDomainProfile" ("Id", "UserId", "DomainId", "KBias",
            "SampleCount") VALUES (:id, :user, :domain, :k, :n)"""),
        {
            "id": profile,
            "user": user,
            "domain": DOMAINS[domain],
            "k": k,
            "n": len(ratios),
        },
    )
    await conn.execute(INSERT_TASK, tasks)
    await conn.execute(INSERT_SESSION, sessions)
    await conn.execute(INSERT_LOG, logs)
    return tasks


async def setup_user_a(conn) -> None:
    user = IDS["user_a"]
    await insert_user(conn, user, "a")
    await conn.execute(
        text(
            """INSERT INTO "Client" ("Id", "UserId", "Name") VALUES (:id, :user, :name)"""
        ),
        {"id": IDS["client"], "user": user, "name": "FR20 Khách ABC"},
    )
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "Name", "ClientId")
        VALUES (:landing, :user, 'FR20 Landing page ABC', :client),
               (:personal, :user, 'FR20 Việc cá nhân', NULL)"""),
        {**IDS, "user": user},
    )
    landing, personal = IDS["landing"], IDS["personal"]
    # Writing: usually 30-40% longer → K ends near 1.3 after 12 samples (confident).
    writing = [1.4, 1.3, 1.5, 1.25, 1.35, 1.3, 1.45, 1.2, 1.3, 1.35, 1.25, 1.3]
    await learned_domain(conn, user, landing, "Writing", writing, 34)
    # Design: about 30% faster → K below 0.9 after 7 samples (learning).
    await learned_domain(conn, user, personal, "Design", [0.7] * 7, 20)
    # Coding: only 2 samples → not enough data.
    await learned_domain(conn, user, landing, "Coding", [1.5, 1.6], 8)

    no_time = task_row(
        landing, "Coding", "FR20 không bấm giờ", "Done", ago(2), 90, completed=ago(3)
    )
    running = task_row(
        landing, "Coding", "FR20 timer đang chạy", "Done", ago(2), 60, completed=ago(2)
    )
    unconfirmed = task_row(
        personal,
        "Design",
        "FR20 giờ chưa xác nhận",
        "Done",
        ago(4),
        60,
        completed=ago(5),
    )
    ai_estimate = task_row(
        landing,
        "Writing",
        "FR20 bài viết AI ước lượng",
        "Done",
        ago(1),
        45,
        ai_minutes=45,
        completed=ago(2, 15),
    )
    late = task_row(
        personal, "Writing", "FR20 xong trễ hạn", "Done", ago(6), 30, completed=ago(5)
    )
    others = [
        task_row(landing, "Coding", f"FR20 quá hạn {i}", "ToDo", ago(i + 1), 60)
        for i in range(3)
    ]
    others += [
        task_row(personal, "Design", f"FR20 bỏ qua {i}", "Skipped", ago(i + 2), 60)
        for i in range(2)
    ]
    others += [
        task_row(landing, "Writing", f"FR20 chưa tới hạn {i}", "ToDo", ago(-3 - i), 60)
        for i in range(2)
    ]
    meeting = task_row(
        landing,
        "Coding",
        "FR20 họp khách",
        "Done",
        None,
        None,
        type="Fixed",
        fixed_start=ago(1, 9),
        fixed_end=ago(1, 10),
    )
    await conn.execute(
        INSERT_TASK,
        [no_time, running, unconfirmed, ai_estimate, late, *others, meeting],
    )
    await conn.execute(
        INSERT_SESSION,
        [
            {
                "task": running["id"],
                "start": ago(2, 9),
                "end": None,
                "reason": None,
                "confirmed": True,
            },
            {
                "task": unconfirmed["id"],
                "start": ago(5, 9),
                "end": ago(5, 10),
                "reason": "Completed",
                "confirmed": False,
            },
            {
                "task": ai_estimate["id"],
                "start": ago(2, 13),
                "end": ago(2, 14),
                "reason": "Completed",
                "confirmed": True,
            },
            {
                "task": late["id"],
                "start": ago(5, 9),
                "end": ago(5, 9) + timedelta(minutes=40),
                "reason": "Completed",
                "confirmed": True,
            },
        ],
    )


async def setup_user_b(conn) -> None:
    """Speed data (AC7): 500 tasks, 2,000 sessions, 200 K updates, generated in SQL."""
    user = IDS["user_b"]
    await insert_user(conn, user, "b")
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "Name")
        VALUES (:big, :user, 'FR20 B secret project')"""),
        {"big": IDS["big"], "user": user},
    )
    params = {"project": IDS["big"], "domain": DOMAINS["Writing"], "done": B_DONE}
    await conn.execute(
        text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "Deadline", "EstimatedMinutes", "CompletedAt")
        SELECT gen_random_uuid(), :project, :domain, 'FR20 B secret task ' || g, 'Flexible',
            CASE WHEN g <= :done THEN 'Done' ELSE 'ToDo' END,
            now() - (g % 85) * interval '1 day' + interval '1 day',
            60 + (g % 5) * 30,
            CASE WHEN g <= :done THEN now() - (g % 85) * interval '1 day' END
        FROM generate_series(1, :total) AS g"""),
        {**params, "total": B_TASKS},
    )
    await conn.execute(
        text("""INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt", "StopReason",
            "CaptureMethod", "IsConfirmed")
        SELECT t."Id", t."CompletedAt" - s * interval '1 hour',
            t."CompletedAt" - s * interval '1 hour' + interval '25 minutes',
            'Completed', 'Timer', true
        FROM "Task" t, generate_series(1, :each) AS s
        WHERE t."ProjectId" = :project AND t."Status" = 'Done'"""),
        {**params, "each": B_SESSIONS_EACH},
    )
    profile = uuid4()
    await conn.execute(
        text("""INSERT INTO "UserDomainProfile" ("Id", "UserId", "DomainId", "KBias",
            "SampleCount") VALUES (:id, :user, :domain, 1.250, :n)"""),
        {"id": profile, "user": user, "domain": DOMAINS["Writing"], "n": B_LOGS},
    )
    await conn.execute(
        text("""INSERT INTO "KBiasUpdateLog" ("UserDomainProfileId", "TaskId", "OldKBias",
            "NewKBias", "Alpha", "EstimatedMinutes", "ActualMinutes", "ObservedRatio",
            "CaptureMethod", "ConfidenceTier", "SampleIndex", "CreatedAt")
        SELECT :profile, x."Id", 1.250, 1.250, 0.20, 60, 75, 1.250, 'Timer', 'High',
            row_number() OVER (ORDER BY x."CompletedAt", x."Id"), x."CompletedAt"
        FROM (SELECT "Id", "CompletedAt" FROM "Task"
              WHERE "ProjectId" = :project AND "Status" = 'Done'
              ORDER BY "CompletedAt", "Id" LIMIT :logs) AS x"""),
        {**params, "profile": profile, "logs": B_LOGS},
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


# --- calls and checks ---


def make_token(user_id) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=60),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def headers(user_id) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


def check(label: str, ok: bool, detail: object = "") -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {label} {detail}")
    return ok


async def run_checks(client: httpx.AsyncClient) -> bool:
    a, b = headers(IDS["user_a"]), headers(IDS["user_b"])
    ok = True

    print("\n1) User A — GET /analytics/overview (vi)")
    body = (await client.get("/analytics/overview", headers=a)).json()
    print(json.dumps(body, ensure_ascii=False, indent=2))
    c = body["completion"]
    # 30 days: Writing 10 + Design 7 + Coding 2 + 5 other Done + 3 overdue + 2 skipped.
    ok &= check(
        "completion: Fixed và việc chưa tới hạn không tính",
        c["total"] == 29,
        c["total"],
    )
    ok &= check("24 việc xong, 23 đúng hạn", (c["done"], c["on_time_done"]) == (24, 23))
    ok &= check(
        "có 3 việc quá hạn chưa xong, 2 bỏ qua",
        (c["overdue_open"], c["skipped"]) == (3, 2),
    )
    classes = {d["domain_name"]: d["confidence"] for d in body["domains"]}
    ok &= check(
        "độ tin: Writing confident, Design learning, Coding none",
        classes == {"Writing": "confident", "Design": "learning", "Coding": "none"},
        classes,
    )
    writing = next(d for d in body["domains"] if d["domain_name"] == "Writing")
    ok &= check(
        "Writing có xu hướng theo tuần",
        len(writing["trend"]) >= 4,
        len(writing["trend"]),
    )
    ok &= check(
        "có câu nhận xét",
        1 <= len(body["insights"]) <= 3,
        [i["code"] for i in body["insights"]],
    )

    print("\n2) User A — tiếng Anh, range all")
    en = (
        await client.get(
            "/analytics/overview", params={"language": "en", "range": "all"}, headers=a
        )
    ).json()
    ok &= check("from = null", en["from"] is None)
    for d in en["domains"]:
        print("    ", d["text"])

    print("\n3) User A — breakdown")
    for kind in ("project", "client", "task"):
        items = (
            await client.get("/analytics/breakdown", params={"by": kind}, headers=a)
        ).json()["items"]
        print(f"   by={kind}: {len(items)} dòng")
        for i in items[:6]:
            print(
                f"     {i['name']}: {i['estimated_minutes']}→{i['actual_minutes']} phút, "
                f"thiếu giờ {i['missing_actual_count']}, nguồn {i['estimate_source']} · {i['text']}"
            )
        if kind == "project":
            missing = sum(i["missing_actual_count"] for i in items)
            ok &= check(
                "3 việc Done không dùng được giờ thực tế", missing == 3, missing
            )
        if kind == "client":
            ok &= check(
                "có nhóm 'Không có khách hàng'", any(i["id"] is None for i in items)
            )
        if kind == "task":
            sources = {i["name"]: i["estimate_source"] for i in items}
            ok &= check(
                "việc AI ước lượng có nhãn ai",
                sources.get("FR20 bài viết AI ước lượng") == "ai",
            )

    print("\n4) Mỗi user chỉ thấy dữ liệu của mình (AC6)")
    tasks_a = (
        await client.get(
            "/analytics/breakdown", params={"by": "task", "limit": 200}, headers=a
        )
    ).json()
    ok &= check(
        "A không thấy task của B",
        not any("B secret" in i["name"] for i in tasks_a["items"]),
    )
    projects_b = (
        await client.get("/analytics/breakdown", params={"by": "project"}, headers=b)
    ).json()
    ok &= check(
        "B chỉ thấy project của B",
        [i["name"] for i in projects_b["items"]] == ["FR20 B secret project"],
    )
    clients_b = (
        await client.get("/analytics/breakdown", params={"by": "client"}, headers=b)
    ).json()
    ok &= check(
        "B không thấy khách hàng của A",
        all(i["id"] is None for i in clients_b["items"]),
    )
    other = await client.get(
        "/analytics/breakdown",
        params={"by": "task", "project_id": str(IDS["big"])},
        headers=a,
    )
    ok &= check(
        "A hỏi project của B → 404", other.status_code == 404, other.status_code
    )
    return ok


# --- speed (AC7) ---


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
    """The rows read once from the DB, handed again without network."""

    def __init__(self, user, deadline, profiles, done) -> None:
        self.user, self.deadline, self.profiles, self.done = (
            user,
            deadline,
            profiles,
            done,
        )

    async def get_user(self, *_):
        return self.user

    async def get_deadline_tasks(self, *_):
        return self.deadline

    async def get_domain_profiles(self, *_):
        return self.profiles

    async def get_done_tasks(self, *_):
        return self.done


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[-1]


async def time_server_side(engine, session_factory) -> tuple[list[float], list[float]]:
    """User B, range 90d: DB time of each query + the Python work, per request."""
    settings = get_settings()
    user = IDS["user_b"]
    now = datetime.now(UTC)
    async with session_factory() as session:
        repo = SqlAnalyticsRepository(session)
        period = (
            await GetAnalyticsOverview(
                repo, analytics_settings(settings), lambda: now
            ).execute(user, AnalyticsRange.LAST_90_DAYS, Language.VI)
        ).period
        loaded = Preloaded(
            await repo.get_user(user),
            await repo.get_deadline_tasks(user, period),
            await repo.get_domain_profiles(user, period),
            await repo.get_done_tasks(user, period),
        )
    print(
        f"     B, 90 ngày: {len(loaded.done)} task Done, "
        f"{sum(len(t.sessions) for t in loaded.done)} phiên, "
        f"{sum(len(p.updates) for p in loaded.profiles)} lần cập nhật K"
    )
    overview = GetAnalyticsOverview(loaded, analytics_settings(settings), lambda: now)
    breakdown = GetAnalyticsBreakdown(loaded, lambda: now)
    base = {"user_id": user, "start": period.start, "end": period.end}
    overview_sql = [
        (sql.GET_USER, {"user_id": user, "project_id": None}),
        (sql.GET_DEADLINE_TASKS, base),
        (sql.GET_PROFILES, {"user_id": user}),
        (sql.GET_K_UPDATES, base),
        (sql.GET_DONE_TASKS, {**base, "project_id": None}),
    ]
    breakdown_sql = [overview_sql[0], overview_sql[-1]]
    o_times, b_times = [], []
    for _ in range(PERF_RUNS):
        started = time.perf_counter()
        await overview.execute(user, AnalyticsRange.LAST_90_DAYS, Language.VI)
        o_compute = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        await breakdown.execute(
            user, BreakdownBy.TASK, AnalyticsRange.LAST_90_DAYS, Language.VI, None, 200
        )
        b_compute = (time.perf_counter() - started) * 1000
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            o_db = sum([await db_ms(conn, s, p) for s, p in overview_sql])
            b_db = sum([await db_ms(conn, s, p) for s, p in breakdown_sql])
        o_times.append(o_db + o_compute)
        b_times.append(b_db + b_compute)
    print(
        f"     lần cuối: overview DB {o_db:.1f} ms + Python {o_compute:.1f} ms · "
        f"breakdown DB {b_db:.1f} ms + Python {b_compute:.1f} ms"
    )
    return o_times, b_times


async def time_end_to_end(client: httpx.AsyncClient) -> list[float]:
    """From this machine: network to Supabase included (one round trip per query)."""
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        await client.get(
            "/analytics/overview",
            params={"range": "90d"},
            headers=headers(IDS["user_b"]),
        )
        timings.append((time.perf_counter() - started) * 1000)
    return timings


async def main(keep: bool) -> None:
    engine = create_engine(get_settings())
    app.state.session_factory = create_session_factory(engine)
    ok = False
    try:
        async with engine.begin() as conn:
            await setup_user_a(conn)
            await setup_user_b(conn)
        print(f"Đã tạo 2 user test fr20-try-a/b-{RUN}@example.invalid")
        transport = httpx.ASGITransport(app=app)  # no lifespan → no startup scan
        async with httpx.AsyncClient(
            transport=transport, base_url="http://fr20", timeout=60
        ) as client:
            ok = await run_checks(client)
            print("\n5) Trọn vẹn từ máy này (có mạng tới Supabase), user B, 5 lần")
            e2e = await time_end_to_end(client)
            print(
                f"     trung vị {statistics.median(e2e):.0f} ms · max {max(e2e):.0f} ms"
            )

        print(
            f"\n6) Phía server, user B (500 task, 2.000 phiên), {PERF_RUNS} lần (AC7)"
        )
        o_times, b_times = await time_server_side(engine, app.state.session_factory)
        for name, values in (("overview", o_times), ("breakdown by=task", b_times)):
            print(
                f"     {name}: trung vị {statistics.median(values):.1f} ms · p95 {p95(values):.1f} ms"
            )
            ok &= check(f"AC7: {name} p95 phía server < 1 s", p95(values) < 1000)
    finally:
        if keep:
            print(
                f"\n--keep: GIỮ dữ liệu thử (user {IDS['user_a']}, {IDS['user_b']}). "
                "Chạy lại không có --keep sẽ không xoá được bộ này; xoá tay theo 2 user ID trên."
            )
        else:
            left = await cleanup(engine)
            print(f"\nĐã xoá dữ liệu thử. Còn lại: {left} dòng.")
        await engine.dispose()
    print("\nKẾT QUẢ:", "TẤT CẢ OK" if ok else "CÓ MỤC FAIL (xem ở trên)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the demo data")
    asyncio.run(main(parser.parse_args().keep))
