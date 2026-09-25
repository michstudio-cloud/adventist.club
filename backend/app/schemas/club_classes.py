"""Bloque F · F3 — the club follows a class as a group (`/api/v1/clubs/{id}/classes`).

Privacy (spec F §1.10): the matrix shows a member's name, handle, unit and the state of each
requirement — never an e-mail, a birth date, a guardian or an answer. A minor's photo only
travels once a guardian allowed it (`rbac.visible_avatar`).
"""
import uuid as uuid_module
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.ministry import MinistryRef
from app.schemas.portfolio import SIGNATURE_MAX_LENGTH
from app.schemas.program import ProgramKind, ProgramRef
from app.schemas.unit import UnitRef

MAX_MEMBERS = 500
MAX_CELLS = 2000
NOTE_MAX = 200

ProgressStatus = Literal["PENDING", "SUBMITTED", "COMPLETE", "INCOMPLETE"]
EnrollSkipReason = Literal["already_enrolled", "not_active", "out_of_unit"]
SignSkipReason = Literal[
    "not_found", "out_of_reach", "own_enrollment", "closed", "already_complete", "hours_only"
]
InvestSkipReason = Literal["not_found", "not_ready", "already_invested", "out_of_reach"]


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# ----------------------------------------------------------------------------
# 1. The classes of the club
# ----------------------------------------------------------------------------
class ClubClass(BaseModel):
    program: ProgramRef
    enrolled: int          # live enrollments: in_progress + complete + invested
    complete: int          # READY: every requirement complete, waiting for the investiture
    invested: int          # CERTIFIED
    in_progress: int       # IN_PROGRESS


class AvailableClass(BaseModel):
    id: str
    slug: str
    name: str
    kind: ProgramKind
    image_url: str | None = None
    ministry: str | None = None


class ClubClasses(BaseModel):
    classes: list[ClubClass]
    available: list[AvailableClass]
    # The club's principal ministry (019_club_ministry.sql). None: the club declares none, so
    # `available` carries the classes of every ministry and the screen says so.
    ministry: MinistryRef | None = None
    # 022: every ministry of the club, principal first; `available` carries the classes of all.
    ministries: list[MinistryRef] = []


# ----------------------------------------------------------------------------
# 2. Enrol the club, a unit or a list
# ----------------------------------------------------------------------------
class ClassEnrollIn(BaseModel):
    membership_ids: list[uuid_module.UUID] | None = Field(default=None, max_length=MAX_MEMBERS)
    unit_id: uuid_module.UUID | None = None


class EnrolledMember(BaseModel):
    membership_id: str
    enrollment_id: str


class SkippedMember(BaseModel):
    membership_id: str
    reason: EnrollSkipReason


class ClassEnrollOut(BaseModel):
    enrolled: list[EnrolledMember]
    skipped: list[SkippedMember]


# ----------------------------------------------------------------------------
# 3. The matrix members × requirements
# ----------------------------------------------------------------------------
class MatrixProgram(BaseModel):
    id: str
    slug: str
    name: str


class MatrixRequirement(BaseModel):
    id: str
    position: int
    label: str
    kind: str


class MatrixSection(BaseModel):
    slug: str
    name: str
    requirements: list[MatrixRequirement]


class MatrixUser(BaseModel):
    id: str
    name: str
    handle: str | None = None
    avatar_url: str | None = None


class MatrixQuantity(BaseModel):
    """A `HOURS` requirement of one member: approved since the class started, and the goal."""

    approved: float
    target: float


class MatrixMember(BaseModel):
    membership_id: str
    enrollment_id: str
    user: MatrixUser
    unit: UnitRef | None = None
    status: str
    progress_pct: int
    cells: dict[str, ProgressStatus]
    # Only the `HOURS` requirements, by requirement id: «3 / 5 h» in the cell.
    hours: dict[str, MatrixQuantity] = Field(default_factory=dict)


class ClassMatrix(BaseModel):
    program: MatrixProgram
    sections: list[MatrixSection]
    members: list[MatrixMember]


# ----------------------------------------------------------------------------
# 4. «Firma en bloque»
# ----------------------------------------------------------------------------
class SignEntry(BaseModel):
    enrollment_id: uuid_module.UUID
    requirement_id: uuid_module.UUID


class BlockSignIn(BaseModel):
    entries: list[SignEntry] = Field(min_length=1, max_length=MAX_CELLS)
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    _clean = field_validator("note", mode="before")(_blank_to_none)


class SkippedCell(BaseModel):
    enrollment_id: str
    requirement_id: str
    reason: SignSkipReason


class BlockSignOut(BaseModel):
    signed: int
    skipped: list[SkippedCell]


# ----------------------------------------------------------------------------
# 5. Investidura
# ----------------------------------------------------------------------------
class InvestIn(BaseModel):
    enrollment_ids: list[uuid_module.UUID] = Field(min_length=1, max_length=MAX_MEMBERS)
    # Optional, as on the single certificate: today when omitted.
    issued_date: date | None = None
    place: str | None = Field(default=None, max_length=180)
    instructor_name: str | None = Field(default=None, max_length=180)
    template: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    # 021: as on the single certificate (CertificateIssue). The club screen sends the
    # director's own saved signature when there is one; it is prepared once for the batch.
    signature_director: str | None = Field(default=None, max_length=SIGNATURE_MAX_LENGTH)
    signature_instructor: str | None = Field(default=None, max_length=SIGNATURE_MAX_LENGTH)
    # 023: as on the single certificate — the investiture template's reworded phrases, the same
    # for every certificate of the batch.
    strings: dict[str, str] | None = Field(default=None, max_length=8)

    _clean = field_validator("place", "instructor_name", "signature_director", "signature_instructor",
                             mode="before")(_blank_to_none)


class InvestedEnrollment(BaseModel):
    enrollment_id: str
    certificate_no: str


class SkippedEnrollment(BaseModel):
    enrollment_id: str
    reason: InvestSkipReason


class InvestOut(BaseModel):
    invested: list[InvestedEnrollment]
    skipped: list[SkippedEnrollment]
