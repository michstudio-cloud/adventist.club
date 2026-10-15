"""«Nodos vecinos» (nodo-malla/1): two nodes of the same event exchange their progress on the LAN.

The cloud is the test database (real export endpoint); the two nodes are two REAL node databases
(scripts/db_bootstrap.sh) loaded with their sealed bundles like `nodo_sync.py prepare` does. The
HTTP between them goes through the real mesh Starlette app (in-process transport). Proved:
  * cloud vouchers: valid / tampered / other event; the bundle carries the mesh block and the
    nodes listing exposes sign key + fingerprint + voucher;
  * pairing codes: valid, tampered, other event, overlapping scope (self and a known neighbour)
    refused; «olvidar» stops the trust;
  * the avance endpoint refuses unsigned, unknown, wrongly signed, other-event and clock-skewed
    requests (403 with a Spanish reason);
  * applying: the neighbour's own rows appear (read-only, recorded in nodo_malla_filas) — a node
    prepared LATER is accepted by its cloud voucher; arrivals mirror the registration; an
    overlap is reported and never applied; a row of another neighbour is refused; a base row the
    owner changed is replaced keeping its original; users travel as inert stubs; re-applying
    changes nothing;
  * the cloud upload is unchanged: what each node uploads (and the cloud's dry-run plan) is the
    same with and without the neighbours' rows;
  * the node API refuses edits to rows received from a neighbour.
"""
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
import pytest_asyncio

from app.config import settings
from app.services import event_pass
from app.services import nodo_crypto as nc
from app.services import nodo_malla as malla
from app.services import nodo_malla_server as server
from app.services import nodo_sync_core as core
from tests.conftest import RUN, TEST_DATABASE_URL, module_factory, requires_db
from tests.test_nodo_api import API, Node, _exec, _fast, _ok, hdr, make_event, w  # noqa: F401 (fixtures)

factory = module_factory("nodomalla")
pytestmark = [requires_db, pytest.mark.skipif(shutil.which("psql") is None, reason="needs psql (db_bootstrap.sh)")]
BOOTSTRAP = Path(__file__).resolve().parent.parent / "scripts" / "db_bootstrap.sh"
SECRETS = {"a": "secreto-nodo-a-" + "a" * 20, "b": "secreto-nodo-b-" + "b" * 20}


def _url(name: str) -> str:
    parts = urlsplit(TEST_DATABASE_URL)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))


# --------------------------------------------------------------------------------------------
# Pure crypto
# --------------------------------------------------------------------------------------------
def test_voucher_sign_and_verify():
    vpriv = nc.vouch_private("secreto-nube")
    assert vpriv == nc.vouch_private("secreto-nube") != nc.vouch_private("otro")
    vkey = nc.vouch_public("secreto-nube")
    _, pub = nc.new_sign_keypair()
    scope = core.normalize_scope({"activity_ids": [str(uuid.uuid4())], "check_in": False})
    ev, node = str(uuid.uuid4()), str(uuid.uuid4())
    voucher = nc.sign_voucher(vpriv, ev, node, "Zona A", scope, nc.b64(pub))
    nc.verify_voucher(vkey, voucher, ev, node, "Zona A", scope, nc.b64(pub))
    # same scope in another key order still verifies (canonical JSON)
    nc.verify_voucher(vkey, voucher, ev, node, "Zona A", dict(reversed(list(scope.items()))), nc.b64(pub))
    bad = [(str(uuid.uuid4()), node, "Zona A", scope, nc.b64(pub)),  # other event
           (ev, node, "Zona B", scope, nc.b64(pub)),  # tampered label
           (ev, node, "Zona A", {**scope, "check_in": True}, nc.b64(pub)),  # tampered scope
           (ev, node, "Zona A", scope, nc.b64(nc.new_sign_keypair()[1]))]  # another key
    for args in bad:
        with pytest.raises(nc.CryptoError):
            nc.verify_voucher(vkey, voucher, *args)
    with pytest.raises(nc.CryptoError):  # another cloud
        nc.verify_voucher(nc.vouch_public("otra-nube"), voucher, ev, node, "Zona A", scope, nc.b64(pub))


# --------------------------------------------------------------------------------------------
# World: cloud event + two node databases
# --------------------------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def node_dbs():
    parts = urlsplit(TEST_DATABASE_URL)
    base = parts.path.lstrip("/")
    names = {"a": f"{base}_malla_a_{RUN}", "b": f"{base}_malla_b_{RUN}"}
    subprocess.run(["bash", str(BOOTSTRAP), "--create", _url(names["a"])], check=True, capture_output=True)
    admin = await core.connect(_url("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{names["b"]}" TEMPLATE "{names["a"]}"')
    finally:
        await admin.close()
    yield {k: _url(v) for k, v in names.items()}
    admin = await core.connect(_url("postgres"))
    try:
        for name in names.values():
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def load_node(dsn: str, mac: Node, secret: str) -> None:
    """What `nodo_sync.py prepare` does with the opened bundle."""
    bundle = mac.bundle
    info = bundle["node"]
    local = {"id": info["id"], "label": info["label"], "scope": info["scope"],
             "sign_key": nc.b64(mac.sign_public), "import_key": bundle["import_key"],
             "node_private_enc": nc.box(secret, malla.NODE_KEY_PURPOSE, mac.sign_private)}
    conn = await core.connect(dsn)
    try:
        await core.load_into_node(conn, bundle, replace=True, local_logo=None, export_id=info["export_id"],
                                  pass_encrypt=lambda t: event_pass.encrypt(t, secret=secret), local_node=local)
        await malla.store_bundle_mesh(conn, uuid.UUID(bundle["event_id"]), bundle.get("mesh"))
    finally:
        await conn.close()


class Net:
    """The LAN: http://a and http://b are the two nodes' mesh apps (in-process)."""

    def __init__(self, apps: dict):
        self.clients = {host: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")
                        for host, app in apps.items()}

    async def __call__(self, method, url, body=None, headers=None, timeout=30):
        parts = urlsplit(url)
        client = self.clients.get(parts.hostname)
        if client is None:
            raise malla.MeshError("no contesta (sin ruta)", 504)
        r = await client.request(method, parts.path, content=body, headers=headers or {})
        return r.status_code, r.json()

    async def close(self):
        for c in self.clients.values():
            await c.aclose()


@pytest_asyncio.fixture(scope="module")
async def mesh(client, w, node_dbs):  # noqa: F811
    # A base capture in the cloud BEFORE the nodes exist (activity B, club c): both copies have it.
    event = await make_event(client, w, "Malla")
    A, B, C = (event["acts"][k] for k in "ABC")
    base = await _ok(await client.post(f"{event['url']}/evaluations", headers=hdr(w["judge"]), json={
        "registration_id": event["regs"]["c"], "activity_id": B, "inputs": {"points": 10},
        "idempotency_key": f"m-{uuid.uuid4().hex}"}), 201)
    # Node A: «Señal» (on_checkin) + arrivals desk. Node B, prepared LATER: «Marcha», no desk.
    mac_a, mac_b = Node("Zona Señal"), Node("Zona Marcha")
    await mac_a.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [A], "check_in": True})
    await mac_b.prepare(client, event, hdr(w["coordinator2"]), {"activity_ids": [B], "check_in": False})
    await load_node(node_dbs["a"], mac_a, SECRETS["a"])
    await load_node(node_dbs["b"], mac_b, SECRETS["b"])
    apps = {"a": server.make_app(node_dbs["a"], SECRETS["a"]), "b": server.make_app(node_dbs["b"], SECRETS["b"])}
    net = Net(apps)
    world = {"event": event, "A": A, "B": B, "C": C, "base_eval": base["id"],
             "mac_a": mac_a, "mac_b": mac_b, "db": node_dbs, "net": net, "apps": apps}
    yield world
    await net.close()


async def _me(dsn: str, key: str) -> dict:
    conn = await core.connect(dsn)
    try:
        return await malla.me(conn, SECRETS[key])
    finally:
        await conn.close()


async def _q(dsn: str, sql: str, *args):
    conn = await core.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _gather(dsn: str, event_id: str) -> dict:
    conn = await core.connect(dsn)
    try:
        changes = await core.gather_node_changes(conn, uuid.UUID(event_id))
    finally:
        await conn.close()
    return {t: sorted(core.canon(r, t) for r in rows) for t, rows in changes["event"].items()}


async def _dry_run(dsn: str, node_id: str, event_id: str, actor_email: str) -> dict:
    """The cloud's plan for this node's upload (dry run: nothing written)."""
    node = await core.connect(dsn)
    cloud = await core.connect(TEST_DATABASE_URL)
    try:
        changes = await core.gather_node_changes(node, uuid.UUID(event_id))
        actor = await core.find_actor(cloud, actor_email)
        rep = await core.import_changes(cloud, uuid.UUID(event_id), changes, actor, node_id=uuid.UUID(node_id),
                                        prefer=None, dry_run=True)
    finally:
        await node.close()
        await cloud.close()
    return {k: rep[k] for k in ("insert", "update", "delete", "unchanged")} | {
        "conflicts": sorted((c["table"], c["id"], c["what"]) for c in rep["conflicts"])}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _capture(dsn, reg, activity, points, *, judge=None, captured_as="JUDGE", check_in_id=None) -> str:
    rid = uuid.uuid4()
    conn = await core.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO evaluations (id, registration_id, activity_id, inputs, points, rules_version, judge_id,"
            " captured_as, idempotency_key, created_at, updated_at, check_in_id) VALUES ($1, $2, $3, $4::jsonb, $5,"
            " (SELECT e.rules_version FROM events e JOIN event_activities a ON a.event_id = e.id WHERE a.id = $3),"
            " $6, $7, $8, $9, $9, $10)",
            rid, uuid.UUID(reg), uuid.UUID(activity), json.dumps({"points": points}), points,
            uuid.UUID(judge) if judge else None, captured_as, f"m-{uuid.uuid4().hex}", _now(),
            uuid.UUID(check_in_id) if check_in_id else None)
    finally:
        await conn.close()
    return str(rid)


async def _correct(dsn, eval_id, points) -> None:
    conn = await core.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO evaluation_revisions (id, evaluation_id, revision, inputs, points, status, rules_version,"
                " judge_id, action, reason, created_at) SELECT $1, id, revision, inputs, points, status, rules_version,"
                " judge_id, 'CORRECTION', 'corrección en el nodo', now() FROM evaluations WHERE id = $2",
                uuid.uuid4(), uuid.UUID(eval_id))
            await conn.execute("UPDATE evaluations SET points = $2, inputs = $3::jsonb, revision = revision + 1,"
                               " updated_at = now() WHERE id = $1", uuid.UUID(eval_id), points,
                               json.dumps({"points": points}))
    finally:
        await conn.close()


# --------------------------------------------------------------------------------------------
# Cloud: the mesh block
# --------------------------------------------------------------------------------------------
async def test_export_bundle_carries_the_mesh_block(client, w, mesh):  # noqa: F811
    a, b = mesh["mac_a"], mesh["mac_b"]
    vkey = nc.vouch_public(settings.nodo_sync_secret)
    block_a, block_b = a.bundle["mesh"], b.bundle["mesh"]
    assert block_a["format"] == block_b["format"] == "nodo-malla/1"
    assert block_a["vouch_key"] == block_b["vouch_key"] == vkey
    assert block_a["peers"] == []  # A was the first node
    assert [p["node_id"] for p in block_b["peers"]] == [a.id]
    peer = block_b["peers"][0]
    assert peer["label"] == "Zona Señal" and peer["sign_key"] == nc.b64(a.sign_public)
    nc.verify_voucher(vkey, peer["voucher"], mesh["event"]["id"], a.id, peer["label"], peer["scope"],
                      peer["sign_key"])
    info = b.bundle["node"]
    nc.verify_voucher(vkey, block_b["voucher"], mesh["event"]["id"], b.id, info["label"], info["scope"],
                      nc.b64(b.sign_public))
    # the coordination listing shows sign key, fingerprint and voucher (no secrets)
    listing = await _ok(await client.get(f"{API}/{mesh['event']['id']}/offline/nodes", headers=hdr(w["admin"])))
    items = {n["id"]: n for n in listing["nodes"]}
    assert items[a.id]["mesh"]["voucher"] == peer["voucher"]
    assert items[a.id]["mesh"]["huella"] == malla.huella(peer["sign_key"])
    assert "node_private_enc" not in json.dumps(listing) and "baseline" not in json.dumps(listing)


# --------------------------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------------------------
def _fake(event_id: str, scope: dict, label="Mac manual") -> dict:
    private, public = nc.new_sign_keypair()
    return {"event_id": event_id, "node_id": str(uuid.uuid4()), "label": label,
            "scope": core.normalize_scope(scope), "sign_key": nc.b64(public), "voucher": None, "private": private}


async def test_pairing_codes(mesh):
    eid, dsn = mesh["event"]["id"], mesh["db"]["a"]
    manual = _fake(eid, {"activity_ids": [mesh["C"]], "check_in": False})
    code = malla.pairing_code(manual)
    assert code.startswith("NCP1.") and malla.parse_pairing(code)["node_id"] == manual["node_id"]
    assert len(malla.huella(manual["sign_key"]).replace(" ", "")) == 6
    # tampered: the data no longer matches its self-signature
    prefix, data, sig = code.split(".")
    other = json.loads(malla.unb64url(data))
    other["label"] = "Impostor"
    with pytest.raises(malla.MeshError, match="alterado"):
        malla.parse_pairing(f"{prefix}.{malla.b64url(nc.canonical_json(other).encode())}.{sig}")
    with pytest.raises(malla.MeshError):
        malla.parse_pairing("NCP1.basura")
    conn = await core.connect(dsn)
    try:
        me_a = await malla.me(conn, SECRETS["a"])
        # overlaps this node (Señal) / another event: refused
        with pytest.raises(malla.MeshError, match="captura actividades de este nodo") as err:
            await malla.accept_pairing(conn, me_a, malla.pairing_code(
                _fake(eid, {"activity_ids": [mesh["A"]], "check_in": False})))
        assert err.value.code == 409
        with pytest.raises(malla.MeshError, match="otro evento"):
            await malla.accept_pairing(conn, me_a, malla.pairing_code(
                _fake(str(uuid.uuid4()), {"activity_ids": [mesh["C"]], "check_in": False})))
        peer = await malla.accept_pairing(conn, me_a, code)
        assert peer["source"] == "emparejado" and peer["label"] == "Mac manual"
        assert (await malla.accept_pairing(conn, me_a, code))["node_id"] == peer["node_id"]  # idempotent
        # overlaps a KNOWN neighbour («Mac manual» captures Fogata): refused
        with pytest.raises(malla.MeshError, match="se traslapa con el vecino «Mac manual»"):
            await malla.accept_pairing(conn, me_a, malla.pairing_code(
                _fake(eid, {"activity_ids": [mesh["C"]], "check_in": False}, "Otra Mac")))
        # a paired neighbour answering with another key is not trusted; «olvidar» stops the trust
        hola = {**malla.public_identity({**manual, "private": None}), "sign_key": nc.b64(nc.new_sign_keypair()[1])}
        with pytest.raises(malla.MeshError, match="otra llave"):
            await malla.identify(conn, me_a, hola, None)
        assert await malla.forget(conn, eid, manual["node_id"])
        with pytest.raises(malla.MeshError, match="olvidado"):
            await malla.identify(conn, me_a, malla.public_identity({**manual, "private": None}), None)
    finally:
        await conn.close()


# --------------------------------------------------------------------------------------------
# Request authentication
# --------------------------------------------------------------------------------------------
async def test_avance_refuses_unsigned_unknown_and_foreign_requests(mesh):
    net, eid = mesh["net"], mesh["event"]["id"]
    me_b = await _me(mesh["db"]["b"], "b")

    async def post(body: dict, signature: str | None, *, raw: bytes | None = None):
        data = raw or json.dumps(body).encode()
        headers = {"Content-Type": "application/json", **({"X-Nodo-Firma": signature} if signature else {})}
        return await net("POST", "http://a/nodo-malla/v1/avance", data, headers)

    good_body, good_headers, _ = malla.build_request(me_b)
    req = json.loads(good_body)
    # unsigned
    status, out = await post(req, None)
    assert status == 403 and "sin firma" in out["detail"]
    # a stranger without a cloud voucher
    stranger = _fake(eid, {"activity_ids": [mesh["C"]], "check_in": False}, "Extraño")
    body, headers, _ = malla.build_request(stranger)
    status, out = await net("POST", "http://a/nodo-malla/v1/avance", body, headers)
    assert status == 403 and "desconocido" in out["detail"]
    # B's identity (valid cloud voucher) but signed with another key
    impostor = {**me_b, "private": nc.new_sign_keypair()[0]}
    body, headers, _ = malla.build_request(impostor)
    status, out = await net("POST", "http://a/nodo-malla/v1/avance", body, headers)
    assert status == 403 and "firma" in out["detail"]
    # another event / clock skew (validly signed)
    for change, word in (({"event_id": str(uuid.uuid4())}, "otro evento"), ({"ts": int(time.time()) - 3600}, "hora")):
        data = json.dumps({**req, **change}).encode()
        sig = nc.sign_context(me_b["private"], malla.CTX_REQUEST, malla.sha256_hex(data), json.loads(data)["ts"])
        status, out = await post({}, sig, raw=data)
        assert status == 403 and word in out["detail"], out
    # B was NOT admitted by any of these
    conn = await core.connect(mesh["db"]["a"])
    try:
        assert await malla.peer(conn, eid, me_b["node_id"]) is None
    finally:
        await conn.close()
    # the genuine request is answered (and B, prepared later, is admitted by its cloud voucher)
    status, out = await net("POST", "http://a/nodo-malla/v1/avance", good_body, good_headers)
    assert status == 200 and out["from_node"] == mesh["mac_a"].id and "ciphertext" in out["sobre"]
    assert "password_hash" not in json.dumps(out)
    conn = await core.connect(mesh["db"]["a"])
    try:
        assert (await malla.peer(conn, eid, me_b["node_id"]))["source"] == "aval"
    finally:
        await conn.close()


# --------------------------------------------------------------------------------------------
# Exchange + apply rules + upload unchanged
# --------------------------------------------------------------------------------------------
async def test_exchange_apply_rules_and_unchanged_upload(mesh, w):  # noqa: F811
    ev, net = mesh["event"], mesh["net"]
    eid, regs = ev["id"], ev["regs"]
    dsn_a, dsn_b = mesh["db"]["a"], mesh["db"]["b"]
    A, B = mesh["A"], mesh["B"]
    judge = w["judge"]["id"]

    # --- captures on each node (as their APIs would write them) --------------------------------
    conn = await core.connect(dsn_a)
    try:
        ci = uuid.uuid4()
        await conn.execute("INSERT INTO event_check_ins (id, registration_id, checked_in_at, checked_in_by_id,"
                           " checked_in_as, method) VALUES ($1, $2, $3, $4, 'COORDINATION', 'PASS')",
                           ci, uuid.UUID(regs["b"]), _now() - timedelta(minutes=5), uuid.UUID(w["coordinator"]["id"]))
    finally:
        await conn.close()
    a_eval = await _capture(dsn_a, regs["a"], A, 140, judge=judge)
    # the arrivals desk granted an automatic award for an activity B captures: an overlap for B
    a_auto = await _capture(dsn_a, regs["b"], B, 50, captured_as="AUTO_CHECKIN", check_in_id=str(ci))
    # B: a judge account created on the node, a capture, a correction, adjustments
    new_judge = uuid.uuid4()
    conn = await core.connect(dsn_b)
    try:
        await conn.execute("INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, $3,"
                           " 'Juez del nodo B', 'STUDENT')", new_judge, f"{RUN}-juezb@example.com", "$2b$04$x")
        adj, gen = uuid.uuid4(), uuid.uuid4()
        await conn.execute("INSERT INTO event_adjustments (id, registration_id, activity_id, kind, points, reason)"
                           " VALUES ($1, $2, $3, 'PENALTY', 3, 'tarde'), ($4, $2, NULL, 'BONUS', 2, 'limpieza')",
                           adj, uuid.UUID(regs["a"]), uuid.UUID(B), gen)
    finally:
        await conn.close()
    b_eval = await _capture(dsn_b, regs["a"], B, 30, judge=str(new_judge))
    await _correct(dsn_b, b_eval, 35)
    base_eval = (await _q(dsn_b, "SELECT id::text FROM evaluations WHERE registration_id = $1 AND activity_id = $2",
                          uuid.UUID(regs["c"]), uuid.UUID(B)))[0]["id"]
    await _correct(dsn_b, base_eval, 20)  # B corrects a base row (from the cloud) it owns

    # --- what each node would upload BEFORE the exchange ---------------------------------------
    up_a0, up_b0 = await _gather(dsn_a, eid), await _gather(dsn_b, eid)
    plan_a0 = await _dry_run(dsn_a, mesh["mac_a"].id, eid, w["coordinator"]["email"])
    plan_b0 = await _dry_run(dsn_b, mesh["mac_b"].id, eid, w["coordinator2"]["email"])

    # --- A pulls from B, B pulls from A ------------------------------------------------------------
    got_a = await server.pull(dsn_a, SECRETS["a"], "http://b", http=net)
    rep = got_a["reporte"]
    assert got_a["label"] == "Zona Marcha"
    assert rep["insertadas"] == 5 and rep["actualizadas"] == 1, rep  # eval+2 revisions+2 adjustments; base replaced
    assert not rep["traslapes"], rep
    rows = await _q(dsn_a, "SELECT table_name, row_id::text AS id, original IS NOT NULL AS replaced"
                           " FROM nodo_malla_filas WHERE event_id = $1", uuid.UUID(eid))
    got = {(r["table_name"], r["id"]): r["replaced"] for r in rows}
    assert got[("evaluations", b_eval)] is False and got[("evaluations", base_eval)] is True
    assert got[("event_adjustments", str(adj))] is False and got[("event_adjustments", str(gen))] is False
    pts = await _q(dsn_a, "SELECT points FROM evaluations WHERE id = ANY($1::uuid[]) ORDER BY points",
                   [uuid.UUID(b_eval), uuid.UUID(base_eval)])
    assert [float(r["points"]) for r in pts] == [20.0, 35.0]
    stub = (await _q(dsn_a, "SELECT email, status, password_hash, name FROM users WHERE id = $1", new_judge))[0]
    assert stub["status"] == "INACTIVE" and stub["password_hash"] == "!" and stub["email"].endswith("@nodo.invalid")
    assert stub["name"] == "Juez del nodo B"

    got_b = await server.pull(dsn_b, SECRETS["b"], "http://a", http=net)
    rep = got_b["reporte"]
    assert rep["insertadas"] == 2, rep  # A's check-in + A's capture
    assert [t["id"] for t in rep["traslapes"]] == [a_auto]
    assert "también la captura este nodo" in rep["traslapes"][0]["que"] and "Marcha" in rep["traslapes"][0]["que"]
    assert not await _q(dsn_b, "SELECT 1 FROM evaluations WHERE id = $1", uuid.UUID(a_auto))
    assert await _q(dsn_b, "SELECT 1 FROM evaluations WHERE id = $1", uuid.UUID(a_eval))
    # B has no desk: the arrival received from A shows on the registration (mirror)
    mirror = (await _q(dsn_b, "SELECT checked_in_at FROM event_registrations WHERE id = $1", uuid.UUID(regs["b"])))[0]
    assert mirror["checked_in_at"] is not None

    # --- idempotent ------------------------------------------------------------------------------
    for dsn, key, url in ((dsn_a, "a", "http://b"), (dsn_b, "b", "http://a")):
        again = (await server.pull(dsn, SECRETS[key], url, http=net))["reporte"]
        assert again["insertadas"] == again["actualizadas"] == 0, again

    # --- the cloud upload is exactly as without the mesh -----------------------------------------
    assert await _gather(dsn_a, eid) == up_a0
    assert await _gather(dsn_b, eid) == up_b0
    assert await _dry_run(dsn_a, mesh["mac_a"].id, eid, w["coordinator"]["email"]) == plan_a0
    assert await _dry_run(dsn_b, mesh["mac_b"].id, eid, w["coordinator2"]["email"]) == plan_b0
    # rows received via the mesh are never re-forwarded by the receiver
    me_a = await _me(dsn_a, "a")
    conn = await core.connect(dsn_a)
    try:
        avance = await malla.build_avance(conn, me_a)
    finally:
        await conn.close()
    sent = {r["id"] for t in malla.CAPTURE_TABLES for r in avance["event"][t]}
    assert b_eval not in sent and str(adj) not in sent and a_eval in sent
    assert all(set(u) == {"id", "name"} for u in avance["users"]) and avance["passes"] is None

    # --- a row that another neighbour already sent is refused --------------------------------------
    # B voids the base row and captures club c again (a new row)
    await _q(dsn_b, "UPDATE evaluations SET status = 'VOID', revision = revision + 1 WHERE id = $1",
             uuid.UUID(base_eval))
    b_eval2 = await _capture(dsn_b, regs["c"], B, 40, judge=judge)
    ghost = str(uuid.uuid4())
    await _q(dsn_a, "INSERT INTO nodo_malla_pares (event_id, node_id, label, scope, sign_key, source) VALUES"
                    " ($1, $2, 'Zona fantasma', '{\"all\": false, \"activity_ids\": [], \"check_in\": false}',"
                    " $3, 'emparejado')", uuid.UUID(eid), uuid.UUID(ghost), nc.b64(nc.new_sign_keypair()[1]))
    await _q(dsn_a, "INSERT INTO nodo_malla_filas (event_id, table_name, row_id, peer_node_id, fingerprint)"
                    " VALUES ($1, 'evaluations', $2, $3, 'x')", uuid.UUID(eid), uuid.UUID(b_eval2), uuid.UUID(ghost))
    rep = (await server.pull(dsn_a, SECRETS["a"], "http://b", http=net))["reporte"]
    assert [t["id"] for t in rep["traslapes"]] == [b_eval2] and "Zona fantasma" in rep["traslapes"][0]["que"]
    assert rep["actualizadas"] == 1  # the void of the base row (owned by B) still arrives
    # once that neighbour is «olvidado», its rows may come from another one
    conn = await core.connect(dsn_a)
    try:
        assert await malla.forget(conn, eid, ghost)
    finally:
        await conn.close()
    rep = (await server.pull(dsn_a, SECRETS["a"], "http://b", http=net))["reporte"]
    assert rep["insertadas"] == 1 and not rep["traslapes"], rep
    owner = await _q(dsn_a, "SELECT peer_node_id::text AS p FROM nodo_malla_filas WHERE row_id = $1",
                     uuid.UUID(b_eval2))
    assert owner[0]["p"] == mesh["mac_b"].id
    await _q(dsn_a, "DELETE FROM nodo_malla_pares WHERE node_id = $1", uuid.UUID(ghost))

    # --- state for the Mac app ---------------------------------------------------------------------
    rej = server.Rejections()
    rej.add("http://10.0.0.9", "nodo desconocido")
    conn = await core.connect(dsn_b)
    try:
        st = await malla.state(conn, await malla.me(conn, SECRETS["b"]), rej.list())
    finally:
        await conn.close()
    assert st["yo"]["label"] == "Zona Marcha" and st["yo"]["actividades"] == ["Marcha"] and st["yo"]["llegadas"] is False
    (vec,) = st["vecinos"]
    assert vec["label"] == "Zona Señal" and vec["origen"] == "paquete" and vec["actividades"] == ["Señal"]
    assert vec["llegadas"] is True and vec["llegadas_recibidas"] == 1 and vec["filas_recibidas"] == 2
    assert vec["ultima_sync"] and vec["traslapes"] and vec["error"] is None
    assert st["rechazados"][0]["motivo"] == "nodo desconocido"

    # --- a full cycle writes the state file atomically ---------------------------------------------
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "malla-vecinos.json").write_text(json.dumps([{"url": "http://a", "nombre": "A", "visto": 1},
                                                            {"url": "http://nadie"}]))
        await server.cycle(dsn_b, SECRETS["b"], tmp, server.Rejections(), http=net)
        state = json.loads((tmp / "malla-estado.json").read_text())
        assert state["vecinos"][0]["url"] == "http://a"
        assert [r["url"] for r in state["rechazados"]] == ["http://nadie"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------------
# The node API: rows received from a neighbour are read-only
# --------------------------------------------------------------------------------------------
async def test_node_api_refuses_edits_to_mesh_rows(client, w):  # noqa: F811
    event = await make_event(client, w, "Malla guardia")
    B = event["acts"]["B"]
    url, h = event["url"], hdr(w["coordinator"])
    ev = await _ok(await client.post(f"{url}/evaluations", headers=hdr(w["judge"]), json={
        "registration_id": event["regs"]["a"], "activity_id": B, "inputs": {"points": 10},
        "idempotency_key": f"g-{uuid.uuid4().hex}"}), 201)
    adj = await _ok(await client.post(f"{url}/adjustments", headers=h, json={
        "registration_id": event["regs"]["a"], "kind": "BONUS", "points": 5, "reason": "vecino"}), 201)
    own = await _ok(await client.post(f"{url}/adjustments", headers=h, json={
        "registration_id": event["regs"]["b"], "kind": "BONUS", "points": 5, "reason": "propio"}), 201)
    node_id = uuid.uuid4()
    # This database plays a node with EVERY activity (the guard used to let it write anything).
    await _exec("INSERT INTO event_offline_nodes (id, event_id, label, scope, local) VALUES (:id, :e, 'Zona Todo',"
                " :s, true)", id=node_id, e=uuid.UUID(event["id"]),
                s=json.dumps({"all": True, "activity_ids": [], "check_in": True}))
    conn = await core.connect(TEST_DATABASE_URL)
    created = not await core.has_mesh(conn)
    try:
        await malla.ensure_tables(conn)
        peer = uuid.uuid4()
        for table, rid in (("evaluations", ev["id"]), ("event_adjustments", adj["id"])):
            await conn.execute("INSERT INTO nodo_malla_filas (event_id, table_name, row_id, peer_node_id, fingerprint)"
                               " VALUES ($1, $2, $3, $4, 'x')", uuid.UUID(event["id"]), table, uuid.UUID(rid), peer)
        r = await client.post(f"{url}/evaluations/{ev['id']}/void", headers=h, json={"reason": "no"})
        assert r.status_code == 403 and "otro nodo" in r.json()["detail"], r.text
        r = await client.patch(f"{url}/evaluations/{ev['id']}", headers=h, json={"inputs": {"points": 1},
                                                                                "reason": "no"})
        assert r.status_code == 403 and "otro nodo" in r.json()["detail"]
        r = await client.post(f"{url}/adjustments/{adj['id']}/void", headers=h, json={"reason": "no"})
        assert r.status_code == 403 and "sólo lectura" in r.json()["detail"]
        r = await client.post(f"{url}/adjustments/{own['id']}/void", headers=h, json={"reason": "sí"})
        assert r.status_code == 200, r.text  # its own rows stay editable
    finally:
        await conn.execute("DELETE FROM nodo_malla_filas WHERE event_id = $1", uuid.UUID(event["id"]))
        if created:
            await conn.execute("DROP TABLE nodo_malla_filas, nodo_malla_pares, nodo_malla_conf")
        await conn.close()
        await _exec("DELETE FROM event_offline_nodes WHERE id = :id", id=node_id)
