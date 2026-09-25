"""Invitations to a role of an organization (Bloque I §1.2, 026_role_assignments.sql).

Always NOMINAL (the e-mail is required and must be the e-mail of the account that
accepts), single use, at most 30 days. The token is a URL secret stored as SHA-256
only — the pattern of `invitations.py` for club links — and shown ONCE to whoever
creates (or resends) it.

Who invites: somebody who holds, through `rbac.outranks_in`, a role of STRICTLY higher
rank than the invited one on the target node or above it. MASTER_GC everywhere. Nobody
invites an equal or a superior.

Accepting creates the `role_assignments` row and recomputes `users.role`; for
CLUB_DIRECTOR it also appoints the director of the club (`clubs._appoint_director`),
which leaves their ACTIVE membership behind. Minors never accept: every invitable
role is an adult one. Nothing here commits.
"""
import urllib.parse
import uuid
from datetime import timedelta

from fastapi import BackgroundTasks, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OrgInvitation, Organization, User
from app.people import is_minor_user
from app.rbac import outranks_in
from app.security import (
    ADMIN_ASSOCIATION,
    CLUB_DIRECTOR,
    COORDINATOR_ZONE,
    INSTRUCTOR,
    generate_url_token,
    sha256_hex,
    utcnow,
)
from app.services import role_assignments
from app.services.audit import record_audit

# Role -> the node types it can be given on.
INVITABLE_ROLES = {
    ADMIN_ASSOCIATION: ("association",),
    COORDINATOR_ZONE: ("zone",),
    INSTRUCTOR: ("association", "zone"),
    CLUB_DIRECTOR: ("club",),
}
DEFAULT_DAYS = 14
MAX_DAYS = 30

# Computed states (nothing stored: derived from the row).
PENDING, ACCEPTED, EXPIRED, REVOKED = "PENDING", "ACCEPTED", "EXPIRED", "REVOKED"

ORG_INVITATION = "ORG_INVITATION"
# ONE answer for "does not exist", "expired", "revoked" and "already used".
INVALID_DETAIL = "Invitación no válida o vencida"
WRONG_ACCOUNT_DETAIL = "Esta invitación es para otra cuenta de correo."
MINOR_DETAIL = "Un menor de edad no puede aceptar un rol de adulto."
FORBIDDEN_DETAIL = "No puedes invitar a ese rol en esta organización."
NOT_FOUND_DETAIL = "Invitación no encontrada en esta organización"


def invalid_invitation() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, INVALID_DETAIL)


def state_of(invitation: OrgInvitation, now=None) -> str:
    now = now or utcnow()
    if invitation.accepted_at is not None:
        return ACCEPTED
    if invitation.revoked_at is not None:
        return REVOKED
    if invitation.expires_at <= now:
        return EXPIRED
    return PENDING


def join_url(token: str) -> str:
    return f"{settings.frontend_url}/invitacion?t={token}"


def whatsapp_url(organization_name: str, token: str) -> str:
    text = (
        f"Te invito a sumarte al equipo de {organization_name} en Adventist.Club: "
        f"{join_url(token)}"
    )
    return "https://wa.me/?text=" + urllib.parse.quote(text)


def _normalize(email: str | None) -> str:
    return (email or "").strip().lower()


# ----------------------------------------------------------------------------
# Permissions
# ----------------------------------------------------------------------------
async def can_invite(db: AsyncSession, actor: User, role: str, organization: Organization) -> bool:
    return role in INVITABLE_ROLES and await outranks_in(db, actor, role, organization)


async def require_can_invite(
    db: AsyncSession, actor: User, role: str, organization: Organization
) -> None:
    if not await can_invite(db, actor, role, organization):
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN_DETAIL)


def _check_target(role: str, organization: Organization) -> None:
    kinds = INVITABLE_ROLES.get(role)
    if kinds is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"No se puede invitar con el rol {role}."
        )
    if organization.type not in kinds:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"El rol {role} se da sobre: {', '.join(kinds)}.",
        )
    if organization.status != "active" or not organization.path:
        raise HTTPException(status.HTTP_409_CONFLICT, "La organización no está activa.")


# ----------------------------------------------------------------------------
# Writers
# ----------------------------------------------------------------------------
async def create(
    db: AsyncSession,
    *,
    organization: Organization,
    actor: User,
    role: str,
    email: str,
    expires_in_days: int | None = None,
    request: Request | None = None,
    audit_action: str = "ORG_INVITATION_CREATE",
) -> tuple[OrgInvitation, str]:
    """Returns the row and the plain token, which the caller shows ONCE."""
    _check_target(role, organization)
    await require_can_invite(db, actor, role, organization)
    email = _normalize(email)
    if "@" not in email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Correo no válido.")
    days = expires_in_days or DEFAULT_DAYS
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"La vigencia va de 1 a {MAX_DAYS} días."
        )

    now = utcnow()
    live = select(OrgInvitation.id).where(
        OrgInvitation.organization_id == organization.id,
        OrgInvitation.role == role,
        OrgInvitation.email == email,
        OrgInvitation.accepted_at.is_(None),
        OrgInvitation.revoked_at.is_(None),
        OrgInvitation.expires_at > now,
    )
    if (await db.execute(live.limit(1))).scalar_one_or_none():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ya hay una invitación pendiente para ese correo y rol: reenvíala o revócala.",
        )
    invitee = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if invitee is not None and await _holds_exactly(db, invitee, role, organization):
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa persona ya tiene ese rol aquí.")

    token = generate_url_token()
    invitation = OrgInvitation(
        id=uuid.uuid4(),
        organization_id=organization.id,
        role=role,
        email=email,
        token_hash=sha256_hex(token),
        expires_at=now + timedelta(days=days),
        created_by_id=actor.id,
        created_at=now,
    )
    db.add(invitation)
    record_audit(
        db,
        action=audit_action,
        entity_type=ORG_INVITATION,
        entity_id=invitation.id,
        actor=actor,
        details=f"{role} invitation for {organization.type} {organization.id}",
        # No token and no guest address in the audit trail.
        metadata={
            "organization_id": str(organization.id),
            "role": role,
            "expires_in_days": days,
            "invitee_has_account": invitee is not None,
        },
        request=request,
    )
    return invitation, token


async def _holds_exactly(db: AsyncSession, person: User, role: str, organization: Organization) -> bool:
    """The role ON this very node (not inherited from above: an association admin may
    still be invited to direct one of its clubs)."""
    from app.rbac import effective_roles

    return any(
        scoped.role == role and scoped.organization_id == organization.id
        for scoped in await effective_roles(db, person)
    )


async def get_of_org(
    db: AsyncSession, organization: Organization, invitation_id: uuid.UUID
) -> OrgInvitation:
    stmt = select(OrgInvitation).where(
        OrgInvitation.id == invitation_id, OrgInvitation.organization_id == organization.id
    )
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND_DETAIL)
    return invitation


async def list_of_org(db: AsyncSession, organization: Organization) -> list[OrgInvitation]:
    stmt = (
        select(OrgInvitation)
        .where(OrgInvitation.organization_id == organization.id)
        .order_by(OrgInvitation.created_at.desc(), OrgInvitation.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def revoke(
    db: AsyncSession,
    invitation: OrgInvitation,
    *,
    actor: User,
    request: Request | None = None,
    audit_action: str = "ORG_INVITATION_REVOKE",
) -> OrgInvitation:
    """Idempotent on a dead invitation. An accepted one is not revoked: the role it gave
    is retired from the team instead."""
    if invitation.accepted_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La invitación ya fue aceptada: retira el rol desde el equipo."
        )
    if invitation.revoked_at is None:
        invitation.revoked_at = utcnow()
        invitation.revoked_by_id = actor.id
        record_audit(
            db,
            action=audit_action,
            entity_type=ORG_INVITATION,
            entity_id=invitation.id,
            actor=actor,
            metadata={"organization_id": str(invitation.organization_id), "role": invitation.role},
            request=request,
        )
    return invitation


async def resend(
    db: AsyncSession,
    invitation: OrgInvitation,
    *,
    organization: Organization,
    actor: User,
    request: Request | None = None,
) -> tuple[OrgInvitation, str]:
    """A NEW token for the same person, role and node; the old one is revoked whatever
    state it was in (pending or expired). Returns the new row and its token, shown ONCE."""
    if invitation.accepted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "La invitación ya fue aceptada.")
    await revoke(db, invitation, actor=actor, request=request, audit_action="ORG_INVITATION_REVOKE")
    await db.flush()  # the old one stops counting as pending
    renewed, token = await create(
        db,
        organization=organization,
        actor=actor,
        role=invitation.role,
        email=invitation.email,
        request=request,
        audit_action="ORG_INVITATION_RESEND",
    )
    return renewed, token


# ----------------------------------------------------------------------------
# The invitee
# ----------------------------------------------------------------------------
async def by_token(db: AsyncSession, token: str) -> OrgInvitation:
    """Look one up WITHOUT consuming it. The single 404 when unusable."""
    stmt = select(OrgInvitation).where(OrgInvitation.token_hash == sha256_hex(token or ""))
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if invitation is None or state_of(invitation) != PENDING:
        raise invalid_invitation()
    return invitation


async def _claim(db: AsyncSession, invitation: OrgInvitation, user: User) -> bool:
    """Spend the invitation, atomically (the pattern of `invitations.claim`)."""
    now = utcnow()
    stmt = (
        update(OrgInvitation)
        .where(
            OrgInvitation.id == invitation.id,
            OrgInvitation.accepted_at.is_(None),
            OrgInvitation.revoked_at.is_(None),
            OrgInvitation.expires_at > now,
        )
        .values(accepted_at=now, accepted_by_id=user.id)
        .returning(OrgInvitation.id)
    )
    return (await db.execute(stmt)).scalars().first() is not None


async def accept(
    db: AsyncSession, *, user: User, token: str, request: Request | None = None
) -> tuple[OrgInvitation, Organization]:
    from app.services import clubs as club_service

    invitation = await by_token(db, token)
    organization = await db.get(Organization, invitation.organization_id)
    if organization is None or organization.status != "active":
        raise invalid_invitation()
    if _normalize(invitation.email) != _normalize(user.email):
        raise HTTPException(status.HTTP_403_FORBIDDEN, WRONG_ACCOUNT_DETAIL)
    if is_minor_user(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, MINOR_DETAIL)
    if user.status != "ACTIVE":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La cuenta no está activa.")

    appoint = False
    if invitation.role == CLUB_DIRECTOR:
        from app.services import memberships as membership_service

        current = await membership_service.active_membership(db, user.id)
        already = (
            current is not None
            and current.club_id == organization.id
            and current.role == CLUB_DIRECTOR
        )
        if not already:
            if await club_service.leads_a_club(db, user):
                raise HTTPException(status.HTTP_409_CONFLICT, club_service.DIRECTOR_HAS_CLUB)
            appoint = True

    # Spend it BEFORE creating anything: the same token twice must not grant twice.
    if not await _claim(db, invitation, user):
        raise invalid_invitation()
    await db.refresh(invitation)

    inviter = await db.get(User, invitation.created_by_id) if invitation.created_by_id else None
    if appoint:
        # The principal as it stands now, before the membership service writes the
        # two legacy columns; `recompute` then puts back whatever outranks the club.
        await role_assignments.sync_legacy(db, user)
        await club_service._appoint_director(db, inviter or user, organization, user, request)
    await role_assignments.grant(
        db,
        user=user,
        role=invitation.role,
        organization=organization,
        actor=inviter,
        source=role_assignments.INVITATION,
        invitation_id=invitation.id,
        request=request,
        sync=not appoint,
    )
    record_audit(
        db,
        action="ORG_INVITATION_ACCEPT",
        entity_type=ORG_INVITATION,
        entity_id=invitation.id,
        actor=user,
        details=f"{user.email} accepted {invitation.role} of {organization.id}",
        metadata={
            "organization_id": str(organization.id),
            "role": invitation.role,
            "principal_after": user.role,
        },
        request=request,
    )
    return invitation, organization


# ----------------------------------------------------------------------------
# The e-mail, after the commit (pattern of the club invitations)
# ----------------------------------------------------------------------------
def stage_email(db: AsyncSession, invitation: OrgInvitation):
    from app.services import notifications

    return notifications.stage_log(
        db,
        kind=notifications.ORG_INVITATION,
        email=invitation.email,
        entity_type=notifications.ORG_INVITATION_ENTITY,
        entity_id=invitation.id,
    )


def queue_email(
    background: BackgroundTasks,
    log,
    invitation: OrgInvitation,
    token: str,
    organization: Organization,
    inviter: User,
) -> None:
    """Hand the send to `BackgroundTasks`; call it AFTER the commit. Never fails the request."""
    from app.services import email as email_service
    from app.services import notifications

    background.add_task(
        notifications.send_and_record,
        email_service.send_org_invitation_email,
        log.id,
        invitation.email,
        organization.name,
        invitation.role,
        join_url(token),
        inviter.name,
        invitation.expires_at.date().isoformat(),
    )
