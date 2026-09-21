"""Managing a club: its roster and its control data (Bloque E).

Thin: check the permission in `app/rbac.py`, call `app/services/memberships.py`,
serialize. The roster is the one read of this API that shows minors, so its
serializer is deliberately narrow (spec §7): years of age, never a birth date,
never an e-mail, and `guardian_email` only for the director and the
administrators above them — not even for the club's own secretary.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import ClubInvitation, ClubMembership, Guardianship, Organization, User
from app.people import age_in_years, is_minor_user
from app.rbac import (
    CONSENT_GRANTED,
    can_grant_club_role,
    can_manage_members,
    can_view_guardian_contact,
    can_view_roster,
)
from app.schemas.membership import (
    ClubProfileOut,
    ClubProfileUpdate,
    ClubRole,
    ConsentSummary,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    ManagedMemberRow,
    MemberRemoval,
    MemberRoleUpdate,
    MemberRow,
    MembershipEnded,
    MembershipStatus,
    as_invitation_out,
)
from app.security import COUNSELOR, utcnow
from app.services import email as email_service
from app.services import invitations as invitation_service
from app.services import memberships as membership_service
from app.services import notifications
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])

NOT_MANAGER_DETAIL = "No tienes permiso para gestionar los miembros de este club"
NOT_ROSTER_DETAIL = "No tienes permiso para ver la nómina de este club"


async def _club_for_manager(db: AsyncSession, actor: User, club_id: uuid.UUID) -> Organization:
    club = await membership_service.get_club(db, club_id)
    if not await can_manage_members(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_MANAGER_DETAIL)
    return club


# ----------------------------------------------------------------------------
# Roster
# ----------------------------------------------------------------------------
@router.get("/{club_id}/members", response_model=None)
async def list_members(
    club_id: uuid.UUID,
    status_filter: MembershipStatus | None = Query(None, alias="status"),
    role: ClubRole | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemberRow | ManagedMemberRow]:
    """`response_model` is left off on purpose: each row is serialized by its
    own class, so `guardian_email` is missing — not null — for the rest of the
    staff. Declaring one model would put the key back into every payload."""
    club = await membership_service.get_club(db, club_id)
    if not await can_view_roster(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROSTER_DETAIL)
    manages = await can_manage_members(db, current_user, club)
    sees_guardians = await can_view_guardian_contact(db, current_user, club)
    if not manages and current_user.role == COUNSELOR:
        # A counselor only ever sees the members of their own units, and units
        # arrive with E5: until then there is nobody they may look at.
        return []

    stmt = (
        select(ClubMembership, User)
        .join(User, User.id == ClubMembership.user_id)
        .where(
            ClubMembership.club_id == club.id,
            # The roster is the people who are IN the club unless asked otherwise.
            ClubMembership.status == (status_filter or membership_service.ACTIVE),
        )
    )
    if role:
        stmt = stmt.where(ClubMembership.role == role)
    rows = (await db.execute(stmt.order_by(User.name, User.id))).all()

    return [
        await _member_row(db, membership, member, include_guardian_email=sees_guardians)
        for membership, member in rows
    ]


async def _member_row(
    db: AsyncSession,
    membership: ClubMembership,
    member: User,
    *,
    include_guardian_email: bool,
) -> MemberRow | ManagedMemberRow:
    minor = is_minor_user(member)
    consent = None
    if minor:
        consent = ConsentSummary(
            status="APPROVED" if membership.consent_at else None,
            guardian_name=await _guardian_name(db, membership, member),
        )
    fields = dict(
        membership_id=str(membership.id),
        user_id=str(member.id),
        name=member.name,
        role=membership.role,
        status=membership.status,
        is_minor=minor,
        # Years, never the date: the roster is read by the whole staff.
        age=age_in_years(member.birth_date),
        since=membership.started_at,
        consent=consent,
    )
    if not include_guardian_email:
        return MemberRow(**fields)
    return ManagedMemberRow(**fields, guardian_email=membership.guardian_email)


async def _guardian_name(db: AsyncSession, membership: ClubMembership, member: User) -> str | None:
    """Who authorized this minor. The club's human control: the director sees
    that a real adult said yes."""
    if membership.consent_by_id is not None:
        guardian = await db.get(User, membership.consent_by_id)
        return guardian.name if guardian else None
    stmt = (
        select(User.name)
        .join(Guardianship, Guardianship.guardian_id == User.id)
        .where(
            Guardianship.child_id == member.id,
            Guardianship.consent_status == CONSENT_GRANTED,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


# ----------------------------------------------------------------------------
# Roles and removals
# ----------------------------------------------------------------------------
@router.patch("/{club_id}/members/{membership_id}", response_model=None)
async def change_member_role(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberRoleUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemberRow | ManagedMemberRow:
    club = await _club_for_manager(db, current_user, club_id)
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    await membership_service.change_role(
        db, membership, new_role=payload.role, actor=current_user, request=request
    )
    await db.commit()
    member = await db.get(User, membership.user_id)
    sees_guardians = await can_view_guardian_contact(db, current_user, club)
    return await _member_row(db, membership, member, include_guardian_email=sees_guardians)


@router.post("/{club_id}/members/{membership_id}/remove", response_model=MembershipEnded)
async def remove_member(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberRemoval,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take somebody off the roster, always with a reason: it reaches them."""
    club = await _club_for_manager(db, current_user, club_id)
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    await membership_service.remove_member(
        db, membership, reason=payload.reason, actor=current_user, request=request
    )
    await db.commit()
    return MembershipEnded(
        membership_id=str(membership.id),
        club_id=str(membership.club_id),
        status=membership.status,
        end_reason=membership.end_reason,
    )


# ----------------------------------------------------------------------------
# Invitations (E3)
# ----------------------------------------------------------------------------
@router.post(
    "/{club_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    club_id: uuid.UUID,
    payload: InvitationCreate,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The link the club shares. Its token comes back ONCE: it is never stored
    in the clear and no later read can show it again."""
    club = await _club_for_manager(db, current_user, club_id)
    invitation, token = await invitation_service.create(
        db,
        club=club,
        actor=current_user,
        role=payload.role,
        max_uses=payload.max_uses,
        expires_in_days=payload.expires_in_days,
        email=payload.email,
        request=request,
    )
    log = None
    if invitation.email:
        log = notifications.stage_log(
            db,
            kind=notifications.CLUB_INVITATION,
            email=invitation.email,
            entity_type=notifications.INVITATION,
            entity_id=invitation.id,
        )
    await db.commit()

    if log is not None:
        # After the commit, and never able to fail the request (pattern of org.py).
        background.add_task(
            notifications.send_and_record,
            email_service.send_club_invitation_email,
            log.id,
            invitation.email,
            club.name,
            invitation.role,
            invitation_service.join_url(token),
            current_user.name,
        )
    return InvitationCreated(
        invitation=as_invitation_out(
            invitation,
            state=invitation_service.state_of(invitation),
            requires_approval=invitation_service.requires_approval(invitation),
        ),
        token=token,
        url=invitation_service.join_url(token),
        whatsapp_url=invitation_service.whatsapp_url(club.name, token),
    )


@router.get("/{club_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    club_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await _club_for_manager(db, current_user, club_id)
    stmt = (
        select(ClubInvitation)
        .where(ClubInvitation.club_id == club.id)
        .order_by(ClubInvitation.created_at.desc())
    )
    return [
        as_invitation_out(
            row,
            state=invitation_service.state_of(row),
            requires_approval=invitation_service.requires_approval(row),
        )
        for row in (await db.execute(stmt)).scalars().all()
    ]


@router.delete("/{club_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    club_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kill a link. Idempotent: a link that was already dead answers the same."""
    club = await _club_for_manager(db, current_user, club_id)
    stmt = select(ClubInvitation).where(
        ClubInvitation.id == invitation_id, ClubInvitation.club_id == club.id
    )
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitación no encontrada en este club")
    if not can_grant_club_role(current_user, invitation.role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes revocar invitaciones de ese rol."
        )
    await invitation_service.revoke(db, invitation, actor=current_user, request=request)
    await db.commit()


# ----------------------------------------------------------------------------
# Control data of the club
# ----------------------------------------------------------------------------
@router.patch("/{club_id}/profile", response_model=ClubProfileOut)
async def update_club_profile(
    club_id: uuid.UUID,
    payload: ClubProfileUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Meeting day and time, contact and whether the club takes join requests.
    A whitelist inside `metadata_json.profile`: it never touches the name, the
    location or the place of the club in the tree."""
    club = await _club_for_manager(db, current_user, club_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que actualizar")

    metadata = dict(club.metadata_json or {})
    profile = {**metadata.get("profile", {}), **changes}
    # Reassign a new dict: in-place JSONB mutations are not tracked.
    club.metadata_json = {**metadata, "profile": profile}
    club.updated_at = utcnow()
    record_audit(
        db,
        action="CLUB_PROFILE_UPDATE",
        entity_type="ORGANIZATION",
        entity_id=club.id,
        actor=current_user,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await db.commit()
    return ClubProfileOut(club_id=str(club.id), name=club.name, profile=profile)
