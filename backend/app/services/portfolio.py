"""Portfolio (Bloque A): every rule and state transition lives here.

    enrollment   IN_PROGRESS -> READY -> CERTIFIED          (WITHDRAWN from the first two)
    requirement  PENDING -> SUBMITTED -> COMPLETE | INCOMPLETE ;  INCOMPLETE -> SUBMITTED
    evidence     PENDING_UPLOAD -> ACTIVE -> REMOVED

Integrity rules (spec §1):
  1. COMPLETE on a practical requirement needs at least one ACTIVE evidence.
  2. After every verdict: all COMPLETE => READY, otherwise back to IN_PROGRESS.
  3. CERTIFIED and WITHDRAWN freeze the enrollment.
  4. A COMPLETE requirement is closed to the member (status, note, evidence); only a
     reviewer reopens it, with an INCOMPLETE verdict.
  5. Nobody reviews or certifies their own enrollment (app/rbac.py).

Each function commits its own change together with its audit row.
"""
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.certificates.render import TemplateError, load_template
from app.config import settings
from app.db import violated_constraint
from app.models import (
    Application,
    Certificate,
    CertificateTemplate,
    Course,
    CourseRequirement,
    Evidence,
    Honor,
    HonorEnrollment,
    HonorRequirement,
    HonorTranslation,
    Organization,
    RequirementProgress,
    User,
)
from app.rbac import (
    can_issue,
    can_review,
    can_view_enrollment,
    can_view_portfolio,
    club_staff_in_good_standing,
    instructor_is_verified,
    is_course_instructor,
    is_master,
    member_club,
)
from app.schemas.portfolio import (
    CertificateIssue,
    CertificateOut,
    ClubRef,
    Counters,
    CourseRef,
    EnrollmentCreate,
    EnrollmentDetail,
    EnrollmentSummary,
    EvidenceCreate,
    EvidenceOut,
    EvidenceUpload,
    HonorRef,
    Permissions,
    PersonRef,
    PortfolioOut,
    PortfolioUser,
    QueueReady,
    QueueRequirement,
    RequirementOut,
    RequirementUpdate,
    ReviewIn,
    SignedUrl,
    UploadTarget,
)
from app.security import CLUB_APPROVED, CLUB_DIRECTOR, utcnow
from app.services import private_storage
from app.services.audit import record_audit
from app.services.certificates import (
    get_or_create_club,
    get_or_create_template,
    issue_certificate,
    resolve_issuer_organization,
)
from app.services.locales import SOURCE_LOCALE, best_locale, match_locale

IN_PROGRESS, READY, CERTIFIED, WITHDRAWN = "IN_PROGRESS", "READY", "CERTIFIED", "WITHDRAWN"
FROZEN = (CERTIFIED, WITHDRAWN)
PENDING, SUBMITTED, COMPLETE, INCOMPLETE = "PENDING", "SUBMITTED", "COMPLETE", "INCOMPLETE"
PENDING_UPLOAD, ACTIVE, REMOVED = "PENDING_UPLOAD", "ACTIVE", "REMOVED"
PUBLISHED = "PUBLISHED"

EVIDENCE_MAX_PER_REQUIREMENT = 6
DEFAULT_CERTIFICATE_TEMPLATE = "especialidad-basica"
ACTIVE_ENROLLMENT_CONSTRAINT = "honor_enrollments_user_honor_active_key"

ENROLLMENT, PROGRESS, EVIDENCE, CERTIFICATE = "ENROLLMENT", "REQUIREMENT_PROGRESS", "EVIDENCE", "CERTIFICATE"


# ----------------------------------------------------------------------------
# Lookups and guards
# ----------------------------------------------------------------------------
async def _get_enrollment(db: AsyncSession, enrollment_id: uuid.UUID, lock: bool = False) -> HonorEnrollment:
    stmt = select(HonorEnrollment).where(HonorEnrollment.id == enrollment_id)
    if lock:
        # Serializes verdicts, evidence and issuance on the same enrollment.
        stmt = stmt.with_for_update()
    enrollment = (await db.execute(stmt)).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inscripción no encontrada")
    return enrollment


async def _get_progress(db: AsyncSession, enrollment: HonorEnrollment, position: int) -> RequirementProgress:
    stmt = select(RequirementProgress).where(
        RequirementProgress.enrollment_id == enrollment.id,
        RequirementProgress.requirement_position == position,
    )
    progress = (await db.execute(stmt)).scalar_one_or_none()
    if progress is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisito no encontrado en esta inscripción")
    return progress


async def _get_evidence(
    db: AsyncSession, evidence_id: uuid.UUID, lock: bool = False
) -> tuple[Evidence, RequirementProgress, HonorEnrollment]:
    evidence = await db.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidencia no encontrada")
    progress = await db.get(RequirementProgress, evidence.progress_id)
    return evidence, progress, await _get_enrollment(db, progress.enrollment_id, lock=lock)


def _require_owner(actor: User, enrollment: HonorEnrollment) -> None:
    if enrollment.user_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo quien cursa la especialidad puede hacer esto")


def _require_open(enrollment: HonorEnrollment) -> None:
    """Rule 3."""
    if enrollment.status in FROZEN:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La inscripción está certificada o retirada y ya no admite cambios",
        )


def _require_open_requirement(progress: RequirementProgress) -> None:
    """Rule 4."""
    if progress.status == COMPLETE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El requisito ya está completo; solo un revisor puede reabrirlo",
        )


def _require_private_storage() -> None:
    if not settings.private_storage_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)


async def _require_viewer(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> None:
    # `can_view_enrollment` (Bloque B §2.2), not `can_view_portfolio`: the instructor of the
    # course reads THIS enrollment and its evidence, never the rest of the portfolio.
    if not await can_view_enrollment(db, actor, enrollment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver este portafolio")


async def _touch(db: AsyncSession, enrollment: HonorEnrollment) -> None:
    """Every write re-reads the member's current club, so `club_id` is right when it freezes."""
    club = await member_club(db, await db.get(User, enrollment.user_id))
    enrollment.club_id = club.id if club else None
    enrollment.updated_at = utcnow()


async def _active_evidence_count(db: AsyncSession, progress_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(Evidence.progress_id == progress_id, Evidence.status == ACTIVE)
    return (await db.execute(stmt)).scalar_one()


# Cached once per process: 007 either is applied or is not, and it cannot be
# un-applied while the service runs. It lets block E ship on a database that
# does not carry the portfolio yet (spec E §5.3).
_portfolio_installed: bool | None = None


async def on_club_changed(
    db: AsyncSession, user_id: uuid.UUID, new_club_id: uuid.UUID | None
) -> None:
    """
    The member changed club (or left): move their OPEN enrollments so the new
    club's review queue sees them at once instead of waiting for the next write.
    CERTIFIED and WITHDRAWN are frozen with the club that closed them.

    Called by app/services/memberships.py inside the same transaction; the
    jurisdiction itself already followed the member through `users.organization_id`.
    """
    global _portfolio_installed
    if _portfolio_installed is None:
        _portfolio_installed = bool(
            await db.scalar(text("SELECT to_regclass('public.honor_enrollments')"))
        )
    if not _portfolio_installed:
        return
    await db.execute(
        update(HonorEnrollment)
        .where(
            HonorEnrollment.user_id == user_id,
            HonorEnrollment.status.in_((IN_PROGRESS, READY)),
        )
        .values(club_id=new_club_id, updated_at=utcnow())
    )


# ----------------------------------------------------------------------------
# Requirement texts
# ----------------------------------------------------------------------------
async def _requirement_list(
    db: AsyncSession, honor_id: uuid.UUID, requested_locale: str | None
) -> tuple[list[tuple[int, HonorRequirement]], str]:
    """The honor's requirement list in the best language for the request (same fallback as
    the catalogue), keyed by position. Every stored row is a top-level requirement."""
    stored = (
        await db.execute(
            select(HonorRequirement.locale).where(HonorRequirement.honor_id == honor_id).distinct()
        )
    ).scalars().all()
    if not stored:
        return [], SOURCE_LOCALE
    locale = best_locale(stored, requested_locale)
    rows = (
        await db.execute(
            select(HonorRequirement)
            .where(HonorRequirement.honor_id == honor_id, HonorRequirement.locale == locale)
            .order_by(HonorRequirement.position, HonorRequirement.created_at)
        )
    ).scalars().all()
    positions = [row.position for row in rows]
    if len(set(positions)) != len(positions):
        # A hand-written list may repeat a position: fall back to the order shown.
        positions = list(range(1, len(rows) + 1))
    return list(zip(positions, rows)), locale


# ----------------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------------
def _evidence_out(evidence: Evidence) -> EvidenceOut:
    return EvidenceOut(
        id=str(evidence.id),
        kind=evidence.kind,
        status=evidence.status,
        content_type=evidence.content_type,
        size_bytes=evidence.size_bytes,
        taken_on=evidence.taken_on,
        place=evidence.place,
        caption=evidence.caption,
        created_at=evidence.created_at,
    )


async def _honor_refs(
    db: AsyncSession, enrollments: list[HonorEnrollment]
) -> dict[uuid.UUID, HonorRef]:
    """Honor of each enrollment, named in the enrollment's language when a translation exists."""
    honor_ids = {e.honor_id for e in enrollments}
    if not honor_ids:
        return {}
    honors = {
        h.id: h for h in (await db.execute(select(Honor).where(Honor.id.in_(honor_ids)))).scalars()
    }
    translations: dict[uuid.UUID, dict[str, str]] = {}
    stmt = select(HonorTranslation).where(HonorTranslation.honor_id.in_(honor_ids))
    for row in (await db.execute(stmt)).scalars():
        translations.setdefault(row.honor_id, {})[row.locale] = row.name
    refs = {}
    for enrollment in enrollments:
        honor = honors[enrollment.honor_id]
        names = translations.get(honor.id, {})
        is_source = enrollment.locale.lower().split("-")[0] == SOURCE_LOCALE
        translated = None if is_source else names.get(match_locale(list(names), enrollment.locale))
        refs[enrollment.id] = HonorRef(
            id=str(honor.id), name=translated or honor.name, slug=honor.slug, image_url=honor.image_url
        )
    return refs


async def _certificates_out(db: AsyncSession, certificates: list[Certificate]) -> list[CertificateOut]:
    template_ids = {c.template_id for c in certificates}
    slugs = {}
    if template_ids:
        stmt = select(CertificateTemplate).where(CertificateTemplate.id.in_(template_ids))
        # Only templates created from a server SVG template are named after its slug.
        slugs = {t.id: t.name for t in (await db.execute(stmt)).scalars() if t.supports_svg}
    return [
        CertificateOut(
            id=str(c.id),
            certificate_no=c.certificate_no,
            recipient_name=c.recipient_name,
            honor_id=str(c.honor_id) if c.honor_id else None,
            honor_name_snapshot=c.honor_name_snapshot,
            club_name_snapshot=c.club_name_snapshot,
            issued_date=c.issued_date,
            place=c.place,
            instructor_name=c.instructor_name,
            director_name=c.director_name,
            status=c.status,
            certificate_hash=c.certificate_hash,
            template=slugs.get(c.template_id),
            user_id=str(c.user_id) if c.user_id else None,
            enrollment_id=str(c.enrollment_id) if c.enrollment_id else None,
            issued_by_id=str(c.issued_by_id) if c.issued_by_id else None,
            issued_role=c.issued_role,
        )
        for c in certificates
    ]


async def _summaries(db: AsyncSession, enrollments: list[HonorEnrollment]) -> list[EnrollmentSummary]:
    if not enrollments:
        return []
    ids = [e.id for e in enrollments]
    users = {
        u.id: u
        for u in (
            await db.execute(select(User).where(User.id.in_({e.user_id for e in enrollments})))
        ).scalars()
    }
    # Open enrollments show the member's club of today; frozen ones the club they kept.
    club_of = {
        e.id: e.club_id if e.status in FROZEN else users[e.user_id].organization_id
        for e in enrollments
    }
    org_ids = {org_id for org_id in club_of.values() if org_id}
    clubs = {}
    if org_ids:
        stmt = select(Organization).where(Organization.id.in_(org_ids), Organization.type == "club")
        clubs = {o.id: o for o in (await db.execute(stmt)).scalars()}

    counters = {enrollment_id: {} for enrollment_id in ids}
    stmt = (
        select(RequirementProgress.enrollment_id, RequirementProgress.status, func.count())
        .where(RequirementProgress.enrollment_id.in_(ids))
        .group_by(RequirementProgress.enrollment_id, RequirementProgress.status)
    )
    for enrollment_id, progress_status, count in await db.execute(stmt):
        counters[enrollment_id][progress_status] = count

    certificate_ids = {e.certificate_id for e in enrollments if e.certificate_id}
    certificates = {}
    if certificate_ids:
        rows = (await db.execute(select(Certificate).where(Certificate.id.in_(certificate_ids)))).scalars().all()
        certificates = {uuid.UUID(c.id): c for c in await _certificates_out(db, rows)}

    honors = await _honor_refs(db, enrollments)
    courses = await _course_refs(db, enrollments)
    summaries = []
    for e in enrollments:
        club = clubs.get(club_of[e.id])
        if club is not None and e.status not in FROZEN and club.status != "active":
            club = None  # a pending or rejected club reviews nobody
        by_status = counters[e.id]
        summaries.append(
            EnrollmentSummary(
                id=str(e.id),
                status=e.status,
                mode=e.mode,
                locale=e.locale,
                user=PersonRef(id=str(e.user_id), name=users[e.user_id].name),
                club=ClubRef(id=str(club.id), name=club.name) if club else None,
                honor=honors[e.id],
                counters=Counters(
                    total=sum(by_status.values()),
                    complete=by_status.get(COMPLETE, 0),
                    submitted=by_status.get(SUBMITTED, 0),
                    incomplete=by_status.get(INCOMPLETE, 0),
                ),
                certificate=certificates.get(e.certificate_id),
                started_at=e.started_at,
                ready_at=e.ready_at,
                certified_at=e.certified_at,
                withdrawn_at=e.withdrawn_at,
                updated_at=e.updated_at,
                course=courses.get(e.course_id),
                course_removed_reason=e.course_removed_reason,
            )
        )
    return summaries


async def _course_refs(
    db: AsyncSession, enrollments: list[HonorEnrollment]
) -> dict[uuid.UUID, CourseRef]:
    """Bloque B · I3: title and instructor of every course these enrollments point at."""
    course_ids = {e.course_id for e in enrollments if e.course_id}
    if not course_ids:
        return {}
    rows = (await db.execute(select(Course).where(Course.id.in_(course_ids)))).scalars().all()
    names = dict(
        (await db.execute(
            select(User.id, User.name).where(User.id.in_({row.instructor_id for row in rows}))
        )).all()
    )
    return {
        row.id: CourseRef(
            id=str(row.id), title=row.title, instructor_name=names.get(row.instructor_id, "")
        )
        for row in rows
    }


async def course_plan(db: AsyncSession, enrollment: HonorEnrollment) -> dict[int, str]:
    """`assessment` by requirement position for a COURSE enrollment; empty in CLUB.

    The single reader of `course_requirements` from the portfolio side: Bloque C asks it
    which requirements the exam completes, and the serializer shows it to the member.
    """
    if enrollment.mode != "COURSE" or enrollment.course_id is None:
        return {}
    stmt = select(CourseRequirement.requirement_position, CourseRequirement.assessment).where(
        CourseRequirement.course_id == enrollment.course_id
    )
    return dict((await db.execute(stmt)).all())


async def _detail(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> EnrollmentDetail:
    summary = (await _summaries(db, [enrollment]))[0]
    progress_rows = (
        await db.execute(
            select(RequirementProgress)
            .where(RequirementProgress.enrollment_id == enrollment.id)
            .order_by(RequirementProgress.requirement_position)
        )
    ).scalars().all()

    listed, _ = await _requirement_list(db, enrollment.honor_id, enrollment.locale)
    text_by_id = {row.id: row for _, row in listed}
    text_by_position = dict(listed)

    evidences: dict[uuid.UUID, list[EvidenceOut]] = {}
    stmt = (
        select(Evidence)
        .where(Evidence.progress_id.in_([p.id for p in progress_rows]), Evidence.status == ACTIVE)
        .order_by(Evidence.created_at)
    )
    for evidence in (await db.execute(stmt)).scalars():
        evidences.setdefault(evidence.progress_id, []).append(_evidence_out(evidence))

    reviewer_ids = {p.reviewed_by_id for p in progress_rows if p.reviewed_by_id}
    reviewers = {}
    if reviewer_ids:
        stmt = select(User.id, User.name).where(User.id.in_(reviewer_ids))
        reviewers = {user_id: PersonRef(id=str(user_id), name=name) for user_id, name in await db.execute(stmt)}

    plan = await course_plan(db, enrollment)
    requirements = []
    for p in progress_rows:
        source = text_by_id.get(p.requirement_id) or text_by_position.get(p.requirement_position)
        requirements.append(
            RequirementOut(
                assessment=plan.get(p.requirement_position),
                position=p.requirement_position,
                requirement_id=str(p.requirement_id) if p.requirement_id else None,
                description=source.description if source else None,
                instructions=source.instructions if source else None,
                is_practical=p.is_practical,
                status=p.status,
                completed_via=p.completed_via,
                member_note=p.member_note,
                submitted_at=p.submitted_at,
                reviewed_by=reviewers.get(p.reviewed_by_id),
                reviewed_at=p.reviewed_at,
                review_note=p.review_note,
                evidences=evidences.get(p.id, []),
            )
        )
    permissions = Permissions(
        is_owner=enrollment.user_id == actor.id,
        can_review=await can_review(db, actor, enrollment),
        can_issue=await can_issue(db, actor, enrollment),
    )
    return EnrollmentDetail(**summary.model_dump(), requirements=requirements, permissions=permissions)


# ----------------------------------------------------------------------------
# Enrollment
# ----------------------------------------------------------------------------
async def _live_enrollment(db: AsyncSession, user_id: uuid.UUID, honor_id: uuid.UUID) -> HonorEnrollment | None:
    stmt = select(HonorEnrollment).where(
        HonorEnrollment.user_id == user_id,
        HonorEnrollment.honor_id == honor_id,
        HonorEnrollment.status != WITHDRAWN,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def enroll(
    db: AsyncSession, actor: User, payload: EnrollmentCreate, request: Request | None
) -> tuple[EnrollmentDetail, bool]:
    """Idempotent: a live enrollment in the same honor is returned as it is. -> (detail, created)"""
    existing = await _live_enrollment(db, actor.id, payload.honor_id)
    if existing is not None:
        return await _detail(db, actor, existing), False

    honor = await db.get(Honor, payload.honor_id)
    if honor is None or honor.status != PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Especialidad no encontrada")
    requirements, locale = await _requirement_list(db, honor.id, payload.locale)
    if not requirements:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La especialidad todavía no tiene requisitos cargados"
        )

    now = utcnow()
    club = await member_club(db, actor)
    enrollment = HonorEnrollment(
        id=uuid.uuid4(),
        user_id=actor.id,
        honor_id=honor.id,
        mode="CLUB",
        club_id=club.id if club else None,
        locale=locale,
        status=IN_PROGRESS,
        started_at=now,
        updated_at=now,
    )
    db.add(enrollment)
    record_audit(
        db,
        action="ENROLL",
        entity_type=ENROLLMENT,
        entity_id=enrollment.id,
        actor=actor,
        metadata={"honor_id": str(honor.id), "locale": locale, "requirements": len(requirements)},
        request=request,
    )
    try:
        # The models declare no relationship(): the enrollment goes in before its rows.
        await db.flush()
        db.add_all(
            RequirementProgress(
                id=uuid.uuid4(),
                enrollment_id=enrollment.id,
                requirement_position=position,
                requirement_id=requirement.id,
                is_practical=not requirement.is_theoretical,
                status=PENDING,
            )
            for position, requirement in requirements
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != ACTIVE_ENROLLMENT_CONSTRAINT:
            raise
        # Two requests at once (a double click): the other one won, answer with its row.
        existing = await _live_enrollment(db, actor.id, payload.honor_id)
        return await _detail(db, actor, existing), False
    return await _detail(db, actor, enrollment), True


async def list_enrollments(db: AsyncSession, user: User, status_filter: str | None) -> list[EnrollmentSummary]:
    stmt = select(HonorEnrollment).where(HonorEnrollment.user_id == user.id)
    if status_filter:
        stmt = stmt.where(HonorEnrollment.status == status_filter)
    else:
        stmt = stmt.where(HonorEnrollment.status != WITHDRAWN)
    rows = (await db.execute(stmt.order_by(HonorEnrollment.updated_at.desc()))).scalars().all()
    return await _summaries(db, rows)


async def get_detail(db: AsyncSession, actor: User, enrollment_id: uuid.UUID) -> EnrollmentDetail:
    enrollment = await _get_enrollment(db, enrollment_id)
    await _require_viewer(db, actor, enrollment)
    return await _detail(db, actor, enrollment)


async def withdraw(db: AsyncSession, actor: User, enrollment_id: uuid.UUID, request: Request | None) -> None:
    enrollment = await _get_enrollment(db, enrollment_id, lock=True)
    _require_owner(actor, enrollment)
    if enrollment.status == WITHDRAWN:
        return
    if enrollment.status == CERTIFIED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Una especialidad certificada no se puede retirar")
    await _touch(db, enrollment)
    enrollment.status = WITHDRAWN
    enrollment.withdrawn_at = utcnow()
    record_audit(
        db, action="ENROLLMENT_WITHDRAW", entity_type=ENROLLMENT, entity_id=enrollment.id,
        actor=actor, request=request,
    )
    await db.commit()


# ----------------------------------------------------------------------------
# The member's side of a requirement
# ----------------------------------------------------------------------------
async def update_requirement(
    db: AsyncSession,
    actor: User,
    enrollment_id: uuid.UUID,
    position: int,
    payload: RequirementUpdate,
    request: Request | None,
) -> EnrollmentDetail:
    enrollment = await _get_enrollment(db, enrollment_id, lock=True)
    _require_owner(actor, enrollment)
    _require_open(enrollment)
    progress = await _get_progress(db, enrollment, position)
    _require_open_requirement(progress)
    if payload.status == PENDING and progress.status == INCOMPLETE:
        # Already reviewed: the only way forward is to fix it and send it again.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El requisito ya fue dictaminado; corrígelo y envíalo de nuevo a revisión",
        )

    if payload.status is None and progress.status == SUBMITTED:
        # What a reviewer is about to read is not edited behind their back: take it back first.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El requisito ya está enviado a revisión; deshaz el envío para editar tu respuesta",
        )

    previous = progress.status
    if "member_note" in payload.model_fields_set:
        progress.member_note = (payload.member_note or "").strip() or None
    if payload.status == SUBMITTED:
        # A reviewer can only judge something: evidence when the requirement is practical,
        # an answer or evidence otherwise.
        evidence = await _active_evidence_count(db, progress.id)
        if progress.is_practical and evidence == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Este requisito pide evidencia: añade al menos una foto o PDF antes de enviarlo.",
            )
        if not progress.member_note and evidence == 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Escribe tu respuesta o añade una evidencia antes de enviar.",
            )
    if payload.status is not None:
        progress.status = payload.status
        progress.submitted_at = utcnow() if payload.status == SUBMITTED else None
    await _touch(db, enrollment)
    record_audit(
        db,
        action="REQUIREMENT_SUBMIT",
        entity_type=PROGRESS,
        entity_id=progress.id,
        actor=actor,
        metadata={"enrollment_id": str(enrollment.id), "position": position,
                  "from": previous, "to": progress.status, "draft": payload.status is None},
        request=request,
    )
    await db.commit()
    return await _detail(db, actor, enrollment)


# ----------------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------------
async def add_evidence(
    db: AsyncSession,
    actor: User,
    enrollment_id: uuid.UUID,
    position: int,
    payload: EvidenceCreate,
    request: Request | None,
) -> EvidenceUpload:
    """Reserve the evidence and hand out the presigned PUT. It counts as evidence only after
    `complete_evidence` has seen the object in the bucket."""
    _require_private_storage()
    enrollment = await _get_enrollment(db, enrollment_id, lock=True)
    _require_owner(actor, enrollment)
    _require_open(enrollment)
    progress = await _get_progress(db, enrollment, position)
    _require_open_requirement(progress)

    max_size = private_storage.max_size_for(payload.content_type)
    if payload.size_bytes > max_size:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"El archivo supera el tamaño máximo de {max_size // (1024 * 1024)} MB",
        )
    # Uploads still inside their signing window hold a slot, so the cap cannot be bypassed
    # by opening many at once; abandoned ones stop counting when their URL expires.
    fresh = utcnow() - timedelta(seconds=private_storage.PUT_EXPIRES_SECONDS)
    in_use = select(func.count()).where(
        Evidence.progress_id == progress.id,
        or_(Evidence.status == ACTIVE, (Evidence.status == PENDING_UPLOAD) & (Evidence.created_at > fresh)),
    )
    if (await db.execute(in_use)).scalar_one() >= EVIDENCE_MAX_PER_REQUIREMENT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Máximo {EVIDENCE_MAX_PER_REQUIREMENT} evidencias por requisito",
        )

    evidence_id = uuid.uuid4()
    evidence = Evidence(
        id=evidence_id,
        progress_id=progress.id,
        uploaded_by_id=actor.id,
        kind=private_storage.kind_for(payload.content_type),
        status=PENDING_UPLOAD,
        storage_key=private_storage.build_key(actor.id, enrollment.id, evidence_id, payload.content_type),
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256.lower() if payload.sha256 else None,
        taken_on=payload.taken_on,
        place=payload.place,
        caption=payload.caption,
        created_at=utcnow(),
    )
    try:
        upload = private_storage.presign_put(evidence.storage_key, evidence.content_type, evidence.size_bytes)
    except private_storage.PrivateStorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)
    db.add(evidence)
    await _touch(db, enrollment)
    await db.commit()
    return EvidenceUpload(evidence=_evidence_out(evidence), upload=UploadTarget(**upload))


async def complete_evidence(
    db: AsyncSession, actor: User, evidence_id: uuid.UUID, request: Request | None
) -> EvidenceOut:
    _require_private_storage()
    evidence, progress, enrollment = await _get_evidence(db, evidence_id, lock=True)
    if evidence.uploaded_by_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo quien subió la evidencia puede confirmarla")
    if evidence.status == ACTIVE:
        return _evidence_out(evidence)
    if evidence.status == REMOVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "La evidencia fue eliminada")
    _require_open(enrollment)
    _require_open_requirement(progress)

    try:
        stored = await private_storage.head(evidence.storage_key)
    except private_storage.PrivateStorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)
    except private_storage.PrivateStorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "El archivo todavía no se ha subido")
    if stored.size_bytes != evidence.size_bytes or stored.content_type != evidence.content_type:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El archivo subido no coincide con el tamaño o el tipo declarados"
        )
    if await _active_evidence_count(db, progress.id) >= EVIDENCE_MAX_PER_REQUIREMENT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Máximo {EVIDENCE_MAX_PER_REQUIREMENT} evidencias por requisito",
        )

    evidence.status = ACTIVE
    await _touch(db, enrollment)
    record_audit(
        db,
        action="EVIDENCE_ADD",
        entity_type=EVIDENCE,
        entity_id=evidence.id,
        actor=actor,
        metadata={"enrollment_id": str(enrollment.id), "position": progress.requirement_position,
                  "kind": evidence.kind, "size_bytes": evidence.size_bytes},
        request=request,
    )
    await db.commit()
    return _evidence_out(evidence)


async def evidence_url(db: AsyncSession, actor: User, evidence_id: uuid.UUID) -> SignedUrl:
    """A 5-minute read URL, signed on every call and stored nowhere."""
    evidence, _, enrollment = await _get_evidence(db, evidence_id)
    await _require_viewer(db, actor, enrollment)
    if evidence.status != ACTIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidencia no encontrada")
    try:
        return SignedUrl(**private_storage.presign_get(evidence.storage_key))
    except private_storage.PrivateStorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)


async def remove_evidence(
    db: AsyncSession, actor: User, evidence_id: uuid.UUID, request: Request | None
) -> None:
    """Logical removal; migrations/purge_removed_evidence.py deletes the object later."""
    evidence, progress, enrollment = await _get_evidence(db, evidence_id, lock=True)
    _require_owner(actor, enrollment)
    if evidence.status == REMOVED:
        return
    _require_open(enrollment)
    _require_open_requirement(progress)
    evidence.status = REMOVED
    evidence.removed_at = utcnow()
    await _touch(db, enrollment)
    record_audit(
        db,
        action="EVIDENCE_REMOVE",
        entity_type=EVIDENCE,
        entity_id=evidence.id,
        actor=actor,
        metadata={"enrollment_id": str(enrollment.id), "position": progress.requirement_position},
        request=request,
    )
    await db.commit()


# ----------------------------------------------------------------------------
# Review
# ----------------------------------------------------------------------------
async def _reviewer_scope(db: AsyncSession, actor: User) -> list | None:
    """Whose work `actor` sees in the queue. None = everyone (MASTER_GC).

    Two ways in, and a verified instructor attached to a club has both: the members of the
    club they staff (Bloque A) and the enrollments of the courses they teach (Bloque B).
    """
    if is_master(actor):
        return None
    reach = []
    club = await member_club(db, actor) if club_staff_in_good_standing(actor) else None
    if club is not None:
        reach.append(User.organization_id == club.id)
    course_ids = await _taught_course_ids(db, actor)
    if course_ids:
        reach.append(HonorEnrollment.course_id.in_(course_ids))
    if not reach:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo los revisores de un club aprobado tienen cola de revisión"
        )
    return [or_(*reach)] if len(reach) > 1 else reach


async def _taught_course_ids(db: AsyncSession, actor: User) -> list[uuid.UUID]:
    """Courses whose enrollments `actor` may act on right now: theirs, live, and only while
    the letter holds. `can_review` / `can_issue` decide again row by row."""
    if not await instructor_is_verified(db, actor):
        return []
    stmt = select(Course.id).where(
        Course.instructor_id == actor.id,
        Course.status.in_(("PUBLISHED", "ARCHIVED")),
        Course.archived_by_authority.is_(False),
    )
    return list((await db.execute(stmt)).scalars().all())


async def review_queue(
    db: AsyncSession, actor: User, queue_status: str, limit: int, offset: int
) -> list[QueueRequirement] | list[QueueReady]:
    reach = await _reviewer_scope(db, actor)
    # Jurisdiction is the member's club of today (or the reviewer's own courses), and never
    # the reviewer's own work.
    scope = [HonorEnrollment.user_id != actor.id, *(reach or [])]

    if queue_status == READY:
        stmt = (
            select(HonorEnrollment, User)
            .join(User, User.id == HonorEnrollment.user_id)
            .where(HonorEnrollment.status == READY, *scope)
            .order_by(HonorEnrollment.ready_at, HonorEnrollment.id)
        )
        rows = (await db.execute(stmt.limit(limit).offset(offset))).all()
        honors = await _honor_refs(db, [enrollment for enrollment, _ in rows])
        return [
            QueueReady(
                enrollment_id=str(enrollment.id),
                member=PersonRef(id=str(member.id), name=member.name),
                honor=honors[enrollment.id],
                ready_at=enrollment.ready_at,
                can_issue=await can_issue(db, actor, enrollment),
            )
            for enrollment, member in rows
        ]

    stmt = (
        select(RequirementProgress, HonorEnrollment, User)
        .join(HonorEnrollment, HonorEnrollment.id == RequirementProgress.enrollment_id)
        .join(User, User.id == HonorEnrollment.user_id)
        .where(RequirementProgress.status == SUBMITTED, HonorEnrollment.status == IN_PROGRESS, *scope)
        .order_by(RequirementProgress.submitted_at, RequirementProgress.id)
    )
    rows = (await db.execute(stmt.limit(limit).offset(offset))).all()
    honors = await _honor_refs(db, [enrollment for _, enrollment, _ in rows])
    evidence_counts = {}
    if rows:
        counts = (
            select(Evidence.progress_id, func.count())
            .where(Evidence.progress_id.in_([progress.id for progress, _, _ in rows]), Evidence.status == ACTIVE)
            .group_by(Evidence.progress_id)
        )
        evidence_counts = dict((await db.execute(counts)).all())
    return [
        QueueRequirement(
            enrollment_id=str(enrollment.id),
            position=progress.requirement_position,
            is_practical=progress.is_practical,
            member=PersonRef(id=str(member.id), name=member.name),
            honor=honors[enrollment.id],
            member_note=progress.member_note,
            submitted_at=progress.submitted_at,
            evidence_count=evidence_counts.get(progress.id, 0),
        )
        for progress, enrollment, member in rows
    ]


async def review_requirement(
    db: AsyncSession,
    actor: User,
    enrollment_id: uuid.UUID,
    position: int,
    payload: ReviewIn,
    request: Request | None,
) -> EnrollmentDetail:
    enrollment = await _get_enrollment(db, enrollment_id, lock=True)
    if not await can_review(db, actor, enrollment):
        own = enrollment.user_id == actor.id
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Nadie dictamina su propia inscripción" if own
            else "No tienes jurisdicción para dictaminar esta inscripción",
        )
    _require_open(enrollment)
    progress = await _get_progress(db, enrollment, position)
    # A verdict answers a submission. The one exception is rule 4: reopening a COMPLETE one.
    reopening = progress.status == COMPLETE and payload.verdict == INCOMPLETE
    if progress.status != SUBMITTED and not reopening:
        raise HTTPException(status.HTTP_409_CONFLICT, "El requisito no está enviado a revisión")
    if reopening and enrollment.mode == "COURSE":
        # D4a: in COURSE a COMPLETE is reopened only by whoever gave it, the instructor of
        # the course (they sign the certificate) or MASTER_GC. A director who disagrees
        # escalates instead of undoing. In CLUB, rule 4 of A applies unchanged.
        allowed = (
            progress.reviewed_by_id == actor.id
            or is_master(actor)
            or await is_course_instructor(db, actor, enrollment)
        )
        if not allowed:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Este requisito lo completó otra persona; solo quien lo dictaminó o el"
                " instructor del curso pueden reabrirlo",
            )
    if payload.verdict == COMPLETE and progress.is_practical:
        if await _active_evidence_count(db, progress.id) == 0:  # rule 1
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Un requisito práctico necesita al menos una evidencia para completarse",
            )

    now = utcnow()
    previous = progress.status
    progress.status = payload.verdict
    progress.completed_via = "REVIEW" if payload.verdict == COMPLETE else None
    progress.reviewed_by_id = actor.id
    progress.reviewed_at = now
    progress.review_note = payload.note
    await db.flush()

    # Rule 2: READY is never set by hand, it follows the requirements.
    open_rows = select(func.count()).where(
        RequirementProgress.enrollment_id == enrollment.id, RequirementProgress.status != COMPLETE
    )
    all_complete = (await db.execute(open_rows)).scalar_one() == 0
    if all_complete and enrollment.status != READY:
        enrollment.status, enrollment.ready_at = READY, now
    elif not all_complete:
        enrollment.status, enrollment.ready_at = IN_PROGRESS, None
    await _touch(db, enrollment)
    record_audit(
        db,
        action="REQUIREMENT_REVIEW",
        entity_type=PROGRESS,
        entity_id=progress.id,
        actor=actor,
        details=payload.note,
        metadata={"enrollment_id": str(enrollment.id), "position": position, "from": previous,
                  "verdict": payload.verdict, "enrollment_status": enrollment.status},
        request=request,
    )
    await db.commit()
    return await _detail(db, actor, enrollment)


# ----------------------------------------------------------------------------
# Certificate
# ----------------------------------------------------------------------------
async def _course_signatures(
    db: AsyncSession, enrollment: HonorEnrollment
) -> tuple[str | None, str | None]:
    """(instructor_name, director_name) for a COURSE certificate (spec D §5.3).

    The instructor of the course signs it — the form cannot change that name — and the
    director's line carries the last CLUB_DIRECTOR who judged a requirement of this
    enrollment, or nothing at all when no club took part.
    """
    course = await db.get(Course, enrollment.course_id)
    instructor = await db.get(User, course.instructor_id) if course else None
    last_director = (
        select(User.name)
        .join(RequirementProgress, RequirementProgress.reviewed_by_id == User.id)
        .where(
            RequirementProgress.enrollment_id == enrollment.id,
            RequirementProgress.reviewed_at.is_not(None),
            User.role == CLUB_DIRECTOR,
        )
        .order_by(RequirementProgress.reviewed_at.desc())
        .limit(1)
    )
    return (
        instructor.name if instructor else None,
        (await db.execute(last_director)).scalar_one_or_none(),
    )


async def _director_name(db: AsyncSession, actor: User, club: Organization | None) -> str | None:
    if actor.role == CLUB_DIRECTOR:
        return actor.name
    if club is None:
        return None
    stmt = (
        select(User.name)
        .where(
            User.role == CLUB_DIRECTOR,
            User.organization_id == club.id,
            User.status == "ACTIVE",
            or_(User.club_approval.is_(None), User.club_approval == CLUB_APPROVED),
        )
        .order_by(User.created_at)
    )
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()


async def issue(
    db: AsyncSession,
    actor: User,
    enrollment_id: uuid.UUID,
    payload: CertificateIssue,
    request: Request | None,
) -> CertificateOut:
    """READY -> CERTIFIED: same issuer, folio, hash and QR as every other certificate, now linked
    to the account, the enrollment and whoever pressed the button."""
    enrollment = await _get_enrollment(db, enrollment_id, lock=True)
    if not await can_issue(db, actor, enrollment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes certificar esta inscripción")
    if enrollment.status != READY:
        raise HTTPException(status.HTTP_409_CONFLICT, "La inscripción no está lista para certificar")

    slug = payload.template or DEFAULT_CERTIFICATE_TEMPLATE
    try:
        svg_template = load_template(slug)
    except TemplateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))

    member = await db.get(User, enrollment.user_id)
    honor = await db.get(Honor, enrollment.honor_id)
    if honor.ministry_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La especialidad no pertenece a ningún ministerio")
    organization = await resolve_issuer_organization(db)
    application = (
        await db.execute(
            select(Application)
            .where(Application.ministry_id == honor.ministry_id, Application.status == "active")
            .order_by(Application.created_at)
            .limit(1)
        )
    ).scalars().first()
    member_org = await member_club(db, member)
    club = (
        await get_or_create_club(db, organization.id, honor.ministry_id, member_org.name)
        if member_org
        else None
    )
    width_in, height_in = round(svg_template.width_pt / 72, 4), round(svg_template.height_pt / 72, 4)
    template = await get_or_create_template(
        db, honor.ministry_id, slug, width_in, height_in,
        orientation="landscape" if width_in >= height_in else "portrait", supports_svg=True,
    )

    if enrollment.mode == "COURSE":
        instructor_name, director_name = await _course_signatures(db, enrollment)
    else:
        instructor_name = payload.instructor_name
        director_name = await _director_name(db, actor, member_org)
    certificate = await issue_certificate(
        db,
        ministry_id=honor.ministry_id,
        application_id=application.id if application else None,
        organization=organization,
        club=club,
        honor_id=honor.id,
        honor_name=(await _honor_refs(db, [enrollment]))[enrollment.id].name,
        template=template,
        recipient_name=member.name,
        issued_date=payload.issued_date,
        place=payload.place,
        instructor_name=instructor_name,
        director_name=director_name,
        user_id=member.id,
        enrollment_id=enrollment.id,
        issued_by=actor,
    )
    await _touch(db, enrollment)  # last refresh: from here on club_id is frozen
    enrollment.status = CERTIFIED
    enrollment.certified_at = utcnow()
    enrollment.certificate_id = certificate.id
    record_audit(
        db,
        action="CERTIFICATE_ISSUE",
        entity_type=CERTIFICATE,
        entity_id=certificate.id,
        actor=actor,
        metadata={"enrollment_id": str(enrollment.id), "user_id": str(member.id),
                  "certificate_no": certificate.certificate_no, "template": slug,
                  "mode": enrollment.mode,
                  "course_id": str(enrollment.course_id) if enrollment.course_id else None},
        request=request,
    )
    await db.commit()
    return (await _certificates_out(db, [certificate]))[0]


# ----------------------------------------------------------------------------
# Portfolio of a person
# ----------------------------------------------------------------------------
async def portfolio_of(db: AsyncSession, actor: User, user_id: uuid.UUID) -> PortfolioOut:
    target = actor if user_id == actor.id else await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    if not await can_view_portfolio(db, actor, target):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver este portafolio")

    enrollments = (
        await db.execute(
            select(HonorEnrollment)
            .where(HonorEnrollment.user_id == target.id, HonorEnrollment.status != WITHDRAWN)
            .order_by(HonorEnrollment.updated_at.desc())
        )
    ).scalars().all()
    certificates = (
        await db.execute(
            select(Certificate)
            .where(Certificate.user_id == target.id)
            .order_by(Certificate.issued_date.desc(), Certificate.created_at.desc())
        )
    ).scalars().all()
    club = await member_club(db, target)
    return PortfolioOut(
        user=PortfolioUser(
            id=str(target.id),
            name=target.name,
            avatar_url=target.avatar_url,
            club=ClubRef(id=str(club.id), name=club.name) if club else None,
        ),
        enrollments=await _summaries(db, enrollments),
        certificates=await _certificates_out(db, certificates),
    )
