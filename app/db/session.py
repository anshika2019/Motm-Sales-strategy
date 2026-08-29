from collections.abc import AsyncIterator

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _async_database_url() -> URL:
    """DATABASE_URL (see .env.example) is a plain postgresql:// URI with
    ?sslmode=require, meant for asyncpg's own connect()/create_pool(), which
    accepts that directly. SQLAlchemy's asyncpg dialect does not: it needs
    the postgresql+asyncpg:// driver prefix, and TLS has to be passed via
    connect_args rather than a sslmode query param.
    """
    url = make_url(settings.database_url)
    url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    query.pop("sslmode", None)
    return url.set(query=query)


# The connection must use a Postgres role that bypasses RLS (Supabase's
# `postgres` role) -- this backend enforces authorization in Python, not via
# RLS, so a lower-privileged role would silently filter query results
# instead of erroring. See README "Why RLS isn't the enforcement layer".
#
# statement_cache_size=0: DATABASE_URL points at Supabase's pgbouncer pooler
# in transaction mode, which does not support asyncpg's default server-side
# prepared statement caching -- the pooler can hand a client's next query to
# a different backend connection mid-session, so a statement name prepared
# against one backend can collide with (or simply not exist on) another,
# surfacing as an intermittent asyncpg.exceptions.DuplicatePreparedStatementError.
# Disabling the client-side cache is asyncpg's documented fix for exactly
# this pooler configuration.
engine = create_async_engine(
    _async_database_url(),
    connect_args={"ssl": "require", "statement_cache_size": 0},
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
