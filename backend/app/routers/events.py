"""Eventos y puntajes (spec docs/superpowers/specs/2026-09-24-eventos.md §3.5).

Thin: who may act is decided in `app/services/event_access.py` (through
`events.require_coordination` / `require_access`), the work in `app/services/events.py` and
`app/services/event_scores.py`, the points in `app/services/event_scoring.py`.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit import limiter
from app.schemas.event import (
    ActivityCreate,
    ActivityOut,
    ActivityReorder,
    ActivityUpdate,
    AdjustmentCreate,
    AdjustmentOut,
    AdjustmentTypeCreate,
    AdjustmentTypeOut,
    AdjustmentTypeUpdate,
    AdjustmentVoid,
    EvaluationCorrect,
    EvaluationCreate,
    EvaluationOut,
    EvaluationRevisionOut,
    EvaluationVoid,
    EventCreate,
    EventDuplicate,
    EventOut,
    EventStatusChange,
    EventUpdate,
    RegistrationCreate,
    RegistrationOut,
    RegistrationUpdate,
    ResolvePass,
    StaffCreate,
    StaffOut,
)
from app.services import event_scores as scores
from app.services import events as event_service

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# ----------------------------------------------------------------------------
# Eventos
# ----------------------------------------------------------------------------
@router.get("", response_model=list[EventOut])
async def list_events(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The events where the reader is something (admin in scope, staff, director)."""
    return [await event_service.event_out(db, event, roles)
            for event, roles in await event_service.list_events_for(db, current_user)]


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def create_event(payload: EventCreate, request: Request,
                       current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.create_event(db, current_user, payload, request)
    await db.commit()
    await db.refresh(event)
    return await event_service.event_out(db, event, await event_service.roles_for(db, current_user, event))


@router.get("/{event_id}", response_model=EventOut)
async def get_event(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.require_access(db, current_user, event)
    return await event_service.event_out(db, event, roles)


@router.patch("/{event_id}", response_model=EventOut)
async def update_event(event_id: uuid.UUID, payload: EventUpdate, request: Request,
                       current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    roles = await event_service.require_coordination(db, current_user, event)
    await event_service.update_event(db, current_user, event, roles, payload, request)
    await db.commit()
    await db.refresh(event)
    return await event_service.event_out(db, event, await event_service.roles_for(db, current_user, event))


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: uuid.UUID, request: Request,
                       current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    roles = await event_service.require_coordination(db, current_user, event)
    await event_service.delete_event(db, current_user, event, roles, request)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{event_id}/status", response_model=EventOut)
async def change_status(event_id: uuid.UUID, payload: EventStatusChange, request: Request,
                        current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    roles = await event_service.require_coordination(db, current_user, event)
    await event_service.change_status(db, current_user, event, payload.status, payload.reason, request)
    await db.commit()
    await db.refresh(event)
    return await event_service.event_out(db, event, roles)


@router.post("/{event_id}/duplicate", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def duplicate_event(event_id: uuid.UUID, payload: EventDuplicate, request: Request,
                          current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    source = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, source)
    copy = await event_service.duplicate_event(db, current_user, source, payload, request)
    await db.commit()
    await db.refresh(copy)
    return await event_service.event_out(db, copy, await event_service.roles_for(db, current_user, copy))


@router.get("/{event_id}/my")
async def my_view(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    """What the reader does in this event: coordination panel, the judge's activities, the
    director's club. Several at once for a person with several roles."""
    event = await event_service.get_event(db, event_id)
    roles = await event_service.require_access(db, current_user, event)
    activities = await event_service.list_activities(db, event)
    view: dict = {"event": await event_service.event_out(db, event, roles), "roles": roles.names()}
    if roles.coordination:
        types = await event_service.list_adjustment_types(db, event)
        registrations = await scores.list_registrations(db, event)
        view["coordination"] = {
            "registrations": sum(1 for r, _ in registrations if r.status == scores.REGISTERED),
            "staff": len(await event_service.list_staff(db, event)),
            "to_define": {
                "activities": [{"id": str(a.id), "name": a.name} for a in activities
                               if a.status == event_service.TO_DEFINE],
                "adjustment_types": [{"id": str(t.id), "label": t.label} for t in types
                                     if t.amount_mode == event_service.FIXED and t.points is None and t.active],
            },
        }
    if roles.judge:
        judged = scores.judged_activity_ids(roles, activities)
        view["judge"] = {"activities": [event_service.activity_out(a) for a in activities
                                        if a.id in judged]}
    if roles.director_club_ids:
        snap = await scores.snapshot(db, event)
        closed = event.status in (event_service.CLOSED, event_service.ARCHIVED)
        clubs = []
        for registration, club in await scores.list_registrations(db, event, club_ids=roles.director_club_ids):
            item = scores.breakdown(snap, registration, show_honor=closed)
            item.pop("pending_adjustments", None)
            item["club"] = {"id": str(club.id), "name": club.name}
            item["has_pass"] = registration.pass_token_hash is not None
            clubs.append(item)
        view["director"] = {"registrations": clubs}
    return view


# ----------------------------------------------------------------------------
# Actividades
# ----------------------------------------------------------------------------
@router.get("/{event_id}/activities", response_model=list[ActivityOut])
async def list_activities(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_access(db, current_user, event)
    return [event_service.activity_out(a) for a in await event_service.list_activities(db, event)]


@router.post("/{event_id}/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
async def create_activity(event_id: uuid.UUID, payload: ActivityCreate, request: Request,
                          current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    await event_service.require_coordination(db, current_user, event)
    activity = await event_service.create_activity(db, current_user, event, payload, request)
    await db.commit()
    await db.refresh(activity)
    return event_service.activity_out(activity)


@router.patch("/{event_id}/activities/{activity_id}", response_model=ActivityOut)
async def update_activity(event_id: uuid.UUID, activity_id: uuid.UUID, payload: ActivityUpdate,
                          request: Request, current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    await event_service.require_coordination(db, current_user, event)
    activity = await event_service.get_activity(db, event, activity_id)
    await event_service.update_activity(db, current_user, event, activity, payload, request)
    await db.commit()
    await db.refresh(activity)
    return event_service.activity_out(activity)


@router.delete("/{event_id}/activities/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity(event_id: uuid.UUID, activity_id: uuid.UUID, request: Request,
                          current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    await event_service.require_coordination(db, current_user, event)
    activity = await event_service.get_activity(db, event, activity_id)
    await event_service.delete_activity(db, current_user, event, activity, request)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{event_id}/activities/reorder", response_model=list[ActivityOut])
async def reorder_activities(event_id: uuid.UUID, payload: ActivityReorder, request: Request,
                             current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id, lock=True)
    await event_service.require_coordination(db, current_user, event)
    await event_service.reorder_activities(db, current_user, event, payload.parent_id, payload.ids, request)
    await db.commit()
    db.expire_all()
    await db.refresh(event)
    return [event_service.activity_out(a) for a in await event_service.list_activities(db, event)]


# ----------------------------------------------------------------------------
# Tipos de ajuste
# ----------------------------------------------------------------------------
@router.get("/{event_id}/adjustment-types", response_model=list[AdjustmentTypeOut])
async def list_adjustment_types(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                                db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_access(db, current_user, event)
    return [event_service.adjustment_type_out(t) for t in await event_service.list_adjustment_types(db, event)]


@router.post("/{event_id}/adjustment-types", response_model=AdjustmentTypeOut,
             status_code=status.HTTP_201_CREATED)
async def create_adjustment_type(event_id: uuid.UUID, payload: AdjustmentTypeCreate, request: Request,
                                 current_user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row = await event_service.create_adjustment_type(db, current_user, event, payload, request)
    await db.commit()
    await db.refresh(row)
    return event_service.adjustment_type_out(row)


@router.patch("/{event_id}/adjustment-types/{type_id}", response_model=AdjustmentTypeOut)
async def update_adjustment_type(event_id: uuid.UUID, type_id: uuid.UUID, payload: AdjustmentTypeUpdate,
                                 request: Request, current_user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row = await event_service.get_adjustment_type(db, event, type_id)
    await event_service.update_adjustment_type(db, current_user, event, row, payload, request)
    await db.commit()
    await db.refresh(row)
    return event_service.adjustment_type_out(row)


# ----------------------------------------------------------------------------
# Personal
# ----------------------------------------------------------------------------
@router.get("/{event_id}/staff", response_model=list[StaffOut])
async def list_staff(event_id: uuid.UUID, include_ended: bool = False,
                     current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    return [event_service.staff_out(row, user)
            for row, user in await event_service.list_staff(db, event, include_ended=include_ended)]


@router.post("/{event_id}/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
async def add_staff(event_id: uuid.UUID, payload: StaffCreate, request: Request,
                    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row, user = await event_service.add_staff(db, current_user, event, payload, request)
    await db.commit()
    await db.refresh(row)
    return event_service.staff_out(row, user)


@router.delete("/{event_id}/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_staff(event_id: uuid.UUID, staff_id: uuid.UUID, request: Request,
                    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    await event_service.end_staff(db, current_user, event, staff_id, request)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------
# Inscripciones
# ----------------------------------------------------------------------------
@router.get("/{event_id}/registrations", response_model=list[RegistrationOut])
async def list_registrations(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                             db: AsyncSession = Depends(get_db)):
    """Coordination and judges (manual search when the camera fails) see every club; a
    director only their own."""
    event = await event_service.get_event(db, event_id)
    roles = await event_service.require_access(db, current_user, event)
    club_ids = None if (roles.coordination or roles.judge) else roles.director_club_ids
    return [await scores.registration_out(db, row, club=club)
            for row, club in await scores.list_registrations(db, event, club_ids=club_ids)]


@router.post("/{event_id}/registrations", response_model=RegistrationOut,
             status_code=status.HTTP_201_CREATED)
async def register_club(event_id: uuid.UUID, payload: RegistrationCreate, request: Request,
                        current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row, token = await scores.register_club(db, current_user, event, payload.club_id, request)
    await db.commit()
    await db.refresh(row)
    return await scores.registration_out(db, row, pass_token=token)


@router.patch("/{event_id}/registrations/{registration_id}", response_model=RegistrationOut)
async def update_registration(event_id: uuid.UUID, registration_id: uuid.UUID,
                              payload: RegistrationUpdate, request: Request,
                              current_user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    """Finalists, chosen by hand by the coordination (spec §3.4)."""
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row = await scores.get_registration(db, event, registration_id)
    await scores.set_finalists(db, current_user, event, row, payload.finalist_flags, request)
    await db.commit()
    await db.refresh(row)
    return await scores.registration_out(db, row)


@router.post("/{event_id}/registrations/{registration_id}/withdraw", response_model=RegistrationOut)
async def withdraw(event_id: uuid.UUID, registration_id: uuid.UUID, request: Request,
                   current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row = await scores.get_registration(db, event, registration_id)
    await scores.withdraw(db, current_user, event, row, request)
    await db.commit()
    await db.refresh(row)
    return await scores.registration_out(db, row)


@router.post("/{event_id}/registrations/{registration_id}/pass", response_model=RegistrationOut)
async def regenerate_pass(event_id: uuid.UUID, registration_id: uuid.UUID, request: Request,
                          current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """A new pass (the old one stops working). Coordination, or the director of that club —
    the only way a director ever holds the pass, since it is stored hashed."""
    event = await event_service.get_event(db, event_id)
    roles = await event_service.require_access(db, current_user, event)
    row = await scores.get_registration(db, event, registration_id)
    if not (roles.coordination or row.club_id in roles.director_club_ids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, scores.DIRECTOR_ONLY_OWN)
    token = await scores.regenerate_pass(db, current_user, event, row, request)
    await db.commit()
    await db.refresh(row)
    return await scores.registration_out(db, row, pass_token=token)


@router.post("/{event_id}/resolve-pass", response_model=RegistrationOut)
@limiter.limit("120/minute")
async def resolve_pass(event_id: uuid.UUID, payload: ResolvePass, request: Request,
                       current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The judge reads a QR: which club is it? Identifies; authorizes nothing."""
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    if not (roles.coordination or roles.judge):
        raise HTTPException(status.HTTP_403_FORBIDDEN, event_service.FORBIDDEN)
    row = await scores.resolve_pass(db, event, payload.token)
    return await scores.registration_out(db, row)


@router.get("/{event_id}/registrations/{registration_id}/breakdown")
async def registration_breakdown(event_id: uuid.UUID, registration_id: uuid.UUID,
                                 current_user: User = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.require_access(db, current_user, event)
    row = await scores.get_registration(db, event, registration_id)
    return await scores.registration_breakdown(db, event, row, roles)


@router.get("/{event_id}/standings")
async def standings(event_id: uuid.UUID, current_user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)):
    """Coordination only: directors never see the other clubs (spec §0.5)."""
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    return {"event_id": str(event.id), "status": event.status,
            "rules_version": event.rules_version, "rows": await scores.standings(db, event)}


# ----------------------------------------------------------------------------
# Evaluaciones
# ----------------------------------------------------------------------------
@router.get("/{event_id}/evaluations", response_model=list[EvaluationOut])
async def list_evaluations(event_id: uuid.UUID, activity_id: uuid.UUID | None = None,
                           registration_id: uuid.UUID | None = None, include_void: bool = False,
                           current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    if roles.coordination:
        allowed = None
    elif roles.judge:
        allowed = scores.judged_activity_ids(roles, await event_service.list_activities(db, event))
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, event_service.FORBIDDEN)
    if activity_id is not None:
        if allowed is not None and activity_id not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, scores.NOT_ASSIGNED)
        allowed = {activity_id}
    rows = await scores.list_evaluations(db, event, activity_ids=allowed,
                                         registration_id=registration_id, include_void=include_void)
    return [scores.evaluation_out(row) for row in rows]


@router.post("/{event_id}/evaluations", response_model=EvaluationOut, status_code=status.HTTP_201_CREATED)
async def capture(event_id: uuid.UUID, payload: EvaluationCreate, request: Request, response: Response,
                  current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """201 the first time; 200 with the very same evaluation when retried with its key.
    The assigned judge captures; coordination may too (a missing judge), recorded as such."""
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    if not (roles.judge or roles.coordination):
        raise HTTPException(status.HTTP_403_FORBIDDEN, scores.NOT_ASSIGNED)
    row, created = await scores.capture(db, current_user, event, roles, payload, request)
    await db.commit()
    await db.refresh(row)
    if not created:
        response.status_code = status.HTTP_200_OK
    return scores.evaluation_out(row, created=created)


@router.patch("/{event_id}/evaluations/{evaluation_id}", response_model=EvaluationOut)
async def correct(event_id: uuid.UUID, evaluation_id: uuid.UUID, payload: EvaluationCorrect,
                  request: Request, current_user: User = Depends(get_current_user),
                  db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    if not (roles.coordination or roles.judge):
        raise HTTPException(status.HTTP_403_FORBIDDEN, scores.NOT_ASSIGNED)
    row = await scores.correct(db, current_user, event, roles, evaluation_id, payload, request)
    await db.commit()
    await db.refresh(row)
    return scores.evaluation_out(row)


@router.post("/{event_id}/evaluations/{evaluation_id}/void", response_model=EvaluationOut)
async def void(event_id: uuid.UUID, evaluation_id: uuid.UUID, payload: EvaluationVoid, request: Request,
               current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    row = await scores.void(db, current_user, event, roles, evaluation_id, payload, request)
    await db.commit()
    await db.refresh(row)
    return scores.evaluation_out(row)


@router.get("/{event_id}/evaluations/{evaluation_id}/revisions", response_model=list[EvaluationRevisionOut])
async def revisions(event_id: uuid.UUID, evaluation_id: uuid.UUID,
                    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    row = await scores.get_evaluation(db, event, evaluation_id)
    activity = await event_service.get_activity(db, event, row.activity_id)
    if not (roles.coordination or roles.judges(activity.id, activity.parent_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, event_service.FORBIDDEN)
    return [scores.revision_out(r) for r in await scores.list_revisions(db, row)]


# ----------------------------------------------------------------------------
# Ajustes
# ----------------------------------------------------------------------------
@router.get("/{event_id}/adjustments", response_model=list[AdjustmentOut])
async def list_adjustments(event_id: uuid.UUID, registration_id: uuid.UUID | None = Query(default=None),
                           current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    return [scores.adjustment_out(row, label)
            for row, label in await scores.list_adjustments(db, event, registration_id=registration_id)]


@router.post("/{event_id}/adjustments", response_model=AdjustmentOut, status_code=status.HTTP_201_CREATED)
async def create_adjustment(event_id: uuid.UUID, payload: AdjustmentCreate, request: Request,
                            current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    roles = await event_service.roles_for(db, current_user, event)
    row, label = await scores.create_adjustment(db, current_user, event, roles, payload, request)
    await db.commit()
    await db.refresh(row)
    return scores.adjustment_out(row, label)


async def _adjustment_label(db: AsyncSession, row) -> str | None:
    if row.adjustment_type_id is None:
        return None
    from app.models import EventAdjustmentType

    found = await db.get(EventAdjustmentType, row.adjustment_type_id)
    return found.label if found else None


@router.post("/{event_id}/adjustments/{adjustment_id}/approve", response_model=AdjustmentOut)
async def approve_adjustment(event_id: uuid.UUID, adjustment_id: uuid.UUID, request: Request,
                             current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row = await scores.approve_adjustment(db, current_user, event, adjustment_id, request)
    await db.commit()
    await db.refresh(row)
    return scores.adjustment_out(row, await _adjustment_label(db, row))


@router.post("/{event_id}/adjustments/{adjustment_id}/void", response_model=AdjustmentOut)
async def void_adjustment(event_id: uuid.UUID, adjustment_id: uuid.UUID, payload: AdjustmentVoid,
                          request: Request, current_user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event(db, event_id)
    await event_service.require_coordination(db, current_user, event)
    row = await scores.void_adjustment(db, current_user, event, adjustment_id, payload.reason, request)
    await db.commit()
    await db.refresh(row)
    return scores.adjustment_out(row, await _adjustment_label(db, row))
