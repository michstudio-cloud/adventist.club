"""Bloque H — Secretaría del club: cargos, reuniones, asistencia, página pública y puntuación.

Spec: docs/superpowers/specs/2026-09-23-secretaria-club.md. Nothing here carries an e-mail,
a birth date or a guardian: the roster's privacy rules (spec E §7) hold for every row.
"""
import uuid as uuid_module
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.org import OrgRef
from app.schemas.unit import UnitRef

OfficerTitle = Literal[
    "DIRECTOR", "SUBDIRECTOR", "SECRETARIO", "TESORERO", "CAPELLAN", "CONSEJERO", "INSTRUCTOR", "OTRO"
]
OTHER_TITLE = "OTRO"
MeetingKind = Literal["REUNION", "CAMPAMENTO", "SERVICIO", "OTRO"]
AttendanceStatus = Literal["PRESENT", "ABSENT", "JUSTIFIED"]
MAX_ENTRIES = 500

CUSTOM_TITLE_RULE = "custom_title sólo se usa con el cargo OTRO, que siempre lo necesita"


def _squeeze(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


# ----------------------------------------------------------------------------
# 1. Cargos
# ----------------------------------------------------------------------------
class OfficerCreate(BaseModel):
    membership_id: uuid_module.UUID
    title: OfficerTitle
    custom_title: str | None = Field(default=None, max_length=60)
    since: date | None = None

    model_config = {"extra": "forbid"}

    _clean = field_validator("custom_title", mode="before")(_squeeze)

    @model_validator(mode="after")
    def _custom_title_only_with_other(self):
        if (self.title == OTHER_TITLE) != (self.custom_title is not None):
            raise ValueError(CUSTOM_TITLE_RULE)
        return self


class OfficerUpdate(BaseModel):
    until: date | None = None
    custom_title: str | None = Field(default=None, max_length=60)

    model_config = {"extra": "forbid"}

    _clean = field_validator("custom_title", mode="before")(_squeeze)


class OfficerOut(BaseModel):
    id: str
    club_id: str
    membership_id: str
    user_id: str
    name: str
    title: str
    custom_title: str | None = None
    since: date
    until: date | None = None
    active: bool


class PublicOfficer(BaseModel):
    """What the internet sees of a cargo: the title and the name, never an e-mail."""

    title: str
    custom_title: str | None = None
    name: str


# ----------------------------------------------------------------------------
# 2. Reuniones y asistencia
# ----------------------------------------------------------------------------
class MeetingCreate(BaseModel):
    held_on: date
    kind: MeetingKind = "REUNION"
    title: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}

    _clean = field_validator("title", "notes", mode="before")(_squeeze)


class MeetingCounts(BaseModel):
    present: int = 0
    absent: int = 0
    justified: int = 0


class MeetingOut(BaseModel):
    id: str
    club_id: str
    held_on: date
    kind: str
    title: str | None = None
    notes: str | None = None
    created_at: datetime
    counts: MeetingCounts


class MeetingAttendee(BaseModel):
    membership_id: str
    user_id: str
    name: str
    unit: UnitRef | None = None
    status: AttendanceStatus | None = None


class MeetingDetail(MeetingOut):
    attendees: list[MeetingAttendee]


class AttendanceEntry(BaseModel):
    membership_id: uuid_module.UUID
    status: AttendanceStatus

    model_config = {"extra": "forbid"}


class AttendanceUpdate(BaseModel):
    """The whole list of the meeting (within what the caller may record): it REPLACES it."""

    entries: list[AttendanceEntry] = Field(default_factory=list, max_length=MAX_ENTRIES)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _one_entry_per_member(self):
        ids = [entry.membership_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Cada miembro aparece una sola vez en la lista")
        return self


class AttendanceSummaryMember(BaseModel):
    membership_id: str
    user_id: str
    name: str
    unit_id: str | None = None
    meetings: int
    present: int
    pct: float | None = None


class AttendanceSummaryUnit(BaseModel):
    unit_id: str
    name: str
    meetings: int
    present: int
    pct: float | None = None


class AttendanceSummary(BaseModel):
    starts_on: date
    ends_on: date
    meetings: int
    members: list[AttendanceSummaryMember]
    units: list[AttendanceSummaryUnit]


class ServiceHoursMember(BaseModel):
    """Totals only: never the description or the place (where a minor was, and when)."""

    membership_id: str
    user_id: str
    name: str
    unit_id: str | None = None
    service_month: float
    service_year: float
    attendance_month: float
    # Service logs of this member still waiting for a decision (any date).
    pending: int


class ServiceHoursUnit(BaseModel):
    unit_id: str
    name: str
    members: int
    service_month: float


class ServiceHoursSummary(BaseModel):
    month: str
    starts_on: date
    ends_on: date
    service_month: float
    members: list[ServiceHoursMember]
    units: list[ServiceHoursUnit]


# ----------------------------------------------------------------------------
# 3. Nómina
# ----------------------------------------------------------------------------
class Completeness(BaseModel):
    """Flags, never the data: whether the record is complete."""

    birth_date: bool
    consent: bool
    guardian: bool
    email_verified: bool


# ----------------------------------------------------------------------------
# 5. Página pública del club
# ----------------------------------------------------------------------------
class ClubPublicProfile(BaseModel):
    id: str
    name: str
    church: str | None = None
    zone: OrgRef | None = None
    association: OrgRef | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    meeting_day: str | None = None
    meeting_time: str | None = None
    contact: str | None = None
    description: str | None = None
    logo_url: str | None = None
    officers: list[PublicOfficer]
    active_members: int
    accepts_requests: bool


# ----------------------------------------------------------------------------
# 6. Puntuación
# ----------------------------------------------------------------------------
class ScoreBySource(BaseModel):
    awards: int
    attendance: int
    badges: int


class ScoreUnit(BaseModel):
    unit_id: str | None = None
    name: str | None = None
    total: int


class ScoreMonth(BaseModel):
    month: str
    total: int


class ClubScore(BaseModel):
    club_id: str
    season: int
    total: int
    by_source: ScoreBySource
    units: list[ScoreUnit]
    months: list[ScoreMonth]
