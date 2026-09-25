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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Certificate,
    CertificateEvent,
    CertificateTemplate,
    Club,
    Course,
    Honor,
    HonorEnrollment,
    HonorTranslation,
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
    # 016: the language it is issued in; outside the hash.
    locale: str = "es",
    # 023: reworded phrases ({key: text}, already checked against the template). Outside the
    # hash; the `issued` event records which keys changed, never the text.
    text_overrides: dict[str, str] | None = None,
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
        locale=locale,
        text_overrides=dict(text_overrides) if text_overrides else None,
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
            metadata_json={"hash": certificate.certificate_hash,
                           **({"text_overrides": sorted(text_overrides)} if text_overrides else {}),
                           **(event_metadata or {})},
        )
    )
    # Bloque G: the holder's cached XP is stale from this moment on.
    from app.services import xp

    xp.invalidate(user_id)
    return certificate


async def stored_locale(db: AsyncSession, certificate_no: str) -> str | None:
    """The language certificate `certificate_no` was issued in, or None when there is none."""
    stmt = select(Certificate.locale).where(Certificate.certificate_no == certificate_no)
    return (await db.execute(stmt)).scalar_one_or_none()


async def stored_text_overrides(db: AsyncSession, certificate_no: str) -> dict[str, str] | None:
    """023 — the phrases certificate `certificate_no` was issued with: None when there is no such
    certificate (the caller's stand, as for data), `{}` when it kept the template's own."""
    stmt = select(Certificate.id, Certificate.text_overrides).where(Certificate.certificate_no == certificate_no)
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    kept = row.text_overrides
    return {str(k): str(v) for k, v in kept.items()} if isinstance(kept, dict) else {}


async def template_slug(db: AsyncSession, template_id: uuid.UUID) -> str | None:
    """Slug of the server SVG template a certificate was issued on; None for prototype ones."""
    template = await db.get(CertificateTemplate, template_id)
    return template.name if template is not None and template.supports_svg else None


# ----------------------------------------------------------------------------
# What an issued certificate prints on an element template (docs/CERTIFICADOS_V4.md).
# ----------------------------------------------------------------------------
async def _association_name(db: AsyncSession, organization_id: uuid.UUID | None) -> str | None:
    """Name of the association that `organization_id` is (or hangs from)."""
    if organization_id is None:
        return None
    path = (await db.execute(select(Organization.path).where(Organization.id == organization_id))).scalar_one_or_none()
    if not path:
        return None
    stmt = select(Organization.name).where(Organization.type == "association", Organization.path.op("@>")(path))
    return (await db.execute(stmt.order_by(func.nlevel(Organization.path).desc()).limit(1))).scalar_one_or_none()


async def _batch_association(db: AsyncSession, certificate_id: uuid.UUID) -> str | None:
    """«Asociación o misión» of a certificate from the assistant's batch (POST
    /certificates/prototype-batch): there is no enrollment to read the club from, so the batch
    keeps what the page printed in its `issued` event (`association_name`, typed or taken from
    the registered club). `certificates` has no column for it and the hash never covers it."""
    stmt = select(CertificateEvent.metadata_json).where(
        CertificateEvent.certificate_id == certificate_id, CertificateEvent.event_type == "issued"
    )
    metadata = (await db.execute(stmt.limit(1))).scalar_one_or_none() or {}
    value = metadata.get("association_name")
    if not isinstance(value, str):
        return None
    return value.strip() or None


# Every data key `render_data` answers for. With a folio these come from the record or not at
# all: a key the record leaves empty is not taken from the caller either (POST /render).
RECORD_FIELDS = ("recipient_name", "honor_name", "issued_date", "director_name", "instructor_name",
                 "certificate_no", "folio", "organization_name", "association_name")


async def render_data(db: AsyncSession, certificate_no: str, locale: str) -> tuple[dict[str, str], str | None] | None:
    """`(data, honor image URL)` of the issued certificate `certificate_no`, in the words of
    an element template, or None when there is no such issued certificate.

    - `honor_name`: the honor's name in `locale` from `honor_translations`, Spanish otherwise
      (the snapshot when the honor no longer exists);
    - `issued_date`: ISO — the template writes it in its language;
    - `organization_name`: the association the member's club hangs from, else the issuer's
      association, else the issuing organisation's name;
    - `association_name` («Especialidad dorada»): ONLY the association the member's club hangs
      from (`organizations.path`, nearest ancestor of type association). No club or no
      association above it: absent, and the template leaves the line out — never a stand-in.
      A batch certificate (no enrollment) prints the association its batch recorded, if any;
    - `church_name` is not here: it is a translated string of the template.
    Only reads; never touches the hash."""
    from app.services.portfolio import honor_name_in   # portfolio imports this module

    certificate = (
        await db.execute(select(Certificate).where(Certificate.certificate_no == certificate_no))
    ).scalar_one_or_none()
    if certificate is None or certificate.status != "issued":
        return None
    honor_name, image_url = certificate.honor_name_snapshot, None
    honor = await db.get(Honor, certificate.honor_id) if certificate.honor_id else None
    if honor is not None:
        names = {
            row.locale: row.name
            for row in (await db.execute(select(HonorTranslation).where(HonorTranslation.honor_id == honor.id))).scalars()
        }
        honor_name, image_url = honor_name_in(honor.name, names, locale), honor.image_url
    club_org = None
    if certificate.enrollment_id:
        enrollment = await db.get(HonorEnrollment, certificate.enrollment_id)
        club_org = enrollment.club_id if enrollment else None
    association_name = await _association_name(db, club_org)
    batch_association = None if certificate.enrollment_id else await _batch_association(db, certificate.id)
    organization_name = (
        association_name
        or await _association_name(db, certificate.organization_id)
        or (await db.execute(select(Organization.name).where(Organization.id == certificate.organization_id))).scalar_one_or_none()
    )
    data = {
        "recipient_name": certificate.recipient_name,
        "honor_name": honor_name,
        "issued_date": certificate.issued_date.isoformat(),
        "director_name": certificate.director_name or "",
        "instructor_name": certificate.instructor_name or "",
        "certificate_no": certificate.certificate_no,
        "organization_name": organization_name or "",
        "association_name": association_name or batch_association or "",
    }
    return {k: v for k, v in data.items() if v}, image_url


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
    from app.services import xp  # Bloque G: the holder's cached XP is stale

    xp.invalidate(certificate.user_id)
    return certificate
