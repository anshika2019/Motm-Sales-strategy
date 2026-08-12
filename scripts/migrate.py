"""Applies pending .sql files from supabase/migrations/ directly via DATABASE_URL.

Usage: python scripts/migrate.py

Tracks applied migrations in public.schema_migrations so re-running only
picks up files that haven't been applied yet.
"""

import asyncio
import pathlib
import sys

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import settings

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "supabase" / "migrations"


async def main() -> None:
    conn = await asyncpg.connect(dsn=settings.database_url)
    try:
        await conn.execute(
            """
            create table if not exists public.schema_migrations (
                filename text primary key,
                applied_at timestamptz not null default now()
            )
            """
        )

        applied = {
            row["filename"]
            for row in await conn.fetch("select filename from public.schema_migrations")
        }
        pending = sorted(
            path for path in MIGRATIONS_DIR.glob("*.sql") if path.name not in applied
        )

        if not pending:
            print("No pending migrations.")
            return

        for path in pending:
            print(f"Applying {path.name} ...")
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (filename) values ($1)",
                    path.name,
                )
            print(f"  OK - {path.name} applied.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
