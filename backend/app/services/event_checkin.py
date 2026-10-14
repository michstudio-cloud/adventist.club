"""Check-in, automatic participation and the judge's progress (028; spec SPEC_CHECKIN_JUEZ «Backend»).

  * CHECK-IN — coordination or ANY judge of the event records that a club arrived, by reading
    its pass (or picking it from the list). Event OPEN or IN_PROGRESS. Idempotent: a second
    scan answers «already» with when and by whom. One current check-in per registration
    (`event_check_ins`, mirrored on `event_registrations.checked_in_*`).
  * UNDO — coordination only, with a reason; it also voids (VOID revision, same reason) every
    evaluation that check-in granted.
  * AUTO AWARD — a `participation` activity may carry `config.auto_award`:
      "on_checkin": the check-in creates the evaluation with the maximum (AUTO_CHECKIN). While
                    the event is only OPEN nothing is granted yet: going IN_PROGRESS (or setting
                    the mode on an activity) grants it to every club already checked in;
      "on_scan":    POST …/activities/{aid}/scan by the assigned judge (or coordination) creates
                    it (AUTO_SCAN) or returns the one in force. Needs IN_PROGRESS.
    Never touches an evaluation that already exists; the usual gates apply (READY, finalists).
    Correcting/voiding them is the normal flow.
  * PROGRESS — per activity: clubs registered, checked in, eligible and scored.

Everything stages on the caller's session (rows + audit); the router commits.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Evaluation, Event, EventActivity, EventCheckIn, EventRegistration, Organization, User
from app.security import utcnow
from app.services import event_scores as scores
from app.services import event_scoring as scoring
from app.services import events as event_service
from app.services.audit import record_audit
from app.services.event_access import EventRoles

AUTO_CHECKIN, AUTO_SCAN = "AUTO_CHECKIN", "AUTO_SCAN"
JUDGE, COORDINATION = "JUDGE", "COORDINATION"
PASS, MANUAL = "PASS", "MANUAL"
ENTITY_CHECK_IN = "EVENT_CHECK_IN"

CHECK_IN_FORBIDDEN = "Sólo la coordinación y los jueces del evento registran llegadas"
UNDO_FORBIDDEN = "Sólo la coordinación anula una llegada"
CHECK_IN_CLOSED = "El check-in se registra con el evento abierto o en curso"
NOT_ON_SCAN = "Esta actividad no otorga puntos al escanear el pase"


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(code, detail)


def acting_as(roles: EventRoles) -> str:
    """The role a check-in is recorded with: coordination when the person has it."""
    return COORDINATION if roles.coordination else JUDGE


async def registration_from(db: AsyncSession, event: Event, payload) -> tuple[EventRegistration, str]:
    """-> (registration, method). By pass (token or QR URL) or by id."""
    if payload.pass_token is not None:
        row = await scores.find_by_pass(db, payload.pass_token)
        if row is None or row.event_id != event.id:
            raise _http(status.HTTP_404_NOT_FOUND, scores.PASS_NOT_FOUND)
        return row, PASS
    return await scores.get_registration(db, event, payload.registration_id), MANUAL


async def _lock(db: AsyncSession, registration: EventRegistration) -> EventRegistration:
    stmt = (select(EventRegistration).where(EventRegistration.id == registration.id)
            .with_for_update().execution_options(populate_existing=True))
    return (await db.execute(stmt)).scalar_one()


async def current_check_in(db: AsyncSession, registration: EventRegistration) -> EventCheckIn | None:
    stmt = select(EventCheckIn).where(EventCheckIn.registration_id == registration.id,
                                      EventCheckIn.undone_at.is_(None))
    return (await db.execute(stmt)).scalar_one_or_none()


async def _granted_by(db: AsyncSession, check_in: EventCheckIn) -> list[tuple[Evaluation, EventActivity]]:
    stmt = (select(Evaluation, EventActivity).join(EventActivity, EventActivity.id == Evaluation.activity_id)
            .where(Evaluation.check_in_id == check_in.id, Evaluation.status == scores.CONFIRMED)
            .order_by(EventActivity.position, Evaluation.created_at))
    return list((await db.execute(stmt)).all())


def award_out(evaluation: Evaluation, activity: EventActivity, *, created: bool) -> dict:
    return {"evaluation_id": str(evaluation.id), "activity_id": str(activity.id),
            "activity_name": activity.name, "points": float(evaluation.points),
            "captured_as": evaluation.captured_as, "revision": evaluation.revision,
            "created": created}


async def check_in_out(db: AsyncSession, check_in: EventCheckIn | None) -> dict | None:
    if check_in is None:
        return None
    who = await db.get(User, check_in.checked_in_by_id) if check_in.checked_in_by_id else None
    return {"id": str(check_in.id), "checked_in_at": check_in.checked_in_at,
            "checked_in_as": check_in.checked_in_as, "checked_in_by_name": who.name if who else None,
            "method": check_in.method}


# ----------------------------------------------------------------------------
# Automatic participation
# ----------------------------------------------------------------------------
def _max_inputs(activity: EventActivity) -> dict:
    return {"points": (activity.config or {}).get("max")}


async def _live(db: AsyncSession, registration: EventRegistration, activity: EventActivity) -> Evaluation | None:
    stmt = select(Evaluation).where(Evaluation.registration_id == registration.id,
                                    Evaluation.activity_id == activity.id,
                                    Evaluation.status == scores.CONFIRMED)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _create_auto(db: AsyncSession, event: Event, registration: EventRegistration,
                       activity: EventActivity, *, captured_as: str, judge_id, key: str,
                       check_in_id=None, actor: User, request: Request | None) -> Evaluation | None:
    """The maximum of `activity` for this club, or None when a twin request won the race."""
    result = scores._score(activity, _max_inputs(activity))
    row = Evaluation(
        id=uuid.uuid4(), registration_id=registration.id, activity_id=activity.id,
        inputs=_max_inputs(activity), points=result.points, breakdown=result.breakdown,
        rules_version=event.rules_version, status=scores.CONFIRMED, judge_id=judge_id,
        captured_as=captured_as, idempotency_key=key, revision=1, check_in_id=check_in_id,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return None
    record_audit(db, action="EVALUATION_CREATE", entity_type=scores.ENTITY_EVALUATION, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id), "registration_id": str(registration.id),
                                        "activity_id": str(activity.id), "points": float(row.points),
                                        "captured_as": captured_as, "rules_version": row.rules_version,
                                        "check_in_id": str(check_in_id) if check_in_id else None},
                 request=request)
    return row


async def _on_checkin_activities(db: AsyncSession, event: Event,
                                 only: list[EventActivity] | None = None) -> list[EventActivity]:
    activities = only if only is not None else await event_service.list_activities(db, event)
    return [a for a in activities if scoring.auto_award(a.kind, a.config) == scoring.ON_CHECKIN]


async def award_for_check_in(db: AsyncSession, event: Event, registration: EventRegistration,
                             check_in: EventCheckIn, *, actor: User, request: Request | None,
                             only: list[EventActivity] | None = None) -> list[uuid.UUID]:
    """Grants every «on_checkin» activity this club can receive and does not have yet. Only
    while IN_PROGRESS. Returns the ids of the evaluations created now."""
    if event.status != event_service.IN_PROGRESS or registration.status != scores.REGISTERED:
        return []
    created = []
    for activity in await _on_checkin_activities(db, event, only):
        try:
            scores._capture_gates(event, registration, activity)
        except HTTPException:
            continue  # TO_DEFINE, not a finalist…: nothing to grant (yet)
        if await _live(db, registration, activity) is not None:
            continue  # «si ya existe no la toca»
        row = await _create_auto(db, event, registration, activity, captured_as=AUTO_CHECKIN,
                                 judge_id=check_in.checked_in_by_id,
                                 key=f"auto-ci:{check_in.id}:{activity.id}", check_in_id=check_in.id,
                                 actor=actor, request=request)
        if row is not None:
            created.append(row.id)
    return created


async def award_checked_in(db: AsyncSession, actor: User, event: Event, request: Request | None,
                           *, only: list[EventActivity] | None = None) -> int:
    """The sweep: every club checked in receives its «on_checkin» activities. Run when the
    event goes IN_PROGRESS and when an activity is set to «on_checkin» during the event."""
    if event.status != event_service.IN_PROGRESS:
        return 0
    if not await _on_checkin_activities(db, event, only):
        return 0
    stmt = (select(EventRegistration, EventCheckIn)
            .join(EventCheckIn, (EventCheckIn.registration_id == EventRegistration.id)
                  & EventCheckIn.undone_at.is_(None))
            .where(EventRegistration.event_id == event.id, EventRegistration.status == scores.REGISTERED))
    total = 0
    for registration, check_in in (await db.execute(stmt)).all():
        total += len(await award_for_check_in(db, event, registration, check_in, actor=actor,
                                              request=request, only=only))
    return total


# ----------------------------------------------------------------------------
# Check-in
# ----------------------------------------------------------------------------
async def check_in(db: AsyncSession, actor: User, event: Event, roles: EventRoles, payload,
                   request: Request | None) -> tuple[dict, bool]:
    """-> (result, created). `created` is False when the club had already arrived (the
    response then says when and who, and nothing changes but missing automatic grants)."""
    if not (roles.coordination or roles.judge):
        raise _http(status.HTTP_403_FORBIDDEN, CHECK_IN_FORBIDDEN)
    if event.status not in (event_service.OPEN, event_service.IN_PROGRESS):
        raise _http(status.HTTP_409_CONFLICT, CHECK_IN_CLOSED)
    registration, method = await registration_from(db, event, payload)
    registration = await _lock(db, registration)
    if registration.status != scores.REGISTERED:
        raise _http(status.HTTP_409_CONFLICT, scores.NOT_REGISTERED)
    row = await current_check_in(db, registration)
    created = row is None
    if created:
        now = utcnow()
        row = EventCheckIn(id=uuid.uuid4(), registration_id=registration.id, checked_in_at=now,
                           checked_in_by_id=actor.id, checked_in_as=acting_as(roles), method=method)
        db.add(row)
        registration.checked_in_at = now
        registration.checked_in_by_id = actor.id
        registration.checked_in_as = row.checked_in_as
        registration.updated_at = now
        await db.flush()
        record_audit(db, action="EVENT_CHECK_IN", entity_type=ENTITY_CHECK_IN, entity_id=row.id,
                     actor=actor, metadata={"event_id": str(event.id), "registration_id": str(registration.id),
                                            "club_id": str(registration.club_id), "as": row.checked_in_as,
                                            "method": method}, request=request)
    new_ids = set(await award_for_check_in(db, event, registration, row, actor=actor, request=request))
    awarded = [award_out(e, a, created=e.id in new_ids) for e, a in await _granted_by(db, row)]
    return {"registration": registration, "check_in": row, "already_checked_in": not created,
            "awarded": awarded}, created


async def undo_check_in(db: AsyncSession, actor: User, event: Event, roles: EventRoles,
                        registration: EventRegistration, reason: str,
                        request: Request | None) -> dict:
    """Coordination only, with a reason. Voids what that check-in granted (revision VOID with
    the same reason). Idempotent: a club not checked in is answered as it is."""
    if not roles.coordination:
        raise _http(status.HTTP_403_FORBIDDEN, UNDO_FORBIDDEN)
    event_service.require_editable(event)
    registration = await _lock(db, registration)
    row = await current_check_in(db, registration)
    if row is None:
        return {"registration": registration, "undone": False, "voided": []}
    now = utcnow()
    voided = []
    for evaluation, activity in await _granted_by(db, row):
        if evaluation.captured_as != AUTO_CHECKIN:
            continue
        db.add(scores._snapshot(evaluation, action="VOID", reason=reason, actor=actor, key=None))
        evaluation.status = scores.VOID
        evaluation.revision += 1
        evaluation.updated_at = now
        record_audit(db, action="EVALUATION_VOID", entity_type=scores.ENTITY_EVALUATION,
                     entity_id=evaluation.id, actor=actor, details=reason,
                     metadata={"event_id": str(event.id), "points": float(evaluation.points),
                               "check_in_id": str(row.id)}, request=request)
        voided.append(award_out(evaluation, activity, created=False))
    row.undone_at = now
    row.undone_by_id = actor.id
    row.undo_reason = reason
    registration.checked_in_at = None
    registration.checked_in_by_id = None
    registration.checked_in_as = None
    registration.updated_at = now
    record_audit(db, action="EVENT_CHECK_IN_UNDO", entity_type=ENTITY_CHECK_IN, entity_id=row.id,
                 actor=actor, details=reason,
                 metadata={"event_id": str(event.id), "registration_id": str(registration.id),
                           "voided": [v["evaluation_id"] for v in voided]}, request=request)
    return {"registration": registration, "undone": True, "voided": voided}


# ----------------------------------------------------------------------------
# Scan at an «on_scan» activity
# ----------------------------------------------------------------------------
async def scan(db: AsyncSession, actor: User, event: Event, roles: EventRoles, activity: EventActivity,
               payload, request: Request | None) -> tuple[Evaluation, EventRegistration, bool]:
    """-> (evaluation, registration, created): the maximum granted now, or the evaluation in
    force (whoever captured it) when the club already has one."""
    if not (roles.judges(activity.id, activity.parent_id) or roles.coordination):
        raise _http(status.HTTP_403_FORBIDDEN, scores.NOT_ASSIGNED)
    if scoring.auto_award(activity.kind, activity.config) != scoring.ON_SCAN:
        raise _http(status.HTTP_409_CONFLICT, NOT_ON_SCAN)
    registration, _ = await registration_from(db, event, payload)
    scores._capture_gates(event, registration, activity)
    existing = await _live(db, registration, activity)
    if existing is not None:
        return existing, registration, False
    row = await _create_auto(db, event, registration, activity, captured_as=AUTO_SCAN, judge_id=actor.id,
                             key=f"auto-scan:{uuid.uuid4().hex}", actor=actor, request=request)
    if row is None:  # a twin scan won: return its evaluation
        return await _live(db, registration, activity), registration, False
    return row, registration, True


# ----------------------------------------------------------------------------
# Progress
# ----------------------------------------------------------------------------
async def progress_counts(db: AsyncSession, event: Event,
                          activities: list[EventActivity]) -> dict[uuid.UUID, dict | None]:
    """{activity_id: {registered, checked_in, eligible, scored}} for scorable activities;
    None for a group. Only REGISTERED clubs count; `eligible` = the finalists of a
    finalists-only activity, every registered club otherwise."""
    regs = [r for r, _ in await scores.list_registrations(db, event) if r.status == scores.REGISTERED]
    registered = len(regs)
    checked_in = sum(1 for r in regs if r.checked_in_at is not None)
    ids = [a.id for a in activities]
    scored: dict[uuid.UUID, int] = {}
    if ids and regs:
        stmt = (select(Evaluation.activity_id, func.count(Evaluation.id))
                .where(Evaluation.activity_id.in_(ids), Evaluation.status == scores.CONFIRMED,
                       Evaluation.registration_id.in_([r.id for r in regs]))
                .group_by(Evaluation.activity_id))
        scored = {activity_id: int(n) for activity_id, n in (await db.execute(stmt)).all()}
    out: dict[uuid.UUID, dict | None] = {}
    for activity in activities:
        if activity.kind == scoring.GROUP:
            out[activity.id] = None
            continue
        if (activity.config or {}).get(scoring.FINALISTS_ONLY):
            eligible = sum(1 for r in regs if (r.finalist_flags or {}).get(str(activity.id)))
        else:
            eligible = registered
        out[activity.id] = {"registered": registered, "checked_in": checked_in, "eligible": eligible,
                            "scored": scored.get(activity.id, 0)}
    return out


async def activity_progress(db: AsyncSession, event: Event, activity: EventActivity) -> dict:
    """Club by club: evaluated or not (points), checked in or not."""
    if activity.kind == scoring.GROUP:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, scores.GROUP_NOT_SCORED)
    rows: list[tuple[EventRegistration, Organization]] = [
        (r, c) for r, c in await scores.list_registrations(db, event) if r.status == scores.REGISTERED]
    stmt = select(Evaluation).where(Evaluation.activity_id == activity.id,
                                    Evaluation.status == scores.CONFIRMED)
    live = {e.registration_id: e for e in (await db.execute(stmt)).scalars()}
    finalists_only = bool((activity.config or {}).get(scoring.FINALISTS_ONLY))
    clubs = []
    for registration, club in rows:
        evaluation = live.get(registration.id)
        clubs.append({
            "registration_id": str(registration.id),
            "club": {"id": str(club.id), "name": club.name, "city": club.city},
            "checked_in": registration.checked_in_at is not None,
            "checked_in_at": registration.checked_in_at,
            "eligible": not finalists_only or bool((registration.finalist_flags or {}).get(str(activity.id))),
            "evaluated": evaluation is not None,
            "evaluation": None if evaluation is None else {
                "id": str(evaluation.id), "points": float(evaluation.points),
                "revision": evaluation.revision, "captured_as": evaluation.captured_as,
                "updated_at": evaluation.updated_at},
        })
    counts = (await progress_counts(db, event, [activity]))[activity.id]
    return {"activity_id": str(activity.id), "activity_name": activity.name, "kind": activity.kind,
            "max_points": scores._num(activity.max_points),
            "auto_award": scoring.auto_award(activity.kind, activity.config),
            "counts": counts, "clubs": clubs}
