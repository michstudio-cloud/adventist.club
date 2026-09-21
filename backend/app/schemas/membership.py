"""Club membership schemas (Bloque E).

Privacy note (spec §7): no serializer here ever carries a birth date, and the
e-mail of a minor never leaves the backend. The roster shows years of age and,
for the director alone, the address consent was asked at.
"""
import uuid as uuid_module
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

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


# ----------------------------------------------------------------------------
# Invitations (E3)
# ----------------------------------------------------------------------------
# What a link may grant today. CLUB_SECRETARY is appointed from the roster
# until E8 opens invitations to it (spec §5.7).
InvitableRole = Literal["STUDENT", "COUNSELOR", "INSTRUCTOR"]


class InvitationCreate(BaseModel):
    role: InvitableRole = "STUDENT"
    max_uses: int = Field(default=1, ge=1, le=200)
    expires_in_days: int | None = Field(default=None, ge=1, le=90)
    # Nominal invitation: only the account with this address may accept it.
    email: EmailStr | None = None

    model_config = {"extra": "forbid"}


class InvitationOut(BaseModel):
    """An invitation as the club sees it. The token is NEVER here: it is shown
    once, in the answer to the request that created the link."""

    id: str
    club_id: str
    role: str
    email: str | None = None
    max_uses: int
    uses: int
    expires_at: datetime
    state: str
    requires_approval: bool
    created_by: str | None = None
    created_at: datetime


class InvitationCreated(BaseModel):
    invitation: InvitationOut
    # Shown exactly once. Losing it costs a new invitation, never a lookup.
    token: str
    url: str
    whatsapp_url: str


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=512)

    model_config = {"extra": "forbid"}


class InvitationPreview(BaseModel):
    """The public face of a link. It never names a person (spec §7)."""

    club: ClubRef
    role: str
    requires_approval: bool
    expires_at: datetime


class InvitationAccept(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    # Minors: where to ask for the authorization.
    guardian_email: EmailStr | None = None
    # Leaving another club is never a side effect: the person says so.
    confirm_transfer: bool = False

    model_config = {"extra": "forbid"}


# ----------------------------------------------------------------------------
# Guardian consent (E3)
# ----------------------------------------------------------------------------
class ConsentClub(ClubRef):
    director_name: str | None = None


class ChildRef(BaseModel):
    """Name and age only: a consent screen never needs more."""

    id: str
    name: str
    age: int | None = None


class ConsentPreview(BaseModel):
    membership_id: str
    child: ChildRef
    club: ConsentClub
    role: str
    # Plain words for the guardian: exactly what the club gets to see.
    club_will_see: list[str]
    seen_by: list[str]
    expires_at: datetime | None = None


class ConsentDecision(BaseModel):
    """Either the emailed token or, for a guardian who already has an approved
    guardianship over the minor, the membership itself."""

    token: str | None = Field(default=None, min_length=10, max_length=512)
    membership_id: uuid_module.UUID | None = None
    decision: Literal["APPROVE", "REJECT"]
    relationship: Literal["PARENT", "LEGAL_GUARDIAN", "OTHER"] = "PARENT"

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _one_reference(self):
        if bool(self.token) == bool(self.membership_id):
            raise ValueError("Provide either `token` or `membership_id`")
        return self


class ConsentResend(BaseModel):
    guardian_email: EmailStr | None = None

    model_config = {"extra": "forbid"}


# ----------------------------------------------------------------------------
# Join requests (E4)
# ----------------------------------------------------------------------------
class JoinRequestCreate(BaseModel):
    club_id: uuid_module.UUID
    message: str | None = Field(default=None, max_length=500)
    # Minors: where to ask for the authorization.
    guardian_email: EmailStr | None = None
    # Leaving another club is never a side effect: the person says so.
    confirm_transfer: bool = False

    model_config = {"extra": "forbid"}

    @field_validator("message")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None


class JoinRequestOut(BaseModel):
    """A request as the person who made it sees it."""

    membership_id: str
    club: ClubRef
    role: str
    status: str
    message: str | None = None
    created_at: datetime


class RequestRow(BaseModel):
    """A line of the club's queue. Age in years, never a birth date, no e-mail.
    A minor only ever appears here once a guardian has authorized them."""

    membership_id: str
    user_id: str
    name: str
    role: str
    status: str
    is_minor: bool
    age: int | None = None
    message: str | None = None
    source: str
    created_at: datetime
    consent: ConsentSummary | None = None


class RequestDecision(BaseModel):
    """Approving may grant a different role, within what the actor may give."""

    role: ClubRole | None = None
    reason: str | None = Field(default=None, max_length=1000)

    model_config = {"extra": "forbid"}

    @field_validator("reason")
    @classmethod
    def _trimmed_reason(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None


class BulkApproval(BaseModel):
    approved: int


class MembershipEnded(BaseModel):
    membership_id: str
    club_id: str
    status: str
    end_reason: str | None = None


def as_club_ref(club, model=ClubRef, **extra):
    metadata = club.metadata_json or {}
    return model(
        id=str(club.id), name=club.name, city=club.city, church=metadata.get("church"), **extra
    )


def as_invitation_out(invitation, *, state: str, requires_approval: bool) -> InvitationOut:
    return InvitationOut(
        id=str(invitation.id),
        club_id=str(invitation.club_id),
        role=invitation.role,
        email=invitation.email,
        max_uses=invitation.max_uses,
        uses=invitation.uses,
        expires_at=invitation.expires_at,
        state=state,
        requires_approval=requires_approval,
        created_by=str(invitation.created_by_id) if invitation.created_by_id else None,
        created_at=invitation.created_at,
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
    "BulkApproval",
    "ChildRef",
    "JoinRequestCreate",
    "JoinRequestOut",
    "RequestDecision",
    "RequestRow",
    "ClubProfileOut",
    "ClubProfileUpdate",
    "ClubRef",
    "ClubRole",
    "ConsentClub",
    "ConsentDecision",
    "ConsentPreview",
    "ConsentResend",
    "ConsentSummary",
    "InvitableRole",
    "InvitationAccept",
    "InvitationCreate",
    "InvitationCreated",
    "InvitationOut",
    "InvitationPreview",
    "ManagedMemberRow",
    "MemberRemoval",
    "MemberRoleUpdate",
    "MemberRow",
    "MembershipEnded",
    "MembershipOut",
    "MembershipStatus",
    "MyMembership",
    "TokenIn",
    "as_club_ref",
    "as_invitation_out",
    "as_membership_out",
]
