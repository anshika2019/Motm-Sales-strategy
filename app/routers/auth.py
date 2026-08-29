import httpx2
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Profile, UserRole
from app.db.session import get_db_session
from app.dependencies.auth import CurrentUser, get_current_user
from app.dependencies.roles import fetch_active_roles
from app.models.schemas import AppRole, LoginRequest, LoginResponse, MeResponse, SignupRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# POST /auth/signup lets a brand-new account grant itself a role with no
# admin involved -- everywhere else (AssignRoleRequest / POST
# /admin/users/{id}/roles) a role grant requires require_admin. This set is
# the entire exception: only these two non-privileged workspace roles can
# ever be self-granted at signup. admin/sales_manager/knowledge_manager
# stay admin-grant-only, enforced by rejecting any other value below rather
# than trusting the request body's role field directly.
SIGNUP_ALLOWED_ROLES = {AppRole.motm_bd, AppRole.motm_sales_engineer}


async def _password_grant(email: str, password: str) -> LoginResponse:
    """Shared by /login and /signup -- proxies Supabase's GoTrue password
    grant to exchange credentials for a session (access_token/refresh_token),
    so a freshly-created signup account is immediately logged in rather than
    requiring a second round trip through /login."""
    async with httpx2.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{settings.supabase_url}/auth/v1/token?grant_type=password",
                headers={
                    "apikey": settings.supabase_service_role_key.get_secret_value(),
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password},
                timeout=10.0,
            )
        except httpx2.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach the auth provider",
            )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    data = resp.json()
    return LoginResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data["expires_in"],
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    """Email/password login, proxied to Supabase's GoTrue password grant.

    Google OAuth remains the real sign-in path for production use; this
    exists for easy curl-based testing without a browser redirect, and as
    the login call the frontend's password-based flow (see /signup) uses.
    """
    return await _password_grant(body.email, body.password)


@router.post("/signup", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest, session: AsyncSession = Depends(get_db_session)
) -> LoginResponse:
    """Self-service account creation: a new employee supplies their name,
    email/password, and picks BD or SE as their workspace -- see
    SignupRequest's docstring and SIGNUP_ALLOWED_ROLES above for why this is
    a narrow, deliberate exception to "role grants require an admin"
    elsewhere in the app. Flow: create the auth.users row via Supabase's
    Admin API (this fires the DB's on_auth_user_created trigger, which
    creates the matching profiles row synchronously -- see
    supabase/migrations/0001_auth_and_roles.sql) -> grant the requested role
    -> log the new account in immediately via the same password-grant /login
    uses, so the frontend gets a usable session in one round trip.
    """
    if body.role not in SIGNUP_ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"role must be one of: {', '.join(r.value for r in SIGNUP_ALLOWED_ROLES)}"
            ),
        )

    async with httpx2.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{settings.supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": settings.supabase_service_role_key.get_secret_value(),
                    "Authorization": f"Bearer {settings.supabase_service_role_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={
                    "email": body.email,
                    "password": body.password,
                    "email_confirm": True,
                    "user_metadata": {"full_name": body.full_name},
                },
                timeout=10.0,
            )
        except httpx2.RequestError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach the auth provider",
            )

    if resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY or resp.status_code == 400:
        # GoTrue's admin-create-user returns one of these for "email already
        # registered" / weak password / malformed email -- surface as a
        # client error rather than the generic 503/500 below.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=resp.json().get("msg", "Could not create account with the given details"),
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not create account",
        )

    new_user_id = resp.json()["id"]

    # granted_by=None distinguishes a self-granted signup role from an
    # admin's grant (which always has granted_by set) in the audit trail --
    # see UserRole.granted_by's nullable FK.
    grant = UserRole(user_id=new_user_id, role=body.role, granted_by=None)
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError:
        # Practically unreachable for a just-created user, but stay safe
        # rather than 500 if it somehow happens.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role already granted for this account",
        )

    return await _password_grant(body.email, body.password)


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    profile = await session.get(Profile, current_user.id)
    if profile is None:
        # Should not happen given the synchronous on-signup trigger, but
        # don't crash on it — surfaces as a clear error rather than a 500.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile found for the authenticated user",
        )

    roles = await fetch_active_roles(session, current_user.id)

    return MeResponse(
        id=profile.id,
        email=profile.email,
        full_name=profile.full_name,
        created_at=profile.created_at,
        roles=roles,
    )
