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
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import violated_constraint
from app.models import ChurchLetter, Organization, User
from app.rbac import (
    club_scope_paths,
    instructor_is_verified,
    is_master,
    is_verified_leader,
    org_in_review_scope,
)
from app.schemas.church_letter import (
    DECIDED_STATUSES,
    LetterCreate,
    LetterOut,
    LetterQueueItem,
    LetterReviewIn,
    LetterUpload,
    VerificationChecklist,
)
from app.schemas.portfolio import PersonRef, SignedUrl, UploadTarget
from app.security import CLUB_DIRECTOR, CLUB_SECRETARY, COUNSELOR, INSTRUCTOR, utcnow
from app.services import notifications, private_storage
from app.services.audit import record_audit
from app.workflow import ASSOCIATION_REVIEWERS, ZONE_REVIEWERS

PENDING_UPLOAD = "PENDING_UPLOAD"
SUBMITTED = "SUBMITTED"
ZONE_VALIDATED = "ZONE_VALIDATED"
AUTHORIZED = "AUTHORIZED"
REJECTED = "REJECTED"
REVOKED = "REVOKED"
LIVE_STATUSES = (SUBMITTED, ZONE_VALIDATED, AUTHORIZED)

# Offices a church letter may back. E7 widens the tuple as the spec foresaw: the column is
# varchar(40) and the rule lives here, so no migration was needed and there is ONE letters
# table in the platform. The secretary is not REQUIRED to present one (they never rule on
# evidence) but may, because a secretary can be the counselor of a unit (§5.4).
LETTER_ROLES = (INSTRUCTOR, CLUB_DIRECTOR, COUNSELOR, CLUB_SECRETARY)

# Decision D5: 12 months, renewable from 60 days before, never more than 24.
DEFAULT_VALIDITY_DAYS = 365
MAX_VALIDITY_DAYS = 730
RENEWAL_WINDOW_DAYS = 60
EXPIRY_NOTICE_DAYS = 30

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


RENEWAL_TOO_EARLY = (
    f"Tu carta sigue vigente: podrás renovarla desde {RENEWAL_WINDOW_DAYS} días antes de que venza."
)
SUPERSEDED_NOTE = "Sustituida por una renovación de la misma persona"


def _renewable(live: ChurchLetter) -> bool:
    """Decision D5: a renewal may START 60 days before the letter expires.

    Until then the answer is «not yet»: one live letter per person and office
    is exactly what the unique index of `009` guarantees.
    """
    if live.status != AUTHORIZED:
        return False
    if live.valid_until is None:
        return False
    return live.valid_until <= utcnow().date() + timedelta(days=RENEWAL_WINDOW_DAYS)


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
    """What is still missing for this person's office. `verified` keeps meaning
    what Bloque B's badge means for an instructor; for a club office it is the
    single gate of E7, `rbac.is_verified_leader`."""
    role = actor.role if actor.role in LETTER_ROLES else INSTRUCTOR
    letter = await _latest_letter(db, actor.id, role)
    verified = (
        await instructor_is_verified(db, actor)
        if role == INSTRUCTOR
        else is_verified_leader(actor)
    )
    return VerificationChecklist(
        role=actor.role,
        email_verified=actor.verification_status == "VERIFIED",
        child_protection=actor.child_protection_completed,
        letter=_letter_out(letter) if letter else None,
        verified=verified,
        valid_until=letter.valid_until if letter and letter.status == AUTHORIZED else None,
        expires_soon=_expires_soon(actor),
    )


def _expires_soon(user: User) -> bool:
    """The panel warns from 30 days before; the e-mail is sent by the Cron
    script `migrations/notify_expiring_letters.py` (decision D5)."""
    if user.leader_verified_until is None:
        return False
    today = utcnow().date()
    return today <= user.leader_verified_until <= today + timedelta(days=EXPIRY_NOTICE_DAYS)


async def create(
    db: AsyncSession,
    actor: User,
    payload: LetterCreate,
    request: Request | None,
    role_requested: str | None = None,
) -> LetterUpload:
    """Reserve the letter and hand out the presigned PUT. It only counts once `complete`
    has seen the object in the bucket, exactly like an evidence."""
    _require_private_storage()
    # E7: the office defaults to the one the person actually holds.
    role_requested = role_requested or payload.role_requested or actor.role
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
    live = await _live_letter(db, actor.id, role_requested)
    if live is not None and not _renewable(live):
        raise HTTPException(status.HTTP_409_CONFLICT, RENEWAL_TOO_EARLY if live.status == AUTHORIZED
                            else "Ya tienes una carta en trámite o autorizada")

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
    db: AsyncSession, actor: User, letter_id: uuid.UUID, request: Request | None, background=None
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

    # E7 renewal: one live letter per person and office, so the one being
    # replaced is closed here. `users.leader_verified_until` is NOT touched —
    # the verification already earned lasts until its own date, which is the
    # whole point of keeping the date on the account and not on the row.
    previous = await _live_letter(db, actor.id, letter.role_requested)
    if previous is not None and previous.id != letter.id:
        if not _renewable(previous):
            raise HTTPException(status.HTTP_409_CONFLICT, RENEWAL_TOO_EARLY)
        previous.status = REVOKED
        previous.decision_note = SUPERSEDED_NOTE
        previous.decided_by_id = actor.id
        previous.decided_at = utcnow()
        previous.updated_at = utcnow()
        # Written BEFORE the new row becomes SUBMITTED: the unique index of
        # `009` allows exactly one live letter per person and office.
        await db.flush()
        record_audit(
            db,
            action="LETTER_REVOKE",
            entity_type=ENTITY,
            entity_id=previous.id,
            actor=actor,
            details=SUPERSEDED_NOTE,
            metadata={"superseded_by": str(letter.id), "role_requested": letter.role_requested},
            request=request,
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
    # Staged inside this transaction, sent after its commit: a mail outage can
    # never fail the upload that caused it (the pattern of `org.py`).
    await notifications.queue_letter_submitted(db, background, letter=letter, applicant=actor)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != LIVE_LETTER_CONSTRAINT:
            raise
        # Another upload of the same person got there first.
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya tienes una carta en trámite o autorizada") from exc
    return _letter_out(letter)


async def get_one(db: AsyncSession, actor: User, letter_id: uuid.UUID) -> LetterQueueItem:
    """One letter, for its owner or a reviewer with scope over it.

    Exactly the audience of `GET /{id}/url`, and exactly as much: metadata, who presents it
    and where they belong. The document itself still needs the signed URL, and the storage
    key never leaves this service.

    404 rather than 403 for everybody else: a letter is a personal document, and confirming
    that this id exists would already say that somebody presented one.
    """
    letter = await _get_letter(db, letter_id)
    if letter.user_id != actor.id and not await org_in_review_scope(
        db, actor, letter.organization_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carta no encontrada")
    applicant = await db.get(User, letter.user_id)
    organization = await db.get(Organization, letter.organization_id)
    return LetterQueueItem(
        **_letter_out(letter).model_dump(),
        user=PersonRef(
            id=str(letter.user_id), name=applicant.name if applicant else "—"
        ),
        organization_name=organization.name if organization else None,
    )


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
    """Letters inside the caller's scope: by default the ones waiting for their step, and
    with `status=` also the ones already decided.

    Why the decided ones are here at all: the UI could validate, authorize and reject, but
    never REVOKE, because nothing in the API ever returned an AUTHORIZED letter and the
    screen had nothing to open. A reviewer who could decide on a letter keeps reading it
    afterwards — they saw the document and the applicant already, so this widens what is
    listed, never who may see it. The act itself is still gated by `review`: only an
    association reviewer revokes.
    """
    if actor.role not in ZONE_REVIEWERS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes cola de validación de cartas")
    stages = [SUBMITTED] if actor.role not in ASSOCIATION_REVIEWERS else [SUBMITTED, ZONE_VALIDATED]
    if status_filter:
        if status_filter not in (*stages, *DECIDED_STATUSES):
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
        # `club_scope_paths` is the coarse SQL filter; the last word is the rule
        # that actually decides, so E6's narrower zone scope also narrows the
        # queue: a coordinator never reads another zone's letters.
        if await org_in_review_scope(db, actor, letter.organization_id)
    ]


def _authorized_until(asked, today):
    """Decision D5: 12 months by default, never more than 24, never in the past.

    Bloque B allowed `NULL = no expiry`; a letter that backs an office over
    minors does not get to be eternal, so an omitted date now means 12 months.
    """
    if asked is None:
        return today + timedelta(days=DEFAULT_VALIDITY_DAYS)
    if asked < today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "La fecha de vigencia ya pasó")
    cap = today + timedelta(days=MAX_VALIDITY_DAYS)
    if asked > cap:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Una carta no puede valer más de 24 meses: los cargos de iglesia se nombran por periodo.",
        )
    return asked


def _write_verification(applicant: User, letter: ChurchLetter) -> None:
    """The copy `rbac.is_verified_leader` reads. Only ever moved FORWARD, so a
    second office of the same person cannot shorten a verification in force."""
    if applicant.leader_verified_until is None or (
        letter.valid_until is not None and letter.valid_until > applicant.leader_verified_until
    ):
        applicant.leader_verified_until = letter.valid_until


def _clear_verification(applicant: User, letter: ChurchLetter) -> None:
    """Rejecting or revoking takes the verification away at once. A rejection of
    a letter that was never authorized leaves an older, still valid one alone."""
    if letter.valid_until is not None and applicant.leader_verified_until == letter.valid_until:
        applicant.leader_verified_until = None
    elif letter.status == REVOKED:
        applicant.leader_verified_until = None


async def review(
    db: AsyncSession,
    actor: User,
    letter_id: uuid.UUID,
    payload: LetterReviewIn,
    request: Request | None,
    background=None,
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

    applicant = await db.get(User, letter.user_id)
    now = utcnow()
    previous = letter.status
    if payload.action == "VALIDATE":
        letter.status = ZONE_VALIDATED
        letter.zone_validated_by_id, letter.zone_validated_at = actor.id, now
    elif payload.action == "AUTHORIZE":
        letter.valid_until = _authorized_until(payload.valid_until, now.date())
        letter.status = AUTHORIZED
        letter.decided_by_id, letter.decided_at = actor.id, now
        if applicant is not None:
            # Whoever validates the letter may tick the child protection course
            # in the same act (spec §5.6); the flag itself is still an
            # administrator's to give through POST /users/{id}/child-protection-cert.
            if payload.child_protection_completed and not applicant.child_protection_completed:
                applicant.child_protection_completed = True
                applicant.child_protection_completed_at = now
            _write_verification(applicant, letter)
    else:
        letter.status = REJECTED if payload.action == "REJECT" else REVOKED
        letter.decided_by_id, letter.decided_at = actor.id, now
        if applicant is not None:
            _clear_verification(applicant, letter)
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
    if payload.action != "VALIDATE":
        # The zone step is internal; the person hears about the final answer.
        await notifications.queue_letter_decision(
            db, background, letter=letter, applicant=applicant
        )
    await db.commit()
    return _letter_out(letter)
