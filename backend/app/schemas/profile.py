"""Public profile, XP and the «barra de buena conducta» (Bloque G).

Spec: docs/superpowers/specs/2026-09-23-perfil-publico.md §3 and §4.1.

`PublicProfile` is what a stranger may read, so it is built field by field from the
service and NEVER from the `User` row: no e-mail, no birth date, no phone, no exact
location. The club is its name and city, nothing more.
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ProfileVisibility = Literal["private", "club", "public"]
AwardCategory = Literal["conducta", "puntualidad", "uniforme", "participacion", "servicio", "otro"]
MasterGuideStatus = Literal["none", "in_progress", "invested"]


class ProfileClub(BaseModel):
    id: str
    name: str
    city: str | None = None


class ProfileAssociation(BaseModel):
    id: str
    name: str


class ProfileClass(BaseModel):
    program_id: str
    name: str
    image_url: str | None = None
    progress_pct: int


class ProfileMasterGuide(BaseModel):
    status: MasterGuideStatus
    progress_pct: int


class ProfileStats(BaseModel):
    honors_earned: int
    honors_in_progress: int
    service_hours: float
    attendance: int


class ProfileXp(BaseModel):
    # Only for the person, their club's staff and the hierarchy above; a public viewer gets
    # the level and never the number (spec §4).
    total: int | None = None
    level: int
    level_name: str
    next_level_at: int | None = None


class ProfileBadge(BaseModel):
    key: str
    name: str
    description: str
    image_url: str | None = None
    earned_at: date | None = None


class HonorEarned(BaseModel):
    honor_id: str
    name: str
    image_url: str | None = None
    certificate_no: str
    issued_date: date
    verify_url: str


class HonorInProgress(BaseModel):
    honor_id: str
    name: str
    image_url: str | None = None
    progress_pct: int


class PublicProfile(BaseModel):
    id: str
    handle: str | None
    name: str
    avatar_url: str | None = None
    cover_url: str | None = None
    bio: str | None = None
    club: ProfileClub | None = None
    association: ProfileAssociation | None = None
    # `class` is a Python keyword: the attribute is `class_`, the JSON key is `class`.
    class_: ProfileClass | None = Field(default=None, serialization_alias="class")
    master_guide: ProfileMasterGuide
    stats: ProfileStats
    xp: ProfileXp
    badges: list[ProfileBadge]
    honors_earned: list[HonorEarned]
    honors_in_progress: list[HonorInProgress]
    visibility: ProfileVisibility
    is_me: bool
    can_edit: bool


class ConductBar(BaseModel):
    """0–100, starts at 70, moved by `conducta` + `puntualidad` + `uniforme` of the last
    8 weeks. Never public, never compared between people."""

    score: int
    start: int
    window_weeks: int


class MyProfile(PublicProfile):
    is_minor: bool
    guardian_allows_avatar: bool
    handle_changed_at: datetime | None = None
    # When the handle may change again (30 days after the last change); None = now.
    handle_locked_until: datetime | None = None
    conduct: ConductBar


class ProfileUpdate(BaseModel):
    """`PATCH /users/me/profile`. Only the fields present are applied; `null` clears
    `bio`, `avatar_url` and `cover_url`. The rules that answer with a short code (handle,
    minors, media URLs) live in the service, not here."""

    name: str | None = Field(default=None, min_length=1, max_length=180)
    handle: str | None = Field(default=None, max_length=64)
    bio: str | None = Field(default=None, max_length=280)
    avatar_url: str | None = Field(default=None, max_length=2048)
    cover_url: str | None = Field(default=None, max_length=2048)
    profile_visibility: ProfileVisibility | None = None

    model_config = {"extra": "forbid"}


# ----------------------------------------------------------------------------
# XP awards (§4.1)
# ----------------------------------------------------------------------------
class XpAwardCreate(BaseModel):
    category: AwardCategory
    points: int = Field(ge=-50, le=50)
    note: str | None = Field(default=None, max_length=200)
    # Default: today (UTC). Never in the future, at most 90 days back (checked in the service).
    occurred_on: date | None = None

    model_config = {"extra": "forbid"}


class XpAwardOut(BaseModel):
    id: str
    user_id: str
    club_id: str
    category: str
    points: int
    note: str | None = None
    occurred_on: date
    created_at: datetime
    awarded_by_name: str | None = None


class XpAwardCreated(XpAwardOut):
    # The positive points of that ISO week in this club after this award, and what is left.
    week_positive_points: int
    week_cap_remaining: int
    conduct: ConductBar


class XpBySource(BaseModel):
    requirements: int
    honors: int
    investitures: int
    service: int
    attendance: int
    courses: int
    awards: int


class XpAwardPage(BaseModel):
    items: list[XpAwardOut]
    total: int
    limit: int
    offset: int


class MyXp(BaseModel):
    xp: ProfileXp
    by_source: XpBySource
    conduct: ConductBar
    awards: XpAwardPage


class ClubXpMember(BaseModel):
    membership_id: str
    user_id: str
    name: str
    handle: str | None = None
    unit_id: str | None = None
    points_week: int
    positive_points_week: int
    cap_remaining: int
    conduct: int


class ClubXpUnit(BaseModel):
    unit_id: str
    name: str
    points_week: int


class ClubXpSummary(BaseModel):
    week: str
    starts_on: date
    ends_on: date
    club_points_week: int
    members: list[ClubXpMember]
    # Ranking by TEAM (unit), never by person (spec §4.1).
    units: list[ClubXpUnit]
