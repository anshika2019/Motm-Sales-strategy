from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class AppRole(str, Enum):
    """Kept in sync manually with the `app_role` Postgres enum in
    supabase/migrations/0001_auth_and_roles.sql — there is no automatic
    sync between the two, so update both when a role is added/removed."""

    admin = "admin"
    sales_manager = "sales_manager"
    motm_bd = "motm_bd"
    motm_sales_engineer = "motm_sales_engineer"
    knowledge_manager = "knowledge_manager"


class MeResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    created_at: datetime
    roles: list[AppRole]


class UserWithRoles(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    created_at: datetime
    roles: list[AppRole]


class AssignRoleRequest(BaseModel):
    role: AppRole


class RoleGrantResponse(BaseModel):
    id: UUID
    user_id: UUID
    role: AppRole
    granted_by: UUID | None
    granted_at: datetime


class RoleRevokeResponse(BaseModel):
    user_id: UUID
    role: AppRole
    revoked_at: datetime
    revoked_by: UUID
