"""Create a new admin account directly, bypassing signup/approval entirely.

For bootstrapping the first admin (or adding another one) without going
through POST /auth/signup + POST /admin/users/{id}/approve -- creates the
Supabase auth user, grants it the admin role, and marks it approved in one
shot.

Usage:
    python -m scripts.create_admin admin@example.com "a-strong-password" "Admin Name"
"""

import argparse
import asyncio

import httpx2

from app.config import settings
from app.db.models import Profile, UserRole
from app.db.session import async_session_factory
from app.models.schemas import AppRole


async def create_admin(email: str, password: str, full_name: str) -> None:
    async with httpx2.AsyncClient() as client:
        resp = await client.post(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers={
                "apikey": settings.supabase_service_role_key.get_secret_value(),
                "Authorization": f"Bearer {settings.supabase_service_role_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name},
            },
            timeout=10.0,
        )

    if resp.status_code not in (200, 201):
        raise SystemExit(
            f"Could not create the auth user (status {resp.status_code}): {resp.text}"
        )

    user_id = resp.json()["id"]

    async with async_session_factory() as session:
        # on_auth_user_created's trigger already inserted the profiles row
        # (is_approved defaults false) synchronously with the call above --
        # flip it to approved and grant admin in one transaction.
        profile = await session.get(Profile, user_id)
        profile.is_approved = True
        session.add(UserRole(user_id=user_id, role=AppRole.admin, granted_by=None))
        await session.commit()

    print(f"Created admin {email} (user_id={user_id}), approved and granted the admin role.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("full_name")
    args = parser.parse_args()
    asyncio.run(create_admin(args.email, args.password, args.full_name))
