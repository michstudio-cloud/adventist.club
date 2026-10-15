#!/usr/bin/env bash
# Builds the whole database schema from nothing, with one command:
#
#   db/base/*.sql          legacy tables that pre-date migrations/001 (in production they come
#                          from the old platform; no numbered migration creates them)
#   migrations/*.sql       001 -> last, in name order, with the known ordering fixes below
#   db/seeds/minimal.sql   reference data every install needs (ministries, honor categories)
#   db/seeds/test_fixtures.sql   (only with --test-fixtures) org tree + honors the test suite expects
#
# For NEW databases only: tests, CI, the event node (nodo-evento/). NEVER production: Neon was
# built by hand over years and its migrations were applied one by one. The script refuses a
# database that already has users but no `schema_migrations` table (= not built by it), and
# any *.neon.tech host.
#
# Usage:
#   backend/scripts/db_bootstrap.sh [--create] [--test-fixtures] [--dry-run] [DATABASE_URL]
#     DATABASE_URL  libpq URL (postgresql://user:pass@host:port/db). Default: $DATABASE_URL.
#     --create      create the database first (UTF8, from template0) if it does not exist.
#     --test-fixtures  also load db/seeds/test_fixtures.sql (backend test suite).
#     --dry-run     print the plan (files in order, applied or pending) and change nothing.
#
# Re-running is safe: applied files are recorded in `schema_migrations` (name + sha256) and
# skipped; a file whose content changed since it was applied is reported and NOT re-applied.
# Requires: bash, psql (PostgreSQL client >= 12), sha256sum or shasum.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"          # backend/backend
BASE_DIR="$HERE/db/base"
MIG_DIR="$HERE/migrations"
SEED_DIR="$HERE/db/seeds"

# A few migrations depend on a LATER file. They are idempotent, but the dependency makes the
# plain name order fail on an empty database. Each entry moves FILE right after ANCHOR.
#   008f_leader_verification.sql indexes church_letters, a table created by 009_church_letters.sql.
ORDER_FIXES=(
  "008f_leader_verification.sql:009_church_letters.sql"
)

create=0; fixtures=0; dry=0; url="${DATABASE_URL:-}"
for arg in "$@"; do
  case "$arg" in
    --create) create=1 ;;
    --test-fixtures) fixtures=1 ;;
    --dry-run) dry=1 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) url="$arg" ;;
  esac
done
[ -n "$url" ] || { echo "DATABASE_URL is not set (pass it as an argument or in the environment)" >&2; exit 2; }
# SQLAlchemy-style URLs (postgresql+asyncpg://) are not libpq URLs.
url="$(printf '%s' "$url" | sed -E 's#^postgres(ql)?\+[a-z0-9]+://#postgresql://#')"

# Never echo a password.
safe_url() { printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'; }
case "$url" in *neon.tech*) echo "refusing a Neon host: this script only builds NEW databases" >&2; exit 3 ;; esac

PSQL=(psql -X -q -v ON_ERROR_STOP=1)
sql() { "${PSQL[@]}" -d "$url" -At -c "$1"; }

if command -v sha256sum >/dev/null 2>&1; then sha() { sha256sum "$1" | cut -d' ' -f1; }
else sha() { shasum -a 256 "$1" | cut -d' ' -f1; }; fi

dbname="$(printf '%s' "$url" | sed -E 's#^[a-z]+://[^/]*/?##; s#\?.*$##')"
[ -n "$dbname" ] || { echo "the URL must name a database" >&2; exit 2; }
maint_url="$(printf '%s' "$url" | sed -E "s#^([a-z]+://[^/]*)/[^?]*#\1/postgres#")"

echo "database: $(safe_url "$url")"
if [ "$create" = 1 ]; then
  exists="$("${PSQL[@]}" -d "$maint_url" -At -c "SELECT 1 FROM pg_database WHERE datname = '$(printf '%s' "$dbname" | sed "s/'/''/g")'")"
  if [ -z "$exists" ]; then
    if [ "$dry" = 1 ]; then echo "would create database $dbname (UTF8)"; else
      # template0 + explicit UTF8: a cluster created with plain `initdb` under LC_ALL=C is
      # SQL_ASCII, and the seeds (accents, \u escapes in JSON) fail there.
      "${PSQL[@]}" -d "$maint_url" -c "CREATE DATABASE \"$dbname\" ENCODING 'UTF8' TEMPLATE template0"
      echo "created database $dbname"
    fi
  fi
fi

if [ "$dry" = 1 ] && [ -z "$(sql "SELECT 1" 2>/dev/null || true)" ]; then
  echo "(database not reachable yet: plan only)"; reachable=0
else
  reachable=1
  enc="$(sql "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()")"
  if [ "$enc" != "UTF8" ]; then
    echo "database encoding is $enc, not UTF8. Recreate it: CREATE DATABASE … ENCODING 'UTF8' TEMPLATE template0" >&2
    echo "(a whole cluster: initdb -E UTF8 --locale=C …)" >&2
    exit 4
  fi
  has_users="$(sql "SELECT to_regclass('public.users') IS NOT NULL")"
  has_track="$(sql "SELECT to_regclass('public.schema_migrations') IS NOT NULL")"
  if [ "$has_users" = t ] && [ "$has_track" = f ]; then
    echo "this database already has a schema that was not built by this script (no schema_migrations)." >&2
    echo "Refusing: it may be production or a hand-made copy. Use an empty database." >&2
    exit 3
  fi
fi

# ---- plan -------------------------------------------------------------------------------
migs=()   # bash 3.2 (macOS) has no mapfile
while IFS= read -r m; do migs+=("$m"); done < <(cd "$MIG_DIR" && ls -1 ./*.sql | sed 's#^\./##' | LC_ALL=C sort)
for fix in "${ORDER_FIXES[@]}"; do
  file="${fix%%:*}"; anchor="${fix##*:}"; out=()
  for m in "${migs[@]}"; do [ "$m" = "$file" ] || out+=("$m"); done
  migs=(); for m in "${out[@]}"; do migs+=("$m"); [ "$m" = "$anchor" ] && migs+=("$file"); done
done

plan=()
for f in "$BASE_DIR"/*.sql; do plan+=("base/$(basename "$f")"); done
for m in "${migs[@]}"; do plan+=("migrations/$m"); done
plan+=("seeds/minimal.sql")
[ "$fixtures" = 1 ] && plan+=("seeds/test_fixtures.sql")

path_of() {
  case "$1" in
    base/*) echo "$BASE_DIR/${1#base/}" ;;
    migrations/*) echo "$MIG_DIR/${1#migrations/}" ;;
    seeds/*) echo "$SEED_DIR/${1#seeds/}" ;;
  esac
}

if [ "$reachable" = 1 ] && [ "$dry" = 0 ]; then
  [ "$(sql "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" = t ] || \
  sql "CREATE TABLE schema_migrations (
         filename text PRIMARY KEY,
         sha256 char(64) NOT NULL,
         applied_at timestamptz NOT NULL DEFAULT now())" >/dev/null
fi

applied=0; skipped=0; changed=0
for item in "${plan[@]}"; do
  file="$(path_of "$item")"; sum="$(sha "$file")"
  prev=""
  if [ "$reachable" = 1 ] && [ "$(sql "SELECT to_regclass('public.schema_migrations') IS NOT NULL")" = t ]; then
    prev="$(sql "SELECT sha256 FROM schema_migrations WHERE filename = '$item'")"
  fi
  if [ -n "$prev" ]; then
    if [ "$prev" != "$sum" ]; then
      echo "changed  $item (applied with another content; not re-applied — review by hand)"; changed=$((changed+1))
    else skipped=$((skipped+1)); fi
    continue
  fi
  if [ "$dry" = 1 ]; then echo "pending  $item"; continue; fi
  # One transaction per file, together with its schema_migrations row.
  if ! "${PSQL[@]}" -d "$url" -1 -c "SET client_min_messages TO warning" -f "$file" \
      -c "INSERT INTO schema_migrations (filename, sha256) VALUES ('$item', '$sum')" >/dev/null; then
    echo "FAILED   $item" >&2; exit 1
  fi
  echo "applied  $item"; applied=$((applied+1))
done

echo "done: $applied applied, $skipped already applied, $changed changed"
[ "$changed" = 0 ] || exit 5
