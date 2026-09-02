import logging
from uuid import UUID

import httpx2
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Profile, UserRole
from app.db.session import get_db_session
from app.dependencies.auth import CurrentUser, get_bearer_token, get_current_user
from app.dependencies.roles import fetch_active_roles
from app.models.schemas import (
    AppRole,
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    UpdateEmailRequest,
    UpdateEmailResponse,
    UpdatePasswordRequest,
    UpdatePasswordResponse,
    UpdateProfileRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# POST /auth/signup lets a brand-new account grant itself a role with no
# admin involved -- everywhere else (AssignRoleRequest / POST
# /admin/users/{id}/roles) a role grant requires require_admin. This set is
# the entire exception: only these two non-privileged workspace roles can
# ever be self-granted at signup. admin/sales_manager/knowledge_manager
# stay admin-grant-only, enforced by rejecting any other value below rather
# than trusting the request body's role field directly.
SIGNUP_ALLOWED_ROLES = {AppRole.motm_bd, AppRole.motm_sales_engineer}


async def _grant(grant_type: str, payload: dict, invalid_detail: str) -> LoginResponse:
    """Shared by /login and /refresh -- proxies one of Supabase's GoTrue
    token grants (password or refresh_token) to exchange credentials for a
    session (access_token/refresh_token)."""
    async with httpx2.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{settings.supabase_url}/auth/v1/token?grant_type={grant_type}",
                headers={
                    "apikey": settings.supabase_service_role_key.get_secret_value(),
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
        except httpx2.RequestError as exc:
            logger.warning("Auth provider request failed for grant_type=%s: %s", grant_type, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach the auth provider",
            )

    if resp.status_code != 200:
        logger.warning(
            "Supabase %s grant rejected with status %s", grant_type, resp.status_code
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=invalid_detail,
        )

    data = resp.json()
    return LoginResponse(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        token_type=data.get("token_type", "bearer"),
        expires_in=data["expires_in"],
    )


async def _password_grant(email: str, password: str) -> LoginResponse:
    """Shared by /login and (formerly) /signup -- see _grant()."""
    return await _grant(
        "password", {"email": email, "password": password}, "Invalid email or password"
    )


def _unverified_sub(access_token: str) -> UUID:
    """Pulls the user id out of a token /login just received directly from
    Supabase over a server-to-server call -- signature verification is
    pointless here (we're not trusting a client-supplied token, we're
    reading back the one we just requested), so this only needs to decode
    the payload, not authenticate it. get_current_user (dependencies/auth.py)
    is what verifies tokens presented by callers on every other route."""
    payload = jwt.decode(access_token, options={"verify_signature": False})
    return UUID(payload["sub"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_db_session)
) -> LoginResponse:
    """Email/password login, proxied to Supabase's GoTrue password grant.

    Google OAuth remains the real sign-in path for production use; this
    exists for easy curl-based testing without a browser redirect, and as
    the login call the frontend's password-based flow uses.

    A self-service signup (POST /auth/signup) starts pending -- valid
    credentials alone aren't enough to log in until an admin approves the
    account (POST /admin/users/{id}/approve), so this checks
    profiles.is_approved after Supabase confirms the password is correct.
    """
    session_tokens = await _password_grant(body.email, body.password)
    user_id = _unverified_sub(session_tokens.access_token)
    profile = await session.get(Profile, user_id)
    if profile is not None and not profile.is_approved:
        logger.warning("Login blocked for unapproved user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending admin approval. You'll be able to log in once it's approved.",
        )
    return session_tokens


@router.post("/refresh", response_model=LoginResponse)
async def refresh(body: RefreshRequest) -> LoginResponse:
    """Exchanges a refresh_token (from a prior /login or /refresh response)
    for a new access_token/refresh_token pair, so the frontend can
    keep a session alive past the access token's short expiry without
    forcing the user to log in again. An expired/revoked refresh_token
    surfaces as 401, same as a bad password on /login -- that's the signal
    the frontend uses to fall back to a real re-login."""
    return await _grant(
        "refresh_token", {"refresh_token": body.refresh_token}, "Invalid or expired refresh token"
    )


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest, session: AsyncSession = Depends(get_db_session)
) -> SignupResponse:
    """Self-service account creation: a new employee supplies their name,
    email/password, and picks BD or SE as their workspace -- see
    SignupRequest's docstring and SIGNUP_ALLOWED_ROLES above for why this is
    a narrow, deliberate exception to "role grants require an admin"
    elsewhere in the app. Flow: create the auth.users row via Supabase's
    Admin API (this fires the DB's on_auth_user_created trigger, which
    creates the matching profiles row synchronously, starting is_approved
    false -- see migrations/versions/a3b4c5d6e7f8_add_profile_approval.py)
    -> grant the requested role -> return a pending-approval message rather
    than logging the account in; POST /auth/login 403s for this account
    until an admin approves it via POST /admin/users/{id}/approve.
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
        except httpx2.RequestError as exc:
            logger.warning("Auth provider request failed for admin user create: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach the auth provider",
            )

    if resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY or resp.status_code == 400:
        # GoTrue's admin-create-user returns one of these for "email already
        # registered" / weak password / malformed email -- surface as a
        # client error rather than the generic 503/500 below.
        logger.warning("Signup rejected by auth provider with status %s", resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=resp.json().get("msg", "Could not create account with the given details"),
        )
    if resp.status_code not in (200, 201):
        logger.warning("Signup user creation failed with status %s", resp.status_code)
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
    except IntegrityError as exc:
        # Practically unreachable for a just-created user, but stay safe
        # rather than 500 if it somehow happens.
        logger.warning("Role grant failed during signup for user_id=%s: %s", new_user_id, exc)
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role already granted for this account",
        )

    return SignupResponse(
        message="Your account has been created and is pending admin approval. "
        "You'll be able to log in once it's approved."
    )


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
        username=profile.username,
        created_at=profile.created_at,
        roles=roles,
    )


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: UpdateProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    """Settings page's Name/Username save -- a direct write to our own
    profiles table (unlike email/password below, nothing here needs
    Supabase involved). Fields are only touched when present in the
    request body -- see UpdateProfileRequest's docstring."""
    profile = await session.get(Profile, current_user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile found for the authenticated user",
        )

    if body.full_name is not None:
        profile.full_name = body.full_name.strip() or None

    if body.username is not None:
        username = body.username.strip() or None
        if username is not None:
            existing = await session.execute(
                select(Profile.id).where(Profile.username == username, Profile.id != current_user.id)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="That username is already taken"
                )
        profile.username = username

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already taken")

    await session.refresh(profile)
    roles = await fetch_active_roles(session, current_user.id)
    return MeResponse(
        id=profile.id,
        email=profile.email,
        full_name=profile.full_name,
        username=profile.username,
        created_at=profile.created_at,
        roles=roles,
    )


async def _proxy_user_update(token: str, payload: dict, action: str) -> None:
    """Shared by update_email/update_password below -- both are Supabase
    GoTrue's PUT /auth/v1/user, authenticated as the CALLER (their own
    bearer token in Authorization, not our service-role key) rather than
    the admin API _grant()/signup() use above. That's what lets Supabase
    apply its own rules for the action -- e.g. an email change goes through
    its double-opt-in confirmation flow instead of applying immediately,
    which is exactly the "don't silently change email" behavior we want
    and would otherwise have to reimplement ourselves."""
    async with httpx2.AsyncClient() as client:
        try:
            resp = await client.put(
                f"{settings.supabase_url}/auth/v1/user",
                headers={
                    "apikey": settings.supabase_service_role_key.get_secret_value(),
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
        except httpx2.RequestError as exc:
            logger.warning("Auth provider request failed for %s: %s", action, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach the auth provider",
            )

    if resp.status_code not in (200, 201):
        logger.warning("Supabase rejected %s with status %s", action, resp.status_code)
        detail = "Could not complete this request"
        try:
            detail = resp.json().get("msg") or resp.json().get("message") or detail
        except ValueError:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST if resp.status_code < 500 else status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )


@router.put("/me/email", response_model=UpdateEmailResponse)
async def update_email(
    body: UpdateEmailRequest,
    token: str = Depends(get_bearer_token),
    current_user: CurrentUser = Depends(get_current_user),
) -> UpdateEmailResponse:
    """Starts Supabase's email-change flow -- does NOT update our own
    profiles.email row. Supabase sends confirmation link(s) and only
    applies the change once confirmed, so profiles.email intentionally
    stays as-is here; it reflects the account's *current* email until the
    user completes that confirmation (there is no webhook/sync back into
    our DB for this yet)."""
    await _proxy_user_update(token, {"email": body.email}, "email update")
    return UpdateEmailResponse(
        message=(
            "A confirmation link has been sent to your new email address. "
            "Your email will update once you confirm it."
        )
    )


@router.put("/me/password", response_model=UpdatePasswordResponse)
async def update_password(
    body: UpdatePasswordRequest,
    token: str = Depends(get_bearer_token),
    current_user: CurrentUser = Depends(get_current_user),
) -> UpdatePasswordResponse:
    """Updates the account's password via Supabase, authenticated by the
    caller's own current session (no separate current-password check --
    Supabase's own PUT /auth/v1/user doesn't require one when called with a
    valid, unexpired access token, which is already proof of an active
    logged-in session)."""
    await _proxy_user_update(token, {"password": body.new_password}, "password update")
    return UpdatePasswordResponse(message="Your password has been updated.")
