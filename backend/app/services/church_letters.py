"""Bloque B · I1 — The church letter: every rule and state transition lives here.

    letter  PENDING_UPLOAD -> SUBMITTED -> ZONE_VALIDATED -> AUTHORIZED
            SUBMITTED | ZONE_VALIDATED -> REJECTED ;  AUTHORIZED -> REVOKED

The double step is the one the vision describes: the zone "validates instructors with a
pastoral letter" and the association "authorises instructors". An association reviewer may
also give the zone step (today almost no association has zone coordinators) and nobody ever
decides on their own letter.

The document goes browser -> PRIVATE bucket with the same presign / PUT / complete handshake
as portfolio evidence: this API never receives the file and stores no URL.

Each function commits its change together with its audit row.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import violated_constraint
from app.models import ChurchLetter, Organization, User
from app.rbac import club_scope_paths, instructor_is_verified, is_master, org_in_review_scope
from app.schemas.church_letter import (
    LetterCreate,
    LetterOut,
    LetterQueueItem,
    LetterReviewIn,
    LetterUpload,
    VerificationChecklist,
)
from app.schemas.portfolio import PersonRef, SignedUrl, UploadTarget
from app.security import INSTRUCTOR, utcnow
from app.services import private_storage
from app.services.audit import record_audit
from app.workflow import ASSOCIATION_REVIEWERS, ZONE_REVIEWERS

PENDING_UPLOAD = "PENDING_UPLOAD"
SUBMITTED = "SUBMITTED"
ZONE_VALIDATED = "ZONE_VALIDATED"
AUTHORIZED = "AUTHORIZED"
REJECTED = "REJECTED"
REVOKED = "REVOKED"
LIVE_STATUSES = (SUBMITTED, ZONE_VALIDATED, AUTHORIZED)

# Offices a church letter may back. Bloques E (director, club) and F widen this tuple and
# pass their role to `create`; the column is already varchar(40), so nothing migrates.
LETTER_ROLES = (INSTRUCTOR,)

# A letter is one signed page: 10 MB is plenty for a scan and keeps the bucket tidy.
MAX_SIZE_BYTES = 10 * 1024 * 1024
LIVE_LETTER_CONSTRAINT = "church_letters_user_role_live_key"

ENTITY = "CHURCH_LETTER"
# Which statuses each action may start from, and who decides at each status.
ACTION_SOURCES = {
    "VALIDATE": (SUBMITTED,),
    "AUTHORIZE": (ZONE_VALIDATED,),
    "REJECT": (SUBMITTED, ZONE_VALIDATED),
    "REVOKE": (AUTHORIZED,),
}
STAGE_REVIEWERS = {
    SUBMITTED: ZONE_REVIEWERS,
    ZONE_VALIDATED: ASSOCIATION_REVIEWERS,
    AUTHORIZED: ASSOCIATION_REVIEWERS,
}
AUDIT_ACTIONS = {
    "VALIDATE": "LETTER_VALIDATE",
    "AUTHORIZE": "LETTER_AUTHORIZE",
    "REJECT": "LETTER_REJECT",
    "REVOKE": "LETTER_REVOKE",
}


# ----------------------------------------------------------------------------
# Lookups and guards
# ----------------------------------------------------------------------------
async def _get_letter(db: AsyncSession, letter_id: uuid.UUID, lock: bool = False) -> ChurchLetter:
    stmt = select(ChurchLetter).where(ChurchLetter.id == letter_id)
    if lock:
        # Serializes two reviewers deciding on the same letter at the same time.
        stmt = stmt.with_for_update()
    letter = (await db.execute(stmt)).scalar_one_or_none()
    if letter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carta no encontrada")
    return letter


def _require_private_storage() -> None:
    if not settings.private_storage_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)


async def _live_letter(db: AsyncSession, user_id: uuid.UUID, role: str) -> ChurchLetter | None:
    stmt = select(ChurchLetter).where(
        ChurchLetter.user_id == user_id,
        ChurchLetter.role_requested == role,
        ChurchLetter.status.in_(LIVE_STATUSES),
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _latest_letter(db: AsyncSession, user_id: uuid.UUID, role: str) -> ChurchLetter | None:
    """The live one when there is one, else the last decision (so its note stays readable)."""
    stmt = (
        select(ChurchLetter)
        .where(ChurchLetter.user_id == user_id, ChurchLetter.role_requested == role)
        .order_by(ChurchLetter.status.in_(LIVE_STATUSES).desc(), ChurchLetter.created_at.desc())
    )
    return (await db.execute(stmt.limit(1))).scalars().first()


def _letter_out(letter: ChurchLetter) -> LetterOut:
    return LetterOut(
        id=str(letter.id),
        user_id=str(letter.user_id),
        role_requested=letter.role_requested,
        organization_id=str(letter.organization_id),
        church_name=letter.church_name,
        pastor_name=letter.pastor_name,
        status=letter.status,
        content_type=letter.content_type,
        size_bytes=letter.size_bytes,
        valid_until=letter.valid_until,
        decision_note=letter.decision_note,
        zone_validated_at=letter.zone_validated_at,
        decided_at=letter.decided_at,
        created_at=letter.created_at,
        updated_at=letter.updated_at,
    )


# ----------------------------------------------------------------------------
# The instructor's side
# ----------------------------------------------------------------------------
async def checklist(db: AsyncSession, actor: User) -> VerificationChecklist:
    letter = await _latest_letter(db, actor.id, INSTRUCTOR)
    return VerificationChecklist(
        role=actor.role,
        email_verified=actor.verification_status == "VERIFIED",
        child_protection=actor.child_protection_completed,
        letter=_letter_out(letter) if letter else None,
        verified=await instructor_is_verified(db, actor),
    )


async def create(
    db: AsyncSession,
    actor: User,
    payload: LetterCreate,
    request: Request | None,
    role_requested: str = INSTRUCTOR,
) -> LetterUpload:
    """Reserve the letter and hand out the presigned PUT. It only counts once `complete`
    has seen the object in the bucket, exactly like an evidence."""
    _require_private_storage()
    if role_requested not in LETTER_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Cargo no admitido para una carta")
    if actor.is_minor:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo una persona adulta puede presentar la carta")
    if actor.organization_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Elige primero tu Asociación o club")
    if payload.size_bytes > MAX_SIZE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"El archivo supera el tamaño máximo de {MAX_SIZE_BYTES // (1024 * 1024)} MB",
        )
    if await _live_letter(db, actor.id, role_requested) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya tienes una carta en trámite o autorizada")

    letter_id = uuid.uuid4()
    now = utcnow()
    letter = ChurchLetter(
        id=letter_id,
        user_id=actor.id,
        role_requested=role_requested,
        organization_id=actor.organization_id,
        church_name=payload.church_name.strip(),
        pastor_name=payload.pastor_name,
        storage_key=private_storage.build_letter_key(actor.id, letter_id, payload.content_type),
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        status=PENDING_UPLOAD,
        created_at=now,
        updated_at=now,
    )
    try:
        upload = private_storage.presign_put(letter.storage_key, letter.content_type, letter.size_bytes)
    except private_storage.PrivateStorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)
    db.add(letter)
    await db.commit()
    return LetterUpload(letter=_letter_out(letter), upload=UploadTarget(**upload))


async def complete(
    db: AsyncSession, actor: User, letter_id: uuid.UUID, request: Request | None
) -> LetterOut:
    """PENDING_UPLOAD -> SUBMITTED, once the object is in the bucket with the declared size and type."""
    _require_private_storage()
    letter = await _get_letter(db, letter_id, lock=True)
    if letter.user_id != actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo quien presenta la carta puede confirmarla")
    if letter.status != PENDING_UPLOAD:
        return _letter_out(letter)  # idempotent: it was already submitted or decided

    try:
        stored = await private_storage.head(letter.storage_key)
    except private_storage.PrivateStorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "El archivo todavía no se ha subido")
    if stored.size_bytes != letter.size_bytes or stored.content_type != letter.content_type:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El archivo subido no coincide con el tamaño o el tipo declarados"
        )

    letter.status = SUBMITTED
    letter.updated_at = utcnow()
    record_audit(
        db,
        action="LETTER_SUBMIT",
        entity_type=ENTITY,
        entity_id=letter.id,
        actor=actor,
        metadata={"role_requested": letter.role_requested, "organization_id": str(letter.organization_id)},
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != LIVE_LETTER_CONSTRAINT:
            raise
        # Another upload of the same person got there first.
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya tienes una carta en trámite o autorizada") from exc
    return _letter_out(letter)


async def signed_url(db: AsyncSession, actor: User, letter_id: uuid.UUID) -> SignedUrl:
    """A 5-minute read URL: the letter is personal data, so only its owner and the reviewers
    with scope over it ever see the document."""
    _require_private_storage()
    letter = await _get_letter(db, letter_id)
    if letter.user_id != actor.id and not await org_in_review_scope(db, actor, letter.organization_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver esta carta")
    return SignedUrl(**private_storage.presign_get(letter.storage_key))


# ----------------------------------------------------------------------------
# The reviewers' side
# ----------------------------------------------------------------------------
async def queue(
    db: AsyncSession, actor: User, status_filter: str | None, limit: int, offset: int
) -> list[LetterQueueItem]:
    """Letters waiting for the caller's step, inside the caller's scope."""
    if actor.role not in ZONE_REVIEWERS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes cola de validación de cartas")
    stages = [SUBMITTED] if actor.role not in ASSOCIATION_REVIEWERS else [SUBMITTED, ZONE_VALIDATED]
    if status_filter:
        if status_filter not in stages:
            return []
        stages = [status_filter]

    conditions = [ChurchLetter.status.in_(stages), ChurchLetter.user_id != actor.id]
    if not is_master(actor):
        paths = await club_scope_paths(db, actor)
        if not paths:
            return []
        in_scope = select(Organization.id).where(
            or_(*[Organization.path.op("<@")(path) for path in paths])
        )
        conditions.append(ChurchLetter.organization_id.in_(in_scope))

    rows = (
        await db.execute(
            select(ChurchLetter, User, Organization)
            .join(User, User.id == ChurchLetter.user_id)
            .outerjoin(Organization, Organization.id == ChurchLetter.organization_id)
            .where(*conditions)
            .order_by(ChurchLetter.updated_at, ChurchLetter.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [
        LetterQueueItem(
            **_letter_out(letter).model_dump(),
            user=PersonRef(id=str(applicant.id), name=applicant.name),
            organization_name=organization.name if organization else None,
        )
        for letter, applicant, organization in rows
    ]


async def review(
    db: AsyncSession, actor: User, letter_id: uuid.UUID, payload: LetterReviewIn, request: Request | None
) -> LetterOut:
    letter = await _get_letter(db, letter_id, lock=True)
    if letter.user_id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nadie valida su propia carta")
    if not await org_in_review_scope(db, actor, letter.organization_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Esta carta está fuera de tu alcance")
    if letter.status not in ACTION_SOURCES[payload.action]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"No se puede {payload.action} una carta en estado {letter.status}"
        )
    if actor.role not in STAGE_REVIEWERS[letter.status]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No decides cartas en el escalón {letter.status}"
        )

    now = utcnow()
    previous = letter.status
    if payload.action == "VALIDATE":
        letter.status = ZONE_VALIDATED
        letter.zone_validated_by_id, letter.zone_validated_at = actor.id, now
    elif payload.action == "AUTHORIZE":
        if payload.valid_until is not None and payload.valid_until < now.date():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "La fecha de vigencia ya pasó"
            )
        letter.status = AUTHORIZED
        letter.valid_until = payload.valid_until
        letter.decided_by_id, letter.decided_at = actor.id, now
    else:
        letter.status = REJECTED if payload.action == "REJECT" else REVOKED
        letter.decided_by_id, letter.decided_at = actor.id, now
    letter.decision_note = payload.note
    letter.updated_at = now

    record_audit(
        db,
        action=AUDIT_ACTIONS[payload.action],
        entity_type=ENTITY,
        entity_id=letter.id,
        actor=actor,
        details=payload.note,
        metadata={
            "user_id": str(letter.user_id),
            "role_requested": letter.role_requested,
            "from": previous,
            "to": letter.status,
            "valid_until": letter.valid_until.isoformat() if letter.valid_until else None,
        },
        request=request,
    )
    await db.commit()
    return _letter_out(letter)
