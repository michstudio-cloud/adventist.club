"""What leaves an event node (nodo-evento/docs/EGRESO.md): event data only, never host data.

Proved here:
  * the allow-list mirrors the real schema (a migration that adds a column fails here until the
    column is reviewed and added to nodo_egreso) and the sync/mesh table lists;
  * the scrubber gives the same output as depurar.pl and Depurador.swift on the shared cases
    (nodo-evento/mac/pruebas/depurador/casos.txt), including the real incident shape
    `--header X-Goog-Api-Key: AQ.…`;
  * the receiving side REJECTS unknown keys/tables/columns, audit rows with an IP, host metadata
    or foreign entities, secrets anywhere in the rows, and avances with audit/passes — without
    echoing the value;
  * a REAL node database (bootstrapped and loaded like `nodo_sync.py prepare`) with host data
    planted in its audit trail serializes an upload and an avance that carry no host identifier
    (host name, user name, home path, MACs of this Mac, this process's arguments, the planted
    token), and both pass the validator;
  * the import endpoint answers 422 (with the reason, not the value) to a sealed + signed upload
    that carries something else.
"""
import getpass
import json
import re
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.services import nodo_egreso as eg
from app.services import nodo_malla as malla
from app.services import nodo_sync_core as core
from tests.conftest import RUN, fetch_all, module_factory, requires_db
from tests.test_nodo_api import Node, _fast, hdr, make_event, w  # noqa: F401 (fixtures)
from tests.test_nodo_malla import SECRETS, _capture, _me, _now, mesh, node_dbs  # noqa: F401 (fixtures)

factory = module_factory("nodoegreso")
CASES = Path(__file__).resolve().parents[2] / "nodo-evento" / "mac" / "pruebas" / "depurador" / "casos.txt"


def _repeat(s: str, n: int) -> str:
    return (s * (n // len(s) + 1))[:n]


# Fake secrets built at run time (never written as such in the repo; = probar.pl and main.swift).
FAKE = {
    "AIZA": "AI" + "za" + _repeat("Xy9_", 35), "AQ": "AQ" + "." + _repeat("Ab3-", 40),
    "YA29": "ya" + "29." + _repeat("a0B_", 32), "GHP": "gh" + "p_" + _repeat("Z9y8", 36),
    "GHO": "gh" + "o_" + _repeat("K7j6", 36), "GHPAT": "github" + "_pat_" + _repeat("11AB", 48),
    "SK": "sk" + "-ant-" + _repeat("q1W2", 32), "XOX": "xo" + "xb-" + _repeat("12345-", 24),
    "JWT": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0." + _repeat("dGVzdA", 30),
    "B64": _repeat("QUJD", 48),
    "PEM": "-----BEGIN " + "PRIVATE KEY-----\n" + _repeat("MIIEvQ", 64) + "\n" + _repeat("AbCd", 64)
           + "\n-----END " + "PRIVATE KEY-----",
    "AWS": "AK" + "IA" + "ABCDEFGHIJKLMNOP", "EVP": "evp" + "_" + _repeat("Pq7-", 43),
}
NAMES = ["Mac de Prueba Ñandú", "ana-prueba"]


# --------------------------------------------------------------------------------------------
# Allow-list vs schema and the other modules
# --------------------------------------------------------------------------------------------
def test_allowlist_tables_match_sync_and_mesh():
    assert set(eg.EVENT_COLUMNS) == set(core.EVENT_TABLE_NAMES)
    assert eg.AUDIT_ENTITIES == set(core.AUDIT_ENTITIES)
    assert eg.AVANCE_TABLES == set(malla.CAPTURE_TABLES)


@requires_db
async def test_allowlist_matches_the_schema():
    tables = [*eg.EVENT_COLUMNS, "users", "audit_log"]
    rows = await fetch_all("SELECT table_name, column_name FROM information_schema.columns"
                           " WHERE table_schema = 'public' AND table_name = ANY(:t)", t=tables)
    schema: dict[str, set] = {}
    for r in rows:
        schema.setdefault(r["table_name"], set()).add(r["column_name"])
    for table, cols in eg.EVENT_COLUMNS.items():
        assert schema[table] == set(cols), f"{table}: revisar nodo_egreso.EVENT_COLUMNS"
    assert schema["users"] == set(eg.USER_COLUMNS), "users: revisar nodo_egreso.USER_COLUMNS"
    assert schema["audit_log"] == set(eg.AUDIT_COLUMNS), "audit_log: revisar nodo_egreso.AUDIT_COLUMNS"


# --------------------------------------------------------------------------------------------
# Scrubber: the shared cases
# --------------------------------------------------------------------------------------------
def _cases() -> list[dict]:
    out, sec = [], None
    for line in CASES.read_text(encoding="utf-8").splitlines(keepends=True):
        if not out and line.startswith("#") and not line.startswith("### "):
            continue
        if line.startswith("### "):
            out.append({"nombre": line[4:].strip()})
            sec = None
        elif line.rstrip("\n") in ("--- entrada", "--- esperado", "--- procesos"):
            sec = line.strip()[4:]
            out[-1][sec] = ""
        elif sec:
            out[-1][sec] += line
    return out


def _expand(text: str) -> str:
    return re.sub(r"\{\{([A-Z0-9]+)\}\}", lambda m: FAKE[m[1]], text)


@pytest.mark.skipif(not CASES.exists(), reason="nodo-evento/ not next to backend/")
def test_scrubber_shared_cases():
    cases = _cases()
    assert len(cases) >= 12
    for case in cases:
        got = eg.scrub_text(_expand(case["entrada"]), NAMES)
        assert got == case["esperado"], case["nombre"]
        assert eg.find_secrets(got) == [], case["nombre"]


def test_incident_shape_never_survives():
    line = f"  812 /usr/bin/curl -sS https://x.googleapis.com --header X-Goog-Api-Key: {FAKE['AQ']}"
    assert FAKE["AQ"] not in eg.scrub_text(line)
    assert "cabecera con secreto" in eg.find_secrets(line) and "token de Google (AQ.)" in eg.find_secrets(line)


# --------------------------------------------------------------------------------------------
# Validator (receiving side) and sanitizer (node side), pure
# --------------------------------------------------------------------------------------------
def _bundle() -> dict:
    ev, reg = str(uuid.uuid4()), str(uuid.uuid4())
    return {
        "format": core.CHANGES_FORMAT, "event_id": ev, "node_id": str(uuid.uuid4()), "export_id": str(uuid.uuid4()),
        "copied_at": "2026-11-20T08:00:00+00:00", "created_at": "2026-11-20T18:00:00+00:00",
        "event": {"events": [{"id": ev, "name": "Camporee"}],
                  "evaluations": [{"id": str(uuid.uuid4()), "registration_id": reg, "inputs": {"points": 10}}]},
        "users": [{"id": str(uuid.uuid4()), "email": "juez@ejemplo.org", "name": "Juez", "password_hash": "$2b$04$x",
                   "mfa_secret": "JBSWY3DPEHPK3PXP"}],
        "audit": [{"id": str(uuid.uuid4()), "action": "CREATE", "entity_type": "EVALUATION", "entity_id": reg,
                   "details": "captura", "metadata_json": {"event_id": ev}, "ip_address": None}],
        "passes": {reg: "PASE-123"},
    }


def _rejects(bundle, *fragments, avance=False):
    with pytest.raises(eg.EgresoError) as exc:
        eg.validate_changes(bundle, avance=avance)
    message = str(exc.value)
    for fragment in fragments:
        assert fragment in message, message
    for value in FAKE.values():
        assert value not in message
    return message


def test_validator_accepts_event_data_and_rejects_everything_else():
    eg.validate_changes(_bundle())
    b = _bundle(); b["host"] = {"hostname": "x"}
    _rejects(b, "clave desconocida «host»")
    b = _bundle(); b["event"]["config_env"] = [{"id": "x"}]
    _rejects(b, "tabla no permitida «config_env»")
    b = _bundle(); b["event"]["evaluations"][0]["ps"] = "curl --header …"
    _rejects(b, "columna desconocida «evaluations.ps»")
    b = _bundle(); b["users"][0]["home"] = "/Users/x"
    _rejects(b, "dato de cuenta no permitido «home»")
    b = _bundle(); b["audit"][0]["ip_address"] = "192.168.8.23"
    _rejects(b, "bitácora con IP")
    b = _bundle(); b["audit"][0]["entity_type"] = "USER"
    _rejects(b, "bitácora que no es del evento")
    b = _bundle(); b["audit"][0]["metadata_json"] = {"event_id": "x", "hostname": "Mac de Ana"}
    _rejects(b, "bitácora con datos del equipo")
    b = _bundle(); b["audit"][0]["user_agent"] = "curl"
    _rejects(b, "columna de bitácora desconocida «user_agent»")
    # the incident shape hidden in event data / audit text / metadata
    b = _bundle(); b["event"]["evaluations"][0]["inputs"] = {"nota": f"--header X-Goog-Api-Key: {FAKE['AQ']}"}
    _rejects(b, "token de Google (AQ.) en «evaluations.inputs»")
    b = _bundle(); b["audit"][0]["details"] = f"export GOOGLE_API_KEY={FAKE['AIZA']}"
    _rejects(b, "llave de Google (AIza) en «audit.details»")
    b = _bundle(); b["audit"][0]["metadata_json"] = {"event_id": "x", "nota": f"/Users/ana/.ssh/{FAKE['PEM']}"}
    _rejects(b, "bloque PEM privado", "ruta con nombre de usuario")
    b = _bundle(); b["event"]["events"][0]["source_note"] = "ether 3c:22:fb:1a:2b:3c"
    _rejects(b, "MAC en «events.source_note»")
    b = _bundle(); b["passes"] = {"no-es-uuid": "x"}
    _rejects(b, "«passes» con una clave")
    # the event's own secrets (password hash, MFA seed, pass ciphertext) are not «secrets found»
    b = _bundle(); b["users"][0]["password_hash"] = FAKE["JWT"]
    eg.validate_changes(b)


def test_validator_avance():
    av = {"format": core.CHANGES_FORMAT, "event_id": "e", "node_id": "n", "label": "Zona A", "scope": {"all": True},
          "created_at": "x", "event": {"evaluations": []}, "users": [{"id": "u", "name": "Juez"}], "audit": [],
          "passes": None}
    eg.validate_changes(av, avance=True)
    _rejects({**av, "audit": [{"id": "x"}]}, "un avance no lleva bitácora", avance=True)
    _rejects({**av, "passes": {}}, "un avance no lleva pases", avance=True)
    _rejects({**av, "users": [{"id": "u", "name": "J", "email": "j@x"}]}, "dato de cuenta no permitido «email»", avance=True)
    _rejects({**av, "event": {"events": []}}, "tabla no permitida «events»", avance=True)
    _rejects({**av, "label": f"Zona {FAKE['GHP']}"}, "token de GitHub en «label»", avance=True)


def test_sanitize_keeps_event_data_only():
    b = _bundle()
    b["host"] = "x"
    b["event"]["evaluations"][0]["extra"] = 1
    b["event"]["nodo_malla_filas"] = [{"id": "x"}]
    b["users"][0]["home"] = "/Users/ana"
    b["audit"][0].update(ip_address="192.168.8.23", user_agent="x",
                         details=f"por ana desde /Users/ana-prueba/x token={FAKE['B64']}",
                         metadata_json={"event_id": "e", "hostname": "Mac de Ana", "nota": f"Bearer {FAKE['JWT']}"})
    b["audit"].append({"id": "y", "entity_type": "USER", "details": "/Users/ana"})
    out = eg.sanitize_changes(b, names=["ana-prueba"])
    eg.validate_changes(out)
    text = json.dumps(out)
    for planted in ("host", "extra", "nodo_malla_filas", "/Users/ana", "192.168.8.23", "user_agent", "Mac de Ana",
                    FAKE["B64"], FAKE["JWT"]):
        assert planted not in text, planted
    assert len(out["audit"]) == 1 and out["audit"][0]["ip_address"] is None
    assert out["audit"][0]["metadata_json"] == {"event_id": "e", "nota": "Bearer [oculto]"}
    assert out["users"][0]["password_hash"] == "$2b$04$x" and out["passes"] == b["passes"]
    av = eg.sanitize_changes({**b, "label": "Zona"}, avance=True)
    assert av["audit"] == [] and av["passes"] is None and set(av["event"]) == {"evaluations"}
    assert all(set(u) <= {"id", "name"} for u in av["users"])


# --------------------------------------------------------------------------------------------
# A REAL node database: nothing of this Mac in what it sends
# --------------------------------------------------------------------------------------------
def _host_identifiers() -> dict[str, str]:
    ids = {"nombre del equipo": socket.gethostname(), "usuario": getpass.getuser(), "carpeta personal": str(Path.home()),
           "argumentos de este proceso": " ".join(sys.argv[1:]) or sys.argv[0]}
    short = socket.gethostname().split(".")[0]
    if len(short) >= 3:
        ids["nombre corto del equipo"] = short
    if shutil.which("scutil"):
        for key in ("ComputerName", "LocalHostName"):
            value = subprocess.run(["scutil", "--get", key], capture_output=True, text=True).stdout.strip()
            if len(value) >= 3:
                ids[key] = value
    ifconfig = shutil.which("ifconfig") or "/sbin/ifconfig"
    if Path(ifconfig).exists():
        out = subprocess.run([ifconfig], capture_output=True, text=True).stdout
        for i, mac in enumerate(sorted(set(re.findall(r"\bether ([0-9a-f:]{11,17})", out)))):
            if mac not in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
                ids[f"MAC {i}"] = mac
    return {k: v for k, v in ids.items() if v and len(v) >= 3}


@requires_db
@pytest.mark.skipif(shutil.which("psql") is None, reason="needs psql (db_bootstrap.sh)")
async def test_real_node_upload_and_avance_carry_no_host_data(mesh, w, monkeypatch):  # noqa: F811
    ev, dsn = mesh["event"], mesh["db"]["a"]
    eid = uuid.UUID(ev["id"])
    host = _host_identifiers()
    # What nodo-mac.sh passes to the sync/mesh containers (equipo_nombres).
    monkeypatch.setenv("NODO_EQUIPO_NOMBRES", "|".join(
        v for k, v in host.items() if k in ("nombre del equipo", "nombre corto del equipo", "usuario",
                                             "ComputerName", "LocalHostName")))
    token = FAKE["AQ"]
    await _capture(dsn, ev["regs"]["a"], mesh["A"], 120, judge=w["judge"]["id"])
    # Host data planted where a node could pick it up: its audit trail (event and foreign entities).
    conn = await core.connect(dsn)
    try:
        meta = {"event_id": ev["id"], "hostname": host["nombre del equipo"], "user_agent": "Nodo Camporee/324",
                "nota": f"curl --header X-Goog-Api-Key: {token} desde {host['carpeta personal']}/x"}
        await conn.execute(
            "INSERT INTO audit_log (id, action, entity_type, entity_id, details, metadata_json, ip_address, created_at)"
            " VALUES ($1, 'UPDATE', 'EVALUATION', $2, $3, $4::jsonb, '192.168.8.23', now() + interval '1 second'),"
            " ($5, 'UPDATE', 'USER', $2, $3, $4::jsonb, '127.0.0.1', now() + interval '1 second')",
            uuid.uuid4(), ev["id"], f"por {host['usuario']} en {host['nombre del equipo']}", json.dumps(meta),
            uuid.uuid4())
        changes = await core.gather_node_changes(conn, eid)
        avance = await malla.build_avance(conn, await _me(dsn, "a"))
    finally:
        await conn.close()
    assert changes["audit"], "the planted EVALUATION audit row travels (sanitized)"
    for kind, payload in (("subida", changes), ("avance", avance)):
        eg.validate_changes(payload, avance=kind == "avance")
        text = json.dumps(payload, default=str)
        for name, value in {**host, "token": token, "IP del teléfono": "192.168.8.23", "IP local": "127.0.0.1"}.items():
            assert value not in text, f"{kind}: {name}"
        assert not re.search(r"/Users/(?!USUARIO\b)[^/\"]+/", text), kind
    assert all(a["entity_type"] in eg.AUDIT_ENTITIES and a["ip_address"] is None for a in changes["audit"])


# --------------------------------------------------------------------------------------------
# The import endpoint refuses (sealed and signed correctly, but not event data)
# --------------------------------------------------------------------------------------------
@requires_db
async def test_import_endpoint_rejects_non_event_data(client, w):  # noqa: F811
    event = await make_event(client, w, "Egreso")
    node = Node("Nodo 1")
    await node.prepare(client, event, hdr(w["coordinator"]), {"activity_ids": [event["acts"]["A"]]})
    h = hdr(w["coordinator"])
    await node.upload(client, h, dry_run=True)  # the normal upload passes
    bad = node.changes()
    bad["diagnostico"] = {"ps": f"curl --header X-Goog-Api-Key: {FAKE['AQ']}"}
    r = await node.upload(client, h, dry_run=True, body=node.body(bad), code=422)
    assert "clave desconocida «diagnostico»" in r["detail"] and FAKE["AQ"] not in r["detail"]
    bad = node.changes()
    bad["audit"] = [{"id": str(uuid.uuid4()), "action": "UPDATE", "entity_type": "EVALUATION", "entity_id": "x",
                     "ip_address": "192.168.8.23", "metadata_json": {"hostname": "Mac de Ana"}}]
    r = await node.upload(client, h, dry_run=True, body=node.body(bad), code=422)
    assert "bitácora con IP" in r["detail"] and "Mac de Ana" not in r["detail"]
    bad = node.changes()
    bad["event"]["evaluations"] = [{**node.capture(event["regs"]["a"], event["acts"]["A"], 10),
                                    "inputs": {"nota": f"GOOGLE_API_KEY={FAKE['AIZA']}"}}]
    r = await node.upload(client, h, dry_run=True, body=node.body(bad), code=422)
    assert "en «evaluations.inputs»" in r["detail"] and FAKE["AIZA"] not in r["detail"]
    assert RUN  # the event belongs to this run (cleanup by the w fixture)
