"""Bloque I §1: the team of an organization — scoped roles and nominal invitations."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

OrgInvitableRole = Literal["ADMIN_ASSOCIATION", "COORDINATOR_ZONE", "INSTRUCTOR", "CLUB_DIRECTOR"]


class OrgRefOut(BaseModel):
    id: str
    name: str
    type: str


class ScopedRoleOut(BaseModel):
    """One entry of `roles` in `GET /auth/me`: the principal (`users.role`) first."""

    role: str
    principal: bool = False
    organization: OrgRefOut | None = None


class OrgInvitationCreate(BaseModel):
    role: OrgInvitableRole
    # Always nominal: only the account with this address may accept it.
    email: EmailStr
    expires_in_days: int | None = Field(default=None, ge=1, le=30)

    model_config = {"extra": "forbid"}


class OrgInvitationOut(BaseModel):
    """An invitation as the team screen sees it. The token is NEVER here."""

    id: str
    organization_id: str
    role: str
    email: str
    state: str
    expires_at: datetime
    created_at: datetime
    created_by: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    revoked_at: datetime | None = None


class OrgInvitationCreated(BaseModel):
    invitation: OrgInvitationOut
    # Shown exactly once. Losing it costs a resend, never a lookup.
    token: str
    url: str
    whatsapp_url: str


class OrgTokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=512)

    model_config = {"extra": "forbid"}


class OrgInvitationPreview(BaseModel):
    """What the link is, before signing in. The address is only hinted (`m•••@dominio`)
    so a leaked link does not hand it out; `matches_session` says whether the account
    signed in (if any) is the one invited."""

    organization: OrgRefOut
    role: str
    email_hint: str
    expires_at: datetime
    matches_session: bool | None = None


class OrgInvitationAccepted(BaseModel):
    role: str
    organization: OrgRefOut
    # The account's principal role after accepting, and every role in force.
    principal_role: str
    roles: list[ScopedRoleOut]


class RoleAssignmentOut(BaseModel):
    id: str
    role: str
    organization_id: str
    user: dict
    principal: bool
    source: str
    granted_at: datetime
    granted_by: str | None = None
