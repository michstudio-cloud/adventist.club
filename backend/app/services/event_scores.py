"""Inscripciones, pases, evaluaciones, ajustes y totales de un evento (spec eventos §3.1–§3.5).

The rules that must never break:
  * the server computes every point (`event_scoring`); a judge sends facts, never totals;
  * a retry with the same `idempotency_key` returns the SAME evaluation and never counts twice;
  * a correction is a new revision with a reason; the old values stay in `evaluation_revisions`;
  * captures need: event IN_PROGRESS, registration REGISTERED, activity READY, judge assigned;
  * official total = Σ CONFIRMED evaluations of counting activities + Σ BONUS − Σ PENALTY
    (approved, not voided). No implicit floor nor cap. «Pendiente» is never «0»;
  * a director only ever sees their own club, and honours only once the event is CLOSED.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Evaluation,
    EvaluationRevision,
    Event,
    EventActivity,
    EventAdjustment,
    EventAdjustmentType,
    EventRegistration,
    Organization,
    OrganizationMinistry,
    User,
)
from app.rbac import get_org_path, org_in_subtree
from app.schemas.event import (
    AdjustmentOut,
    ClubBrief,
    EvaluationOut,
    EvaluationRevisionOut,
    RegistrationOut,
)
from app.security import sha256_hex, utcnow
from app.services import event_scoring as scoring
from app.services import events as event_service
from app.services.audit import record_audit
from app.services.event_access import EventRoles

REGISTERED, WITHDRAWN = "REGISTERED", "WITHDRAWN"
CONFIRMED, VOID = "CONFIRMED", "VOID"
BONUS, PENALTY = "BONUS", "PENALTY"
PASS_PREFIX = "evp_"

ENTITY_REGISTRATION = "EVENT_REGISTRATION"
ENTITY_EVALUATION = "EVALUATION"
ENTITY_ADJUSTMENT = "EVENT_ADJUSTMENT"

REGISTRATION_NOT_FOUND = "Inscripción no encontrada en este evento"
EVALUATION_NOT_FOUND = "Evaluación no encontrada en este evento"
ADJUSTMENT_NOT_FOUND = "Ajuste no encontrado en este evento"
PASS_NOT_FOUND = "Pase no válido para este evento"
NOT_IN_PROGRESS = "El evento no está en curso: no admite capturas"
NOT_REGISTERED = "El club no está inscrito (o se retiró) en este evento"
ACTIVITY_TO_DEFINE = "Las reglas de esta actividad están por definir: no admite capturas todavía"
NOT_ASSIGNED = "No estás asignado como juez de esta actividad"
GROUP_NOT_SCORED = "Un grupo no se evalúa directamente: se evalúan sus rondas o estaciones"
NOT_FINALIST = "Este club no está marcado como finalista de esta actividad"
RULES_CHANGED = "Las reglas del evento cambiaron: vuelve a cargar la actividad"
ALREADY_SCORED = "Este club ya tiene una evaluación vigente en esta actividad: corrígela en vez de capturar otra"
KEY_REUSED = "Esa clave de idempotencia ya se usó con otros datos"
STALE_REVISION = "La evaluación cambió mientras la corregías: vuelve a cargarla"
ALREADY_VOID = "La evaluación está anulada"
DIRECTOR_ONLY_OWN = "Sólo puedes ver la inscripción de tu club"


def _http(code: int, detail: str) -> HTTPException:
    return HTTPException(code, detail)


def _num(value) -> float | None:
    return None if value is None else float(value)


def _require_in_progress(event: Event) -> None:
    if event.status != event_service.IN_PROGRESS:
        raise _http(status.HTTP_409_CONFLICT, NOT_IN_PROGRESS)


# ----------------------------------------------------------------------------
# Inscripciones y pase
# ----------------------------------------------------------------------------
def new_pass_token() -> str:
    import secrets

    return PASS_PREFIX + secrets.token_urlsafe(24)


async def get_registration(db: AsyncSession, event: Event, registration_id: uuid.UUID) -> EventRegistration:
    row = await db.get(EventRegistration, registration_id)
    if row is None or row.event_id != event.id:
        raise _http(status.HTTP_404_NOT_FOUND, REGISTRATION_NOT_FOUND)
    return row


async def registration_out(db: AsyncSession, row: EventRegistration,
                           pass_token: str | None = None,
                           club: Organization | None = None) -> RegistrationOut:
    club = club or await db.get(Organization, row.club_id)
    return RegistrationOut(
        id=str(row.id), event_id=str(row.event_id),
        club=ClubBrief(id=str(club.id), name=club.name, city=club.city),
        status=row.status, has_pass=row.pass_token_hash is not None,
        finalist_flags={k: bool(v) for k, v in (row.finalist_flags or {}).items()},
        created_at=row.created_at, pass_token=pass_token,
    )


async def list_registrations(db: AsyncSession, event: Event, *, club_ids: set[uuid.UUID] | None = None):
    stmt = (select(EventRegistration, Organization)
            .join(Organization, Organization.id == EventRegistration.club_id)
            .where(EventRegistration.event_id == event.id).order_by(Organization.name))
    if club_ids is not None:
        stmt = stmt.where(EventRegistration.club_id.in_(club_ids or {uuid.uuid4()}))
    return list((await db.execute(stmt)).all())


async def _club_has_ministry(db: AsyncSession, club: Organization, ministry_id: uuid.UUID) -> bool:
    if club.ministry_id == ministry_id:
        return True
    return bool(await db.scalar(select(exists().where(
        OrganizationMinistry.organization_id == club.id,
        OrganizationMinistry.ministry_id == ministry_id))))


async def register_club(db: AsyncSession, actor: User, event: Event, club_id: uuid.UUID,
                        request: Request | None) -> tuple[EventRegistration, str]:
    event_service.require_editable(event)
    club = await db.get(Organization, club_id)
    if club is None or club.type != "club":
        raise _http(status.HTTP_404_NOT_FOUND, "Club no encontrado")
    if club.status != "active":
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Sólo se inscribe un club oficial activo")
    owner_path = await get_org_path(db, event.organization_id)
    if not await org_in_subtree(db, club.id, owner_path):
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "El club no pertenece a la organización dueña del evento")
    if not await _club_has_ministry(db, club, event.ministry_id):
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "El club no tiene el ministerio del evento")
    token = new_pass_token()
    row = (await db.execute(select(EventRegistration).where(
        EventRegistration.event_id == event.id, EventRegistration.club_id == club.id)
        .with_for_update())).scalar_one_or_none()
    if row is not None and row.status == REGISTERED:
        raise _http(status.HTTP_409_CONFLICT, "Ese club ya está inscrito en el evento")
    if row is None:
        row = EventRegistration(id=uuid.uuid4(), event_id=event.id, club_id=club.id,
                                status=REGISTERED, pass_token_hash=sha256_hex(token),
                                finalist_flags={}, registered_by_id=actor.id)
        db.add(row)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            raise _http(status.HTTP_409_CONFLICT, "Ese club ya está inscrito en el evento")
    else:
        row.status = REGISTERED
        row.pass_token_hash = sha256_hex(token)
        row.registered_by_id = actor.id
        row.updated_at = utcnow()
    record_audit(db, action="EVENT_REGISTER", entity_type=ENTITY_REGISTRATION, entity_id=row.id,
                 actor=actor, details=club.name,
                 metadata={"event_id": str(event.id), "club_id": str(club.id)}, request=request)
    return row, token


async def withdraw(db: AsyncSession, actor: User, event: Event, row: EventRegistration,
                   request: Request | None) -> EventRegistration:
    event_service.require_editable(event)
    if row.status == WITHDRAWN:
        return row
    row.status = WITHDRAWN
    row.pass_token_hash = None
    row.updated_at = utcnow()
    record_audit(db, action="EVENT_WITHDRAW", entity_type=ENTITY_REGISTRATION, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id), "club_id": str(row.club_id)},
                 request=request)
    return row


async def regenerate_pass(db: AsyncSession, actor: User, event: Event, row: EventRegistration,
                          request: Request | None) -> str:
    event_service.require_editable(event)
    if row.status != REGISTERED:
        raise _http(status.HTTP_409_CONFLICT, NOT_REGISTERED)
    token = new_pass_token()
    row.pass_token_hash = sha256_hex(token)
    row.updated_at = utcnow()
    record_audit(db, action="EVENT_PASS_REGENERATE", entity_type=ENTITY_REGISTRATION,
                 entity_id=row.id, actor=actor, metadata={"event_id": str(event.id)}, request=request)
    return token


async def resolve_pass(db: AsyncSession, event: Event, token: str) -> EventRegistration:
    """Identifies a club. It authorizes NOTHING by itself: every capture checks the judge."""
    row = (await db.execute(select(EventRegistration).where(
        EventRegistration.event_id == event.id,
        EventRegistration.pass_token_hash == sha256_hex(token.strip())))).scalar_one_or_none()
    if row is None or row.status != REGISTERED:
        raise _http(status.HTTP_404_NOT_FOUND, PASS_NOT_FOUND)
    return row


async def set_finalists(db: AsyncSession, actor: User, event: Event, row: EventRegistration,
                        flags: dict[str, bool], request: Request | None) -> EventRegistration:
    event_service.require_editable(event)
    clean: dict[str, bool] = {}
    for key, value in flags.items():
        try:
            activity_id = uuid.UUID(key)
        except ValueError:
            raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, f"finalist_flags: id inválido «{key}»")
        activity = await event_service.get_activity(db, event, activity_id)
        if not (activity.config or {}).get(scoring.FINALISTS_ONLY):
            raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                        f"«{activity.name}» no es una actividad sólo para finalistas")
        if value:
            clean[str(activity_id)] = True
    row.finalist_flags = clean
    row.updated_at = utcnow()
    record_audit(db, action="EVENT_FINALISTS", entity_type=ENTITY_REGISTRATION, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id), "finalists": sorted(clean)},
                 request=request)
    return row


# ----------------------------------------------------------------------------
# Evaluaciones
# ----------------------------------------------------------------------------
def evaluation_out(row: Evaluation, *, created: bool | None = None) -> EvaluationOut:
    return EvaluationOut(
        id=str(row.id), registration_id=str(row.registration_id), activity_id=str(row.activity_id),
        inputs=row.inputs or {}, points=float(row.points), breakdown=row.breakdown or {},
        rules_version=row.rules_version, status=row.status,
        judge_id=str(row.judge_id) if row.judge_id else None, captured_as=row.captured_as,
        revision=row.revision, idempotency_key=row.idempotency_key, created_at=row.created_at, updated_at=row.updated_at,
        created=created,
    )


def revision_out(row: EvaluationRevision) -> EvaluationRevisionOut:
    return EvaluationRevisionOut(
        id=str(row.id), revision=row.revision, inputs=row.inputs or {}, points=float(row.points),
        status=row.status, rules_version=row.rules_version,
        judge_id=str(row.judge_id) if row.judge_id else None, action=row.action, reason=row.reason,
        changed_by_id=str(row.changed_by_id) if row.changed_by_id else None, created_at=row.created_at,
    )


async def get_evaluation(db: AsyncSession, event: Event, evaluation_id: uuid.UUID,
                         *, lock: bool = False) -> Evaluation:
    stmt = (select(Evaluation).join(EventRegistration, EventRegistration.id == Evaluation.registration_id)
            .where(Evaluation.id == evaluation_id, EventRegistration.event_id == event.id))
    if lock:
        stmt = stmt.with_for_update(of=Evaluation)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise _http(status.HTTP_404_NOT_FOUND, EVALUATION_NOT_FOUND)
    return row


def _score(activity: EventActivity, inputs: dict) -> scoring.ScoreResult:
    try:
        return scoring.score(activity.kind, activity.config, inputs)
    except scoring.ScoringError as error:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, error.message)


def _capture_gates(event: Event, registration: EventRegistration, activity: EventActivity) -> None:
    """Event IN_PROGRESS, registration REGISTERED, activity READY and scorable, finalists."""
    _require_in_progress(event)
    if registration.status != REGISTERED:
        raise _http(status.HTTP_409_CONFLICT, NOT_REGISTERED)
    if activity.kind == scoring.GROUP:
        raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, GROUP_NOT_SCORED)
    if activity.status != event_service.READY:
        raise _http(status.HTTP_409_CONFLICT, ACTIVITY_TO_DEFINE)
    if (activity.config or {}).get(scoring.FINALISTS_ONLY) and not (
        registration.finalist_flags or {}
    ).get(str(activity.id)):
        raise _http(status.HTTP_409_CONFLICT, NOT_FINALIST)


async def _by_key(db: AsyncSession, key: str) -> Evaluation | None:
    return (await db.execute(select(Evaluation).where(Evaluation.idempotency_key == key))).scalar_one_or_none()


def _replay(existing: Evaluation, actor: User, payload) -> Evaluation:
    same = (existing.registration_id == payload.registration_id
            and existing.activity_id == payload.activity_id
            and existing.judge_id == actor.id
            and existing.inputs == payload.inputs)
    if not same:
        raise _http(status.HTTP_409_CONFLICT, KEY_REUSED)
    return existing


async def capture(db: AsyncSession, actor: User, event: Event, roles: EventRoles, payload,
                  request: Request | None) -> tuple[Evaluation, bool]:
    """-> (evaluation, created). A retry (same key, same judge, same club and activity)
    returns the stored evaluation — whatever changed since, it is never counted twice."""
    existing = await _by_key(db, payload.idempotency_key)
    if existing is not None:
        return _replay(existing, actor, payload), False
    registration = await get_registration(db, event, payload.registration_id)
    activity = await event_service.get_activity(db, event, payload.activity_id)
    # The assigned judge; or coordination (COORDINATOR staff, event admins) when a judge is
    # missing — same gates, same validation, same idempotency; `captured_as` says which.
    if roles.judges(activity.id, activity.parent_id):
        captured_as = "JUDGE"
    elif roles.coordination:
        captured_as = "COORDINATION"
    else:
        raise _http(status.HTTP_403_FORBIDDEN, NOT_ASSIGNED)
    _capture_gates(event, registration, activity)
    if payload.rules_version is not None and payload.rules_version != event.rules_version:
        raise _http(status.HTTP_409_CONFLICT, RULES_CHANGED)
    result = _score(activity, payload.inputs)
    live = await db.scalar(select(exists().where(
        Evaluation.registration_id == registration.id, Evaluation.activity_id == activity.id,
        Evaluation.status == CONFIRMED)))
    if live:
        raise _http(status.HTTP_409_CONFLICT, ALREADY_SCORED)
    row = Evaluation(
        id=uuid.uuid4(), registration_id=registration.id, activity_id=activity.id,
        inputs=payload.inputs, points=result.points, breakdown=result.breakdown,
        rules_version=event.rules_version, status=CONFIRMED, judge_id=actor.id,
        captured_as=captured_as, idempotency_key=payload.idempotency_key, revision=1,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        # A concurrent twin won the race: same key -> replay it; same club+activity -> 409.
        again = await _by_key(db, payload.idempotency_key)
        if again is not None:
            return _replay(again, actor, payload), False
        raise _http(status.HTTP_409_CONFLICT, ALREADY_SCORED)
    record_audit(db, action="EVALUATION_CREATE", entity_type=ENTITY_EVALUATION, entity_id=row.id,
                 actor=actor, metadata={"event_id": str(event.id),
                                        "registration_id": str(registration.id),
                                        "activity_id": str(activity.id), "points": float(row.points),
                                        "captured_as": captured_as,
                                        "rules_version": row.rules_version}, request=request)
    return row, True


def _snapshot(row: Evaluation, *, action: str, reason: str, actor: User,
              key: str | None) -> EvaluationRevision:
    return EvaluationRevision(
        id=uuid.uuid4(), evaluation_id=row.id, revision=row.revision, inputs=row.inputs,
        points=row.points, status=row.status, rules_version=row.rules_version, judge_id=row.judge_id,
        action=action, reason=reason, changed_by_id=actor.id, idempotency_key=key,
    )


async def correct(db: AsyncSession, actor: User, event: Event, roles: EventRoles,
                  evaluation_id: uuid.UUID, payload, request: Request | None) -> Evaluation:
    if payload.idempotency_key:
        done = (await db.execute(select(EvaluationRevision).where(
            EvaluationRevision.idempotency_key == payload.idempotency_key))).scalar_one_or_none()
        if done is not None:
            if done.evaluation_id != evaluation_id or done.changed_by_id != actor.id:
                raise _http(status.HTTP_409_CONFLICT, KEY_REUSED)
            return await get_evaluation(db, event, evaluation_id)
    row = await get_evaluation(db, event, evaluation_id, lock=True)
    activity = await event_service.get_activity(db, event, row.activity_id)
    if not (roles.coordination or roles.judges(activity.id, activity.parent_id)):
        raise _http(status.HTTP_403_FORBIDDEN, NOT_ASSIGNED)
    registration = await get_registration(db, event, row.registration_id)
    _capture_gates(event, registration, activity)
    if row.status != CONFIRMED:
        raise _http(status.HTTP_409_CONFLICT, ALREADY_VOID)
    if payload.expected_revision != row.revision:
        raise _http(status.HTTP_409_CONFLICT, STALE_REVISION)
    result = _score(activity, payload.inputs)
    previous_points = float(row.points)
    db.add(_snapshot(row, action="CORRECTION", reason=payload.reason, actor=actor,
                     key=payload.idempotency_key))
    row.inputs = payload.inputs
    row.points = result.points
    row.breakdown = result.breakdown
    row.rules_version = event.rules_version
    row.judge_id = actor.id
    row.revision += 1
    row.updated_at = utcnow()
    record_audit(db, action="EVALUATION_CORRECT", entity_type=ENTITY_EVALUATION, entity_id=row.id,
                 actor=actor, details=payload.reason,
                 metadata={"event_id": str(event.id), "revision": row.revision,
                           "from": previous_points, "to": float(result.points)}, request=request)
    return row


async def void(db: AsyncSession, actor: User, event: Event, roles: EventRoles,
               evaluation_id: uuid.UUID, payload, request: Request | None) -> Evaluation:
    if not roles.coordination:
        raise _http(status.HTTP_403_FORBIDDEN, event_service.FORBIDDEN)
    row = await get_evaluation(db, event, evaluation_id, lock=True)
    if row.status == VOID:
        return row  # idempotent
    _require_in_progress(event)
    if payload.expected_revision is not None and payload.expected_revision != row.revision:
        raise _http(status.HTTP_409_CONFLICT, STALE_REVISION)
    db.add(_snapshot(row, action="VOID", reason=payload.reason, actor=actor, key=None))
    row.status = VOID
    row.revision += 1
    row.updated_at = utcnow()
    record_audit(db, action="EVALUATION_VOID", entity_type=ENTITY_EVALUATION, entity_id=row.id,
                 actor=actor, details=payload.reason,
                 metadata={"event_id": str(event.id), "points": float(row.points)}, request=request)
    return row


async def list_evaluations(db: AsyncSession, event: Event, *, activity_ids: set[uuid.UUID] | None,
                           registration_id: uuid.UUID | None = None,
                           include_void: bool = False) -> list[Evaluation]:
    stmt = (select(Evaluation).join(EventRegistration, EventRegistration.id == Evaluation.registration_id)
            .where(EventRegistration.event_id == event.id).order_by(Evaluation.created_at))
    if activity_ids is not None:
        stmt = stmt.where(Evaluation.activity_id.in_(activity_ids or {uuid.uuid4()}))
    if registration_id is not None:
        stmt = stmt.where(Evaluation.registration_id == registration_id)
    if not include_void:
        stmt = stmt.where(Evaluation.status == CONFIRMED)
    return list((await db.execute(stmt)).scalars())


async def list_revisions(db: AsyncSession, row: Evaluation) -> list[EvaluationRevision]:
    stmt = (select(EvaluationRevision).where(EvaluationRevision.evaluation_id == row.id)
            .order_by(EvaluationRevision.revision))
    return list((await db.execute(stmt)).scalars())


def judged_activity_ids(roles: EventRoles, activities: list[EventActivity]) -> set[uuid.UUID]:
    return {a.id for a in activities if roles.judges(a.id, a.parent_id)}


# ----------------------------------------------------------------------------
# Ajustes
# ----------------------------------------------------------------------------
def adjustment_status(row: EventAdjustment) -> str:
    if row.voided_at is not None:
        return "VOID"
    return "APPROVED" if row.approved_by_id is not None else "PENDING"


def adjustment_out(row: EventAdjustment, label: str | None = None) -> AdjustmentOut:
    return AdjustmentOut(
        id=str(row.id), registration_id=str(row.registration_id),
        activity_id=str(row.activity_id) if row.activity_id else None, kind=row.kind,
        adjustment_type_id=str(row.adjustment_type_id) if row.adjustment_type_id else None,
        label=label, points=float(row.points), reason=row.reason, status=adjustment_status(row),
        created_by_id=str(row.created_by_id) if row.created_by_id else None,
        approved_by_id=str(row.approved_by_id) if row.approved_by_id else None,
        approved_at=row.approved_at, voided_at=row.voided_at, void_reason=row.void_reason,
        created_at=row.created_at,
    )


async def list_adjustments(db: AsyncSession, event: Event, *, registration_id: uuid.UUID | None = None):
    stmt = (select(EventAdjustment, EventAdjustmentType.label)
            .join(EventRegistration, EventRegistration.id == EventAdjustment.registration_id)
            .outerjoin(EventAdjustmentType, EventAdjustmentType.id == EventAdjustment.adjustment_type_id)
            .where(EventRegistration.event_id == event.id).order_by(EventAdjustment.created_at))
    if registration_id is not None:
        stmt = stmt.where(EventAdjustment.registration_id == registration_id)
    return list((await db.execute(stmt)).all())


async def _get_adjustment(db: AsyncSession, event: Event, adjustment_id: uuid.UUID) -> EventAdjustment:
    stmt = (select(EventAdjustment)
            .join(EventRegistration, EventRegistration.id == EventAdjustment.registration_id)
            .where(EventAdjustment.id == adjustment_id, EventRegistration.event_id == event.id)
            .with_for_update(of=EventAdjustment))
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise _http(status.HTTP_404_NOT_FOUND, ADJUSTMENT_NOT_FOUND)
    return row


async def _check_limits(db: AsyncSession, event: Event, adjustment_type: EventAdjustmentType,
                        registration: EventRegistration, *, exclude: uuid.UUID | None = None) -> None:
    base = (select(func.count(EventAdjustment.id))
            .join(EventRegistration, EventRegistration.id == EventAdjustment.registration_id)
            .where(EventRegistration.event_id == event.id,
                   EventAdjustment.adjustment_type_id == adjustment_type.id,
                   EventAdjustment.voided_at.is_(None)))
    if exclude is not None:
        base = base.where(EventAdjustment.id != exclude)
    if adjustment_type.max_per_event is not None:
        if int(await db.scalar(base) or 0) >= adjustment_type.max_per_event:
            raise _http(status.HTTP_409_CONFLICT,
                        f"«{adjustment_type.label}» ya alcanzó su límite en el evento "
                        f"({adjustment_type.max_per_event}); anula el anterior para reasignarlo")
    if adjustment_type.max_per_club is not None:
        mine = base.where(EventAdjustment.registration_id == registration.id)
        if int(await db.scalar(mine) or 0) >= adjustment_type.max_per_club:
            raise _http(status.HTTP_409_CONFLICT,
                        f"«{adjustment_type.label}» ya alcanzó su límite para este club "
                        f"({adjustment_type.max_per_club})")


async def create_adjustment(db: AsyncSession, actor: User, event: Event, roles: EventRoles,
                            payload, request: Request | None) -> tuple[EventAdjustment, str | None]:
    """Coordination applies it approved; a judge proposes it PENDING (it does not count yet)."""
    if not (roles.coordination or roles.judge):
        raise _http(status.HTTP_403_FORBIDDEN, event_service.FORBIDDEN)
    _require_in_progress(event)
    # Serialize adjustments of the event so the limits hold under concurrency.
    await event_service.get_event(db, event.id, lock=True)
    registration = await get_registration(db, event, payload.registration_id)
    if registration.status != REGISTERED:
        raise _http(status.HTTP_409_CONFLICT, NOT_REGISTERED)
    if payload.activity_id is not None:
        await event_service.get_activity(db, event, payload.activity_id)
    label = None
    if payload.adjustment_type_id is not None:
        adjustment_type = await event_service.get_adjustment_type(db, event, payload.adjustment_type_id)
        if not adjustment_type.active:
            raise _http(status.HTTP_409_CONFLICT, "Ese tipo de ajuste está desactivado")
        if payload.kind is not None and payload.kind != adjustment_type.kind:
            raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "kind no coincide con el tipo de ajuste")
        if adjustment_type.amount_mode == event_service.FREE:
            # FREE: the amount comes with each adjustment, within the type's bound if any.
            if payload.points is None:
                raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"«{adjustment_type.label}» se aplica con un monto: envía points")
            points = scoring.to_points(payload.points, "points")
            if adjustment_type.max_points is not None and points > adjustment_type.max_points:
                raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"El máximo de «{adjustment_type.label}» es "
                            f"{scoring.as_number(adjustment_type.max_points)}")
        else:
            if adjustment_type.points is None:
                raise _http(status.HTTP_409_CONFLICT,
                            f"El monto de «{adjustment_type.label}» está por definir: no se puede aplicar todavía")
            if payload.points is not None and Decimal(str(payload.points)) != adjustment_type.points:
                raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "El monto lo fija el tipo de ajuste: no lo envíes o envía el mismo")
            points = adjustment_type.points
        await _check_limits(db, event, adjustment_type, registration)
        kind, label = adjustment_type.kind, adjustment_type.label
    else:
        if not roles.coordination:
            raise _http(status.HTTP_403_FORBIDDEN, "Un ajuste sin tipo sólo lo aplica la coordinación")
        if payload.kind is None or payload.points is None:
            raise _http(status.HTTP_422_UNPROCESSABLE_ENTITY, "Un ajuste sin tipo necesita kind y points")
        kind, points = payload.kind, scoring.to_points(payload.points, "points")
    now = utcnow()
    row = EventAdjustment(
        id=uuid.uuid4(), registration_id=registration.id, activity_id=payload.activity_id, kind=kind,
        adjustment_type_id=payload.adjustment_type_id, points=points, reason=payload.reason,
        created_by_id=actor.id,
        approved_by_id=actor.id if roles.coordination else None,
        approved_at=now if roles.coordination else None,
    )
    db.add(row)
    record_audit(db, action="EVENT_ADJUSTMENT_CREATE", entity_type=ENTITY_ADJUSTMENT, entity_id=row.id,
                 actor=actor, details=payload.reason,
                 metadata={"event_id": str(event.id), "registration_id": str(registration.id),
                           "kind": kind, "points": float(points), "approved": roles.coordination},
                 request=request)
    return row, label


async def approve_adjustment(db: AsyncSession, actor: User, event: Event, adjustment_id: uuid.UUID,
                             request: Request | None) -> EventAdjustment:
    _require_in_progress(event)
    row = await _get_adjustment(db, event, adjustment_id)
    if row.voided_at is not None:
        raise _http(status.HTTP_409_CONFLICT, "El ajuste está anulado")
    if row.approved_by_id is not None:
        return row
    row.approved_by_id = actor.id
    row.approved_at = utcnow()
    record_audit(db, action="EVENT_ADJUSTMENT_APPROVE", entity_type=ENTITY_ADJUSTMENT,
                 entity_id=row.id, actor=actor, metadata={"event_id": str(event.id)}, request=request)
    return row


async def void_adjustment(db: AsyncSession, actor: User, event: Event, adjustment_id: uuid.UUID,
                          reason: str, request: Request | None) -> EventAdjustment:
    _require_in_progress(event)
    row = await _get_adjustment(db, event, adjustment_id)
    if row.voided_at is not None:
        return row
    row.voided_at = utcnow()
    row.voided_by_id = actor.id
    row.void_reason = reason
    record_audit(db, action="EVENT_ADJUSTMENT_VOID", entity_type=ENTITY_ADJUSTMENT, entity_id=row.id,
                 actor=actor, details=reason, metadata={"event_id": str(event.id)}, request=request)
    return row


# ----------------------------------------------------------------------------
# Totales, desglose y clasificación
# ----------------------------------------------------------------------------
@dataclass
class Snapshot:
    event: Event
    activities: list[EventActivity]
    evaluations: dict[tuple[uuid.UUID, uuid.UUID], Evaluation] = field(default_factory=dict)
    adjustments: dict[uuid.UUID, list[tuple[EventAdjustment, str | None]]] = field(default_factory=dict)


async def snapshot(db: AsyncSession, event: Event) -> Snapshot:
    snap = Snapshot(event=event, activities=await event_service.list_activities(db, event))
    for row in await list_evaluations(db, event, activity_ids=None):
        snap.evaluations[(row.registration_id, row.activity_id)] = row
    for row, label in await list_adjustments(db, event):
        snap.adjustments.setdefault(row.registration_id, []).append((row, label))
    return snap


def _counts(activity: EventActivity, by_id: dict[uuid.UUID, EventActivity]) -> bool:
    if not activity.counts_to_total:
        return False
    parent = by_id.get(activity.parent_id) if activity.parent_id else None
    return parent is None or parent.counts_to_total


def breakdown(snap: Snapshot, registration: EventRegistration, *, show_honor: bool) -> dict:
    """The official total of one club, activity by activity. `points` is None while pending:
    «pendiente» is never «0»."""
    by_id = {a.id: a for a in snap.activities}
    flags = registration.finalist_flags or {}
    leaves: dict[uuid.UUID, dict] = {}
    evaluated_total = Decimal(0)
    done = expected = 0
    for activity in snap.activities:
        if activity.kind == scoring.GROUP:
            continue
        evaluation = snap.evaluations.get((registration.id, activity.id))
        counts = _counts(activity, by_id)
        node = {"activity_id": str(activity.id), "name": activity.name, "kind": activity.kind,
                "max_points": _num(activity.max_points), "counts_to_total": counts,
                "points": None, "state": "pending", "evaluation": None}
        if evaluation is not None:
            node.update(points=float(evaluation.points), state="scored",
                        evaluation={"id": str(evaluation.id), "revision": evaluation.revision,
                                    "breakdown": evaluation.breakdown or {},
                                    "rules_version": evaluation.rules_version})
            if counts:
                evaluated_total += evaluation.points
        elif (activity.config or {}).get(scoring.FINALISTS_ONLY) and not flags.get(str(activity.id)):
            node["state"] = "not_applicable"  # no finalista: 0 en ESTE componente, no pendiente
        elif activity.status == event_service.TO_DEFINE:
            node["state"] = "to_define"
        if counts and node["state"] != "not_applicable":
            expected += 1
            done += node["state"] == "scored"
        leaves[activity.id] = node
    tree = []
    for activity in snap.activities:
        if activity.parent_id is not None:
            continue
        if activity.kind != scoring.GROUP:
            tree.append(leaves[activity.id])
            continue
        children = [leaves[c.id] for c in snap.activities if c.parent_id == activity.id]
        scored = [c for c in children if c["state"] == "scored"]
        open_children = [c for c in children if c["state"] in ("pending", "to_define")]
        state = "pending" if not scored else ("partial" if open_children else "scored")
        if not children:
            state = "pending"
        tree.append({"activity_id": str(activity.id), "name": activity.name, "kind": activity.kind,
                     "max_points": _num(activity.max_points),
                     "counts_to_total": activity.counts_to_total,
                     "points": float(sum(Decimal(str(c["points"])) for c in scored)) if scored else None,
                     "state": state, "children": children})
    bonus = penalty = Decimal(0)
    adjustments, pending_adjustments = [], []
    for row, label in snap.adjustments.get(registration.id, []):
        state = adjustment_status(row)
        item = {"id": str(row.id), "kind": row.kind, "label": label, "points": float(row.points),
                "reason": row.reason, "status": state,
                "activity_id": str(row.activity_id) if row.activity_id else None}
        if state == "APPROVED":
            adjustments.append(item)
            if row.kind == BONUS:
                bonus += row.points
            else:
                penalty += row.points
        elif state == "PENDING":
            pending_adjustments.append(item)
    total = (evaluated_total + bonus - penalty).quantize(scoring.CENT)
    honor = scoring.honor_for(total, snap.event.honor_bands or []) if show_honor else None
    return {
        "registration_id": str(registration.id),
        "club_id": str(registration.club_id),
        "status": registration.status,
        "total": float(total),
        "evaluated_points": float(evaluated_total),
        "bonus": float(bonus),
        "penalty": float(penalty),
        "progress": {"done": done, "expected": expected, "complete": expected > 0 and done == expected},
        "honor": honor,
        "honor_final": snap.event.status in (event_service.CLOSED, event_service.ARCHIVED),
        "activities": tree,
        "adjustments": adjustments,
        "pending_adjustments": pending_adjustments,
        "rules_version": snap.event.rules_version,
    }


async def registration_breakdown(db: AsyncSession, event: Event, registration: EventRegistration,
                                 roles: EventRoles) -> dict:
    """Coordination sees everything (provisional honour included). A director only their own
    club, and the honour only once the event is CLOSED."""
    if roles.coordination:
        show_honor = True
    elif registration.club_id in roles.director_club_ids:
        show_honor = event.status in (event_service.CLOSED, event_service.ARCHIVED)
    else:
        raise _http(status.HTTP_403_FORBIDDEN, DIRECTOR_ONLY_OWN)
    snap = await snapshot(db, event)
    club = await db.get(Organization, registration.club_id)
    result = breakdown(snap, registration, show_honor=show_honor)
    result["club"] = {"id": str(club.id), "name": club.name}
    if not roles.coordination:
        result.pop("pending_adjustments", None)
    return result


async def standings(db: AsyncSession, event: Event) -> list[dict]:
    snap = await snapshot(db, event)
    rows = []
    for registration, club in await list_registrations(db, event):
        if registration.status != REGISTERED:
            continue
        item = breakdown(snap, registration, show_honor=True)
        rows.append({"registration_id": item["registration_id"],
                     "club": {"id": str(club.id), "name": club.name, "city": club.city},
                     "total": item["total"], "bonus": item["bonus"], "penalty": item["penalty"],
                     "progress": item["progress"], "honor": item["honor"],
                     "pending_adjustments": len(item["pending_adjustments"])})
    rows.sort(key=lambda row: (-row["total"], row["club"]["name"]))
    for index, row in enumerate(rows, start=1):
        row["position"] = index
    return rows
