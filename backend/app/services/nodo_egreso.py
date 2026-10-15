"""What may leave an event node (nodo-evento/docs/EGRESO.md).

The node's upload to the cloud (`POST /events/{id}/offline/import`, `nodo_sync.py upload/import`)
and the «avances» between neighbour nodes (nodo_malla) carry ONLY event data, built from the
explicit allow-list below — never free-form dumps, config, environment or host data (process
lists, host names, MACs, home paths, tokens). Two sides:

  * `sanitize_changes` (node side, applied by `nodo_sync_core.gather_node_changes`): keeps the
    allowed keys/tables/columns, drops the node's audit rows that are not about the event, the
    audit IP (a phone on the event LAN, or this Mac) and scrubs the audit's free text.
  * `validate_changes` (receiving side: the import endpoint, the manual import, a neighbour's
    avance): REJECTS a bundle with an unknown key, table or column, an audit row with an IP or
    a foreign entity, or anything shaped like a secret/host identifier. Rejected, not cleaned:
    an older or tampered node must not be able to push extra data through.

The column lists mirror the database (a test compares them with the real schema, so a new
migration that adds a column fails until it is reviewed and added here).
"""
from __future__ import annotations

import json
import os
import re
import uuid

OCULTO = "[oculto]"

# --------------------------------------------------------------------------------------------
# The allow-list
# --------------------------------------------------------------------------------------------
EVENT_COLUMNS: dict[str, frozenset[str]] = {
    "events": frozenset((
        "id organization_id ministry_id name slug venue city starts_on ends_on status registration_closes_on "
        "rules_version honor_bands total_floor source_note template_of_id created_by_id created_at updated_at "
        "brand_logo_url brand_color brand_accent offline_locked_at offline_locked_by_id offline_lock_reason "
        "offline_node_label").split()),
    "event_activities": frozenset((
        "id event_id parent_id position name description kind max_points config status counts_to_total "
        "schedule_at created_at updated_at").split()),
    "event_adjustment_types": frozenset((
        "id event_id kind label amount_mode points max_points max_per_event max_per_club position active "
        "created_at updated_at").split()),
    "event_staff": frozenset("id event_id user_id role activity_id active created_by_id created_at ended_at ended_by_id".split()),
    "event_registrations": frozenset((
        "id event_id club_id status pass_token_hash finalist_flags tiebreak_rank registered_by_id created_at "
        "updated_at pass_token_enc checked_in_at checked_in_by_id checked_in_as").split()),
    "event_check_ins": frozenset((
        "id registration_id checked_in_at checked_in_by_id checked_in_as method undone_at undone_by_id "
        "undo_reason").split()),
    "evaluations": frozenset((
        "id registration_id activity_id inputs points breakdown rules_version status judge_id captured_as "
        "idempotency_key revision created_at updated_at check_in_id").split()),
    "evaluation_revisions": frozenset((
        "id evaluation_id revision inputs points status rules_version judge_id action reason changed_by_id "
        "idempotency_key created_at").split()),
    "event_adjustments": frozenset((
        "id registration_id activity_id kind adjustment_type_id points reason created_by_id approved_by_id "
        "approved_at voided_at voided_by_id void_reason created_at").split()),
}
# Accounts referenced by the node's rows (a judge account created on the node is created in the
# cloud with its password hash). Every column the node's users table has — never anything else.
USER_COLUMNS = frozenset((
    "id email password_hash name avatar_url role organization_id is_minor birth_date mfa_secret mfa_enabled "
    "verification_status child_protection_completed child_protection_completed_at status last_login "
    "legacy_mongo_id created_at updated_at club_approval club_approval_reason club_approval_at notify_progress "
    "leader_verified_until handle bio cover_url profile_visibility guardian_allows_avatar handle_changed_at "
    "onboarding_completed_at signature_url active_ministry_id active_club_id").split())
AUDIT_COLUMNS = frozenset((
    "id action entity_type entity_id user_id user_email user_role organization_id details metadata_json "
    "ip_address legacy_mongo_id created_at").split())
# = nodo_sync_core.AUDIT_ENTITIES (a test keeps them equal; no import: core imports this module).
AUDIT_ENTITIES = frozenset(("EVENT", "EVENT_ACTIVITY", "EVENT_ADJUSTMENT", "EVENT_ADJUSTMENT_TYPE", "EVENT_CHECK_IN",
                            "EVALUATION", "EVENT_REGISTRATION", "EVENT_STAFF"))
UPLOAD_KEYS = frozenset(("format", "event_id", "node_id", "export_id", "copied_at", "created_at",
                         "event", "users", "audit", "passes"))
# A neighbour's avance (nodo_malla.build_avance): only capture tables, users as {id, name} stubs,
# no audit, no passes.
AVANCE_KEYS = frozenset(("format", "event_id", "node_id", "label", "scope", "created_at",
                         "event", "users", "audit", "passes"))
AVANCE_TABLES = frozenset(("event_check_ins", "evaluations", "evaluation_revisions", "event_adjustments"))
AVANCE_USER_COLUMNS = frozenset(("id", "name"))
# Fields that legitimately hold secrets of the EVENT (hashes, sealed pass tokens): not scanned.
SECRET_FIELDS = {"users": {"password_hash", "mfa_secret"}, "event_registrations": {"pass_token_hash", "pass_token_enc"}}
# Metadata keys that describe a host, never the event: dropped on the node, refused on arrival.
HOST_METADATA_KEYS = frozenset(("ip", "ip_address", "client_ip", "remote_ip", "user_agent", "hostname", "host",
                                "mac", "mac_address", "env", "environ", "environment", "args", "argv", "cmd",
                                "command", "path", "home", "user", "username", "headers", "cookies"))
PASS_TOKEN_MAX = 256

# --------------------------------------------------------------------------------------------
# Secret / host-identifier patterns (same rules as nodo-evento/mac/depurar.pl and
# app/Depurador.swift; shared cases in nodo-evento/mac/pruebas/depurador/casos.txt)
# --------------------------------------------------------------------------------------------
_B64 = r"[A-Za-z0-9+/_\-]"
def _quoted(prefix: str, value: str) -> str:
    q = value[0] if value[:1] in ("'", '"') else ""
    return f"{prefix}{q}{OCULTO}{q}"


# (kind, compiled regex, replacement). Replacement functions get the match.
_RULES: list[tuple[str, re.Pattern, object]] = [
    ("bloque PEM privado", re.compile(r"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----.*?(?:-----END \1PRIVATE KEY-----|\Z)", re.S),
     lambda m: f"-----BEGIN {m[1]}PRIVATE KEY----- {OCULTO} -----END {m[1]}PRIVATE KEY-----"),
    ("cabecera con secreto", re.compile(
        r"\b(X-[A-Za-z0-9\-]*(?:Key|Token|Secret|Signature|Auth[A-Za-z]*)|Authorization|Proxy-Authorization|Cookie|Set-Cookie)"
        r"([ \t]*:[ \t]*)(?!\[oculto\])([^'\"\s][^'\"\r\n]*)", re.I),
     lambda m: f"{m[1]}{m[2]}{OCULTO}"),
    ("credencial en URL", re.compile(r"(\b[a-z][a-z0-9+.\-]*://)[^/\s:@'\"]*:[^/\s@'\"]+@", re.I), lambda m: f"{m[1]}{OCULTO}@"),
    ("Bearer/Basic", re.compile(r"\b(Bearer|Basic)[ \t]+(?!\[oculto\])(?=[A-Za-z0-9\-._~+/]*[0-9\-._~+/])[A-Za-z0-9\-._~+/]{12,}=*"),
     lambda m: f"{m[1]} {OCULTO}"),
    ("llave de Google (AIza)", re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), lambda m: OCULTO),
    ("token de Google (AQ.)", re.compile(r"\bAQ\.[0-9A-Za-z_\-.]{16,}"), lambda m: OCULTO),
    ("token OAuth de Google (ya29.)", re.compile(r"\bya29\.[0-9A-Za-z_\-.]{10,}"), lambda m: OCULTO),
    ("token de GitHub", re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})"), lambda m: OCULTO),
    ("llave sk-", re.compile(r"\bsk-[0-9A-Za-z_\-]{16,}"), lambda m: OCULTO),
    ("token de Slack", re.compile(r"\bxox[abposr]-[0-9A-Za-z\-]{10,}"), lambda m: OCULTO),
    ("llave de AWS", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), lambda m: OCULTO),
    ("pase del evento (evp_)", re.compile(r"\bevp_[A-Za-z0-9_\-]{16,}"), lambda m: OCULTO),
    ("JWT", re.compile(r"\beyJ[0-9A-Za-z_\-]{8,}(?:\.[0-9A-Za-z_\-]+){0,2}"), lambda m: OCULTO),
    ("contraseña del Wi-Fi (QR)", re.compile(r"(WIFI:[^\n]*?\bP:)((?:\\;|[^;\n])+)"), lambda m: f"{m[1]}{OCULTO}"),
    ("contraseña del Wi-Fi (uci)", re.compile(
        r"(\boption[ \t]+(?:key|password|sae_password|wpa_passphrase)[ \t]+|\.(?:key|password|sae_password|wpa_passphrase)=)"
        r"('[^'\n]*'|\"[^\"\n]*\"|[^\s'\"]+)"),
     lambda m: _quoted(m[1], m[2])),
    ("contraseña del Wi-Fi (MikroTik/wpa)", re.compile(
        r"\b(wpa2?-pre-shared-key|passphrase|psk|wpa_passphrase|sae_password)=(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"';]+)"),
     lambda m: f"{m[1]}={OCULTO}"),
    ("variable de entorno con secreto", re.compile(
        r"\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PWD|CREDENTIALS?|DSN|PRIVATE|AUTH)[A-Z0-9_]*)=(?!\[oculto\])"
        r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"']+)"),
     lambda m: f"{m[1]}={OCULTO}"),
    ("bandera con secreto", re.compile(
        r"((?<![\w-])--?(?:[A-Za-z0-9]+[-_])*(?:api[-_]?key|key|token|secret|password|passwd|pass|pwd|auth|credentials?)"
        r"(?:=|[ \t]+))(?!\[oculto\])(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"']+)", re.I),
     lambda m: f"{m[1]}{OCULTO}"),
    ("base64 junto a key/token/secret", re.compile(
        r"(\b(?:[A-Za-z0-9]+[-_])*(?:key|token|secret|passw(?:or)?d|credentials?|signature|auth(?:orization)?)s?\b[\"']?[ \t]*[:=]?[ \t]*[\"']?)"
        rf"({_B64}{{32,}}={{0,2}})", re.I),
     lambda m: f"{m[1]}{OCULTO}"),
]
_LINK_LOCAL = re.compile(r"\bfe80:[0-9A-Fa-f:]*", re.I)
_MAC = re.compile(r"(?<![0-9A-Fa-f:\-])([0-9A-Fa-f]{1,2})([:\-])([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})\2"
                  r"([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})(?![0-9A-Fa-f:\-])")
_USERS = re.compile(r"/Users/(?!USUARIO\b|Shared\b)[^/\s\"':]+")
_HOME = re.compile(r"/home/(?!USUARIO\b)[^/\s\"':]+")
# What a receiving side refuses outright (high-confidence tokens + host traces). «Bearer/Basic»
# and the base64/flag heuristics stay out: an activity may be called «Basic first-aid-course».
_TOKEN_KINDS = ("bloque PEM privado", "llave de Google (AIza)", "token de Google (AQ.)", "token OAuth de Google (ya29.)",
                "token de GitHub", "llave sk-", "token de Slack", "llave de AWS", "pase del evento (evp_)", "JWT",
                "cabecera con secreto",
                "variable de entorno con secreto", "contraseña del Wi-Fi (uci)",
                "contraseña del Wi-Fi (MikroTik/wpa)")
_HOST_TRACES = [("ruta con nombre de usuario", re.compile(r"/Users/(?!USUARIO\b|Shared\b)[A-Za-z][^/\s\"':]*/")),
                ("MAC", _MAC),
                ("IPv6 de enlace local", re.compile(r"\bfe80::[0-9A-Fa-f:]{2,}", re.I))]


def scrub_text(text: str, names: list[str] | tuple[str, ...] = ()) -> str:
    """Same output as depurar.pl / Depurador.swift (`names`: host/user names → MAC-NODO)."""
    if not text:
        return text
    for _, rx, repl in _RULES:
        text = rx.sub(repl, text)
    text = _LINK_LOCAL.sub(f"fe80::{OCULTO}", text)
    text = _MAC.sub(lambda m: f"xx{m[2]}xx{m[2]}xx{m[2]}xx{m[2]}{m[6]}{m[2]}{m[7]}", text)
    text = _USERS.sub("/Users/USUARIO", text)
    text = _HOME.sub("/home/USUARIO", text)
    for name in sorted({n for n in names if len(n) >= 3}, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", "MAC-NODO", text, flags=re.I)
    return text


def find_secrets(text: str) -> list[str]:
    """Kinds of secret/host trace present in `text` (never the value). Already-scrubbed text
    («[oculto]») does not count."""
    if not text:
        return []
    found = [kind for kind, rx, repl in _RULES if kind in _TOKEN_KINDS and rx.sub(repl, text) != text]
    found += [kind for kind, rx in _HOST_TRACES if rx.search(text)]
    return found


def host_names_from_env() -> list[str]:
    """This Mac's names (computer, local host, user, full name), passed by the motor in
    NODO_EQUIPO_NOMBRES («|»-separated) to the sync and mesh containers. Never stored."""
    return [n.strip() for n in os.environ.get("NODO_EQUIPO_NOMBRES", "").split("|") if len(n.strip()) >= 3]


def _scrub_value(value, names=()):
    if isinstance(value, str):
        return scrub_text(value, names)
    if isinstance(value, list):
        return [_scrub_value(v, names) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v, names) for k, v in value.items() if str(k).lower() not in HOST_METADATA_KEYS}
    return value


# --------------------------------------------------------------------------------------------
# Node side
# --------------------------------------------------------------------------------------------
def sanitize_changes(changes: dict, *, avance: bool = False, names: list[str] | tuple[str, ...] = ()) -> dict:
    """The upload (or avance) rebuilt from the allow-list. Never adds anything."""
    keys = AVANCE_KEYS if avance else UPLOAD_KEYS
    tables = AVANCE_TABLES if avance else EVENT_COLUMNS.keys()
    out = {k: v for k, v in changes.items() if k in keys and k not in ("event", "users", "audit", "passes")}
    event = changes.get("event") or {}
    out["event"] = {t: [{c: v for c, v in row.items() if c in EVENT_COLUMNS[t]} for row in event.get(t) or []]
                    for t in EVENT_COLUMNS if t in tables and t in event}
    user_cols = AVANCE_USER_COLUMNS if avance else USER_COLUMNS
    out["users"] = [{c: v for c, v in u.items() if c in user_cols} for u in changes.get("users") or []]
    audit = []
    if not avance:
        for row in changes.get("audit") or []:
            if row.get("entity_type") not in AUDIT_ENTITIES:
                continue
            row = {c: v for c, v in row.items() if c in AUDIT_COLUMNS}
            # The IP is a phone on the event LAN or this Mac: it means nothing in the cloud.
            row["ip_address"] = None
            if isinstance(row.get("details"), str):
                row["details"] = scrub_text(row["details"], names)
            meta = row.get("metadata_json")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except ValueError:
                    meta = {"texto": meta}
            if meta is not None:
                row["metadata_json"] = _scrub_value(meta, names)
            audit.append(row)
    out["audit"] = audit
    passes = changes.get("passes")
    out["passes"] = None if avance or passes is None else {
        str(k): v for k, v in passes.items() if isinstance(v, str) and len(v) <= PASS_TOKEN_MAX}
    return out


# --------------------------------------------------------------------------------------------
# Receiving side
# --------------------------------------------------------------------------------------------
class EgresoError(ValueError):
    """The bundle carries something that is not event data (Spanish, safe to show)."""


def _strings(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))


def validate_changes(changes, *, avance: bool = False) -> None:
    """Raises EgresoError listing (at most 5) reasons; never echoes a value."""
    problems: list[str] = []

    def no(msg: str) -> None:
        if msg not in problems:
            problems.append(msg)

    if not isinstance(changes, dict):
        raise EgresoError("el paquete del nodo no es un objeto")
    keys = AVANCE_KEYS if avance else UPLOAD_KEYS
    for k in changes:
        if k not in keys:
            no(f"clave desconocida «{str(k)[:40]}»")
    event = changes.get("event")
    if not isinstance(event, dict):
        no("falta «event»")
        event = {}
    allowed_tables = AVANCE_TABLES if avance else frozenset(EVENT_COLUMNS)
    for table, rows in event.items():
        if table not in allowed_tables:
            no(f"tabla no permitida «{str(table)[:40]}»")
            continue
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            no(f"«{table}» no es una lista de filas")
            continue
        for row in rows:
            for col in row:
                if col not in EVENT_COLUMNS[table]:
                    no(f"columna desconocida «{table}.{str(col)[:40]}»")
    users = changes.get("users") or []
    user_cols = AVANCE_USER_COLUMNS if avance else USER_COLUMNS
    if not isinstance(users, list) or not all(isinstance(u, dict) for u in users):
        no("«users» no es una lista de cuentas")
        users = []
    for u in users:
        for col in u:
            if col not in user_cols:
                no(f"dato de cuenta no permitido «{str(col)[:40]}»")
    audit = changes.get("audit") or []
    if not isinstance(audit, list) or not all(isinstance(a, dict) for a in audit):
        no("«audit» no es una lista")
        audit = []
    if avance and audit:
        no("un avance no lleva bitácora")
    for row in audit:
        for col in row:
            if col not in AUDIT_COLUMNS:
                no(f"columna de bitácora desconocida «{str(col)[:40]}»")
        if row.get("entity_type") not in AUDIT_ENTITIES:
            no("bitácora que no es del evento")
        if row.get("ip_address"):
            no("bitácora con IP (dato del equipo, no del evento)")
        meta = row.get("metadata_json")
        if isinstance(meta, dict) and any(str(k).lower() in HOST_METADATA_KEYS for k in meta):
            no("bitácora con datos del equipo (ip, host, ruta, entorno…)")
    passes = changes.get("passes")
    if passes is not None:
        if avance:
            no("un avance no lleva pases")
        elif not isinstance(passes, dict) or not all(
                isinstance(v, str) and len(v) <= PASS_TOKEN_MAX for v in passes.values()):
            no("«passes» con formato inválido")
        else:
            for k in passes:
                try:
                    uuid.UUID(str(k))
                except ValueError:
                    no("«passes» con una clave que no es una inscripción")
                    break
    # Anything shaped like a secret or a host trace, anywhere but the event's own secrets.
    for section, rows in (("event", None), ("users", users), ("audit", audit)):
        items = ([(t, r) for t, rs in event.items() if isinstance(rs, list) for r in rs if isinstance(r, dict)]
                 if section == "event" else [(section, r) for r in rows])
        for table, row in items:
            skip = SECRET_FIELDS.get(table, set())
            for path, text in _strings({k: v for k, v in row.items() if k not in skip}):
                for kind in find_secrets(text):
                    no(f"{kind} en «{table}.{path.split('.')[0].split('[')[0]}»")
    for k in ("label", "export_id", "copied_at", "created_at", "format"):
        if isinstance(changes.get(k), str):
            for kind in find_secrets(changes[k]):
                no(f"{kind} en «{k}»")
    if problems:
        extra = f" (y {len(problems) - 5} más)" if len(problems) > 5 else ""
        raise EgresoError("El paquete del nodo trae datos que no son del evento: " + "; ".join(problems[:5]) + extra)
