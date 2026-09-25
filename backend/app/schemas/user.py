"""User and guardianship schemas."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

from app.models import Guardianship, User
from app.schemas.auth import RoleName
from app.schemas.membership import ClubRef
from app.schemas.org_invitation import ScopedRoleOut
from app.schemas.ministry import MinistryContext

UserStatus = Literal["ACTIVE", "SUSPENDED", "INACTIVE"]
VerificationStatus = Literal["PENDING", "VERIFIED", "REJECTED"]
Relationship = Literal["PARENT", "LEGAL_GUARDIAN", "OTHER"]


class ChildProtectionCert(BaseModel):
    completed: bool
    completed_at: datetime | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: str | None
    role: str
    organization_id: str | None
    # Legacy name of `organization_id`, kept for the existing front ends.
    org_node_id: str | None
    is_minor: bool
    birth_date: date | None
    mfa_enabled: bool
    verification_status: str
    child_protection_cert: ChildProtectionCert
    status: str
    # CLUB_DIRECTOR who requested a club: PENDING / APPROVED / REJECTED (+ reason).
    club_approval: str | None = None
    club_approval_reason: str | None = None
    created_at: datetime
    last_login: datetime | None
    # Bloque G: the public @handle and whether a guardian allows the minor's photo.
    handle: str | None = None
    guardian_allows_avatar: bool = False
    # 018: when the first-use guide was finished or skipped; `null` = the app should offer it.
    onboarding_completed_at: datetime | None = None
    # 020: the saved signature. Only in the person's own answers (`/auth/me`, `/users/me`):
    # a list of users or somebody else's record never carries it (`own=False`).
    signature_url: str | None = None
    # Bloque I §1.1: every role in force, the principal (`role`) first. Only in the
    # person's own answers (`/auth/me`, `/users/me`); `null` everywhere else.
    roles: list[ScopedRoleOut] | None = None

    @classmethod
    def from_model(cls, user: User, *, own: bool = False) -> "UserResponse":
        organization_id = str(user.organization_id) if user.organization_id else None
        return cls(
            id=str(user.id),
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            role=user.role,
            organization_id=organization_id,
            org_node_id=organization_id,
            is_minor=user.is_minor,
            birth_date=user.birth_date,
            mfa_enabled=user.mfa_enabled,
            verification_status=user.verification_status,
            child_protection_cert=ChildProtectionCert(
                completed=user.child_protection_completed,
                completed_at=user.child_protection_completed_at,
            ),
            status=user.status,
            club_approval=user.club_approval,
            club_approval_reason=user.club_approval_reason,
            created_at=user.created_at,
            last_login=user.last_login,
            handle=user.handle,
            guardian_allows_avatar=user.guardian_allows_avatar,
            onboarding_completed_at=user.onboarding_completed_at,
            signature_url=user.signature_url if own else None,
        )



class MeResponse(UserResponse, MinistryContext):
    """The person's own record (`GET /auth/me`, `GET /users/me`, `PATCH /users/me/preferences`):
    `UserResponse` plus the ministry context of the shell's selector (024). Lists of users and
    somebody else's record never carry it."""

class OnboardingUpdate(BaseModel):
    """`PATCH /users/me/onboarding`. Finishing and skipping both close the guide for good;
    the outcome only goes to the audit log."""

    outcome: Literal["completed", "skipped"] = "completed"
    # The last step the person saw (1-based), for the audit log only.
    step: int | None = Field(default=None, ge=1, le=10)


class UserUpdate(BaseModel):
    """
    Explicit whitelist. Only fields present in the request body are applied,
    so `{"avatar_url": null}` clears the avatar while omitting it leaves it alone.
    """

    name: str | None = Field(default=None, min_length=1, max_length=180)
    avatar_url: str | None = Field(default=None, max_length=2048)
    # Bloque E, E9: switches OFF the portfolio progress e-mails. The security,
    # invitation, consent and membership ones are never silenced.
    notify_progress: bool | None = None
    # Bloque G: only an approved guardian of the minor (or MASTER_GC) sets it.
    guardian_allows_avatar: bool | None = None
    # Admin-only fields
    role: RoleName | None = None
    organization_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasChoices("organization_id", "org_node_id")
    )
    status: UserStatus | None = None
    verification_status: VerificationStatus | None = None
    birth_date: date | None = None
    is_minor: bool | None = None

    model_config = {"extra": "forbid"}


class GuardianshipCreate(BaseModel):
    child_id: uuid.UUID
    relationship: Relationship = "PARENT"


class ChildGuardianship(BaseModel):
    """A guardianship as the adult sees it on their panel: the minor, their
    club, and what is waiting for a decision. No e-mail, no birth date."""

    id: str
    guardian_id: str
    child_id: str
    relationship: str
    consent_status: str
    consent_granted_at: datetime | None
    created_at: datetime
    child_name: str
    # Bloque G: the link to `/u/{handle}` (always filled by the trigger of 014_profiles.sql;
    # typed like `PublicProfile.handle`) and the current state of the photo switch.
    handle: str | None = None
    guardian_allows_avatar: bool = False
    child_age: int | None = None
    club: ClubRef | None = None
    membership_status: str | None = None
    # The active membership's id, so the panel can call .../{membership_id}/consent/revoke.
    membership_id: str | None = None
    pending_consents: list[str] = []


class GuardianshipResponse(BaseModel):
    id: str
    guardian_id: str
    child_id: str
    relationship: str
    consent_status: str
    consent_granted_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, row: Guardianship) -> "GuardianshipResponse":
        return cls(
            id=str(row.id),
            guardian_id=str(row.guardian_id),
            child_id=str(row.child_id),
            relationship=row.relationship,
            consent_status=row.consent_status,
            consent_granted_at=row.consent_granted_at,
            created_at=row.created_at,
        )
