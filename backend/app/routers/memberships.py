"""What a person does about their own membership (Bloque E).

Thin: validate, call `app/services/memberships.py`, serialize. Everything a
club does TO its members lives in `app/routers/clubs.py` instead.

Two secrets travel through here, the invitation token and the consent token.
Both are accepted ONLY in a JSON body (never a query string, so they stay out
of access logs, browser history and Referer headers), both are stored only as
SHA-256, and anything wrong with either gets the SAME 404: a stranger poking
at /join or /consent must learn nothing about who or what exists.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import ClubMembership, Organization, User
from app.people import age_in_years, is_minor_user
from app.rate_limit import limiter
from app.schemas.membership import (
    ChildRef,
    ConsentClub,
    ConsentDecision,
    ConsentPreview,
    ConsentResend,
    InvitationAccept,
    InvitationPreview,
    JoinRequestCreate,
    JoinRequestOut,
    MembershipEnded,
    MembershipOut,
    MyMembership,
    TokenIn,
    as_club_ref,
    as_membership_out,
)
from app.security import CLUB_DIRECTOR
from app.services import invitations as invitation_service
from app.services import memberships as membership_service
from app.services import notifications

router = APIRouter(prefix="/api/v1/memberships", tags=["memberships"])

# Plain words for the consent screen. The guardian is told exactly what the
# club gets, and who inside the club gets it (spec §5.9).
CLUB_WILL_SEE = ["name", "age", "progress", "evidence"]
SEEN_BY = ["director", "verified_instructors"]


async def _membership_or_404(db: AsyncSession, membership_id: uuid.UUID) -> ClubMembership:
    membership = await db.get(ClubMembership, membership_id)
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada")
    return membership


# ----------------------------------------------------------------------------
# My membership
# ----------------------------------------------------------------------------
@router.get("/me", response_model=MyMembership)
async def my_membership(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """My club today, plus everything of mine still waiting for a decision."""
    active = await membership_service.active_membership(db, current_user.id)
    pending = await membership_service.open_memberships(db, current_user.id)

    async def _out(membership):
        club = await db.get(Organization, membership.club_id)
        return as_membership_out(membership, club)

    return MyMembership(
        active=await _out(active) if active is not None else None,
        pending=[await _out(row) for row in pending],
    )


@router.delete("/me", response_model=MembershipEnded)
async def leave_club(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Leave the club. A minor may do it alone; the only director may not
    (the association appoints the replacement first)."""
    membership = await membership_service.leave(db, current_user, request)
    await db.commit()
    return MembershipEnded(
        membership_id=str(membership.id),
        club_id=str(membership.club_id),
        status=membership.status,
        end_reason=membership.end_reason,
    )


# ----------------------------------------------------------------------------
# Invitations: looking at one, and accepting it
# ----------------------------------------------------------------------------
@router.post("/invitations/preview", response_model=InvitationPreview)
@limiter.limit("10/minute")
async def preview_invitation(
    request: Request, payload: TokenIn, db: AsyncSession = Depends(get_db)
):
    """Public, so `/join` can show what the link is before asking anyone to
    sign in. It never names a person, and every unusable link gives one 404."""
    invitation = await invitation_service.by_token(db, payload.token)
    club = await db.get(Organization, invitation.club_id)
    if club is None or club.status != "active":
        raise invitation_service.invalid_invitation()
    return InvitationPreview(
        club=as_club_ref(club),
        role=invitation.role,
        requires_approval=invitation_service.requires_approval(invitation),
        expires_at=invitation.expires_at,
    )


@router.post("/invitations/accept", response_model=MembershipOut)
async def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Use the link. An adult with a single-use link is in; anybody else waits
    for the director, and a minor waits for a guardian first."""
    membership, club, token = await invitation_service.accept(
        db,
        member=current_user,
        token=payload.token,
        guardian_email=payload.guardian_email,
        confirm_transfer=payload.confirm_transfer,
        request=request,
    )
    if token:
        await notifications.queue_consent_request(
            db,
            background,
            membership=membership,
            member=current_user,
            club=club,
            token=token,
            recipients=await notifications.consent_recipients(db, membership, current_user),
        )
    await db.commit()
    return as_membership_out(membership, club)


# ----------------------------------------------------------------------------
# Asking to join from `/clubs` (E4)
# ----------------------------------------------------------------------------
@router.post("/requests", response_model=JoinRequestOut, status_code=status.HTTP_201_CREATED)
async def request_to_join(
    payload: JoinRequestCreate,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a club to take you in. The club decides; nobody walks in."""
    club = await membership_service.get_club(db, payload.club_id)
    membership, consent_token = await membership_service.request_to_join(
        db,
        member=current_user,
        club=club,
        message=payload.message,
        guardian_email=payload.guardian_email,
        confirm_transfer=payload.confirm_transfer,
        request=request,
    )
    if consent_token:
        await notifications.queue_consent_request(
            db,
            background,
            membership=membership,
            member=current_user,
            club=club,
            token=consent_token,
            recipients=await notifications.consent_recipients(db, membership, current_user),
        )
    else:
        # A minor is not announced to the club until a guardian authorizes.
        pending = await membership_service.pending_requests(db, club.id)
        await notifications.queue_pending_requests_notice(
            db, background, club=club, pending=len(pending)
        )
    await db.commit()
    return _as_request_out(membership, club)


@router.delete("/requests/{membership_id}", response_model=MembershipEnded)
async def cancel_request(
    membership_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    membership = await _membership_or_404(db, membership_id)
    if membership.user_id != current_user.id:
        # Same answer as a request that does not exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada")
    await membership_service.cancel_request(
        db, membership, member=current_user, request=request
    )
    await db.commit()
    return MembershipEnded(
        membership_id=str(membership.id),
        club_id=str(membership.club_id),
        status=membership.status,
        end_reason=membership.end_reason,
    )


def _as_request_out(membership, club) -> JoinRequestOut:
    return JoinRequestOut(
        membership_id=str(membership.id),
        club=as_club_ref(club),
        role=membership.role,
        status=membership.status,
        message=membership.message,
        created_at=membership.created_at,
    )


# ----------------------------------------------------------------------------
# Guardian consent
# ----------------------------------------------------------------------------
@router.post("/consents/preview", response_model=ConsentPreview)
@limiter.limit("10/minute")
async def preview_consent(
    request: Request,
    payload: TokenIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What the guardian reads before deciding: who the minor is, which club,
    and in plain words what the club will see and who inside it."""
    membership = await membership_service.consent_by_token(db, payload.token)
    member = await db.get(User, membership.user_id)
    club = await db.get(Organization, membership.club_id)
    if member is None or club is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no válida o vencida")
    return ConsentPreview(
        membership_id=str(membership.id),
        child=ChildRef(id=str(member.id), name=member.name, age=age_in_years(member.birth_date)),
        club=as_club_ref(club, ConsentClub, director_name=await _director_name(db, club)),
        role=membership.role,
        club_will_see=CLUB_WILL_SEE,
        seen_by=SEEN_BY,
        expires_at=membership.consent_expires_at,
    )


async def _director_name(db: AsyncSession, club: Organization) -> str | None:
    """The guardian is told who is responsible for the club."""
    stmt = (
        select(User.name)
        .join(ClubMembership, ClubMembership.user_id == User.id)
        .where(
            ClubMembership.club_id == club.id,
            ClubMembership.status == membership_service.ACTIVE,
            ClubMembership.role == CLUB_DIRECTOR,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


@router.post("/consents/decide", response_model=MembershipOut)
async def decide_consent(
    payload: ConsentDecision,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    An adult authorizes this minor to join THIS club. Either with the emailed
    token, or — for somebody who already has an approved guardianship over the
    minor — with the membership id, because parents forward the e-mail.
    """
    if payload.token:
        membership = await membership_service.consent_by_token(db, payload.token)
    else:
        membership = await _membership_or_404(db, payload.membership_id)
        guardians = await membership_service.approved_guardians(db, membership.user_id)
        if not any(row.id == current_user.id for row in guardians):
            # Same answer as a bad token: no oracle about other people's minors.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no válida o vencida")

    await membership_service.decide_consent(
        db,
        membership,
        guardian=current_user,
        approve=payload.decision == "APPROVE",
        relationship=payload.relationship,
        request=request,
    )
    await db.commit()
    club = await db.get(Organization, membership.club_id)
    return as_membership_out(membership, club)


@router.post("/{membership_id}/consent/resend", response_model=MembershipOut)
async def resend_consent(
    membership_id: uuid.UUID,
    payload: ConsentResend,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The minor corrects the address or asks again. Three a day and no more:
    a minor's club is not a way to send somebody mail."""
    membership = await _membership_or_404(db, membership_id)
    if membership.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada")
    if membership.status != membership_service.PENDING_CONSENT:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esta solicitud ya no está esperando autorización."
        )
    if (
        await notifications.count_recent(
            db, kind=notifications.CONSENT_RESEND, entity_id=membership.id
        )
        >= notifications.CONSENT_RESENDS_PER_DAY
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Ya se enviaron tres avisos hoy; vuelve a intentarlo mañana.",
        )

    if payload.guardian_email:
        membership.guardian_email = payload.guardian_email.strip().lower()
    # A new link invalidates the previous one: only one live consent link.
    token = membership_service.stage_consent_token(membership)
    club = await db.get(Organization, membership.club_id)
    recipients = await notifications.consent_recipients(db, membership, current_user)
    if not recipients:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Indica el correo de tu madre, padre o tutor.",
        )
    await notifications.queue_consent_request(
        db,
        background,
        membership=membership,
        member=current_user,
        club=club,
        token=token,
        recipients=recipients,
        kind=notifications.CONSENT_RESEND,
    )
    await db.commit()
    return as_membership_out(membership, club)


@router.post("/{membership_id}/consent/revoke", response_model=MembershipEnded)
async def revoke_consent(
    membership_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The guardian withdraws the authorization; the club loses access at once."""
    membership = await _membership_or_404(db, membership_id)
    if is_minor_user(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sólo un adulto puede retirar el permiso.")
    await membership_service.revoke_consent(
        db, membership, guardian=current_user, request=request
    )
    await db.commit()
    return MembershipEnded(
        membership_id=str(membership.id),
        club_id=str(membership.club_id),
        status=membership.status,
        end_reason=membership.end_reason,
    )
