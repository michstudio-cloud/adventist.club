"""Bloque B · I2 — Courses: every rule and state transition lives here.

    course   DRAFT -> ZONE_REVIEW -> ASSOCIATION_REVIEW -> PUBLISHED -> ARCHIVED
             REJECT / REQUEST_CHANGES send it back to DRAFT

A course is the offering one verified instructor makes of ONE published version of an
honor in ONE language. Integrity rules (spec §3.9):
  1. The content of a course that is not DRAFT never changes; only the operational fields
     (`enrollment_open`, `capacity`) do. New content means a new version and a new review.
  2. The plan covers every requirement of the honor in the course's language, exactly once.
  3. The plan is never more lenient than the honor: a practical requirement stays EVIDENCE.
  4. Everything the instructor does beyond preparing a draft goes through the single gate
     `rbac.instructor_is_verified`, checked at that moment.

The review flow, its stages and its reviewers are the honors' ones (app/workflow.py), and
the history is written to `honor_reviews` with `course_id` set.

Each function commits its change together with its audit row.
"""
import json
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import violated_constraint
from app.models import Course, CourseLesson, CourseRequirement, Honor, HonorReview, Organization, User
from app.rbac import club_scope_paths, instructor_is_verified, is_master, org_in_review_scope
from app.schemas.course import (
    MAX_LESSON_BYTES,
    MAX_LESSONS_PER_COURSE,
    AssessmentCounts,
    CourseArchive,
    CourseCard,
    CourseCreate,
    CourseDetail,
    CourseOperation,
    CourseStaffDetail,
    CourseUpdate,
    CourseVersionCreate,
    LessonIn,
    LessonOrder,
    LessonOut,
    PaginatedCourses,
    PlanItemIn,
    PlanItemOut,
)
from app.schemas.honor import HonorReviewIn, ReviewOut
from app.schemas.portfolio import PersonRef
from app.security import utcnow
from app.services.audit import record_audit
from app.services.locales import match_locale
from app.services.portfolio import _honor_refs, _requirement_list
from app.workflow import (
    APPROVE,
    ARCHIVED,
    ASSOCIATION_REVIEW,
    ASSOCIATION_REVIEWERS,
    DRAFT,
    PUBLISHED,
    STAGE_REVIEWERS,
    ZONE_REVIEW,
    ZONE_REVIEWERS,
)

EXAM, REVIEW, EVIDENCE = "EXAM", "REVIEW", "EVIDENCE"
IN_REVIEW = (ZONE_REVIEW, ASSOCIATION_REVIEW)
# A course keeps its content open only while it is a draft.
EDITABLE = (DRAFT,)

ENTITY = "COURSE"
LIVE_COURSE_CONSTRAINTS = {
    "courses_instructor_honor_locale_published_key",
    "courses_instructor_honor_locale_draft_key",
}


# ----------------------------------------------------------------------------
# Lookups and guards
# ----------------------------------------------------------------------------
async def _get_course(db: AsyncSession, course_id: uuid.UUID, lock: bool = False) -> Course:
    stmt = select(Course).where(Course.id == course_id)
    if lock:
        # Serializes concurrent workflow transitions on the same course.
        stmt = stmt.with_for_update()
    course = (await db.execute(stmt)).scalar_one_or_none()
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")
    return course


def _is_author(actor: User | None, course: Course) -> bool:
    return actor is not None and course.instructor_id == actor.id


async def _is_reviewer(db: AsyncSession, actor: User | None, course: Course) -> bool:
    if actor is None or actor.role not in ZONE_REVIEWERS:
        return False
    return await org_in_review_scope(db, actor, course.org_scope_id)


async def _is_staff(db: AsyncSession, actor: User | None, course: Course) -> bool:
    """Who sees a course that is not published, and who sees the lesson content."""
    return _is_author(actor, course) or await _is_reviewer(db, actor, course)


async def _require_author(db: AsyncSession, actor: User, course: Course) -> None:
    if not _is_author(actor, course):
        # 404, not 403: an unpublished course does not exist for anyone else (as with honors).
        if not await _is_reviewer(db, actor, course):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo el instructor autor edita su curso")


def _require_draft(course: Course) -> None:
    """Rule 1."""
    if course.status not in EDITABLE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "El contenido de un curso enviado a revisión o publicado no se modifica;"
            " crea una versión nueva",
        )


async def _require_verified(db: AsyncSession, actor: User) -> None:
    """Rule 4: the gate is asked NOW, so a revoked or expired letter stops everything at once."""
    if not await instructor_is_verified(db, actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Necesitas la carta de tu iglesia autorizada para actuar como instructor",
        )


async def _enrolled_count(db: AsyncSession, course_id: uuid.UUID) -> int:
    """Seam for I3: enrolments in a course arrive with 009c_course_enrollment.sql. Until
    then no enrollment can point at a course, so the honest answer is zero."""
    return 0


# ----------------------------------------------------------------------------
# The plan, always read from the honor so it cannot drift
# ----------------------------------------------------------------------------
async def _honor_requirements(db: AsyncSession, course: Course) -> dict[int, object]:
    """The honor's requirement rows by position, in the course's language. Uses the same
    function the enrollment of Bloque A uses, so the two lists can never differ."""
    listed, _ = await _requirement_list(db, course.honor_id, course.locale)
    return dict(listed)


async def _plan_rows(db: AsyncSession, course_id: uuid.UUID) -> list[CourseRequirement]:
    stmt = (
        select(CourseRequirement)
        .where(CourseRequirement.course_id == course_id)
        .order_by(CourseRequirement.requirement_position)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _plan_out(db: AsyncSession, course: Course) -> list[PlanItemOut]:
    requirements = await _honor_requirements(db, course)
    return [
        PlanItemOut(
            position=row.requirement_position,
            assessment=row.assessment,
            draw_count=row.draw_count,
            guidance=row.guidance,
            is_practical=not getattr(
                requirements.get(row.requirement_position), "is_theoretical", True
            ),
            description=getattr(requirements.get(row.requirement_position), "description", None),
        )
        for row in await _plan_rows(db, course.id)
    ]


# ----------------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------------
def _lesson_out(lesson: CourseLesson, *, with_blocks: bool) -> LessonOut:
    blocks = list(lesson.blocks or [])
    return LessonOut(
        id=str(lesson.id),
        position=lesson.position,
        title=lesson.title,
        requirement_positions=list(lesson.requirement_positions or []),
        block_count=len(blocks),
        blocks=blocks if with_blocks else [],
    )


async def _lessons(db: AsyncSession, course_id: uuid.UUID) -> list[CourseLesson]:
    stmt = (
        select(CourseLesson)
        .where(CourseLesson.course_id == course_id)
        .order_by(CourseLesson.position, CourseLesson.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _card_fields(db: AsyncSession, course: Course) -> dict:
    instructor = await db.get(User, course.instructor_id)
    honor_ref = (await _honor_refs(db, [_HonorRefRow(course)]))[course.id]
    approved_by = None
    if course.approved_association_org_id:
        organization = await db.get(Organization, course.approved_association_org_id)
        approved_by = organization.name if organization else None
    counts = {EXAM: 0, REVIEW: 0, EVIDENCE: 0}
    for row in await _plan_rows(db, course.id):
        counts[row.assessment] = counts.get(row.assessment, 0) + 1
    enrolled = await _enrolled_count(db, course.id)
    lesson_count = (
        await db.execute(select(func.count()).where(CourseLesson.course_id == course.id))
    ).scalar_one()
    return {
        "id": str(course.id),
        "title": course.title,
        "summary": course.summary,
        "cover_url": course.cover_url,
        "locale": course.locale,
        "status": course.status,
        "version": course.version,
        "instructor": PersonRef(id=str(instructor.id), name=instructor.name),
        "honor": honor_ref,
        "approved_by": approved_by,
        "lesson_count": lesson_count,
        "assessment_counts": AssessmentCounts(**counts),
        "enrollment_open": course.enrollment_open,
        "capacity": course.capacity,
        "enrolled_count": enrolled,
        "seats_left": None if course.capacity is None else max(course.capacity - enrolled, 0),
        "instructor_verified": await instructor_is_verified(db, instructor),
        "published_at": course.published_at,
    }


class _HonorRefRow:
    """`portfolio._honor_refs` names an honor in the row's language and keys by `id`.
    A course answers both questions, so it borrows the function through this adapter."""

    def __init__(self, course: Course):
        self.id = course.id
        self.honor_id = course.honor_id
        self.locale = course.locale


async def _detail(db: AsyncSession, actor: User | None, course: Course, *, staff: bool) -> CourseDetail:
    fields = {
        **await _card_fields(db, course),
        # I3: the enrolled member sees the blocks too (`can_view_enrollment`).
        "lessons": [_lesson_out(row, with_blocks=staff) for row in await _lessons(db, course.id)],
        "plan": await _plan_out(db, course),
        "archived_by_authority": course.archived_by_authority,
        "archive_reason": course.archive_reason,
        "previous_version_id": str(course.previous_version_id) if course.previous_version_id else None,
        "changes_description": course.changes_description,
        "updated_at": course.updated_at,
    }
    if not staff:
        return CourseDetail(**fields)
    reviews = (
        await db.execute(
            select(HonorReview)
            .where(HonorReview.course_id == course.id)
            .order_by(HonorReview.reviewed_at)
        )
    ).scalars().all()
    fields["review_history"] = [
        ReviewOut(
            id=str(row.id),
            reviewer_id=str(row.reviewer_id) if row.reviewer_id else None,
            reviewer_name=row.reviewer_name,
            reviewer_role=row.reviewer_role,
            action=row.action,
            comments=row.comments,
            reviewed_at=row.reviewed_at,
        )
        for row in reviews
    ]
    return CourseStaffDetail(**fields)


# ----------------------------------------------------------------------------
# Authoring
# ----------------------------------------------------------------------------
async def create(
    db: AsyncSession, actor: User, payload: CourseCreate, request: Request | None
) -> CourseStaffDetail:
    """201 DRAFT with the plan preloaded from the honor. Preparing a draft does NOT need the
    verification gate: the instructor works while the letter is being validated."""
    if actor.organization_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Elige primero tu Asociación o club")
    honor = await db.get(Honor, payload.honor_id)
    if honor is None or honor.status != PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Especialidad no encontrada")

    requirements, resolved = await _requirement_list(db, honor.id, payload.locale)
    if not requirements or match_locale([resolved], payload.locale) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La especialidad no tiene requisitos en ese idioma"
        )

    now = utcnow()
    course = Course(
        id=uuid.uuid4(),
        honor_id=honor.id,
        instructor_id=actor.id,
        org_scope_id=actor.organization_id,
        locale=resolved,
        title=payload.title.strip(),
        summary=payload.summary,
        cover_url=payload.cover_url,
        status=DRAFT,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(course)
    db.add_all(
        # The honor's own mark decides the starting point; the instructor may only tighten it.
        CourseRequirement(
            course_id=course.id,
            requirement_position=position,
            assessment=REVIEW if requirement.is_theoretical else EVIDENCE,
            draw_count=0,
        )
        for position, requirement in requirements
    )
    record_course_audit(
        db, "CREATE", course, actor, request,
        metadata={"honor_id": str(honor.id), "locale": resolved, "requirements": len(requirements)},
    )
    await _commit_unique(db, "Ya tienes un curso de esta especialidad en ese idioma")
    return await _detail(db, actor, course, staff=True)


async def _commit_unique(db: AsyncSession, conflict_detail: str) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) not in LIVE_COURSE_CONSTRAINTS:
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, conflict_detail) from exc


def record_course_audit(
    db: AsyncSession,
    action: str,
    course: Course,
    actor: User,
    request: Request | None,
    *,
    details: str | None = None,
    metadata: dict | None = None,
) -> None:
    record_audit(
        db,
        action=action,
        entity_type=ENTITY,
        entity_id=course.id,
        actor=actor,
        details=details,
        metadata=metadata,
        request=request,
    )


async def update(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: CourseUpdate, request: Request | None
) -> CourseStaffDetail:
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    _require_draft(course)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("title") is not None:
        course.title = changes["title"].strip()
    for column in ("summary", "cover_url"):
        if column in changes:
            setattr(course, column, changes[column])
    course.updated_at = utcnow()
    record_course_audit(db, "UPDATE", course, actor, request, metadata={"fields": sorted(changes)})
    await db.commit()
    return await _detail(db, actor, course, staff=True)


# ----------------------------------------------------------------------------
# Lessons
# ----------------------------------------------------------------------------
def _stage_blocks(payload: LessonIn) -> list[dict]:
    """Every block keeps a short stable id, assigned here when the editor did not send one."""
    blocks = []
    for block in payload.blocks:
        stored = block.model_dump()
        stored["id"] = stored.get("id") or uuid.uuid4().hex[:8]
        blocks.append(stored)
    if len(json.dumps(blocks, ensure_ascii=False).encode()) > MAX_LESSON_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"La lección supera los {MAX_LESSON_BYTES // 1024} KB",
        )
    return blocks


async def _get_lesson(db: AsyncSession, course: Course, lesson_id: uuid.UUID) -> CourseLesson:
    stmt = select(CourseLesson).where(
        CourseLesson.id == lesson_id, CourseLesson.course_id == course.id
    )
    lesson = (await db.execute(stmt)).scalar_one_or_none()
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lección no encontrada")
    return lesson


async def add_lesson(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: LessonIn, request: Request | None
) -> LessonOut:
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    _require_draft(course)
    blocks = _stage_blocks(payload)

    lessons = await _lessons(db, course.id)
    if len(lessons) >= MAX_LESSONS_PER_COURSE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Un curso admite como máximo {MAX_LESSONS_PER_COURSE} lecciones"
        )
    now = utcnow()
    lesson = CourseLesson(
        id=uuid.uuid4(),
        course_id=course.id,
        position=max((row.position for row in lessons), default=0) + 1,
        title=payload.title.strip(),
        requirement_positions=payload.requirement_positions,
        blocks=blocks,
        created_at=now,
        updated_at=now,
    )
    db.add(lesson)
    course.updated_at = now
    record_course_audit(
        db, "UPDATE", course, actor, request,
        details=f"Lección añadida: {lesson.title}",
        metadata={"lesson_id": str(lesson.id), "blocks": len(blocks)},
    )
    await db.commit()
    return _lesson_out(lesson, with_blocks=True)


async def update_lesson(
    db: AsyncSession,
    actor: User,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonIn,
    request: Request | None,
) -> LessonOut:
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    _require_draft(course)
    lesson = await _get_lesson(db, course, lesson_id)

    lesson.blocks = _stage_blocks(payload)
    lesson.title = payload.title.strip()
    lesson.requirement_positions = payload.requirement_positions
    lesson.updated_at = course.updated_at = utcnow()
    record_course_audit(
        db, "UPDATE", course, actor, request,
        details=f"Lección editada: {lesson.title}", metadata={"lesson_id": str(lesson.id)},
    )
    await db.commit()
    return _lesson_out(lesson, with_blocks=True)


async def delete_lesson(
    db: AsyncSession, actor: User, course_id: uuid.UUID, lesson_id: uuid.UUID, request: Request | None
) -> None:
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    _require_draft(course)
    lesson = await _get_lesson(db, course, lesson_id)
    await db.delete(lesson)
    course.updated_at = utcnow()
    record_course_audit(
        db, "UPDATE", course, actor, request,
        details=f"Lección eliminada: {lesson.title}", metadata={"lesson_id": str(lesson.id)},
    )
    await db.commit()


async def reorder_lessons(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: LessonOrder, request: Request | None
) -> CourseStaffDetail:
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    _require_draft(course)
    lessons = {lesson.id: lesson for lesson in await _lessons(db, course.id)}
    if set(payload.lesson_ids) != set(lessons) or len(payload.lesson_ids) != len(lessons):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "La lista debe contener exactamente las lecciones del curso, una vez cada una",
        )
    for position, lesson_id in enumerate(payload.lesson_ids, start=1):
        lessons[lesson_id].position = position
    course.updated_at = utcnow()
    record_course_audit(db, "UPDATE", course, actor, request, details="Lecciones reordenadas")
    await db.commit()
    return await _detail(db, actor, course, staff=True)


# ----------------------------------------------------------------------------
# The evaluation plan
# ----------------------------------------------------------------------------
async def set_plan(
    db: AsyncSession, actor: User, course_id: uuid.UUID, items: list[PlanItemIn], request: Request | None
) -> CourseStaffDetail:
    """The whole plan at once: one entry per requirement of the honor, never more lenient."""
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    _require_draft(course)

    requirements = await _honor_requirements(db, course)
    sent = [item.position for item in items]
    if sorted(sent) != sorted(requirements) or len(set(sent)) != len(sent):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "El plan debe declarar cada requisito de la especialidad exactamente una vez",
        )
    relaxed = [
        item.position
        for item in items
        if not requirements[item.position].is_theoretical and item.assessment != EVIDENCE
    ]
    if relaxed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un requisito práctico de la especialidad exige evidencia; revisa los requisitos "
            + ", ".join(str(position) for position in sorted(relaxed)),
        )

    await db.execute(delete(CourseRequirement).where(CourseRequirement.course_id == course.id))
    db.add_all(
        CourseRequirement(
            course_id=course.id,
            requirement_position=item.position,
            assessment=item.assessment,
            draw_count=0,  # only an EXAM requirement draws questions (I4)
            guidance=item.guidance,
        )
        for item in items
    )
    course.updated_at = utcnow()
    record_course_audit(
        db, "UPDATE", course, actor, request, details="Plan de evaluación actualizado",
        metadata={"plan": {str(item.position): item.assessment for item in items}},
    )
    await db.commit()
    return await _detail(db, actor, course, staff=True)


# ----------------------------------------------------------------------------
# Review workflow
# ----------------------------------------------------------------------------
async def _missing_before_submit(db: AsyncSession, course: Course) -> list[str]:
    missing = []
    honor = await db.get(Honor, course.honor_id)
    if honor is None or honor.status != PUBLISHED:
        missing.append("la especialidad ya no está publicada")
    lessons = await _lessons(db, course.id)
    if not any(lesson.blocks for lesson in lessons):
        missing.append("hace falta al menos una lección con contenido")

    requirements = await _honor_requirements(db, course)
    plan = {row.requirement_position: row for row in await _plan_rows(db, course.id)}
    if set(plan) != set(requirements):
        missing.append("el plan de evaluación no cubre todos los requisitos")
    else:
        relaxed = [
            position
            for position, requirement in requirements.items()
            if not requirement.is_theoretical and plan[position].assessment != EVIDENCE
        ]
        if relaxed:
            missing.append(
                "hay requisitos prácticos sin evidencia: "
                + ", ".join(str(position) for position in sorted(relaxed))
            )
    return missing


async def submit(
    db: AsyncSession, actor: User, course_id: uuid.UUID, request: Request | None
) -> CourseStaffDetail:
    """DRAFT -> ZONE_REVIEW. From here on the instructor must be verified."""
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    await _require_verified(db, actor)
    if course.status != DRAFT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Solo un borrador se envía a revisión")
    missing = await _missing_before_submit(db, course)
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Antes de enviar a revisión: " + "; ".join(missing)
        )

    course.status = ZONE_REVIEW
    course.updated_at = utcnow()
    record_course_audit(db, "SUBMIT", course, actor, request, details=f"Curso enviado: {course.title}")
    await db.commit()
    return await _detail(db, actor, course, staff=True)


async def review(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: HonorReviewIn, request: Request | None
) -> CourseStaffDetail:
    """Same contract as POST /honors/{id}/review: APPROVE walks the two steps, REJECT and
    REQUEST_CHANGES send the course back to DRAFT with a comment."""
    course = await _get_course(db, course_id, lock=True)
    if actor.role not in STAGE_REVIEWERS.get(course.status, ()):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No revisas cursos en la etapa {course.status}"
        )
    if not await org_in_review_scope(db, actor, course.org_scope_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Este curso está fuera de tu alcance")
    if course.instructor_id == actor.id and not is_master(actor):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes revisar tu propio curso")

    now = utcnow()
    previous = course.status
    if payload.action == APPROVE:
        if course.status == ZONE_REVIEW:
            course.status = ASSOCIATION_REVIEW
            course.approved_zone_org_id = actor.organization_id
        else:
            course.approved_association_org_id = actor.organization_id
            await _publish(db, course, now)
    else:
        course.status = DRAFT
    course.updated_at = now

    # The history is append-only and shared with the honors': `course_id` tells them apart.
    db.add(
        HonorReview(
            id=uuid.uuid4(),
            honor_id=course.honor_id,
            course_id=course.id,
            reviewer_id=actor.id,
            reviewer_name=actor.name,
            reviewer_role=actor.role,
            action=payload.action,
            comments=payload.comments,
            reviewed_at=now,
        )
    )
    record_course_audit(
        db, payload.action, course, actor, request,
        details=f"{payload.action} curso: {course.title}",
        metadata={"from": previous, "to": course.status, "comments": payload.comments},
    )
    await _commit_unique(db, "El instructor ya tiene un curso publicado de esta especialidad")
    return await _detail(db, actor, course, staff=True)


async def _publish(db: AsyncSession, course: Course, now) -> None:
    """The previous version is archived BEFORE this one is published: the partial unique
    index is not deferrable, so there is never a moment with two published versions."""
    if course.previous_version_id:
        previous = await _get_course(db, course.previous_version_id, lock=True)
        if previous.status == PUBLISHED:
            previous.status = ARCHIVED
            previous.archived_at = now
            previous.enrollment_open = False
            previous.updated_at = now
            await db.flush()
    course.status = PUBLISHED
    course.published_at = now


async def create_version(
    db: AsyncSession,
    actor: User,
    course_id: uuid.UUID,
    payload: CourseVersionCreate,
    request: Request | None,
) -> CourseStaffDetail:
    """A new DRAFT copying lessons and plan. The published one stays live until this one is
    published, so nobody studying it loses their material."""
    original = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, original)
    if original.status != PUBLISHED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Solo se versiona un curso publicado")

    honor = await _version_target_honor(db, original, payload.honor_id)
    requirements, resolved = await _requirement_list(db, honor.id, original.locale)
    if not requirements or match_locale([resolved], original.locale) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La especialidad no tiene requisitos en ese idioma"
        )
    # Everything is read BEFORE the new row is staged: a query after `db.add` would autoflush
    # the INSERT and the unique index would raise outside the handler that turns it into a 409.
    source_lessons = await _lessons(db, original.id)
    previous_plan = {row.requirement_position: row for row in await _plan_rows(db, original.id)}
    if await _version_in_preparation(db, original, honor.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya tienes una versión de este curso en preparación")

    now = utcnow()
    course = Course(
        id=uuid.uuid4(),
        honor_id=honor.id,
        instructor_id=original.instructor_id,
        org_scope_id=original.org_scope_id,
        locale=resolved,
        title=original.title,
        summary=original.summary,
        cover_url=original.cover_url,
        status=DRAFT,
        version=original.version + 1,
        previous_version_id=original.id,
        changes_description=payload.changes_description,
        enrollment_open=original.enrollment_open,
        capacity=original.capacity,
        created_at=now,
        updated_at=now,
    )
    db.add(course)
    for lesson in source_lessons:
        db.add(
            CourseLesson(
                id=uuid.uuid4(),
                course_id=course.id,
                position=lesson.position,
                title=lesson.title,
                requirement_positions=list(lesson.requirement_positions or []),
                blocks=list(lesson.blocks or []),
                created_at=now,
                updated_at=now,
            )
        )
    # Positions that no longer exist in the honor are dropped; new ones start from the
    # honor's own mark, exactly as when the course was created.
    for position, requirement in requirements:
        copied = previous_plan.get(position)
        assessment = copied.assessment if copied else (REVIEW if requirement.is_theoretical else EVIDENCE)
        if not requirement.is_theoretical:
            assessment = EVIDENCE  # the new honor version may have tightened it
        db.add(
            CourseRequirement(
                course_id=course.id,
                requirement_position=position,
                assessment=assessment,
                draw_count=copied.draw_count if copied and assessment == EXAM else 0,
                guidance=copied.guidance if copied else None,
            )
        )
    record_course_audit(
        db, "COURSE_VERSION", course, actor, request,
        details=f"Versión {course.version} de: {course.title}",
        metadata={"previous_version_id": str(original.id), "honor_id": str(honor.id),
                  "changes": payload.changes_description},
    )
    await _commit_unique(db, "Ya tienes una versión de este curso en preparación")
    return await _detail(db, actor, course, staff=True)


async def _version_in_preparation(db: AsyncSession, course: Course, honor_id: uuid.UUID) -> bool:
    """The partial unique index is the real guard; this only turns the common case into a
    clear 409 before anything is staged."""
    stmt = select(Course.id).where(
        Course.instructor_id == course.instructor_id,
        Course.honor_id == honor_id,
        Course.locale == course.locale,
        Course.status.in_((DRAFT, *IN_REVIEW)),
    )
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _version_target_honor(db: AsyncSession, course: Course, honor_id: uuid.UUID | None) -> Honor:
    """The same honor, or a newer published version of it: never a different specialty."""
    if honor_id is None or honor_id == course.honor_id:
        return await db.get(Honor, course.honor_id)
    honor = await db.get(Honor, honor_id)
    if honor is None or honor.status != PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Especialidad no encontrada")
    ancestor, hops = honor.previous_version_id, 0
    while ancestor is not None and hops < 10:
        if ancestor == course.honor_id:
            return honor
        ancestor = (await db.get(Honor, ancestor)).previous_version_id
        hops += 1
    raise HTTPException(
        status.HTTP_409_CONFLICT, "Esa especialidad no es una versión nueva de la del curso"
    )


# ----------------------------------------------------------------------------
# Archiving and operation
# ----------------------------------------------------------------------------
async def archive(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: CourseArchive | None, request: Request | None
) -> None:
    """The instructor retires their own course; a reviewer in scope withdraws it, always with
    a reason, and then the course is closed for its members too (`archived_by_authority`)."""
    course = await _get_course(db, course_id, lock=True)
    reason = payload.reason if payload else None
    by_authority = not _is_author(actor, course)
    if by_authority:
        if not await _is_reviewer(db, actor, course):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")
        if not reason:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Retirar un curso exige explicar el motivo"
            )
    if course.status == ARCHIVED:
        return

    now = utcnow()
    previous = course.status
    course.status = ARCHIVED
    course.archived_at = now
    course.enrollment_open = False
    course.archived_by_authority = by_authority
    course.archive_reason = reason
    course.updated_at = now
    record_course_audit(
        db, "ARCHIVE", course, actor, request, details=reason,
        metadata={"from": previous, "by_authority": by_authority, "reason": reason},
    )
    await db.commit()


async def set_operation(
    db: AsyncSession, actor: User, course_id: uuid.UUID, payload: CourseOperation, request: Request | None
) -> CourseDetail:
    """Capacity and open/closed enrolment: the only content-free levers on a published course."""
    course = await _get_course(db, course_id, lock=True)
    await _require_author(db, actor, course)
    await _require_verified(db, actor)
    if course.status != PUBLISHED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Solo un curso publicado tiene ajustes de operación"
        )
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("capacity") is not None:
        enrolled = await _enrolled_count(db, course.id)
        if changes["capacity"] < enrolled:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"El curso ya tiene {enrolled} inscripciones activas"
            )
    for column in ("enrollment_open", "capacity"):
        if column in changes:
            setattr(course, column, changes[column])
    course.updated_at = utcnow()
    record_course_audit(
        db, "COURSE_OPERATION_UPDATE", course, actor, request, metadata={"fields": sorted(changes)}
    )
    await db.commit()
    return await _detail(db, actor, course, staff=True)


# ----------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------
async def get_detail(
    db: AsyncSession, actor: User | None, course_id: uuid.UUID, *, staff_view: bool = False
) -> CourseDetail:
    course = await _get_course(db, course_id)
    is_staff = await _is_staff(db, actor, course)
    if staff_view and not is_staff:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")
    if course.status != PUBLISHED and not is_staff:
        # 404 rather than 403: do not confirm that an unpublished course exists.
        # I3: an enrolled member also sees their (possibly archived) course.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Curso no encontrado")
    return await _detail(db, actor, course, staff=is_staff)


async def discover(
    db: AsyncSession, honor_id: uuid.UUID | None, locale: str | None, limit: int, offset: int
) -> list[CourseCard]:
    """The shop window: published courses, open for enrolment, of verified instructors.
    Global by design (D3a); the card says which association approved the course."""
    conditions = [Course.status == PUBLISHED, Course.enrollment_open.is_(True)]
    if honor_id is not None:
        conditions.append(Course.honor_id == honor_id)
    rows = (
        await db.execute(
            select(Course)
            .join(Honor, Honor.id == Course.honor_id)
            .where(*conditions, Honor.status == PUBLISHED)
            .order_by(Course.published_at.desc(), Course.id)
            .limit(limit * 2)  # room to drop the courses of instructors who lost their letter
            .offset(offset)
        )
    ).scalars().all()

    cards, verified = [], {}
    for course in rows:
        if course.instructor_id not in verified:
            instructor = await db.get(User, course.instructor_id)
            verified[course.instructor_id] = await instructor_is_verified(db, instructor)
        if not verified[course.instructor_id]:
            continue
        cards.append(CourseCard(**await _card_fields(db, course)))
        if len(cards) >= limit:
            break
    if locale:
        # The interface language first; the rest still show (a course is global).
        cards.sort(key=lambda card: match_locale([card.locale], locale) is None)
    return cards


async def my_created(
    db: AsyncSession, actor: User, status_filter: str | None, limit: int, offset: int
) -> PaginatedCourses:
    conditions = [Course.instructor_id == actor.id]
    if status_filter:
        conditions.append(Course.status == status_filter)
    total = (await db.execute(select(func.count()).where(*conditions))).scalar_one()
    rows = (
        await db.execute(
            select(Course)
            .where(*conditions)
            .order_by(Course.updated_at.desc(), Course.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    items = [CourseCard(**await _card_fields(db, course)) for course in rows]
    return PaginatedCourses(
        items=items, total=total, limit=limit, offset=offset, has_more=offset + len(items) < total
    )


async def pending_reviews(db: AsyncSession, actor: User, limit: int) -> list[CourseCard]:
    """Courses waiting for the caller's review level, inside the caller's scope."""
    if actor.role not in ASSOCIATION_REVIEWERS:
        stages = [ZONE_REVIEW]
    elif is_master(actor):
        stages = [ZONE_REVIEW, ASSOCIATION_REVIEW]
    else:
        stages = [ASSOCIATION_REVIEW]

    conditions = [Course.status.in_(stages), Course.instructor_id != actor.id]
    if not is_master(actor):
        paths = await club_scope_paths(db, actor)
        if not paths:
            return []
        in_scope = select(Organization.id).where(
            or_(*[Organization.path.op("<@")(path) for path in paths])
        )
        conditions.append(Course.org_scope_id.in_(in_scope))
    rows = (
        await db.execute(
            select(Course).where(*conditions).order_by(Course.updated_at, Course.id).limit(limit)
        )
    ).scalars().all()
    return [CourseCard(**await _card_fields(db, course)) for course in rows]
