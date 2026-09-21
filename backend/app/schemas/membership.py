"""Club membership schemas (Bloque E).

Privacy note (spec §7): no serializer here ever carries a birth date, and the
e-mail of a minor never leaves the backend. The roster shows years of age and,
for the director alone, the address consent was asked at.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MembershipStatus = Literal[
    "PENDING_CONSENT", "PENDING_APPROVAL", "ACTIVE", "ENDED", "REJECTED", "CANCELLED"
]
# Every role a membership can hold. Which of them the caller may actually grant
# is decided in `rbac.can_grant_club_role`, never here: asking for
# CLUB_DIRECTOR has to come back as a 403 that says the association appoints it.
ClubRole = Literal["STUDENT", "COUNSELOR", "INSTRUCTOR", "CLUB_SECRETARY", "CLUB_DIRECTOR"]


class ClubRef(BaseModel):
    id: str
    name: str
    city: str | None = None
    church: str | None = None


class MembershipOut(BaseModel):
    """One membership as its own holder sees it."""

    membership_id: str
    club: ClubRef
    role: str
    status: str
    source: str
    since: datetime | None = None
    ended_at: datetime | None = None
    end_reason: str | None = None


class MyMembership(BaseModel):
    active: MembershipOut | None = None
    pending: list[MembershipOut] = Field(default_factory=list)


class ConsentSummary(BaseModel):
    status: str | None = None
    guardian_name: str | None = None


class MemberRow(BaseModel):
    """A line of the roster. Age in years, never the birth date; no e-mail."""

    membership_id: str
    user_id: str
    name: str
    role: str
    status: str
    is_minor: bool
    age: int | None = None
    since: datetime | None = None
    consent: ConsentSummary | None = None


class ManagedMemberRow(MemberRow):
    """The same line for whoever manages the club: they alone see the address
    the guardian's consent was asked at. Split into its own model on purpose,
    so the field is ABSENT from the payload of everybody else rather than null."""

    guardian_email: str | None = None


class MemberRoleUpdate(BaseModel):
    role: ClubRole

    model_config = {"extra": "forbid"}


class MemberRemoval(BaseModel):
    """The reason is mandatory: it reaches the member (and their guardian)."""

    reason: str = Field(min_length=3, max_length=1000)

    model_config = {"extra": "forbid"}

    @field_validator("reason")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise ValueError("reason must have at least 3 characters")
        return value


class ClubProfileUpdate(BaseModel):
    """«Datos de control» of a club: a whitelist that never touches the name,
    the location or the place of the club in the tree."""

    meeting_day: str | None = Field(default=None, max_length=40)
    meeting_time: str | None = Field(default=None, max_length=20)
    contact: str | None = Field(default=None, max_length=180)
    accepts_requests: bool | None = None

    model_config = {"extra": "forbid"}

    @field_validator("meeting_day", "meeting_time", "contact")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None


class ClubProfileOut(BaseModel):
    club_id: str
    name: str
    profile: dict


class MembershipEnded(BaseModel):
    membership_id: str
    club_id: str
    status: str
    end_reason: str | None = None


def as_club_ref(club) -> ClubRef:
    metadata = club.metadata_json or {}
    return ClubRef(
        id=str(club.id), name=club.name, city=club.city, church=metadata.get("church")
    )


def as_membership_out(membership, club) -> MembershipOut:
    return MembershipOut(
        membership_id=str(membership.id),
        club=as_club_ref(club),
        role=membership.role,
        status=membership.status,
        source=membership.source,
        since=membership.started_at,
        ended_at=membership.ended_at,
        end_reason=membership.end_reason,
    )


__all__ = [
    "ClubProfileOut",
    "ClubProfileUpdate",
    "ClubRef",
    "ConsentSummary",
    "ClubRole",
    "ManagedMemberRow",
    "MemberRemoval",
    "MemberRoleUpdate",
    "MemberRow",
    "MembershipEnded",
    "MembershipOut",
    "MembershipStatus",
    "MyMembership",
    "as_club_ref",
    "as_membership_out",
]
