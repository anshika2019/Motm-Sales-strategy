"""baseline auth schema

Revision ID: 15264e15fc09
Revises:
Create Date: 2026-08-12 23:14:17.182181

This baselines the schema created by the original raw-SQL migration
(supabase/migrations/0001_auth_and_roles.sql, kept as a historical record).
It is NOT run against the live database via `alembic upgrade` -- those
objects already exist there, so this revision is applied with
`alembic stamp 15264e15fc09` instead (records it as applied without
executing anything). It exists so:
  (a) autogenerate has a correct starting point to diff future model changes
      against, and
  (b) a *fresh* database (new environment, CI, etc.) can be built from
      scratch with a plain `alembic upgrade head`.

RLS policies, the is_admin() SECURITY DEFINER function, and the
handle_new_user() trigger aren't expressible via SQLAlchemy's schema DSL, so
they're raw SQL here via op.execute(), same as they are in the original
migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '15264e15fc09'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("create extension if not exists pgcrypto")

    app_role = postgresql.ENUM(
        "admin",
        "sales_manager",
        "motm_bd",
        "motm_sales_engineer",
        "knowledge_manager",
        name="app_role",
    )
    app_role.create(op.get_bind())

    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["id"], ["auth.users.id"], ondelete="CASCADE", name="profiles_id_fkey"
        ),
        schema="public",
    )

    op.create_table(
        "user_roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", app_role, nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["public.profiles.id"],
            ondelete="CASCADE",
            name="user_roles_user_id_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["public.profiles.id"], name="user_roles_granted_by_fkey"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["public.profiles.id"], name="user_roles_revoked_by_fkey"
        ),
        sa.CheckConstraint(
            "(revoked_at is null) = (revoked_by is null)",
            name="revoked_by_requires_revoked_at",
        ),
        schema="public",
    )

    op.create_index(
        "user_roles_active_unique",
        "user_roles",
        ["user_id", "role"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("user_roles_user_id_idx", "user_roles", ["user_id"], schema="public")
    op.create_index(
        "user_roles_active_role_idx",
        "user_roles",
        ["role"],
        schema="public",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.execute(
        """
        create or replace function public.is_admin(uid uuid)
        returns boolean
        language sql
        security definer
        set search_path = public
        stable
        as $$
          select exists (
            select 1
            from public.user_roles
            where user_id = uid
              and role = 'admin'
              and revoked_at is null
          );
        $$
        """
    )
    op.execute("revoke all on function public.is_admin(uuid) from public")
    op.execute("grant execute on function public.is_admin(uuid) to authenticated, service_role")

    op.execute("alter table public.profiles enable row level security")
    op.execute("alter table public.user_roles enable row level security")

    op.execute(
        """
        create policy profiles_select_own_or_admin
          on public.profiles for select
          using (id = auth.uid() or public.is_admin(auth.uid()))
        """
    )
    op.execute(
        """
        create policy profiles_admin_write
          on public.profiles for all
          using (public.is_admin(auth.uid()))
          with check (public.is_admin(auth.uid()))
        """
    )
    op.execute(
        """
        create policy user_roles_select_own_or_admin
          on public.user_roles for select
          using (user_id = auth.uid() or public.is_admin(auth.uid()))
        """
    )
    op.execute(
        """
        create policy user_roles_admin_write
          on public.user_roles for all
          using (public.is_admin(auth.uid()))
          with check (public.is_admin(auth.uid()))
        """
    )

    op.execute(
        """
        create or replace function public.handle_new_user()
        returns trigger
        language plpgsql
        security definer
        set search_path = public
        as $$
        begin
          insert into public.profiles (id, email, full_name)
          values (
            new.id,
            new.email,
            new.raw_user_meta_data ->> 'full_name'
          )
          on conflict (id) do nothing;
          return new;
        end;
        $$
        """
    )
    op.execute(
        """
        create trigger on_auth_user_created
          after insert on auth.users
          for each row execute function public.handle_new_user()
        """
    )


def downgrade() -> None:
    op.execute("drop trigger if exists on_auth_user_created on auth.users")
    op.execute("drop function if exists public.handle_new_user()")

    op.execute("drop policy if exists user_roles_admin_write on public.user_roles")
    op.execute("drop policy if exists user_roles_select_own_or_admin on public.user_roles")
    op.execute("drop policy if exists profiles_admin_write on public.profiles")
    op.execute("drop policy if exists profiles_select_own_or_admin on public.profiles")

    op.execute("drop function if exists public.is_admin(uuid)")

    op.drop_table("user_roles", schema="public")
    op.drop_table("profiles", schema="public")

    postgresql.ENUM(name="app_role").drop(op.get_bind())
