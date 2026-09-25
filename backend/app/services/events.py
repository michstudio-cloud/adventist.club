"""Eventos: el evento, sus actividades, su personal y su catálogo de ajustes (spec eventos §3).

Scores, registrations and totals live in `app/services/event_scores.py`; who may do what in
`app/services/event_access.py`. Everything here only STAGES its change (rows + audit) on the
caller's session; the router commits.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Evaluation,
    Event,
    EventActivity,
    EventAdjustment,
    EventAdjustmentType,
    EventRegistration,
    EventStaff,
    Ministry,
    Organization,
    User,
)
from app.people import is_minor_user
from app.rbac import get_org_path, is_master
from app.schemas.event import (
    ActivityOut,
    AdjustmentTypeOut,
    EventOut,
    OrgBrief,
    StaffOut,
)
from app.security import ADMIN_ASSOCIATION, ROLE_RANK, utcnow
from app.services import event_scoring as scoring
from app.services import ministries as ministry_service
from app.services.audit import record_audit
from app.services.event_access import (
    COORDINATOR,
    JUDGE,
    OWNER_TYPES,
    EventRoles,
    can_manage_events_of,
    director_club_ids,
    event_roles,
)
from app.text import slugify

DRAFT, OPEN, IN_PROGRESS, CLOSED, ARCHIVED = "DRAFT", "OPEN", "IN_PROGRESS", "CLOSED", "ARCHIVED"
EDITABLE = (DRAFT, OPEN, IN_PROGRESS)
TRANSITIONS = {
    DRAFT: {OPEN},
    OPEN: {DRAFT, IN_PROGRESS},
    IN_PROGRESS: {CLOSED},
    CLOSED: {IN_PROGRESS, ARCHIVED},  # CLOSED -> IN_PROGRESS = reapertura, con motivo
    ARCHIVED: set(),
}
READY, TO_DEFINE = "READY", "TO_DEFINE"

ENTITY_EVENT = "EVENT"
ENTITY_ACTIVITY = "EVENT_ACTIVITY"
ENTITY_STAFF = "EVENT_STAFF"
ENTITY_ADJ_TYPE = "EVENT_ADJUSTMENT_TYPE"

NOT_FOUND = "Evento no encontrado"
NO_ACCESS = "No tienes acceso a este evento"
FORBIDDEN = "No tienes permiso para gestionar este evento"
ADMIN_ONLY = "Sólo la administración de la asociación puede hacer esto"
CREATE_FORBIDDEN = "No tienes permiso para crear eventos en esa organización"
BAD_OWNER = "Un evento pertenece a una asociación, unión o división"
FROZEN = "El evento está cerrado: no admite cambios"
SLUG_TAKEN = "Ya existe un evento con ese identificador en la organización"
ACTIVITY_NOT_FOUND = "Actividad no encontrada en este evento"
STAFF_NOT_FOUND = "Personal no encontrado en este evento"
TYPE_NOT_FOUND = "Tipo de ajuste no encontrado en este evento"
USER_NOT_FOUND = "No existe una cuenta activa con ese correo o id"


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(code, detail)


# ----------------------------------------------------------------------------
# Lecturas y serializadores
# ----------------------------------------------------------------------------
async def get_event(db: AsyncSession, event_id: uuid.UUID, *, lock: bool = False) -> Event:
    stmt = select(Event).where(Event.id == event_id)
    if lock:
        stmt = stmt.with_for_update()
    event = (await db.execute(stmt)).scalar_one_or_none()
    if event is None:
        raise _http(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return event


async def registered_club_ids(db: AsyncSession, event: Event) -> set[uuid.UUID]:
    stmt = select(EventRegistration.club_id).where(EventRegistration.event_id == event.id)
    return set((await db.execute(stmt)).scalars())


async def roles_for(db: AsyncSession, actor: User | None, event: Event) -> EventRoles:
    return await event_roles(db, actor, event, registered_club_ids=await registered_club_ids(db, event))


async def require_access(db: AsyncSession, actor: User, event: Event) -> EventRoles:
    roles = await roles_for(db, actor, event)
    if not roles.any:
        raise _http(status.HTTP_403_FORBIDDEN, NO_ACCESS)
    return roles


async def require_coordination(db: AsyncSession, actor: User, event: Event) -> EventRoles:
    roles = await roles_for(db, actor, event)
    if not roles.coordination:
        raise _http(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    return roles


def require_editable(event: Event) -> None:
    if event.status not in EDITABLE:
        raise _http(status.HTTP_409_CONFLICT, FROZEN)


async def event_out(db: AsyncSession, event: Event, roles: EventRoles | None = None) -> EventOut:
    org = await db.get(Organization, event.organization_id)
    ministry = await db.get(Ministry, event.ministry_id)
    return EventOut(
        id=str(event.id),
        organization=OrgBrief(id=str(org.id), name=org.name, code=org.code),
        ministry=ministry_service.as_ref(ministry),
        name=event.name,
        slug=event.slug,
        venue=event.venue,
        city=event.city,
        starts_on=event.starts_on,
        ends_on=event.ends_on,
        status=event.status,
        registration_closes_on=event.registration_closes_on,
        rules_version=event.rules_version,
        honor_bands=event.honor_bands or [],
        source_note=event.source_note,
        template_of_id=str(event.template_of_id) if event.template_of_id else None,
        created_at=event.created_at,
        updated_at=event.updated_at,
        my_roles=roles.names() if roles else [],
    )


def _num(value) -> float | None:
    return None if value is None else float(value)


def activity_out(activity: EventActivity) -> ActivityOut:
    maximum = scoring.config_max(activity.kind, activity.config)
    return ActivityOut(
        id=str(activity.id),
        event_id=str(activity.event_id),
        parent_id=str(activity.parent_id) if activity.parent_id else None,
        position=activity.position,
        name=activity.name,
        description=activity.description,
        kind=activity.kind,
        max_points=_num(activity.max_points),
        config=activity.config or {},
        status=activity.status,
        counts_to_total=activity.counts_to_total,
        schedule_at=activity.schedule_at,
        config_complete=activity.kind == scoring.GROUP or scoring.is_complete(activity.kind, activity.config),
        config_max=_num(maximum),
    )


def adjustment_type_out(row: EventAdjustmentType) -> AdjustmentTypeOut:
    return AdjustmentTypeOut(
        id=str(row.id), event_id=str(row.event_id), kind=row.kind, label=row.label,
        points=_num(row.points), to_define=row.points is None, max_per_event=row.max_per_event,
        max_per_club=row.max_per_club, position=row.position, active=row.active,
    )


def staff_out(row: EventStaff, user: User) -> StaffOut:
    return StaffOut(
        id=str(row.id), event_id=str(row.event_id), user_id=str(user.id), name=user.name,
        email=user.email, role=row.role, activity_id=str(row.activity_id) if row.activity_id else None,
        active=row.active, created_at=row.created_at,
    )


async def list_activities(db: AsyncSession, event: Event) -> list[EventActivity]:
    """Top-level activities by position, each followed by its children by position."""
    rows = list(
        (
            await db.execute(
                select(EventActivity)
                .where(EventActivity.event_id == event.id)
                .order_by(EventActivity.position, EventActivity.created_at)
            )
        ).scalars()
    )
    children: dict[uuid.UUID, list[EventActivity]] = {}
    for row in rows:
        if row.parent_id:
            children.setdefault(row.parent_id, []).append(row)
    ordered = []
    for row in rows:
        if row.parent_id is None:
            ordered.append(row)
            ordered.extend(children.get(row.id, []))
    return ordered


async def get_activity(db: AsyncSession, event: Event, activity_id: uuid.UUID) -> EventActivity:
    activity = await db.get(EventActivity, activity_id)
    if activity is None or activity.event_id != event.id:
        raise _http(status.HTTP_404_NOT_FOUND, ACTIVITY_NOT_FOUND)
    return activity


async def list_adjustment_types(db: AsyncSession, event: Event) -> list[EventAdjustmentType]:
    stmt = (
        select(EventAdjustmentType)
        .where(EventAdjustmentType.event_id == event.id)
        .order_by(EventAdjustmentType.position, EventAdjustmentType.created_at)
    )
    return list((await db.execute(stmt)).scalars())


# ----------------------------------------------------------------------------
# Eventos
# ----------------------------------------------------------------------------
async def _owner(db: AsyncSession, actor: User, organization_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise _http(status.HTTP_404_NOT_FOUND, "Organización no encontrada")
    if not await can_manage_events_of(db, actor, org.id):
        raise _http(status.HTTP_403_FORBIDDEN, CREATE_FORBIDDEN)
    if org.type not in OWNER_TYPES:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, BAD_OWNER)
    return org


def _bands(bands) -> list[dict]:
    try:
        return scoring.validate_honor_bands(bands)
    except scoring.ScoringError as error:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, error.message)


async def _free_slug(db: AsyncSession, organization_id: uuid.UUID, wanted: str,
                     *, exclude: uuid.UUID | None = None, strict: bool = False) -> str:
    base = wanted[:110] or "evento"
    candidate, suffix = base, 1
    while True:
        stmt = select(Event.id).where(Event.organization_id == organization_id, Event.slug == candidate)
        if exclude is not None:
            stmt = stmt.where(Event.id != exclude)
        if (await db.execute(stmt)).scalar_one_or_none() is None:
            return candidate
        if strict:
            raise _http(status.HTTP_409_CONFLICT, SLUG_TAKEN)
        suffix += 1
        candidate = f"{base}-{suffix}"


async def create_event(db: AsyncSession, actor: User, payload, request: Request | None) -> Event:
    org = await _owner(db, actor, payload.organization_id)
    ministry = await ministry_service.require(db, payload.ministry, payload.ministry_id)
    slug = await _free_slug(db, org.id, payload.slug or slugify(payload.name, "evento"),
                            strict=payload.slug is not None)
    event = Event(
        id=uuid.uuid4(), organization_id=org.id, ministry_id=ministry.id, name=payload.name,
        slug=slug, venue=payload.venue, city=payload.city, starts_on=payload.starts_on,
        ends_on=payload.ends_on, status=DRAFT, registration_closes_on=payload.registration_closes_on,
        rules_version=1, honor_bands=_bands(payload.honor_bands), source_note=payload.source_note,
        created_by_id=actor.id,
    )
    db.add(event)
    record_audit(db, action="EVENT_CREATE", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, details=event.name,
                 metadata={"organization_id": str(org.id), "ministry": ministry.slug}, request=request)
    return event


async def update_event(db: AsyncSession, actor: User, event: Event, roles: EventRoles,
                       payload, request: Request | None) -> Event:
    require_editable(event)
    changes = payload.model_dump(exclude_unset=True)
    has_registrations = bool(await registered_club_ids(db, event))
    if "organization_id" in changes and changes["organization_id"] != event.organization_id:
        # Only an administrator moves an event, and only while no club is registered.
        if not roles.admin:
            raise _http(status.HTTP_403_FORBIDDEN, ADMIN_ONLY)
        if has_registrations:
            raise _http(status.HTTP_409_CONFLICT, "No se cambia la organización con clubes inscritos")
        org = await _owner(db, actor, changes["organization_id"])
        event.organization_id = org.id
        event.slug = await _free_slug(db, org.id, event.slug, exclude=event.id)
    changes.pop("organization_id", None)
    if "ministry" in changes or "ministry_id" in changes:
        ministry = await ministry_service.require(db, changes.pop("ministry", None),
                                                  changes.pop("ministry_id", None))
        if ministry.id != event.ministry_id:
            if has_registrations:
                raise _http(status.HTTP_409_CONFLICT, "No se cambia el ministerio con clubes inscritos")
            event.ministry_id = ministry.id
    if "name" in changes and changes["name"] is None:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "El nombre es obligatorio")
    if "slug" in changes:
        if changes["slug"] is None:
            raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "El identificador es obligatorio")
        changes["slug"] = await _free_slug(db, event.organization_id, changes["slug"],
                                           exclude=event.id, strict=True)
    for key in ("starts_on", "ends_on"):
        if key in changes and changes[key] is None:
            raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Las fechas son obligatorias")
    if "honor_bands" in changes:
        changes["honor_bands"] = _bands(changes["honor_bands"])
    for key, value in changes.items():
        setattr(event, key, value)
    if event.ends_on < event.starts_on:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "La fecha de fin no puede ser anterior a la de inicio")
    event.updated_at = utcnow()
    record_audit(db, action="EVENT_UPDATE", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, metadata={"fields": sorted(payload.model_dump(exclude_unset=True))},
                 request=request)
    return event


async def change_status(db: AsyncSession, actor: User, event: Event, new_status: str,
                        reason: str | None, request: Request | None) -> Event:
    if new_status == event.status:
        return event
    if new_status not in TRANSITIONS.get(event.status, set()):
        raise _http(status.HTTP_409_CONFLICT,
                    f"No se puede pasar de {event.status} a {new_status}")
    reason = " ".join((reason or "").split()) or None
    if event.status == CLOSED and new_status == IN_PROGRESS and not reason:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Reabrir un evento cerrado requiere motivo")
    previous = event.status
    event.status = new_status
    event.updated_at = utcnow()
    record_audit(db, action="EVENT_STATUS", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, details=reason,
                 metadata={"from": previous, "to": new_status}, request=request)
    return event


async def delete_event(db: AsyncSession, actor: User, event: Event, roles: EventRoles,
                       request: Request | None) -> None:
    """Only an administrator, only a DRAFT with no captures ever made."""
    if not roles.admin:
        raise _http(status.HTTP_403_FORBIDDEN, ADMIN_ONLY)
    if event.status != DRAFT:
        raise _http(status.HTTP_409_CONFLICT, "Sólo se borra un evento en borrador")
    captured = await db.scalar(
        select(exists().where(Evaluation.registration_id == EventRegistration.id,
                              EventRegistration.event_id == event.id))
    )
    adjusted = await db.scalar(
        select(exists().where(EventAdjustment.registration_id == EventRegistration.id,
                              EventRegistration.event_id == event.id))
    )
    if captured or adjusted:
        raise _http(status.HTTP_409_CONFLICT, "El evento ya tiene puntajes: archívalo en vez de borrarlo")
    record_audit(db, action="EVENT_DELETE", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, details=event.name, request=request)
    await db.delete(event)


async def list_events_for(db: AsyncSession, actor: User) -> list[tuple[Event, EventRoles]]:
    """Every event where `actor` is something: admin in scope, staff, or director of a
    registered club. Newest first."""
    conditions = []
    if is_master(actor):
        conditions.append(Event.id.isnot(None))
    elif ROLE_RANK.get(actor.role, 0) >= ROLE_RANK[ADMIN_ASSOCIATION]:
        own = await get_org_path(db, actor.organization_id)
        if own:
            conditions.append(Event.organization_id.in_(
                select(Organization.id).where(Organization.path.op("<@")(own))))
    conditions.append(Event.id.in_(
        select(EventStaff.event_id).where(EventStaff.user_id == actor.id, EventStaff.active)))
    clubs = await director_club_ids(db, actor)
    if clubs:
        conditions.append(Event.id.in_(
            select(EventRegistration.event_id).where(EventRegistration.club_id.in_(clubs))))
    stmt = select(Event).where(or_(*conditions)).order_by(Event.starts_on.desc(), Event.name)
    found = []
    for event in (await db.execute(stmt)).scalars():
        roles = await roles_for(db, actor, event)
        if roles.any:
            found.append((event, roles))
    return found


async def duplicate_event(db: AsyncSession, actor: User, source: Event, payload,
                          request: Request | None) -> Event:
    """Copies the RULES (event fields, activities with their status, adjustment types) into a
    new DRAFT. Never registrations, staff, evaluations nor adjustments."""
    org = await _owner(db, actor, payload.organization_id or source.organization_id)
    slug = await _free_slug(db, org.id, payload.slug or slugify(payload.name, "evento"),
                            strict=payload.slug is not None)
    copy = Event(
        id=uuid.uuid4(), organization_id=org.id, ministry_id=source.ministry_id, name=payload.name,
        slug=slug, venue=source.venue, city=source.city, starts_on=payload.starts_on,
        ends_on=payload.ends_on, status=DRAFT, registration_closes_on=None, rules_version=1,
        honor_bands=list(source.honor_bands or []), source_note=source.source_note,
        template_of_id=source.id, created_by_id=actor.id,
    )
    db.add(copy)
    await db.flush()
    mapping: dict[uuid.UUID, uuid.UUID] = {}
    activities = await list_activities(db, source)
    for activity in activities:  # parents always come before their children
        new_id = uuid.uuid4()
        mapping[activity.id] = new_id
        db.add(EventActivity(
            id=new_id, event_id=copy.id,
            parent_id=mapping[activity.parent_id] if activity.parent_id else None,
            position=activity.position, name=activity.name, description=activity.description,
            kind=activity.kind, max_points=activity.max_points, config=dict(activity.config or {}),
            status=activity.status, counts_to_total=activity.counts_to_total, schedule_at=None,
        ))
        await db.flush()
    for row in await list_adjustment_types(db, source):
        db.add(EventAdjustmentType(
            id=uuid.uuid4(), event_id=copy.id, kind=row.kind, label=row.label, points=row.points,
            max_per_event=row.max_per_event, max_per_club=row.max_per_club, position=row.position,
            active=row.active,
        ))
    record_audit(db, action="EVENT_DUPLICATE", entity_type=ENTITY_EVENT, entity_id=copy.id,
                 actor=actor, details=copy.name,
                 metadata={"source_id": str(source.id), "activities": len(activities)},
                 request=request)
    return copy


# ----------------------------------------------------------------------------
# Actividades
# ----------------------------------------------------------------------------
def _validated_config(kind: str, config, activity_status: str) -> dict:
    try:
        return scoring.validate_config(kind, config, draft=activity_status == TO_DEFINE)
    except scoring.ScoringError as error:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, error.message)


def _check_max(kind: str, config: dict, activity_status: str, max_points) -> None:
    """A READY activity's own maximum must be the one its config allows (no hidden caps)."""
    if kind == scoring.GROUP or activity_status != READY or max_points is None:
        return
    allowed = scoring.config_max(kind, config)
    if allowed is not None and Decimal(str(max_points)).quantize(scoring.CENT) != allowed:
        raise _http(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"max_points ({scoring.as_number(Decimal(str(max_points)))}) no coincide con el "
            f"máximo de la configuración ({scoring.as_number(allowed)})",
        )


async def _check_parent(db: AsyncSession, event: Event, parent_id, kind: str,
                        activity: EventActivity | None = None) -> None:
    if parent_id is None:
        return
    parent = await get_activity(db, event, parent_id)
    if activity is not None and parent.id == activity.id:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Una actividad no puede ser su propia madre")
    if parent.kind != scoring.GROUP or parent.parent_id is not None:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Sólo una actividad de tipo «group» de primer nivel contiene rondas o estaciones")
    if kind == scoring.GROUP:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Un grupo no puede ir dentro de otro grupo")


async def _has_children(db: AsyncSession, activity: EventActivity) -> bool:
    return bool(await db.scalar(select(exists().where(EventActivity.parent_id == activity.id))))


async def _has_live_evaluations(db: AsyncSession, activity: EventActivity) -> bool:
    return bool(await db.scalar(select(exists().where(
        Evaluation.activity_id == activity.id, Evaluation.status == "CONFIRMED"))))


def _bump_rules(event: Event) -> None:
    event.rules_version = (event.rules_version or 1) + 1
    event.updated_at = utcnow()


async def create_activity(db: AsyncSession, actor: User, event: Event, payload,
                          request: Request | None) -> EventActivity:
    require_editable(event)
    config = _validated_config(payload.kind, payload.config, payload.status)
    _check_max(payload.kind, config, payload.status, payload.max_points)
    await _check_parent(db, event, payload.parent_id, payload.kind)
    position = payload.position
    if position is None:
        siblings = select(func.coalesce(func.max(EventActivity.position), 0)).where(
            EventActivity.event_id == event.id,
            EventActivity.parent_id.is_(None) if payload.parent_id is None
            else EventActivity.parent_id == payload.parent_id,
        )
        position = int(await db.scalar(siblings) or 0) + 1
    activity = EventActivity(
        id=uuid.uuid4(), event_id=event.id, parent_id=payload.parent_id, position=position,
        name=payload.name, description=payload.description, kind=payload.kind,
        max_points=payload.max_points, config=config, status=payload.status,
        counts_to_total=payload.counts_to_total, schedule_at=payload.schedule_at,
    )
    db.add(activity)
    _bump_rules(event)
    record_audit(db, action="EVENT_ACTIVITY_CREATE", entity_type=ENTITY_ACTIVITY,
                 entity_id=activity.id, actor=actor, details=activity.name,
                 metadata={"event_id": str(event.id), "kind": activity.kind}, request=request)
    return activity


SCORING_FIELDS = ("kind", "config", "max_points", "status", "counts_to_total", "parent_id")


async def update_activity(db: AsyncSession, actor: User, event: Event, activity: EventActivity,
                          payload, request: Request | None) -> EventActivity:
    require_editable(event)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "El nombre es obligatorio")
    kind = changes.get("kind") or activity.kind
    new_status = changes.get("status") or activity.status
    config = changes["config"] if changes.get("config") is not None else activity.config
    config = _validated_config(kind, config, new_status)
    max_points = changes["max_points"] if "max_points" in changes else activity.max_points
    _check_max(kind, config, new_status, max_points)
    parent_id = changes["parent_id"] if "parent_id" in changes else activity.parent_id
    await _check_parent(db, event, parent_id, kind, activity)
    if kind != activity.kind and activity.kind == scoring.GROUP and await _has_children(db, activity):
        raise _http(status.HTTP_409_CONFLICT, "El grupo tiene rondas: muévelas o bórralas primero")
    if parent_id is not None and await _has_children(db, activity):
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Un grupo no puede ir dentro de otro grupo")
    rules_changed = (
        kind != activity.kind
        or config != (activity.config or {})
        or (max_points is None) != (activity.max_points is None)
        or (max_points is not None and Decimal(str(max_points)) != Decimal(str(activity.max_points)))
    )
    if rules_changed and await _has_live_evaluations(db, activity):
        raise _http(status.HTTP_409_CONFLICT,
                    "Esta actividad ya tiene evaluaciones vigentes: anúlalas antes de cambiar sus reglas")
    touched_scoring = rules_changed or any(
        key in changes and changes[key] != getattr(activity, key)
        for key in ("status", "counts_to_total", "parent_id")
    )
    activity.kind = kind
    activity.config = config
    activity.max_points = max_points
    activity.status = new_status
    activity.parent_id = parent_id
    for key in ("name", "description", "counts_to_total", "schedule_at"):
        if key in changes:
            setattr(activity, key, changes[key])
    activity.updated_at = utcnow()
    if touched_scoring:
        _bump_rules(event)
    record_audit(db, action="EVENT_ACTIVITY_UPDATE", entity_type=ENTITY_ACTIVITY,
                 entity_id=activity.id, actor=actor,
                 metadata={"event_id": str(event.id), "fields": sorted(changes),
                           "rules_version": event.rules_version}, request=request)
    return activity


async def delete_activity(db: AsyncSession, actor: User, event: Event, activity: EventActivity,
                          request: Request | None) -> None:
    require_editable(event)
    if await _has_children(db, activity):
        raise _http(status.HTTP_409_CONFLICT, "El grupo tiene rondas: bórralas primero")
    if await db.scalar(select(exists().where(Evaluation.activity_id == activity.id))):
        raise _http(status.HTTP_409_CONFLICT, "La actividad ya tiene evaluaciones: no se borra")
    record_audit(db, action="EVENT_ACTIVITY_DELETE", entity_type=ENTITY_ACTIVITY,
                 entity_id=activity.id, actor=actor, details=activity.name,
                 metadata={"event_id": str(event.id)}, request=request)
    await db.delete(activity)
    _bump_rules(event)


async def reorder_activities(db: AsyncSession, actor: User, event: Event, parent_id, ids,
                             request: Request | None) -> None:
    require_editable(event)
    stmt = select(EventActivity.id).where(
        EventActivity.event_id == event.id,
        EventActivity.parent_id.is_(None) if parent_id is None else EventActivity.parent_id == parent_id,
    )
    siblings = set((await db.execute(stmt)).scalars())
    if len(set(ids)) != len(ids) or set(ids) != siblings:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "La lista debe contener exactamente las actividades de ese nivel")
    for index, activity_id in enumerate(ids, start=1):
        await db.execute(update(EventActivity).where(EventActivity.id == activity_id)
                         .values(position=index, updated_at=utcnow()))
    record_audit(db, action="EVENT_ACTIVITY_REORDER", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, metadata={"parent_id": str(parent_id) if parent_id else None,
                                        "ids": [str(i) for i in ids]}, request=request)


# ----------------------------------------------------------------------------
# Personal
# ----------------------------------------------------------------------------
async def list_staff(db: AsyncSession, event: Event, *, include_ended: bool = False):
    stmt = (select(EventStaff, User).join(User, User.id == EventStaff.user_id)
            .where(EventStaff.event_id == event.id).order_by(EventStaff.role, User.name))
    if not include_ended:
        stmt = stmt.where(EventStaff.active)
    return list((await db.execute(stmt)).all())


async def add_staff(db: AsyncSession, actor: User, event: Event, payload,
                    request: Request | None) -> tuple[EventStaff, User]:
    require_editable(event)
    if payload.user_id is not None:
        user = await db.get(User, payload.user_id)
    else:
        user = (await db.execute(select(User).where(User.email == payload.email.strip()))).scalar_one_or_none()
    if user is None or user.status != "ACTIVE":
        raise _http(status.HTTP_404_NOT_FOUND, USER_NOT_FOUND)
    # Protección infantil: coordinar o juzgar clubes con niños es un papel de adulto.
    if is_minor_user(user):
        raise _http(status.HTTP_403_FORBIDDEN, "Un menor de edad no puede ser personal del evento")
    if payload.activity_id is not None:
        await get_activity(db, event, payload.activity_id)
    duplicate = await db.scalar(select(exists().where(
        EventStaff.event_id == event.id, EventStaff.user_id == user.id, EventStaff.role == payload.role,
        EventStaff.active,
        EventStaff.activity_id.is_(None) if payload.activity_id is None
        else EventStaff.activity_id == payload.activity_id,
    )))
    if duplicate:
        raise _http(status.HTTP_409_CONFLICT, "Esa persona ya tiene esa asignación en el evento")
    row = EventStaff(id=uuid.uuid4(), event_id=event.id, user_id=user.id, role=payload.role,
                     activity_id=payload.activity_id, active=True, created_by_id=actor.id)
    db.add(row)
    record_audit(db, action="EVENT_STAFF_ADD", entity_type=ENTITY_STAFF, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id), "user_id": str(user.id),
                                        "role": row.role,
                                        "activity_id": str(row.activity_id) if row.activity_id else None},
                 request=request)
    return row, user


async def end_staff(db: AsyncSession, actor: User, event: Event, staff_id: uuid.UUID,
                    request: Request | None) -> None:
    row = await db.get(EventStaff, staff_id)
    if row is None or row.event_id != event.id or not row.active:
        raise _http(status.HTTP_404_NOT_FOUND, STAFF_NOT_FOUND)
    row.active = False
    row.ended_at = utcnow()
    row.ended_by_id = actor.id
    record_audit(db, action="EVENT_STAFF_END", entity_type=ENTITY_STAFF, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id), "user_id": str(row.user_id),
                                        "role": row.role}, request=request)


# ----------------------------------------------------------------------------
# Tipos de ajuste
# ----------------------------------------------------------------------------
async def get_adjustment_type(db: AsyncSession, event: Event, type_id: uuid.UUID) -> EventAdjustmentType:
    row = await db.get(EventAdjustmentType, type_id)
    if row is None or row.event_id != event.id:
        raise _http(status.HTTP_404_NOT_FOUND, TYPE_NOT_FOUND)
    return row


async def create_adjustment_type(db: AsyncSession, actor: User, event: Event, payload,
                                 request: Request | None) -> EventAdjustmentType:
    require_editable(event)
    position = payload.position
    if position is None:
        position = int(await db.scalar(select(func.coalesce(func.max(EventAdjustmentType.position), 0))
                                       .where(EventAdjustmentType.event_id == event.id)) or 0) + 1
    row = EventAdjustmentType(id=uuid.uuid4(), event_id=event.id, kind=payload.kind,
                              label=payload.label, points=payload.points,
                              max_per_event=payload.max_per_event, max_per_club=payload.max_per_club,
                              position=position, active=True)
    db.add(row)
    record_audit(db, action="EVENT_ADJ_TYPE_CREATE", entity_type=ENTITY_ADJ_TYPE, entity_id=row.id,
                 actor=actor, details=row.label,
                 metadata={"event_id": str(event.id), "kind": row.kind, "points": payload.points},
                 request=request)
    return row


async def update_adjustment_type(db: AsyncSession, actor: User, event: Event,
                                 row: EventAdjustmentType, payload,
                                 request: Request | None) -> EventAdjustmentType:
    require_editable(event)
    changes = payload.model_dump(exclude_unset=True)
    if "label" in changes and changes["label"] is None:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "El nombre es obligatorio")
    for key, value in changes.items():
        if key in ("active", "position") and value is None:
            continue
        setattr(row, key, value)
    row.updated_at = utcnow()
    record_audit(db, action="EVENT_ADJ_TYPE_UPDATE", entity_type=ENTITY_ADJ_TYPE, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id),
                                        "changes": {k: v for k, v in changes.items()}},
                 request=request)
    return row
