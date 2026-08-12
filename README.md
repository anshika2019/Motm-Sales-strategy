# MOTM AI Sales Director — Auth Service

This is the auth/roles slice of the MOTM AI Sales Director internal tool.
It handles Google-OAuth sign-in (via Supabase Auth) and role management only —
no frontend, no sales-reasoning logic. That comes later.

## How it works

- Employees sign in with Google through **Supabase Auth**. There is no
  separate signup flow — the first successful Google sign-in creates an
  `auth.users` row, and a Postgres trigger auto-creates a matching
  `public.profiles` row with **no roles assigned**.
- Roles (`admin`, `sales_manager`, `motm_bd`, `motm_sales_engineer`,
  `knowledge_manager`) are never self-assigned. Only an existing admin can
  grant/revoke them via the API. A user can hold multiple roles.
- The FastAPI backend verifies the Supabase-issued JWT on every request and
  looks up roles itself — it does **not** rely on Postgres Row Level Security
  for API authorization (see "Why RLS isn't the enforcement layer" below).

## 1. Prerequisites

- Python 3.11+
- A Supabase project (Postgres + Auth)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # then fill in the values (see section 3)
```

## 2. Run the database migration

Open the Supabase dashboard → SQL Editor, paste the contents of
`supabase/migrations/0001_auth_and_roles.sql`, and run it. (Or, if you use the
Supabase CLI: `supabase db push`.)

This creates the `app_role` enum, `profiles` and `user_roles` tables, RLS
policies, and the `on_auth_user_created` trigger.

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
- `SUPABASE_URL` — API → Project URL
- `SUPABASE_JWT_SECRET` — API → JWT Settings → JWT Secret (labeled "legacy
  JWT secret" on newer projects that also offer asymmetric signing keys —
  this backend verifies the shared-secret HS256 tokens Supabase issues by
  default)
- `SUPABASE_SERVICE_ROLE_KEY` — API → Project API keys → `service_role`.
  Not used by any endpoint yet; loaded for future Supabase Admin API use.
  **Never** expose this key to a client.
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

Sign-in is Google-OAuth-only, so there's no password/magic-link shortcut for
grabbing a token — you need to complete a real OAuth redirect once. Save this
as a local HTML file (fill in your project URL/anon key from API settings),
open it in a browser, and click the button:

```html
<!doctype html>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<button id="go">Sign in with Google</button>
<script>
  const supabase = window.supabase.createClient(
    "https://<project-ref>.supabase.co",
    "<anon-public-key>" // API settings -> Project API keys -> anon/public
  );
  document.getElementById("go").onclick = () =>
    supabase.auth.signInWithOAuth({ provider: "google" });
  supabase.auth.onAuthStateChange((_event, session) => {
    if (session) console.log("ACCESS TOKEN:", session.access_token);
  });
</script>
```

After signing in, copy the access token logged to the browser console and use
it as `$TOKEN` below. It expires (default 1 hour) — repeat if needed.

## Why RLS isn't the enforcement layer

`DATABASE_URL` connects directly to Postgres using the `postgres` role, which
bypasses Row Level Security entirely. Authorization for every API request is
enforced in Python (`get_current_user` + `require_role`), not by RLS. The RLS
policies defined in the migration are a backstop for any *other* way into the
data — Supabase's PostgREST API, the SQL editor with a non-superuser role, or
a future frontend querying Supabase directly with a user's own JWT.

## curl examples

```bash
TOKEN="<paste access_token from section 6>"
BASE_URL="http://localhost:8000"

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
