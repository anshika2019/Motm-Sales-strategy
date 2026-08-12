from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request

from app.config import settings


async def create_pool() -> asyncpg.Pool:
    """Create the app-wide connection pool.

    Use Supabase's *direct* connection string (port 5432), not the
    Supavisor/PgBouncer transaction-mode pooler (port 6543) — asyncpg's
    server-side prepared statements can intermittently fail against the
    pooler ("prepared statement ... does not exist"). If you must use the
    pooler, pass statement_cache_size=0 below.

    The connection must use a Postgres role that bypasses RLS (Supabase's
    `postgres` role) — this backend enforces authorization in Python, not
    via RLS, so a lower-privileged role would silently filter query results
    instead of erroring.

    Supabase requires TLS. Include `?sslmode=require` in DATABASE_URL rather
    than passing `ssl=` here — asyncpg raises if a DSN-embedded sslmode and
    an explicit `ssl` kwarg both end up specified.
    """
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=10,
    )


async def get_db_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as connection:
        yield connection
