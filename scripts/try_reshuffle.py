"""FR-22d step 7 (T13): urgent block, postpone, catch up with the real Supabase + Gemini.

Usage (from the repo root; no need to start the API):
    uv run python scripts/try_reshuffle.py

1 test user ("Premium" plan, works 08:00-20:00 every day, 10' break) with a client
project, a default project, 4 slots tomorrow, a client meeting (Fixed) tomorrow
15:00-15:30, and 2 missed slots today. Calls the 3 endpoints and 4 spoken sentences
through /assistant/turn in-process (Gemini picks the tool: 1 AI call each), checks the
answers and that the slots did not change (AC6), then ALWAYS deletes exactly the rows
it made and shows that 0 remain. Never prints DATABASE_URL, the JWT secret or keys.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from sqlalchemy import text

from metanoia_ai.api.main import app
from metanoia_ai.config import get_settings
from metanoia_ai.infrastructure.db.engine import create_engine, create_session_factory

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
RUN = uuid4().hex[:8]
ROLE_FREELANCER = "90a6d82a-be10-4e27-89b4-0be88fbf1a03"
DOMAIN_CODING = "a7b1eb7a-e137-4a50-851b-199273b98c6f"
PLAN_PREMIUM = "2c7e4b3f-1d5e-4a9b-8f32-6e8d9c0b1a02"
NAMES = ["user", "subscription", "client", "project", "personal"]
IDS = {name: uuid4() for name in NAMES}

NOW = datetime.now(ZONE).replace(second=0, microsecond=0)
TOMORROW = NOW.date() + timedelta(days=1)


def tomorrow(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TOMORROW, datetime.min.time(), ZONE).replace(
        hour=hour, minute=minute
    )


# name → (title, start, end, status)
SLOTS = {
    "a": ("Đọc tài liệu API", tomorrow(10), tomorrow(11), "Approved"),
    "b": ("Viết blog", tomorrow(11, 10), tomorrow(12), "Approved"),
    "c": ("Thiết kế banner", tomorrow(14, 30), tomorrow(15, 30), "Approved"),
    "d": ("Sửa form liên hệ", tomorrow(16), tomorrow(17), "Approved"),
    "m1": ("Gửi báo giá", NOW - timedelta(hours=3), NOW - timedelta(hours=2), "Missed"),
    "m2": (
        "Viết email khách",
        NOW - timedelta(minutes=100),
        NOW - timedelta(minutes=70),
        "Approved",
    ),
}
TASK = {k: uuid4() for k in SLOTS}
SLOT = {k: uuid4() for k in SLOTS}
MEETING = uuid4()

TASKS = '"ProjectId" IN (:project, :personal)'
CLEANUP = [
    ("AIUsageLog", '"UserSubscriptionId" = :subscription'),
    ("ScheduleSlot", f'"TaskId" IN (SELECT "Id" FROM "Task" WHERE {TASKS})'),
    ("Task", TASKS),
    ("Project", '"Id" IN (:project, :personal)'),
    ("Client", '"Id" = :client'),
    ("UserSubscription", '"Id" = :subscription'),
    ("User", '"Id" = :user'),
]
PARAMS = dict(IDS)
results: list[tuple[str, bool, str]] = []
timings: dict[str, float] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


async def setup(conn) -> None:
    await conn.execute(
        text("""INSERT INTO "User" ("Id", "RoleId", "Email", "FullName", "GoogleSubjectId",
            "TimeZone", "WorkDayStart", "WorkDayEnd", "WorksOnWeekend", "DefaultBufferMinutes")
        VALUES (:user, :role, :email, 'FR22d Try User', :google, 'Asia/Ho_Chi_Minh',
            '08:00', '20:00', true, 10)"""),
        {
            "user": IDS["user"],
            "role": ROLE_FREELANCER,
            "email": f"fr22d-try-{RUN}@example.invalid",
            "google": f"fr22d-try-{RUN}",
        },
    )
    await conn.execute(
        text("""INSERT INTO "UserSubscription" ("Id", "UserId", "PlanId", "StartAt", "Status")
        VALUES (:id, :user, :plan, now() - interval '1 day', 'Active')"""),
        {"id": IDS["subscription"], "user": IDS["user"], "plan": PLAN_PREMIUM},
    )
    await conn.execute(
        text(
            """INSERT INTO "Client" ("Id", "UserId", "Name") VALUES (:id, :user, 'Lam Sơn')"""
        ),
        {"id": IDS["client"], "user": IDS["user"]},
    )
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "ClientId", "Name")
        VALUES (:id, :user, :client, 'Web Lam Sơn')"""),
        {"id": IDS["project"], "user": IDS["user"], "client": IDS["client"]},
    )
    await conn.execute(
        text("""INSERT INTO "Project" ("Id", "UserId", "Name", "IsDefault")
        VALUES (:id, :user, 'Cá nhân', true)"""),
        {"id": IDS["personal"], "user": IDS["user"]},
    )
    for key, (title, start, end, status) in SLOTS.items():
        await conn.execute(
            text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
                "Status", "Deadline", "EstimatedMinutes")
            VALUES (:id, :project, :domain, :title, 'Flexible', 'Scheduled', :deadline, 60)"""),
            {
                "id": TASK[key],
                "project": IDS["project"],
                "domain": DOMAIN_CODING,
                "title": title,
                "deadline": NOW + timedelta(days=5),
            },
        )
        await conn.execute(
            text("""INSERT INTO "ScheduleSlot" ("Id", "TaskId", "StartTime", "EndTime",
                "ProposedMinutes", "Status")
            VALUES (:id, :task, :start, :end, :minutes, :status)"""),
            {
                "id": SLOT[key],
                "task": TASK[key],
                "start": start,
                "end": end,
                "minutes": int((end - start).total_seconds() // 60),
                "status": status,
            },
        )
    await conn.execute(
        text("""INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType",
            "Status", "FixedStart", "FixedEnd")
        VALUES (:id, :project, :domain, 'Họp khách Lam Sơn', 'Fixed', 'Scheduled', :s, :e)"""),
        {
            "id": MEETING,
            "project": IDS["project"],
            "domain": DOMAIN_CODING,
            "s": tomorrow(15),
            "e": tomorrow(15, 30),
        },
    )


def make_token() -> str:
    s = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(IDS["user"]),
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "iat": now,
        "exp": now + timedelta(minutes=30),
    }
    return jwt.encode(claims, s.jwt_secret.get_secret_value(), algorithm="HS256")


def local(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(ZONE).strftime("%d/%m %H:%M")


async def slot_times(engine) -> list[tuple]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(f"""SELECT "Id", "StartTime", "EndTime", "Status" FROM "ScheduleSlot"
            WHERE "TaskId" IN (SELECT "Id" FROM "Task" WHERE {TASKS}) ORDER BY "Id" """),
            PARAMS,
        )
        return [tuple(r) for r in rows]


async def run_checks(engine, c: httpx.AsyncClient) -> None:
    async def call(label: str, url: str, body: dict) -> httpx.Response:
        started = time.perf_counter()
        r = await c.post(url, json=body)
        timings[label] = time.perf_counter() - started
        print(f"  -> POST {url} {r.status_code} ({timings[label]:.2f}s)")
        if r.status_code >= 400:
            print(f"     {r.text[:300]}")
        return r

    before = await slot_times(engine)
    by_id = {str(v): k for k, v in SLOT.items()}

    print("\n=== M3: việc gấp ngày mai 10:00–11:00 (đè 'Đọc tài liệu API') ===")
    r = await call(
        "insert_free",
        "/schedule/insert-block",
        {
            "title": "Sửa lỗi gấp",
            "start": tomorrow(10).isoformat(),
            "end": tomorrow(11).isoformat(),
        },
    )
    body = r.json()
    for m in body["moves"]:
        print(
            f"    {m['task_title']}: {local(m['old_start'])} → {local(m['new_start'])}"
        )
    print(f"    {body['summary']}")
    moved = [by_id[m["slot_id"]] for m in body["moves"]]
    check("insert 200, không trùng", r.status_code == 200 and body["conflicts"] == [])
    check("chỉ dời việc bị đè (a)", moved == ["a"], str(moved))
    check(
        "a qua giờ nghỉ trưa → 13:00",
        bool(body["moves"]) and local(body["moves"][0]["new_start"]).endswith("13:00"),
    )
    check("b (11:10, sau 10' nghỉ), c, d giữ nguyên", not {"b", "c", "d"} & set(moved))
    check("gợi ý dự án mặc định", body["project_id"] == str(IDS["personal"]))
    check("gợi ý loại việc", body["domain_id"] == DOMAIN_CODING)

    print("\n=== M4: việc gấp ngày mai 14:00–16:00 (trùng họp khách 15:00) ===")
    r = await call(
        "insert_conflict",
        "/schedule/insert-block",
        {
            "title": "Sửa lỗi gấp",
            "start": tomorrow(14).isoformat(),
            "end": tomorrow(16).isoformat(),
        },
    )
    body = r.json()
    print(f"    {body['summary']}")
    print(
        f"    options={body['options']} recommendation={body['recommendation']} other_time={body['other_time']}"
    )
    conflict = body["conflicts"][0] if body["conflicts"] else {}
    check(
        "báo trùng họp khách, quan trọng (có khách)",
        (conflict.get("id"), conflict.get("important"), conflict.get("why"))
        == (str(MEETING), True, "client"),
    )
    check(
        "gợi ý giờ khác 08:00–10:00",
        body["recommendation"] == "other_time"
        and local(body["other_time"]["start"]).endswith("08:00"),
    )
    check("ghi đè → skip đúng task họp", body["skip_task_ids"] == [str(MEETING)])
    check(
        "không dời task Fixed",
        str(MEETING) not in [m["slot_id"] for m in body["moves"]],
    )

    print("\n=== M7: để mai — 'Thiết kế banner' ===")
    r = await call("postpone", "/schedule/postpone", {"slot_id": str(SLOT["c"])})
    body = r.json()
    print(f"    {body['summary']}")
    check(
        "sang 08:00 ngày kế tiếp",
        r.status_code == 200
        and body["move"] is not None
        and local(body["move"]["new_start"])
        == (TOMORROW + timedelta(days=1)).strftime("%d/%m") + " 08:00",
    )
    r = await call(
        "postpone_missed", "/schedule/postpone", {"slot_id": str(SLOT["m1"])}
    )
    check("slot Missed không dời bằng postpone (409)", r.status_code == 409)

    print("\n=== M10: xếp lại việc bị lỡ ===")
    r = await call("catch_up", "/schedule/catch-up", {})
    body = r.json()
    for m in body["moves"]:
        print(
            f"    {m['task_title']}: {local(m['old_start'])} → {local(m['new_start'])}"
        )
    print(f"    {body['summary']}")
    check(
        "m1 rồi m2, đúng thứ tự, từ bây giờ",
        [by_id[m["slot_id"]] for m in body["moves"]] == ["m1", "m2"]
        and all(datetime.fromisoformat(m["new_start"]) >= NOW for m in body["moves"]),
    )
    check("có task_id để Mobile ghi", all(m["task_id"] for m in body["moves"]))

    print("\n=== Trợ lý giọng nói (Gemini chọn công cụ) ===")
    turns = [
        (
            "Mai 2 giờ chiều đến 4 giờ tôi có việc gấp sửa lỗi cho khách",
            "insert_block",
            "confirm",
        ),
        ("Dời việc thiết kế banner sang mai giúp tôi", "postpone_task", "confirm"),
        ("Xếp lại mấy việc bị lỡ hôm nay giúp tôi", "catch_up", "confirm"),
        ("Tôi có việc gấp", "insert_block", "answer"),
    ]
    for i, (sentence, tool, kind) in enumerate(turns, 1):
        r = await call(f"turn{i}", "/assistant/turn", {"text": sentence})
        body = r.json() if r.status_code == 200 else {}
        print(f'    "{sentence}"')
        print(f"      → {body.get('tool')} / {body.get('kind')}: {body.get('reply')}")
        check(
            f"câu {i}: {tool} / {kind}",
            (body.get("tool"), body.get("kind")) == (tool, kind),
        )
        if i == 1 and body.get("action"):
            preview = body["action"]["preview"]
            check(
                "câu 1: khung 14:00–16:00 ngày mai, báo trùng họp",
                local(preview["block"]["start"]) == tomorrow(14).strftime("%d/%m %H:%M")
                and local(preview["block"]["end"]).endswith("16:00")
                and len(preview["conflicts"]) == 1,
            )
        if i == 2 and body.get("action"):
            check(
                "câu 2: đúng slot thiết kế banner",
                body["action"]["slot_id"] == str(SLOT["c"]),
            )

    after = await slot_times(engine)
    check("AC6: DB không đổi sau mọi lần gọi", before == after)
    async with engine.connect() as conn:
        used = (
            await conn.execute(
                text(
                    'SELECT "AiCallsUsed" FROM "UserSubscription" WHERE "Id" = :subscription'
                ),
                PARAMS,
            )
        ).scalar_one()
    check("chỉ trợ lý tốn lượt AI (≤ 4)", used <= 4, f"(AiCallsUsed = {used})")


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    app.state.session_factory = create_session_factory(engine)
    try:
        async with engine.begin() as conn:
            await setup(conn)
        print(f"Đã tạo dữ liệu thử: user {IDS['user']} (bây giờ {NOW:%d/%m %H:%M})")
        transport = httpx.ASGITransport(app=app)  # no lifespan → no startup scan
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://ai",
            headers={"Authorization": f"Bearer {make_token()}"},
            timeout=60,
        ) as client:
            await run_checks(engine, client)
    finally:
        async with engine.begin() as conn:
            for table, where in CLEANUP:
                await conn.execute(text(f'DELETE FROM "{table}" WHERE {where}'), PARAMS)
        async with engine.connect() as conn:
            left = 0
            for table, where in CLEANUP:
                left += (
                    await conn.execute(
                        text(f'SELECT count(*) FROM "{table}" WHERE {where}'), PARAMS
                    )
                ).scalar_one()
        print(f"\nDọn dẹp xong: còn {left} dòng thử")
        await engine.dispose()

    passed = sum(ok for _, ok, _ in results)
    print(f"\n=== {passed}/{len(results)} PASS ===")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name} {detail}")
    print("Thời gian (s):", {k: round(v, 2) for k, v in timings.items()})


if __name__ == "__main__":
    asyncio.run(main())
