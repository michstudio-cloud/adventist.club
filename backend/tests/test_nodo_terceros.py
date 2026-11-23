"""«Nodo de evento» for OTHER associations (032): the node is a Mac in someone else's hands.

What is proved here (real API + database; the node side simulated like test_nodo_api):
  * a node can be prepared only for an event a MASTER_GC enabled, and only from an account with
    a second factor; only a MASTER_GC changes those settings, and a node cannot switch them by
    uploading;
  * the real TLS certificate (and its private key) travels only when a MASTER_GC enabled it for
    the event; otherwise the bundle says «ca_local» and carries no key; the node side removes a
    real certificate left by an earlier event;
  * an account created on a node reaches the cloud WITHOUT privileges (minimal role, no
    organization, inactive), whatever the node claims;
  * an upload carrying rows of ANOTHER event (staff, captures, a moved row, a reused id) is
    rejected whole and changes nothing; audit rows about another event are dropped;
  * the bundle carries the owning association's admins (not the union's), and a usable password
    hash only for the people capturing at the event.
"""
import copy
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services import node_certs
from app.services import nodo_sync_core as core
from tests.conftest import RUN, fetch_all, fetch_one, module_factory, requires_db
from tests.test_nodo_api import API, Node, _exec, _fast, _node_row, _ok, hdr, make_event, w  # noqa: F401 (fixtures)

pytestmark = requires_db
factory = module_factory("nodoterceros")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _settings(client, event, user, body, code=200):
    r = await client.put(f"{API}/{event['id']}/offline/settings", json=body, headers=hdr(user))
    assert r.status_code == code, r.text
    return r.json()


# --------------------------------------------------------------------------------------------
# Who may prepare, and what MASTER_GC decides
# --------------------------------------------------------------------------------------------
async def test_export_needs_a_node_enabled_event_and_an_account_with_mfa(client, w):
    event = await make_event(client, w, "Sin habilitar", node_enabled=False)
    for who in ("coordinator", "admin", "master"):
        r = await Node(who).prepare(client, event, hdr(w[who]), {"all": True}, code=403)
        assert "no está habilitado" in r.json()["detail"], who
    mine = await _ok(await client.get(f"{API}/offline/mine", headers=hdr(w["coordinator"])))
    item = next(e for e in mine if e["id"] == event["id"])
    assert item["node_enabled"] is False and item["real_certificate"] is False
    assert await _settings(client, event, w["master"], {"node_enabled": True}) == {
        "node_enabled": True, "real_certificate": False}
    # a coordinator WITHOUT a second factor on the account may not prepare (passes + hashes)
    await _ok(await client.post(f"{event['url']}/staff", json={"user_id": w["no_mfa_coord"]["id"],
                                                               "role": "COORDINATOR"},
                                headers=w["admin"]["headers"]), 201)
    r = await Node("sin mfa").prepare(client, event, hdr(w["no_mfa_coord"]), {"all": True}, code=403)
    assert "dos pasos" in r.json()["detail"]
    row = await fetch_one("SELECT offline_locked_at FROM events WHERE id = :id", id=uuid.UUID(event["id"]))
    assert row["offline_locked_at"] is None  # nothing happened
    await Node("con mfa").prepare(client, event, hdr(w["coordinator"]), {"all": True})


async def test_only_master_changes_node_settings_and_a_node_cannot_switch_them(client, w):
    event = await make_event(client, w, "Ajustes", node_enabled=False)
    for who in ("admin", "coordinator", "union_admin"):
        r = await client.put(f"{API}/{event['id']}/offline/settings", json={"node_enabled": True},
                             headers=hdr(w[who]))
        assert r.status_code == 403, who
    old = await client.put(f"{API}/{event['id']}/offline/settings", json={"node_enabled": True},
                           headers=hdr(w["master"], age=16 * 60))
    assert old.status_code == 401  # recent login, like preparing
    await _settings(client, event, w["master"], {"node_enabled": True})
    audit = await fetch_all("SELECT action, metadata_json FROM audit_log WHERE entity_id = :e"
                            " AND action = 'EVENT_OFFLINE_NODE_SETTINGS'", e=event["id"])
    assert len(audit) == 1 and audit[0]["metadata_json"]["after"]["node_enabled"] is True
    # the node sends the event row with both settings on: the cloud ignores them
    node = Node("Ajustes")
    await node.prepare(client, event, hdr(w["coordinator"]), {"all": True})
    node.rows["events"][0].update(offline_node_real_cert=True, offline_node_enabled=False)
    await node.upload(client, hdr(w["coordinator"]))
    row = await fetch_one("SELECT offline_node_enabled, offline_node_real_cert FROM events WHERE id = :id",
                          id=uuid.UUID(event["id"]))
    assert row["offline_node_enabled"] is True and row["offline_node_real_cert"] is False


async def test_real_certificate_only_for_events_master_enabled(client, w, monkeypatch):
    calls = []

    async def fake_certificate(conn, **kwargs):
        calls.append(1)
        return {"status": "ok", "fullchain_pem": "CHAIN", "privkey_pem": "LLAVE-REAL", "not_after": "2027-01-01",
                "names": ["events.adventist.club"], "staging": False, "cached": True}

    monkeypatch.setattr(node_certs, "certificate_for_node", fake_certificate)
    event = await make_event(client, w, "Certificado")
    node = Node("Local")
    await node.prepare(client, event, hdr(w["coordinator"]), {"all": True})
    assert node.bundle["certificate"]["status"] == "ca_local" and "privkey_pem" not in node.bundle["certificate"]
    assert node.answer["summary"]["certificate"] == "ca_local" and not calls
    await _ok(await client.post(f"{API}/{event['id']}/offline/nodes/{node.id}/close", json={"reason": "prueba"},
                                headers=hdr(w["admin"])))
    await _settings(client, event, w["master"], {"real_certificate": True})
    real = Node("Real")
    await real.prepare(client, event, hdr(w["coordinator"]), {"all": True})
    assert real.bundle["certificate"]["status"] == "ok" and real.bundle["certificate"]["privkey_pem"] == "LLAVE-REAL"
    assert calls == [1]


def _self_signed(issuer_cn: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + timedelta(days=30)).sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))


def test_node_side_removes_a_real_certificate_when_the_event_uses_the_local_ca(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "nodo_sync_terceros", Path(__file__).resolve().parent.parent / "scripts" / "nodo_sync.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    real, local = tmp_path / "real", tmp_path / "local"
    for folder, issuer in ((real, "R11 Let's Encrypt"), (local, "Nodo evento CA local 20261119")):
        folder.mkdir()
        chain, key = _self_signed(issuer)
        (folder / "fullchain.pem").write_bytes(chain)
        (folder / "privkey.pem").write_bytes(key)
    out = sync.write_certificate({"status": "ca_local"}, str(real))
    assert out["removed_real"] is True and out["installed"] is False
    assert not (real / "fullchain.pem").exists() and not (real / "privkey.pem").exists()
    out = sync.write_certificate({"status": "ca_local"}, str(local))
    assert out["removed_real"] is False and (local / "privkey.pem").exists()


# --------------------------------------------------------------------------------------------
# Issue 2: accounts created on a node
# --------------------------------------------------------------------------------------------
async def test_a_user_created_on_a_node_arrives_without_privileges(client, w):
    event = await make_event(client, w, "Cuenta del nodo")
    node = Node("Cuenta")
    await node.prepare(client, event, hdr(w["coordinator"]), {"all": True})
    template = next(u for u in node.bundle["reference"]["users"] if u["email"] == w["judge"]["email"])
    new_id = str(uuid.uuid4())
    forged = dict(copy.deepcopy(template), id=new_id, email=f"{RUN}-nodo-juez@example.org".lower(),
                  name="Juez del nodo", handle=None, role="MASTER_GC", organization_id=w["assoc"]["id"],
                  status="ACTIVE", verification_status="VERIFIED", mfa_enabled=False,
                  leader_verified_until="2030-01-01", child_protection_completed=True)
    node.rows["event_staff"].append({
        "id": str(uuid.uuid4()), "event_id": event["id"], "user_id": new_id, "role": "JUDGE", "activity_id": None,
        "active": True, "created_by_id": w["coordinator"]["id"], "created_at": _now(), "ended_at": None,
        "ended_by_id": None})
    changes = node.changes()
    changes["users"] = [*changes["users"], forged]
    report = await node.upload(client, hdr(w["coordinator"]), body=node.body(changes))
    assert report["new_users"] == 1 and any("sin permisos" in n for n in report["users"])
    user = await fetch_one("SELECT role, organization_id, status, verification_status, leader_verified_until,"
                           " child_protection_completed, mfa_enabled FROM users WHERE id = :id", id=uuid.UUID(new_id))
    assert user["role"] == "STUDENT" and user["organization_id"] is None and user["status"] == "INACTIVE"
    assert user["verification_status"] == "PENDING" and user["leader_verified_until"] is None
    assert user["child_protection_completed"] is False and user["mfa_enabled"] is False
    staff = await fetch_one("SELECT role FROM event_staff WHERE event_id = :e AND user_id = :u",
                            e=uuid.UUID(event["id"]), u=uuid.UUID(new_id))
    assert staff["role"] == "JUDGE"  # the event row still lands (the judge's work is not lost)


# --------------------------------------------------------------------------------------------
# Issue 3: rows of another event
# --------------------------------------------------------------------------------------------
async def _counts(event_id: str) -> dict:
    out = {}
    for table, where in (("event_staff", "event_id = :e"), ("event_activities", "event_id = :e"),
                         ("evaluations", "registration_id IN (SELECT id FROM event_registrations WHERE event_id = :e)")):
        out[table] = (await fetch_one(f"SELECT count(*) AS n FROM {table} WHERE {where}", e=uuid.UUID(event_id)))["n"]
    return out


async def test_upload_with_rows_of_another_event_is_rejected_whole(client, w):
    mine = await make_event(client, w, "Evento del nodo")
    other = await make_event(client, w, "Otro evento")
    node = Node("Alcance")
    await node.prepare(client, mine, hdr(w["coordinator"]), {"all": True})
    original = copy.deepcopy(node.rows)
    before_mine, before_other = await _counts(mine["id"]), await _counts(other["id"])
    acts = {r["id"]: r for r in original["event_activities"]}

    def staff_for_other(rows):  # the audit's example: a COORDINATOR of another event
        rows["event_staff"].append({
            "id": str(uuid.uuid4()), "event_id": other["id"], "user_id": w["outsider"]["id"], "role": "COORDINATOR",
            "activity_id": None, "active": True, "created_by_id": w["coordinator"]["id"], "created_at": _now(),
            "ended_at": None, "ended_by_id": None})

    def capture_for_other_club(rows):
        node.rows = rows
        node.capture(other["regs"]["a"], mine["acts"]["B"], 10, judge=w["judge"]["id"])

    def capture_for_other_activity(rows):
        node.rows = rows
        node.capture(mine["regs"]["a"], other["acts"]["B"], 10, judge=w["judge"]["id"])

    def move_activity_to_other(rows):
        rows["event_activities"][-1]["event_id"] = other["id"]

    def reuse_other_id(rows):
        row = dict(acts[mine["acts"]["C"]], id=other["acts"]["C"], name="Pisada")
        rows["event_activities"].append(row)

    def other_event_row(rows):
        rows["events"][0] = dict(rows["events"][0], id=other["id"])

    for change in (staff_for_other, capture_for_other_club, capture_for_other_activity, move_activity_to_other,
                   reuse_other_id, other_event_row):
        rows = copy.deepcopy(original)
        change(rows)
        node.rows = rows
        for dry_run in (True, False):
            r = await node.upload(client, hdr(w["coordinator"]), dry_run=dry_run, code=422)
            assert "no son de este evento" in r["detail"], change.__name__
        assert await _counts(mine["id"]) == before_mine, change.__name__
        assert await _counts(other["id"]) == before_other, change.__name__
        assert (await _node_row(node.id))["status"] == "PREPARED", change.__name__
    # the honest upload still goes through
    node.rows = copy.deepcopy(original)
    node.capture(mine["regs"]["a"], mine["acts"]["B"], 12, judge=w["judge"]["id"])
    report = await node.upload(client, hdr(w["coordinator"]))
    assert report["insert"] == {"evaluations": 1} and report["node_status"] == "FINAL"


async def test_audit_rows_of_another_event_are_dropped(client, w):
    mine = await make_event(client, w, "Bitácora")
    other = await make_event(client, w, "Bitácora ajena")
    node = Node("Bitácora")
    await node.prepare(client, mine, hdr(w["coordinator"]), {"all": True})
    row = node.capture(mine["regs"]["a"], mine["acts"]["B"], 7, judge=w["judge"]["id"])

    def audit(entity_type, entity_id):
        return {"id": str(uuid.uuid4()), "action": "EVENT_UPDATE", "entity_type": entity_type,
                "entity_id": entity_id, "user_id": w["coordinator"]["id"], "user_email": w["coordinator"]["email"],
                "user_role": "INSTRUCTOR", "organization_id": None, "details": "prueba", "metadata_json": {},
                "ip_address": None, "legacy_mongo_id": None, "created_at": _now()}

    ours, theirs = audit("EVALUATION", row["id"]), audit("EVENT", other["id"])
    changes = node.changes()
    changes["audit"] = [ours, theirs]
    report = await node.upload(client, hdr(w["coordinator"]), body=node.body(changes))
    assert report["audit_rows"] == 1 and any("bitácora" in n for n in report["users"])
    ids = {str(r["id"]) for r in await fetch_all("SELECT id FROM audit_log WHERE id = ANY(:ids)",
                                                 ids=[uuid.UUID(ours["id"]), uuid.UUID(theirs["id"])])}
    assert ids == {ours["id"]}


def test_foreign_rows_checks_every_reference():
    eid, other = str(uuid.uuid4()), str(uuid.uuid4())
    act, reg, ev_ = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    base = {t: [] for t in core.EVENT_TABLE_NAMES}
    prod = dict(base, events=[{"id": eid}], event_activities=[{"id": act, "event_id": eid}],
                event_registrations=[{"id": reg, "event_id": eid}],
                evaluations=[{"id": ev_, "registration_id": reg, "activity_id": act}])
    assert core.foreign_rows(uuid.UUID(eid), copy.deepcopy(prod), prod) == []
    cases = {
        "event_adjustments": {"id": str(uuid.uuid4()), "registration_id": reg, "activity_id": None,
                              "adjustment_type_id": str(uuid.uuid4())},
        "evaluation_revisions": {"id": str(uuid.uuid4()), "evaluation_id": str(uuid.uuid4())},
        "event_check_ins": {"id": str(uuid.uuid4()), "registration_id": None},
        "event_staff": {"id": str(uuid.uuid4()), "event_id": eid, "activity_id": str(uuid.uuid4())},
        "event_adjustment_types": {"id": str(uuid.uuid4()), "event_id": other},
    }
    for table, row in cases.items():
        node = copy.deepcopy(prod)
        node[table] = [*node[table], row]
        assert core.foreign_rows(uuid.UUID(eid), node, prod), table
    bad_id = copy.deepcopy(prod)
    bad_id["event_registrations"][0]["id"] = "no-es-un-uuid"
    assert core.foreign_rows(uuid.UUID(eid), bad_id, prod)


# --------------------------------------------------------------------------------------------
# Issue 4: who travels, and with which password hash
# --------------------------------------------------------------------------------------------
async def test_bundle_carries_the_owner_association_not_the_union(client, w):
    union_world = {**w, "assoc": w["union_assoc"], "admin": w["union_assoc_admin"], "clubs": {"u": w["union_club"]}}
    event = await make_event(client, union_world, "Asociación bajo unión")
    node = Node("Unión")
    await node.prepare(client, event, hdr(w["coordinator"]), {"all": True})
    users = {u["email"]: u for u in node.bundle["reference"]["users"]}
    assert w["union_admin"]["email"] not in users  # the union's admin never reaches the Mac
    assert w["union_assoc_admin"]["email"] in users  # the owner's admin does, as a name
    assert users[w["union_assoc_admin"]["email"]]["password_hash"] == core.NO_LOGIN
    assert users[w["coordinator"]["email"]]["password_hash"].startswith("$2")  # prepares + staff
    assert users[w["judge"]["email"]]["password_hash"].startswith("$2")  # captures
    # the union's admin may still prepare (coordination via its scope): then it travels, with a hash
    await _ok(await client.post(f"{API}/{event['id']}/offline/nodes/{node.id}/close", json={"reason": "prueba"},
                                headers=hdr(w["union_assoc_admin"])))
    await _exec("UPDATE users SET mfa_enabled = true, mfa_secret = 'JBSWY3DPEHPK3PXP' WHERE id = :id",
                id=uuid.UUID(w["union_admin"]["id"]))
    by_union = Node("Por la unión")
    await by_union.prepare(client, event, hdr(w["union_admin"]), {"all": True})
    users = {u["email"]: u for u in by_union.bundle["reference"]["users"]}
    assert users[w["union_admin"]["email"]]["password_hash"].startswith("$2")
