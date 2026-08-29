# MOTM AI Sales Director — Auth Service

This is the auth/roles slice of the MOTM AI Sales Director internal tool.
It handles sign-in (via Supabase Auth) and role management only —
no frontend, no sales-reasoning logic. That comes later.

## How it works

- Employees sign in through **Supabase Auth**, either via Google OAuth (the
  intended production path) or email/password via `POST /auth/login` (a
  thin proxy to Supabase, mainly for easy curl-based testing without a
  browser redirect — see section 6). There is no separate signup flow either
  way — the first successful sign-in creates an `auth.users` row, and a
  Postgres trigger auto-creates a matching `public.profiles` row with **no
  roles assigned**. Email/password test users are created directly in the
  Supabase dashboard (no `POST /auth/signup` exists — see section 6).
- Roles (`admin`, `sales_manager`, `motm_bd`, `motm_sales_engineer`,
  `knowledge_manager`) are never self-assigned. Only an existing admin can
  grant/revoke them via the API. A user can hold multiple roles.
- The FastAPI backend verifies the Supabase-issued JWT on every request and
  looks up roles itself — it does **not** rely on Postgres Row Level Security
  for API authorization (see "Why RLS isn't the enforcement layer" below).
- Database access is **SQLAlchemy (async ORM)**, schema changes are
  **Alembic** migrations (`migrations/versions/`). `supabase/migrations/0001_auth_and_roles.sql`
  is kept only as a historical record of how the live schema was originally
  created (via a hand-rolled script, before this project used Alembic) — it
  is not re-run and should not be edited; all *new* schema changes go
  through Alembic from here on (see section 2).

## 1. Prerequisites

- Python 3.11+
- A Supabase project (Postgres + Auth)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # then fill in the values (see section 3)
```

## 2. Database migrations (Alembic)

Schema changes are managed with Alembic, wired to this app's own SQLAlchemy
engine (`migrations/env.py` imports `app.db.session.engine` — there's no
`sqlalchemy.url` in `alembic.ini`, so the DB URL only ever lives in `.env`).

**On a fresh database:**
```bash
alembic upgrade head
```
This creates the `app_role` enum, `profiles`/`user_roles` tables, RLS
policies, and the `on_auth_user_created` trigger — the same schema that was
originally created by hand (see `supabase/migrations/0001_auth_and_roles.sql`,
kept only as a historical record) — now expressed as a proper Alembic
baseline revision (`migrations/versions/15264e15fc09_baseline_auth_schema.py`).

**On the existing project database**, that schema already exists — the
baseline revision has already been recorded as applied via
`alembic stamp head`, so don't run `alembic upgrade head` against it (it
would try to `CREATE TABLE` things that already exist and fail). Just confirm
you're in sync:
```bash
alembic current   # should show 15264e15fc09 (head)
alembic check     # should print "No new upgrade operations detected."
```

**Making future schema changes:** edit `app/db/models.py`, then:
```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```
Autogenerate handles plain tables/columns/indexes well. RLS policies,
`is_admin()`, and `handle_new_user()` aren't expressible via the ORM and need
hand-written `op.execute(...)` SQL in the migration, same as the baseline
does — autogenerate won't touch those on its own.

Note: `migrations/env.py` scopes comparison to the `public` schema only
(`include_object` in that file) — Supabase's project database also has
`auth`, `storage`, `realtime`, `vault`, etc. schemas full of tables this app
doesn't own; without that filter, autogenerate would try to diff against all
of them.

## 3. Supabase dashboard configuration

**Enable Google as a sign-in provider**
1. Authentication → Providers → Google → enable it.
2. You'll need a Google Cloud OAuth 2.0 Client ID/Secret (Google Cloud Console
   → APIs & Services → Credentials). Add this as an **Authorized redirect
   URI** on that Google OAuth client:
   `https://<project-ref>.supabase.co/auth/v1/callback`
3. Paste the Google Client ID/Secret into the Supabase provider settings.
4. Since this is an internal-only tool, consider setting the Google OAuth
   consent screen's **User type** to "Internal" (Google Workspace orgs) or
   otherwise restricting which Google accounts can complete the OAuth flow —
   Supabase itself won't restrict *who* can sign in, only what they can *do*
   once signed in (governed by the role system here).

**Configure redirect URLs**
Authentication → URL Configuration → set your **Site URL** and add any
frontend callback URLs you'll use once the frontend exists, under
**Redirect URLs**.

**Find your env var values** (all under Project Settings):
- `SUPABASE_URL` — API → Project URL. No separate JWT secret is needed —
  this backend verifies tokens against Supabase's public JWKS endpoint
  (`SUPABASE_URL/auth/v1/.well-known/jwks.json`), which works for projects
  using the newer asymmetric signing keys (ES256/RS256 — this project uses
  ES256). If your Supabase project is still on the legacy HS256 shared
  secret instead, JWKS verification won't work for it, since a shared
  secret is never published via JWKS — that setup would need the old
  shared-secret verification path added back.
- `SUPABASE_SERVICE_ROLE_KEY` — API → Project API keys → `service_role`.
  Used server-side by `POST /auth/login` to call Supabase's auth API, and
  reserved for future Supabase Admin API use. **Never** expose this key to
  a client — it's only ever sent from this backend to Supabase, never
  returned in any response.
- `DATABASE_URL` — Database → Connection string → URI. Use the **direct**
  connection (port `5432`), not the pooler (port `6543`) — asyncpg's prepared
  statements can misbehave against Supabase's transaction-mode pooler. Use
  the `postgres` role (not `anon`/`authenticated`) and append
  `?sslmode=require`.

## 4. Bootstrap the first admin

Every role-granting endpoint requires an existing admin, but a brand-new
system has none — so the very first admin has to be created by hand:

1. Sign in once via Google (through any client that can complete the OAuth
   flow — see the token snippet in section 6). This creates your `profiles`
   row with no roles.
2. Find your user id: in the Supabase dashboard, Authentication → Users, or
   run `select id, email from public.profiles;` in the SQL Editor.
3. In the SQL Editor, run:
   ```sql
   insert into public.user_roles (user_id, role, granted_by)
   values ('<your-user-id>', 'admin', '<your-user-id>');
   ```
4. From here on, use the API (`POST /admin/users/{user_id}/roles`) to grant
   further roles to yourself or others.

## 5. Run the server

```bash
uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for interactive API docs.

## 6. Getting a bearer token to test with

**Easiest path — email/password via `POST /auth/login`:**

1. Create a test user in the Supabase dashboard: Authentication → Users →
   Add User. Set an email + password, and check **"Auto Confirm User"** so
   there's no email-verification step in the way.
2. Get a token with plain curl, no browser needed:
   ```bash
   curl -s -X POST http://localhost:8000/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email":"you@example.com","password":"your-password"}'
   ```
   This returns `{access_token, refresh_token, token_type, expires_in}`.
   There's no `POST /auth/signup` — this endpoint only logs in users that
   already exist, created via the dashboard as above.

**Google OAuth** remains the real production sign-in path for actual
employees, but there's no API-only way to test it — it requires a real
browser OAuth redirect. Testing it is deferred until the frontend exists;
`/auth/login` above covers all API testing needs until then.

Use the access token from `/auth/login` as `$TOKEN` below. Tokens expire
(default 1 hour) — get a fresh one if requests start 401ing.

## Why RLS isn't the enforcement layer

`DATABASE_URL` connects directly to Postgres using the `postgres` role, which
bypasses Row Level Security entirely. Authorization for every API request is
enforced in Python (`get_current_user` + `require_role`), not by RLS. The RLS
policies defined in the migration are a backstop for any *other* way into the
data — Supabase's PostgREST API, the SQL editor with a non-superuser role, or
a future frontend querying Supabase directly with a user's own JWT.

## curl examples

```bash
BASE_URL="http://localhost:8000"

# Log in (email/password test user) and grab the token in one go
TOKEN=$(curl -s -X POST "$BASE_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}' \
  | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

# Current user's profile + roles
curl -s "$BASE_URL/auth/me" \
  -H "Authorization: Bearer $TOKEN"

# Admin: list all users with their current roles
curl -s "$BASE_URL/admin/users" \
  -H "Authorization: Bearer $TOKEN"

# Admin: grant a role to a user
curl -s -X POST "$BASE_URL/admin/users/<user-id>/roles" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "sales_manager"}'

# Admin: revoke a role from a user
curl -s -X DELETE "$BASE_URL/admin/users/<user-id>/roles/sales_manager" \
  -H "Authorization: Bearer $TOKEN"
```

`$TOKEN` must belong to a user with the `admin` role for the last three
calls — otherwise they return `403`. A missing/invalid/expired token returns
`401` on any endpoint.
