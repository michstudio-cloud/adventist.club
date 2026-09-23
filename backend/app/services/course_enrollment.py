"""Bloque B · I3 — Joining, leaving and being removed from a course.

A course enrollment IS an enrollment of Bloque A: the same row, the same requirement
progress, the same certificate. Joining only moves it to `mode = 'COURSE'` with a
`course_id` and applies the course's evaluation plan; leaving moves it back. Nothing is
ever deleted or reset, so a member who leaves (or is removed) keeps every verdict.

Integrity rules (spec §3.9):
  2. `mode = 'COURSE'` <=> `course_id` is set (CHECK) and `course.honor_id = enrollment.honor_id`.
  3. One course per enrollment, one enrollment per honor (index of A).
  5. Everything the instructor does on an enrollment asks `course_author_in_good_standing`
     (the letter, or an institutional author) NOW.
  6. Rules 1-5 of A keep applying to COURSE enrollments without exception.

Each function commits its change together with its audit row.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Course,
    CourseRequirement,
    Guardianship,
    Honor,
    HonorEnrollment,
    Organization,
    RequirementProgress,
    User,
)
from app.rbac import CONSENT_GRANTED, course_author_in_good_standing, is_master
from app.schemas.course import CourseMember, CourseMemberRemove, JoinedCourse
from app.schemas.portfolio import ClubRef, Counters, EnrollmentCreate, EnrollmentDetail, PersonRef
from app.security import utcnow
from app.services import courses as courses_service
from app.services import portfolio
from app.services.audit import record_audit
from app.workflow import PUBLISHED

COURSE, CLUB = "COURSE", "CLUB"
ENTITY = "ENROLLMENT"


# ----------------------------------------------------------------------------
# Guards
# ----------------------------------------------------------------------------
async def _open_course(db: AsyncSession, course_id: uuid.UUID, *, lock: bool = False) -> Course:
    """A course a member may be inside of. 404 (never 403) while it is not published: an
    unpublished course does not exist for anyone but its author and its reviewers."""
    course = await courses_service._get_course(db, course_id, lock=lock)
    if course.status != PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")
    return course


async def _require_joinable(db: AsyncSession, actor: User, course: Course) -> None:
    """Conditions 1 and 2 of §3.6, asked again inside the locked transaction."""
    if not course.enrollment_open:
        raise HTTPException(status.HTTP_409_CONFLICT, "El curso no admite inscripciones ahora")
    honor = await db.get(Honor, course.honor_id)
    if honor is None or honor.status != PUBLISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "La especialidad ya no está publicada")
    if course.instructor_id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes inscribirte en tu propio curso")
    instructor = await db.get(User, course.instructor_id)
    if not await course_author_in_good_standing(db, instructor):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El instructor del curso no tiene su carta autorizada en este momento",
        )
    if actor.status != "ACTIVE":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tu cuenta no está activa")
    if actor.is_minor and not await _has_consent(db, actor):
        # D2a: any active account may join; a minor needs their guardian's consent first.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Tu tutor debe autorizar tu participación en un curso"
        )


async def _has_consent(db: AsyncSession, member: User) -> bool:
    stmt = select(Guardianship.id).where(
        Guardianship.child_id == member.id, Guardianship.consent_status == CONSENT_GRANTED
    )
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _plan_of(db: AsyncSession, course_id: uuid.UUID) -> dict[int, str]:
    stmt = select(CourseRequirement.requirement_position, CourseRequirement.assessment).where(
        CourseRequirement.course_id == course_id
    )
    return dict((await db.execute(stmt)).all())


# ----------------------------------------------------------------------------
# Join
# ----------------------------------------------------------------------------
async def join(
    db: AsyncSession, actor: User, course_id: uuid.UUID, request: Request | None
) -> EnrollmentDetail:
    """§3.6. Idempotent: calling it again answers with the same enrollment.

    The enrollment row itself is created by the service of Bloque A, which commits on its
    own; everything that decides a seat (capacity, the course being open) is then checked
    again with the course locked, so two members cannot take the same last seat.
    """
    course = await _open_course(db, course_id)
    await _require_joinable(db, actor, course)
    existing = await portfolio._live_enrollment(db, actor.id, course.honor_id)
    if existing is not None and existing.course_id == course.id:
        return await portfolio._detail(db, actor, existing)
    if existing is None:
        await _check_capacity(db, course)  # cheap refusal before creating anything
        await portfolio.enroll(
            db,
            actor,
            EnrollmentCreate(honor_id=course.honor_id, locale=course.locale),
            request,
        )

    # The transaction that decides: the course is locked, so the seat count cannot move.
    course = await _open_course(db, course_id, lock=True)
    await _require_joinable(db, actor, course)
    enrollment = await portfolio._live_enrollment(db, actor.id, course.honor_id)
    if enrollment is None:  # withdrawn between the two steps
        raise HTTPException(status.HTTP_409_CONFLICT, "No tienes una inscripción viva en esta especialidad")
    enrollment = await portfolio._get_enrollment(db, enrollment.id, lock=True)
    if enrollment.course_id == course.id:
        return await portfolio._detail(db, actor, enrollment)
    if enrollment.status != portfolio.IN_PROGRESS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tu inscripción en esta especialidad ya está lista o certificada",
        )
    if enrollment.course_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sal primero del curso actual")
    await _check_capacity(db, course)

    plan = await _plan_of(db, course.id)
    rows = await _progress_rows(db, enrollment.id)
    if set(plan) != {row.requirement_position for row in rows}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tu inscripción no tiene los mismos requisitos que el curso; sal de la"
            " especialidad y vuelve a empezarla desde el curso",
        )

    now = utcnow()
    enrollment.mode = COURSE
    enrollment.course_id = course.id
    enrollment.course_joined_at = now
    enrollment.course_removed_reason = None
    _apply_plan(rows, plan)
    await portfolio._touch(db, enrollment)
    await _recalculate_ready(db, enrollment, now)
    record_audit(
        db,
        action="COURSE_JOIN",
        entity_type=ENTITY,
        entity_id=enrollment.id,
        actor=actor,
        metadata={"course_id": str(course.id), "honor_id": str(course.honor_id)},
        request=request,
    )
    await db.commit()
    return await portfolio._detail(db, actor, enrollment)


async def _check_capacity(db: AsyncSession, course: Course) -> None:
    if course.capacity is None:
        return  # no cap, no waiting list (§3.6)
    if await courses_service._enrolled_count(db, course.id) >= course.capacity:
        raise HTTPException(status.HTTP_409_CONFLICT, "Curso lleno")


async def _progress_rows(db: AsyncSession, enrollment_id: uuid.UUID) -> list[RequirementProgress]:
    stmt = (
        select(RequirementProgress)
        .where(RequirementProgress.enrollment_id == enrollment_id)
        .order_by(RequirementProgress.requirement_position)
    )
    return list((await db.execute(stmt)).scalars().all())


def _apply_plan(rows: list[RequirementProgress], plan: dict[int, str]) -> None:
    """The course's plan over the rows that are NOT complete: what is already earned stays
    earned. A requirement the course answers with an exam stops waiting for a human."""
    for row in rows:
        if row.status == portfolio.COMPLETE:
            continue
        assessment = plan.get(row.requirement_position)
        row.is_practical = assessment == courses_service.EVIDENCE
        if assessment == courses_service.EXAM and row.status == portfolio.SUBMITTED:
            row.status = portfolio.PENDING  # the answer is kept; the exam decides now
            row.submitted_at = None


async def _recalculate_ready(db: AsyncSession, enrollment: HonorEnrollment, now) -> None:
    """Rule 2 of A, reused verbatim: all COMPLETE => READY, otherwise IN_PROGRESS."""
    await db.flush()
    open_rows = select(func.count()).where(
        RequirementProgress.enrollment_id == enrollment.id,
        RequirementProgress.status != portfolio.COMPLETE,
    )
    all_complete = (await db.execute(open_rows)).scalar_one() == 0
    if all_complete and enrollment.status != portfolio.READY:
        enrollment.status, enrollment.ready_at = portfolio.READY, now
    elif not all_complete and enrollment.status == portfolio.READY:
        enrollment.status, enrollment.ready_at = portfolio.IN_PROGRESS, None


# ----------------------------------------------------------------------------
# Leave and remove
# ----------------------------------------------------------------------------
async def leave(
    db: AsyncSession, actor: User, course_id: uuid.UUID, request: Request | None
) -> EnrollmentDetail:
    """«Pasar a modalidad club»: nothing is lost. `is_practical` is NOT relaxed on the way
    out — the course was stricter than the honor and the evidence already asked for stands."""
    enrollment = await _enrollment_in_course(db, course_id, user_id=actor.id)
    return await _detach(db, actor, enrollment, reason=None, action="COURSE_LEAVE", request=request)


async def remove_member(
    db: AsyncSession,
    actor: User,
    course_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: CourseMemberRemove,
    request: Request | None,
) -> None:
    course = await courses_service._get_course(db, course_id)
    await _require_course_author(db, actor, course)
    enrollment = await portfolio._get_enrollment(db, enrollment_id, lock=True)
    if enrollment.course_id != course.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa inscripción no es de este curso")
    await _detach(
        db, actor, enrollment, reason=payload.reason, action="COURSE_MEMBER_REMOVE", request=request
    )


async def _detach(
    db: AsyncSession,
    actor: User,
    enrollment: HonorEnrollment,
    *,
    reason: str | None,
    action: str,
    request: Request | None,
) -> EnrollmentDetail:
    portfolio._require_open(enrollment)  # rule 3 of A: certified or withdrawn is frozen
    course_id = enrollment.course_id
    enrollment.mode = CLUB
    enrollment.course_id = None
    enrollment.course_joined_at = None
    enrollment.course_removed_reason = reason
    await portfolio._touch(db, enrollment)
    record_audit(
        db,
        action=action,
        entity_type=ENTITY,
        entity_id=enrollment.id,
        actor=actor,
        details=reason,
        metadata={"course_id": str(course_id), "reason": reason},
        request=request,
    )
    await db.commit()
    return await portfolio._detail(db, actor, enrollment)


async def _enrollment_in_course(
    db: AsyncSession, course_id: uuid.UUID, *, user_id: uuid.UUID
) -> HonorEnrollment:
    stmt = (
        select(HonorEnrollment)
        .where(HonorEnrollment.user_id == user_id, HonorEnrollment.course_id == course_id)
        .with_for_update()
    )
    enrollment = (await db.execute(stmt)).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No estás inscrito en este curso")
    return enrollment


async def _require_course_author(db: AsyncSession, actor: User, course: Course) -> None:
    if course.instructor_id != actor.id and not is_master(actor):
        # 404 for the world, 403 for nobody: the roster of a course is the author's.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")


# ----------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------
async def my_joined(db: AsyncSession, actor: User) -> list[JoinedCourse]:
    stmt = (
        select(HonorEnrollment)
        .where(HonorEnrollment.user_id == actor.id, HonorEnrollment.course_id.is_not(None))
        .order_by(HonorEnrollment.updated_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    joined = []
    for enrollment in rows:
        course = await db.get(Course, enrollment.course_id)
        joined.append(
            JoinedCourse(
                **await courses_service._card_fields(db, course),
                enrollment_id=str(enrollment.id),
                enrollment_status=enrollment.status,
            )
        )
    return joined


async def members(
    db: AsyncSession, actor: User, course_id: uuid.UUID, limit: int, offset: int
) -> list[CourseMember]:
    """The roster of a course: name, club and the counters of A. Never an e-mail, a birth
    date or any way to reach the member outside the platform (spec §6)."""
    course = await courses_service._get_course(db, course_id)
    await _require_course_author(db, actor, course)
    stmt = (
        select(HonorEnrollment, User)
        .join(User, User.id == HonorEnrollment.user_id)
        .where(HonorEnrollment.course_id == course.id)
        .order_by(HonorEnrollment.course_joined_at, HonorEnrollment.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return []
    counters = {enrollment.id: {} for enrollment, _ in rows}
    by_status = (
        select(RequirementProgress.enrollment_id, RequirementProgress.status, func.count())
        .where(RequirementProgress.enrollment_id.in_(list(counters)))
        .group_by(RequirementProgress.enrollment_id, RequirementProgress.status)
    )
    for enrollment_id, progress_status, count in await db.execute(by_status):
        counters[enrollment_id][progress_status] = count

    club_ids = {member.organization_id for _, member in rows if member.organization_id}
    clubs = {}
    if club_ids:
        stmt = select(Organization).where(
            Organization.id.in_(club_ids), Organization.type == "club"
        )
        clubs = {row.id: row for row in (await db.execute(stmt)).scalars()}
    listed = []
    for enrollment, member in rows:
        club = clubs.get(member.organization_id)
        counts = counters[enrollment.id]
        listed.append(
            CourseMember(
                enrollment_id=str(enrollment.id),
                member=PersonRef(id=str(member.id), name=member.name),
                club=ClubRef(id=str(club.id), name=club.name) if club else None,
                status=enrollment.status,
                counters=Counters(
                    total=sum(counts.values()),
                    complete=counts.get(portfolio.COMPLETE, 0),
                    submitted=counts.get(portfolio.SUBMITTED, 0),
                    incomplete=counts.get(portfolio.INCOMPLETE, 0),
                ),
                joined_at=enrollment.course_joined_at,
                updated_at=enrollment.updated_at,
            )
        )
    return listed
