"""Shared logic of the event-node sync (029/030): used by `scripts/nodo_sync.py` (manual mode,
direct database connections, and the node side of the automatic mode) and by the API
(`app/services/nodo_api.py`, automatic mode).

Everything here talks to PostgreSQL through raw asyncpg connections and moves rows as JSON
(`to_jsonb`), so both sides compare the same text. Nothing here imports the web app, except
lazily where noted, so the script can run it with nothing but asyncpg + cryptography.

Several NODES may operate the same event (one per coordinator/location, 030). Each node has a
scope (`{"all", "activity_ids", "check_in"}`) and a BASELINE: what the cloud held for every row
according to that node (its export, then advanced by each of its uploads). An upload is a
three-way merge per row: S = baseline, N = node now, P = cloud now.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

EXPORT_FORMAT = "nodo-evento/1"
CHANGES_FORMAT = "nodo-cambios/1"
# Snapshot fingerprints: 1 = 029 (every column but IGNORED), 2 = 030 (also COMPARE_IGNORED).
SNAPSHOT_FORMAT = 2
FORMAT_KEY = "__format"
ROWS_KEY = "__rows"

OPEN_STATUSES = ("PREPARED", "PARTIAL")
NODE_STATUS_ES = {"PREPARED": "preparado", "PARTIAL": "subió avance", "FINAL": "subió final", "CLOSED": "cerrado"}

# --------------------------------------------------------------------------------------------
# What is copied
# --------------------------------------------------------------------------------------------
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
# 030: also never COMPARED (but still copied): the pass ciphertext differs by design (each side
# encrypts with its own secret; the hash is what identifies the pass), and the check-in mirror
# on the registration is recomputed from event_check_ins after every merge (two nodes may each
# check a club in; the earliest wins).
COMPARE_IGNORED = {
    "event_registrations": {"pass_token_enc", "updated_at", "checked_in_at", "checked_in_by_id", "checked_in_as"},
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
# Event fields merged field by field when two nodes changed the event row (status, rules).
MAX_MERGE_FIELDS = {"rules_version", "updated_at"}
# Event-level tables a node with a PARTIAL scope may not change (the node API refuses it too).
EVENT_LEVEL_TABLES = {"event_staff", "event_adjustment_types", "event_registrations"}

# Users that take part in the event: staff, everybody referenced by its rows, the directors of
# its clubs, the admins whose scope contains its organization, and whoever prepares the node.
# Never minors. (029 also copied every MASTER_GC: since 030 a coordinator can prepare a node and
# read its bundle, so a MASTER account travels only when it takes part in the event.)
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
               OR (role IN {ADMIN_ROLES} AND organization_id IN (SELECT id FROM anc)))
       UNION SELECT id FROM users WHERE role IN {ADMIN_ROLES} AND organization_id IN (SELECT id FROM anc)
       UNION SELECT id FROM users WHERE lower(email) = lower($2)
     )
SELECT to_jsonb(u) AS row FROM users u
WHERE u.id IN (SELECT id FROM ids WHERE id IS NOT NULL) AND NOT u.is_minor
ORDER BY u.created_at, u.id
"""
# 030: only the organizations the copy needs — the event's owner, its clubs, the organizations
# of the people copied (accounts, roles, memberships) — and every ancestor of those, parents
# first. (029 copied the whole tree.)
ORGS_SQL = """
WITH need AS (SELECT path FROM organizations WHERE id = ANY($1::uuid[]))
SELECT to_jsonb(o) AS row FROM organizations o
WHERE EXISTS (SELECT 1 FROM need WHERE o.path @> need.path)
ORDER BY nlevel(o.path), o.id
"""


# --------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------
class SyncError(Exception):
    """A reason to stop, safe to show (Spanish). `code` maps to an HTTP status in the API."""

    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


class SchemaMismatch(SyncError):
    pass


def describe(dsn: str) -> str:
    """host:port/db — never the user or the password."""
    parts = urlsplit(dsn)
    return f"{parts.hostname or 'socket'}{':' + str(parts.port) if parts.port else ''}{parts.path or ''}"


def clean_dsn(dsn: str) -> str:
    """libpq URL for asyncpg: no SQLAlchemy driver suffix, no libpq-only parameters."""
    if not dsn:
        raise SyncError("falta una cadena de conexión (PROD_DATABASE_URL / NODE_DATABASE_URL)")
    _scheme, rest = dsn.split("://", 1)
    parts = urlsplit(f"postgresql://{rest}")
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"channel_binding"}]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def connect(dsn: str, *, ssl=None) -> asyncpg.Connection:
    kwargs = {"ssl": ssl} if ssl is not None else {}
    conn = await asyncpg.connect(clean_dsn(dsn), timeout=30, **kwargs)
    # Same text for the same value on both sides (fingerprints, jsonb timestamps).
    await conn.execute("SET TIME ZONE 'UTC'")
    return conn


def _j(value):
    """jsonb as it comes from asyncpg (text) or already decoded."""
    if value is None:
        return None
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _skip(table: str, fmt: int) -> set[str]:
    skip = set(IGNORED.get(table, set()))
    if fmt >= 2:
        skip |= COMPARE_IGNORED.get(table, set())
    return skip


def canon(row: dict, table: str, fmt: int = SNAPSHOT_FORMAT) -> str:
    skip = _skip(table, fmt)
    return json.dumps({k: v for k, v in row.items() if k not in skip}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def fingerprint(row: dict, table: str, fmt: int = SNAPSHOT_FORMAT) -> str:
    return hashlib.md5(canon(row, table, fmt).encode()).hexdigest()


async def rows_of(conn: asyncpg.Connection, sql: str, *args) -> list[dict]:
    return [_j(r["row"]) for r in await conn.fetch(sql, *args)]


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


async def audit(conn, *, action: str, entity_id, actor: dict | None, details: str | None, metadata: dict,
                ip: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO audit_log (id, action, entity_type, entity_id, user_id, user_email, user_role,"
        " organization_id, details, metadata_json, ip_address) VALUES ($1, $2, 'EVENT', $3, $4, $5, $6, $7, $8,"
        " $9::jsonb, $10::text::inet)",
        uuid.uuid4(), action, str(entity_id),
        uuid.UUID(actor["id"]) if actor else None, actor["email"] if actor else None,
        actor["role"] if actor else None,
        uuid.UUID(actor["organization_id"]) if actor and actor.get("organization_id") else None,
        details, json.dumps(metadata, default=str), ip)


async def find_actor(conn, email: str) -> dict:
    row = await conn.fetchrow("SELECT to_jsonb(u) AS row FROM users u WHERE lower(email) = lower($1)"
                              " AND status = 'ACTIVE'", email)
    if row is None:
        raise SyncError(f"no hay una cuenta activa con el correo indicado ({email})")
    return _j(row["row"])


async def actor_by_id(conn, user_id) -> dict:
    row = await conn.fetchrow("SELECT to_jsonb(u) AS row FROM users u WHERE id = $1", uuid.UUID(str(user_id)))
    if row is None:
        raise SyncError("la cuenta ya no existe", 401)
    return _j(row["row"])


def counts(rows: dict[str, list]) -> dict[str, int]:
    return {table: len(items) for table, items in rows.items()}


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def null_missing(row: dict, cols: list[str], keep: set[str]) -> dict:
    for col in cols:
        if row.get(col) and row[col] not in keep:
            row[col] = None
    return row


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------------------------
# Media the event's screens show (offline copy on the node)
# --------------------------------------------------------------------------------------------
MEDIA_BASES = ("https://media.adventist.club/",)
MEDIA_MAX_FILES = 3000


def media_urls(bundle: dict, bases: tuple[str, ...] = MEDIA_BASES) -> list[str]:
    """Every public media URL (R2 bucket: event logo, club logos, avatars…) the event's rows,
    its people and its clubs (+ their ancestors) reference. The node mirrors them under the same
    URL (its DNS answers media.adventist.club and Caddy serves the copy)."""
    found: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, str):
            if value.startswith(bases) and len(value) < 1000:
                found.add(value.split("#", 1)[0])
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(bundle.get("event"))
    ref = bundle.get("reference") or {}
    walk(ref.get("users"))
    events = (bundle.get("event") or {}).get("events") or [{}]
    wanted = {r.get("club_id") for r in (bundle.get("event") or {}).get("event_registrations", [])}
    wanted.add(events[0].get("organization_id"))
    orgs = {o["id"]: o for o in ref.get("organizations") or []}
    for oid in list(wanted):
        while oid and oid in orgs:
            wanted.add(oid)
            oid = orgs[oid].get("parent_id")
    walk([o for o in orgs.values() if o["id"] in wanted])
    return sorted(found)[:MEDIA_MAX_FILES]


# --------------------------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------------------------
def normalize_scope(scope: dict | None) -> dict:
    scope = dict(scope or {})
    ids = sorted({str(uuid.UUID(str(i))) for i in scope.get("activity_ids") or []})
    every = bool(scope.get("all")) or (not ids and scope.get("all") is None)
    return {"all": every, "activity_ids": [] if every else ids, "check_in": bool(scope.get("check_in", True))}


def expand_scope(scope: dict, activities: list[dict]) -> set[str] | None:
    """Activity ids the scope covers (a group covers its rounds). None = every activity."""
    if scope.get("all"):
        return None
    chosen = set(scope.get("activity_ids") or [])
    out = set(chosen)
    for a in activities:
        if a.get("parent_id") in chosen:
            out.add(a["id"])
    # A round chosen alone makes its group readable (the group scores nothing itself).
    return out


def scope_overlap(a: dict, b: dict, activities: list[dict]) -> set[str]:
    """Scorable activities two scopes share (groups excluded)."""
    scorable = {x["id"] for x in activities if x.get("kind") != "group"}
    ea, eb = expand_scope(a, activities), expand_scope(b, activities)
    ea = scorable if ea is None else ea & scorable
    eb = scorable if eb is None else eb & scorable
    return ea & eb


# --------------------------------------------------------------------------------------------
# Export (cloud side)
# --------------------------------------------------------------------------------------------
async def gather_export(prod, event_id: uuid.UUID, actor_email: str) -> dict:
    """Everything the node needs, read from ONE consistent snapshot of production."""
    event = await prod.fetchrow("SELECT id FROM events WHERE id = $1", event_id)
    if event is None:
        raise SyncError("el evento no existe en producción", 404)
    users = await rows_of(prod, USERS_SQL, event_id, actor_email)
    user_ids = {u["id"] for u in users}
    for u in users:
        u["birth_date"] = None  # not needed at the event; less personal data on the node
        # The second factor's seed never leaves the cloud: on the node everybody logs in with
        # the password only (an offline LAN of the event).
        u["mfa_secret"], u["mfa_enabled"] = None, False
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
    org_ids = {rows["events"][0]["organization_id"]} | {r["club_id"] for r in rows["event_registrations"]}
    org_ids |= {u.get("organization_id") for u in users} | {r.get("organization_id") for r in roles}
    org_ids |= {m.get("club_id") for m in memberships}
    organizations = await rows_of(prod, ORGS_SQL, [uuid.UUID(i) for i in org_ids if i])
    kept = {o["id"] for o in organizations}
    missing = sorted({ref for table, refs in USER_REFS.items() for item in rows[table] for col in refs
                      if (ref := item.get(col)) and ref not in user_ids})
    if missing:
        # Only a minor's account is never copied; an event row pointing at one needs a human.
        raise SyncError(f"filas del evento apuntan a {len(missing)} cuenta(s) de menores que no se copian: "
                        f"{', '.join(missing[:5])}")
    return {
        "format": EXPORT_FORMAT,
        "event_id": str(event_id),
        "exported_at": _now().isoformat(),
        "reference": {
            "ministries": await rows_of(prod, "SELECT to_jsonb(m) AS row FROM ministries m ORDER BY created_at, id"),
            "organizations": organizations,
            "organization_ministries": await rows_of(
                prod, "SELECT to_jsonb(x) AS row FROM organization_ministries x WHERE organization_id = ANY($1::uuid[])"
                      " ORDER BY organization_id, ministry_id", [uuid.UUID(i) for i in kept]),
            "users": users,
            "role_assignments": roles,
            "club_memberships": memberships,
        },
        "event": rows,
    }


def snapshot_of(rows: dict[str, list[dict]]) -> dict:
    """Fingerprints of every event row + the event row itself (field-level merge of `events`)."""
    snap: dict = {table: {r["id"]: fingerprint(r, table) for r in items} for table, items in rows.items()}
    snap[FORMAT_KEY] = SNAPSHOT_FORMAT
    snap[ROWS_KEY] = {"events": {r["id"]: r for r in rows.get("events", [])}}
    return snap


async def open_nodes(conn, event_id: uuid.UUID, *, exclude: uuid.UUID | None = None) -> list[dict]:
    return [_j(r["row"]) for r in await conn.fetch(
        "SELECT to_jsonb(n) - 'baseline' - 'node_private_enc' AS row FROM event_offline_nodes n"
        " WHERE event_id = $1 AND NOT local AND status = ANY($2::text[]) AND ($3::uuid IS NULL OR id <> $3)"
        " ORDER BY prepared_at", event_id, list(OPEN_STATUSES), exclude)]


def _labels(nodes: list[dict]) -> str | None:
    text = ", ".join(n["label"] for n in nodes)
    return (text[:117] + "…") if len(text) > 120 else (text or None)


async def refresh_lock(conn, event_id: uuid.UUID, actor: dict | None, *, via: str, ip: str | None = None) -> dict:
    """The cloud lock follows the open nodes: set while any is open, released when none is.
    Returns {"locked": bool, "released": bool, "open_nodes": n}."""
    nodes = await open_nodes(conn, event_id)
    event = await conn.fetchrow("SELECT offline_locked_at, offline_node_label FROM events WHERE id = $1 FOR UPDATE",
                                event_id)
    if nodes:
        label = _labels(nodes)
        if event["offline_locked_at"] is None:
            reason = "Evento operado sin internet en nodos locales"
            await conn.execute(
                "UPDATE events SET offline_locked_at = now(), offline_locked_by_id = $2, offline_lock_reason = $3,"
                " offline_node_label = $4 WHERE id = $1",
                event_id, uuid.UUID(actor["id"]) if actor else None, reason, label)
            await audit(conn, action="EVENT_OFFLINE_LOCK", entity_id=event_id, actor=actor, details=reason,
                        metadata={"node_label": label, "via": via}, ip=ip)
        elif event["offline_node_label"] != label:
            await conn.execute("UPDATE events SET offline_node_label = $2 WHERE id = $1", event_id, label)
        return {"locked": True, "released": False, "open_nodes": len(nodes)}
    if event["offline_locked_at"] is not None:
        await conn.execute(
            "UPDATE events SET offline_locked_at = NULL, offline_locked_by_id = NULL, offline_lock_reason = NULL,"
            " offline_node_label = NULL WHERE id = $1", event_id)
        await audit(conn, action="EVENT_OFFLINE_UNLOCK", entity_id=event_id, actor=actor,
                    details="Todos los nodos locales subieron sus datos (o se cerraron)",
                    metadata={"node_label": event["offline_node_label"], "via": via}, ip=ip)
        return {"locked": False, "released": True, "open_nodes": 0}
    return {"locked": False, "released": False, "open_nodes": 0}


async def prepare_node(prod, event_id: uuid.UUID, actor: dict, *, label: str, scope: dict,
                       sign_key: str | None, replace_node_id: uuid.UUID | None = None,
                       require_status: tuple[str, ...] | None = None, settle_seconds: float = 0.0,
                       via: str = "api", ip: str | None = None) -> tuple[dict, dict, uuid.UUID]:
    """Registers a node, locks the event in the cloud and reads the bundle.
    -> (bundle, node_row, export_id). Two transactions: lock first (then let in-flight writes
    finish), then ONE consistent read + the EXPORT record + the node row."""
    scope = normalize_scope(scope)
    node_id = uuid.uuid4()
    async with prod.transaction():
        event = await prod.fetchrow("SELECT id, name, status, offline_locked_at FROM events WHERE id = $1 FOR UPDATE",
                                    event_id)
        if event is None:
            raise SyncError("el evento no existe", 404)
        if require_status and event["status"] not in require_status:
            raise SyncError("Sólo se prepara un nodo con el evento abierto o en curso", 409)
        activities = await rows_of(prod, "SELECT to_jsonb(a) AS row FROM event_activities a WHERE event_id = $1",
                                   event_id)
        known = {a["id"] for a in activities}
        unknown = [i for i in scope["activity_ids"] if i not in known]
        if unknown:
            raise SyncError("Hay actividades que no son de este evento en el alcance del nodo", 422)
        if not scope["all"] and not scope["activity_ids"] and not scope["check_in"]:
            raise SyncError("El nodo debe capturar al menos una actividad o la mesa de llegadas", 422)
        if replace_node_id is not None:
            old = await prod.fetchrow("SELECT id, status, label FROM event_offline_nodes WHERE id = $1 AND event_id = $2"
                                      " AND NOT local FOR UPDATE", replace_node_id, event_id)
            if old is None:
                raise SyncError("El nodo a reemplazar no es de este evento", 404)
            if old["status"] in OPEN_STATUSES:
                if old["status"] == "PARTIAL":
                    raise SyncError(f"El nodo «{old['label']}» ya subió un avance: sube su final antes de volver a "
                                    "prepararlo, o pide a un administrador que lo cierre", 409)
                await prod.execute(
                    "UPDATE event_offline_nodes SET status = 'CLOSED', closed_at = now(), closed_by_id = $2,"
                    " close_reason = 'Reemplazado al volver a preparar el nodo', updated_at = now() WHERE id = $1",
                    replace_node_id, uuid.UUID(actor["id"]))
        for other in await open_nodes(prod, event_id):
            shared = scope_overlap(scope, normalize_scope(other["scope"]), activities)
            if shared:
                names = sorted(a["name"] for a in activities if a["id"] in shared)
                raise SyncError(f"El nodo «{other['label']}» ya captura: {', '.join(names[:6])}"
                                f"{'…' if len(names) > 6 else ''}. Elige otras actividades.", 409)
        # The node row first (status PREPARED, baseline filled below) so the lock sees it.
        await prod.execute(
            "INSERT INTO event_offline_nodes (id, event_id, label, coordinator_id, coordinator_email, scope, status,"
            " sign_key) VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'PREPARED', $7)",
            node_id, event_id, label, uuid.UUID(actor["id"]), actor["email"], json.dumps(scope), sign_key)
        locked_before = event["offline_locked_at"] is not None
        await refresh_lock(prod, event_id, actor, via=via, ip=ip)
    try:
        if not locked_before and settle_seconds:
            await asyncio.sleep(settle_seconds)
        export_id = uuid.uuid4()
        async with prod.transaction(isolation="repeatable_read"):
            bundle = await gather_export(prod, event_id, actor["email"])
            snapshot = snapshot_of(bundle["event"])
            bundle.update(node_label=label, actor_email=actor["email"])
            await prod.execute(
                "INSERT INTO event_offline_syncs (id, event_id, kind, node_label, actor_email, snapshot, report, node_id)"
                " VALUES ($1, $2, 'EXPORT', $3, $4, $5::jsonb, $6::jsonb, $7)",
                export_id, event_id, label, actor["email"], json.dumps(snapshot),
                json.dumps({"counts": counts(bundle["event"]), "users": len(bundle["reference"]["users"]),
                            "scope": scope, "via": via}), node_id)
            await prod.execute(
                "UPDATE event_offline_nodes SET export_id = $2, baseline = $3::jsonb, updated_at = now() WHERE id = $1",
                node_id, export_id, json.dumps(snapshot))
            await audit(prod, action="EVENT_OFFLINE_NODE_PREPARE", entity_id=event_id, actor=actor,
                        details=label, metadata={"node_id": str(node_id), "scope": scope, "via": via,
                                                 "export_id": str(export_id)}, ip=ip)
            node = await node_out(prod, node_id)
    except BaseException:
        # Nothing was handed to the node: forget it, and release the lock if it was the only one.
        async with prod.transaction():
            await prod.execute("DELETE FROM event_offline_nodes WHERE id = $1", node_id)
            await refresh_lock(prod, event_id, actor, via=via, ip=ip)
        raise
    bundle["node"] = {"id": str(node_id), "label": label, "scope": scope, "export_id": str(export_id)}
    return bundle, node, export_id


async def node_out(conn, node_id: uuid.UUID) -> dict:
    row = await conn.fetchrow(
        "SELECT n.*, u.name AS coordinator_name FROM event_offline_nodes n LEFT JOIN users u ON u.id = n.coordinator_id"
        " WHERE n.id = $1", node_id)
    if row is None:
        raise SyncError("Nodo no encontrado en este evento", 404)
    acts = {r["id"]: r["name"] for r in await conn.fetch("SELECT id::text AS id, name FROM event_activities"
                                                          " WHERE event_id = $1", row["event_id"])}
    scope = _j(row["scope"]) or {}
    return {
        "id": str(row["id"]), "event_id": str(row["event_id"]), "label": row["label"], "status": row["status"],
        "status_text": NODE_STATUS_ES.get(row["status"], row["status"]),
        "coordinator_email": row["coordinator_email"], "coordinator_name": row["coordinator_name"],
        "scope": scope,
        "activity_names": (["Todas las actividades"] if scope.get("all")
                           else [acts[i] for i in scope.get("activity_ids", []) if i in acts]),
        "prepared_at": row["prepared_at"], "last_upload_at": row["last_upload_at"], "final_at": row["final_at"],
        "uploads": row["uploads"], "conflicts": row["conflicts"], "close_reason": row["close_reason"],
        "closed_at": row["closed_at"], "signed": row["sign_key"] is not None,
    }


async def close_node(conn, event_id: uuid.UUID, node_id: uuid.UUID, actor: dict, reason: str, *,
                     via: str = "api", ip: str | None = None) -> tuple[dict, dict]:
    async with conn.transaction():
        row = await conn.fetchrow("SELECT id, status, label FROM event_offline_nodes WHERE id = $1 AND event_id = $2"
                                  " AND NOT local FOR UPDATE", node_id, event_id)
        if row is None:
            raise SyncError("Nodo no encontrado en este evento", 404)
        if row["status"] not in OPEN_STATUSES:
            raise SyncError(f"El nodo «{row['label']}» ya está {NODE_STATUS_ES[row['status']]}", 409)
        await conn.execute(
            "UPDATE event_offline_nodes SET status = 'CLOSED', closed_at = now(), closed_by_id = $2, close_reason = $3,"
            " updated_at = now() WHERE id = $1", node_id, uuid.UUID(actor["id"]), reason)
        await audit(conn, action="EVENT_OFFLINE_NODE_CLOSE", entity_id=event_id, actor=actor, details=reason,
                    metadata={"node_id": str(node_id), "label": row["label"], "previous": row["status"], "via": via},
                    ip=ip)
        lock = await refresh_lock(conn, event_id, actor, via=via, ip=ip)
        return await node_out(conn, node_id), lock


# --------------------------------------------------------------------------------------------
# Loading a bundle into the node
# --------------------------------------------------------------------------------------------
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


def reencrypt_passes(rows: list[dict], tokens: dict[str, str], encrypt) -> dict:
    """Every registration whose plain token is known (and matches its hash) gets its pass
    ciphertext re-made with `encrypt` (this side's secret). The hash never changes, so printed
    passes keep working. -> {"ok": n, "missing": n}."""
    ok = missing = 0
    for row in rows:
        if not row.get("pass_token_hash"):
            continue
        token = tokens.get(row["id"])
        if token and sha256_hex(token) == row["pass_token_hash"]:
            row["pass_token_enc"] = encrypt(token)
            ok += 1
        else:
            missing += 1
    return {"ok": ok, "missing": missing}


async def load_into_node(node, bundle: dict, *, replace: bool, local_logo: str | None, export_id: str,
                         pass_encrypt=None, local_node: dict | None = None) -> dict:
    """Writes the bundle into the node's database (one transaction). With `pass_encrypt` and the
    bundle's plain tokens (`bundle["passes"]`), passes are re-encrypted with the node's OWN secret.
    `local_node` = this node's row (030): id, label, scope, sign/import material."""
    event_id = uuid.UUID(bundle["event_id"])
    ref, rows = bundle["reference"], bundle["event"]
    passes = {"ok": 0, "missing": 0, "reencrypted": False}
    if pass_encrypt is not None and bundle.get("passes") is not None:
        passes.update(reencrypt_passes(rows["event_registrations"], bundle["passes"], pass_encrypt), reencrypted=True)
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
        sync_id = uuid.uuid4()
        node_meta = None
        if local_node is not None:
            node_meta = {"node_id": local_node["id"], "label": local_node["label"], "scope": local_node["scope"]}
        await node.execute(
            "INSERT INTO event_offline_syncs (id, event_id, kind, export_id, node_label, actor_email, snapshot, report,"
            " created_at) VALUES ($1, $2, 'NODE_COPY', NULL, $3, $4, $5::jsonb, $6::jsonb, $7)",
            sync_id, event_id, bundle.get("node_label"), bundle.get("actor_email"),
            json.dumps(snapshot_of(rows)),
            json.dumps({"prod_export_id": export_id, "counts": counts(rows), "node": node_meta}),
            datetime.fromisoformat(bundle["exported_at"]))
        if local_node is not None:
            await node.execute("DELETE FROM event_offline_nodes WHERE event_id = $1 AND local", event_id)
            await node.execute(
                "INSERT INTO event_offline_nodes (id, event_id, label, coordinator_email, scope, status, sign_key,"
                " local, node_private_enc, import_key, prepared_at) VALUES ($1, $2, $3, $4, $5::jsonb, 'PREPARED', $6,"
                " true, $7, $8, $9)",
                uuid.UUID(local_node["id"]), event_id, local_node["label"], bundle.get("actor_email"),
                json.dumps(local_node["scope"]), local_node.get("sign_key"), local_node.get("node_private_enc"),
                local_node.get("import_key"), datetime.fromisoformat(bundle["exported_at"]))
    return passes


async def local_node(conn, event_id: uuid.UUID | None = None) -> dict | None:
    """This database's own node row (only on a node)."""
    sql = ("SELECT to_jsonb(n) - 'baseline' AS row FROM event_offline_nodes n WHERE local"
           + (" AND event_id = $1" if event_id else "") + " ORDER BY prepared_at DESC LIMIT 1")
    row = await conn.fetchrow(sql, *([event_id] if event_id else []))
    return _j(row["row"]) if row else None


# --------------------------------------------------------------------------------------------
# The node's changes (node side)
# --------------------------------------------------------------------------------------------
async def gather_node_changes(node, event_id: uuid.UUID, *, pass_decrypt=None) -> dict:
    """What a node uploads: its event rows, the users they reference, its audit trail since the
    copy, and (with `pass_decrypt`) the plain pass tokens so the cloud re-encrypts them."""
    copy = await node.fetchrow("SELECT created_at, report FROM event_offline_syncs WHERE event_id = $1"
                               " AND kind = 'NODE_COPY' ORDER BY created_at DESC LIMIT 1", event_id)
    if copy is None:
        raise SyncError("el nodo no tiene una copia de este evento")
    report = _j(copy["report"]) or {}
    me = await local_node(node, event_id)
    rows = await event_rows(node, event_id)
    if not rows["events"]:
        raise SyncError("el nodo no tiene el evento")
    referenced = {row[col] for table, refs in USER_REFS.items() for row in rows[table] for col in refs
                  if row.get(col)}
    users = await rows_of(node, "SELECT to_jsonb(u) AS row FROM users u WHERE id = ANY($1::uuid[])"
                                " ORDER BY created_at, id", list(referenced))
    audits = await rows_of(node, "SELECT to_jsonb(a) AS row FROM audit_log a WHERE created_at >= $1"
                                 " AND entity_type = ANY($2::text[]) ORDER BY created_at, id",
                           copy["created_at"], list(AUDIT_ENTITIES))
    tokens = {}
    if pass_decrypt is not None:
        for r in rows["event_registrations"]:
            token = pass_decrypt(r.get("pass_token_enc"))
            if token and r.get("pass_token_hash") == sha256_hex(token):
                tokens[r["id"]] = token
    return {
        "format": CHANGES_FORMAT, "event_id": str(event_id),
        "node_id": me["id"] if me else (report.get("node") or {}).get("node_id"),
        "export_id": report.get("prod_export_id"),
        "copied_at": copy["created_at"].isoformat(),
        "created_at": _now().isoformat(),
        "event": rows, "users": users, "audit": audits, "passes": tokens if pass_decrypt is not None else None,
    }


# --------------------------------------------------------------------------------------------
# Planning an upload (cloud side)
# --------------------------------------------------------------------------------------------
async def plan_users(prod, users: list[dict], node_rows) -> tuple[dict, list[dict], list[str]]:
    """Users referenced by the node's rows that production does not have: created on the node.
    Same e-mail in production -> remap to that account; otherwise -> insert it."""
    referenced = {row[col] for table, refs in USER_REFS.items() for row in node_rows[table] for col in refs
                  if row.get(col)}
    if not referenced:
        return {}, [], []
    known = {str(r["id"]) for r in await prod.fetch("SELECT id FROM users WHERE id = ANY($1::uuid[])",
                                                    list(referenced))}
    remap, new_users, notes = {}, [], []
    for user in users:
        if user["id"] not in referenced or user["id"] in known:
            continue
        same = await prod.fetchval("SELECT id FROM users WHERE lower(email) = lower($1)", user["email"])
        if same:
            remap[user["id"]] = str(same)
            notes.append(f"cuenta creada en el nodo {user['email']} -> se usa la de producción")
        elif user.get("is_minor"):
            notes.append(f"cuenta de menor creada en el nodo ({user['id']}): no se sube")
        else:
            new_users.append(user)
            notes.append(f"cuenta creada en el nodo {user['email']} -> se crea en producción")
    return remap, new_users, notes


def apply_remap(node_rows: dict, remap: dict) -> None:
    for table, refs in USER_REFS.items():
        for row in node_rows[table]:
            for col in refs:
                if row.get(col) in remap:
                    row[col] = remap[row[col]]


def diff_fields(a: dict, b: dict, table: str, fmt: int = SNAPSHOT_FORMAT) -> list[str]:
    skip = _skip(table, fmt)
    return sorted(k for k in set(a) | set(b) if k not in skip and a.get(k) != b.get(k))


def _merge_event_row(base: dict, node: dict, prod: dict) -> tuple[dict, list[str]]:
    """Field-level three-way merge of the event row (two nodes may both move the status or bump
    the rules version). -> (merged, conflicting fields)."""
    skip = IGNORED.get("events", set())
    merged, conflicts = dict(prod), []
    for key in set(node) | set(prod):
        if key in skip:
            continue
        n, p, b = node.get(key), prod.get(key), base.get(key)
        if n == p or n == b:
            continue
        if p == b:
            merged[key] = n
        elif key in MAX_MERGE_FIELDS:
            merged[key] = max(n, p)
        else:
            conflicts.append(key)
    return merged, conflicts


class _Scope:
    def __init__(self, scope: dict | None, activities: list[dict]):
        self.scope = normalize_scope(scope) if scope is not None else None
        self.covered = expand_scope(self.scope, activities) if self.scope else None

    @property
    def partial(self) -> bool:
        return self.scope is not None and not self.scope["all"]

    def activity(self, activity_id) -> bool:
        return self.covered is None or activity_id in self.covered

    def check_in(self) -> bool:
        return self.scope is None or self.scope["check_in"]


def plan_changes(snapshot: dict, node_rows: dict, prod_rows: dict, *, prefer: str | None,
                 scope: dict | None = None, node_label: str = "el nodo") -> dict:
    """Three-way comparison per row: S = baseline, N = node now, P = production now.
      N == P                -> unchanged
      P == S, N != S        -> the node changed it -> apply (insert / update)
      N == S, P != S        -> only the cloud changed it (another node, or before the lock) -> keep, warn
      N != S, P != S, N!=P  -> CONFLICT (both changed) -> not applied unless `prefer`
                               (the event row is merged field by field first)
      only in node          -> new on the node -> insert
      in S and P, not in N  -> deleted on the node -> delete if P == S (else conflict)
      in S and N, not in P  -> deleted in the cloud: respected if the node did not change it
      only in P (not in S)  -> created in the cloud after the export (another node) -> keep
    Multi-node rules (030): a node only changes what its scope covers (anything else is a
    conflict «fuera del alcance»); two current check-ins of a club -> the earliest stays; an
    automatic on_checkin award the cloud already has -> skipped (granted once); the same
    (club, activity) captured on two nodes -> conflict (prefer node: the cloud's is voided)."""
    fmt = int(snapshot.get(FORMAT_KEY) or 1)
    base_rows = (snapshot.get(ROWS_KEY) or {}).get("events", {})
    activities = node_rows.get("event_activities") or prod_rows.get("event_activities") or []
    sc = _Scope(scope, activities)
    plan = {"insert": {}, "update": {}, "delete": {}, "conflicts": [], "cloud_only": [], "unchanged": {},
            "skipped": set()}
    eval_activity = {r["id"]: r.get("activity_id") for r in prod_rows["evaluations"]}
    eval_activity.update({r["id"]: r.get("activity_id") for r in node_rows["evaluations"]})

    def out_of_scope(table: str, row: dict) -> str | None:
        if sc.scope is None:
            return None
        if table == "event_check_ins" and not sc.check_in():
            return "llegada registrada en un nodo sin mesa de llegadas"
        if table == "evaluations" and not sc.activity(row.get("activity_id")):
            if row.get("captured_as") == "AUTO_CHECKIN" and sc.check_in():
                return None
            return "evaluación de una actividad fuera del alcance del nodo"
        if table == "evaluation_revisions" and not sc.activity(eval_activity.get(row.get("evaluation_id"))):
            return "revisión de una actividad fuera del alcance del nodo"
        if table == "event_adjustments" and row.get("activity_id") and not sc.activity(row["activity_id"]):
            return "ajuste de una actividad fuera del alcance del nodo"
        if table == "event_activities" and not sc.activity(row["id"]):
            return "regla de una actividad fuera del alcance del nodo"
        if sc.partial and table in EVENT_LEVEL_TABLES:
            return "configuración del evento cambiada en un nodo con alcance parcial"
        return None

    for table in EVENT_TABLE_NAMES:
        snap = snapshot.get(table, {})
        nodes = {r["id"]: r for r in node_rows[table]}
        prods = {r["id"]: r for r in prod_rows[table]}
        ins, upd, dele, same = [], [], [], 0

        def change(kind: str, row: dict, rid: str, item_extra: dict | None = None) -> None:
            why = out_of_scope(table, row)
            if why:
                plan["conflicts"].append({"table": table, "id": rid, "what": why, "scope": True,
                                          **(item_extra or {})})
                plan["skipped"].add(rid)
            else:
                (ins if kind == "insert" else upd).append(row)

        for rid, row in nodes.items():
            n = fingerprint(row, table, fmt)
            if rid not in prods:
                if rid in snap:  # deleted in the cloud after the export, still alive on the node
                    if n == snap[rid]:
                        plan["cloud_only"].append({"table": table, "id": rid,
                                                   "what": "borrada en la nube (se respeta)"})
                        continue
                    item = {"table": table, "id": rid, "what": "borrada en la nube, cambiada en el nodo"}
                    if prefer == "node":
                        change("insert", row, rid)
                        item["resolved"] = "node"
                        plan["cloud_only"].append(item)
                    else:
                        if prefer == "prod":
                            item["resolved"] = "prod"
                            plan["cloud_only"].append(item)
                        else:
                            plan["conflicts"].append(item)
                        plan["skipped"].add(rid)
                else:
                    change("insert", row, rid)
                continue
            p = fingerprint(prods[rid], table, fmt)
            s = snap.get(rid)
            if n == p:
                same += 1
            elif p == s:
                if table in INSERT_ONLY:
                    plan["conflicts"].append({"table": table, "id": rid, "what": "fila inmutable distinta"})
                    plan["skipped"].add(rid)
                else:
                    change("update", row, rid)
            elif n == s:
                plan["cloud_only"].append({"table": table, "id": rid, "what": "cambiada en la nube (otro nodo) y no en este: se conserva"})
            else:
                if table == "events" and rid in base_rows:
                    merged, fields = _merge_event_row(base_rows[rid], row, prods[rid])
                    if not fields:
                        upd.append(merged)
                        continue
                item = {"table": table, "id": rid, "what": "cambiada en la nube y en el nodo",
                        "fields": diff_fields(prods[rid], row, table, fmt)}
                if prefer == "node" and table not in INSERT_ONLY:
                    change("update", row, rid)
                    item["resolved"] = "node"
                    plan["cloud_only"].append(item)
                elif prefer == "prod":
                    item["resolved"] = "prod"
                    plan["cloud_only"].append(item)
                    plan["skipped"].add(rid)
                else:
                    plan["conflicts"].append(item)
                    plan["skipped"].add(rid)
        for rid, row in prods.items():
            if rid in nodes:
                continue
            if rid in snap:
                if fingerprint(row, table, fmt) == snap[rid]:
                    why = out_of_scope(table, row)
                    if why:
                        plan["conflicts"].append({"table": table, "id": rid, "what": why, "scope": True})
                    else:
                        dele.append(rid)
                else:
                    plan["conflicts"].append({"table": table, "id": rid,
                                              "what": "borrada en el nodo pero cambiada en la nube"})
            else:
                plan["cloud_only"].append({"table": table, "id": rid, "what": "llegó a la nube después de preparar este nodo (otro nodo): se conserva"})
        plan["insert"][table], plan["update"][table], plan["delete"][table] = ins, upd, dele
        plan["unchanged"][table] = same

    _merge_check_ins(plan, prod_rows, node_label)
    _merge_evaluations(plan, prod_rows, prefer, node_label)
    # A revision records the values an evaluation had BEFORE a change: while that evaluation is
    # held (conflict / skipped), its new revisions wait too, or the history would contradict it.
    held = {c["id"] for c in plan["conflicts"] if c["table"] == "evaluations"} | {
        i for i in plan["skipped"] if i in eval_activity}
    if held:
        waiting = [r for r in plan["insert"]["evaluation_revisions"] if r["evaluation_id"] in held
                   and not r.get("_generated")]
        plan["insert"]["evaluation_revisions"] = [r for r in plan["insert"]["evaluation_revisions"]
                                                  if r["evaluation_id"] not in held or r.get("_generated")]
        plan["cloud_only"].extend({"table": "evaluation_revisions", "id": r["id"],
                                   "what": "espera: su evaluación no se aplicó"} for r in waiting)
    return plan


def _merge_check_ins(plan: dict, prod_rows: dict, node_label: str) -> None:
    """Two nodes (or the cloud before the lock) checked the same club in: the EARLIEST current
    check-in stays; the other is kept as history, undone with a reason."""
    current = {r.get("registration_id"): r for r in prod_rows["event_check_ins"] if not r.get("undone_at")}
    now = _now().isoformat()
    for row in plan["insert"]["event_check_ins"]:
        if row.get("undone_at"):
            continue
        other = current.get(row["registration_id"])
        if other is None or other["id"] == row["id"]:
            continue
        if datetime.fromisoformat(row["checked_in_at"]) < datetime.fromisoformat(other["checked_in_at"]):
            loser = dict(other, undone_at=now, undone_by_id=None,
                         undo_reason=f"Llegada duplicada: se conserva la registrada antes en el nodo «{node_label}»")
            plan["update"]["event_check_ins"].append(loser)
            current[row["registration_id"]] = row
        else:
            row.update(undone_at=now, undone_by_id=None,
                       undo_reason="Llegada duplicada: el club ya había llegado (se conserva la más temprana)")
        plan["cloud_only"].append({"table": "event_check_ins", "id": row["id"],
                                   "what": "llegada registrada en dos nodos: se conserva la más temprana"})


def _merge_evaluations(plan: dict, prod_rows: dict, prefer: str | None, node_label: str) -> None:
    """A node inserts a CONFIRMED evaluation for a (club, activity) the cloud already has a
    different current one for: an automatic on_checkin award is granted once (skip); anything
    else was captured on two nodes -> conflict (prefer prod: skip; prefer node: void the cloud's)."""
    live = {(r.get("registration_id"), r.get("activity_id")): r for r in prod_rows["evaluations"]
            if r.get("status") == "CONFIRMED"}
    updated = {r["id"]: r for r in plan["update"]["evaluations"]}
    keep = []
    for row in plan["insert"]["evaluations"]:
        other = live.get((row.get("registration_id"), row.get("activity_id")))
        if row.get("status") != "CONFIRMED" or other is None or other["id"] == row["id"] \
                or not (row.get("registration_id") and row.get("activity_id")) \
                or (other["id"] in updated and updated[other["id"]].get("status") != "CONFIRMED"):
            keep.append(row)
            continue
        if row.get("captured_as") == "AUTO_CHECKIN":
            plan["skipped"].add(row["id"])
            plan["cloud_only"].append({"table": "evaluations", "id": row["id"],
                                       "what": "puntos automáticos por llegada ya otorgados en la nube"})
            continue
        item = {"table": "evaluations", "id": row["id"], "what": "capturada en dos nodos (mismo club y actividad)",
                "fields": ["points"], "other_id": other["id"]}
        if prefer == "node":
            now = _now().isoformat()
            plan["update"]["evaluations"].append(dict(other, status="VOID", revision=other["revision"] + 1,
                                                      updated_at=now))
            plan["insert"]["evaluation_revisions"].append({
                "id": str(uuid.uuid4()), "evaluation_id": other["id"], "revision": other["revision"],
                "inputs": other["inputs"], "points": other["points"], "status": other["status"],
                "rules_version": other["rules_version"], "judge_id": other.get("judge_id"), "action": "VOID",
                "reason": f"Capturada también en el nodo «{node_label}»: se conserva la del nodo",
                "changed_by_id": None, "idempotency_key": None, "created_at": now, "_generated": True})
            keep.append(row)
            item["resolved"] = "node"
            plan["cloud_only"].append(item)
        elif prefer == "prod":
            plan["skipped"].add(row["id"])
            item["resolved"] = "prod"
            plan["cloud_only"].append(item)
        else:
            plan["skipped"].add(row["id"])
            plan["conflicts"].append(item)
    plan["insert"]["evaluations"] = keep


async def plan_audit(prod, rows: list[dict], remap: dict) -> list[dict]:
    if not rows:
        return []
    have = {str(r["id"]) for r in await prod.fetch("SELECT id FROM audit_log WHERE id = ANY($1::uuid[])",
                                                   [uuid.UUID(r["id"]) for r in rows])}
    out = []
    for row in rows:
        if row["id"] in have:
            continue
        row = dict(row)
        if row.get("user_id") in remap:
            row["user_id"] = remap[row["user_id"]]
        row["metadata_json"] = {**(_j(row.get("metadata_json")) or {}), "via_nodo_local": True}
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
        rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in plan["insert"][table]]
        if table == "events" and rows:
            raise SyncError("el evento no existe en producción")
        await insert_rows(prod, table, rows)
    for table in reversed(EVENT_TABLE_NAMES):
        if plan["delete"][table]:
            await prod.execute(f"DELETE FROM {table} WHERE id = ANY($1::uuid[])",
                               [uuid.UUID(i) for i in plan["delete"][table]])
    if audits:
        known_users = {str(r["id"]) for r in await prod.fetch(
            "SELECT id FROM users WHERE id = ANY($1::uuid[])",
            [uuid.UUID(a["user_id"]) for a in audits if a.get("user_id")])}
        for a in audits:
            if a.get("user_id") and a["user_id"] not in known_users:
                a["user_id"] = None
        await insert_rows(prod, "audit_log", audits)


async def after_merge(prod, event_id: uuid.UUID) -> None:
    """The registration mirrors its current check-in (or none)."""
    await prod.execute(
        """
        UPDATE event_registrations r
           SET checked_in_at = c.checked_in_at, checked_in_by_id = c.checked_in_by_id, checked_in_as = c.checked_in_as
          FROM event_registrations r2
          LEFT JOIN event_check_ins c ON c.registration_id = r2.id AND c.undone_at IS NULL
         WHERE r.id = r2.id AND r.event_id = $1
           AND (r.checked_in_at, r.checked_in_by_id, r.checked_in_as)
               IS DISTINCT FROM (c.checked_in_at, c.checked_in_by_id, c.checked_in_as)
        """, event_id)


async def fix_passes(prod, plan: dict, before: dict[str, dict], tokens: dict | None, encrypt) -> dict:
    """After an upload, every registration the node inserted/updated must hold a pass ciphertext
    the CLOUD can open: re-encrypted from the plain token (when given), else the cloud's own
    previous one when the pass did not change, else none (regenerated once on first show)."""
    rows = plan["insert"]["event_registrations"] + plan["update"]["event_registrations"]
    out = {"reencrypted": 0, "kept": 0, "cleared": 0}
    for row in rows:
        token = (tokens or {}).get(row["id"])
        if not row.get("pass_token_hash"):
            continue
        if token and sha256_hex(token) == row["pass_token_hash"] and encrypt is not None:
            value, key = encrypt(token), "reencrypted"
        elif before.get(row["id"], {}).get("pass_token_hash") == row["pass_token_hash"]:
            value, key = before[row["id"]].get("pass_token_enc"), "kept"
        else:
            value, key = None, "cleared"
        await prod.execute("UPDATE event_registrations SET pass_token_enc = $2 WHERE id = $1",
                           uuid.UUID(row["id"]), value)
        out[key] += 1
    return out


def advance_baseline(baseline: dict, node_rows: dict, prod_after: dict, plan: dict) -> dict:
    """After an upload, what the cloud now holds FROM this node becomes its baseline: rows the
    node sent that were applied (or were already equal). Rows still in conflict, and rows only
    the cloud changed (another node), keep their old baseline so a later change on this node is
    still detected as a conflict. Fingerprints are taken from the cloud's rows after the merge,
    so a value that round-trips differently can never fake a conflict later."""
    held = {c["id"] for c in plan["conflicts"]}
    applied = {r["id"] for t in EVENT_TABLE_NAMES for r in plan["insert"][t] + plan["update"][t]}
    old_fmt = int(baseline.get(FORMAT_KEY) or 1)
    out: dict = {FORMAT_KEY: SNAPSHOT_FORMAT,
                 ROWS_KEY: {"events": dict((baseline.get(ROWS_KEY) or {}).get("events", {}))}}
    for table in EVENT_TABLE_NAMES:
        old = baseline.get(table, {}) if old_fmt == SNAPSHOT_FORMAT else {}
        after = {r["id"]: r for r in prod_after[table]}
        new = {}
        for row in node_rows[table]:
            rid = row["id"]
            cloud = after.get(rid)
            accepted = rid not in held and cloud is not None and (
                rid in applied or fingerprint(row, table) == fingerprint(cloud, table))
            if accepted:
                new[rid] = fingerprint(cloud, table)
                if table == "events":
                    out[ROWS_KEY]["events"][rid] = cloud
            elif rid in old:
                new[rid] = old[rid]
        out[table] = new
    return out


def build_report(plan: dict, *, event_id, node: dict | None, export_id, prefer, dry_run, final,
                 user_notes, new_users, audits, locked: bool) -> dict:
    return {
        "event_id": str(event_id),
        "node_id": node["id"] if node else None,
        "node_label": node["label"] if node else None,
        "export_id": str(export_id) if export_id else None,
        "dry_run": dry_run,
        "prefer": prefer,
        "final": final,
        "insert": {t: len(v) for t, v in plan["insert"].items() if v},
        "update": {t: len(v) for t, v in plan["update"].items() if v},
        "delete": {t: len(v) for t, v in plan["delete"].items() if v},
        "unchanged": {t: v for t, v in plan["unchanged"].items() if v},
        "conflicts": plan["conflicts"],
        "warnings": plan["cloud_only"],
        "users": user_notes,
        "new_users": len(new_users),
        "audit_rows": len(audits),
        "locked": locked,
        "lock_released": False,
        "already_applied": False,
    }


TABLE_ES = {"events": "evento", "event_activities": "actividad", "event_adjustment_types": "tipo de ajuste",
            "event_staff": "personal", "event_registrations": "inscripción", "event_check_ins": "llegada",
            "evaluations": "evaluación", "evaluation_revisions": "revisión", "event_adjustments": "ajuste"}


async def describe_items(prod, event_id: uuid.UUID, items: list[dict], node_rows: dict) -> None:
    """Adds a readable `detail` («Evaluación de «Señal» · Club Águilas: …») to each item."""
    if not items:
        return
    acts = {str(r["id"]): r["name"] for r in await prod.fetch(
        "SELECT id, name FROM event_activities WHERE event_id = $1", event_id)}
    clubs = {str(r["id"]): r["name"] for r in await prod.fetch(
        "SELECT r.id, o.name FROM event_registrations r JOIN organizations o ON o.id = r.club_id"
        " WHERE r.event_id = $1", event_id)}
    by_id = {}
    for table in EVENT_TABLE_NAMES:
        for r in node_rows.get(table, []):
            by_id[(table, r["id"])] = r
    evals = {r["id"]: r for r in node_rows.get("evaluations", [])}
    for item in items:
        row = by_id.get((item["table"], item["id"]), {})
        parts = [TABLE_ES.get(item["table"], item["table"]).capitalize()]
        activity = row.get("activity_id") or (evals.get(row.get("evaluation_id"), {}).get("activity_id"))
        if item["table"] == "event_activities":
            activity = item["id"]
        if activity and activity in acts:
            parts.append(f"«{acts[activity]}»")
        reg = row.get("registration_id") or (evals.get(row.get("evaluation_id"), {}).get("registration_id"))
        if item["table"] == "event_registrations":
            reg = item["id"]
        if reg and reg in clubs:
            parts.append(f"· {clubs[reg]}")
        item["detail"] = " ".join(parts) + f": {item['what']}"
        if item.get("fields"):
            item["detail"] += f" ({', '.join(item['fields'])})"


async def import_changes(prod, event_id: uuid.UUID, changes: dict, actor: dict, *, node_id: uuid.UUID | None,
                         prefer: str | None, dry_run: bool, final: bool = True, pass_encrypt=None,
                         payload_sha: str | None = None, via: str = "api", ip: str | None = None) -> dict:
    """Applies one node's upload (the whole merge happens in ONE transaction). Without `node_id`
    (a copy prepared by the 029 script): legacy single-node behaviour against the last EXPORT."""
    async with prod.transaction():
        lock = await prod.fetchrow("SELECT offline_locked_at, offline_node_label FROM events WHERE id = $1 FOR UPDATE",
                                   event_id)
        if lock is None:
            raise SyncError("el evento no existe", 404)
        node = None
        if node_id is not None:
            node = await prod.fetchrow("SELECT * FROM event_offline_nodes WHERE id = $1 AND event_id = $2 AND NOT local"
                                       " FOR UPDATE", node_id, event_id)
            if node is None:
                raise SyncError("Este nodo no está registrado para el evento (¿se preparó contra otra base?)", 404)
            node = dict(node)
            if node["status"] not in OPEN_STATUSES:
                last = _j(node["last_report"]) or {}
                if payload_sha and last.get("payload_sha") == payload_sha:
                    return {**last, "already_applied": True, "dry_run": dry_run}
                raise SyncError(f"El nodo «{node['label']}» ya está {NODE_STATUS_ES[node['status']]}"
                                + (f": {node['close_reason']}" if node.get("close_reason") else ""), 409)
            snapshot = _j(node["baseline"]) or {}
            export_id = node["export_id"]
            scope = _j(node["scope"])
            label = node["label"]
        else:
            export = await prod.fetchrow(
                "SELECT id, snapshot FROM event_offline_syncs WHERE event_id = $1 AND kind = 'EXPORT'"
                " ORDER BY created_at DESC LIMIT 1", event_id)
            if export is None:
                raise SyncError("producción no tiene un EXPORT de este evento: no hay contra qué comparar")
            snapshot, export_id, scope, label = _j(export["snapshot"]), export["id"], None, "el nodo"
        node_rows = changes["event"]
        prod_rows = await event_rows(prod, event_id)
        remap, new_users, notes = await plan_users(prod, changes.get("users") or [], node_rows)
        apply_remap(node_rows, remap)
        plan = plan_changes(snapshot, node_rows, prod_rows, prefer=prefer, scope=scope, node_label=label)
        audits = await plan_audit(prod, changes.get("audit") or [], remap)
        node_info = {"id": str(node["id"]), "label": node["label"]} if node else None
        report = build_report(plan, event_id=event_id, node=node_info, export_id=export_id, prefer=prefer,
                              dry_run=dry_run, final=final, user_notes=notes, new_users=new_users, audits=audits,
                              locked=lock["offline_locked_at"] is not None)
        await describe_items(prod, event_id, report["conflicts"] + report["warnings"], node_rows)
        report["node_status"] = node["status"] if node else None
        report["open_nodes"] = len(await open_nodes(prod, event_id))
        if dry_run:
            return report
        before = {r["id"]: r for r in prod_rows["event_registrations"]}
        await apply_plan(prod, plan, new_users, audits)
        await after_merge(prod, event_id)
        if pass_encrypt is not None or changes.get("passes") is not None:
            report["passes"] = await fix_passes(prod, plan, before, changes.get("passes"), pass_encrypt)
        status = None
        if node is not None:
            closing = final and not plan["conflicts"]
            status = "FINAL" if closing else "PARTIAL"
            report["node_status"] = status
            report["payload_sha"] = payload_sha
            await prod.execute(
                "UPDATE event_offline_nodes SET status = $2::varchar, uploads = uploads + 1, last_upload_at = now(),"
                " final_at = CASE WHEN $2::varchar = 'FINAL' THEN now() ELSE final_at END, conflicts = $3, baseline = $4::jsonb,"
                " last_report = $5::jsonb, updated_at = now() WHERE id = $1",
                node["id"], status, len(plan["conflicts"]),
                json.dumps(advance_baseline(snapshot, node_rows, await event_rows(prod, event_id), plan)),
                json.dumps(report, default=str))
            await audit(prod, action="EVENT_OFFLINE_NODE_UPLOAD", entity_id=event_id, actor=actor,
                        details=f"{label}: {'final' if status == 'FINAL' else 'avance'}",
                        metadata={"node_id": str(node["id"]), "final": final, "status": status,
                                  "conflicts": len(plan["conflicts"]), "via": via}, ip=ip)
            state = await refresh_lock(prod, event_id, actor, via=via, ip=ip)
            report["lock_released"], report["open_nodes"] = state["released"], state["open_nodes"]
        elif lock["offline_locked_at"] is not None and final and not plan["conflicts"]:
            # 029 copy: release unless 030 nodes are still out.
            state = await refresh_lock(prod, event_id, actor, via=via, ip=ip)
            report["lock_released"], report["open_nodes"] = state["released"], state["open_nodes"]
        await prod.execute(
            "INSERT INTO event_offline_syncs (id, event_id, kind, export_id, node_label, actor_email, report, node_id)"
            " VALUES ($1, $2, 'IMPORT', $3, $4, $5, $6::jsonb, $7)",
            uuid.uuid4(), event_id, export_id, label if node else lock["offline_node_label"], actor["email"],
            json.dumps(report, default=str), node["id"] if node else None)
        return report
