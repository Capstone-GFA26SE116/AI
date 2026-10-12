"""Connection to the shared Supabase Postgres database."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from metanoia_ai.config import Settings

ASYNC_SCHEME = "postgresql+asyncpg://"
PLAIN_SCHEMES = ("postgresql://", "postgres://")


def to_async_url(url: str) -> str:
    """Make SQLAlchemy use the asyncpg driver for a normal Postgres URL."""
    if url.startswith(ASYNC_SCHEME):
        return url
    for scheme in PLAIN_SCHEMES:
        if url.startswith(scheme):
            return ASYNC_SCHEME + url.removeprefix(scheme)
    # Never put the URL in the message: it contains the password.
    raise ValueError("DATABASE_URL must start with postgresql:// or postgres://")


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the connection pool. No connection is opened until the first query."""
    url = to_async_url(settings.database_url.get_secret_value())
    # pool_pre_ping: drop connections the pooler has closed, instead of failing.
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Factory for DB sessions (one session per request)."""
    return async_sessionmaker(engine, expire_on_commit=False)
