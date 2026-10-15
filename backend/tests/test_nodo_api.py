"""«Nodo Camporee» automatic mode + several nodes per event (030; CONTRATO_NODO_LOGIN.md).

What is proved here (against the real API and database; the node side is simulated by
opening the sealed bundle, changing its rows like a node would and uploading them back):
  * who may list / prepare / upload / close: master, admin in scope, event coordinator yes;
    judge, director, outsider and an admin of another association no; closing is admin-only;
  * preparing and uploading need a RECENT login (a refresh does not renew it) and, for an
    account with a second factor, a session that passed it;
  * the bundle opens only with the node's one-time key; it carries the current passes in plain
    text (legacy ones generated once), never minors nor unrelated users;
  * uploads are accepted only sealed to the export's key AND signed by the node's key, inside
    the time window, from the node's latest preparation;
  * two nodes with disjoint scopes merge cleanly; a scope that overlaps an open node is refused;
    writes outside a node's scope come back as conflicts; the same club checked in on two nodes
    keeps the earliest; the same capture on two nodes is a conflict (prefer node voids the
    cloud's); the automatic on_checkin award is granted once;
  * «avance» then «final»; repeating an upload changes nothing; an admin force-closes a node;
    the cloud lock stays while ANY node is open;
  * on a node's own database, the API refuses (403) every write outside its scope.
"""
import copy
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.security import create_access_token, create_refresh_token, decode_claims
from app.services import nodo_crypto
from app.services import nodo_sync_core as core
from tests.conftest import RUN, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("nodoapi")
API = "/api/v1/events"


def hdr(user, *, mfa=False, age=0):
    token = create_access_token(uuid.UUID(user["id"]), mfa=mfa, auth_time=int(time.time()) - age)
    return {"Authorization": f"Bearer {token}"}


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(settings, "NODO_SETTLE_SECONDS", 0.0)


@pytest_asyncio.fixture(scope="module")
async def w(factory):
    assoc = await factory.org("assoc", "association")
    other_assoc = await factory.org("assoc-other", "association")
    church = await factory.org("church", "church", assoc)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b", "c")}
    for club in clubs.values():
        await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                    " WHERE id = :id", id=uuid.UUID(club["id"]))
    world = {
        "assoc": assoc, "other_assoc": other_assoc, "clubs": clubs,
        "master": await factory.user("master", "MASTER_GC"),
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "other_admin": await factory.user("other-admin", "ADMIN_ASSOCIATION", other_assoc["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR"),
        "coordinator2": await factory.user("coordinator2", "INSTRUCTOR"),
        "mfa_coord": await factory.user("mfa-coord", "INSTRUCTOR"),
        "judge": await factory.user("judge", "STUDENT"),
        "director": await factory.user("director", "CLUB_DIRECTOR", clubs["a"]["id"]),
        "minor_director": await factory.user("minor-director", "CLUB_DIRECTOR", clubs["b"]["id"], is_minor=True),
        "outsider": await factory.user("outsider", "STUDENT"),
    }
    await _exec("UPDATE users SET mfa_enabled = true, mfa_secret = 'JBSWY3DPEHPK3PXP' WHERE id = :id",
                id=uuid.UUID(world["mfa_coord"]["id"]))
    yield world
    ids = [uuid.UUID(assoc["id"])]
    await _exec("DELETE FROM audit_log WHERE entity_type LIKE 'EVENT%' AND entity_id IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM audit_log WHERE entity_type IN ('EVALUATION') AND metadata_json->>'event_id' IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM events WHERE organization_id = ANY(:ids)", ids=ids)


async def make_event(client, w, name: str) -> dict:
    """IN_PROGRESS event with 3 activities (A on_checkin), 3 clubs, two coordinators + MFA one, a judge."""
    h = w["admin"]["headers"]
    event = await _ok(await client.post(API, json={
        "organization_id": w["assoc"]["id"], "ministry": "adventurers", "name": f"{RUN} {name}",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h), 201)
    url = f"{API}/{event['id']}"
    acts = {}
    for key, body in (("A", {"name": "Señal", "kind": "participation", "max_points": 150,
                             "config": {"max": 150, "auto_award": "on_checkin"}}),
                      ("B", {"name": "Marcha", "kind": "participation", "max_points": 50, "config": {"max": 50}}),
                      ("C", {"name": "Fogata", "kind": "participation", "max_points": 30, "config": {"max": 30}})):
        acts[key] = (await _ok(await client.post(f"{url}/activities", json=body, headers=h), 201))["id"]
    for user, role in ((w["coordinator"], "COORDINATOR"), (w["coordinator2"], "COORDINATOR"),
                       (w["mfa_coord"], "COORDINATOR"), (w["judge"], "JUDGE")):
        await _ok(await client.post(f"{url}/staff", json={"user_id": user["id"], "role": role}, headers=h), 201)
    regs = {}
    for key, club in w["clubs"].items():
        regs[key] = (await _ok(await client.post(f"{url}/registrations", json={"club_id": club["id"]}, headers=h),
                               201))["id"]
    for step in ("OPEN", "IN_PROGRESS"):
        await _ok(await client.post(f"{url}/status", json={"status": step}, headers=h))
    return {"id": event["id"], "url": url, "acts": acts, "regs": regs}


@pytest_asyncio.fixture(scope="module")
async def ev(client, w):
    return await make_event(client, w, "Camporee nodos")


class Node:
    """The Mac side of one node: its keys, its export and its (simulated) database rows."""

    def __init__(self, label: str):
        self.label = label
        self.box_private, self.box_public = nodo_crypto.new_box_keypair()
        self.sign_private, self.sign_public = nodo_crypto.new_sign_keypair()
        self.answer = self.bundle = self.rows = None

    async def prepare(self, client, event, headers, scope, *, code=200, replace=None):
        body = {"node_label": self.label, "recipient_key": nodo_crypto.b64(self.box_public),
                "sign_key": nodo_crypto.b64(self.sign_public), "scope": scope, "replace_node_id": replace}
        response = await client.post(f"{API}/{event['id']}/offline/export", json=body, headers=headers)
        assert response.status_code == code, response.text
        if code != 200:
            return response
        self.answer = response.json()
        self.bundle = nodo_crypto.open_sealed(self.answer, self.box_private, event["id"])
        self.rows = copy.deepcopy(self.bundle["event"])
        return response

    @property
    def id(self):
        return self.answer["node_id"]

    def changes(self) -> dict:
        return {"format": core.CHANGES_FORMAT, "event_id": self.bundle["event_id"], "node_id": self.id,
                "export_id": self.answer["export_id"], "created_at": datetime.now(timezone.utc).isoformat(),
                "event": copy.deepcopy(self.rows), "users": self.bundle["reference"]["users"], "audit": [],
                "passes": self.bundle["passes"]}

    def body(self, changes: dict | None = None) -> bytes:
        envelope = nodo_crypto.seal(changes or self.changes(), nodo_crypto.unb64(self.answer["import_key"]),
                                    self.bundle["event_id"])
        envelope.update(node_id=self.id, export_id=self.answer["export_id"])
        return json.dumps(envelope).encode()

    async def upload(self, client, headers, *, dry_run=False, final=True, prefer=None, body=None,
                     sign_with=None, stamp=None, code=200):
        body = body or self.body()
        stamp = stamp or int(time.time())
        signature = nodo_crypto.sign(sign_with or self.sign_private, self.bundle["event_id"], self.id, stamp, body)
        query = f"dry_run={str(dry_run).lower()}&final={str(final).lower()}" + (f"&prefer={prefer}" if prefer else "")
        response = await client.post(f"{API}/{self.bundle['event_id']}/offline/import?{query}", content=body,
                                     headers={**headers, "Content-Type": "application/json",
                                              "X-Nodo-Timestamp": str(stamp), "X-Nodo-Signature": signature})
        assert response.status_code == code, response.text
        return response.json()

    # --- what a node would write -------------------------------------------------------------
    def capture(self, reg: str, activity: str, points: float, *, judge=None, captured_as="JUDGE",
                check_in_id=None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        points = float(points)  # as to_jsonb renders numeric(8,2) once parsed
        row = {"id": str(uuid.uuid4()), "registration_id": reg, "activity_id": activity,
               "inputs": {"points": points}, "points": points, "breakdown": {}, "rules_version":
               self.rows["events"][0]["rules_version"], "status": "CONFIRMED", "judge_id": judge,
               "captured_as": captured_as, "idempotency_key": f"t-{uuid.uuid4().hex}", "revision": 1,
               "created_at": now, "updated_at": now, "check_in_id": check_in_id}
        self.rows["evaluations"].append(row)
        return row

    def correct(self, row: dict, points: float, *, by=None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        points = float(points)
        self.rows["evaluation_revisions"].append({
            "id": str(uuid.uuid4()), "evaluation_id": row["id"], "revision": row["revision"],
            "inputs": row["inputs"], "points": row["points"], "status": row["status"],
            "rules_version": row["rules_version"], "judge_id": row["judge_id"], "action": "CORRECTION",
            "reason": "corrección", "changed_by_id": by, "idempotency_key": None, "created_at": now})
        row.update(points=points, inputs={"points": points}, revision=row["revision"] + 1, updated_at=now)

    def check_in(self, reg: str, at: datetime, by=None) -> dict:
        row = {"id": str(uuid.uuid4()), "registration_id": reg, "checked_in_at": at.isoformat(),
               "checked_in_by_id": by, "checked_in_as": "COORDINATION", "method": "PASS",
               "undone_at": None, "undone_by_id": None, "undo_reason": None}
        self.rows["event_check_ins"].append(row)
        for r in self.rows["event_registrations"]:
            if r["id"] == reg:
                r.update(checked_in_at=row["checked_in_at"], checked_in_by_id=by, checked_in_as="COORDINATION",
                         updated_at=datetime.now(timezone.utc).isoformat())
        return row


async def _node_row(node_id: str):
    return await fetch_one("SELECT * FROM event_offline_nodes WHERE id = :id", id=uuid.UUID(node_id))


async def _locked(event_id: str) -> bool:
    row = await fetch_one("SELECT offline_locked_at FROM events WHERE id = :id", id=uuid.UUID(event_id))
    return row["offline_locked_at"] is not None


# --------------------------------------------------------------------------------------------
# Crypto
# --------------------------------------------------------------------------------------------
def test_sealed_envelope_round_trip_and_tamper():
    private, public = nodo_crypto.new_box_keypair()
    env = nodo_crypto.seal({"hola": "mundo", "n": 1}, public, "ev-1")
    assert "mundo" not in json.dumps(env)
    assert nodo_crypto.open_sealed(env, private, "ev-1") == {"hola": "mundo", "n": 1}
    other, _ = nodo_crypto.new_box_keypair()
    with pytest.raises(nodo_crypto.CryptoError):
        nodo_crypto.open_sealed(env, other, "ev-1")
    with pytest.raises(nodo_crypto.CryptoError):
        nodo_crypto.open_sealed(env, private, "ev-2")  # bound to the event (AAD)
    bad = dict(env, ciphertext=nodo_crypto.b64(b"x" * 40))
    with pytest.raises(nodo_crypto.CryptoError):
        nodo_crypto.open_sealed(bad, private, "ev-1")


def test_signatures_and_secret_box():
    private, public = nodo_crypto.new_sign_keypair()
    sig = nodo_crypto.sign(private, "e", "n", 123, b"body")
    nodo_crypto.verify(nodo_crypto.b64(public), sig, "e", "n", "123", b"body")
    for args in (("e", "n", "124", b"body"), ("e", "n", "123", b"other"), ("e", "m", "123", b"body")):
        with pytest.raises(nodo_crypto.CryptoError):
            nodo_crypto.verify(nodo_crypto.b64(public), sig, *args)
    boxed = nodo_crypto.box("s3cret", "p", b"key")
    assert nodo_crypto.unbox("s3cret", "p", boxed) == b"key"
    with pytest.raises(nodo_crypto.CryptoError):
        nodo_crypto.unbox("other", "p", boxed)
    # the per-export upload key is deterministic per (secret, export) and differs across exports
    a = nodo_crypto.derived_box_private("s", "x")
    assert a == nodo_crypto.derived_box_private("s", "x") != nodo_crypto.derived_box_private("s", "y")


def test_refresh_keeps_the_original_login_time():
    old = int(time.time()) - 3600
    refresh = create_refresh_token(uuid.uuid4(), auth_time=old)
    assert decode_claims(refresh, "refresh")["auth_time"] == old


async def test_refresh_endpoint_does_not_renew_recent_auth(client, w, ev):
    old = int(time.time()) - 3600
    refresh = create_refresh_token(uuid.UUID(w["coordinator"]["id"]), auth_time=old)
    access = (await _ok(await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})))["access_token"]
    assert decode_claims(access, "access")["auth_time"] == old
    node = Node("refresh")
    r = await node.prepare(client, ev, {"Authorization": f"Bearer {access}"}, {"all": True}, code=401)
    assert r.headers.get("x-reauth-required") == "1"
    assert "vuelve a iniciar sesión" in r.json()["detail"].lower()


# --------------------------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------------------------
async def test_mine_lists_only_events_the_caller_coordinates(client, w, ev):
    async def ids(user):
        return {e["id"] for e in await _ok(await client.get(f"{API}/offline/mine", headers=hdr(user)))}
    for who in ("master", "admin", "coordinator"):
        assert ev["id"] in await ids(w[who]), who
    for who in ("judge", "director", "outsider", "other_admin"):
        assert ev["id"] not in await ids(w[who]), who
    item = next(e for e in await _ok(await client.get(f"{API}/offline/mine", headers=hdr(w["coordinator"])))
                if e["id"] == ev["id"])
    assert {a["name"] for a in item["activities"]} == {"Señal", "Marcha", "Fogata"}
    assert item["my_roles"] == ["COORDINATOR"] and item["offline_lock"] is None


async def test_export_permissions_recent_auth_and_mfa(client, w):
    event = await make_event(client, w, "Permisos")
    for who in ("judge", "director", "outsider", "other_admin"):
        await Node(who).prepare(client, event, hdr(w[who]), {"all": True}, code=403)
    old = await Node("old").prepare(client, event, hdr(w["coordinator"], age=16 * 60), {"all": True}, code=401)
    assert old.headers.get("x-reauth-required") == "1"
    r = await Node("mfa").prepare(client, event, hdr(w["mfa_coord"]), {"all": True}, code=403)
    assert "dos pasos" in r.json()["detail"]
    assert not await _locked(event["id"])  # nothing happened
    node = Node("Con MFA")
    await node.prepare(client, event, hdr(w["mfa_coord"], mfa=True), {"all": True})
    assert await _locked(event["id"])
    # MASTER and the admin in scope may too (other activities would overlap: close first)
    closed = await _ok(await client.post(f"{API}/{event['id']}/offline/nodes/{node.id}/close",
                                         json={"reason": "prueba"}, headers=hdr(w["admin"])))
    assert closed["status"] == "CLOSED" and closed["lock_released"] is True
    await Node("master").prepare(client, event, hdr(w["master"]), {"all": True})


async def test_bundle_contents(client, w, factory):
    event = await make_event(client, w, "Contenido")
    # a pass from before 028 (hash only) gets its stable form once, at export
    await _exec("UPDATE event_registrations SET pass_token_enc = NULL WHERE id = :id",
                id=uuid.UUID(event["regs"]["c"]))
    before = await fetch_one("SELECT pass_token_hash FROM event_registrations WHERE id = :id",
                             id=uuid.UUID(event["regs"]["c"]))
    node = Node("Contenido")
    await node.prepare(client, event, hdr(w["coordinator"]), {"all": True})
    assert node.answer["format"] == "nodo-sobre/1" and "ciphertext" in node.answer
    assert "password_hash" not in json.dumps(node.answer)  # sealed
    emails = {u["email"] for u in node.bundle["reference"]["users"]}
    for who in ("coordinator", "judge", "director", "admin", "master"):
        assert w[who]["email"] in emails, who
    assert w["minor_director"]["email"] not in emails
    assert w["outsider"]["email"] not in emails and w["other_admin"]["email"] not in emails
    assert all(u.get("password_hash") for u in node.bundle["reference"]["users"] if u["email"].startswith(RUN))
    orgs = {o["id"] for o in node.bundle["reference"]["organizations"]}
    assert {w["assoc"]["id"], *(c["id"] for c in w["clubs"].values())} <= orgs
    assert w["other_assoc"]["id"] not in orgs  # only the organizations the copy needs
    regs = {r["id"]: r for r in node.bundle["event"]["event_registrations"]}
    assert set(node.bundle["passes"]) == set(event["regs"].values())
    for rid, token in node.bundle["passes"].items():
        assert core.sha256_hex(token) == regs[rid]["pass_token_hash"]
    after = await fetch_one("SELECT pass_token_hash, pass_token_enc FROM event_registrations WHERE id = :id",
                            id=uuid.UUID(event["regs"]["c"]))
    assert after["pass_token_enc"] and after["pass_token_hash"] != before["pass_token_hash"]
    assert node.bundle["legacy_passes_regenerated"] == 1
    assert node.bundle["certificate"]["status"] == "no_configurado"
    assert node.answer["summary"]["clubs"] == 3 and node.answer["summary"]["passes"] == 3
    row = await _node_row(node.id)
    assert row["status"] == "PREPARED" and row["sign_key"] == nodo_crypto.b64(node.sign_public)
    assert row["coordinator_email"] == w["coordinator"]["email"]
    sync = await fetch_one("SELECT node_id FROM event_offline_syncs WHERE id = :id",
                           id=uuid.UUID(node.answer["export_id"]))
    assert str(sync["node_id"]) == node.id
    audit = await fetch_all("SELECT action FROM audit_log WHERE entity_id = :e AND action LIKE 'EVENT_OFFLINE%'",
                            e=event["id"])
    assert {"EVENT_OFFLINE_LOCK", "EVENT_OFFLINE_NODE_PREPARE"} <= {a["action"] for a in audit}


# --------------------------------------------------------------------------------------------
# Several nodes
# --------------------------------------------------------------------------------------------
async def test_two_nodes_disjoint_scopes_merge_cleanly_and_lock_follows_open_nodes(client, w):
    event = await make_event(client, w, "Dos nodos")
    A, B, C = event["acts"]["A"], event["acts"]["B"], event["acts"]["C"]
    n1, n2 = Node("Zona Misión Galáctica"), Node("Zona Estelar")
    await n1.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [A], "check_in": True})
    # overlapping scope with an OPEN node is refused, naming it
    r = await Node("Choca").prepare(client, event, hdr(w["coordinator2"]), {"activity_ids": [A, C]}, code=409)
    assert "Zona Misión Galáctica" in r.json()["detail"] and "Señal" in r.json()["detail"]
    r = await Node("Todo").prepare(client, event, hdr(w["coordinator2"]), {"all": True}, code=409)
    await n2.prepare(client, event, hdr(w["coordinator2"]), {"activity_ids": [B], "check_in": True})
    assert await _locked(event["id"])
    lock = await fetch_one("SELECT offline_node_label FROM events WHERE id = :id", id=uuid.UUID(event["id"]))
    assert lock["offline_node_label"] == "Zona Misión Galáctica, Zona Estelar"
    nodes = await _ok(await client.get(f"{API}/{event['id']}/offline/nodes", headers=hdr(w["coordinator"])))
    assert [n["label"] for n in nodes["nodes"]] == ["Zona Misión Galáctica", "Zona Estelar"]
    assert nodes["nodes"][0]["activity_names"] == ["Señal"]

    regs = event["regs"]
    judge = w["judge"]["id"]
    early, late = datetime.now(timezone.utc) - timedelta(hours=2), datetime.now(timezone.utc) - timedelta(hours=1)
    # node 1: check-in of club b (LATER), its on_checkin award, and Señal for club a
    ci1 = n1.check_in(regs["b"], late, by=w["coordinator"]["id"])
    n1.capture(regs["b"], A, 150, judge=w["coordinator"]["id"], captured_as="AUTO_CHECKIN", check_in_id=ci1["id"])
    e1 = n1.capture(regs["a"], A, 120, judge=judge)
    # node 2: the same club b checked in EARLIER (+ its own automatic award), Marcha for club a
    ci2 = n2.check_in(regs["b"], early, by=w["coordinator2"]["id"])
    n2.capture(regs["b"], A, 150, judge=w["coordinator2"]["id"], captured_as="AUTO_CHECKIN", check_in_id=ci2["id"])
    e2 = n2.capture(regs["a"], B, 40, judge=judge)

    dry = await n1.upload(client, hdr(w["coordinator"]), dry_run=True)
    assert dry["dry_run"] and dry["insert"] == {"event_check_ins": 1, "evaluations": 2} and not dry["conflicts"]
    assert (await _node_row(n1.id))["status"] == "PREPARED"  # nothing written
    rep1 = await n1.upload(client, hdr(w["coordinator"]))
    assert rep1["node_status"] == "FINAL" and not rep1["conflicts"]
    assert rep1["lock_released"] is False and rep1["open_nodes"] == 1
    assert await _locked(event["id"])  # node 2 is still out

    rep2 = await n2.upload(client, hdr(w["coordinator2"]))
    assert not rep2["conflicts"], rep2["conflicts"]
    assert rep2["node_status"] == "FINAL" and rep2["lock_released"] is True and rep2["open_nodes"] == 0
    assert not await _locked(event["id"])
    # one current check-in for club b: the EARLIEST (node 2); node 1's kept as undone history
    rows = await fetch_all("SELECT id::text, undone_at, undo_reason FROM event_check_ins WHERE registration_id = :r",
                           r=uuid.UUID(regs["b"]))
    current = [r for r in rows if r["undone_at"] is None]
    assert [r["id"] for r in current] == [ci2["id"]]
    assert any(r["id"] == ci1["id"] and "duplicada" in r["undo_reason"] for r in rows)
    reg = await fetch_one("SELECT checked_in_at FROM event_registrations WHERE id = :r", r=uuid.UUID(regs["b"]))
    assert abs((reg["checked_in_at"] - early).total_seconds()) < 1
    # the on_checkin award exists ONCE, and both captures of the disjoint activities are in
    live = await fetch_all("SELECT id::text, activity_id::text, registration_id::text, points FROM evaluations"
                           " WHERE registration_id = ANY(:r) AND status = 'CONFIRMED'",
                           r=[uuid.UUID(x) for x in regs.values()])
    assert sum(1 for e in live if e["registration_id"] == regs["b"] and e["activity_id"] == A) == 1
    assert {e1["id"], e2["id"]} <= {e["id"] for e in live}
    assert any("ya otorgados" in w_["what"] for w_ in rep2["warnings"])
    assert any("dos nodos" in w_["what"] for w_ in rep2["warnings"])
    nodes = await _ok(await client.get(f"{API}/{event['id']}/offline/nodes", headers=hdr(w["admin"])))
    assert [n["status"] for n in nodes["nodes"]] == ["FINAL", "FINAL"] and nodes["offline_lock"] is None


async def test_out_of_scope_and_double_capture_are_conflicts(client, w):
    event = await make_event(client, w, "Conflictos")
    A, B = event["acts"]["A"], event["acts"]["B"]
    regs = event["regs"]
    n1 = Node("Señal")
    await n1.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [A], "check_in": False})
    # writes outside the scope come back as conflicts and are not applied
    n1.capture(regs["a"], B, 10)
    n1.check_in(regs["c"], datetime.now(timezone.utc))
    ok = n1.capture(regs["b"], A, 100)
    rep = await n1.upload(client, hdr(w["coordinator"]), final=True)
    whats = {c["what"] for c in rep["conflicts"]}
    assert "evaluación de una actividad fuera del alcance del nodo" in whats
    assert "llegada registrada en un nodo sin mesa de llegadas" in whats
    assert rep["node_status"] == "PARTIAL"  # a final with conflicts does not close the node
    assert rep["insert"] == {"evaluations": 1} and await _locked(event["id"])
    assert all(c.get("detail") for c in rep["conflicts"])
    assert await fetch_one("SELECT 1 AS x FROM evaluations WHERE id = :i", i=uuid.UUID(ok["id"]))

    # a capture that ALSO exists in the cloud (stray write) for the same club and activity
    n1.rows["evaluations"] = [r for r in n1.rows["evaluations"] if r["activity_id"] == A]
    n1.rows["event_check_ins"] = []
    mine = n1.capture(regs["c"], A, 90)
    stray = str(uuid.uuid4())
    await _exec("INSERT INTO evaluations (id, registration_id, activity_id, inputs, points, rules_version,"
                " idempotency_key) VALUES (:id, :r, :a, '{\"points\": 80}', 80, 1, :k)",
                id=uuid.UUID(stray), r=uuid.UUID(regs["c"]), a=uuid.UUID(A), k=f"stray-{stray}")
    rep = await n1.upload(client, hdr(w["coordinator"]), final=True)
    dup = [c for c in rep["conflicts"] if c["table"] == "evaluations"]
    assert len(dup) == 1 and dup[0]["what"].startswith("capturada en dos nodos") and dup[0]["id"] == mine["id"]
    assert rep["node_status"] == "PARTIAL"
    rep = await n1.upload(client, hdr(w["coordinator"]), final=True, prefer="node")
    assert not rep["conflicts"] and rep["node_status"] == "FINAL" and rep["lock_released"]
    stray_row = await fetch_one("SELECT status, revision FROM evaluations WHERE id = :i", i=uuid.UUID(stray))
    assert stray_row["status"] == "VOID" and stray_row["revision"] == 2
    revision = await fetch_one("SELECT action, reason FROM evaluation_revisions WHERE evaluation_id = :i",
                               i=uuid.UUID(stray))
    assert revision["action"] == "VOID" and "Señal" in revision["reason"]
    assert (await fetch_one("SELECT status FROM evaluations WHERE id = :i", i=uuid.UUID(mine["id"])))["status"] \
        == "CONFIRMED"


async def test_incremental_then_final_and_idempotent_reuploads(client, w):
    event = await make_event(client, w, "Avance")
    A = event["acts"]["A"]
    regs = event["regs"]
    node = Node("Avance")
    await node.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [A], "check_in": True})
    e1 = node.capture(regs["a"], A, 100)
    rep = await node.upload(client, hdr(w["coordinator"]), final=False)
    assert rep["node_status"] == "PARTIAL" and rep["insert"] == {"evaluations": 1} and await _locked(event["id"])
    # the same avance again: nothing new
    rep = await node.upload(client, hdr(w["coordinator"]), final=False)
    assert rep["insert"] == {} and rep["update"] == {} and not rep["conflicts"]
    # keep capturing: correct e1 and add e2 -> the baseline advanced, so no false conflict
    node.correct(e1, 110)
    node.capture(regs["b"], A, 90)
    body = node.body()
    rep = await node.upload(client, hdr(w["coordinator"]), body=body)
    assert not rep["conflicts"], rep["conflicts"]
    assert rep["update"] == {"evaluations": 1} and rep["insert"] == {"evaluations": 1, "evaluation_revisions": 1}
    assert rep["node_status"] == "FINAL" and rep["lock_released"]
    row = await fetch_one("SELECT points, revision FROM evaluations WHERE id = :i", i=uuid.UUID(e1["id"]))
    assert float(row["points"]) == 110 and row["revision"] == 2
    # repeating the very same final upload is answered as already applied
    again = await node.upload(client, hdr(w["coordinator"]), body=body)
    assert again["already_applied"] is True
    # anything else from a closed node is refused
    r = await node.upload(client, hdr(w["coordinator"]), code=409)
    assert "subió final" in r["detail"]
    syncs = await fetch_all("SELECT kind FROM event_offline_syncs WHERE node_id = :n", n=uuid.UUID(node.id))
    assert [s["kind"] for s in syncs].count("IMPORT") == 3


async def test_upload_signature_and_envelope_checks(client, w):
    event = await make_event(client, w, "Firmas")
    n1, n2 = Node("uno"), Node("dos")
    await n1.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [event["acts"]["A"]]})
    await n2.prepare(client, event, hdr(w["coordinator2"]), {"activity_ids": [event["acts"]["B"]]})
    h = hdr(w["coordinator"])
    r = await n1.upload(client, h, sign_with=n2.sign_private, code=403)  # another node's key
    assert "firma" in r["detail"]
    r = await n1.upload(client, h, stamp=int(time.time()) - 3600, code=403)
    assert "hora" in r["detail"]
    # sealed to another export's key
    wrong = json.loads(n1.body())
    wrong["ciphertext"] = json.loads(n2.body())["ciphertext"]
    r = await n1.upload(client, h, body=json.dumps(wrong).encode(), code=403)
    # old login / no permission
    await n1.upload(client, hdr(w["coordinator"], age=3600), code=401)
    await n1.upload(client, hdr(w["judge"]), code=403)
    # re-preparing the node from the Mac replaces it; the old preparation can no longer upload
    n1b = Node("uno")
    await n1b.prepare(client, event, h, {"activity_ids": [event["acts"]["A"]]}, replace=n1.id)
    assert (await _node_row(n1.id))["status"] == "CLOSED"
    r = await n1.upload(client, h, code=409)
    await n1b.upload(client, h, dry_run=True)


async def test_force_close_by_admin(client, w):
    event = await make_event(client, w, "Cerrar")
    n1, n2 = Node("uno"), Node("dos")
    await n1.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [event["acts"]["A"]]})
    await n2.prepare(client, event, hdr(w["coordinator2"]), {"activity_ids": [event["acts"]["B"]]})
    url = f"{API}/{event['id']}/offline/nodes/{n2.id}/close"
    r = await client.post(url, json={"reason": "se perdió la Mac"}, headers=hdr(w["coordinator"]))
    assert r.status_code == 403 and "administración" in r.json()["detail"]
    r = await client.post(url, json={"reason": "  "}, headers=hdr(w["admin"]))
    assert r.status_code == 422
    closed = await _ok(await client.post(url, json={"reason": "se perdió la Mac"}, headers=hdr(w["admin"])))
    assert closed["status"] == "CLOSED" and closed["close_reason"] == "se perdió la Mac"
    assert closed["lock_released"] is False and await _locked(event["id"])  # n1 still open
    await client.post(url, json={"reason": "otra vez"}, headers=hdr(w["admin"]))
    assert (await client.post(url, json={"reason": "otra vez"}, headers=hdr(w["admin"]))).status_code == 409
    await n2.upload(client, hdr(w["coordinator2"]), code=409)
    audit = await fetch_one("SELECT details FROM audit_log WHERE entity_id = :e AND action = 'EVENT_OFFLINE_NODE_CLOSE'",
                            e=event["id"])
    assert audit["details"] == "se perdió la Mac"
    rep = await n1.upload(client, hdr(w["coordinator"]))
    assert rep["lock_released"] and not await _locked(event["id"])


# --------------------------------------------------------------------------------------------
# On the node: scope guard
# --------------------------------------------------------------------------------------------
async def test_a_node_only_writes_within_its_scope(client, w):
    event = await make_event(client, w, "Guardia")
    A, B = event["acts"]["A"], event["acts"]["B"]
    node_id = uuid.uuid4()
    # This database plays the node «Zona Señal»: captures Señal only, no arrivals desk.
    await _exec("INSERT INTO event_offline_nodes (id, event_id, label, scope, local) VALUES (:id, :e, 'Zona Señal',"
                " :s, true)", id=node_id, e=uuid.UUID(event["id"]),
                s=json.dumps({"all": False, "activity_ids": [A], "check_in": False}))
    try:
        url, h, j = event["url"], hdr(w["coordinator"]), hdr(w["judge"])
        got = await _ok(await client.get(url, headers=h))
        assert got["offline_node"] == {"id": str(node_id), "label": "Zona Señal", "all": False,
                                       "activity_ids": [A], "check_in": False}
        r = await client.post(f"{url}/evaluations", headers=j, json={
            "registration_id": event["regs"]["a"], "activity_id": B, "inputs": {"points": 10},
            "idempotency_key": f"g-{uuid.uuid4().hex}"})
        assert r.status_code == 403 and "Zona Señal" in r.json()["detail"]
        r = await client.post(f"{url}/evaluations", headers=j, json={
            "registration_id": event["regs"]["a"], "activity_id": A, "inputs": {"points": 10},
            "idempotency_key": f"g-{uuid.uuid4().hex}"})
        assert r.status_code == 201, r.text
        r = await client.post(f"{url}/check-in", headers=h, json={"registration_id": event["regs"]["b"]})
        assert r.status_code == 403 and "mesa de llegadas" in r.json()["detail"]
        r = await client.patch(url, headers=h, json={"venue": "Otro lugar"})
        assert r.status_code == 403 and "configuración del evento" in r.json()["detail"]
        r = await client.patch(f"{url}/activities/{B}", headers=h, json={"name": "Marcha 2"})
        assert r.status_code == 403
        r = await client.patch(f"{url}/activities/{A}", headers=h, json={"description": "Regla local"})
        assert r.status_code == 200, r.text
        r = await client.post(f"{url}/adjustments", headers=h, json={
            "registration_id": event["regs"]["a"], "kind": "BONUS", "points": 5, "reason": "puntualidad"})
        assert r.status_code in (200, 201), r.text
        r = await client.post(f"{url}/adjustments", headers=h, json={
            "registration_id": event["regs"]["a"], "activity_id": B, "kind": "BONUS", "points": 5,
            "reason": "puntualidad"})
        assert r.status_code == 403
        # the node itself never exports nor imports (that is the cloud's job)
        r = await Node("x").prepare(client, event, h, {"all": True}, code=409)
        assert "nube" in r.json()["detail"]
    finally:
        await _exec("DELETE FROM event_offline_nodes WHERE id = :id", id=node_id)
    # back to a cloud database: writes are not restricted by scope
    r = await client.patch(event["url"], headers=hdr(w["coordinator"]), json={"venue": "La Morita"})
    assert r.status_code == 200


# --------------------------------------------------------------------------------------------
# Pure planning rules
# --------------------------------------------------------------------------------------------
def _rows(**tables):
    data = {t: [] for t in core.EVENT_TABLE_NAMES}
    data.update(tables)
    return data


def test_auto_checkin_award_is_granted_once_and_cloud_deletions_are_respected():
    prod_eval = {"id": "p", "registration_id": "r", "activity_id": "a", "status": "CONFIRMED",
                 "captured_as": "AUTO_CHECKIN", "revision": 1}
    node_eval = dict(prod_eval, id="n")
    plan = core.plan_changes({core.FORMAT_KEY: 2}, _rows(evaluations=[node_eval]),
                             _rows(evaluations=[prod_eval]), prefer=None)
    assert not plan["insert"]["evaluations"] and not plan["conflicts"]
    # a row another node deleted, untouched by this node: the deletion stands
    staff = {"id": "s", "role": "JUDGE"}
    snap = {core.FORMAT_KEY: 2, "event_staff": {"s": core.fingerprint(staff, "event_staff")}}
    plan = core.plan_changes(snap, _rows(event_staff=[staff]), _rows(), prefer=None)
    assert not plan["insert"]["event_staff"] and not plan["conflicts"]


def test_event_row_is_merged_field_by_field():
    base = {"id": "e", "status": "OPEN", "rules_version": 3, "name": "X", "updated_at": "2026-11-20T10:00:00+00:00"}
    node = dict(base, status="IN_PROGRESS", updated_at="2026-11-20T11:00:00+00:00")
    prod = dict(base, rules_version=4, updated_at="2026-11-20T10:30:00+00:00")
    snap = core.snapshot_of(_rows(events=[base]))
    plan = core.plan_changes(snap, _rows(events=[node]), _rows(events=[prod]), prefer=None)
    assert not plan["conflicts"]
    assert plan["update"]["events"][0]["status"] == "IN_PROGRESS" and plan["update"]["events"][0]["rules_version"] == 4
    other = dict(base, name="Y")
    plan = core.plan_changes(snap, _rows(events=[dict(base, name="Z")]), _rows(events=[other]), prefer=None)
    assert plan["conflicts"] and plan["conflicts"][0]["fields"] == ["name"]


def test_pass_ciphertext_and_check_in_mirror_are_not_differences():
    reg = {"id": "r", "pass_token_hash": "h", "pass_token_enc": "v1.cloud", "checked_in_at": None,
           "updated_at": "2026-11-20T10:00:00+00:00", "status": "REGISTERED"}
    node = dict(reg, pass_token_enc="v1.node", checked_in_at="2026-11-20T11:00:00+00:00",
                updated_at="2026-11-20T11:00:00+00:00")
    snap = core.snapshot_of(_rows(event_registrations=[reg]))
    plan = core.plan_changes(snap, _rows(event_registrations=[node]), _rows(event_registrations=[reg]), prefer=None)
    assert plan["unchanged"]["event_registrations"] == 1 and not plan["update"]["event_registrations"]
