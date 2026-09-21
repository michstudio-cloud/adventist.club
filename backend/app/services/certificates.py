"""Certificate issuance: folio, SHA-256 hash and the `issued` event.

One path for every certificate, whether it comes from the open prototype batch
(main.py) or from a portfolio (services/portfolio.py). Everything is staged on
the caller's session; the caller commits.
"""
import hashlib
import json
import secrets
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Certificate,
    CertificateEvent,
    CertificateTemplate,
    Club,
    Course,
    HonorEnrollment,
    Organization,
    User,
)
from app.security import utcnow
from app.services.audit import record_audit


def cert_no() -> str:
    return f"CC-{secrets.token_hex(5).upper()}"


def canonical(c: Certificate) -> dict:
    """What the hash covers. The portfolio links (user, enrollment, issuer) are deliberately
    left out: adding a key here would invalidate every certificate already issued."""
    return {
        "certificate_no": c.certificate_no,
        "application_id": str(c.application_id) if c.application_id else None,
        "ministry_id": str(c.ministry_id),
        "organization_id": str(c.organization_id),
        "club_id": str(c.club_id) if c.club_id else None,
        "honor_id": str(c.honor_id) if c.honor_id else None,
        "template_id": str(c.template_id),
        "recipient_name": c.recipient_name,
        "honor_name": c.honor_name_snapshot,
        "club_name": c.club_name_snapshot,
        "issued_date": c.issued_date.isoformat(),
        "place": c.place,
        "instructor_name": c.instructor_name,
        "director_name": c.director_name,
    }


def hash_cert(c: Certificate) -> str:
    raw = json.dumps(canonical(c), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def resolve_issuer_organization(db: AsyncSession) -> Organization:
    """The organisation named as issuer on certificates (ISSUER_ORGANIZATION_CODE).

    Only the PROTOTYPE placeholder is ever auto-created; a real code that does
    not exist yet is a configuration error, not something to invent.
    """
    code = settings.ISSUER_ORGANIZATION_CODE.strip()
    stmt = select(Organization).where(Organization.code == code, Organization.status == "active")
    org = (await db.execute(stmt)).scalar_one_or_none()
    if org:
        return org
    if code != "PROTOTYPE":
        raise HTTPException(503, f"Organización emisora '{code}' no existe todavía.")
    org = Organization(
        id=uuid.uuid4(),
        type="club_network",
        name="Red Global de Certificados — Prototipo",
        code="PROTOTYPE",
        status="active",
    )
    db.add(org)
    await db.flush()
    return org


async def get_or_create_club(
    db: AsyncSession, organization_id: uuid.UUID, ministry_id: uuid.UUID, name: str
) -> Club:
    """The `clubs` row printed on the certificate, under the issuing organisation."""
    stmt = select(Club).where(
        Club.organization_id == organization_id, Club.ministry_id == ministry_id, Club.name == name
    )
    club = (await db.execute(stmt.limit(1))).scalars().first()
    if not club:
        club = Club(
            id=uuid.uuid4(),
            organization_id=organization_id,
            ministry_id=ministry_id,
            name=name,
            status="active",
        )
        db.add(club)
        await db.flush()
    return club


async def get_or_create_template(
    db: AsyncSession,
    ministry_id: uuid.UUID,
    name: str,
    width_in: float,
    height_in: float,
    *,
    orientation: str = "landscape",
    supports_svg: bool = False,
) -> CertificateTemplate:
    stmt = select(CertificateTemplate).where(
        CertificateTemplate.ministry_id == ministry_id, CertificateTemplate.name == name
    )
    template = (await db.execute(stmt.limit(1))).scalars().first()
    if not template:
        template = CertificateTemplate(
            id=uuid.uuid4(),
            ministry_id=ministry_id,
            name=name,
            width=width_in,
            height=height_in,
            unit="in",
            orientation=orientation,
            bleed=0,
            safe_margin=0.125,
            supports_svg=supports_svg,
            active=True,
        )
        db.add(template)
        await db.flush()
    return template


async def issue_certificate(
    db: AsyncSession,
    *,
    ministry_id: uuid.UUID,
    application_id: uuid.UUID | None,
    organization: Organization,
    club: Club | None,
    honor_id: uuid.UUID | None,
    honor_name: str,
    template: CertificateTemplate,
    recipient_name: str,
    issued_date: date,
    place: str | None = None,
    instructor_name: str | None = None,
    director_name: str | None = None,
    user_id: uuid.UUID | None = None,
    enrollment_id: uuid.UUID | None = None,
    issued_by: User | None = None,
    # Bloque D · I7: extra keys for the `issued` event, e.g. `{auto, attempt_id}` when an
    # exam issued the certificate on its own (spec §5.3). Never part of the hash.
    event_metadata: dict | None = None,
    # Bloque F: an investiture of a program. Exactly one of honor_id / program_id is set on
    # a portfolio certificate, and only `honor_id` enables buying the patch.
    program_id: uuid.UUID | None = None,
) -> Certificate:
    """Stage one issued certificate with its hash and `issued` event (flushed, not committed)."""
    certificate = Certificate(
        id=uuid.uuid4(),
        application_id=application_id,
        ministry_id=ministry_id,
        organization_id=organization.id,
        club_id=club.id if club else None,
        honor_id=honor_id,
        template_id=template.id,
        certificate_no=cert_no(),
        recipient_name=recipient_name,
        club_name_snapshot=club.name if club else None,
        honor_name_snapshot=honor_name,
        issued_date=issued_date,
        place=place,
        instructor_name=instructor_name,
        director_name=director_name,
        status="issued",
        user_id=user_id,
        enrollment_id=enrollment_id,
        issued_by_id=issued_by.id if issued_by else None,
        issued_role=issued_by.role if issued_by else None,
        program_id=program_id,
    )
    certificate.certificate_hash = hash_cert(certificate)
    db.add(certificate)
    await db.flush()
    db.add(
        CertificateEvent(
            id=uuid.uuid4(),
            certificate_id=certificate.id,
            event_type="issued",
            actor_id=issued_by.id if issued_by else None,
            metadata_json={"hash": certificate.certificate_hash, **(event_metadata or {})},
        )
    )
    return certificate


# ----------------------------------------------------------------------------
# Bloque D · I7 — The course shown by relation, and revocation.
#
# Neither of the two touches `canonical()`. That function is frozen (hallazgo 7 of the
# spec): adding one key to it would change the hash of every certificate ever issued and
# they would all stop verifying. So the course title travels by relation, outside the hash,
# and revoking writes only columns the hash does not cover.
# ----------------------------------------------------------------------------
REVOKED_STATUS = "revoked"
CERTIFIED, WITHDRAWN = "CERTIFIED", "WITHDRAWN"  # honor_enrollments.status (block A)


async def course_context(db: AsyncSession, certificate: Certificate) -> tuple[str | None, str | None]:
    """`(mode, course_title)` of the enrollment this certificate froze (spec §5.4).

    Nothing is stored on `certificates` for this — no `course_id` column — because the
    enrollment is frozen the moment it is certified and already keeps it.
    """
    if certificate.enrollment_id is None:
        return None, None
    enrollment = await db.get(HonorEnrollment, certificate.enrollment_id)
    if enrollment is None:
        return None, None
    if enrollment.mode != "COURSE" or enrollment.course_id is None:
        return enrollment.mode, None
    course = await db.get(Course, enrollment.course_id)
    return enrollment.mode, course.title if course else None


async def revoke_certificate(
    db: AsyncSession,
    certificate: Certificate,
    actor: User,
    reason: str,
    request=None,
) -> Certificate:
    """Annul an issued certificate (spec §5.5). Staged on the caller's session.

    Revoking is **never** a delete: the row keeps its folio, its hash, its signatures and
    its whole `certificate_events` history, so the story of what happened stays readable.
    What changes is `status`, and the public verification — which already answers "not
    valid" to anything that is not `issued` — starts saying «revocado» with its date.

    The enrollment goes CERTIFIED -> WITHDRAWN, which is the ONLY transition of its kind in
    the platform (spec §2.2), and keeps `certificate_id` as history: it is frozen, and the
    member may start the honor again from zero. There is no un-revoking.

    Who may do this is `rbac.can_revoke`, decided by the caller.
    """
    if certificate.status == REVOKED_STATUS:
        raise HTTPException(409, "Este certificado ya está anulado")
    now = utcnow()
    certificate.status = REVOKED_STATUS
    certificate.revoked_at = now
    certificate.revoked_by_id = actor.id
    certificate.revocation_reason = reason
    certificate.updated_at = now
    db.add(
        CertificateEvent(
            id=uuid.uuid4(),
            certificate_id=certificate.id,
            event_type="revoked",
            actor_id=actor.id,
            metadata_json={"reason": reason},
        )
    )

    enrollment = (
        await db.get(HonorEnrollment, certificate.enrollment_id)
        if certificate.enrollment_id
        else None
    )
    if enrollment is not None and enrollment.status == CERTIFIED:
        enrollment.status = WITHDRAWN
        enrollment.withdrawn_at = now
        enrollment.updated_at = now
    record_audit(
        db,
        action="CERTIFICATE_REVOKE",
        entity_type="CERTIFICATE",
        entity_id=certificate.id,
        actor=actor,
        details=reason,
        metadata={
            "certificate_no": certificate.certificate_no,
            "enrollment_id": str(certificate.enrollment_id) if certificate.enrollment_id else None,
            "user_id": str(certificate.user_id) if certificate.user_id else None,
            "issued_by_id": str(certificate.issued_by_id) if certificate.issued_by_id else None,
        },
        request=request,
    )
    return certificate
