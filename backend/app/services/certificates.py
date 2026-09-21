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
from app.models import Certificate, CertificateEvent, CertificateTemplate, Club, Organization, User


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
            metadata_json={"hash": certificate.certificate_hash},
        )
    )
    return certificate
