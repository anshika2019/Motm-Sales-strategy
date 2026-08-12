import asyncpg
from fastapi import Depends, HTTPException, status

from app.db.session import get_db_connection
from app.dependencies.auth import CurrentUser, get_current_user
from app.models.schemas import AppRole


async def fetch_active_roles(conn: asyncpg.Connection, user_id) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT role::text AS role
        FROM public.user_roles
        WHERE user_id = $1 AND revoked_at IS NULL
        ORDER BY role
        """,
        user_id,
    )
    return [row["role"] for row in rows]


def require_role(*roles: AppRole):
    """Dependency factory: 401s if the token is missing/invalid (via the
    nested get_current_user dependency), 403s if the caller holds none of
    the given roles, otherwise returns the CurrentUser."""

    allowed = {role.value for role in roles}

    async def dependency(
        current_user: CurrentUser = Depends(get_current_user),
        conn: asyncpg.Connection = Depends(get_db_connection),
    ) -> CurrentUser:
        active_roles = await fetch_active_roles(conn, current_user.id)
        if allowed.isdisjoint(active_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return dependency


require_admin = require_role(AppRole.admin)
