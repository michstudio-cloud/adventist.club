"""Bloque D · I7 — The certificate that issues itself when the exam is the whole course.

The rule of the owner's vision, and nothing wider than it:

    «Sin parte física, aprobar el examen (≥ 80 %) emite el certificado automáticamente;
     con parte física, aprueba un director o el instructor tras revisar la evidencia.»

So this runs from exactly ONE place — an attempt turning `PASSED` (spec §5.3) — and only
when every single one of these holds:

  * the enrollment is COURSE and has just become `READY` (rule 2 of A: all requirements
    COMPLETE), so nothing is pending;
  * not one `requirement_progress` row of it is practical, according to the plan the
    reviewers approved — never the `is_theoretical` default of the imported catalogue
    (hallazgo 3);
  * it carries no certificate yet (an enrollment is certified once, §5.6 rule 3);
  * the course is live (published or archived by its own instructor, never withdrawn by a
    reviewer) and its instructor's church letter is authorized AT THIS MOMENT;
  * the instructor is not the member (rule 5 of A).

The instructor's approval is previous and general: a course whose plan has no `EVIDENCE`
requirement shows the author, the zone and the association the warning «este curso emitirá
el certificado automáticamente» before it is published (Bloque C · I4, deviation 23). By
the time this code runs, three people have agreed to it.

**The SAVEPOINT.** §5.6 rule 4: the exam result and the issuance are atomic *separately*.
Everything here happens inside `begin_nested()`, so a missing template, a broken issuer
organisation or any other surprise rolls back the certificate alone: the pass, the
completed requirements and `READY` survive, the enrollment waits in the instructor's
«listos para certificar» queue and the error goes to the logs (and to Sentry).

The assembly below deliberately mirrors `portfolio.issue`, which stays THE path for a
manual issuance. It is repeated rather than extracted so that block A's endpoint keeps its
shape untouched; if the two ever need to diverge, that is a decision, not an accident.
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.certificates.render import load_template
from app.models import (
    Application,
    Certificate,
    Course,
    ExamAttempt,
    Honor,
    HonorEnrollment,
    RequirementProgress,
    User,
)
from app.rbac import COURSE_LIVE_STATUSES, course_author_in_good_standing, member_club
from app.security import utcnow
from app.services import certificate_signatures
from app.services.audit import record_audit
from app.services.certificates import (
    get_or_create_club,
    get_or_create_template,
    issue_certificate,
    resolve_issuer_organization,
)

logger = logging.getLogger(__name__)

CERTIFICATE = "CERTIFICATE"


async def maybe_issue(
    db: AsyncSession,
    enrollment: HonorEnrollment,
    attempt: ExamAttempt,
    actor: User | None,
    request,
    background=None,
) -> Certificate | None:
    """Issue, or answer None and leave everything exactly as it was.

    Staged on the caller's session (the caller commits), except for the savepoint, which is
    resolved here because that is the whole point of it.
    """
    if not await _applies(db, enrollment):
        return None
    course = await db.get(Course, enrollment.course_id)
    if course is None or course.status not in COURSE_LIVE_STATUSES or course.archived_by_authority:
        return None
    instructor = await db.get(User, course.instructor_id)
    if instructor is None or instructor.id == enrollment.user_id:
        return None
    if not await course_author_in_good_standing(db, instructor):
        # The letter is the authorisation. Without it nobody signs, and the enrollment
        # waits: this is not an error, it is the gate doing its job.
        return None

    # Everything decided before the savepoint is already on the wire, so rolling back to
    # the savepoint gives back the certificate and nothing else.
    await db.flush()
    savepoint = await db.begin_nested()
    try:
        certificate = await _build(db, enrollment, attempt, course, instructor)
        enrollment.status = "CERTIFIED"
        enrollment.certified_at = utcnow()
        enrollment.certificate_id = certificate.id
        enrollment.updated_at = utcnow()
        record_audit(
            db,
            action="CERTIFICATE_AUTO_ISSUE",
            entity_type=CERTIFICATE,
            entity_id=certificate.id,
            # Whoever provoked the request (the member handing in, or the instructor
            # grading the last answer); the signature is the instructor's either way.
            actor=actor,
            metadata={
                "issued_by_id": str(instructor.id),
                "attempt_id": str(attempt.id),
                "enrollment_id": str(enrollment.id),
                "user_id": str(enrollment.user_id),
                "certificate_no": certificate.certificate_no,
                "mode": enrollment.mode,
                "course_id": str(course.id),
            },
            request=request,
        )
        await savepoint.commit()
    except Exception:
        await savepoint.rollback()
        logger.exception(
            "automatic issuance failed for enrollment %s; the exam result is kept",
            enrollment.id,
        )
        return None

    if background is not None:
        # E9: the one progress notice a family waits for. Staged in the caller's
        # transaction and sent after its commit, like every other notification.
        from app.services import notifications

        await notifications.queue_certificate_issued(
            db, background, enrollment_id=enrollment.id
        )
    return certificate


async def _applies(db: AsyncSession, enrollment: HonorEnrollment) -> bool:
    if enrollment.mode != "COURSE" or enrollment.course_id is None:
        return False
    if enrollment.status != "READY" or enrollment.certificate_id is not None:
        return False
    practical = (
        await db.execute(
            select(func.count()).where(
                RequirementProgress.enrollment_id == enrollment.id,
                RequirementProgress.is_practical.is_(True),
            )
        )
    ).scalar_one()
    return practical == 0


async def _build(
    db: AsyncSession,
    enrollment: HonorEnrollment,
    attempt: ExamAttempt,
    course: Course,
    instructor: User,
) -> Certificate:
    """The certificate itself: default template, the UTC date of `finished_at`, no place,
    the course instructor's signature and no director's line (§5.3)."""
    from app.services.portfolio import DEFAULT_CERTIFICATE_TEMPLATE, _honor_refs

    member = await db.get(User, enrollment.user_id)
    honor = await db.get(Honor, enrollment.honor_id)
    if member is None or honor is None or honor.ministry_id is None:
        raise ValueError("la inscripción no tiene especialidad con ministerio")

    svg_template = load_template(DEFAULT_CERTIFICATE_TEMPLATE)
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
    width_in = round(svg_template.width_pt / 72, 4)
    height_in = round(svg_template.height_pt / 72, 4)
    template = await get_or_create_template(
        db,
        honor.ministry_id,
        DEFAULT_CERTIFICATE_TEMPLATE,
        width_in,
        height_in,
        orientation="landscape" if width_in >= height_in else "portrait",
        supports_svg=True,
    )
    finished = attempt.finished_at or utcnow()
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
        issued_date=finished.date(),
        place=None,
        instructor_name=instructor.name,
        director_name=None,
        user_id=member.id,
        enrollment_id=enrollment.id,
        issued_by=instructor,
        # §5.3: the `issued` event says this was nobody's click.
        event_metadata={"auto": True, "attempt_id": str(attempt.id)},
    )
    # 021: nobody clicked, so nothing new is asked for: the instructor's own saved signature
    # (`users.signature_url`), if there is one, goes on their line as an immutable copy. Best
    # effort — a bucket that is down issues the certificate unsigned, never blocks it.
    saved = await certificate_signatures.saved_signature(instructor)
    if saved is not None:
        await certificate_signatures.attach([certificate], {"signature_instructor": saved}, strict=False)
    return certificate
