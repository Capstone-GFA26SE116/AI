"""Whether a user's account still exists, checked after the JWT (plans/auth-active-user.md)."""

from typing import Protocol
from uuid import UUID


class AccountRepository(Protocol):
    async def is_active(self, user_id: UUID) -> bool:
        """True when the user exists and has not deleted the account."""
        ...
