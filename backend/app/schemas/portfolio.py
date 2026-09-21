"""Portfolio schemas: enrollment, progress per requirement, evidence, verdicts, certificate."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.locales import LOCALE_PATTERN

EnrollmentStatus = Literal["IN_PROGRESS", "READY", "CERTIFIED", "WITHDRAWN"]
QueueStatus = Literal["SUBMITTED", "READY"]
EvidenceContentType = Literal["image/jpeg", "image/png", "image/webp", "application/pdf"]
NOTE_MAX_LENGTH = 2000


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------
class EnrollmentCreate(BaseModel):
    honor_id: uuid.UUID
    locale: str | None = Field(default=None, pattern=LOCALE_PATTERN, max_length=35)


class RequirementUpdate(BaseModel):
    # The member only sends or takes back; COMPLETE / INCOMPLETE belong to the reviewer.
    # Without `status` it is a draft: the answer typed in the card saves itself, nothing is sent.
    status: Literal["SUBMITTED", "PENDING"] | None = None
    member_note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)


class EvidenceCreate(BaseModel):
    content_type: EvidenceContentType
    size_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    taken_on: date | None = None
    place: str | None = Field(default=None, max_length=180)
    caption: str | None = Field(default=None, max_length=500)

    _clean = field_validator("place", "caption", mode="before")(_blank_to_none)


class ReviewIn(BaseModel):
    verdict: Literal["COMPLETE", "INCOMPLETE"]
    note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)

    _clean = field_validator("note", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _incomplete_needs_a_note(self):
        if self.verdict == "INCOMPLETE" and not self.note:
            raise ValueError("Un dictamen INCOMPLETE necesita una observación para el miembro.")
        return self


class CertificateIssue(BaseModel):
    # Slug of a server template (GET /certificates/templates); default in the service.
    template: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    issued_date: date
    place: str | None = Field(default=None, max_length=180)
    instructor_name: str | None = Field(default=None, max_length=180)

    _clean = field_validator("place", "instructor_name", mode="before")(_blank_to_none)


# ----------------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------------
class PersonRef(BaseModel):
    id: str
    name: str


class ClubRef(BaseModel):
    id: str
    name: str


class HonorRef(BaseModel):
    id: str
    name: str
    slug: str
    image_url: str | None


class Counters(BaseModel):
    total: int
    complete: int
    submitted: int
    incomplete: int


class EvidenceOut(BaseModel):
    """Metadata only. The file is reached through GET /evidences/{id}/url, never from here."""
    id: str
    kind: str
    status: str
    content_type: str
    size_bytes: int
    taken_on: date | None
    place: str | None
    caption: str | None
    created_at: datetime


class UploadTarget(BaseModel):
    url: str
    method: Literal["PUT"]
    headers: dict[str, str]
    expires_in: int


class EvidenceUpload(BaseModel):
    evidence: EvidenceOut
    upload: UploadTarget


class SignedUrl(BaseModel):
    url: str
    expires_in: int


class CertificateOut(BaseModel):
    id: str
    certificate_no: str
    recipient_name: str
    honor_id: str | None
    honor_name_snapshot: str
    club_name_snapshot: str | None
    issued_date: date
    place: str | None
    instructor_name: str | None
    director_name: str | None
    status: str
    certificate_hash: str | None
    # Slug to hand to POST /certificates/render; None for certificates without a server template.
    template: str | None
    user_id: str | None
    enrollment_id: str | None
    issued_by_id: str | None
    issued_role: str | None


class RequirementOut(BaseModel):
    position: int
    requirement_id: str | None
    description: str | None
    instructions: str | None
    is_practical: bool
    status: str
    completed_via: str | None
    member_note: str | None
    submitted_at: datetime | None
    reviewed_by: PersonRef | None
    reviewed_at: datetime | None
    review_note: str | None
    evidences: list[EvidenceOut]


class Permissions(BaseModel):
    is_owner: bool
    can_review: bool
    can_issue: bool


class EnrollmentSummary(BaseModel):
    id: str
    status: str
    mode: str
    locale: str
    user: PersonRef
    # None = no approved club: nobody can review this enrollment yet.
    club: ClubRef | None
    honor: HonorRef
    counters: Counters
    certificate: CertificateOut | None
    started_at: datetime
    ready_at: datetime | None
    certified_at: datetime | None
    withdrawn_at: datetime | None
    updated_at: datetime


class EnrollmentDetail(EnrollmentSummary):
    requirements: list[RequirementOut]
    permissions: Permissions


class QueueRequirement(BaseModel):
    """status=SUBMITTED: one requirement waiting for a verdict."""
    enrollment_id: str
    position: int
    is_practical: bool
    member: PersonRef
    honor: HonorRef
    member_note: str | None
    submitted_at: datetime | None
    evidence_count: int


class QueueReady(BaseModel):
    """status=READY: an enrollment waiting for its certificate."""
    enrollment_id: str
    member: PersonRef
    honor: HonorRef
    ready_at: datetime | None
    can_issue: bool


class PortfolioUser(BaseModel):
    id: str
    name: str
    avatar_url: str | None
    club: ClubRef | None


class PortfolioOut(BaseModel):
    user: PortfolioUser
    enrollments: list[EnrollmentSummary]
    certificates: list[CertificateOut]
