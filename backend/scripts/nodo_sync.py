#!/usr/bin/env python3
"""Copies ONE event between production and the local event node (nodo-evento/README.md).

    export   production -> node, the day before. Copies what the node needs to run the event
             without internet (ministries, the org tree, the users that take part — with their
             password hashes, so everybody logs in with the same password —, their roles and
             memberships, and the event with ALL its event_* rows) and marks the event
             «en nodo local» in production: from then on the cloud only reads it (423).
    import   node -> production, when internet is back. Pushes the event's changes (check-ins,
             evaluations + revisions, adjustments, rules/status changes, registrations, staff)
             idempotently, detects conflicts against the snapshot taken at export, copies the
             node's audit trail of the event, records a report and releases the lock.
    status   what each side holds now (counts, lock, last export/import).

Both directions write in ONE transaction per database. `--dry-run` prints the full report
and changes nothing. Connection strings come ONLY from the environment or the arguments
(`PROD_DATABASE_URL`, `NODE_DATABASE_URL`); they are never printed (host and database only).

    python scripts/nodo_sync.py export --event <uuid> --actor-email <coordination e-mail>
           [--node-label "Mini PC La Morita"] [--dry-run] [--replace-node-copy]
           [--bundle-dir DIR] [--media-dir DIR] [--no-lock]
    python scripts/nodo_sync.py import --event <uuid> --actor-email <e-mail>
           [--dry-run] [--prefer node|prod] [--keep-lock] [--report FILE]
    python scripts/nodo_sync.py status --event <uuid>

Exit codes: 0 ok · 1 error (nothing written) · 2 import found conflicts (nothing applied for
those rows; lock kept) · 3 schema mismatch between both databases.
Requires asyncpg (already in requirements.txt). Run it with the API's environment, e.g.
`docker compose run --rm -e PROD_DATABASE_URL api python scripts/nodo_sync.py …`.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

# --------------------------------------------------------------------------------------------
# What is copied
# --------------------------------------------------------------------------------------------
# The event's own rows, parents first. `where` selects the rows of event $1.
REGS = "SELECT id FROM event_registrations WHERE event_id = $1"
EVENT_TABLES: list[tuple[str, str, str]] = [
    ("events", "id = $1", "id"),
    # Rounds/stations after their group activity.
    ("event_activities", "event_id = $1", "(parent_id IS NOT NULL), position, id"),
    ("event_adjustment_types", "event_id = $1", "position, id"),
    ("event_staff", "event_id = $1", "created_at, id"),
    ("event_registrations", "event_id = $1", "created_at, id"),
    ("event_check_ins", f"registration_id IN ({REGS})", "checked_in_at, id"),
    ("evaluations", f"registration_id IN ({REGS})", "created_at, id"),
    ("evaluation_revisions",
     f"evaluation_id IN (SELECT id FROM evaluations WHERE registration_id IN ({REGS}))", "created_at, id"),
    ("event_adjustments", f"registration_id IN ({REGS})", "created_at, id"),
]
EVENT_TABLE_NAMES = [name for name, _, _ in EVENT_TABLES]
# Columns that legitimately differ between both sides and are never compared nor imported:
# the lock lives only in production; the logo is mirrored locally on the node; the template
# the event was duplicated from is not copied.
IGNORED = {
    "events": {"offline_locked_at", "offline_locked_by_id", "offline_lock_reason", "offline_node_label",
               "brand_logo_url", "template_of_id"},
}
# Immutable on both sides (a trigger forbids UPDATE): a difference is always a conflict.
INSERT_ONLY = {"evaluation_revisions"}
# The node's audit rows that describe the event (imported if missing in production).
AUDIT_ENTITIES = ("EVENT", "EVENT_ACTIVITY", "EVENT_ADJUSTMENT", "EVENT_ADJUSTMENT_TYPE", "EVENT_CHECK_IN",
                  "EVALUATION", "EVENT_REGISTRATION", "EVENT_STAFF")
# Columns that reference users, per table (to remap a user created on the node).
USER_REFS = {
    "events": ["created_by_id"],
    "event_staff": ["user_id", "created_by_id", "ended_by_id"],
    "event_registrations": ["registered_by_id", "checked_in_by_id"],
    "event_check_ins": ["checked_in_by_id", "undone_by_id"],
    "evaluations": ["judge_id"],
    "evaluation_revisions": ["judge_id", "changed_by_id"],
    "event_adjustments": ["created_by_id", "approved_by_id", "voided_by_id"],
}
ADMIN_ROLES = ("ADMIN_ASSOCIATION", "ADMIN_UNION", "ADMIN_DIVISION")

# Users that take part in the event: staff, everybody referenced by its rows, the directors of
# its clubs, the admins whose scope contains its organization, and MASTER_GC. Never minors.
USERS_SQL = f"""
WITH ev AS (SELECT * FROM events WHERE id = $1),
     owner AS (SELECT o.path FROM organizations o JOIN ev ON ev.organization_id = o.id),
     anc AS (SELECT o.id FROM organizations o, owner WHERE o.path @> owner.path),
     regs AS (SELECT * FROM event_registrations WHERE event_id = $1),
     clubs AS (SELECT club_id FROM regs),
     ids AS (
       SELECT user_id AS id FROM event_staff WHERE event_id = $1
       UNION SELECT created_by_id FROM ev UNION SELECT offline_locked_by_id FROM ev
       UNION SELECT registered_by_id FROM regs UNION SELECT checked_in_by_id FROM regs
       UNION SELECT created_by_id FROM event_staff WHERE event_id = $1
       UNION SELECT ended_by_id FROM event_staff WHERE event_id = $1
       UNION SELECT checked_in_by_id FROM event_check_ins WHERE registration_id IN (SELECT id FROM regs)
       UNION SELECT undone_by_id FROM event_check_ins WHERE registration_id IN (SELECT id FROM regs)
       UNION SELECT judge_id FROM evaluations WHERE registration_id IN (SELECT id FROM regs)
       UNION SELECT r.judge_id FROM evaluation_revisions r JOIN evaluations e ON e.id = r.evaluation_id
             WHERE e.registration_id IN (SELECT id FROM regs)
       UNION SELECT r.changed_by_id FROM evaluation_revisions r JOIN evaluations e ON e.id = r.evaluation_id
             WHERE e.registration_id IN (SELECT id FROM regs)
       UNION SELECT created_by_id FROM event_adjustments WHERE registration_id IN (SELECT id FROM regs)
       UNION SELECT approved_by_id FROM event_adjustments WHERE registration_id IN (SELECT id FROM regs)
       UNION SELECT voided_by_id FROM event_adjustments WHERE registration_id IN (SELECT id FROM regs)
       UNION SELECT user_id FROM club_memberships
             WHERE club_id IN (SELECT club_id FROM clubs) AND role = 'CLUB_DIRECTOR' AND status = 'ACTIVE'
       UNION SELECT id FROM users WHERE role = 'CLUB_DIRECTOR' AND organization_id IN (SELECT club_id FROM clubs)
       UNION SELECT user_id FROM role_assignments WHERE status = 'ACTIVE' AND (
               (role = 'CLUB_DIRECTOR' AND organization_id IN (SELECT club_id FROM clubs))
               OR (role IN {ADMIN_ROLES} AND organization_id IN (SELECT id FROM anc))
               OR role = 'MASTER_GC')
       UNION SELECT id FROM users WHERE role IN {ADMIN_ROLES} AND organization_id IN (SELECT id FROM anc)
       UNION SELECT id FROM users WHERE role = 'MASTER_GC'
       UNION SELECT id FROM users WHERE lower(email) = lower($2)
     )
SELECT to_jsonb(u) AS row FROM users u
WHERE u.id IN (SELECT id FROM ids WHERE id IS NOT NULL) AND NOT u.is_minor
ORDER BY u.created_at, u.id
"""
ORGS_SQL = """
WITH RECURSIVE tree AS (
  SELECT id, 0 AS depth FROM organizations WHERE parent_id IS NULL
  UNION ALL SELECT o.id, t.depth + 1 FROM organizations o JOIN tree t ON o.parent_id = t.id
)
SELECT to_jsonb(o) AS row FROM organizations o JOIN tree t ON t.id = o.id ORDER BY t.depth, o.id
"""


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------
class SyncError(Exception):
    pass


def say(*parts) -> None:
    print(*parts, flush=True)


def describe(dsn: str) -> str:
    """host:port/db — never the user or the password."""
    parts = urlsplit(dsn)
    return f"{parts.hostname or 'socket'}{':' + str(parts.port) if parts.port else ''}{parts.path or ''}"


def clean_dsn(dsn: str) -> str:
    """libpq URL for asyncpg: no SQLAlchemy driver suffix, no libpq-only parameters."""
    if not dsn:
        raise SyncError("falta una cadena de conexión (PROD_DATABASE_URL / NODE_DATABASE_URL)")
    scheme, rest = dsn.split("://", 1)
    scheme = "postgresql"
    parts = urlsplit(f"{scheme}://{rest}")
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"channel_binding"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def connect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(clean_dsn(dsn), timeout=30)
    # Same text for the same value on both sides (fingerprints, jsonb timestamps).
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


def canon(row: dict, table: str) -> str:
    skip = IGNORED.get(table, set())
    return json.dumps({k: v for k, v in row.items() if k not in skip}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def fingerprint(row: dict, table: str) -> str:
    return hashlib.md5(canon(row, table).encode()).hexdigest()


async def rows_of(conn: asyncpg.Connection, sql: str, *args) -> list[dict]:
    return [json.loads(r["row"]) for r in await conn.fetch(sql, *args)]


async def event_rows(conn: asyncpg.Connection, event_id: uuid.UUID) -> dict[str, list[dict]]:
    out = {}
    for table, where, order in EVENT_TABLES:
        out[table] = await rows_of(conn, f"SELECT to_jsonb(t) AS row FROM {table} t WHERE {where} ORDER BY {order}",
                                   event_id)
    return out


async def columns(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1"
        " ORDER BY ordinal_position", table)
    return [r["column_name"] for r in rows]


async def check_schemas(prod, node, tables) -> None:
    problems = []
    for table in tables:
        a, b = set(await columns(prod, table)), set(await columns(node, table))
        if not a:
            problems.append(f"{table}: no existe en producción")
        elif not b:
            problems.append(f"{table}: no existe en el nodo")
        elif a != b:
            problems.append(f"{table}: sólo en producción {sorted(a - b)}; sólo en el nodo {sorted(b - a)}")
    if problems:
        raise SchemaMismatch("los esquemas no coinciden (aplica las migraciones que falten):\n  "
                             + "\n  ".join(problems))


class SchemaMismatch(SyncError):
    pass


async def upsert(conn, table: str, rows: list[dict], *, key: str = "id", update: bool = True) -> int:
    """INSERT … ON CONFLICT (key) DO UPDATE every column (DO NOTHING for update=False)."""
    if not rows:
        return 0
    cols = await columns(conn, table)
    keys = [c.strip() for c in key.split(",")]
    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in keys)
    action = f"DO UPDATE SET {sets}" if update and sets else "DO NOTHING"
    await conn.execute(
        f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table}, $1::jsonb)"
        f" ON CONFLICT ({key}) {action}", json.dumps(rows))
    return len(rows)


async def insert_rows(conn, table: str, rows: list[dict]) -> None:
    if rows:
        await conn.execute(f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table}, $1::jsonb)",
                           json.dumps(rows))


async def update_row(conn, table: str, row: dict) -> None:
    cols = [c for c in await columns(conn, table) if c != "id" and c not in IGNORED.get(table, set())]
    col_list = ", ".join(f'"{c}"' for c in cols)
    await conn.execute(
        f"UPDATE {table} SET ({col_list}) = (SELECT {col_list} FROM jsonb_populate_record(NULL::{table}, $1::jsonb))"
        f" WHERE id = $2", json.dumps(row), uuid.UUID(row["id"]))


async def audit(conn, *, action: str, entity_id, actor: dict | None, details: str, metadata: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (id, action, entity_type, entity_id, user_id, user_email, user_role,"
        " organization_id, details, metadata_json) VALUES ($1, $2, 'EVENT', $3, $4, $5, $6, $7, $8, $9::jsonb)",
        uuid.uuid4(), action, str(entity_id),
        uuid.UUID(actor["id"]) if actor else None, actor["email"] if actor else None,
        actor["role"] if actor else None,
        uuid.UUID(actor["organization_id"]) if actor and actor.get("organization_id") else None,
        details, json.dumps(metadata))


async def find_actor(conn, email: str) -> dict:
    row = await conn.fetchrow("SELECT to_jsonb(u) AS row FROM users u WHERE lower(email) = lower($1)"
                              " AND status = 'ACTIVE'", email)
    if row is None:
        raise SyncError(f"no hay una cuenta activa con el correo indicado en --actor-email ({email})")
    return json.loads(row["row"])


def counts(rows: dict[str, list]) -> dict[str, int]:
    return {table: len(items) for table, items in rows.items()}


# --------------------------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------------------------
def null_missing(row: dict, cols: list[str], keep: set[str]) -> dict:
    for col in cols:
        if row.get(col) and row[col] not in keep:
            row[col] = None
    return row


async def gather_export(prod, event_id: uuid.UUID, actor_email: str) -> dict:
    """Everything the node needs, read from ONE consistent snapshot of production."""
    event = await prod.fetchrow("SELECT id FROM events WHERE id = $1", event_id)
    if event is None:
        raise SyncError("el evento no existe en producción")
    users = await rows_of(prod, USERS_SQL, event_id, actor_email)
    user_ids = {u["id"] for u in users}
    for u in users:
        u["birth_date"] = None  # not needed at the event; less personal data on the node
    roles = [null_missing(r, ["granted_by_id", "ended_by_id"], user_ids) | {"invitation_id": None}
             for r in await rows_of(prod, "SELECT to_jsonb(r) AS row FROM role_assignments r"
                                          " WHERE user_id = ANY($1::uuid[]) ORDER BY granted_at, id",
                                    list(user_ids))]
    memberships = [
        null_missing(m, ["consent_by_id", "decided_by_id", "ended_by_id"], user_ids)
        | {"invitation_id": None, "unit_id": None, "consent_token_hash": None, "guardian_email": None}
        for m in await rows_of(prod, "SELECT to_jsonb(m) AS row FROM club_memberships m"
                                     " WHERE user_id = ANY($1::uuid[]) AND status = 'ACTIVE' ORDER BY created_at, id",
                               list(user_ids))]
    rows = await event_rows(prod, event_id)
    for row in rows["events"]:
        row["template_of_id"] = None  # the template is not copied (ignored on import)
    missing = sorted({ref for table, refs in USER_REFS.items() for item in rows[table] for col in refs
                      if (ref := item.get(col)) and ref not in user_ids})
    if missing:
        # Only a minor's account is never copied; an event row pointing at one needs a human.
        raise SyncError(f"filas del evento apuntan a {len(missing)} cuenta(s) de menores que no se copian: "
                        f"{', '.join(missing[:5])}")
    return {
        "format": "nodo-evento/1",
        "event_id": str(event_id),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "reference": {
            "ministries": await rows_of(prod, "SELECT to_jsonb(m) AS row FROM ministries m ORDER BY created_at, id"),
            "organizations": await rows_of(prod, ORGS_SQL),
            "organization_ministries": await rows_of(
                prod, "SELECT to_jsonb(x) AS row FROM organization_ministries x ORDER BY organization_id, ministry_id"),
            "users": users,
            "role_assignments": roles,
            "club_memberships": memberships,
        },
        "event": rows,
    }


def snapshot_of(rows: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    return {table: {r["id"]: fingerprint(r, table) for r in items} for table, items in rows.items()}


def mirror_logo(event_row: dict, media_dir: str | None) -> str | None:
    """Downloads the event's logo so the node serves it without internet. Best effort."""
    url = event_row.get("brand_logo_url")
    if not url or not media_dir or not url.startswith("https://"):
        return None
    name = f"{event_row['id']}-{hashlib.sha256(url.encode()).hexdigest()[:12]}{Path(urlsplit(url).path).suffix[:6]}"
    target = Path(media_dir) / "events" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 (https only, own bucket)
            target.write_bytes(response.read(5_000_000))
    except Exception as exc:  # noqa: BLE001
        say(f"  aviso: no se pudo copiar el logo del evento ({type(exc).__name__}); el nodo lo mostrará sin logo")
        return None
    return f"/nodo-media/events/{name}"


async def rekey(conn, table: str, natural: str, rows: list[dict]) -> int:
    """The node was bootstrapped on its own, so its seeded rows (ministries) carry other ids
    than production's. For each production row whose natural key exists on the node under
    another id, move every reference to production's id, then drop the node's row."""
    refs = await conn.fetch(
        "SELECT c.conrelid::regclass::text AS tbl, a.attname AS col FROM pg_constraint c"
        " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)"
        " WHERE c.contype = 'f' AND c.confrelid = $1::regclass", table)
    moved = 0
    for row in rows:
        old = await conn.fetchval(f"SELECT id FROM {table} WHERE {natural} = $1 AND id <> $2",
                                  row[natural], uuid.UUID(row["id"]))
        if old is None:
            continue
        await conn.execute(f"UPDATE {table} SET {natural} = {natural} || '~' || left(id::text, 8) WHERE id = $1", old)
        await upsert(conn, table, [row])
        for ref in refs:
            await conn.execute(f'UPDATE {ref["tbl"]} SET "{ref["col"]}" = $1 WHERE "{ref["col"]}" = $2',
                               uuid.UUID(row["id"]), old)
        await conn.execute(f"DELETE FROM {table} WHERE id = $1", old)
        moved += 1
    return moved


async def load_into_node(node, bundle: dict, *, replace: bool, local_logo: str | None, export_id: str) -> None:
    event_id = uuid.UUID(bundle["event_id"])
    ref, rows = bundle["reference"], bundle["event"]
    async with node.transaction():
        if await node.fetchval("SELECT 1 FROM events WHERE id = $1", event_id):
            if not replace:
                raise SyncError("el nodo ya tiene una copia de este evento. Si es de una prueba o de un export "
                                "anterior SIN capturas que conservar, repite con --replace-node-copy")
            await node.execute("DELETE FROM events WHERE id = $1", event_id)
        await rekey(node, "ministries", "slug", ref["ministries"])
        await upsert(node, "ministries", ref["ministries"])
        await upsert(node, "organizations", ref["organizations"])
        await upsert(node, "organization_ministries", ref["organization_ministries"],
                     key="organization_id, ministry_id", update=False)
        await upsert(node, "users", ref["users"])
        await upsert(node, "role_assignments", ref["role_assignments"])
        await upsert(node, "club_memberships", ref["club_memberships"])
        for table in EVENT_TABLE_NAMES:
            await insert_rows(node, table, rows[table])
        # The node's copy is never locked: there it is the source of truth.
        await node.execute(
            "UPDATE events SET offline_locked_at = NULL, offline_locked_by_id = NULL, offline_lock_reason = NULL,"
            " offline_node_label = NULL, template_of_id = NULL, brand_logo_url = COALESCE($2, brand_logo_url)"
            " WHERE id = $1", event_id, local_logo)
        await node.execute(
            "INSERT INTO event_offline_syncs (id, event_id, kind, export_id, node_label, actor_email, snapshot, report,"
            " created_at) VALUES ($1, $2, 'NODE_COPY', NULL, $3, $4, $5::jsonb, $6::jsonb, $7)",
            uuid.uuid4(), event_id, bundle.get("node_label"), bundle.get("actor_email"),
            json.dumps(snapshot_of(rows)), json.dumps({"prod_export_id": export_id, "counts": counts(rows)}),
            datetime.fromisoformat(bundle["exported_at"]))


async def verify_passes(node, event_id: uuid.UUID) -> dict:
    """With the node's EVENT_PASS_SECRET (this process's settings), every stored pass must open.
    A pass that does not open would be REGENERATED the first time somebody shows it on the node,
    and the printed QR would stop working. Only runs where the app code is importable."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ (the app package)
    os.environ.setdefault("DATABASE_URL", "postgresql://unused@localhost/unused")
    try:
        from app.services import event_pass  # noqa: PLC0415
        from app.security import sha256_hex  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {"checked": False}
    rows = await node.fetch("SELECT pass_token_hash, pass_token_enc FROM event_registrations"
                            " WHERE event_id = $1 AND status = 'REGISTERED'", event_id)
    ok = bad = none = 0
    for r in rows:
        if not r["pass_token_enc"]:
            none += 1
            continue
        token = event_pass.decrypt(r["pass_token_enc"])
        if token is not None and sha256_hex(token) == r["pass_token_hash"]:
            ok += 1
        else:
            bad += 1
    return {"checked": True, "ok": ok, "unreadable": bad, "without_pass": none}


async def cmd_export(args) -> int:
    event_id = uuid.UUID(args.event)
    prod_dsn, node_dsn = args.prod_url, args.node_url
    say(f"export · evento {event_id}\n  producción: {describe(prod_dsn)}\n  nodo:       {describe(node_dsn)}")
    prod, node = await connect(prod_dsn), await connect(node_dsn)
    try:
        await check_schemas(prod, node, ["ministries", "organizations", "organization_ministries", "users",
                                         "role_assignments", "club_memberships", "event_offline_syncs",
                                         *EVENT_TABLE_NAMES])
        actor = await find_actor(prod, args.actor_email)
        event = await prod.fetchrow("SELECT name, status, offline_locked_at, offline_node_label FROM events"
                                    " WHERE id = $1", event_id)
        if event is None:
            raise SyncError("el evento no existe en producción")
        say(f"  evento: {event['name']} ({event['status']})")
        on_node = await node.fetchval("SELECT 1 FROM events WHERE id = $1", event_id)
        if on_node and not args.replace_node_copy:
            raise SyncError("el nodo ya tiene una copia de este evento (usa --replace-node-copy si es descartable)")

        if args.dry_run:
            async with prod.transaction(isolation="repeatable_read", readonly=True):
                bundle = await gather_export(prod, event_id, args.actor_email)
            report_export(bundle, locked_before=event["offline_locked_at"] is not None, dry=True)
            return 0

        # 1) Lock first, then wait for writes already past the API guard to finish, then read.
        locked_now = False
        if not args.no_lock and event["offline_locked_at"] is None:
            async with prod.transaction():
                await prod.execute(
                    "UPDATE events SET offline_locked_at = now(), offline_locked_by_id = $2,"
                    " offline_lock_reason = $3, offline_node_label = $4 WHERE id = $1",
                    event_id, uuid.UUID(actor["id"]), args.reason, args.node_label)
                await audit(prod, action="EVENT_OFFLINE_LOCK", entity_id=event_id, actor=actor, details=args.reason,
                            metadata={"node_label": args.node_label, "via": "nodo_sync export"})
            locked_now = True
            await asyncio.sleep(args.settle_seconds)
        # 2) One consistent read + the EXPORT record (snapshot for conflict detection on import).
        export_id = uuid.uuid4()
        async with prod.transaction(isolation="repeatable_read"):
            bundle = await gather_export(prod, event_id, args.actor_email)
            bundle["node_label"], bundle["actor_email"] = args.node_label, actor["email"]
            await prod.execute(
                "INSERT INTO event_offline_syncs (id, event_id, kind, node_label, actor_email, snapshot, report)"
                " VALUES ($1, $2, 'EXPORT', $3, $4, $5::jsonb, $6::jsonb)",
                export_id, event_id, args.node_label, actor["email"], json.dumps(snapshot_of(bundle["event"])),
                json.dumps({"counts": counts(bundle["event"]), "users": len(bundle["reference"]["users"])}))
        # 3) Keep a copy of what was sent (it holds password hashes: owner-only file).
        if args.bundle_dir:
            path = Path(args.bundle_dir) / f"export-{event_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as fh:
                fh.write(json.dumps(bundle, ensure_ascii=False).encode())
            say(f"  copia del export: {path} (contiene hashes de contraseñas: no la compartas)")
        # 4) Load the node.
        local_logo = mirror_logo(bundle["event"]["events"][0], args.media_dir)
        try:
            await load_into_node(node, bundle, replace=args.replace_node_copy, local_logo=local_logo,
                                 export_id=str(export_id))
        except Exception:
            say("  ERROR al cargar el nodo: el nodo quedó sin cambios."
                + (" Producción quedó BLOQUEADA para este evento: corrige y repite el export, o libera el bloqueo"
                   " (DELETE /events/{id}/offline-lock)." if locked_now or event["offline_locked_at"] else ""))
            raise
        report_export(bundle, locked_before=event["offline_locked_at"] is not None, dry=False,
                      locked_now=locked_now, no_lock=args.no_lock)
        passes = await verify_passes(node, event_id)
        if passes.get("checked"):
            say(f"  pases en el nodo: {passes['ok']} legibles, {passes['unreadable']} ilegibles, "
                f"{passes['without_pass']} sin pase todavía")
            if passes["unreadable"]:
                say("  ¡ATENCIÓN! EVENT_PASS_SECRET del nodo no coincide con el de producción: al mostrarse en el "
                    "nodo, esos pases se regenerarían y los impresos dejarían de servir. Corrige el secreto.")
                return 1
        return 0
    finally:
        await prod.close()
        await node.close()


def report_export(bundle: dict, *, locked_before: bool, dry: bool, locked_now: bool = False,
                  no_lock: bool = False) -> None:
    ref, rows = bundle["reference"], bundle["event"]
    say("  " + ("[simulación] se copiaría:" if dry else "copiado al nodo:"))
    for name in ("ministries", "organizations", "users", "role_assignments", "club_memberships"):
        say(f"    {name:<24} {len(ref[name]):>6}")
    for table in EVENT_TABLE_NAMES:
        say(f"    {table:<24} {len(rows[table]):>6}")
    regs = rows["event_registrations"]
    no_pass = sum(1 for r in regs if r.get("status") == "REGISTERED" and not r.get("pass_token_hash"))
    unreadable_kind = sum(1 for r in regs if r.get("status") == "REGISTERED" and r.get("pass_token_hash")
                          and not r.get("pass_token_enc"))
    if no_pass or unreadable_kind:
        say(f"  aviso: {no_pass} club(es) sin pase y {unreadable_kind} con pase antiguo: imprime los pases "
            "ANTES del export para que el QR impreso sea el vigente.")
    if dry:
        say("  [simulación] producción quedaría bloqueada (en nodo local)" if not locked_before
            else "  producción ya está bloqueada (en nodo local)")
    elif no_lock:
        say("  producción NO se bloqueó (--no-lock): sólo para pruebas")
    else:
        say("  producción: evento bloqueado (en nodo local)" + ("" if locked_now else " — ya lo estaba"))


# --------------------------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------------------------
async def cmd_import(args) -> int:
    event_id = uuid.UUID(args.event)
    say(f"import · evento {event_id}{' · SIMULACIÓN' if args.dry_run else ''}\n"
        f"  nodo:       {describe(args.node_url)}\n  producción: {describe(args.prod_url)}")
    prod, node = await connect(args.prod_url), await connect(args.node_url)
    try:
        await check_schemas(prod, node, ["users", "audit_log", "event_offline_syncs", *EVENT_TABLE_NAMES])
        actor = await find_actor(prod, args.actor_email)
        export = await prod.fetchrow(
            "SELECT id, snapshot, created_at FROM event_offline_syncs WHERE event_id = $1 AND kind = 'EXPORT'"
            " ORDER BY created_at DESC LIMIT 1", event_id)
        if export is None:
            raise SyncError("producción no tiene un EXPORT de este evento: no hay contra qué comparar")
        node_copy = await node.fetchrow(
            "SELECT created_at, report FROM event_offline_syncs WHERE event_id = $1 AND kind = 'NODE_COPY'"
            " ORDER BY created_at DESC LIMIT 1", event_id)
        if node_copy is None:
            raise SyncError("el nodo no tiene una copia de este evento")
        copied_from = json.loads(node_copy["report"]).get("prod_export_id")
        if copied_from and copied_from != str(export["id"]):
            say(f"  aviso: el nodo se cargó del export {copied_from}, y el último de producción es {export['id']}")
        snapshot = json.loads(export["snapshot"])

        async with prod.transaction(isolation="repeatable_read"):
            node_rows = await event_rows(node, event_id)
            prod_rows = await event_rows(prod, event_id)
            if not node_rows["events"]:
                raise SyncError("el nodo no tiene el evento")
            remap, new_users, user_notes = await plan_users(prod, node, node_rows)
            for table, refs in USER_REFS.items():
                for row in node_rows[table]:
                    for col in refs:
                        if row.get(col) in remap:
                            row[col] = remap[row[col]]
            plan = plan_changes(snapshot, node_rows, prod_rows, prefer=args.prefer)
            audits = await plan_audit(prod, node, node_copy["created_at"], remap, args.node_label_hint)
            lock = await prod.fetchrow("SELECT offline_locked_at, offline_node_label FROM events WHERE id = $1",
                                       event_id)
            report = build_report(plan, new_users, user_notes, audits, export, lock, args)
            print_report(report)
            if args.report:
                Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
                say(f"  reporte JSON: {args.report}")
            if args.dry_run:
                say("  SIMULACIÓN: no se escribió nada.")
                raise _Rollback()
            await apply_plan(prod, plan, new_users, audits)
            release = (lock["offline_locked_at"] is not None and not args.keep_lock and not plan["conflicts"])
            if release:
                await prod.execute(
                    "UPDATE events SET offline_locked_at = NULL, offline_locked_by_id = NULL,"
                    " offline_lock_reason = NULL, offline_node_label = NULL WHERE id = $1", event_id)
                await audit(prod, action="EVENT_OFFLINE_UNLOCK", entity_id=event_id, actor=actor,
                            details="Datos del nodo local importados",
                            metadata={"node_label": lock["offline_node_label"], "via": "nodo_sync import",
                                      "export_id": str(export["id"])})
            report["lock_released"] = release
            await prod.execute(
                "INSERT INTO event_offline_syncs (id, event_id, kind, export_id, node_label, actor_email, report)"
                " VALUES ($1, $2, 'IMPORT', $3, $4, $5, $6::jsonb)",
                uuid.uuid4(), event_id, export["id"], lock["offline_node_label"], actor["email"],
                json.dumps(report, default=str))
        say("  APLICADO en producción." + (" Bloqueo liberado." if report["lock_released"] else
                                           " El bloqueo SIGUE (conflictos pendientes o --keep-lock)."
                                           if lock["offline_locked_at"] else ""))
        return 2 if plan["conflicts"] else 0
    except _Rollback:
        return 2 if plan["conflicts"] else 0
    finally:
        await prod.close()
        await node.close()


class _Rollback(Exception):
    pass


async def plan_users(prod, node, node_rows) -> tuple[dict, list[dict], list[str]]:
    """Users referenced by the node's rows that production does not have: created on the node.
    Same e-mail in production -> remap to that account; otherwise -> insert it."""
    referenced = {row[col] for table, refs in USER_REFS.items() for row in node_rows[table] for col in refs
                  if row.get(col)}
    if not referenced:
        return {}, [], []
    known = {str(r["id"]) for r in await prod.fetch("SELECT id FROM users WHERE id = ANY($1::uuid[])",
                                                    list(referenced))}
    remap, new_users, notes = {}, [], []
    for user in await rows_of(node, "SELECT to_jsonb(u) AS row FROM users u WHERE id = ANY($1::uuid[])",
                              list(referenced - known)):
        same = await prod.fetchval("SELECT id FROM users WHERE lower(email) = lower($1)", user["email"])
        if same:
            remap[user["id"]] = str(same)
            notes.append(f"cuenta creada en el nodo {user['email']} -> se usa la de producción")
        else:
            new_users.append(user)
            notes.append(f"cuenta creada en el nodo {user['email']} -> se crea en producción")
    return remap, new_users, notes


def plan_changes(snapshot: dict, node_rows: dict, prod_rows: dict, *, prefer: str | None) -> dict:
    """Three-way comparison per row: S = fingerprint at export, N = node now, P = production now.
      N == P                -> unchanged
      P == S, N != S        -> the node changed it -> apply (insert / update)
      N == S, P != S        -> only the cloud changed it (should not happen while locked) -> keep, warn
      N != S, P != S, N!=P  -> CONFLICT (both changed) -> not applied unless --prefer
      only in node          -> new on the node -> insert
      in S and P, not in N  -> deleted on the node -> delete if P == S (else conflict)
      only in P (not in S)  -> created in the cloud after the export -> keep, warn"""
    plan = {"insert": {}, "update": {}, "delete": {}, "conflicts": [], "cloud_only": [], "unchanged": {}}
    for table in EVENT_TABLE_NAMES:
        snap = snapshot.get(table, {})
        nodes = {r["id"]: r for r in node_rows[table]}
        prods = {r["id"]: r for r in prod_rows[table]}
        ins, upd, dele, same = [], [], [], 0
        for rid, row in nodes.items():
            n = fingerprint(row, table)
            if rid not in prods:
                if rid in snap:  # deleted in the cloud after the export, still alive on the node
                    item = {"table": table, "id": rid, "what": "borrado en la nube, vivo en el nodo"}
                    if prefer == "node":
                        ins.append(row)
                        item["resolved"] = "node"
                        plan["cloud_only"].append(item)
                    else:
                        plan["conflicts"].append(item)
                else:
                    ins.append(row)
                continue
            p = fingerprint(prods[rid], table)
            s = snap.get(rid)
            if n == p:
                same += 1
            elif p == s:
                if table in INSERT_ONLY:
                    plan["conflicts"].append({"table": table, "id": rid, "what": "fila inmutable distinta"})
                else:
                    upd.append(row)
            elif n == s:
                plan["cloud_only"].append({"table": table, "id": rid, "what": "cambiada sólo en la nube (se conserva)"})
            else:
                item = {"table": table, "id": rid, "what": "cambiada en la nube y en el nodo",
                        "fields": diff_fields(prods[rid], row, table)}
                if prefer == "node" and table not in INSERT_ONLY:
                    upd.append(row)
                    item["resolved"] = "node"
                    plan["cloud_only"].append(item)
                elif prefer == "prod":
                    item["resolved"] = "prod"
                    plan["cloud_only"].append(item)
                else:
                    plan["conflicts"].append(item)
        for rid, row in prods.items():
            if rid in nodes:
                continue
            if rid in snap:
                if fingerprint(row, table) == snap[rid]:
                    dele.append(rid)
                else:
                    plan["conflicts"].append({"table": table, "id": rid,
                                              "what": "borrada en el nodo pero cambiada en la nube"})
            else:
                plan["cloud_only"].append({"table": table, "id": rid, "what": "creada en la nube tras el export"})
        plan["insert"][table], plan["update"][table], plan["delete"][table] = ins, upd, dele
        plan["unchanged"][table] = same
    # A revision records the values an evaluation had BEFORE a change: while that evaluation is
    # in conflict (not applied), its new revisions wait too, or the history would contradict it.
    held = {c["id"] for c in plan["conflicts"] if c["table"] == "evaluations"}
    if held:
        waiting = [r for r in plan["insert"]["evaluation_revisions"] if r["evaluation_id"] in held]
        plan["insert"]["evaluation_revisions"] = [r for r in plan["insert"]["evaluation_revisions"]
                                                  if r["evaluation_id"] not in held]
        plan["cloud_only"].extend({"table": "evaluation_revisions", "id": r["id"],
                                   "what": "espera: su evaluación está en conflicto"} for r in waiting)
    return plan


def diff_fields(a: dict, b: dict, table: str) -> list[str]:
    skip = IGNORED.get(table, set())
    return sorted(k for k in set(a) | set(b) if k not in skip and a.get(k) != b.get(k))


async def plan_audit(prod, node, since, remap: dict, _hint) -> list[dict]:
    rows = await rows_of(node, "SELECT to_jsonb(a) AS row FROM audit_log a WHERE created_at >= $1"
                               " AND entity_type = ANY($2::text[]) ORDER BY created_at, id", since, list(AUDIT_ENTITIES))
    if not rows:
        return []
    have = {str(r["id"]) for r in await prod.fetch("SELECT id FROM audit_log WHERE id = ANY($1::uuid[])",
                                                   [r["id"] for r in rows])}
    out = []
    for row in rows:
        if row["id"] in have:
            continue
        if row.get("user_id") in remap:
            row["user_id"] = remap[row["user_id"]]
        row["metadata_json"] = {**(row.get("metadata_json") or {}), "via_nodo_local": True}
        row["legacy_mongo_id"] = None
        out.append(row)
    return out


async def apply_plan(prod, plan: dict, new_users: list[dict], audits: list[dict]) -> None:
    if new_users:
        await insert_rows(prod, "users", new_users)
    # Parents first; inside a table, updates before inserts (a void frees the «one current» slot
    # that the new capture takes).
    for table in EVENT_TABLE_NAMES:
        for row in plan["update"][table]:
            await update_row(prod, table, row)
        rows = plan["insert"][table]
        if table == "events" and rows:
            raise SyncError("el evento no existe en producción")
        await insert_rows(prod, table, rows)
    for table in reversed(EVENT_TABLE_NAMES):
        if plan["delete"][table]:
            await prod.execute(f"DELETE FROM {table} WHERE id = ANY($1::uuid[])",
                               [uuid.UUID(i) for i in plan["delete"][table]])
    if audits:
        known_users = {str(r["id"]) for r in await prod.fetch(
            "SELECT id FROM users WHERE id = ANY($1::uuid[])", [a["user_id"] for a in audits if a.get("user_id")])}
        for a in audits:
            if a.get("user_id") and a["user_id"] not in known_users:
                a["user_id"] = None
        await insert_rows(prod, "audit_log", audits)


def build_report(plan, new_users, user_notes, audits, export, lock, args) -> dict:
    return {
        "event_id": args.event,
        "export_id": str(export["id"]),
        "exported_at": export["created_at"].isoformat(),
        "dry_run": args.dry_run,
        "prefer": args.prefer,
        "insert": {t: len(v) for t, v in plan["insert"].items() if v},
        "update": {t: len(v) for t, v in plan["update"].items() if v},
        "delete": {t: len(v) for t, v in plan["delete"].items() if v},
        "unchanged": {t: v for t, v in plan["unchanged"].items() if v},
        "conflicts": plan["conflicts"],
        "warnings": plan["cloud_only"],
        "users": user_notes,
        "new_users": len(new_users),
        "audit_rows": len(audits),
        "locked": lock["offline_locked_at"] is not None,
        "lock_released": False,
    }


def print_report(report: dict) -> None:
    def block(title, data):
        say(f"  {title}:" + ("" if data else " —"))
        for table, n in data.items():
            say(f"    {table:<24} {n:>6}")
    block("nuevas (se insertan)", report["insert"])
    block("cambiadas en el nodo (se actualizan)", report["update"])
    block("borradas en el nodo (se borran)", report["delete"])
    block("sin cambios", report["unchanged"])
    say(f"  bitácora del nodo a copiar: {report['audit_rows']} fila(s)")
    for note in report["users"]:
        say(f"  {note}")
    for w in report["warnings"]:
        say(f"  aviso: {w['table']} {w['id']}: {w['what']}" + (f" (resuelto: {w['resolved']})" if w.get("resolved") else ""))
    if report["conflicts"]:
        say(f"  CONFLICTOS ({len(report['conflicts'])}): no se aplican; revisa y repite con --prefer node|prod")
        for c in report["conflicts"]:
            say(f"    {c['table']} {c['id']}: {c['what']}" + (f" · campos {c['fields']}" if c.get("fields") else ""))
    else:
        say("  conflictos: ninguno")


# --------------------------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------------------------
async def cmd_status(args) -> int:
    event_id = uuid.UUID(args.event)
    for label, dsn in (("producción", args.prod_url), ("nodo", args.node_url)):
        if not dsn:
            continue
        conn = await connect(dsn)
        try:
            event = await conn.fetchrow("SELECT name, status, offline_locked_at, offline_node_label FROM events"
                                        " WHERE id = $1", event_id)
            say(f"{label} ({describe(dsn)}):")
            if event is None:
                say("  sin el evento")
                continue
            lock = (f"BLOQUEADO desde {event['offline_locked_at']:%Y-%m-%d %H:%M} UTC ({event['offline_node_label']})"
                    if event["offline_locked_at"] else "sin bloqueo")
            say(f"  {event['name']} · {event['status']} · {lock}")
            for table, n in counts(await event_rows(conn, event_id)).items():
                say(f"    {table:<24} {n:>6}")
            for r in await conn.fetch("SELECT kind, created_at, actor_email FROM event_offline_syncs"
                                      " WHERE event_id = $1 ORDER BY created_at DESC LIMIT 5", event_id):
                say(f"  {r['kind']:<9} {r['created_at']:%Y-%m-%d %H:%M} UTC  {r['actor_email'] or ''}")
        finally:
            await conn.close()
    return 0


# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sincroniza un evento entre producción y el nodo local.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, actor=True):
        p.add_argument("--event", required=True, help="id (uuid) del evento")
        p.add_argument("--prod-url", default=os.environ.get("PROD_DATABASE_URL"),
                       help="por defecto $PROD_DATABASE_URL (mejor por entorno: no queda en el historial)")
        p.add_argument("--node-url", default=os.environ.get("NODE_DATABASE_URL") or os.environ.get("DATABASE_URL"),
                       help="por defecto $NODE_DATABASE_URL o $DATABASE_URL")
        if actor:
            p.add_argument("--actor-email", required=True, help="cuenta de coordinación que firma la bitácora")

    e = sub.add_parser("export", help="producción -> nodo (y bloqueo en producción)")
    common(e)
    e.add_argument("--node-label", default="Nodo local del evento")
    e.add_argument("--reason", default="Evento operado sin internet en el nodo local")
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--replace-node-copy", action="store_true", help="borra antes la copia del nodo (¡sus capturas!)")
    e.add_argument("--no-lock", action="store_true", help="no bloquear producción (sólo pruebas)")
    e.add_argument("--bundle-dir", default=os.environ.get("NODO_BUNDLE_DIR"))
    e.add_argument("--media-dir", default=os.environ.get("NODO_MEDIA_DIR"))
    e.add_argument("--settle-seconds", type=float, default=3.0, help=argparse.SUPPRESS)

    i = sub.add_parser("import", help="nodo -> producción (y libera el bloqueo)")
    common(i)
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--prefer", choices=("node", "prod"))
    i.add_argument("--keep-lock", action="store_true")
    i.add_argument("--report")
    i.set_defaults(node_label_hint=None)

    s = sub.add_parser("status", help="qué tiene cada lado")
    common(s, actor=False)

    args = parser.parse_args(argv)
    if args.command != "status" and not (args.prod_url and args.node_url):
        parser.error("faltan PROD_DATABASE_URL y/o NODE_DATABASE_URL (entorno o --prod-url/--node-url)")
    if args.command != "status" and clean_dsn(args.prod_url) == clean_dsn(args.node_url):
        parser.error("producción y nodo apuntan a la misma base")
    handler = {"export": cmd_export, "import": cmd_import, "status": cmd_status}[args.command]
    try:
        return asyncio.run(handler(args))
    except SchemaMismatch as exc:
        say(f"ERROR: {exc}")
        return 3
    except SyncError as exc:
        say(f"ERROR: {exc}")
        return 1
    except (OSError, asyncpg.PostgresError) as exc:
        # Never echo a DSN: asyncpg messages do not include the password, but keep it short.
        say(f"ERROR ({type(exc).__name__}): {str(exc).splitlines()[0][:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
