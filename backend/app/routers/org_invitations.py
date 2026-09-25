"""The team of an organization (Bloque I §1): nominal invitations to a role and the
roles in force. Thin: every rule lives in `services/org_invitations.py` and
`services/role_assignments.py`.

    /api/v1/org-nodes/{id}/invitations            POST, GET
    /api/v1/org-nodes/{id}/invitations/{inv}      DELETE (revoke)
    /api/v1/org-nodes/{id}/invitations/{inv}/resend  POST (new token, old one revoked)
    /api/v1/org-nodes/{id}/role-assignments       GET (the team)
    /api/v1/org-nodes/{id}/role-assignments/{a}   DELETE (retire a role)
    /api/v1/org-invitations/preview               POST (no session needed)
    /api/v1/org-invitations/accept                POST (with session)
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, get_optional_user
from app.models import Organization, RoleAssignment, User
from app.rate_limit import limiter
from app.rbac import outranks_in
from app.schemas.org_invitation import (
    OrgInvitationAccepted,
    OrgInvitationCreate,
    OrgInvitationCreated,
    OrgInvitationOut,
    OrgInvitationPreview,
    OrgRefOut,
    OrgTokenIn,
    RoleAssignmentOut,
)
from app.security import ROLE_RANK
from app.services import org_invitations as service
from app.services import role_assignments

router = APIRouter(prefix="/api/v1/org-nodes", tags=["organization team"])
invitations_router = APIRouter(prefix="/api/v1/org-invitations", tags=["organization team"])

TEAM_FORBIDDEN = "No administras el equipo de esta organización."


def _org_ref(node: Organization) -> OrgRefOut:
    return OrgRefOut(id=str(node.id), name=node.name, type=node.type.upper())


def _out(row) -> OrgInvitationOut:
    return OrgInvitationOut(
        id=str(row.id),
        organization_id=str(row.organization_id),
        role=row.role,
        email=row.email,
        state=service.state_of(row),
        expires_at=row.expires_at,
        created_at=row.created_at,
        created_by=str(row.created_by_id) if row.created_by_id else None,
        accepted_at=row.accepted_at,
        accepted_by=str(row.accepted_by_id) if row.accepted_by_id else None,
        revoked_at=row.revoked_at,
    )


def _created(row, token: str, node: Organization) -> OrgInvitationCreated:
    return OrgInvitationCreated(
        invitation=_out(row),
        token=token,
        url=service.join_url(token),
        whatsapp_url=service.whatsapp_url(node.name, token),
    )


async def _node_or_404(db: AsyncSession, node_id: uuid.UUID) -> Organization:
    node = await db.get(Organization, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Org node not found: {node_id}")
    return node


async def _require_team_admin(db: AsyncSession, actor: User, node: Organization) -> None:
    """Reading the team of a node: whoever may invite to at least ONE of its roles —
    i.e. outranks, on the node or above it, the lowest role invitable there."""
    kinds = [role for role, types in service.INVITABLE_ROLES.items() if node.type in types]
    if not kinds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Este nodo no tiene equipo.")
    lowest = min(kinds, key=lambda role: ROLE_RANK.get(role, 0))
    if not await outranks_in(db, actor, lowest, node):
        raise HTTPException(status.HTTP_403_FORBIDDEN, TEAM_FORBIDDEN)


# ----------------------------------------------------------------------------
# Invitations of a node
# ----------------------------------------------------------------------------
@router.post(
    "/{node_id}/invitations",
    response_model=OrgInvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_org_invitation(
    node_id: uuid.UUID,
    payload: OrgInvitationCreate,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invite one person (by e-mail) to a role of this node. The token comes back ONCE."""
    node = await _node_or_404(db, node_id)
    invitation, token = await service.create(
        db,
        organization=node,
        actor=current_user,
        role=payload.role,
        email=payload.email,
        expires_in_days=payload.expires_in_days,
        request=request,
    )
    log = service.stage_email(db, invitation)
    await db.commit()
    service.queue_email(background, log, invitation, token, node, current_user)
    return _created(invitation, token, node)


@router.get("/{node_id}/invitations", response_model=list[OrgInvitationOut])
async def list_org_invitations(
    node_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    node = await _node_or_404(db, node_id)
    await _require_team_admin(db, current_user, node)
    return [_out(row) for row in await service.list_of_org(db, node)]


@router.post(
    "/{node_id}/invitations/{invitation_id}/resend",
    response_model=OrgInvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def resend_org_invitation(
    node_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A new token for the same person and role; the old link stops working."""
    node = await _node_or_404(db, node_id)
    invitation = await service.get_of_org(db, node, invitation_id)
    await service.require_can_invite(db, current_user, invitation.role, node)
    renewed, token = await service.resend(
        db, invitation, organization=node, actor=current_user, request=request
    )
    log = service.stage_email(db, renewed)
    await db.commit()
    service.queue_email(background, log, renewed, token, node, current_user)
    return _created(renewed, token, node)


@router.delete(
    "/{node_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_org_invitation(
    node_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kill an invitation. Idempotent on one that was already dead."""
    node = await _node_or_404(db, node_id)
    invitation = await service.get_of_org(db, node, invitation_id)
    await service.require_can_invite(db, current_user, invitation.role, node)
    await service.revoke(db, invitation, actor=current_user, request=request)
    await db.commit()


# ----------------------------------------------------------------------------
# The roles in force on a node
# ----------------------------------------------------------------------------
@router.get("/{node_id}/role-assignments", response_model=list[RoleAssignmentOut])
async def list_role_assignments(
    node_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    node = await _node_or_404(db, node_id)
    await _require_team_admin(db, current_user, node)
    return [
        RoleAssignmentOut(
            id=str(row.id),
            role=row.role,
            organization_id=str(row.organization_id),
            user={"id": str(person.id), "name": person.name, "email": person.email},
            principal=(row.role, row.organization_id) == (person.role, person.organization_id),
            source=row.source,
            granted_at=row.granted_at,
            granted_by=str(row.granted_by_id) if row.granted_by_id else None,
        )
        for row, person in await role_assignments.active_on(db, node)
    ]


@router.delete(
    "/{node_id}/role-assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def retire_role_assignment(
    node_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take one role away. The same rank rule as inviting; never one's own; club roles
    end with the club membership instead (409)."""
    node = await _node_or_404(db, node_id)
    stmt = select(RoleAssignment).where(
        RoleAssignment.id == assignment_id, RoleAssignment.organization_id == node.id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.status != role_assignments.ACTIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rol no encontrado en esta organización")
    if row.user_id == current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes retirarte tu propio rol.")
    if not await outranks_in(db, current_user, row.role, node):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes retirar ese rol.")
    person = await db.get(User, row.user_id)
    await role_assignments.end(db, row, user=person, actor=current_user, request=request)
    await db.commit()


# ----------------------------------------------------------------------------
# The invitee
# ----------------------------------------------------------------------------
def _email_hint(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}"


@invitations_router.post("/preview", response_model=OrgInvitationPreview)
@limiter.limit("10/minute")
async def preview_org_invitation(
    request: Request,
    payload: OrgTokenIn,
    viewer: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Public, so the page can say what the link is before asking anyone to sign in
    (or to register with the invited address). Every unusable link gives one 404."""
    invitation = await service.by_token(db, payload.token)
    node = await db.get(Organization, invitation.organization_id)
    if node is None or node.status != "active":
        raise service.invalid_invitation()
    return OrgInvitationPreview(
        organization=_org_ref(node),
        role=invitation.role,
        email_hint=_email_hint(invitation.email),
        expires_at=invitation.expires_at,
        matches_session=(
            viewer.email.lower() == invitation.email.lower() if viewer is not None else None
        ),
    )


@invitations_router.post("/accept", response_model=OrgInvitationAccepted)
@limiter.limit("10/minute")
async def accept_org_invitation(
    request: Request,
    payload: OrgTokenIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take the role. The session's e-mail must be the invited one; a minor never
    accepts. Without an account the page registers first, then accepts with the
    same token."""
    invitation, node = await service.accept(
        db, user=current_user, token=payload.token, request=request
    )
    await db.commit()
    await db.refresh(current_user)
    return OrgInvitationAccepted(
        role=invitation.role,
        organization=_org_ref(node),
        principal_role=current_user.role,
        roles=await role_assignments.roles_out(db, current_user),
    )
