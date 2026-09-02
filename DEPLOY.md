# Deploying to the Hostinger VPS (srv994268)

This VPS already runs ~25 other apps behind one **shared, host-level Caddy**
(`/etc/caddy/Caddyfile`, systemd service `caddy`, owning ports 80/443).
This app does **not** run its own reverse proxy or request its own TLS cert
— it plugs into the existing one, the same way every other app on this box
does: containers publish to `127.0.0.1` only, and a Caddy site file routes
a subdomain to them.

- **backend** — FastAPI, built from the root [Dockerfile](Dockerfile), published on `127.0.0.1:4020`
- **frontend** — Vite build served by nginx, built from [frontend/Dockerfile](frontend/Dockerfile), published on `127.0.0.1:4021`
- **Subdomain**: `motm-sales.b2botix.ai` (routes by path to the two above — see [motm-sales.caddy](motm-sales.caddy))

The database stays on Supabase — nothing to run on the VPS for that.

## 0. Before you start

Add a DNS record for `motm-sales.b2botix.ai` pointing at this VPS's IP
(wherever `b2botix.ai`'s other records are managed — check Hostinger's DNS
Manager, or wherever the other `*.b2botix.ai` subdomains are set up).
Caddy needs it resolving before it can issue a cert for it.

## 1. Get the code onto the server

```bash
cd ~
git clone <your-repo-url> motm-sales-strategy
cd motm-sales-strategy
```

(Docker is already installed on this box — confirmed via `docker --version`.)

## 2. Configure `.env`

```bash
cp .env.example .env
nano .env
```

Fill in your real `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`,
`GEMINI_API_KEY`, `OPENAI_API_KEY` (same values as local dev). Leave
`FRONTEND_ORIGIN` and `VITE_API_BASE_URL` as the defaults in `.env.example`
— they already match the subdomain/port plan above.

## 3. Build and start the app containers

```bash
docker compose up -d --build
```

First run takes a few minutes (image build + ~2GB embedding model download,
cached afterwards in a named volume). Watch it come up:

```bash
docker compose logs -f backend
```

Wait for `Application startup complete.`, then sanity-check locally on the
box (this bypasses Caddy, confirms the container itself is fine):

```bash
curl -s http://127.0.0.1:4020/health   # {"status":"ok"}
curl -s http://127.0.0.1:4021/ -o /dev/null -w "%{http_code}\n"   # 200
```

## 4. Wire it into the existing Caddy — additive, isolated

The live Caddyfile already has `import /etc/caddy/sites/*.caddy`, so a new
site is a **new file**, not an edit to the shared config other apps depend on:

```bash
sudo cp motm-sales.caddy /etc/caddy/sites/motm-sales.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Only if `validate` reports no errors:

```bash
sudo systemctl reload caddy
```

`reload` (not `restart`) applies the new config without dropping connections
to any of the other running sites.

## 5. Verify

```bash
curl -s https://motm-sales.b2botix.ai/health   # {"status":"ok"}
```

Then open `https://motm-sales.b2botix.ai` in a browser and sign in. Check
`sudo journalctl -u caddy -n 50` if the cert hasn't issued yet — same ACME
flow as every other `*.b2botix.ai` subdomain already on this box.

## 6. Database migrations

The Supabase database is already at the latest schema for what's currently
in this repo (per the main README, the baseline was `alembic stamp head`ed
against it). **Don't run `alembic upgrade head` automatically on every
deploy** — run it by hand, only when you've actually added a new migration:

```bash
docker compose exec backend alembic current   # sanity check
docker compose exec backend alembic upgrade head
```

## Redeploying after code changes

```bash
git pull
docker compose up -d --build
```

Rebuilds only the images whose source changed; `.env` and the `hf_cache`
volume are untouched. No Caddy changes needed unless you touch
`motm-sales.caddy` itself (then repeat step 4).

## Rolling back

```bash
git checkout <previous-commit-or-tag>
docker compose up -d --build
```

## Removing this app later

```bash
docker compose down          # stops + removes just this app's 2 containers
sudo rm /etc/caddy/sites/motm-sales.caddy
sudo systemctl reload caddy
```

Nothing else on the box is touched by either step.

## Notes / gotchas

- Containers are bound to `127.0.0.1` only, matching the convention already
  used by most other apps on this VPS (see `docker ps` output) — don't add a
  `0.0.0.0` port publish, that would make the backend directly reachable
  from the internet bypassing Caddy/TLS entirely.
- `VITE_API_BASE_URL` is baked into the frontend's JS bundle at **build**
  time, not read at runtime. If you ever move this off the shared
  subdomain-routing setup, you must rebuild the frontend image, not just
  restart it.
- `--workers 1` in the backend Dockerfile is deliberate: the ~2GB embedding
  model loads once per worker process.
- Ports 4020/4021 were picked as free after checking every port already in
  use across this VPS's ~25 running containers (`docker ps`). If you add
  more services later, check `sudo ss -tlnp` before reusing either number.
