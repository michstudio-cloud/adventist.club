"""Bloque F · F2 — Actividades: horas de servicio y asistencia a reuniones.

The member records their own and the director approves them; or the director records the
club's outing for several members at once, already approved (§1.3: «las horas las asigna y
las aprueba el director»). An approved row is the ONLY way a `HOURS` requirement completes.

Privacy: `description` and `place` say where a minor was and when. They are read with
`can_view_portfolio` (the member, their guardians, the staff of their club and the hierarchy
above it) and are never public. Every write leaves an audit row per member.
"""
import uuid
from datetime import date

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, HonorEnrollment, Organization, User
from app.rbac import (
    can_approve_activity,
    can_view_portfolio,
    club_scope_paths,
    is_master,
    member_club,
)
from app.schemas.activity import ActivityCreate, ActivityDecision, ActivityListOut, ActivityOut
from app.schemas.portfolio import PersonRef
from app.security import utcnow
from app.services.audit import record_audit

SUBMITTED, APPROVED, REJECTED = "SUBMITTED", "APPROVED", "REJECTED"
ACTIVITY = "ACTIVITY_LOG"


# ----------------------------------------------------------------------------
# What a `HOURS` requirement counts
# ----------------------------------------------------------------------------
async def approved_totals(db: AsyncSession, enrollment: HonorEnrollment) -> dict[str, float]:
    """Approved quantity by category that counts for THIS enrollment.

    §1.3: only what was done from the day the member started the program. Hours earned
    before do not count — the card is the work of this class, not a lifetime total.
    """
    started = enrollment.started_at.date() if enrollment.started_at else date.min
    stmt = (
        select(ActivityLog.category, func.sum(ActivityLog.quantity))
        .where(
            ActivityLog.user_id == enrollment.user_id,
            ActivityLog.status == APPROVED,
            ActivityLog.performed_on >= started,
        )
        .group_by(ActivityLog.category)
    )
    return {category: float(total) for category, total in (await db.execute(stmt)).all()}


# ----------------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------------
async def _out(
    db: AsyncSession, logs: list[ActivityLog], actor: User, *, deciders: bool = True
) -> list[ActivityOut]:
    people = {
        user.id: user
        for user in (
            await db.execute(
                select(User).where(
                    User.id.in_(
                        {log.user_id for log in logs}
                        | {log.decided_by_id for log in logs if log.decided_by_id}
                    )
                )
            )
        ).scalars()
    }
    out = []
    for log in logs:
        member = people.get(log.user_id)
        decider = people.get(log.decided_by_id) if log.decided_by_id else None
        out.append(
            ActivityOut(
                id=str(log.id),
                user=PersonRef(id=str(log.user_id), name=member.name if member else ""),
                category=log.category,
                performed_on=log.performed_on,
                quantity=float(log.quantity),
                description=log.description,
                place=log.place,
                status=log.status,
                decided_by=PersonRef(id=str(decider.id), name=decider.name) if decider else None,
                decided_at=log.decided_at,
                decision_note=log.decision_note,
                created_at=log.created_at,
                can_decide=(
                    deciders
                    and log.status == SUBMITTED
                    and member is not None
                    and await can_approve_activity(db, actor, member)
                ),
            )
        )
    return out


# ----------------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------------
async def create_logs(
    db: AsyncSession, actor: User, payload: ActivityCreate, request: Request | None
) -> list[ActivityOut]:
    """Without `user_ids`, the member records their own (SUBMITTED). With `user_ids`, whoever
    may approve the hours of those members records them already APPROVED — that person is the
    one who would approve them anyway, and it is how a club registers a Saturday's outing."""
    targets: list[User] = []
    if not payload.user_ids:
        targets = [actor]
    else:
        for user_id in dict.fromkeys(payload.user_ids):
            member = await db.get(User, user_id)
            if member is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Miembro no encontrado")
            if not await can_approve_activity(db, actor, member):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Solo el director del club (o su Asociación) registra horas de otros miembros",
                )
            targets.append(member)

    now = utcnow()
    created: list[ActivityLog] = []
    for member in targets:
        own = member.id == actor.id
        club = await member_club(db, member)
        log = ActivityLog(
            id=uuid.uuid4(),
            user_id=member.id,
            club_id=club.id if club else None,
            category=payload.category,
            performed_on=payload.performed_on,
            quantity=payload.quantity,
            description=payload.description.strip(),
            place=payload.place,
            status=SUBMITTED if own else APPROVED,
            created_by_id=actor.id,
            decided_by_id=None if own else actor.id,
            decided_at=None if own else now,
            created_at=now,
            updated_at=now,
        )
        db.add(log)
        # One audit row per member: the trail of each minor has to be complete on its own.
        record_audit(
            db,
            action="ACTIVITY_LOG_CREATE",
            entity_type=ACTIVITY,
            entity_id=log.id,
            actor=actor,
            metadata={"user_id": str(member.id), "category": log.category,
                      "quantity": float(log.quantity), "performed_on": log.performed_on.isoformat(),
                      "status": log.status, "on_behalf": not own},
            request=request,
        )
        created.append(log)
    await db.flush()
    await _sync_members(db, actor, [member.id for member in targets], request)
    await db.commit()
    return await _out(db, created, actor)


async def _sync_members(db, actor, user_ids, request) -> None:
    from app.services import portfolio_links

    for user_id in dict.fromkeys(user_ids):
        await portfolio_links.sync(db, actor, user_id=user_id, request=request)


# ----------------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------------
async def list_logs(
    db: AsyncSession,
    actor: User,
    user_id: uuid.UUID | None,
    category: str | None,
    status_filter: str | None,
) -> ActivityListOut:
    target = actor if user_id is None or user_id == actor.id else await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    if target.id != actor.id and not await can_view_portfolio(db, actor, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver este portafolio")

    conditions = [ActivityLog.user_id == target.id]
    if category:
        conditions.append(ActivityLog.category == category)
    if status_filter:
        conditions.append(ActivityLog.status == status_filter)
    rows = (
        await db.execute(
            select(ActivityLog)
            .where(*conditions)
            .order_by(ActivityLog.performed_on.desc(), ActivityLog.created_at.desc())
        )
    ).scalars().all()
    totals = (
        await db.execute(
            select(ActivityLog.category, func.sum(ActivityLog.quantity))
            .where(ActivityLog.user_id == target.id, ActivityLog.status == APPROVED)
            .group_by(ActivityLog.category)
        )
    ).all()
    by_category = {"SERVICE": 0.0, "ATTENDANCE": 0.0}
    by_category.update({category: float(total) for category, total in totals})
    return ActivityListOut(logs=await _out(db, list(rows), actor), totals=by_category)


async def queue(db: AsyncSession, actor: User, limit: int, offset: int) -> list[ActivityOut]:
    """«Horas por aprobar»: what is waiting for `actor`, never their own."""
    conditions = [ActivityLog.status == SUBMITTED, ActivityLog.user_id != actor.id]
    if not is_master(actor):
        reach = []
        club = await member_club(db, actor)
        if club is not None:
            reach.append(ActivityLog.club_id == club.id)
        paths = await club_scope_paths(db, actor)  # [] unless the actor is an administrator
        if paths:
            reach.append(
                ActivityLog.club_id.in_(
                    select(Organization.id).where(
                        or_(*[Organization.path.op("<@")(path) for path in paths])
                    )
                )
            )
        if not reach:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Solo quien aprueba horas de un club tiene cola de actividades",
            )
        conditions.append(or_(*reach) if len(reach) > 1 else reach[0])
    rows = (
        await db.execute(
            select(ActivityLog)
            .where(*conditions)
            .order_by(ActivityLog.performed_on.desc(), ActivityLog.created_at)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    # The serializer asks `can_approve_activity` row by row: the club filter above is only a
    # coarse SQL narrowing, never the last word.
    out = await _out(db, list(rows), actor)
    return [row for row in out if row.can_decide]


# ----------------------------------------------------------------------------
# Decide and delete
# ----------------------------------------------------------------------------
async def _get_log(db: AsyncSession, log_id: uuid.UUID) -> ActivityLog:
    log = await db.get(ActivityLog, log_id)
    if log is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Actividad no encontrada")
    return log


async def decide(
    db: AsyncSession,
    actor: User,
    log_id: uuid.UUID,
    payload: ActivityDecision,
    request: Request | None,
) -> ActivityOut:
    log = await _get_log(db, log_id)
    member = await db.get(User, log.user_id)
    if member is None or not await can_approve_activity(db, actor, member):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Nadie aprueba sus propias horas" if log.user_id == actor.id
            else "No tienes jurisdicción para decidir estas horas",
        )
    previous = log.status
    log.status = payload.status
    log.decided_by_id = actor.id
    log.decided_at = utcnow()
    log.decision_note = payload.note
    log.updated_at = utcnow()
    record_audit(
        db,
        action="ACTIVITY_LOG_DECIDE",
        entity_type=ACTIVITY,
        entity_id=log.id,
        actor=actor,
        details=payload.note,
        metadata={"user_id": str(log.user_id), "from": previous, "to": log.status,
                  "category": log.category, "quantity": float(log.quantity)},
        request=request,
    )
    await db.flush()
    # The sum changed in both directions: a requirement may complete, or come back.
    await _sync_members(db, actor, [log.user_id], request)
    await db.commit()
    return (await _out(db, [log], actor))[0]


async def delete_log(
    db: AsyncSession, actor: User, log_id: uuid.UUID, request: Request | None
) -> None:
    log = await _get_log(db, log_id)
    if log.user_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo quien registró la actividad puede borrarla")
    if log.status != SUBMITTED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Una actividad ya decidida no se borra: pide que se rechace"
        )
    record_audit(
        db,
        action="ACTIVITY_LOG_DELETE",
        entity_type=ACTIVITY,
        entity_id=log.id,
        actor=actor,
        metadata={"category": log.category, "quantity": float(log.quantity)},
        request=request,
    )
    await db.delete(log)
    await db.commit()
