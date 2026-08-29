from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import PitchEvaluation, Profile, UserRole
from app.db.session import get_db_session
from app.dependencies.auth import CurrentUser
from app.dependencies.roles import require_admin
from app.models.schemas import (
    AppRole,
    AssignRoleRequest,
    PitchEvaluationSummary,
    RoleGrantResponse,
    RoleRevokeResponse,
    UserWithRoles,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserWithRoles])
async def list_users(
    _admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserWithRoles]:
    result = await session.execute(
        select(Profile).options(selectinload(Profile.roles)).order_by(Profile.created_at)
    )
    profiles = result.scalars().all()

    return [
        UserWithRoles(
            id=p.id,
            email=p.email,
            full_name=p.full_name,
            created_at=p.created_at,
            roles=sorted(ur.role for ur in p.roles if ur.revoked_at is None),
        )
        for p in profiles
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
    session: AsyncSession = Depends(get_db_session),
) -> RoleGrantResponse:
    target = await session.get(Profile, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    grant = UserRole(user_id=user_id, role=body.role, granted_by=admin.id)
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User already has an active '{body.role.value}' role",
        )

    return RoleGrantResponse(
        id=grant.id,
        user_id=grant.user_id,
        role=grant.role,
        granted_by=grant.granted_by,
        granted_at=grant.granted_at,
    )


@router.delete("/users/{user_id}/roles/{role}", response_model=RoleRevokeResponse)
async def revoke_role(
    user_id: UUID,
    role: AppRole,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> RoleRevokeResponse:
    if role == AppRole.admin:
        # Row-lock every currently-active admin grant so a concurrent revoke
        # request can't race past this check -- it will block on the same
        # FOR UPDATE scan until this transaction commits/rolls back, then
        # re-read the now-reduced active set. SQLAlchemy's AsyncSession
        # auto-begins a transaction on first use, so this SELECT and the
        # UPDATE below share one transaction without an explicit begin().
        result = await session.execute(
            select(UserRole.user_id)
            .where(UserRole.role == AppRole.admin, UserRole.revoked_at.is_(None))
            .with_for_update()
        )
        active_admin_ids = set(result.scalars().all())
        if active_admin_ids == {user_id}:
            await session.rollback()  # release the row lock promptly
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the last remaining admin",
            )

    result = await session.execute(
        update(UserRole)
        .where(
            UserRole.user_id == user_id,
            UserRole.role == role,
            UserRole.revoked_at.is_(None),
        )
        .values(revoked_at=func.now(), revoked_by=admin.id)
        .returning(
            UserRole.user_id, UserRole.role, UserRole.revoked_at, UserRole.revoked_by
        )
    )
    row = result.first()
    await session.commit()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active role grant found for this user/role",
        )

    return RoleRevokeResponse(
        user_id=row.user_id,
        role=row.role,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
    )


@router.get("/pitch-evaluations", response_model=list[PitchEvaluationSummary])
async def list_pitch_evaluations(
    limit: int = 50,
    _admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[PitchEvaluationSummary]:
    """Cross-conversation W2R rubric compliance listing, most recent first
    -- gives a manager a dashboard view of how generated pitches are
    scoring against the framework without opening each conversation
    individually (see PitchEvaluation / evaluate_pitch())."""
    result = await session.execute(
        select(PitchEvaluation).order_by(PitchEvaluation.created_at.desc()).limit(limit)
    )
    evaluations = result.scalars().all()

    return [
        PitchEvaluationSummary(
            id=e.id,
            message_id=e.message_id,
            conversation_id=e.conversation_id,
            output_format=e.output_format,
            overall_score=e.overall_score,
            top_gaps=e.top_gaps,
            created_at=e.created_at,
        )
        for e in evaluations
    ]
