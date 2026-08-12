-- ============================================================================
-- MOTM AI Sales Director — auth & roles schema
-- Run this in the Supabase SQL editor, or via `supabase db push` /
-- `supabase migration up` if you're using the Supabase CLI.
-- ============================================================================

create extension if not exists pgcrypto;

-- ----------------------------------------------------------------------------
-- Enum of application roles. A user can hold zero or more of these — "zero"
-- (no rows in user_roles) is what "pending, not yet assigned" looks like.
-- ----------------------------------------------------------------------------
create type app_role as enum (
  'admin',
  'sales_manager',
  'motm_bd',
  'motm_sales_engineer',
  'knowledge_manager'
);

-- ----------------------------------------------------------------------------
-- profiles: one row per auth.users row. Created automatically (see trigger
-- below) the first time someone signs in via Google OAuth — there is no
-- separate signup flow.
-- ----------------------------------------------------------------------------
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  full_name text,
  created_at timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- user_roles: one row per (user, role) grant, soft-revoked rather than
-- deleted, so there's a durable history of who granted/revoked what and
-- when. Only one *active* (revoked_at is null) row per (user_id, role) can
-- exist at a time; re-granting after a revoke inserts a fresh row.
-- ----------------------------------------------------------------------------
create table public.user_roles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  role app_role not null,
  granted_by uuid references public.profiles (id),
  granted_at timestamptz not null default now(),
  revoked_at timestamptz,
  revoked_by uuid references public.profiles (id),
  constraint revoked_by_requires_revoked_at check (
    (revoked_at is null) = (revoked_by is null)
  )
);

-- Enforce "one active row per (user, role)" without blocking re-grants of a
-- previously-revoked role.
create unique index user_roles_active_unique
  on public.user_roles (user_id, role)
  where revoked_at is null;

create index user_roles_user_id_idx on public.user_roles (user_id);
create index user_roles_active_role_idx on public.user_roles (role) where revoked_at is null;

-- ----------------------------------------------------------------------------
-- is_admin(): SECURITY DEFINER helper so RLS policies below can check "is
-- this caller an admin" without recursing into user_roles' own RLS (a
-- policy on user_roles that queries user_roles directly would re-trigger
-- itself). It runs as the function owner — in Supabase that's a role with
-- BYPASSRLS — so the SELECT inside it bypasses RLS instead of looping.
-- ----------------------------------------------------------------------------
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
$$;

revoke all on function public.is_admin(uuid) from public;
grant execute on function public.is_admin(uuid) to authenticated, service_role;

-- ----------------------------------------------------------------------------
-- Row Level Security
--
-- NOTE: the FastAPI backend in this repo talks to Postgres directly over
-- DATABASE_URL (typically the `postgres` role), which bypasses RLS, and
-- enforces authorization itself via the get_current_user / require_role
-- dependencies. These policies are the second line of defense for any
-- other path into the data (Supabase client libraries, PostgREST, the
-- SQL editor with a non-superuser role, etc).
-- ----------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.user_roles enable row level security;

-- profiles: a user can read their own row; admins can read every row.
create policy profiles_select_own_or_admin
  on public.profiles for select
  using (id = auth.uid() or public.is_admin(auth.uid()));

-- profiles: only admins can write (insert/update/delete) rows. Normal
-- profile creation happens via the trigger below, running as a role that
-- bypasses RLS, so this doesn't block signup.
create policy profiles_admin_write
  on public.profiles for all
  using (public.is_admin(auth.uid()))
  with check (public.is_admin(auth.uid()));

-- user_roles: a user can read their own grants (including revoked history);
-- admins can read everyone's.
create policy user_roles_select_own_or_admin
  on public.user_roles for select
  using (user_id = auth.uid() or public.is_admin(auth.uid()));

-- user_roles: only admins can grant/revoke roles.
create policy user_roles_admin_write
  on public.user_roles for all
  using (public.is_admin(auth.uid()))
  with check (public.is_admin(auth.uid()));

-- ----------------------------------------------------------------------------
-- Auto-create a profiles row (with zero roles) whenever a new auth.users
-- row appears — i.e. the first time someone signs in via Google OAuth.
-- ----------------------------------------------------------------------------
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
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
