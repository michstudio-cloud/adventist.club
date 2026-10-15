#!/usr/bin/env bash
# Prueba REAL de «nodos vecinos» con dos procesos, sin Docker: una «nube» (base nodo_v3_malla_nube)
# prepara DOS nodos del mismo evento por el API de verdad (POST /offline/export, alcances distintos),
# cada paquete se carga en su base de nodo (nodo_v3_malla_a / _b) como lo hace `nodo_sync.py prepare`,
# y se arrancan dos `scripts/nodo_malla.py servir` en 127.0.0.1 (puertos distintos). Cada uno recibe
# el URL del otro en su malla-vecinos.json (lo que haría la app con Bonjour). Comprueba:
#   - se pasan su avance: lo de B aparece en A (sólo lectura, nodo_malla_filas) y al revés;
#   - una petición sin firma y la de un nodo desconocido se rechazan (403) y salen en «rechazados»;
#   - un traslape (premio automático de llegada de A en una actividad de B) se reporta y no se aplica;
#   - el plan de subida a la nube de A (y de B) es idéntico al de antes del intercambio;
#   - los subcomandos identidad / emparejar-codigo / emparejar-aceptar / estado / tirar.
# Limpia todo al salir (bases, procesos, carpeta temporal).
#
#   nodo-evento/mac/pruebas/malla-dos-nodos.sh
#   Variables: MALLA_PG (postgresql://test@127.0.0.1:55432), PY (python del venv),
#              MALLA_PUERTO_A (18101), MALLA_PUERTO_B (18102)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
BACKEND="$ROOT/backend"
PY="${PY:-$ROOT/../venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
PG="${MALLA_PG:-postgresql://test@127.0.0.1:55432}"
PA="${MALLA_PUERTO_A:-18101}"; PB="${MALLA_PUERTO_B:-18102}"
NUBE="$PG/nodo_v3_malla_nube"; DBA="$PG/nodo_v3_malla_a"; DBB="$PG/nodo_v3_malla_b"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/malla-dos-nodos.XXXXXX")"
PIDS=()

dropdbs() {
  for db in nodo_v3_malla_nube nodo_v3_malla_a nodo_v3_malla_b; do
    psql -X -q "$PG/postgres" -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" >/dev/null 2>&1 || true
  done
}
limpiar() {
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  # por si acaso: ningún «servir» de esta prueba queda vivo (escribiría otra vez en $WORK)
  pkill -f "nodo_malla.py servir --puerto ($PA|$PB) --host 127.0.0.1" 2>/dev/null || true
  dropdbs
  rm -rf "$WORK"
}
trap limpiar EXIT
paso() { printf '\n== %s\n' "$*"; }

paso "Bases: nube + dos nodos (db_bootstrap.sh)"
dropdbs
bash "$BACKEND/scripts/db_bootstrap.sh" --create --test-fixtures "$NUBE" >/dev/null
bash "$BACKEND/scripts/db_bootstrap.sh" --create "$DBA" >/dev/null
psql -X -q "$PG/postgres" -c "CREATE DATABASE nodo_v3_malla_b TEMPLATE nodo_v3_malla_a" >/dev/null
echo "ok"

cat > "$WORK/ayuda.py" <<'PYEOF'
"""Helper of malla-dos-nodos.sh: setup (cloud + two nodes), plan (cloud dry-run), checks."""
import asyncio, json, os, sys, time, urllib.error, urllib.request, uuid
from datetime import datetime, timedelta, timezone

WORK, NUBE, DBA, DBB = os.environ["WORK"], os.environ["NUBE"], os.environ["DBA"], os.environ["DBB"]
os.environ.update(DATABASE_URL=NUBE, JWT_SECRET="nube-jwt-" + "n" * 32, EVENT_PASS_SECRET="nube-pases-" + "p" * 20,
                  RATE_LIMIT_ENABLED="false", ENV="test", NODO_SETTLE_SECONDS="0")
SECRETS = {"a": os.environ["SECRETO_A"], "b": os.environ["SECRETO_B"]}

from app.services import event_pass, nodo_crypto as nc, nodo_malla as malla, nodo_sync_core as core  # noqa: E402


def save(name, data):
    with open(os.path.join(WORK, name), "w") as fh:
        json.dump(data, fh, default=str)


def load(name):
    with open(os.path.join(WORK, name)) as fh:
        return json.load(fh)


async def setup():
    import httpx
    from app.db import engine
    from app.main import app
    from app.security import create_access_token

    cloud = await core.connect(NUBE)
    run = uuid.uuid4().hex[:6]

    async def org(label, kind, parent=None):
        oid = uuid.uuid4()
        path = f"{parent['path']}.{oid.hex}" if parent else oid.hex
        await cloud.execute("INSERT INTO organizations (id, parent_id, type, name, status, path) VALUES"
                            " ($1, $2, $3, $4, 'active', text2ltree($5))", oid,
                            uuid.UUID(parent["id"]) if parent else None, kind, f"{label} {run}", path)
        return {"id": str(oid), "path": path}

    async def user(label, role, org_id=None):
        uid = uuid.uuid4()
        await cloud.execute("INSERT INTO users (id, email, password_hash, name, role, organization_id) VALUES"
                            " ($1, $2, '$2b$04$abcdefghijklmnopqrstuuJ2sQ0iY5Q6a2uZxv5x1a4xjv1W3Gk7a', $3, $4, $5)",
                            uid, f"{label}-{run}@example.com", label, role, uuid.UUID(org_id) if org_id else None)
        return {"id": str(uid), "email": f"{label}-{run}@example.com"}

    assoc = await org("Asociación", "association")
    church = await org("Iglesia", "church", assoc)
    clubs = {k: await org(f"Club {k.upper()}", "club", church) for k in "abc"}
    for c in clubs.values():
        await cloud.execute("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug ="
                            " 'adventurers') WHERE id = $1", uuid.UUID(c["id"]))
    admin = await user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    coord_a, coord_b, judge = (await user("coord-a", "INSTRUCTOR"), await user("coord-b", "INSTRUCTOR"),
                               await user("juez", "STUDENT"))

    def h(u):
        return {"Authorization": f"Bearer {create_access_token(uuid.UUID(u['id']), auth_time=int(time.time()))}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nube") as http:
        async def ok(r, code=200):
            assert r.status_code == code, r.text
            return r.json()
        ev = await ok(await http.post("/api/v1/events", headers=h(admin), json={
            "organization_id": assoc["id"], "ministry": "adventurers", "name": f"Camporee malla {run}",
            "starts_on": "2026-11-20", "ends_on": "2026-11-22"}), 201)
        url = f"/api/v1/events/{ev['id']}"
        acts = {}
        for key, body in (("senal", {"name": "Señal", "kind": "participation", "max_points": 150,
                                     "config": {"max": 150, "auto_award": "on_checkin"}}),
                          ("marcha", {"name": "Marcha", "kind": "participation", "max_points": 50,
                                      "config": {"max": 50}})):
            acts[key] = (await ok(await http.post(f"{url}/activities", headers=h(admin), json=body), 201))["id"]
        for u, role in ((coord_a, "COORDINATOR"), (coord_b, "COORDINATOR"), (judge, "JUDGE")):
            await ok(await http.post(f"{url}/staff", headers=h(admin), json={"user_id": u["id"], "role": role}), 201)
        regs = {k: (await ok(await http.post(f"{url}/registrations", headers=h(admin),
                                             json={"club_id": c["id"]}), 201))["id"] for k, c in clubs.items()}
        for st in ("OPEN", "IN_PROGRESS"):
            await ok(await http.post(f"{url}/status", headers=h(admin), json={"status": st}))
        # Two coordinators prepare their nodes (A: Señal + llegadas; B, later: Marcha, sin llegadas).
        nodes = {}
        for key, coord, scope in (("a", coord_a, {"activity_ids": [acts["senal"]], "check_in": True}),
                                  ("b", coord_b, {"activity_ids": [acts["marcha"]], "check_in": False})):
            box_priv, box_pub = nc.new_box_keypair()
            sign_priv, sign_pub = nc.new_sign_keypair()
            ans = await ok(await http.post(f"{url}/offline/export", headers=h(coord), json={
                "node_label": f"Zona {key.upper()}", "recipient_key": nc.b64(box_pub),
                "sign_key": nc.b64(sign_pub), "scope": scope}))
            bundle = nc.open_sealed(ans, box_priv, ev["id"])
            nodes[key] = (bundle, sign_priv, sign_pub)
    await engine.dispose()
    await cloud.close()

    for key, dsn in (("a", DBA), ("b", DBB)):
        bundle, sign_priv, sign_pub = nodes[key]
        info = bundle["node"]
        local = {"id": info["id"], "label": info["label"], "scope": info["scope"], "sign_key": nc.b64(sign_pub),
                 "import_key": bundle["import_key"],
                 "node_private_enc": nc.box(SECRETS[key], malla.NODE_KEY_PURPOSE, sign_priv)}
        conn = await core.connect(dsn)
        try:
            await core.load_into_node(conn, bundle, replace=False, local_logo=None, export_id=info["export_id"],
                                      pass_encrypt=lambda t, s=SECRETS[key]: event_pass.encrypt(t, secret=s),
                                      local_node=local)
            stored = await malla.store_bundle_mesh(conn, uuid.UUID(ev["id"]), bundle.get("mesh"))
            print(f"  nodo {key.upper()} cargado · vecinos del paquete: {stored['peers']}")
        finally:
            await conn.close()

    # Captures on each node (as their APIs would write them).
    now = datetime.now(timezone.utc)
    ids = {"event_id": ev["id"], "node_a": nodes["a"][0]["node"]["id"], "node_b": nodes["b"][0]["node"]["id"],
           "admin": admin["email"], "regs": regs, "acts": acts}
    a = await core.connect(DBA)
    try:
        ci, ea, auto = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await a.execute("INSERT INTO event_check_ins (id, registration_id, checked_in_at, checked_in_by_id,"
                        " checked_in_as, method) VALUES ($1, $2, $3, $4, 'COORDINATION', 'PASS')",
                        ci, uuid.UUID(regs["b"]), now - timedelta(minutes=3), uuid.UUID(coord_a["id"]))
        for rid, reg, act, pts, how, cin in ((ea, regs["a"], acts["senal"], 140, "JUDGE", None),
                                             (auto, regs["b"], acts["marcha"], 50, "AUTO_CHECKIN", ci)):
            await a.execute(
                "INSERT INTO evaluations (id, registration_id, activity_id, inputs, points, rules_version, judge_id,"
                " captured_as, idempotency_key, check_in_id) VALUES ($1, $2, $3, $4::jsonb, $5, 1, $6, $7, $8, $9)",
                rid, uuid.UUID(reg), uuid.UUID(act), json.dumps({"points": pts}), pts, uuid.UUID(judge["id"]), how,
                f"m-{uuid.uuid4().hex}", cin)
        ids.update(a_check_in=str(ci), a_eval=str(ea), a_auto=str(auto))
    finally:
        await a.close()
    b = await core.connect(DBB)
    try:
        eb, adj = uuid.uuid4(), uuid.uuid4()
        await b.execute(
            "INSERT INTO evaluations (id, registration_id, activity_id, inputs, points, rules_version, judge_id,"
            " captured_as, idempotency_key) VALUES ($1, $2, $3, '{\"points\": 30}', 30, 1, $4, 'JUDGE', $5)",
            eb, uuid.UUID(regs["a"]), uuid.UUID(acts["marcha"]), uuid.UUID(judge["id"]), f"m-{uuid.uuid4().hex}")
        await b.execute("INSERT INTO event_adjustments (id, registration_id, activity_id, kind, points, reason)"
                        " VALUES ($1, $2, $3, 'PENALTY', 3, 'llegó tarde a la marcha')",
                        adj, uuid.UUID(regs["c"]), uuid.UUID(acts["marcha"]))
        ids.update(b_eval=str(eb), b_adj=str(adj))
    finally:
        await b.close()
    save("ids.json", ids)
    print("  capturas: A = 1 llegada + 1 evaluación + 1 premio automático (de una actividad de B);"
          " B = 1 evaluación + 1 ajuste")


async def plan(out):
    ids = load("ids.json")
    result = {}
    for key, dsn in (("a", DBA), ("b", DBB)):
        node, cloud = await core.connect(dsn), await core.connect(NUBE)
        try:
            changes = await core.gather_node_changes(node, uuid.UUID(ids["event_id"]))
            actor = await core.find_actor(cloud, ids["admin"])
            rep = await core.import_changes(cloud, uuid.UUID(ids["event_id"]), changes, actor,
                                            node_id=uuid.UUID(ids[f"node_{key}"]), prefer=None, dry_run=True)
            rows = {t: sorted(core.canon(r, t) for r in v) for t, v in changes["event"].items()}
        finally:
            await node.close()
            await cloud.close()
        result[key] = {"plan": {k: rep[k] for k in ("insert", "update", "delete", "unchanged")},
                       "conflicts": sorted([c["table"], c["id"], c["what"]] for c in rep["conflicts"]),
                       "rows": rows}
    save(out, result)
    print(f"  A sube: {result['a']['plan']['insert']} · B sube: {result['b']['plan']['insert']}")


def post(url, body, headers):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


async def check():
    ids = load("ids.json")
    eid = uuid.UUID(ids["event_id"])
    fails = []

    def expect(cond, text):
        print(("  OK  " if cond else "  FALLA ") + text)
        if not cond:
            fails.append(text)

    a, b = await core.connect(DBA), await core.connect(DBB)
    try:
        fa = {str(r["row_id"]) for r in await a.fetch("SELECT row_id FROM nodo_malla_filas WHERE event_id = $1", eid)}
        fb = {str(r["row_id"]) for r in await b.fetch("SELECT row_id FROM nodo_malla_filas WHERE event_id = $1", eid)}
        expect({ids["b_eval"], ids["b_adj"]} <= fa, "lo de B (evaluación + ajuste) llegó a A como sólo lectura")
        expect(bool(await a.fetchval("SELECT 1 FROM evaluations WHERE id = $1", uuid.UUID(ids["b_eval"]))),
               "la evaluación de B está en la base de A (la clasificación de A ya la cuenta)")
        expect({ids["a_eval"], ids["a_check_in"]} <= fb, "lo de A (evaluación + llegada) llegó a B")
        expect(ids["a_auto"] not in fb and not await b.fetchval("SELECT 1 FROM evaluations WHERE id = $1",
                                                                uuid.UUID(ids["a_auto"])),
               "el traslape (premio automático de A en «Marcha») NO se aplicó en B")
        mirror = await b.fetchval("SELECT checked_in_at FROM event_registrations WHERE id = $1",
                                  uuid.UUID(ids["regs"]["b"]))
        expect(mirror is not None, "B (sin mesa de llegadas) ve la llegada del club registrada en A")
    finally:
        await a.close()
        await b.close()

    est_b = load("b/malla-estado.json")
    tras = [t for v in est_b["vecinos"] for t in v["traslapes"]]
    expect(any(t["id"] == ids["a_auto"] for t in tras), "el estado de B reporta el traslape: "
           + next((t["que"] for t in tras if t["id"] == ids["a_auto"]), "—"))
    est_a = load("a/malla-estado.json")
    expect([v["label"] for v in est_a["vecinos"]] == ["Zona B"] and est_a["vecinos"][0]["origen"] == "aval",
           "A aceptó a B (preparado después) por el aval de la nube")
    expect([v["origen"] for v in est_b["vecinos"]] == ["paquete"], "B conocía a A por su paquete")

    # Requests that must be refused
    url = f"http://127.0.0.1:{os.environ['PA']}/nodo-malla/v1/avance"
    body = json.dumps({"event_id": ids["event_id"], "from_node": ids["node_b"], "ts": int(time.time()),
                       "epk": nc.b64(nc.new_box_keypair()[1])}).encode()
    status, out = post(url, body, {})
    expect(status == 403 and "sin firma" in out.get("detail", ""), f"petición sin firma -> {status} «{out.get('detail')}»")
    priv, pub = nc.new_sign_keypair()
    stranger = {"event_id": ids["event_id"], "node_id": str(uuid.uuid4()), "label": "Mac extraña",
                "scope": core.normalize_scope({"activity_ids": [], "check_in": False}), "sign_key": nc.b64(pub),
                "voucher": None, "private": priv}
    body, headers, _ = malla.build_request(stranger)
    status, out = post(url, body, headers)
    expect(status == 403 and "desconocido" in out.get("detail", ""),
           f"nodo desconocido (firma válida, sin aval) -> {status} «{out.get('detail')}»")
    with urllib.request.urlopen(f"http://127.0.0.1:{os.environ['PA']}/nodo-malla/v1/hola", timeout=5) as r:
        hola = json.loads(r.read())
    expect(set(hola) == {"format", "event_id", "node_id", "label", "scope", "sign_key", "voucher"},
           "«hola» sólo trae la identidad pública")
    if fails:
        sys.exit(1)


def same(before, after):
    ok = True
    p0, p1 = load(before), load(after)
    for key in ("a", "b"):
        eq = p0[key] == p1[key]
        print(("  OK  " if eq else "  FALLA ") + f"la subida a la nube del nodo {key.upper()} es idéntica a la de antes"
              f" del intercambio (plan: {p1[key]['plan']['insert']}, conflictos: {len(p1[key]['conflicts'])})")
        ok &= eq
    rej = load("a/malla-estado.json")["rechazados"]
    print(("  OK  " if rej else "  FALLA ") + f"«rechazados» de A: {[r['motivo'] for r in rej][:3]}")
    sys.exit(0 if ok and rej else 1)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "setup":
        asyncio.run(setup())
    elif cmd == "plan":
        asyncio.run(plan(sys.argv[2]))
    elif cmd == "check":
        asyncio.run(check())
    elif cmd == "same":
        same(sys.argv[2], sys.argv[3])
PYEOF

export WORK NUBE DBA DBB PA PB
export SECRETO_A="secreto-nodo-a-$(openssl rand -hex 12)" SECRETO_B="secreto-nodo-b-$(openssl rand -hex 12)"
ayuda() { (cd "$BACKEND" && PYTHONPATH="$BACKEND" "$PY" "$WORK/ayuda.py" "$@"); }
malla_exec() {  # malla_exec a|b <subcomando> …  (reemplaza al shell: úsalo en un subshell o con &)
  local k="$1"; shift
  local db secret
  if [ "$k" = a ]; then db="$DBA"; secret="$SECRETO_A"; else db="$DBB"; secret="$SECRETO_B"; fi
  cd "$BACKEND"
  exec env -u DATABASE_URL NODE_DATABASE_URL="$db" EVENT_PASS_SECRET="$secret" JWT_SECRET="$secret" \
    NODO_RUN_DIR="$WORK/$k" "$PY" scripts/nodo_malla.py "$@"
}
malla_cli() { (malla_exec "$@"); }

paso "Nube: evento + dos nodos preparados por el API (alcances distintos) y cargados"
ayuda setup
paso "Plan de subida a la nube ANTES del intercambio"
ayuda plan plan-antes.json

paso "Dos procesos «servir» (A :$PA, B :$PB) y cada uno ve al otro en malla-vecinos.json"
mkdir -p "$WORK/a" "$WORK/b"
printf '[{"url": "http://127.0.0.1:%s", "nombre": "Mac B", "visto": %s}]\n' "$PB" "$(date +%s)" > "$WORK/a/malla-vecinos.json"
printf '[{"url": "http://127.0.0.1:%s", "nombre": "Mac A", "visto": %s}]\n' "$PA" "$(date +%s)" > "$WORK/b/malla-vecinos.json"
for k in a b; do
  port="$PA"; [ "$k" = b ] && port="$PB"
  malla_exec "$k" servir --puerto "$port" --host 127.0.0.1 --cada 1 > "$WORK/servir-$k.log" 2>&1 &
  PIDS+=("$!")
done
listo=0
for _ in $(seq 1 60); do
  sleep 0.5
  if "$PY" - "$WORK" <<'PYEOF' 2>/dev/null; then listo=1; break; fi
import json, sys
for k in "ab":
    st = json.load(open(f"{sys.argv[1]}/{k}/malla-estado.json"))
    assert st["vecinos"] and all(v["ultima_sync"] for v in st["vecinos"])
PYEOF
done
[ "$listo" = 1 ] || { echo "FALLA: no se sincronizaron en 30 s"; cat "$WORK"/servir-*.log; exit 1; }
echo "ok: los dos estados muestran la última sincronización con el vecino"

paso "Comprobaciones"
ayuda check
sleep 2  # un ciclo más: los rechazos quedan en malla-estado.json

paso "Subcomandos para la app"
malla_cli a identidad | grep '^@@ok'
CODIGO="$(malla_cli a emparejar-codigo | sed -n 's/^@@json //p' | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["codigo"])')"
echo "  código de A: ${CODIGO:0:40}… (${#CODIGO} caracteres)"
malla_cli b emparejar-aceptar "$CODIGO" | grep '^@@ok'
malla_cli b tirar "http://127.0.0.1:$PA" | grep '^@@'
malla_cli b estado | sed -n 's/^@@json //p' > "$WORK/estado-b.json"
"$PY" - "$WORK/estado-b.json" <<'PYEOF'
import json, sys
st = json.load(open(sys.argv[1]))
yo, v = st["yo"], st["vecinos"][0]
print(f"  estado de B: yo «{yo['label']}» {yo['actividades']} · vecino «{v['label']}» ({v['origen']}) {v['actividades']}"
      f" · filas {v['filas_recibidas']} · llegadas {v['llegadas_recibidas']} · traslapes {len(v['traslapes'])}")
PYEOF

paso "Plan de subida DESPUÉS del intercambio (debe ser idéntico)"
ayuda plan plan-despues.json
ayuda same plan-antes.json plan-despues.json

printf '\nRESULTADO: OK — nodos vecinos probados con dos procesos reales\n'
