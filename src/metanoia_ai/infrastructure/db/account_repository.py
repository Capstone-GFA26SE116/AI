"""AccountRepository on Postgres: the same rule as the Backend's `IsActiveAsync`."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

IS_ACTIVE = text("""
    SELECT 1 FROM "User"
    WHERE "Id" = :user_id AND "DeletedAt" IS NULL
""")


class SqlAccountRepository:
    """AccountRepository on Postgres. Shares the request's session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_active(self, user_id: UUID) -> bool:
        started_here = not self.session.in_transaction()
        try:
            result = await self.session.execute(IS_ACTIVE, {"user_id": user_id})
            return result.first() is not None
        finally:
            if started_here:
                # Close the read opened by autobegin, so later repositories can begin().
                await self.session.rollback()
