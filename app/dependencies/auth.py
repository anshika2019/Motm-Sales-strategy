import asyncio
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import settings

# auto_error=False so a missing header falls through to our own 401 below,
# rather than FastAPI's HTTPBearer default of 403 for a missing credential.
_bearer_scheme = HTTPBearer(auto_error=False)

# Supabase now signs tokens with an asymmetric key (this project uses ES256)
# rather than a shared HS256 secret. PyJWKClient fetches and caches
# Supabase's public keys from its JWKS endpoint, keyed by the token's `kid`
# header, so there's no shared secret to configure at all.
_jwks_client = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


class CurrentUser(BaseModel):
    id: UUID
    email: str


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # get_signing_key_from_jwt does a blocking network fetch on a cache
        # miss (new key / rotation) -- offload to a thread so it never
        # blocks the event loop. Cache hits (the common case) return fast.
        signing_key = await asyncio.to_thread(
            _jwks_client.get_signing_key_from_jwt, credentials.credentials
        )
        payload = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            # Tolerate ordinary clock drift between this machine and
            # Supabase's auth server -- without it, a freshly-issued token
            # can fail exp/iat/nbf validation purely because this machine's
            # clock is a few seconds behind Supabase's at verification time
            # (observed directly: reproducible ImmatureSignatureError on a
            # token issued moments earlier).
            leeway=30,
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Note: Supabase's JWT `role` claim is the Postgres/PostgREST role
    # ("authenticated"), not an application role — app roles only ever come
    # from the user_roles table, looked up separately in dependencies/roles.py.
    sub = payload.get("sub")
    email = payload.get("email")
    if not sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required claims",
        )

    try:
        user_id = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed subject claim",
        )

    return CurrentUser(id=user_id, email=email)
