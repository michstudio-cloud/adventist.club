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

from app.certificates.render import OverrideError, TemplateError, check_overrides_fit, clean_text_overrides, load_template
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
    course_author_in_good_standing,
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
# Bloque F: the requirement adapter (§1.1) and everything a program adds on top of block A.
# Neither module imports this one at import time, so there is no cycle.
from app.services import certificate_signatures, curriculum, programs
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
# Owner, 2026-09-24: the v4 designs are the only honor templates offered; the first one is the default.
DEFAULT_CERTIFICATE_TEMPLATE = "especialidad-color"
ACTIVE_ENROLLMENT_CONSTRAINT = "honor_enrollments_user_honor_active_key"
# Bloque F: the same rule for a program, on its own partial unique index.
ACTIVE_ENROLLMENT_CONSTRAINTS = (
    ACTIVE_ENROLLMENT_CONSTRAINT,
    "honor_enrollments_user_program_active_key",
)

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


# Bloque F · F3: the club's bulk acts refresh the enrollments they change with the same rule.
touch = _touch


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
    """Honor of each enrollment, named in the enrollment's language when a translation exists.

    Bloque F: enrollments in a PROGRAM have no honor and are simply absent from the result,
    so every caller reads it with `.get()` and no program is ever named as an honor.
    """
    honor_ids = {e.honor_id for e in enrollments if e.honor_id is not None}
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
        if enrollment.honor_id is None:
            continue
        honor = honors[enrollment.honor_id]
        refs[enrollment.id] = HonorRef(
            id=str(honor.id),
            name=honor_name_in(honor.name, translations.get(honor.id, {}), enrollment.locale),
            slug=honor.slug,
            image_url=honor.image_url,
        )
    return refs


def honor_name_in(source_name: str, names: dict[str, str], locale: str) -> str:
    """The honor's name in the enrollment's language: the translation when one matches,
    the source text otherwise (and always for the source language)."""
    if locale.lower().split("-")[0] == SOURCE_LOCALE or not names:
        return source_name
    return names.get(match_locale(list(names), locale)) or source_name


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
            template_slug=slugs.get(c.template_id),
            locale=c.locale or "es",
            user_id=str(c.user_id) if c.user_id else None,
            enrollment_id=str(c.enrollment_id) if c.enrollment_id else None,
            issued_by_id=str(c.issued_by_id) if c.issued_by_id else None,
            issued_role=c.issued_role,
            revoked_at=c.revoked_at,
            revocation_reason=c.revocation_reason,
            signed=list(certificate_signatures.stored_urls(c)),
            text_overrides=c.text_overrides or None,
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
    programs_by_enrollment = await programs.program_refs(db, enrollments)  # Bloque F
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
                honor=honors.get(e.id),
                type="program" if e.program_id else "honor",       # Bloque F
                program=programs_by_enrollment.get(e.id),
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


async def _require_not_exam(db: AsyncSession, enrollment: HonorEnrollment, position: int) -> None:
    """Bloque C §4.4: in COURSE a requirement the plan evaluates with the exam is completed
    ONLY by passing it — neither the member sends it, nor a reviewer marks it complete. It
    can still be reopened with an INCOMPLETE verdict, and rows already COMPLETE when the
    member joined the course are respected."""
    if (await course_plan(db, enrollment)).get(position) == "EXAM":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Este requisito se completa con el examen del curso"
        )


async def _detail(db: AsyncSession, actor: User, enrollment: HonorEnrollment) -> EnrollmentDetail:
    summary = (await _summaries(db, [enrollment]))[0]
    progress_rows = (
        await db.execute(
            select(RequirementProgress)
            .where(RequirementProgress.enrollment_id == enrollment.id)
            .order_by(RequirementProgress.requirement_position)
        )
    ).scalars().all()

    # Bloque F: ONE adapter, two catalogues. For an honor this is exactly the list
    # `_requirement_list` returned before; for a program, its sections and requirements.
    specs = await curriculum.enrollment_specs(db, enrollment)
    text_by_id = {spec.source_id: spec for spec in specs if spec.source_id is not None}
    text_by_position = {spec.position: spec for spec in specs}

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
    # Bloque F: label, kind, target, what satisfied it and the approved hours. Empty dict
    # on an honor enrollment, so block A's payload is byte for byte the one it was.
    extras = await programs.requirement_extras(db, enrollment, progress_rows, specs)
    requirements = []
    for p in progress_rows:
        source = text_by_id.get(p.program_requirement_id or p.requirement_id) or text_by_position.get(
            p.requirement_position
        )
        requirements.append(
            RequirementOut(
                **extras.get(p.id, {}),
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
    return EnrollmentDetail(
        **summary.model_dump(),
        requirements=requirements,
        permissions=permissions,
        sections=programs.sections_of(specs, progress_rows),  # Bloque F: the card
    )


# ----------------------------------------------------------------------------
# Enrollment
# ----------------------------------------------------------------------------
async def _live_enrollment(
    db: AsyncSession,
    user_id: uuid.UUID,
    honor_id: uuid.UUID | None = None,
    program_id: uuid.UUID | None = None,   # Bloque F
) -> HonorEnrollment | None:
    stmt = select(HonorEnrollment).where(
        HonorEnrollment.user_id == user_id,
        HonorEnrollment.honor_id == honor_id
        if program_id is None
        else HonorEnrollment.program_id == program_id,
        HonorEnrollment.status != WITHDRAWN,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def enroll(
    db: AsyncSession, actor: User, payload: EnrollmentCreate, request: Request | None
) -> tuple[EnrollmentDetail, bool]:
    """Idempotent: a live enrollment in the same honor (or program) is returned as it is.

    Bloque F: `curriculum.resolve` turns either catalogue into the same requirement list,
    and from `stage_enrollment` on everything is block A's code with no branch.
    -> (detail, created)
    """
    existing = await _live_enrollment(db, actor.id, payload.honor_id, payload.program_id)
    if existing is not None:
        return await _detail(db, actor, existing), False

    source = await curriculum.resolve(db, payload.honor_id, payload.program_id, payload.locale)
    try:
        enrollment = await stage_enrollment(db, actor, actor, source, request)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) not in ACTIVE_ENROLLMENT_CONSTRAINTS:
            raise
        # Two requests at once (a double click): the other one won, answer with its row.
        existing = await _live_enrollment(db, actor.id, payload.honor_id, payload.program_id)
        return await _detail(db, actor, existing), False
    if source.program_id is not None:
        # Bloque F · F2: what the member already holds (an honor, a program, approved hours)
        # completes its requirement now, inside this same request.
        from app.services import portfolio_links

        if await portfolio_links.sync(db, actor, user_id=actor.id, request=request):
            await db.commit()
    return await _detail(db, actor, enrollment), True


async def stage_enrollment(
    db: AsyncSession,
    actor: User,
    member: User,
    source: "curriculum.Curriculum",
    request: Request | None,
    *,
    audit_extra: dict | None = None,
) -> HonorEnrollment:
    """THE enrollment of block A: the row, its audit and one progress row per requirement.

    Staged and flushed on the caller's session, never committed: `enroll` (the member
    enrolling themself) and F3 (a leader enrolling the club, `audit_extra` says so) share it,
    so a card opened by the club is exactly the card the member would have opened.
    Raises IntegrityError when `member` already holds a live enrollment in it.
    """
    requirements, locale = source.specs, source.locale
    now = utcnow()
    club = await member_club(db, member)
    enrollment = HonorEnrollment(
        id=uuid.uuid4(),
        user_id=member.id,
        honor_id=source.honor_id,
        program_id=source.program_id,
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
        metadata={"honor_id": str(source.honor_id) if source.honor_id else None,
                  "program_id": str(source.program_id) if source.program_id else None,
                  "locale": locale, "requirements": len(requirements), **(audit_extra or {})},
        request=request,
    )
    # The models declare no relationship(): the enrollment goes in before its rows.
    await db.flush()
    db.add_all(
        RequirementProgress(
            id=uuid.uuid4(),
            enrollment_id=enrollment.id,
            requirement_position=spec.position,
            requirement_id=spec.source_id if source.honor_id else None,
            program_requirement_id=spec.source_id if source.program_id else None,
            kind=spec.kind,
            is_practical=spec.evidence_required,
            status=PENDING,
        )
        for spec in requirements
    )
    await db.flush()
    return enrollment


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
    if payload.honor_enrollment_id is not None:
        # Bloque F · F2: an OPEN `HONOR` slot — which of my certified honors fills it.
        from app.services import portfolio_links

        return await portfolio_links.link_open_honor(
            db, actor, enrollment, position, payload.honor_enrollment_id, request
        )
    progress = await _get_progress(db, enrollment, position)
    _require_open_requirement(progress)
    programs.require_manual_route(progress)   # Bloque F · F2: hours are never sent by hand
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
        await _require_not_exam(db, enrollment, position)
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
    if not await course_author_in_good_standing(db, actor):
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
        queued = [enrollment for enrollment, _ in rows]
        honors = await _honor_refs(db, queued)
        program_refs = await programs.program_refs(db, queued)   # Bloque F
        return [
            QueueReady(
                enrollment_id=str(enrollment.id),
                member=PersonRef(id=str(member.id), name=member.name),
                honor=honors.get(enrollment.id),
                type="program" if enrollment.program_id else "honor",
                program=program_refs.get(enrollment.id),
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
    queued = [enrollment for _, enrollment, _ in rows]
    honors = await _honor_refs(db, queued)
    program_refs = await programs.program_refs(db, queued)   # Bloque F
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
            honor=honors.get(enrollment.id),
            type="program" if enrollment.program_id else "honor",
            program=program_refs.get(enrollment.id),
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
    if payload.verdict == COMPLETE:
        programs.require_manual_route(progress)   # Bloque F · F2: hours are never signed
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
    if payload.verdict == COMPLETE:
        await _require_not_exam(db, enrollment, position)
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
    if payload.verdict != COMPLETE:
        # Bloque F · F2, rule 8: reopening one that completed itself breaks the link, so the
        # automation does not put it back the moment the page is read again.
        progress.satisfied_by_enrollment_id = None
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
    *,
    audit_extra: dict | None = None,
    signatures: dict[str, bytes] | None = None,
) -> CertificateOut:
    """READY -> CERTIFIED: same issuer, folio, hash and QR as every other certificate, now linked
    to the account, the enrollment and whoever pressed the button.

    `audit_extra` (F3): what the investiture of a whole club adds to the audit row.
    `signatures` (021): already prepared by the caller (the investiture of a club prepares its
    signature once for every certificate); otherwise they come from `payload`."""
    enrollment = await _get_enrollment(db, enrollment_id, lock=True)
    if not await can_issue(db, actor, enrollment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes certificar esta inscripción")
    if enrollment.status != READY:
        raise HTTPException(status.HTTP_409_CONFLICT, "La inscripción no está lista para certificar")
    if signatures is None:
        # 021: checked before anything is written; a signature that is not valid or not the
        # issuer's own saved one is a 422 and nothing is issued.
        signatures = await certificate_signatures.prepare(
            {"signature_director": payload.signature_director, "signature_instructor": payload.signature_instructor},
            actor,
        )

    # Bloque F: what is being awarded — an honor (block A) or a program (an investiture).
    # For an honor `award` carries exactly what `honors` carried before.
    award = await curriculum.award_for(db, enrollment)
    if award.ministry_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La especialidad no pertenece a ningún ministerio")
    if award.kind == "program":
        svg_template = await programs.program_template(db, payload.template, award)
        slug = svg_template.slug
    else:
        slug = payload.template or DEFAULT_CERTIFICATE_TEMPLATE
        try:
            svg_template = load_template(slug)
        except TemplateError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if payload.locale not in svg_template.locales:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "locale_not_supported")
    # 023: reworded phrases, checked (keys, length, fit) before anything is written.
    try:
        text_overrides = clean_text_overrides(svg_template, payload.strings, payload.locale)
        check_overrides_fit(svg_template, text_overrides)
    except OverrideError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"code": exc.code, "detail": str(exc), "key": exc.key}) from exc

    member = await db.get(User, enrollment.user_id)
    organization = await resolve_issuer_organization(db)
    application = (
        await db.execute(
            select(Application)
            .where(Application.ministry_id == award.ministry_id, Application.status == "active")
            .order_by(Application.created_at)
            .limit(1)
        )
    ).scalars().first()
    member_org = await member_club(db, member)
    club = (
        await get_or_create_club(db, organization.id, award.ministry_id, member_org.name)
        if member_org
        else None
    )
    width_in, height_in = round(svg_template.width_pt / 72, 4), round(svg_template.height_pt / 72, 4)
    template = await get_or_create_template(
        db, award.ministry_id, slug, width_in, height_in,
        orientation="landscape" if width_in >= height_in else "portrait", supports_svg=True,
    )

    if enrollment.mode == "COURSE":
        instructor_name, director_name = await _course_signatures(db, enrollment)
    else:
        instructor_name = payload.instructor_name
        director_name = await _director_name(db, actor, member_org)
    certificate = await issue_certificate(
        db,
        ministry_id=award.ministry_id,
        application_id=application.id if application else None,
        organization=organization,
        club=club,
        honor_id=award.honor_id,
        program_id=award.program_id,   # Bloque F: NULL on an honor, and vice versa
        honor_name=award.name,
        template=template,
        recipient_name=member.name,
        issued_date=payload.issued_date,
        place=payload.place,
        instructor_name=instructor_name,
        director_name=director_name,
        user_id=member.id,
        enrollment_id=enrollment.id,
        issued_by=actor,
        locale=payload.locale,
        text_overrides=text_overrides or None,
    )
    # 021: an immutable copy in the certificate's own folder; no bucket, no certificate.
    signed = await certificate_signatures.attach([certificate], signatures)
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
                  "locale": payload.locale,
                  "mode": enrollment.mode,
                  "program_id": str(award.program_id) if award.program_id else None,
                  "course_id": str(enrollment.course_id) if enrollment.course_id else None,
                  **({"signed": signed} if signed else {}),
                  **({"text_overrides": sorted(text_overrides)} if text_overrides else {}),
                  **(audit_extra or {})},
        request=request,
    )
    # Bloque F · F2: this achievement may be the one another card was waiting for. Same
    # transaction as the certificate, and idempotent.
    from app.services import portfolio_links

    await portfolio_links.sync(db, actor, user_id=member.id, request=request)
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


# ----------------------------------------------------------------------------
# Bloque F · F2: the ONE door through which a requirement completes itself.
#
# `app/services/portfolio_links.py` (HONOR, PROGRAM, HOURS) uses it today and blocks B–D
# will use it for `COURSE` in F9. It stages the change on the caller's session — the caller
# commits — and it never overrides a verdict: a row that is already COMPLETE is not touched.
# ----------------------------------------------------------------------------
async def auto_complete(
    db: AsyncSession,
    actor: User | None,
    enrollment: HonorEnrollment,
    progress: RequirementProgress,
    *,
    via: str,
    source: HonorEnrollment | None = None,
    request: Request | None = None,
    action: str = "REQUIREMENT_AUTOCOMPLETE",
    after_verdict: bool = False,
) -> bool:
    """-> True when it completed the requirement, False when it deliberately did nothing."""
    if enrollment.status in FROZEN or progress.status == COMPLETE:
        return False
    if progress.status == INCOMPLETE and progress.reviewed_by_id is not None and not after_verdict:
        # A reviewer looked at this one and said no. The automation does not argue with a
        # person: the member fixes it and sends it again. `after_verdict` is the member's
        # own explicit act (choosing an honor for an open slot), which is not the automation.
        return False
    previous = progress.status
    progress.status = COMPLETE
    progress.completed_via = via
    # Nobody judged this: `reviewed_by_id` stays empty on purpose, so the card can say
    # «se completó solo» and a reviewer's signature is never invented.
    progress.reviewed_by_id = None
    progress.reviewed_at = None
    progress.submitted_at = None
    progress.satisfied_by_enrollment_id = source.id if source else None
    record_audit(
        db,
        action=action,
        entity_type=PROGRESS,
        entity_id=progress.id,
        actor=actor,
        metadata={"enrollment_id": str(enrollment.id), "position": progress.requirement_position,
                  "from": previous, "via": via,
                  "source_enrollment_id": str(source.id) if source else None},
        request=request,
    )
    await db.flush()
    return True


async def recompute_ready(db: AsyncSession, enrollment: HonorEnrollment) -> None:
    """Rule 2 of block A, applied after an automatic change: READY is never set by hand."""
    if enrollment.status in FROZEN:
        return
    open_rows = select(func.count()).where(
        RequirementProgress.enrollment_id == enrollment.id, RequirementProgress.status != COMPLETE
    )
    all_complete = (await db.execute(open_rows)).scalar_one() == 0
    now = utcnow()
    if all_complete and enrollment.status != READY:
        enrollment.status, enrollment.ready_at = READY, now
    elif not all_complete and enrollment.status == READY:
        enrollment.status, enrollment.ready_at = IN_PROGRESS, None
    enrollment.updated_at = now
