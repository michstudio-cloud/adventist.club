"""«En nodo local» (029): an event that runs without internet on the local event node.

While `events.offline_locked_at` is set, the CLOUD only reads that event: every write to it
answers 423 with a message that says where the captures happen. The lock is set by the
`export` script of `nodo-evento/` (or by coordination through the API) and released by its
`import`, once the node's changes are back in production. The node's own copy of the event is
never locked: there it is the source of truth.

The guard is one router dependency (`guard_offline_writes`) on the events router, so a new
write route is covered without remembering to call anything. The few POSTs that only READ are
listed in `READ_ONLY_POSTS`.
"""
from __future__ import annotations

import uuid
from datetime import timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Event, User
from app.schemas.event import OfflineLockOut
from app.security import utcnow
from app.services.audit import record_audit

ENTITY_EVENT = "EVENT"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Route templates (relative to /api/v1/events) whose POST only reads, or that manage the lock.
READ_ONLY_POSTS = {
    "/{event_id}/resolve-pass",   # identifies a club; writes nothing
    "/{event_id}/duplicate",      # reads this event, writes a NEW one
}
LOCK_ROUTE = "/{event_id}/offline-lock"

ALREADY_LOCKED = "El evento ya está en el nodo local"


def locked_detail(event: Event) -> str:
    where = f" «{event.offline_node_label}»" if event.offline_node_label else ""
    since = event.offline_locked_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Este evento se está operando sin internet en el nodo local{where} desde {since}. "
        "Aquí sólo se puede consultar: las capturas, llegadas y cambios se hacen en el nodo y "
        "se suben a la nube al terminar (import)."
    )


def ensure_online(event: Event) -> None:
    """423 when the event is «en nodo local». For writes that do not go through the guard
    (e.g. the one-time regeneration of a legacy pass inside a GET)."""
    if event.offline_locked_at is not None:
        raise HTTPException(status.HTTP_423_LOCKED, locked_detail(event))


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "") or ""
    prefix = "/api/v1/events"
    return path[len(prefix):] if path.startswith(prefix) else path


async def guard_offline_writes(request: Request) -> None:
    """Router dependency: rejects (423) any write to an event that is «en nodo local».
    Reads, read-only POSTs and the lock routes themselves go through."""
    if request.method in SAFE_METHODS:
        return
    raw = request.path_params.get("event_id")
    if raw is None:
        return
    template = _route_template(request)
    if template in READ_ONLY_POSTS or template == LOCK_ROUTE:
        return
    try:
        event_id = uuid.UUID(str(raw))
    except ValueError:
        return  # the route answers 422 itself
    # Own short-lived session: the guard runs before the route and must not leave a
    # transaction open on the route's session.
    async with SessionLocal() as db:
        event = (await db.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
    if event is not None:
        ensure_online(event)


async def lock_out(db: AsyncSession, event: Event) -> OfflineLockOut | None:
    if event.offline_locked_at is None:
        return None
    by = await db.get(User, event.offline_locked_by_id) if event.offline_locked_by_id else None
    return OfflineLockOut(
        locked_at=event.offline_locked_at,
        locked_by_name=by.name if by else None,
        reason=event.offline_lock_reason or "",
        node_label=event.offline_node_label,
    )


async def lock(db: AsyncSession, actor: User, event: Event, reason: str, node_label: str | None,
               request: Request | None) -> Event:
    """Marks the event «en nodo local». Stages the change and its audit row."""
    if event.offline_locked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_LOCKED)
    event.offline_locked_at = utcnow()
    event.offline_locked_by_id = actor.id
    event.offline_lock_reason = reason
    event.offline_node_label = node_label
    record_audit(
        db, action="EVENT_OFFLINE_LOCK", entity_type=ENTITY_EVENT, entity_id=event.id, actor=actor,
        details=reason, metadata={"node_label": node_label, "via": "api"}, request=request,
    )
    return event


async def unlock(db: AsyncSession, actor: User, event: Event, reason: str,
                 request: Request | None) -> bool:
    """Releases the lock. Idempotent: False when it was not locked (nothing audited)."""
    if event.offline_locked_at is None:
        return False
    metadata = {
        "node_label": event.offline_node_label,
        "locked_at": event.offline_locked_at.isoformat(),
        "lock_reason": event.offline_lock_reason,
        "via": "api",
    }
    event.offline_locked_at = None
    event.offline_locked_by_id = None
    event.offline_lock_reason = None
    event.offline_node_label = None
    record_audit(
        db, action="EVENT_OFFLINE_UNLOCK", entity_type=ENTITY_EVENT, entity_id=event.id, actor=actor,
        details=reason, metadata=metadata, request=request,
    )
    return True
