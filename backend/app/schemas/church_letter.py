"""Church letter schemas: presentation, upload handshake, validation queue and decisions."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.portfolio import PersonRef, SignedUrl, UploadTarget  # noqa: F401  (shared shapes)

LetterStatus = Literal[
    "PENDING_UPLOAD", "SUBMITTED", "ZONE_VALIDATED", "AUTHORIZED", "REJECTED", "REVOKED"
]
# What `GET /queue?status=` accepts. The first two are the steps waiting for a decision;
# the last three are letters already decided, which the reviewers who could decide on them
# keep reading — it is the only way a screen reaches REVOKE on an AUTHORIZED letter, and
# the only way a rejection stays consultable.
QueueStatus = Literal["SUBMITTED", "ZONE_VALIDATED", "AUTHORIZED", "REJECTED", "REVOKED"]
DECIDED_STATUSES = ("AUTHORIZED", "REJECTED", "REVOKED")
LetterAction = Literal["VALIDATE", "AUTHORIZE", "REJECT", "REVOKE"]
# The offices a church letter may back. ONE table for the whole platform: the
# virtual instructor of Bloque B and the club offices of Bloque E (E7).
LetterRole = Literal["INSTRUCTOR", "CLUB_DIRECTOR", "COUNSELOR", "CLUB_SECRETARY"]
# Same whitelist as portfolio evidence: a scan or a photo of the signed letter.
LetterContentType = Literal["application/pdf", "image/jpeg", "image/png", "image/webp"]
NOTE_MAX_LENGTH = 2000


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------
class LetterCreate(BaseModel):
    church_name: str = Field(min_length=2, max_length=180)
    pastor_name: str | None = Field(default=None, max_length=180)
    content_type: LetterContentType
    size_bytes: int = Field(gt=0)
    # Bloque E: which office the letter backs. Omitted, it is the one the
    # person holds; the service checks it against `church_letters.LETTER_ROLES`.
    role_requested: LetterRole | None = None

    _clean = field_validator("pastor_name", mode="before")(_blank_to_none)


class LetterReviewIn(BaseModel):
    action: LetterAction
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    # Only read on AUTHORIZE. Omitted it means 12 months (E §D5); 24 is the cap.
    valid_until: date | None = None
    # Whoever validates the letter may tick the child protection course in the
    # same act (spec E §5.6). It never takes the flag away.
    child_protection_completed: bool | None = None

    _clean = field_validator("note", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _a_refusal_needs_a_reason(self):
        if self.action in ("REJECT", "REVOKE") and not self.note:
            raise ValueError("Rechazar o revocar una carta exige explicar el motivo.")
        return self


# ----------------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------------
class LetterOut(BaseModel):
    """Metadata only: the document itself is reached through GET /{id}/url."""

    id: str
    user_id: str
    role_requested: str
    organization_id: str
    church_name: str
    pastor_name: str | None
    status: str
    content_type: str
    size_bytes: int
    valid_until: date | None
    decision_note: str | None
    zone_validated_at: datetime | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LetterUpload(BaseModel):
    letter: LetterOut
    upload: UploadTarget


class LetterQueueItem(LetterOut):
    """What a reviewer needs to decide: who presents it and where they belong. Nothing
    else of the account travels here (no e-mail, no birth date)."""

    user: PersonRef
    organization_name: str | None


class VerificationChecklist(BaseModel):
    """`/teach` shows this; `verified` is `rbac.instructor_is_verified`, the single gate."""

    role: str
    email_verified: bool
    child_protection: bool
    letter: LetterOut | None
    verified: bool
    # E7: what the panel shows about the letter in force and its renewal.
    valid_until: date | None = None
    expires_soon: bool = False
