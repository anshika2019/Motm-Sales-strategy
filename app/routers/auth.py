import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.db.session import get_db_connection
from app.dependencies.auth import CurrentUser, get_current_user
from app.dependencies.roles import fetch_active_roles
from app.models.schemas import MeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db_connection),
) -> MeResponse:
    profile = await conn.fetchrow(
        "SELECT id, email, full_name, created_at FROM public.profiles WHERE id = $1",
        current_user.id,
    )
    if profile is None:
        # Should not happen given the synchronous on-signup trigger, but
        # don't crash on it — surfaces as a clear error rather than a 500.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile found for the authenticated user",
        )

    roles = await fetch_active_roles(conn, current_user.id)

    return MeResponse(
        id=profile["id"],
        email=profile["email"],
        full_name=profile["full_name"],
        created_at=profile["created_at"],
        roles=roles,
    )
