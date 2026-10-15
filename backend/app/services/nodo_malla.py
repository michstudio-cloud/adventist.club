"""«Nodos vecinos» (nodo-malla/1): nodes of the SAME event that share a LAN see each other's
progress without the cloud (030 multi-node; nodo-evento/README.md «Nodos vecinos»).

Who is who
  * Each node has an Ed25519 identity: the signing key bound at prepare time (`sign_key` of its
    `event_offline_nodes` row; a manual-mode node gets a mesh-only key here).
  * CLOUD VOUCHERS. At export the cloud signs every node's registration (event, node, label,
    scope, sign_key) with a key DERIVED from its secret (`nodo_crypto.vouch_private`) and puts in
    the sealed bundle: the vouch PUBLIC key, this node's voucher and the other open nodes (with
    theirs). A node prepared LATER presents its voucher and is accepted by the earlier ones.
  * PAIRING (fallback, e.g. a manual-mode node): a self-signed code «NCP1.<datos>.<firma>» shown
    as QR/text on one Mac and pasted on the other, plus a 6-digit fingerprint to compare.

Exchange (plain HTTP on the LAN; everything is signed and sealed at application level)
  GET  /nodo-malla/v1/hola    public identity, no data.
  POST /nodo-malla/v1/avance  signed request (X-Nodo-Firma) carrying an ephemeral X25519 key;
                              the answer is the responder's OWN captures in the cloud upload
                              format (nodo-cambios/1, without pass tokens nor account secrets),
                              sealed to that key and signed by the responder's node key.

Applying (READ-ONLY, conflict-free by construction). Only capture tables travel (check-ins,
evaluations, revisions, adjustments) — never activities, rules, staff or registrations: those
are event configuration and change only in the cloud. A row is applied only when the PEER owns
it under the cloud's own rules (`plan_changes` scope rules) and THIS node does not; a general
adjustment (no activity) belongs to every node and is applied as the sender's row. Anything that
would collide (same id with other content, a second current capture/check-in for the same club,
a row of another neighbour) is REPORTED («traslapes»), never applied. Received rows are recorded
in the node-only table `nodo_malla_filas` (with the ORIGINAL of a replaced base row): the node's
API refuses edits to them (event_nodes) and the cloud upload leaves them out
(`nodo_sync_core.without_mesh`).

Node-only storage: tables created lazily (CREATE TABLE IF NOT EXISTS) only on a database that
has a local node row; the production schema does not change.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

import asyncpg

from app.services import nodo_crypto as nc
from app.services import nodo_egreso
from app.services import nodo_sync_core as core

FORMAT = nc.MESH_FORMAT
CTX_REQUEST = "nodo-malla-pide/v1"
CTX_ANSWER = "nodo-malla-avance/v1"
CTX_PAIR = "nodo-malla-par/v1"
PAIR_PREFIX = "NCP1"
SKEW_SECONDS = 15 * 60
NODE_KEY_PURPOSE = "nodo-sign-key"  # = scripts/nodo_sync.py
MESH_KEY_PURPOSE = "nodo-malla-key"
# Apply order (foreign keys: an automatic award points at its check-in, a revision at its evaluation).
CAPTURE_TABLES = ["event_check_ins", "evaluations", "evaluation_revisions", "event_adjustments"]
SOURCES = ("paquete", "aval", "emparejado")
SOURCE_ES = {"paquete": "paquete de la nube", "aval": "aval de la nube", "emparejado": "emparejado a mano"}
UNKNOWN_NODE = "nodo desconocido: emparéjalo o vuelve a preparar con internet"
MAX_ANSWER_BYTES = 60_000_000

DDL = """
CREATE TABLE IF NOT EXISTS nodo_malla_conf (
  event_id uuid PRIMARY KEY,
  node_id uuid NOT NULL,
  vouch_key text,
  voucher text,
  sign_key text,
  private_enc text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS nodo_malla_pares (
  event_id uuid NOT NULL,
  node_id uuid NOT NULL,
  label text NOT NULL,
  scope jsonb NOT NULL,
  sign_key text NOT NULL,
  source varchar(12) NOT NULL CHECK (source IN ('paquete', 'aval', 'emparejado')),
  voucher text,
  url text,
  added_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz,
  last_sync_at timestamptz,
  last_error text,
  last_report jsonb NOT NULL DEFAULT '{}'::jsonb,
  revoked boolean NOT NULL DEFAULT false,
  PRIMARY KEY (event_id, node_id)
);
CREATE TABLE IF NOT EXISTS nodo_malla_filas (
  event_id uuid NOT NULL,
  table_name text NOT NULL,
  row_id uuid NOT NULL,
  peer_node_id uuid NOT NULL,
  fingerprint text NOT NULL,
  original jsonb,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (table_name, row_id)
);
CREATE INDEX IF NOT EXISTS nodo_malla_filas_peer_idx ON nodo_malla_filas (event_id, peer_node_id);
"""


class MeshError(Exception):
    """A reason, safe to show (Spanish). `code` = the HTTP status the server answers."""

    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(value) -> int | None:
    return int(value.timestamp()) if value else None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def huella(sign_key: str) -> str:
    """6 digits to compare on both screens when pairing («123 456»)."""
    digest = hashlib.sha256(nc.unb64(sign_key, size=32, what="sign_key")).digest()
    n = int.from_bytes(digest[:8], "big") % 1_000_000
    return f"{n // 1000:03d} {n % 1000:03d}"


# --------------------------------------------------------------------------------------------
# Cloud side: vouchers in the bundle
# --------------------------------------------------------------------------------------------
def voucher_for(vouch_priv: bytes, event_id, node: dict) -> str:
    return nc.sign_voucher(vouch_priv, event_id, node["id"], node["label"], core.normalize_scope(node["scope"]),
                           node["sign_key"])


async def cloud_mesh_block(conn, secret: str, event_id: uuid.UUID, node: dict) -> dict | None:
    """The bundle's `mesh` block for `node` (id, label, scope, sign_key): the vouch key, this
    node's voucher and every OTHER open node of the event that has a signing key."""
    if not secret or not node.get("sign_key"):
        return None
    vpriv = nc.vouch_private(secret)
    peers = []
    for other in await core.open_nodes(conn, event_id, exclude=uuid.UUID(str(node["id"]))):
        if not other.get("sign_key"):
            continue
        item = {"node_id": other["id"], "label": other["label"], "scope": core.normalize_scope(other["scope"]),
                "sign_key": other["sign_key"], "prepared_at": other.get("prepared_at")}
        item["voucher"] = voucher_for(vpriv, event_id, {**other, "scope": item["scope"]})
        peers.append(item)
    return {"format": FORMAT, "vouch_key": nc.b64(nc.sign_public_of(vpriv)),
            "voucher": voucher_for(vpriv, event_id, node), "peers": peers}


# --------------------------------------------------------------------------------------------
# Node storage
# --------------------------------------------------------------------------------------------
async def ensure_tables(conn) -> None:
    if await core.has_mesh(conn) and await conn.fetchval("SELECT to_regclass('public.nodo_malla_conf') IS NOT NULL"):
        return
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(7243001)")  # two processes may race here
        await conn.execute(DDL)


async def store_bundle_mesh(conn, event_id: uuid.UUID, mesh: dict | None) -> dict:
    """At prepare (nodo_sync.py): the vouch key, this node's voucher and the peers the cloud
    listed. -> {"peers": n, "rejected": n}. `load_into_node` already emptied the old mesh data."""
    me = await core.local_node(conn, event_id)
    if me is None:
        raise MeshError("este nodo no tiene su registro local")
    await ensure_tables(conn)
    out = {"peers": 0, "rejected": 0}
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO nodo_malla_conf (event_id, node_id, vouch_key, voucher) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (event_id) DO UPDATE SET node_id = EXCLUDED.node_id, vouch_key = EXCLUDED.vouch_key,"
            " voucher = EXCLUDED.voucher, updated_at = now()",
            event_id, uuid.UUID(me["id"]), (mesh or {}).get("vouch_key"), (mesh or {}).get("voucher"))
        if not mesh:
            return out
        for peer in mesh.get("peers") or []:
            ident = {**peer, "event_id": str(event_id)}
            try:
                nc.verify_voucher(mesh["vouch_key"], peer.get("voucher") or "", event_id, peer["node_id"],
                                  peer["label"], core.normalize_scope(peer["scope"]), peer["sign_key"])
            except (nc.CryptoError, KeyError):
                out["rejected"] += 1
                continue
            await _upsert_peer(conn, event_id, ident, "paquete")
            out["peers"] += 1
    return out


async def _upsert_peer(conn, event_id: uuid.UUID, ident: dict, source: str, url: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO nodo_malla_pares (event_id, node_id, label, scope, sign_key, source, voucher, url)"
        " VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8) ON CONFLICT (event_id, node_id) DO UPDATE SET"
        " label = EXCLUDED.label, scope = EXCLUDED.scope, sign_key = EXCLUDED.sign_key, source = EXCLUDED.source,"
        " voucher = EXCLUDED.voucher, url = COALESCE(EXCLUDED.url, nodo_malla_pares.url), revoked = false",
        event_id, uuid.UUID(str(ident["node_id"])), ident["label"], json.dumps(core.normalize_scope(ident["scope"])),
        ident["sign_key"], source, ident.get("voucher"), url)


# --------------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------------
async def me(conn, secret: str, event_id: uuid.UUID | None = None) -> dict:
    """This node: {event_id, node_id, label, scope, sign_key, voucher, vouch_key, private}."""
    local = await core.local_node(conn, event_id)
    if local is None:
        raise MeshError("este nodo todavía no tiene un evento preparado", 503)
    await ensure_tables(conn)
    eid = uuid.UUID(local["event_id"])
    conf = await conn.fetchrow("SELECT * FROM nodo_malla_conf WHERE event_id = $1", eid)
    if conf is not None and str(conf["node_id"]) != local["id"]:
        conf = None  # stale (another preparation)
    if local.get("node_private_enc") and local.get("sign_key"):
        sign_key = local["sign_key"]
        private = nc.unbox(secret, NODE_KEY_PURPOSE, local["node_private_enc"])
    elif conf is not None and conf["sign_key"] and conf["private_enc"]:
        sign_key, private = conf["sign_key"], nc.unbox(secret, MESH_KEY_PURPOSE, conf["private_enc"])
    else:
        # A manual-mode node (029/030 without the API): a mesh-only identity, paired by hand.
        private, public = nc.new_sign_keypair()
        sign_key = nc.b64(public)
        await conn.execute(
            "INSERT INTO nodo_malla_conf (event_id, node_id, sign_key, private_enc) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (event_id) DO UPDATE SET node_id = EXCLUDED.node_id, sign_key = EXCLUDED.sign_key,"
            " private_enc = EXCLUDED.private_enc, vouch_key = NULL, voucher = NULL, updated_at = now()",
            eid, uuid.UUID(local["id"]), sign_key, nc.box(secret, MESH_KEY_PURPOSE, private))
        conf = await conn.fetchrow("SELECT * FROM nodo_malla_conf WHERE event_id = $1", eid)
    return {"event_id": str(eid), "node_id": local["id"], "label": local["label"],
            "scope": core.normalize_scope(local.get("scope")), "sign_key": sign_key,
            "voucher": conf["voucher"] if conf is not None else None,
            "vouch_key": conf["vouch_key"] if conf is not None else None, "private": private}


def public_identity(me_: dict) -> dict:
    return {"format": FORMAT, "event_id": me_["event_id"], "node_id": me_["node_id"], "label": me_["label"],
            "scope": me_["scope"], "sign_key": me_["sign_key"], "voucher": me_["voucher"]}


async def activities(conn, event_id) -> list[dict]:
    return await core.rows_of(conn, "SELECT to_jsonb(a) AS row FROM event_activities a WHERE event_id = $1"
                                    " ORDER BY (parent_id IS NOT NULL), position, id", uuid.UUID(str(event_id)))


def activity_names(scope: dict, acts: list[dict]) -> list[str]:
    if scope.get("all"):
        return ["Todas las actividades"]
    names = {a["id"]: a["name"] for a in acts}
    return [names[i] for i in scope.get("activity_ids") or [] if i in names]


async def peers(conn, event_id, *, revoked: bool = False) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM nodo_malla_pares WHERE event_id = $1 AND ($2 OR NOT revoked)"
                            " ORDER BY added_at, node_id", uuid.UUID(str(event_id)), revoked)
    out = []
    for r in rows:
        item = dict(r)
        item["node_id"] = str(r["node_id"])
        item["scope"] = core._j(r["scope"])
        item["last_report"] = core._j(r["last_report"]) or {}
        out.append(item)
    return out


async def peer(conn, event_id, node_id) -> dict | None:
    try:
        nid = uuid.UUID(str(node_id))
    except ValueError:
        return None
    for item in await peers(conn, event_id, revoked=True):
        if item["node_id"] == str(nid):
            return item
    return None


def check_vouched(me_: dict, ident: dict) -> None:
    """A cloud voucher for `ident`, verified with the vouch key this node got from the same cloud."""
    if not me_.get("vouch_key"):
        raise MeshError("este nodo no tiene la llave de avales de la nube (se preparó sin internet): "
                        + "empareja a mano", 403)
    if not ident.get("voucher"):
        raise MeshError(UNKNOWN_NODE, 403)
    try:
        nc.verify_voucher(me_["vouch_key"], ident["voucher"], me_["event_id"], ident["node_id"], ident["label"],
                          core.normalize_scope(ident["scope"]), ident["sign_key"])
    except (nc.CryptoError, KeyError, ValueError) as exc:
        raise MeshError(f"{UNKNOWN_NODE} ({exc})", 403) from exc


async def admit(conn, me_: dict, ident: dict, source: str, *, url: str | None = None) -> dict:
    """Adds (or re-enables) a neighbour after its identity was verified by the caller. Refuses
    another event, this node itself, and a scope that overlaps this node or a known neighbour."""
    if ident.get("event_id") != me_["event_id"]:
        raise MeshError("ese nodo es de otro evento", 403)
    try:
        node_id = str(uuid.UUID(str(ident.get("node_id"))))
        nc.check_sign_public(ident.get("sign_key") or "")
        scope = core.normalize_scope(ident.get("scope"))
        label = " ".join(str(ident.get("label") or "").split())[:120]
    except (ValueError, TypeError, nc.CryptoError) as exc:
        raise MeshError(f"identidad del nodo inválida ({exc})", 422) from exc
    if not label:
        raise MeshError("identidad del nodo inválida (sin nombre)", 422)
    if node_id == me_["node_id"]:
        raise MeshError("es este mismo nodo", 409)
    acts = await activities(conn, me_["event_id"])
    shared = core.scope_overlap(me_["scope"], scope, acts)
    if shared:
        names = sorted(a["name"] for a in acts if a["id"] in shared)
        raise MeshError(f"«{label}» captura actividades de este nodo: {', '.join(names[:6])}", 409)
    known = await peer(conn, me_["event_id"], node_id)
    if known is not None and not known["revoked"] and known["sign_key"] != ident["sign_key"]:
        raise MeshError(f"«{known['label']}» ya se conoce con otra llave: «olvídalo» primero", 409)
    for other in await peers(conn, me_["event_id"]):
        if other["node_id"] == node_id:
            continue
        shared = core.scope_overlap(other["scope"], scope, acts)
        if shared:
            raise MeshError(f"«{label}» se traslapa con el vecino «{other['label']}»", 409)
    ident = {**ident, "node_id": node_id, "scope": scope, "label": label}
    await _upsert_peer(conn, uuid.UUID(me_["event_id"]), ident, source, url)
    return await peer(conn, me_["event_id"], node_id)


async def forget(conn, event_id, node_id) -> bool:
    """«Olvidar»: the neighbour is no longer trusted (it must be paired again). The rows it
    already sent stay (read-only); another neighbour may now send them (e.g. the same Mac
    prepared again under a new node id)."""
    result = await conn.execute("UPDATE nodo_malla_pares SET revoked = true WHERE event_id = $1 AND node_id = $2",
                                uuid.UUID(str(event_id)), uuid.UUID(str(node_id)))
    return result.endswith(" 1")


# --------------------------------------------------------------------------------------------
# Pairing code
# --------------------------------------------------------------------------------------------
def pairing_code(me_: dict) -> str:
    data = nc.canonical_json({k: me_[k] for k in ("event_id", "node_id", "label", "scope", "sign_key", "voucher")})
    signature = nc.unb64(nc.sign_context(me_["private"], CTX_PAIR, data))
    return f"{PAIR_PREFIX}.{b64url(data.encode())}.{b64url(signature)}"


def parse_pairing(code: str) -> dict:
    try:
        prefix, data, sig = "".join(code.split()).split(".")
        if prefix != PAIR_PREFIX:
            raise ValueError("prefijo")
        text = unb64url(data).decode()
        ident = json.loads(text)
        nc.verify_context(ident["sign_key"], nc.b64(unb64url(sig)), CTX_PAIR, text, what="la firma del código")
    except nc.CryptoError as exc:
        raise MeshError(f"código de emparejamiento alterado: {exc}", 422) from exc
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise MeshError("código de emparejamiento inválido", 422) from exc
    return ident


async def accept_pairing(conn, me_: dict, code: str) -> dict:
    ident = parse_pairing(code)
    known = await peer(conn, me_["event_id"], ident.get("node_id"))
    if known is not None and not known["revoked"] and known["sign_key"] == ident.get("sign_key") \
            and ident.get("event_id") == me_["event_id"]:
        return known  # already a neighbour with that key: nothing changes
    source = "emparejado"
    if ident.get("voucher") and me_.get("vouch_key"):
        try:
            check_vouched(me_, ident)
            source = "aval"
        except MeshError:
            source = "emparejado"
    return await admit(conn, me_, ident, source)


# --------------------------------------------------------------------------------------------
# Ownership (the cloud's scope rules, nodo_sync_core.plan_changes)
# --------------------------------------------------------------------------------------------
def owns(sc: core._Scope, table: str, row: dict, evals: dict) -> bool:
    if table == "event_check_ins":
        return sc.check_in()
    if table == "evaluations":
        return sc.activity(row.get("activity_id")) or (row.get("captured_as") == "AUTO_CHECKIN" and sc.check_in())
    if table == "evaluation_revisions":
        ev = evals.get(row.get("evaluation_id"))
        return ev is not None and owns(sc, "evaluations", ev, evals)
    if table == "event_adjustments":
        return not row.get("activity_id") or sc.activity(row["activity_id"])
    return False


def general(table: str, row: dict) -> bool:
    """Belongs to every node (an adjustment without activity)."""
    return table == "event_adjustments" and not row.get("activity_id")


# --------------------------------------------------------------------------------------------
# Responder: the avance
# --------------------------------------------------------------------------------------------
async def build_avance(conn, me_: dict) -> dict:
    """This node's OWN captures, cloud-upload format, without pass tokens, audit nor account
    secrets (users travel as {id, name} stubs for the foreign keys). Rows received via the mesh
    are never re-forwarded (gather_node_changes already leaves them out)."""
    eid = uuid.UUID(me_["event_id"])
    changes = await core.gather_node_changes(conn, eid)
    rows = changes["event"]
    sc = core._Scope(me_["scope"], rows["event_activities"])
    evals = {r["id"]: r for r in rows["evaluations"]}
    event = {t: [r for r in rows[t] if owns(sc, t, r, evals)] for t in CAPTURE_TABLES}
    refs = {r[c] for t in CAPTURE_TABLES for r in event[t] for c in core.USER_REFS.get(t, []) if r.get(c)}
    users = [{"id": u["id"], "name": u.get("name")} for u in changes["users"] if u["id"] in refs]
    return nodo_egreso.sanitize_changes({
        "format": core.CHANGES_FORMAT, "event_id": me_["event_id"], "node_id": me_["node_id"],
        "label": me_["label"], "scope": me_["scope"], "created_at": _now().isoformat(),
        "event": event, "users": users, "audit": [], "passes": None}, avance=True)


def check_clock(ts) -> int:
    try:
        stamp = int(ts)
    except (TypeError, ValueError) as exc:
        raise MeshError("petición sin hora válida", 403) from exc
    if abs(time.time() - stamp) > SKEW_SECONDS:
        raise MeshError("la hora no coincide (más de 15 min): revisa el reloj de las Macs", 403)
    return stamp


async def answer_avance(conn, secret: str, body: bytes, signature: str | None) -> dict:
    """POST /avance. Raises MeshError(403…) with the Spanish reason."""
    me_ = await me(conn, secret)
    try:
        req = json.loads(body)
        if not isinstance(req, dict):
            raise ValueError
    except ValueError as exc:
        raise MeshError("petición inválida", 400) from exc
    if req.get("event_id") != me_["event_id"]:
        raise MeshError("la petición es de otro evento", 403)
    stamp = check_clock(req.get("ts"))
    if not signature:
        raise MeshError("petición sin firma", 403)
    from_node = str(req.get("from_node") or "")
    known = await peer(conn, me_["event_id"], from_node)
    if known is not None and known["revoked"]:
        raise MeshError("este nodo «olvidó» al que pide: emparéjalos de nuevo", 403)
    if known is None:
        ident = req.get("yo") if isinstance(req.get("yo"), dict) else None
        if ident is None or str(ident.get("node_id")) != from_node:
            raise MeshError(UNKNOWN_NODE, 403)
        check_vouched(me_, ident)
        if not _ok_sig(ident.get("sign_key") or "", signature, body, stamp):
            _raise_sig()  # a valid voucher is public: the request must also be signed by that node
        known = await admit(conn, me_, ident, "aval")
    if not _ok_sig(known["sign_key"], signature, body, stamp):
        _raise_sig()
    try:
        epk = nc.check_box_public(req.get("epk") or "")
    except (nc.CryptoError, ValueError) as exc:
        raise MeshError("petición sin llave efímera válida", 400) from exc
    avance = await build_avance(conn, me_)
    sobre = nc.seal(avance, epk, me_["event_id"])
    ts = int(time.time())
    firma = nc.sign_context(me_["private"], CTX_ANSWER, sha256_hex(nc.canonical_json(sobre).encode()), ts,
                            from_node, req["epk"])
    await conn.execute("UPDATE nodo_malla_pares SET last_seen_at = now() WHERE event_id = $1 AND node_id = $2",
                       uuid.UUID(me_["event_id"]), uuid.UUID(from_node))
    return {"format": FORMAT, "event_id": me_["event_id"], "from_node": me_["node_id"], "ts": ts,
            "sobre": sobre, "firma": firma}


def _ok_sig(sign_key: str, signature: str, body: bytes, stamp: int) -> bool:
    try:
        nc.verify_context(sign_key, signature, CTX_REQUEST, sha256_hex(body), stamp)
        return True
    except nc.CryptoError:
        return False


def _raise_sig():
    raise MeshError("firma inválida: la petición no la firmó ese nodo", 403)


# --------------------------------------------------------------------------------------------
# Requester: one pull
# --------------------------------------------------------------------------------------------
def build_request(me_: dict) -> tuple[bytes, dict, bytes]:
    """-> (body, headers, ephemeral private)."""
    eph_private, eph_public = nc.new_box_keypair()
    ts = int(time.time())
    body = json.dumps({"event_id": me_["event_id"], "from_node": me_["node_id"], "ts": ts,
                       "epk": nc.b64(eph_public), "yo": public_identity(me_)}, separators=(",", ":")).encode()
    signature = nc.sign_context(me_["private"], CTX_REQUEST, sha256_hex(body), ts)
    return body, {"Content-Type": "application/json", "X-Nodo-Firma": signature}, eph_private


async def identify(conn, me_: dict, hola: dict, url: str | None) -> dict:
    """Who answered `hola`: a known neighbour (same key), or a new one with a cloud voucher."""
    if not isinstance(hola, dict) or hola.get("format") != FORMAT:
        raise MeshError("no contesta como un nodo del evento", 502)
    if hola.get("event_id") != me_["event_id"]:
        raise MeshError("ese nodo es de otro evento", 403)
    if str(hola.get("node_id")) == me_["node_id"]:
        raise MeshError("es este mismo nodo", 409)
    known = await peer(conn, me_["event_id"], hola.get("node_id"))
    if known is not None and known["revoked"]:
        raise MeshError(f"«{known['label']}» fue olvidado en este nodo: emparéjalo de nuevo", 403)
    if known is not None:
        if known["sign_key"] != hola.get("sign_key"):
            raise MeshError(f"«{known['label']}» contesta con otra llave: no se confía", 403)
        if url:
            await conn.execute("UPDATE nodo_malla_pares SET url = $3 WHERE event_id = $1 AND node_id = $2",
                               uuid.UUID(me_["event_id"]), uuid.UUID(known["node_id"]), url)
        return known
    check_vouched(me_, hola)
    return await admit(conn, me_, hola, "aval", url=url)


def open_answer(me_: dict, known: dict, answer: dict, eph_private: bytes, epk: str) -> dict:
    if not isinstance(answer, dict) or answer.get("format") != FORMAT or answer.get("event_id") != me_["event_id"]:
        raise MeshError("respuesta inválida del vecino", 502)
    if str(answer.get("from_node")) != known["node_id"]:
        raise MeshError("contestó otro nodo", 403)
    stamp = check_clock(answer.get("ts"))
    try:
        nc.verify_context(known["sign_key"], answer.get("firma") or "", CTX_ANSWER,
                          sha256_hex(nc.canonical_json(answer.get("sobre")).encode()), stamp, me_["node_id"], epk,
                          what="la firma del vecino")
        changes = nc.open_sealed(answer["sobre"], eph_private, me_["event_id"], max_bytes=MAX_ANSWER_BYTES)
    except nc.CryptoError as exc:
        raise MeshError(f"respuesta rechazada: {exc}", 403) from exc
    if changes.get("format") != core.CHANGES_FORMAT or changes.get("node_id") != known["node_id"] \
            or changes.get("event_id") != me_["event_id"]:
        raise MeshError("el avance no corresponde a ese nodo", 403)
    try:
        nodo_egreso.validate_changes(changes, avance=True)
    except nodo_egreso.EgresoError as exc:
        raise MeshError(f"avance rechazado: {exc}", 403) from exc
    return changes


async def record_error(conn, event_id, node_id, error: str | None) -> None:
    await conn.execute("UPDATE nodo_malla_pares SET last_error = $3 WHERE event_id = $1 AND node_id = $2",
                       uuid.UUID(str(event_id)), uuid.UUID(str(node_id)), error)


# --------------------------------------------------------------------------------------------
# Receiver: applying an avance
# --------------------------------------------------------------------------------------------
async def _stub_users(conn, table: str, row: dict, names: dict[str, str]) -> None:
    for col in core.USER_REFS.get(table, []):
        uid = row.get(col)
        if not uid:
            continue
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, name, role, status) VALUES ($1, $2, '!', $3, 'STUDENT',"
            " 'INACTIVE') ON CONFLICT DO NOTHING",
            uuid.UUID(uid), f"nodo-malla+{uid}@nodo.invalid", (names.get(uid) or "Cuenta de otro nodo")[:180])


async def _collision(conn, table: str, row: dict) -> str | None:
    rid = uuid.UUID(row["id"])
    if table == "evaluations":
        if row.get("status") == "CONFIRMED" and await conn.fetchval(
                "SELECT 1 FROM evaluations WHERE registration_id = $1 AND activity_id = $2 AND status = 'CONFIRMED'"
                " AND id <> $3", uuid.UUID(row["registration_id"]), uuid.UUID(row["activity_id"]), rid):
            return "el club ya tiene otra captura vigente de esa actividad en este nodo"
        if row.get("check_in_id") and not await conn.fetchval("SELECT 1 FROM event_check_ins WHERE id = $1",
                                                              uuid.UUID(row["check_in_id"])):
            return "su llegada no está en este nodo"
    if table == "event_check_ins" and not row.get("undone_at") and await conn.fetchval(
            "SELECT 1 FROM event_check_ins WHERE registration_id = $1 AND undone_at IS NULL AND id <> $2",
            uuid.UUID(row["registration_id"]), rid):
        return "el club ya tiene otra llegada vigente en este nodo"
    if table == "evaluation_revisions":
        if not await conn.fetchval("SELECT 1 FROM evaluations WHERE id = $1", uuid.UUID(row["evaluation_id"])):
            return "su evaluación no está en este nodo"
        if await conn.fetchval("SELECT 1 FROM evaluation_revisions WHERE evaluation_id = $1 AND revision = $2"
                               " AND id <> $3", uuid.UUID(row["evaluation_id"]), row.get("revision"), rid):
            return "ya hay otra revisión con ese número"
    if table in ("evaluations", "evaluation_revisions") and row.get("idempotency_key") and await conn.fetchval(
            f"SELECT 1 FROM {table} WHERE idempotency_key = $1 AND id <> $2", row["idempotency_key"], rid):
        return "otra fila de este nodo usa la misma clave de captura"
    return None


async def apply_avance(conn, me_: dict, known: dict, changes: dict) -> dict:
    """One neighbour's avance, in ONE transaction. Idempotent. -> report
    {"insertadas", "actualizadas", "sin_cambios", "traslapes": [{tabla, id, que}], "filas_recibidas"}."""
    eid = uuid.UUID(me_["event_id"])
    peer_id = known["node_id"]
    incoming = changes.get("event") or {}
    names = {u["id"]: u.get("name") for u in changes.get("users") or [] if u.get("id")}
    report = {"insertadas": 0, "actualizadas": 0, "sin_cambios": 0, "traslapes": []}
    items: list[dict] = []
    async with conn.transaction():
        local_rows = await core.event_rows(conn, eid)
        acts = local_rows["event_activities"]
        mine, theirs = core._Scope(me_["scope"], acts), core._Scope(known["scope"], acts)
        act_ids = {a["id"] for a in acts}
        regs = {r["id"] for r in local_rows["event_registrations"]}
        local = {t: {r["id"]: r for r in local_rows[t]} for t in CAPTURE_TABLES}
        evals = {**local["evaluations"], **{r["id"]: r for r in incoming.get("evaluations") or []}}
        copy = await conn.fetchrow("SELECT snapshot FROM event_offline_syncs WHERE event_id = $1 AND kind = 'NODE_COPY'"
                                   " ORDER BY created_at DESC LIMIT 1", eid)
        snapshot = core._j(copy["snapshot"]) if copy else {}
        filas = {(r["table_name"], str(r["row_id"])): r for r in await conn.fetch(
            "SELECT table_name, row_id, peer_node_id, fingerprint FROM nodo_malla_filas WHERE event_id = $1", eid)}
        known_peers = await peers(conn, eid, revoked=True)
        labels = {p["node_id"]: p["label"] for p in known_peers}
        # rows of a FORGOTTEN neighbour (e.g. the same Mac prepared again) may pass to another one
        released = {p["node_id"] for p in known_peers if p["revoked"]}
        check_ins_changed = False

        def overlap(table, row, what):
            items.append({"table": table, "id": row.get("id"), "what": what})

        for table in CAPTURE_TABLES:
            rows = [r for r in incoming.get(table) or [] if isinstance(r, dict) and r.get("id")]
            # updates before inserts (a void frees the «one current» slot a new capture takes)
            rows.sort(key=lambda r: r["id"] not in local[table])
            held = {i["id"] for i in items if i["table"] == "evaluations"}
            for row in rows:
                rid = row["id"]
                fp = core.fingerprint(row, table)
                if not owns(theirs, table, row, evals):
                    overlap(table, row, "el vecino no captura esto (fuera de su alcance): no se aplica")
                    continue
                mesh = filas.get((table, rid))
                if mesh is not None and str(mesh["peer_node_id"]) != peer_id \
                        and str(mesh["peer_node_id"]) not in released:
                    other = labels.get(str(mesh["peer_node_id"]), "otro vecino")
                    overlap(table, row, f"esta fila ya llegó de «{other}»: no se aplica")
                    continue
                current = local[table].get(rid)
                if current is not None and core.fingerprint(current, table) == fp:
                    if mesh is not None and str(mesh["peer_node_id"]) != peer_id:
                        await conn.execute("UPDATE nodo_malla_filas SET peer_node_id = $3 WHERE table_name = $1"
                                           " AND row_id = $2", table, uuid.UUID(rid), uuid.UUID(peer_id))
                    report["sin_cambios"] += 1
                    continue
                if table == "evaluation_revisions" and row.get("evaluation_id") in held:
                    # its evaluation was not applied: the history would contradict it (as the cloud)
                    overlap(table, row, "espera: su evaluación no se aplicó")
                    continue
                if mesh is None and owns(mine, table, row, evals) and not general(table, row):
                    overlap(table, row, "también la captura este nodo: no se aplica")
                    continue
                if row.get("registration_id") and row["registration_id"] not in regs:
                    overlap(table, row, "su inscripción no está en este nodo")
                    continue
                if row.get("activity_id") and row["activity_id"] not in act_ids:
                    overlap(table, row, "su actividad no está en este nodo")
                    continue
                original = None
                if current is not None:
                    if table in core.INSERT_ONLY:
                        overlap(table, row, "fila inmutable distinta en este nodo")
                        continue
                    if mesh is None:
                        base = (snapshot.get(table) or {}).get(rid)
                        if core.fingerprint(current, table) != base:
                            overlap(table, row, "este nodo también la cambió: no se reemplaza")
                            continue
                        original = current
                why = await _collision(conn, table, row)
                if why:
                    overlap(table, row, why)
                    continue
                try:
                    async with conn.transaction():  # savepoint: one bad row never spoils the rest
                        await _stub_users(conn, table, row, names)
                        if current is not None:
                            await core.update_row(conn, table, row)
                        else:
                            await core.insert_rows(conn, table, [row])
                        if mesh is None:
                            await conn.execute(
                                "INSERT INTO nodo_malla_filas (event_id, table_name, row_id, peer_node_id, fingerprint,"
                                " original) VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
                                eid, table, uuid.UUID(rid), uuid.UUID(peer_id), fp,
                                json.dumps(original) if original is not None else None)
                        else:
                            await conn.execute("UPDATE nodo_malla_filas SET fingerprint = $3, peer_node_id = $4,"
                                               " received_at = now() WHERE table_name = $1 AND row_id = $2",
                                               table, uuid.UUID(rid), fp, uuid.UUID(peer_id))
                except asyncpg.PostgresError as exc:
                    overlap(table, row, f"no se pudo aplicar ({type(exc).__name__})")
                    continue
                local[table][rid] = row
                report["actualizadas" if current is not None else "insertadas"] += 1
                check_ins_changed |= table == "event_check_ins"
        if check_ins_changed:
            # the registration mirrors its current check-in, as the cloud does after a merge
            await core.after_merge(conn, eid)
        await core.describe_items(conn, eid, items, {t: list(incoming.get(t) or []) for t in CAPTURE_TABLES})
        report["traslapes"] = [{"tabla": core.TABLE_ES.get(i["table"], i["table"]), "id": i["id"],
                                "que": i.get("detail") or i["what"]} for i in items]
        report["filas_recibidas"] = await conn.fetchval(
            "SELECT count(*) FROM nodo_malla_filas WHERE event_id = $1 AND peer_node_id = $2", eid, uuid.UUID(peer_id))
        await conn.execute(
            "UPDATE nodo_malla_pares SET last_sync_at = now(), last_seen_at = now(), last_error = NULL,"
            " last_report = $3::jsonb WHERE event_id = $1 AND node_id = $2",
            eid, uuid.UUID(peer_id), json.dumps(report, default=str))
    return report


# --------------------------------------------------------------------------------------------
# State for the Mac app
# --------------------------------------------------------------------------------------------
async def arrivals(conn, event_id, peer_id: str | None) -> int:
    """Current check-ins: this node's own (peer_id None) or those received from a neighbour."""
    if peer_id is None:
        return await conn.fetchval(
            "SELECT count(*) FROM event_check_ins c JOIN event_registrations r ON r.id = c.registration_id"
            " WHERE r.event_id = $1 AND c.undone_at IS NULL AND NOT EXISTS (SELECT 1 FROM nodo_malla_filas f"
            " WHERE f.table_name = 'event_check_ins' AND f.row_id = c.id AND f.original IS NULL)",
            uuid.UUID(str(event_id)))
    return await conn.fetchval(
        "SELECT count(*) FROM event_check_ins c JOIN nodo_malla_filas f ON f.table_name = 'event_check_ins'"
        " AND f.row_id = c.id WHERE f.event_id = $1 AND f.peer_node_id = $2 AND c.undone_at IS NULL",
        uuid.UUID(str(event_id)), uuid.UUID(peer_id))


async def state(conn, me_: dict, rejected: list[dict] | None = None) -> dict:
    acts = await activities(conn, me_["event_id"])
    vecinos = []
    for p in await peers(conn, me_["event_id"]):
        report = p["last_report"] or {}
        vecinos.append({
            "node_id": p["node_id"], "label": p["label"], "origen": p["source"],
            "origen_texto": SOURCE_ES.get(p["source"], p["source"]),
            "actividades": activity_names(p["scope"], acts), "llegadas": bool(p["scope"].get("check_in")),
            "url": p["url"], "ultimo_contacto": _epoch(p["last_seen_at"]), "ultima_sync": _epoch(p["last_sync_at"]),
            "filas_recibidas": await conn.fetchval(
                "SELECT count(*) FROM nodo_malla_filas WHERE event_id = $1 AND peer_node_id = $2",
                uuid.UUID(me_["event_id"]), uuid.UUID(p["node_id"])),
            "llegadas_recibidas": await arrivals(conn, me_["event_id"], p["node_id"]),
            "traslapes": report.get("traslapes") or [], "error": p["last_error"],
            "huella": huella(p["sign_key"])})
    return {
        "actualizado": int(time.time()),
        "yo": {"node_id": me_["node_id"], "label": me_["label"], "event_id": me_["event_id"],
               "actividades": activity_names(me_["scope"], acts), "llegadas": bool(me_["scope"].get("check_in")),
               "llegadas_registradas": await arrivals(conn, me_["event_id"], None),
               "huella": huella(me_["sign_key"]), "aval_nube": bool(me_.get("voucher"))},
        "vecinos": vecinos,
        "rechazados": list(rejected or []),
    }
