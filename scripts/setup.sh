#!/usr/bin/env bash
# setup.sh — one-command bootstrap: deps → database → config → migrate → run.
#
#   From a fresh clone:   ./scripts/setup.sh          # sets up everything, then launches
#   Set up without running: ./scripts/setup.sh --no-start
#
# Idempotent: safe to re-run. Existing venv / database / .env are reused, not clobbered.
#
# Requires (NOT auto-installed):
#   - Postgres, with a superuser you can reach (default local install = your OS user;
#     on Linux: run via `sudo -u postgres ./scripts/setup.sh` if your user is not a superuser)
#   - Python 3.12+, Node 18+
#
# Overrides (optional): RPG_DB_NAME, RPG_DB_USER, RPG_DB_PASSWORD, PGPORT
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RPG_DIR="$ROOT/rpg"

DB_NAME="${RPG_DB_NAME:-rpg}"
DB_USER="${RPG_DB_USER:-rpg}"
DB_PASS="${RPG_DB_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_hex(8))')}"
PG_PORT="${PGPORT:-5432}"
DB_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:${PG_PORT}/${DB_NAME}"

NO_START=0
[ "${1:-}" = "--no-start" ] && NO_START=1

say() { printf '\033[36m▸ %s\033[0m\n' "$1"; }
ok()  { printf '\033[32m  ✓ %s\033[0m\n' "$1"; }
die() { printf '\033[31m  ✗ %s\033[0m\n' "$1" >&2; exit 1; }

command -v psql    >/dev/null 2>&1 || die "psql not found — install Postgres first"
command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3.12+"

# ── 1. Postgres running? ─────────────────────────────────────────────
say "Postgres on :$PG_PORT"
if ! pg_isready -q -p "$PG_PORT" 2>/dev/null; then
  if command -v brew >/dev/null 2>&1; then
    echo "  · not running — trying: brew services start postgresql"
    brew services start postgresql >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do pg_isready -q -p "$PG_PORT" 2>/dev/null && break; sleep 1; done
  fi
  pg_isready -q -p "$PG_PORT" 2>/dev/null \
    || die "Postgres not running on :$PG_PORT — start it and re-run (brew services start postgresql / sudo systemctl start postgresql)"
fi
ok "Postgres up"

# ── 2. Role + database + extensions (idempotent; needs a superuser) ──
say "Database '$DB_NAME' + role '$DB_USER'"
psql -d postgres -v ON_ERROR_STOP=1 -q <<SQL || die "could not create role/database — your OS user needs Postgres superuser access (make it a superuser, or pre-create the role + database as the postgres user; see README)"
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}')\gexec
SQL
# Extensions must be created by a superuser — pgvector ("vector") is not a trusted extension.
psql -d "$DB_NAME" -v ON_ERROR_STOP=1 -q \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" \
  -c "CREATE EXTENSION IF NOT EXISTS pgcrypto;" \
  || die "could not create extensions in '$DB_NAME' — run as a superuser or pre-create vector/pg_trgm/pgcrypto"
ok "database ready (vector + pg_trgm + pgcrypto)"

# ── 3. Python venv + dependencies ───────────────────────────────────
say "Python venv + dependencies"
[ -d "$RPG_DIR/.venv" ] || python3 -m venv "$RPG_DIR/.venv"
"$RPG_DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$RPG_DIR/requirements.txt"
ok "venv ready"

# ── 4. Config (rpg/.env) ────────────────────────────────────────────
say "Config rpg/.env"
if [ -f "$RPG_DIR/.env" ]; then
  ok "rpg/.env exists — left as-is"
else
  cp "$RPG_DIR/.env.example" "$RPG_DIR/.env"
  sed -i.bak "s|^DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$RPG_DIR/.env" && rm -f "$RPG_DIR/.env.bak"
  ok "created rpg/.env from template"
fi

# ── 5. Migrations (direct PG connection; full = baseline + all migrations + pgvector) ──
say "Database migrations"
( cd "$RPG_DIR" && DATABASE_URL="$DB_URL" .venv/bin/python -m platform_app.migrate full >/dev/null ) \
  || die "migration failed — see: cd rpg && DATABASE_URL=$DB_URL .venv/bin/python -m platform_app.migrate full"
ok "schema up to date"

echo ""
ok "Setup complete."
if [ "$NO_START" = "1" ]; then
  echo "    Next:  ./scripts/dev.sh start"
else
  echo ""
  say "Launching dev servers (backend :7860 + frontend :5173)…"
  exec "$ROOT/scripts/dev.sh" start
fi
