from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.db.session import get_db_connection
from app.dependencies.auth import CurrentUser
from app.dependencies.roles import require_admin
from app.models.schemas import (
    AppRole,
    AssignRoleRequest,
    RoleGrantResponse,
    RoleRevokeResponse,
    UserWithRoles,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserWithRoles])
async def list_users(
    _admin: CurrentUser = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_db_connection),
) -> list[UserWithRoles]:
    rows = await conn.fetch(
        """
        SELECT
            p.id,
            p.email,
            p.full_name,
            p.created_at,
            COALESCE(
                array_agg(ur.role::text) FILTER (WHERE ur.revoked_at IS NULL),
                ARRAY[]::text[]
            ) AS roles
        FROM public.profiles p
        LEFT JOIN public.user_roles ur ON ur.user_id = p.id
        GROUP BY p.id, p.email, p.full_name, p.created_at
        ORDER BY p.created_at
        """
    )
    return [
        UserWithRoles(
            id=row["id"],
            email=row["email"],
            full_name=row["full_name"],
            created_at=row["created_at"],
            roles=row["roles"],
        )
        for row in rows
    ]


@router.post(
    "/users/{user_id}/roles",
    response_model=RoleGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_role(
    user_id: UUID,
    body: AssignRoleRequest,
    admin: CurrentUser = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_db_connection),
) -> RoleGrantResponse:
    target_exists = await conn.fetchval(
        "SELECT 1 FROM public.profiles WHERE id = $1", user_id
    )
    if not target_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO public.user_roles (user_id, role, granted_by)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, role::text AS role, granted_by, granted_at
            """,
            user_id,
            body.role.value,
            admin.id,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User already has an active '{body.role.value}' role",
        )

    return RoleGrantResponse(**dict(row))


@router.delete("/users/{user_id}/roles/{role}", response_model=RoleRevokeResponse)
async def revoke_role(
    user_id: UUID,
    role: AppRole,
    admin: CurrentUser = Depends(require_admin),
    conn: asyncpg.Connection = Depends(get_db_connection),
) -> RoleRevokeResponse:
    async with conn.transaction():
        if role == AppRole.admin:
            # Row-lock every currently-active admin grant so a concurrent
            # revoke request can't race past this check — it will block on
            # the same FOR UPDATE scan until this transaction commits, then
            # re-read the now-reduced active set.
            active_admin_rows = await conn.fetch(
                """
                SELECT user_id
                FROM public.user_roles
                WHERE role = 'admin' AND revoked_at IS NULL
                FOR UPDATE
                """
            )
            active_admin_ids = {r["user_id"] for r in active_admin_rows}
            if active_admin_ids == {user_id}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot remove the last remaining admin",
                )

        row = await conn.fetchrow(
            """
            UPDATE public.user_roles
            SET revoked_at = now(), revoked_by = $1
            WHERE user_id = $2 AND role = $3 AND revoked_at IS NULL
            RETURNING user_id, role::text AS role, revoked_at, revoked_by
            """,
            admin.id,
            user_id,
            role.value,
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active role grant found for this user/role",
        )

    return RoleRevokeResponse(**dict(row))
