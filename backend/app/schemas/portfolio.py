"""Portfolio schemas: enrollment, progress per requirement, evidence, verdicts, certificate."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.program import (
    EnrollmentSection,
    ProgramRef,
    QuantityOut,
    RequirementTarget,
    SatisfiedBy,
)
from app.services.locales import LOCALE_PATTERN

EnrollmentStatus = Literal["IN_PROGRESS", "READY", "CERTIFIED", "WITHDRAWN"]
QueueStatus = Literal["SUBMITTED", "READY"]
EvidenceContentType = Literal["image/jpeg", "image/png", "image/webp", "application/pdf"]
NOTE_MAX_LENGTH = 2000
# 021: a signature travels as a data URL of <= 400 KB (base64: ~547 000 characters) or as the
# issuer's saved-signature URL. The real checks are in services/certificate_signatures.py.
SIGNATURE_MAX_LENGTH = 560_000


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------
class EnrollmentCreate(BaseModel):
    # Bloque F, rule 6: an honor OR a program, never both and never neither. `honor_id`
    # alone is still exactly the request block A accepts.
    honor_id: uuid.UUID | None = None
    program_id: uuid.UUID | None = None
    locale: str | None = Field(default=None, pattern=LOCALE_PATTERN, max_length=35)

    @model_validator(mode="after")
    def _exactly_one_curriculum(self):
        if (self.honor_id is None) == (self.program_id is None):
            raise ValueError("Envía honor_id o program_id, exactamente uno de los dos.")
        return self


class RequirementUpdate(BaseModel):
    # The member only sends or takes back; COMPLETE / INCOMPLETE belong to the reviewer.
    # Without `status` it is a draft: the answer typed in the card saves itself, nothing is sent.
    status: Literal["SUBMITTED", "PENDING"] | None = None
    member_note: str | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    # Bloque F · F2: the member's choice for an OPEN `HONOR` requirement ("an honor of
    # Nature"): which of their certified honors fills the slot.
    honor_enrollment_id: uuid.UUID | None = None


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
    # 016: the language the certificate is printed in; must be one the template speaks
    # (422 `locale_not_supported` otherwise). Spanish when not sent.
    locale: str = Field(default="es", pattern=r"^[a-z]{2}$")
    issued_date: date
    place: str | None = Field(default=None, max_length=180)
    instructor_name: str | None = Field(default=None, max_length=180)
    # 021: the handwritten signatures kept with the certificate. A data URL (drawn or uploaded
    # now) or the issuer's own saved signature (`users.signature_url`); anything else is 422.
    signature_director: str | None = Field(default=None, max_length=SIGNATURE_MAX_LENGTH)
    signature_instructor: str | None = Field(default=None, max_length=SIGNATURE_MAX_LENGTH)

    _clean = field_validator("place", "instructor_name", "signature_director", "signature_instructor",
                             mode="before")(_blank_to_none)


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


class CourseRef(BaseModel):
    """Bloque B · I3: the course a COURSE enrollment is being taken in. The instructor's
    name is public (they sign the certificate); nothing else of their account is."""

    id: str
    title: str
    instructor_name: str


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
    # 016: the same slug under the name the verify page uses, and the issued language.
    template_slug: str | None = None
    locale: str = "es"
    user_id: str | None
    enrollment_id: str | None
    issued_by_id: str | None
    issued_role: str | None
    # Bloque D · I7: annulling is never a delete, so a revoked certificate keeps showing up
    # in the portfolio — with its date and the reason its holder is entitled to read.
    revoked_at: datetime | None = None
    revocation_reason: str | None = None
    # 021: which signature lines carry a handwritten signature kept with the certificate
    # (`signature_director`, `signature_instructor`). The render fills them from the folio.
    signed: list[str] = Field(default_factory=list)


class RequirementOut(BaseModel):
    position: int
    # Bloque F: set only on a program enrollment; None for every honor (block A unchanged).
    label: str | None = None
    kind: str = "FREE"
    target: RequirementTarget | None = None
    satisfied_by: SatisfiedBy | None = None
    quantity: QuantityOut | None = None
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
    # Bloque B · I3: how the course evaluates it (EXAM | REVIEW | EVIDENCE); None in CLUB.
    assessment: str | None = None


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
    # Bloque F: `honor` on an honor enrollment, `program` on a program one — never both.
    # A program is NOT served as an honor anywhere, at any depth of any payload.
    honor: HonorRef | None
    type: Literal["honor", "program"] = "honor"
    program: ProgramRef | None = None
    counters: Counters
    certificate: CertificateOut | None
    started_at: datetime
    ready_at: datetime | None
    certified_at: datetime | None
    withdrawn_at: datetime | None
    updated_at: datetime
    # Bloque B · I3: set only while mode == "COURSE".
    course: CourseRef | None = None
    # Why the instructor removed the member from the course, until they join another.
    course_removed_reason: str | None = None


class EnrollmentDetail(EnrollmentSummary):
    requirements: list[RequirementOut]
    permissions: Permissions
    # Bloque F: the digital card groups the requirements above by section. None on an honor.
    sections: list[EnrollmentSection] | None = None


class QueueRequirement(BaseModel):
    """status=SUBMITTED: one requirement waiting for a verdict."""
    enrollment_id: str
    position: int
    is_practical: bool
    member: PersonRef
    honor: HonorRef | None
    type: Literal["honor", "program"] = "honor"
    program: ProgramRef | None = None
    member_note: str | None
    submitted_at: datetime | None
    evidence_count: int


class QueueReady(BaseModel):
    """status=READY: an enrollment waiting for its certificate."""
    enrollment_id: str
    member: PersonRef
    honor: HonorRef | None
    type: Literal["honor", "program"] = "honor"
    program: ProgramRef | None = None
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


# ----------------------------------------------------------------------------
# Bloque D · I7 — annulling a certificate
# ----------------------------------------------------------------------------
class CertificateRevokeIn(BaseModel):
    """The reason is mandatory (spec §5.5): a certificate carries somebody's name and a
    signature, so withdrawing it is always explained. It reaches the holder and the audit
    trail, never the public verification page."""

    reason: str = Field(min_length=3, max_length=NOTE_MAX_LENGTH)

    @field_validator("reason", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


# ----------------------------------------------------------------------------
# Álbum de evidencias: a folder per honor, and everything uploaded to one enrollment
# ----------------------------------------------------------------------------
class AlbumCategory(BaseModel):
    id: str
    name: str
    slug: str


class AlbumHonor(BaseModel):
    id: str
    name: str
    slug: str
    # The patch, shown as the album's sticker.
    image_url: str | None
    category: AlbumCategory | None


class AlbumCertificate(BaseModel):
    certificate_no: str
    issued_date: date
    # The public page the certificate's QR points at.
    verify_url: str
    # "issued" or "revoked": an annulled certificate keeps its folio (Bloque D · I7).
    status: str


class EvidencePreview(BaseModel):
    """One photo or PDF. `url` is a presigned GET on the PRIVATE bucket: a short-lived
    credential, never stored and never cached (the endpoints answer `no-store`)."""

    id: str
    requirement_id: str | None
    requirement_position: int
    content_type: str
    url: str
    # Same as `url` until thumbnails exist.
    thumbnail_url: str
    created_at: datetime


class EvidenceAlbum(BaseModel):
    enrollment_id: str
    honor: AlbumHonor
    status: str
    requirements_total: int
    requirements_done: int
    evidence_count: int
    # Up to four, newest first.
    latest: list[EvidencePreview]
    certificate: AlbumCertificate | None
    updated_at: datetime


class EvidenceItem(EvidencePreview):
    requirement_description: str | None
    # The requirement's status: PENDING | SUBMITTED | COMPLETE | INCOMPLETE.
    status: str
    # The member's caption of the photo.
    note: str | None
