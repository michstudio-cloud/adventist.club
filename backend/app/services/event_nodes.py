"""What a local event NODE may write (030; CONTRATO_NODO_LOGIN.md «Alcance»).

Several nodes may operate the same event, each capturing only ITS activities (its «alcance»)
and, optionally, the arrivals desk. On a node's own database `event_offline_nodes` holds one row
with `local = true` for the event: that row is how the node's API knows its scope. In the cloud
there is no such row and nothing here applies.

`guard_node_scope` is a router dependency on the events router (like the 029 lock guard), so
every write route is covered without remembering to call anything:

  * a node whose scope is «todo» (the classic single node) may write anything;
  * otherwise: captures, scans, corrections, voids and rule changes ONLY for its activities
    (a group covers its rounds), arrivals only with the desk, adjustments without activity or
    for its activities, and the event's status (so every node can open/close the day);
  * everything else (event settings, adjustment types, staff, registrations, logo, passes…)
    answers 403 — never 423, which the phones' outbox would retry forever.
"""
from __future__ import annotations

import json
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PREFIX = "/api/v1/events"
# Route templates (relative to /api/v1/events) that only read or that every node may use.
ALWAYS = {
    "/{event_id}/resolve-pass",
    "/{event_id}/status",
}
ACTIVITY_ROUTES = {"/{event_id}/activities/{activity_id}", "/{event_id}/activities/{activity_id}/scan"}
EVALUATION_ROUTES = {"/{event_id}/evaluations/{evaluation_id}", "/{event_id}/evaluations/{evaluation_id}/void"}
ADJUSTMENT_ROUTES = {"/{event_id}/adjustments/{adjustment_id}/approve", "/{event_id}/adjustments/{adjustment_id}/void"}
CHECK_IN_ROUTES = {"/{event_id}/check-in", "/{event_id}/registrations/{registration_id}/check-in"}


def read_only_detail(label: str) -> str:
    return (f"En este nodo («{label}») sólo se capturan sus actividades; la configuración del evento "
            "se cambia en la nube.")


def out_of_scope_detail(label: str) -> str:
    return f"Esta actividad no se captura en este nodo («{label}»): la captura otro nodo del evento."


def no_desk_detail(label: str) -> str:
    return f"Este nodo («{label}») no tiene mesa de llegadas: el check-in se hace en otro nodo."


async def local_node(db: AsyncSession, event_id: uuid.UUID) -> dict | None:
    row = (await db.execute(text(
        "SELECT id, label, scope FROM event_offline_nodes WHERE event_id = :e AND local LIMIT 1"),
        {"e": event_id})).mappings().first()
    if row is None:
        return None
    scope = row["scope"] if isinstance(row["scope"], dict) else json.loads(row["scope"] or "{}")
    covered = None
    if not scope.get("all"):
        chosen = set(scope.get("activity_ids") or [])
        children = (await db.execute(text(
            "SELECT id::text FROM event_activities WHERE event_id = :e AND parent_id::text = ANY(:ids)"),
            {"e": event_id, "ids": list(chosen)})).scalars().all()
        covered = sorted(chosen | set(children))
    return {"id": str(row["id"]), "label": row["label"], "all": bool(scope.get("all")),
            "activity_ids": covered if covered is not None else [], "covered": covered,
            "check_in": bool(scope.get("check_in", True))}


def offline_node_out(node: dict | None) -> dict | None:
    if node is None:
        return None
    return {"id": node["id"], "label": node["label"], "all": node["all"],
            "activity_ids": node["activity_ids"], "check_in": node["check_in"]}


def _template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "") or ""
    return path[len(PREFIX):] if path.startswith(PREFIX) else path


async def _body_activity(request: Request) -> str | None:
    try:
        data = json.loads(await request.body() or b"{}")
    except ValueError:
        return None
    value = data.get("activity_id") if isinstance(data, dict) else None
    return str(value) if value else None


async def guard_node_scope(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    raw = request.path_params.get("event_id")
    if raw is None:
        return
    try:
        event_id = uuid.UUID(str(raw))
    except ValueError:
        return
    async with SessionLocal() as db:
        node = await local_node(db, event_id)
        if node is None or node["all"]:
            if node is not None and _template(request) in CHECK_IN_ROUTES and not node["check_in"]:
                raise HTTPException(status.HTTP_403_FORBIDDEN, no_desk_detail(node["label"]))
            return
        template = _template(request)
        label, covered = node["label"], set(node["covered"] or [])
        if template in ALWAYS:
            return
        if template in CHECK_IN_ROUTES:
            if not node["check_in"]:
                raise HTTPException(status.HTTP_403_FORBIDDEN, no_desk_detail(label))
            return
        activity = None
        if template in ACTIVITY_ROUTES:
            activity = str(request.path_params.get("activity_id"))
        elif template == "/{event_id}/evaluations":
            activity = await _body_activity(request)
        elif template in EVALUATION_ROUTES:
            activity = (await db.execute(text("SELECT activity_id::text FROM evaluations WHERE id::text = :i"),
                                         {"i": str(request.path_params.get("evaluation_id"))})).scalar()
        elif template == "/{event_id}/adjustments":
            activity = await _body_activity(request)
            if activity is None:
                return  # an adjustment without activity: any node
        elif template in ADJUSTMENT_ROUTES:
            found = (await db.execute(text("SELECT activity_id::text FROM event_adjustments WHERE id::text = :i"),
                                      {"i": str(request.path_params.get("adjustment_id"))})).first()
            if found is None:
                return  # the route answers 404
            activity = found[0]
            if activity is None:
                return
        else:
            raise HTTPException(status.HTTP_403_FORBIDDEN, read_only_detail(label))
        if activity is None or activity not in covered:
            raise HTTPException(status.HTTP_403_FORBIDDEN, out_of_scope_detail(label))
