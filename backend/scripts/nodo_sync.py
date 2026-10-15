#!/usr/bin/env python3
"""Copies ONE event between production and a local event node (nodo-evento/README.md).

AUTOMATIC MODE (030, recommended; the Mac app «Nodo Camporee» runs these in the node's `sync`
container). No database connection string, no Cloudflare token, no production secret: the
coordinator logs in on the Mac, and the access token travels only in `NODO_API_TOKEN`.

    prepare   asks the API (POST /events/{id}/offline/export) for the event, sealed to a
              one-time key of this node, and loads it here: the node gets ITS OWN pass
              encryption (EVENT_PASS_SECRET of the node; the pass hashes do not change, so
              printed passes keep working), its scope (which activities / arrivals desk it
              captures) and, when the API has one, the TLS certificate (written to --tls-dir).
    upload    sends the node's changes, sealed for the API and signed with the key bound at
              prepare time; `--dry-run` only reports; `--partial` («avance») keeps the node open,
              `--final` closes it. Conflicts are never applied unless `--prefer node|prod`.
    node-info this node's scope and state (no network).

MANUAL MODE (029, backup without the API; direct database connections, as before):

    export    production -> node, the day before, and marks the event «en nodo local».
    import    node -> production, when internet is back (3-way merge, report, lock release).
    status    what each side holds now (counts, lock, last export/import).

Both manual directions write in ONE transaction per database. `--dry-run` prints the report and
changes nothing. Connection strings come ONLY from the environment or the arguments
(`PROD_DATABASE_URL`, `NODE_DATABASE_URL`); they are never printed (host and database only).
With `PROD_EVENT_PASS_SECRET` in the environment the node keeps its own EVENT_PASS_SECRET (the
passes are re-encrypted both ways); without it, as in 029, both secrets must be equal.

    python scripts/nodo_sync.py prepare --event <uuid> --node-label "Zona Misión Galáctica"
           [--all | --activities id,id] [--check-in | --no-check-in] [--replace-node-copy]
           [--replace-node NODE_ID] [--tls-dir DIR] [--media-dir DIR]
    python scripts/nodo_sync.py upload --event <uuid> [--dry-run] [--prefer node|prod] [--final | --partial]
    python scripts/nodo_sync.py node-info [--event <uuid>]
    python scripts/nodo_sync.py export --event <uuid> --actor-email <coordination e-mail>
           [--node-label "Mini PC La Morita"] [--all | --activities id,id] [--check-in | --no-check-in]
           [--dry-run] [--replace-node-copy] [--bundle-dir DIR] [--media-dir DIR] [--no-lock]
    python scripts/nodo_sync.py import --event <uuid> --actor-email <e-mail>
           [--dry-run] [--prefer node|prod] [--partial] [--report FILE]
    python scripts/nodo_sync.py status --event <uuid>

Output for the Mac app: «@@paso N/M …», «@@ok …», «@@aviso …», «@@error …» and, at the end of
prepare / upload / node-info, ONE line «@@json {…}».
Exit codes: 0 ok · 1 error (nothing written) · 2 conflicts (not applied; node stays open) ·
3 schema mismatch · 4 log in again (401) · 5 no permission / second factor needed (403).
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import asyncpg

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))  # backend/ (the app package)
os.environ.setdefault("DATABASE_URL", os.environ.get("NODE_DATABASE_URL") or "postgresql://unused@localhost/unused")

from app.services import nodo_crypto  # noqa: E402
from app.services import nodo_sync_core as core  # noqa: E402
from app.services.nodo_sync_core import (  # noqa: E402,F401  (re-exported: tests and older callers)
    AUDIT_ENTITIES,
    EVENT_TABLE_NAMES,
    EVENT_TABLES,
    IGNORED,
    INSERT_ONLY,
    USER_REFS,
    SchemaMismatch,
    SyncError,
    canon,
    clean_dsn,
    describe,
    diff_fields,
    fingerprint,
    plan_changes,
    snapshot_of,
)

DEFAULT_API = "https://api.adventist.club/api/v1"
NODE_KEY_PURPOSE = "nodo-sign-key"


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def say(*parts) -> None:
    print(*parts, flush=True)


def step(n: int, total: int, text: str) -> None:
    say(f"@@paso {n}/{total} {text}")


def emit_json(data: dict) -> None:
    say("@@json " + json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")))


def node_secret() -> str:
    from app.config import settings  # noqa: PLC0415 (needs DATABASE_URL in the environment)

    secret = settings.EVENT_PASS_SECRET or settings.JWT_SECRET
    if not secret:
        raise SyncError("el nodo no tiene EVENT_PASS_SECRET / JWT_SECRET")
    return secret


def pass_codec():
    from app.services import event_pass  # noqa: PLC0415

    return event_pass


# --------------------------------------------------------------------------------------------
# Logo + passes check (node side)
# --------------------------------------------------------------------------------------------
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
        say(f"@@aviso no se pudo copiar el logo del evento ({type(exc).__name__}); el nodo lo mostrará sin logo")
        return None
    return f"/nodo-media/events/{name}"


def mirror_media(bundle: dict, media_dir: str | None, *, max_bytes: int = 10_000_000,
                 total_limit: int = 500_000_000) -> dict:
    """Copies every media.adventist.club file the event shows into <media_dir>/mirror/<path>, so
    the node serves them offline under the SAME URL (its DNS answers media.adventist.club and
    Caddy serves the copy). Files already there are kept. Best effort: a failure is a warning."""
    urls = core.media_urls(bundle)
    out = {"total": len(urls), "downloaded": 0, "kept": 0, "failed": 0, "bytes": 0}
    if not media_dir or not urls:
        return out
    root = (Path(media_dir) / "mirror").resolve()
    for url in urls:
        path = urlsplit(url).path.lstrip("/")
        target = (root / path).resolve()
        if root not in target.parents or not path:
            out["failed"] += 1
            continue
        if target.exists() and target.stat().st_size > 0:
            out["kept"] += 1
            continue
        if out["bytes"] > total_limit:
            out["failed"] += 1
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nodo-camporee/1"})
            with urllib.request.urlopen(req, timeout=20) as response:  # noqa: S310 (fixed media host)
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("demasiado grande")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            out["downloaded"] += 1
            out["bytes"] += len(data)
        except Exception:  # noqa: BLE001
            out["failed"] += 1
    if out["failed"]:
        say(f"@@aviso {out['failed']} imagen(es) no se pudieron copiar al nodo: se verán vacías sin internet")
    return out


async def verify_passes(node, event_id: uuid.UUID) -> dict:
    """With the node's EVENT_PASS_SECRET (this process's settings), every stored pass must open.
    A pass that does not open would be REGENERATED the first time somebody shows it on the node,
    and the printed QR would stop working."""
    try:
        event_pass = pass_codec()
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
        if token is not None and core.sha256_hex(token) == r["pass_token_hash"]:
            ok += 1
        else:
            bad += 1
    return {"checked": True, "ok": ok, "unreadable": bad, "without_pass": none}


def prod_pass_tokens(rows: list[dict], prod_secret: str) -> dict[str, str]:
    event_pass = pass_codec()
    tokens = {}
    for r in rows:
        token = event_pass.decrypt(r.get("pass_token_enc"), secret=prod_secret)
        if token and core.sha256_hex(token) == r.get("pass_token_hash"):
            tokens[r["id"]] = token
    return tokens


def scope_from(args) -> dict:
    if getattr(args, "activities", None):
        ids = [x.strip() for x in args.activities.split(",") if x.strip()]
        return core.normalize_scope({"all": False, "activity_ids": ids, "check_in": args.check_in})
    return core.normalize_scope({"all": True, "check_in": args.check_in})


# --------------------------------------------------------------------------------------------
# Manual mode: export
# --------------------------------------------------------------------------------------------
async def cmd_export(args) -> int:
    event_id = uuid.UUID(args.event)
    prod_dsn, node_dsn = args.prod_url, args.node_url
    prod_secret = os.environ.get("PROD_EVENT_PASS_SECRET") or None
    say(f"export · evento {event_id}\n  producción: {describe(prod_dsn)}\n  nodo:       {describe(node_dsn)}")
    prod, node = await core.connect(prod_dsn), await core.connect(node_dsn)
    try:
        await core.check_schemas(prod, node, ["ministries", "organizations", "organization_ministries", "users",
                                              "role_assignments", "club_memberships", "event_offline_syncs",
                                              "event_offline_nodes", *EVENT_TABLE_NAMES])
        actor = await core.find_actor(prod, args.actor_email)
        event = await prod.fetchrow("SELECT name, status, offline_locked_at FROM events WHERE id = $1", event_id)
        if event is None:
            raise SyncError("el evento no existe en producción")
        say(f"  evento: {event['name']} ({event['status']})")
        on_node = await node.fetchval("SELECT 1 FROM events WHERE id = $1", event_id)
        if on_node and not args.replace_node_copy:
            raise SyncError("el nodo ya tiene una copia de este evento (usa --replace-node-copy si es descartable)")
        scope = scope_from(args)

        if args.dry_run:
            async with prod.transaction(isolation="repeatable_read", readonly=True):
                bundle = await core.gather_export(prod, event_id, args.actor_email)
            report_export(bundle, locked_before=event["offline_locked_at"] is not None, dry=True)
            return 0

        node_row = None
        if args.no_lock:
            # Tests only: a copy without registering a node nor locking production.
            export_id = uuid.uuid4()
            async with prod.transaction(isolation="repeatable_read"):
                bundle = await core.gather_export(prod, event_id, args.actor_email)
                await prod.execute(
                    "INSERT INTO event_offline_syncs (id, event_id, kind, node_label, actor_email, snapshot, report)"
                    " VALUES ($1, $2, 'EXPORT', $3, $4, $5::jsonb, $6::jsonb)",
                    export_id, event_id, args.node_label, actor["email"],
                    json.dumps(snapshot_of(bundle["event"])), json.dumps({"counts": core.counts(bundle["event"])}))
            bundle.update(node_label=args.node_label, actor_email=actor["email"])
        else:
            bundle, node_row, export_id = await core.prepare_node(
                prod, event_id, actor, label=args.node_label, scope=scope, sign_key=None,
                settle_seconds=args.settle_seconds, via="nodo_sync export")
        if prod_secret:
            bundle["passes"] = prod_pass_tokens(bundle["event"]["event_registrations"], prod_secret)
        if args.bundle_dir:
            path = Path(args.bundle_dir) / f"export-{event_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as fh:
                fh.write(json.dumps(bundle, ensure_ascii=False, default=str).encode())
            say(f"  copia del export: {path} (contiene hashes de contraseñas: no la compartas)")
        local_logo = mirror_logo(bundle["event"]["events"][0], args.media_dir)
        local = None
        if node_row is not None:
            local = {"id": node_row["id"], "label": node_row["label"], "scope": node_row["scope"]}
        try:
            passes = await core.load_into_node(
                node, bundle, replace=args.replace_node_copy, local_logo=local_logo, export_id=str(export_id),
                pass_encrypt=pass_codec().encrypt if prod_secret else None, local_node=local)
        except Exception:
            say("  ERROR al cargar el nodo: el nodo quedó sin cambios. Producción quedó registrada con este nodo:"
                " repite el export con --replace-node-copy, o pide a un administrador que cierre el nodo"
                " (POST /events/{id}/offline/nodes/{node}/close).")
            raise
        report_export(bundle, locked_before=event["offline_locked_at"] is not None, dry=False, no_lock=args.no_lock)
        media = mirror_media(bundle, args.media_dir)
        say(f"  imágenes para usar sin internet: {media['downloaded'] + media['kept']} de {media['total']}")
        if passes.get("reencrypted"):
            say(f"  pases re-cifrados con el secreto propio del nodo: {passes['ok']}"
                + (f" ({passes['missing']} sin pase legible en producción)" if passes["missing"] else ""))
        checked = await verify_passes(node, event_id)
        if checked.get("checked"):
            say(f"  pases en el nodo: {checked['ok']} legibles, {checked['unreadable']} ilegibles, "
                f"{checked['without_pass']} sin pase todavía")
            if checked["unreadable"]:
                say("  ¡ATENCIÓN! los pases no se pueden leer con el EVENT_PASS_SECRET del nodo: pasa "
                    "PROD_EVENT_PASS_SECRET (el de producción) para re-cifrarlos, o usa el mismo secreto.")
                return 1
        if node_row is not None:
            say(f"  nodo registrado: «{node_row['label']}» · {', '.join(node_row['activity_names'])}"
                f" · llegadas: {'sí' if node_row['scope']['check_in'] else 'no'}")
        return 0
    finally:
        await prod.close()
        await node.close()


def report_export(bundle: dict, *, locked_before: bool, dry: bool, no_lock: bool = False) -> None:
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
        say("  producción: evento bloqueado (en nodo local)" + (" — ya lo estaba" if locked_before else ""))


# --------------------------------------------------------------------------------------------
# Manual mode: import
# --------------------------------------------------------------------------------------------
async def cmd_import(args) -> int:
    event_id = uuid.UUID(args.event)
    prod_secret = os.environ.get("PROD_EVENT_PASS_SECRET") or None
    say(f"import · evento {event_id}{' · SIMULACIÓN' if args.dry_run else ''}\n"
        f"  nodo:       {describe(args.node_url)}\n  producción: {describe(args.prod_url)}")
    prod, node = await core.connect(args.prod_url), await core.connect(args.node_url)
    try:
        await core.check_schemas(prod, node, ["users", "audit_log", "event_offline_syncs", "event_offline_nodes",
                                              *EVENT_TABLE_NAMES])
        actor = await core.find_actor(prod, args.actor_email)
        changes = await core.gather_node_changes(
            node, event_id, pass_decrypt=pass_codec().decrypt if prod_secret else None)
        node_id = uuid.UUID(changes["node_id"]) if changes.get("node_id") else None
        if node_id is None:
            say("  copia preparada sin registro de nodo (029): se compara contra el último EXPORT")
        report = await core.import_changes(
            prod, event_id, changes, actor, node_id=node_id, prefer=args.prefer, dry_run=args.dry_run,
            final=not args.partial,
            pass_encrypt=(lambda token: pass_codec().encrypt(token, secret=prod_secret)) if prod_secret else None,
            via="nodo_sync import")
        print_report(report)
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            say(f"  reporte JSON: {args.report}")
        if args.dry_run:
            say("  SIMULACIÓN: no se escribió nada.")
        else:
            await remember_upload(node, event_id, report)
            say("  APLICADO en producción." + (" Bloqueo liberado." if report["lock_released"] else
                                               f" El bloqueo SIGUE ({report.get('open_nodes', 0)} nodo(s) abiertos,"
                                               " conflictos pendientes o --partial)." if report["locked"] else ""))
        return 2 if report["conflicts"] else 0
    finally:
        await prod.close()
        await node.close()


def print_report(report: dict) -> None:
    def block(title, data):
        say(f"  {title}:" + ("" if data else " —"))
        for table, n in data.items():
            say(f"    {table:<24} {n:>6}")
    if report.get("already_applied"):
        say("  Esta subida ya se había aplicado: nada nuevo.")
    block("nuevas (se insertan)", report["insert"])
    block("cambiadas en el nodo (se actualizan)", report["update"])
    block("borradas en el nodo (se borran)", report["delete"])
    block("sin cambios", report["unchanged"])
    say(f"  bitácora del nodo a copiar: {report['audit_rows']} fila(s)")
    for note in report.get("users", []):
        say(f"  {note}")
    for w in report["warnings"]:
        say(f"  aviso: {w.get('detail') or w['table'] + ' ' + w['id'] + ': ' + w['what']}"
            + (f" (resuelto: {w['resolved']})" if w.get("resolved") else ""))
    if report["conflicts"]:
        say(f"  CONFLICTOS ({len(report['conflicts'])}): no se aplican; revisa y repite con --prefer node|prod")
        for c in report["conflicts"]:
            say(f"    {c.get('detail') or c['table'] + ' ' + c['id'] + ': ' + c['what']}")
    else:
        say("  conflictos: ninguno")


async def remember_upload(node, event_id: uuid.UUID, report: dict) -> None:
    """The node's own row mirrors the cloud's answer (for the Mac app's panel)."""
    if report.get("dry_run") or not report.get("node_status"):
        return
    await node.execute(
        "UPDATE event_offline_nodes SET status = $2::varchar, uploads = uploads + 1, last_upload_at = now(),"
        " final_at = CASE WHEN $2::varchar = 'FINAL' THEN now() ELSE final_at END, conflicts = $3,"
        " last_report = $4::jsonb, updated_at = now() WHERE event_id = $1 AND local",
        event_id, report["node_status"], len(report["conflicts"]),
        json.dumps({k: report.get(k) for k in ("insert", "update", "delete", "lock_released", "open_nodes",
                                                 "node_status", "final")}, default=str))


# --------------------------------------------------------------------------------------------
# Automatic mode (API)
# --------------------------------------------------------------------------------------------
def api_base() -> str:
    base = (os.environ.get("NODO_API_URL") or DEFAULT_API).strip().rstrip("/")
    return base if base.endswith("/api/v1") else f"{base}/api/v1"


def api_call(method: str, path: str, *, body: bytes | None = None, headers: dict | None = None,
             timeout: float = 600) -> dict:
    token = os.environ.get("NODO_API_TOKEN")
    if not token:
        raise ApiError(401, "Falta la sesión de adventist.club (inicia sesión en la app)")
    req = urllib.request.Request(f"{api_base()}{path}", data=body, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "nodo-camporee/1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed API base)
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        raise ApiError(exc.code, detail or f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError(0, f"No hay conexión con {urlsplit(api_base()).hostname} ({type(exc).__name__})") from exc


def write_certificate(cert: dict, tls_dir: str | None) -> dict:
    out = {"status": cert.get("status"), "not_after": cert.get("not_after"), "names": cert.get("names"),
           "staging": cert.get("staging"), "message": cert.get("message"), "installed": False}
    if cert.get("status") != "ok" or not tls_dir:
        return out
    target = Path(tls_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, value, mode in (("fullchain.pem", cert["fullchain_pem"], 0o644), ("privkey.pem", cert["privkey_pem"], 0o600)):
        tmp = target / f".{name}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(value)
        os.replace(tmp, target / name)
    (target / "ca-local.pem").unlink(missing_ok=True)
    out["installed"] = True
    return out


async def cmd_prepare(args) -> int:
    event_id = uuid.UUID(args.event)
    total = 5
    step(1, total, "Preparando las llaves de este nodo")
    box_private, box_public = nodo_crypto.new_box_keypair()
    sign_private, sign_public = nodo_crypto.new_sign_keypair()
    node = await core.connect(args.node_url)
    try:
        await node.fetchval("SELECT 1 FROM event_offline_nodes LIMIT 1")  # the node has 030
        if await node.fetchval("SELECT 1 FROM events WHERE id = $1", event_id) and not args.replace_node_copy:
            raise SyncError("Este nodo ya tiene el evento. Si es de una prueba sin capturas que conservar, "
                            "vuelve a preparar marcando «Reemplazar la copia del nodo».")
        step(2, total, f"Pidiendo el evento a {urlsplit(api_base()).hostname} (puede tardar si se emite el certificado)")
        scope = scope_from(args)
        payload = {"node_label": args.node_label, "recipient_key": nodo_crypto.b64(box_public),
                   "sign_key": nodo_crypto.b64(sign_public), "scope": scope,
                   "replace_node_id": args.replace_node}
        answer = await asyncio.to_thread(api_call, "POST", f"/events/{event_id}/offline/export",
                                         body=json.dumps(payload).encode())
        step(3, total, "Abriendo el paquete cifrado")
        bundle = nodo_crypto.open_sealed(answer, box_private, str(event_id))
        if bundle.get("event_id") != str(event_id) or (bundle.get("node") or {}).get("id") != answer.get("node_id"):
            raise SyncError("el paquete no corresponde al evento pedido")
        info = bundle["node"]
        step(4, total, "Cargando el evento en el nodo")
        local_logo = None  # the logo keeps its URL: it is mirrored with the rest of the media
        local = {"id": info["id"], "label": info["label"], "scope": info["scope"],
                 "sign_key": nodo_crypto.b64(sign_public), "import_key": bundle["import_key"],
                 "node_private_enc": nodo_crypto.box(node_secret(), NODE_KEY_PURPOSE, sign_private)}
        try:
            passes = await core.load_into_node(node, bundle, replace=args.replace_node_copy, local_logo=local_logo,
                                               export_id=info["export_id"], pass_encrypt=pass_codec().encrypt,
                                               local_node=local)
        except Exception:
            say(f"@@aviso La nube ya registró este nodo ({info['id']}) pero el nodo no se cargó: vuelve a "
                "«Preparar» (se reemplaza ese registro).")
            emit_json({"failed_node_id": info["id"]})
            raise
        checked = await verify_passes(node, event_id)
        media = mirror_media(bundle, args.media_dir)
        step(5, total, "Certificado HTTPS")
        cert = write_certificate(bundle.get("certificate") or {}, args.tls_dir)
        if cert["installed"]:
            say(f"@@ok Certificado instalado (vence {str(cert['not_after'])[:10]})")
        elif cert["status"] == "ok":
            say("@@aviso El paquete trae certificado pero no hay carpeta TLS: no se instaló")
        else:
            say(f"@@aviso Sin certificado del API ({cert.get('message') or cert['status']}): usa el certificado local")
        activities = {a["id"]: a["name"] for a in bundle["event"]["event_activities"]}
        regs = [r for r in bundle["event"]["event_registrations"] if r.get("status") == "REGISTERED"]
        summary = {
            "node_id": info["id"], "label": info["label"], "scope": info["scope"], "event_id": str(event_id),
            "event_name": bundle["event"]["events"][0]["name"],
            "activity_names": (["Todas las actividades"] if info["scope"].get("all")
                               else [activities[i] for i in info["scope"]["activity_ids"] if i in activities]),
            "clubs": len(regs), "accounts": len(bundle["reference"]["users"]),
            "passes": passes.get("ok", 0), "passes_missing": passes.get("missing", 0),
            "passes_readable": checked.get("ok"), "certificate": cert, "media": media,
        }
        say(f"@@ok Evento listo en el nodo: {summary['clubs']} clubes, {summary['accounts']} cuentas, "
            f"{summary['passes']} pases")
        emit_json(summary)
        return 0
    finally:
        await node.close()


async def cmd_upload(args) -> int:
    event_id = uuid.UUID(args.event)
    total = 3 if args.dry_run else 4
    node = await core.connect(args.node_url)
    try:
        me = await core.local_node(node, event_id)
        if me is None:
            raise SyncError("Este nodo no se preparó con el modo automático para este evento "
                            "(usa el modo manual o prepara el nodo)")
        if not me.get("node_private_enc") or not me.get("import_key"):
            raise SyncError("A este nodo le faltan sus llaves (se preparó en modo manual): usa el modo manual")
        step(1, total, "Reuniendo las capturas del nodo")
        changes = await core.gather_node_changes(node, event_id, pass_decrypt=pass_codec().decrypt)
        sign_private = nodo_crypto.unbox(node_secret(), NODE_KEY_PURPOSE, me["node_private_enc"])
        envelope = nodo_crypto.seal(changes, nodo_crypto.unb64(me["import_key"], size=32), str(event_id))
        envelope.update(node_id=me["id"], export_id=changes["export_id"])
        body = json.dumps(envelope).encode()
        stamp = int(time.time())
        signature = nodo_crypto.sign(sign_private, str(event_id), me["id"], stamp, body)
        step(2, total, "Enviando a adventist.club" + (" (simulación)" if args.dry_run else ""))
        query = urlencode({"dry_run": str(args.dry_run).lower(), "final": str(not args.partial).lower(),
                           **({"prefer": args.prefer} if args.prefer else {})})
        report = await asyncio.to_thread(api_call, "POST", f"/events/{event_id}/offline/import?{query}", body=body,
                                         headers={"X-Nodo-Timestamp": str(stamp), "X-Nodo-Signature": signature})
        step(3, total, "Reporte")
        print_report(report)
        if not args.dry_run:
            step(4, total, "Guardando el estado del nodo")
            await remember_upload(node, event_id, report)
            if report.get("node_status") == "FINAL":
                say("@@ok Subida final aplicada: este nodo quedó cerrado"
                    + (" y la nube ya está libre" if report.get("lock_released")
                       else f" (quedan {report.get('open_nodes', 0)} nodo(s) por subir)"))
            else:
                say("@@ok Avance subido: el nodo sigue abierto")
        emit_json(report)
        return 2 if report.get("conflicts") else 0
    finally:
        await node.close()


async def cmd_node_info(args) -> int:
    node = await core.connect(args.node_url)
    try:
        me = await core.local_node(node, uuid.UUID(args.event) if args.event else None)
        if me is None:
            emit_json({"node": None})
            return 0
        names = {r["id"]: r["name"] for r in await node.fetch(
            "SELECT id::text AS id, name FROM event_activities WHERE event_id = $1", uuid.UUID(me["event_id"]))}
        event = await node.fetchrow("SELECT name, status FROM events WHERE id = $1", uuid.UUID(me["event_id"]))
        scope = me.get("scope") or {}
        emit_json({"node": {
            "id": me["id"], "event_id": me["event_id"], "event_name": event["name"] if event else None,
            "event_status": event["status"] if event else None, "label": me["label"], "status": me["status"],
            "status_text": core.NODE_STATUS_ES.get(me["status"], me["status"]), "scope": scope,
            "activity_names": (["Todas las actividades"] if scope.get("all")
                               else [names[i] for i in scope.get("activity_ids", []) if i in names]),
            "prepared_at": me.get("prepared_at"), "last_upload_at": me.get("last_upload_at"),
            "uploads": me.get("uploads"), "automatic": bool(me.get("node_private_enc"))}})
        return 0
    finally:
        await node.close()


# --------------------------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------------------------
async def cmd_status(args) -> int:
    event_id = uuid.UUID(args.event)
    for label, dsn in (("producción", args.prod_url), ("nodo", args.node_url)):
        if not dsn:
            continue
        conn = await core.connect(dsn)
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
            for table, n in core.counts(await core.event_rows(conn, event_id)).items():
                say(f"    {table:<24} {n:>6}")
            for r in await conn.fetch("SELECT label, status, local FROM event_offline_nodes WHERE event_id = $1"
                                      " ORDER BY prepared_at", event_id):
                say(f"  nodo {'(este) ' if r['local'] else ''}«{r['label']}»: {core.NODE_STATUS_ES.get(r['status'])}")
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
    node_default = os.environ.get("NODE_DATABASE_URL") or os.environ.get("DATABASE_URL")

    def common(p, actor=True, prod=True, event_required=True):
        p.add_argument("--event", required=event_required, help="id (uuid) del evento")
        if prod:
            p.add_argument("--prod-url", default=os.environ.get("PROD_DATABASE_URL"),
                           help="por defecto $PROD_DATABASE_URL (mejor por entorno: no queda en el historial)")
        p.add_argument("--node-url", default=node_default, help="por defecto $NODE_DATABASE_URL o $DATABASE_URL")
        if actor:
            p.add_argument("--actor-email", required=True, help="cuenta de coordinación que firma la bitácora")

    def scope_args(p):
        group = p.add_mutually_exclusive_group()
        group.add_argument("--all", action="store_true", help="el nodo captura todas las actividades (por defecto)")
        group.add_argument("--activities", help="ids de las actividades que captura este nodo, separados por coma")
        desk = p.add_mutually_exclusive_group()
        desk.add_argument("--check-in", dest="check_in", action="store_true", default=True,
                          help="este nodo tiene mesa de llegadas (por defecto)")
        desk.add_argument("--no-check-in", dest="check_in", action="store_false")

    e = sub.add_parser("export", help="[manual] producción -> nodo (y bloqueo en producción)")
    common(e)
    e.add_argument("--node-label", default="Nodo local del evento")
    scope_args(e)
    e.add_argument("--reason", default="Evento operado sin internet en el nodo local", help=argparse.SUPPRESS)
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--replace-node-copy", action="store_true", help="borra antes la copia del nodo (¡sus capturas!)")
    e.add_argument("--no-lock", action="store_true", help="no registrar el nodo ni bloquear producción (sólo pruebas)")
    e.add_argument("--bundle-dir", default=os.environ.get("NODO_BUNDLE_DIR"))
    e.add_argument("--media-dir", default=os.environ.get("NODO_MEDIA_DIR"))
    e.add_argument("--settle-seconds", type=float, default=3.0, help=argparse.SUPPRESS)

    i = sub.add_parser("import", help="[manual] nodo -> producción (y libera el bloqueo)")
    common(i)
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--prefer", choices=("node", "prod"))
    i.add_argument("--partial", "--keep-lock", dest="partial", action="store_true",
                   help="avance: el nodo sigue abierto y la nube bloqueada")
    i.add_argument("--report")

    s = sub.add_parser("status", help="qué tiene cada lado")
    common(s, actor=False)

    p = sub.add_parser("prepare", help="[automático] pide el evento al API y lo carga en este nodo")
    common(p, actor=False, prod=False)
    p.add_argument("--node-label", required=True)
    scope_args(p)
    p.add_argument("--replace-node-copy", action="store_true")
    p.add_argument("--replace-node", help="id de un nodo anterior de esta Mac que se reemplaza")
    p.add_argument("--tls-dir", default=os.environ.get("NODO_TLS_DIR"))
    p.add_argument("--media-dir", default=os.environ.get("NODO_MEDIA_DIR"))

    u = sub.add_parser("upload", help="[automático] sube las capturas del nodo al API")
    common(u, actor=False, prod=False)
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--prefer", choices=("node", "prod"))
    kind = u.add_mutually_exclusive_group()
    kind.add_argument("--final", dest="partial", action="store_false", default=False)
    kind.add_argument("--partial", dest="partial", action="store_true")

    n = sub.add_parser("node-info", help="alcance y estado de este nodo")
    common(n, actor=False, prod=False, event_required=False)

    args = parser.parse_args(argv)
    if args.command in ("export", "import"):
        if not (args.prod_url and args.node_url):
            parser.error("faltan PROD_DATABASE_URL y/o NODE_DATABASE_URL (entorno o --prod-url/--node-url)")
        if clean_dsn(args.prod_url) == clean_dsn(args.node_url):
            parser.error("producción y nodo apuntan a la misma base")
    elif args.command in ("prepare", "upload", "node-info") and not args.node_url:
        parser.error("falta NODE_DATABASE_URL")
    handler = {"export": cmd_export, "import": cmd_import, "status": cmd_status, "prepare": cmd_prepare,
               "upload": cmd_upload, "node-info": cmd_node_info}[args.command]
    try:
        return asyncio.run(handler(args))
    except ApiError as exc:
        say(f"@@error {exc}")
        return 4 if exc.status == 401 else 5 if exc.status == 403 else 1
    except SchemaMismatch as exc:
        say(f"@@error {exc}")
        return 3
    except (SyncError, nodo_crypto.CryptoError) as exc:
        say(f"@@error {exc}")
        return 1
    except (OSError, asyncpg.PostgresError) as exc:
        # Never echo a DSN: asyncpg messages do not include the password, but keep it short.
        say(f"@@error ({type(exc).__name__}): {str(exc).splitlines()[0][:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
