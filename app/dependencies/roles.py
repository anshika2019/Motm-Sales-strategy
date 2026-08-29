from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.db.session import get_db_session
from app.dependencies.auth import CurrentUser, get_current_user
from app.models.schemas import AppRole


async def fetch_active_roles(session: AsyncSession, user_id: UUID) -> list[AppRole]:
    result = await session.execute(
        select(UserRole.role)
        .where(UserRole.user_id == user_id, UserRole.revoked_at.is_(None))
        .order_by(UserRole.role)
    )
    return list(result.scalars().all())


def require_role(*roles: AppRole):
    """Dependency factory: 401s if the token is missing/invalid (via the
    nested get_current_user dependency), 403s if the caller holds none of
    the given roles, otherwise returns the CurrentUser."""

    allowed = set(roles)

    async def dependency(
        current_user: CurrentUser = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> CurrentUser:
        active_roles = await fetch_active_roles(session, current_user.id)
        if allowed.isdisjoint(active_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action",
            )
        return current_user

    return dependency


require_admin = require_role(AppRole.admin)
