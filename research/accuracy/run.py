"""Print the Problem #6 numbers for one user, read-only (plans/accuracy-metrics.md).

Usage: uv run python -m research.accuracy.run --user <user_id>

Only SELECTs, every one filtered by that user (CLAUDE.md §8). Prints no email,
name or task title. Never prints DATABASE_URL.
"""

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import text

from metanoia_ai.config import get_settings
from metanoia_ai.infrastructure.db.engine import create_engine
from research.accuracy.metrics import (
    FixedTask,
    LearnedTask,
    ProposedSlot,
    approval_summary,
    estimate_accuracy,
    validity,
)

APPROVED = {"Approved", "PartlyApproved"}

LEARNED = text("""
    SELECT l."EstimatedMinutes", l."ActualMinutes", l."OldKBias"
    FROM "KBiasUpdateLog" l
    JOIN "UserDomainProfile" p ON p."Id" = l."UserDomainProfileId"
    WHERE p."UserId" = :user
""")
PROPOSALS = text("""
    SELECT "Id", "Status" FROM "ScheduleProposal" WHERE "UserId" = :user
""")
SLOTS = text("""
    SELECT s."ScheduleProposalId", sp."CreatedAt", s."Status", s."StartTime", s."EndTime",
           t."Deadline",
           (s."IsManualOverride" OR s."OriginalStartTime" IS NOT NULL OR EXISTS (
               SELECT 1 FROM "ScheduleChangeLog" c
               WHERE c."ScheduleSlotId" = s."Id"
                 AND c."Source" = 'Manual' AND c."ChangeType" <> 'Created'
           )) AS edited
    FROM "ScheduleSlot" s
    JOIN "ScheduleProposal" sp ON sp."Id" = s."ScheduleProposalId"
    JOIN "Task" t ON t."Id" = s."TaskId"
    WHERE sp."UserId" = :user
""")
FIXED = text("""
    SELECT t."FixedStart", t."FixedEnd", t."CreatedAt"
    FROM "Task" t JOIN "Project" p ON p."Id" = t."ProjectId"
    WHERE p."UserId" = :user AND t."TaskType" = 'Fixed'
      AND t."FixedStart" IS NOT NULL AND t."FixedEnd" IS NOT NULL
""")


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def few(flag: bool) -> str:
    return "  ⚠️ chưa đủ dữ liệu" if flag else ""


async def main(user: UUID) -> None:
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as conn:

            async def rows(query):
                return (await conn.execute(query, {"user": user})).all()

            learned = [
                LearnedTask(int(e), int(a), float(k)) for e, a, k in await rows(LEARNED)
            ]
            proposals = await rows(PROPOSALS)
            slots = [
                ProposedSlot(
                    str(p), created, status, start, end, deadline, bool(edited)
                )
                for p, created, status, start, end, deadline, edited in await rows(
                    SLOTS
                )
            ]
            fixed = [FixedTask(s, e, c) for s, e, c in await rows(FIXED)]
    finally:
        await engine.dispose()

    m1 = estimate_accuracy(learned)
    print(f"M1 — Sai số ước lượng ({m1.samples} task đã học){few(m1.too_few)}")
    print(f"  Người dùng tự đoán: sai trung vị {percent(m1.median_user_error)}")
    print(f"  Đã chỉnh bằng K:    sai trung vị {percent(m1.median_ai_error)}")
    print(f"  K sát hơn ở {percent(m1.ai_closer_share)} số task")

    approved = {str(i) for i, status in proposals if status in APPROVED}
    m2 = approval_summary([status for _, status in proposals], slots, approved)
    print(f"\nM2 — Duyệt lịch{few(m2.too_few)}")
    print(f"  Đề xuất theo trạng thái: {m2.proposals_by_status}")
    print(
        f"  Slot đã duyệt: {m2.approved_slots}, không bị sửa: {m2.unedited_slots}"
        f" ({percent(m2.unedited_share)})"
    )

    m3 = validity(slots, fixed)
    print(f"\nM3 — Lịch hợp lệ ({m3.checked_slots} slot AI đề xuất, chưa bị sửa)")
    print(f"  Trùng nhau trong cùng đề xuất: {m3.overlapping_slots}")
    print(f"  Trùng task Fixed đã có lúc đề xuất: {m3.fixed_overlaps}")
    print(
        f"  Kết thúc sau deadline (FR-10 cho phép, kèm cảnh báo): {m3.after_deadline}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=UUID, required=True)
    asyncio.run(main(parser.parse_args().user))
