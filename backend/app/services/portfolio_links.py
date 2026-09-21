"""Bloque F · F2 — the requirements a member does not have to send: those that are already
satisfied by something the platform can see.

  `HONOR` with a concrete target -> a CERTIFIED enrollment of any version of that honor
  `HONOR` open (a category, or free choice) -> the member picks: `link_open_honor`
  `PROGRAM`                                 -> a CERTIFIED enrollment of the target program
  `HOURS`                                   -> approved activity adds up to the target

`sync` is idempotent and runs in the caller's transaction at the three moments of §1.3:
enrolling in a program, issuing ANY certificate of that member, and deciding an activity.

Two rules it never breaks:
  * it never un-completes what a reviewer judged (`completed_via = 'REVIEW'` is untouchable);
  * it never touches an honor enrollment, nor a frozen one (CERTIFIED / WITHDRAWN).
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import violated_constraint
from app.models import Honor, HonorEnrollment, RequirementProgress, User
from app.services import curriculum
from app.services.audit import record_audit

CERTIFIED, WITHDRAWN = "CERTIFIED", "WITHDRAWN"
OPEN_STATUSES = ("IN_PROGRESS", "READY")
PENDING, COMPLETE = "PENDING", "COMPLETE"
REVIEW = "REVIEW"
SATISFIED_ONCE_CONSTRAINT = "requirement_progress_satisfied_by_key"


# ----------------------------------------------------------------------------
# What the member already holds
# ----------------------------------------------------------------------------
async def _certified_enrollments(db: AsyncSession, user_id: uuid.UUID) -> list[HonorEnrollment]:
    stmt = select(HonorEnrollment).where(
        HonorEnrollment.user_id == user_id, HonorEnrollment.status == CERTIFIED
    )
    return list((await db.execute(stmt)).scalars().all())


async def _open_program_enrollments(
    db: AsyncSession, user_id: uuid.UUID
) -> list[HonorEnrollment]:
    stmt = select(HonorEnrollment).where(
        HonorEnrollment.user_id == user_id,
        HonorEnrollment.program_id.is_not(None),
        HonorEnrollment.status.in_(OPEN_STATUSES),
    )
    return list((await db.execute(stmt)).scalars().all())


# ----------------------------------------------------------------------------
# sync
# ----------------------------------------------------------------------------
async def sync(
    db: AsyncSession, actor: User | None, *, user_id: uuid.UUID, request: Request | None = None
) -> int:
    """Bring every OPEN program enrollment of `user_id` up to date. Returns how many
    requirements changed. Stages everything on the caller's session; the caller commits."""
    from app.services import portfolio  # this module is the one that builds on portfolio

    enrollments = await _open_program_enrollments(db, user_id)
    if not enrollments:
        return 0
    achievements = await _certified_enrollments(db, user_id)
    changed = 0
    for enrollment in enrollments:
        specs = {spec.position: spec for spec in await curriculum.enrollment_specs(db, enrollment)}
        rows = (
            await db.execute(
                select(RequirementProgress)
                .where(RequirementProgress.enrollment_id == enrollment.id)
                .order_by(RequirementProgress.requirement_position)
            )
        ).scalars().all()
        used = {row.satisfied_by_enrollment_id for row in rows if row.satisfied_by_enrollment_id}
        totals = None
        for row in rows:
            spec = specs.get(row.requirement_position)
            if spec is None or spec.kind == curriculum.FREE:
                continue
            if spec.kind == curriculum.HOURS:
                if totals is None:
                    from app.services import activity

                    totals = await activity.approved_totals(db, enrollment)
                changed += await _sync_hours(db, actor, enrollment, row, spec, totals, request)
                continue
            if row.status == COMPLETE:
                continue
            source = await _matching_achievement(db, spec, achievements, used)
            if source is None:
                continue
            if await portfolio.auto_complete(
                db, actor, enrollment, row, via=spec.kind, source=source, request=request
            ):
                used.add(source.id)
                changed += 1
        if changed:
            await portfolio.recompute_ready(db, enrollment)
    return changed


async def _matching_achievement(
    db: AsyncSession,
    spec: curriculum.RequirementSpec,
    achievements: list[HonorEnrollment],
    used: set[uuid.UUID],
) -> HonorEnrollment | None:
    """Rule 9: concrete targets only. An OPEN slot is never filled automatically — the member
    chooses which of their honors to spend on it (`link_open_honor`), so a concrete
    requirement is never left without the honor it needed."""
    if spec.kind == curriculum.HONOR and spec.target_honor_id:
        lineage = set(await curriculum.honor_lineage_ids(db, spec.target_honor_id))
        return next(
            (a for a in achievements if a.honor_id in lineage and a.id not in used), None
        )
    if spec.kind == curriculum.PROGRAM and spec.target_program_id:
        lineage = set(await curriculum.program_lineage_ids(db, spec.target_program_id))
        return next(
            (a for a in achievements if a.program_id in lineage and a.id not in used), None
        )
    return None


async def _sync_hours(
    db: AsyncSession,
    actor: User | None,
    enrollment: HonorEnrollment,
    row: RequirementProgress,
    spec: curriculum.RequirementSpec,
    totals: dict[str, float],
    request: Request | None,
) -> int:
    """Up when the approved activity reaches the target, and back down when it stops doing so.

    Only a row this very mechanism completed is ever taken back: a verdict of a reviewer, or
    a `COMPLETE` given before the program grew a `HOURS` requirement, is left alone.
    """
    from app.services import portfolio

    approved = totals.get(spec.activity_category or "", 0.0)
    target = spec.target_quantity or 0
    if approved + 1e-9 >= target and row.status != COMPLETE:
        return int(await portfolio.auto_complete(
            db, actor, enrollment, row, via=curriculum.HOURS, source=None, request=request
        ))
    if approved + 1e-9 < target and row.status == COMPLETE and row.completed_via == curriculum.HOURS:
        row.status = PENDING
        row.completed_via = None
        row.submitted_at = None
        record_audit(
            db,
            action="REQUIREMENT_UNLINK",
            entity_type="REQUIREMENT_PROGRESS",
            entity_id=row.id,
            actor=actor,
            metadata={"enrollment_id": str(enrollment.id), "position": row.requirement_position,
                      "via": curriculum.HOURS, "approved": approved, "target": float(target)},
            request=request,
        )
        return 1
    return 0


# ----------------------------------------------------------------------------
# The open slot: the member's choice
# ----------------------------------------------------------------------------
async def link_open_honor(
    db: AsyncSession,
    actor: User,
    enrollment: HonorEnrollment,
    position: int,
    honor_enrollment_id: uuid.UUID,
    request: Request | None,
):
    """`PUT /enrollments/{id}/requirements/{position}` with `{honor_enrollment_id}`.

    Validates owner, CERTIFIED, the category the slot asks for and rule 9 (one achievement
    fills at most one requirement of this enrollment).
    """
    from app.services import portfolio

    progress = await portfolio._get_progress(db, enrollment, position)
    specs = {spec.position: spec for spec in await curriculum.enrollment_specs(db, enrollment)}
    spec = specs.get(position)
    if spec is None or spec.kind != curriculum.HONOR:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Este requisito no se completa con una especialidad",
        )
    if progress.status == COMPLETE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El requisito ya está completo; solo un revisor puede reabrirlo",
        )

    source = await db.get(HonorEnrollment, honor_enrollment_id)
    if source is None or source.user_id != actor.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inscripción no encontrada")
    if source.status != CERTIFIED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Solo una especialidad ya certificada completa un requisito"
        )
    if source.honor_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Este requisito se completa con una especialidad, no con un programa",
        )
    if spec.target_honor_id:
        lineage = set(await curriculum.honor_lineage_ids(db, spec.target_honor_id))
        if source.honor_id not in lineage:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Este requisito pide otra especialidad",
            )
    if spec.target_category_id:
        honor = await db.get(Honor, source.honor_id)
        if honor is None or honor.category_id != spec.target_category_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Esta especialidad no es de la categoría que pide el requisito",
            )

    try:
        await portfolio.auto_complete(
            db, actor, enrollment, progress, via=curriculum.HONOR, source=source,
            request=request, action="REQUIREMENT_LINK", after_verdict=True,
        )
        await portfolio.recompute_ready(db, enrollment)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != SATISFIED_ONCE_CONSTRAINT:
            raise
        # Rule 9: the same achievement cannot fill two slots of the same card.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esa especialidad ya completa otro requisito de esta inscripción",
        )
    return await portfolio._detail(db, actor, enrollment)


# ----------------------------------------------------------------------------
# Rule 11: the contract certificate revocation will call (it does not exist yet)
# ----------------------------------------------------------------------------
async def unlink(
    db: AsyncSession,
    actor: User | None,
    source_enrollment_id: uuid.UUID,
    request: Request | None = None,
) -> int:
    """Every requirement an achievement completed goes back to PENDING when that achievement
    stops counting (a revoked certificate). A CERTIFIED card is NOT reopened: it is flagged
    for audit, because taking back an investiture is a human decision, not a cascade.
    """
    rows = (
        await db.execute(
            select(RequirementProgress, HonorEnrollment)
            .join(HonorEnrollment, HonorEnrollment.id == RequirementProgress.enrollment_id)
            .where(RequirementProgress.satisfied_by_enrollment_id == source_enrollment_id)
        )
    ).all()
    touched = 0
    for progress, enrollment in rows:
        frozen = enrollment.status in (CERTIFIED, WITHDRAWN)
        record_audit(
            db,
            action="REQUIREMENT_UNLINK",
            entity_type="REQUIREMENT_PROGRESS",
            entity_id=progress.id,
            actor=actor,
            metadata={"enrollment_id": str(enrollment.id),
                      "position": progress.requirement_position,
                      "source_enrollment_id": str(source_enrollment_id),
                      "needs_audit": frozen},
            request=request,
        )
        progress.satisfied_by_enrollment_id = None
        if not frozen and progress.completed_via != REVIEW:
            progress.status = PENDING
            progress.completed_via = None
        touched += 1
    return touched
