"""«Nodo Camporee», automatic mode (030; _dev-eventos/CONTRATO_NODO_LOGIN.md).

A coordinator logs in from the Mac app, picks one of HER events and prepares a local node with
it — no database connection string, no Cloudflare token, no EVENT_PASS_SECRET on the Mac — and
later uploads the node's captures the same way. Several nodes (coordinators in different
locations) may prepare the same event, each with its own scope.

Security model:
  * WHO: coordination of the event (admin in scope via has_role, event COORDINATOR, MASTER).
    Force-closing a node: admins only.
  * WHEN: export and import need a RECENT login (≤ RECENT_AUTH_SECONDS, `auth_time` claim; a
    refresh does not renew it) and, for accounts with a second factor, a session that passed it.
  * WHAT TRAVELS: the bundle (password hashes, plain pass tokens, the TLS key) is sealed to a
    one-time X25519 key the node sends; the upload is sealed to a key derived per export
    (`import_key`) and SIGNED with the Ed25519 key the node bound at export time.
  * Everything is audited (EVENT_OFFLINE_NODE_PREPARE / _UPLOAD / _CLOSE, lock / unlock).

These routes live outside the events router on purpose: they must work while the event is
«en nodo local» (the 029 guard would answer 423).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import connect_args_for, database_url, get_db
from app.deps import get_current_user
from app.models import User
from app.security import RECENT_AUTH_SECONDS
from app.services import event_nodes, event_pass, node_certs, nodo_crypto, nodo_malla
from app.services import event_scores as scores
from app.services import events as event_service
from app.services import nodo_sync_core as core
from app.services.audit import _valid_ip

router = APIRouter(prefix="/api/v1/events", tags=["event-nodes"])

REAUTH = ("Por seguridad, vuelve a iniciar sesión: preparar o subir un nodo exige haber entrado "
          "hace menos de 15 minutos.")
NEEDS_MFA = ("Tu cuenta tiene verificación en dos pasos: inicia sesión con tu código para preparar "
             "o subir un nodo.")
ADMIN_ONLY = "Sólo la administración del evento puede cerrar un nodo"
ON_A_NODE = "Esto se hace contra la nube (adventist.club), no contra un nodo local"
TOO_LARGE = "El paquete del evento es demasiado grande para enviarlo de una vez"


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: bool = False
    activity_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    check_in: bool = True


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_label: str = Field(min_length=1, max_length=120)
    recipient_key: str = Field(max_length=100)
    sign_key: str = Field(max_length=100)
    scope: ScopeIn = Field(default_factory=lambda: ScopeIn(all=True))
    replace_node_id: uuid.UUID | None = None

    @field_validator("node_label")
    @classmethod
    def _label(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("El nombre del nodo es obligatorio")
        return value


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("El motivo es obligatorio")
        return value


# --------------------------------------------------------------------------------------------
async def _conn():
    return await core.connect(str(database_url.set(drivername="postgresql").render_as_string(hide_password=False)),
                              ssl=connect_args_for(database_url, raw=settings.DATABASE_URL).get("ssl"))


def _http(exc: core.SyncError) -> HTTPException:
    return HTTPException(exc.code if 400 <= exc.code < 600 else 409, str(exc))


def require_recent_auth(user: User) -> None:
    """Recent login (+ second factor when the account has one). 401 with X-Reauth-Required."""
    if user.mfa_enabled and not getattr(user, "token_born_of_mfa", False):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NEEDS_MFA, headers={"X-Reauth-Required": "1"})
    auth_time = getattr(user, "token_auth_time", 0) or 0
    if time.time() - auth_time > RECENT_AUTH_SECONDS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, REAUTH, headers={"X-Reauth-Required": "1"})


async def _not_on_a_node(db: AsyncSession, event_id: uuid.UUID) -> None:
    if await event_nodes.local_node(db, event_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, ON_A_NODE)


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


# --------------------------------------------------------------------------------------------
@router.get("/offline/mine")
async def my_offline_events(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Events the caller may take to a node: coordination of the event (admin in scope,
    COORDINATOR, MASTER). With their activities and the nodes already registered."""
    items = [(event, roles) for event, roles in await event_service.list_events_for(db, current_user)
             if roles.coordination and event.status not in ("ARCHIVED",)]
    out = []
    if not items:
        return out
    conn = await _conn()
    try:
        for event, roles in items:
            activities = [{"id": str(a.id), "name": a.name, "kind": a.kind,
                           "parent_id": str(a.parent_id) if a.parent_id else None, "position": a.position}
                          for a in await event_service.list_activities(db, event)]
            nodes = [await core.node_out(conn, r["id"]) for r in await conn.fetch(
                "SELECT id FROM event_offline_nodes WHERE event_id = $1 AND NOT local ORDER BY prepared_at", event.id)]
            last = await conn.fetchrow(
                "SELECT kind, created_at, node_label, actor_email FROM event_offline_syncs WHERE event_id = $1"
                " AND kind IN ('EXPORT', 'IMPORT') ORDER BY created_at DESC LIMIT 1", event.id)
            lock = await event_service.event_offline.lock_out(db, event)
            out.append({
                "id": str(event.id), "name": event.name, "starts_on": event.starts_on, "ends_on": event.ends_on,
                "status": event.status, "venue": event.venue, "city": event.city,
                "my_roles": [r for r in roles.names() if r in ("ADMIN", "COORDINATOR")],
                "offline_lock": lock.model_dump() if lock else None,
                "last_sync": dict(last) if last else None,
                "activities": activities, "nodes": nodes,
            })
    finally:
        await conn.close()
    return _jsonable(out)


@router.get("/{event_id}/offline/nodes")
async def list_nodes(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """The nodes of an event and their state (cloud-side view for coordination)."""
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    lock = await event_service.event_offline.lock_out(db, event)
    conn = await _conn()
    try:
        rows = await conn.fetch("SELECT id, label, scope, sign_key, status FROM event_offline_nodes"
                                " WHERE event_id = $1 AND NOT local ORDER BY prepared_at", event_id)
        nodes = [await core.node_out(conn, r["id"]) for r in rows]
        # «Nodos vecinos» (public, no secrets): signing key, 6-digit fingerprint and cloud voucher
        # of every open node, to coordinate the Macs (e.g. compare fingerprints when pairing).
        vouch = nodo_crypto.vouch_private(settings.nodo_sync_secret) if settings.nodo_sync_secret else None
        for item, r in zip(nodes, rows, strict=True):
            item["mesh"] = None
            if r["sign_key"] and r["status"] in core.OPEN_STATUSES and vouch is not None:
                item["mesh"] = {"sign_key": r["sign_key"], "huella": nodo_malla.huella(r["sign_key"]),
                                "voucher": nodo_malla.voucher_for(vouch, event_id, {
                                    "id": str(r["id"]), "label": r["label"], "scope": core._j(r["scope"]),
                                    "sign_key": r["sign_key"]})}
    finally:
        await conn.close()
    return _jsonable({"offline_lock": lock.model_dump() if lock else None, "nodes": nodes})


@router.post("/{event_id}/offline/nodes/{node_id}/close")
async def close_node(event_id: uuid.UUID, node_id: uuid.UUID, payload: CloseIn, request: Request,
                     current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """An admin closes a node that will not upload (lost Mac, abandoned rehearsal). Its captures
    that never reached the cloud are left out. The cloud lock goes when no node is open."""
    event = await event_service.get_event(db, event_id)
    roles = await event_service.require_coordination(db, current_user, event)
    if not roles.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ADMIN_ONLY)
    await _not_on_a_node(db, event_id)
    user_id = current_user.id
    await db.rollback()
    conn = await _conn()
    try:
        actor = await core.actor_by_id(conn, user_id)
        node, lock = await core.close_node(conn, event_id, node_id, actor, payload.reason, ip=_valid_ip(request))
    except core.SyncError as exc:
        raise _http(exc) from exc
    finally:
        await conn.close()
    return _jsonable({**node, "lock_released": lock["released"], "open_nodes": lock["open_nodes"]})


@router.post("/{event_id}/offline/export")
async def export_for_node(event_id: uuid.UUID, payload: ExportIn, request: Request,
                          current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Prepares a node: registers it (scope, signing key), locks the event in the cloud and
    returns the bundle SEALED to the node's one-time key."""
    require_recent_auth(current_user)
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    await _not_on_a_node(db, event_id)
    try:
        recipient = nodo_crypto.check_box_public(payload.recipient_key)
        nodo_crypto.check_sign_public(payload.sign_key)
    except (nodo_crypto.CryptoError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Clave del nodo inválida: {exc}") from exc
    # Passes from before 028 (hash only) get their stable form ONCE now, while the cloud can
    # still write: the node receives every pass in plain text and nothing has to rotate later.
    regenerated = 0
    if event.offline_locked_at is None and event.status in (event_service.OPEN, event_service.IN_PROGRESS):
        for row, _club in await scores.list_registrations(db, event):
            if row.status != scores.REGISTERED:
                continue
            _token, fresh = await scores.current_pass(db, current_user, event, row, request)
            regenerated += fresh
        if regenerated:
            await db.commit()
    user_id = current_user.id
    await db.rollback()
    ip = _valid_ip(request)
    conn = await _conn()
    try:
        actor = await core.actor_by_id(conn, user_id)
        certificate = await node_certs.certificate_for_node(conn)
        bundle, node, export_id = await core.prepare_node(
            conn, event_id, actor, label=payload.node_label, scope=payload.scope.model_dump(mode="json"),
            sign_key=payload.sign_key, replace_node_id=payload.replace_node_id,
            require_status=(event_service.OPEN, event_service.IN_PROGRESS),
            settle_seconds=settings.NODO_SETTLE_SECONDS, ip=ip)
        tokens = {}
        for row in bundle["event"]["event_registrations"]:
            token = event_pass.decrypt(row.get("pass_token_enc"))
            if token and core.sha256_hex(token) == row.get("pass_token_hash"):
                tokens[row["id"]] = token
        import_key = nodo_crypto.box_public_of(
            nodo_crypto.derived_box_private(settings.nodo_sync_secret, str(export_id)))
        # «Nodos vecinos»: the cloud vouches for this node and lists the other open nodes, so
        # nodes on the same LAN accept each other without internet (derived key, nothing stored).
        mesh = await nodo_malla.cloud_mesh_block(
            conn, settings.nodo_sync_secret, event_id,
            {"id": node["id"], "label": node["label"], "scope": node["scope"], "sign_key": payload.sign_key})
        bundle.update(passes=tokens, certificate=certificate, import_key=nodo_crypto.b64(import_key),
                      legacy_passes_regenerated=regenerated, mesh=mesh)
        size = len(json.dumps(bundle, default=str))
        if size > settings.NODO_BUNDLE_MAX_BYTES:
            async with conn.transaction():
                await conn.execute("DELETE FROM event_offline_nodes WHERE id = $1", uuid.UUID(node["id"]))
                await core.refresh_lock(conn, event_id, actor, via="api", ip=ip)
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, TOO_LARGE)
        envelope = nodo_crypto.seal(bundle, recipient, str(event_id))
    except core.SyncError as exc:
        raise _http(exc) from exc
    finally:
        await conn.close()
    regs = [r for r in bundle["event"]["event_registrations"] if r.get("status") == "REGISTERED"]
    return {
        **envelope, "event_id": str(event_id), "node_id": node["id"], "export_id": str(export_id),
        "import_key": nodo_crypto.b64(import_key), "size": size,
        "summary": {"clubs": len(regs), "accounts": len(bundle["reference"]["users"]),
                    "passes": sum(1 for r in regs if r["id"] in tokens),
                    "certificate": certificate["status"], "certificate_not_after": certificate.get("not_after"),
                    "node": _jsonable(node)},
    }


@router.post("/{event_id}/offline/import")
async def import_from_node(event_id: uuid.UUID, request: Request,
                           dry_run: bool = Query(default=True),
                           prefer: str | None = Query(default=None, pattern="^(node|prod)$"),
                           final: bool = Query(default=True),
                           current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Uploads a node's changes (sealed + signed). `dry_run` (default) only reports. Conflicts are
    never applied unless `prefer`. A final upload without conflicts closes the node; the cloud
    lock goes when no node is left open. Idempotent: repeating an upload changes nothing."""
    require_recent_auth(current_user)
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    await _not_on_a_node(db, event_id)
    user_id = current_user.id
    await db.rollback()
    length = int(request.headers.get("content-length") or 0)
    if length > settings.NODO_BUNDLE_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, TOO_LARGE)
    body = await request.body()
    if len(body) > settings.NODO_BUNDLE_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, TOO_LARGE)
    try:
        envelope = json.loads(body)
        node_id = uuid.UUID(str(envelope.get("node_id")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Sobre del nodo inválido") from exc
    timestamp = request.headers.get("x-nodo-timestamp", "")
    signature = request.headers.get("x-nodo-signature", "")
    conn = await _conn()
    try:
        node = await conn.fetchrow("SELECT id, label, sign_key, export_id, status FROM event_offline_nodes"
                                   " WHERE id = $1 AND event_id = $2 AND NOT local", node_id, event_id)
        if node is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "Este nodo no está registrado para el evento (¿se preparó contra otra base?)")
        if not node["sign_key"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Este nodo se preparó en modo manual: súbelo con el modo manual")
        try:
            if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > nodo_crypto.SIGNATURE_WINDOW_SECONDS:
                raise nodo_crypto.CryptoError("la hora de la firma no es válida (revisa el reloj de la Mac)")
            nodo_crypto.verify(node["sign_key"], signature, str(event_id), str(node_id), timestamp, body)
            if str(envelope.get("export_id")) != str(node["export_id"]):
                raise nodo_crypto.CryptoError("este nodo se volvió a preparar después: sube desde la última preparación")
            private = nodo_crypto.derived_box_private(settings.nodo_sync_secret, str(node["export_id"]))
            changes = nodo_crypto.open_sealed(envelope, private, str(event_id),
                                              max_bytes=settings.NODO_BUNDLE_MAX_BYTES)
        except nodo_crypto.CryptoError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Subida rechazada: {exc}") from exc
        if changes.get("event_id") != str(event_id) or changes.get("node_id") != str(node_id) \
                or changes.get("format") != core.CHANGES_FORMAT:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El contenido no corresponde a este nodo")
        actor = await core.actor_by_id(conn, user_id)
        report = await core.import_changes(
            conn, event_id, changes, actor, node_id=node_id, prefer=prefer, dry_run=dry_run, final=final,
            pass_encrypt=event_pass.encrypt, payload_sha=hashlib.sha256(body).hexdigest(), ip=_valid_ip(request))
    except core.SyncError as exc:
        raise _http(exc) from exc
    finally:
        await conn.close()
    report.pop("payload_sha", None)
    return _jsonable(report)
