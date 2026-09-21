"""Invitations to a club (Bloque E, E3).

Two shapes, and the difference is the whole point of decision D2:

  * a SINGLE-USE link (the default) is handed to one person, lasts 7 days and
    puts an adult straight into the club;
  * a MULTI-USE link — the one shared in a WhatsApp group or shown as a QR at
    the meeting — only ever carries the STUDENT role, lasts 30 days and leaves
    whoever uses it waiting for the director. A leaked link must not put
    strangers next to minors, nor fill block A's review queue.

Staff roles are always single use AND nominal: only the account with that
e-mail can accept. The token is a URL secret: it is stored as SHA-256 only,
travels in JSON bodies only, and is shown exactly once to whoever created it.
"""
import urllib.parse
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ClubInvitation, Organization, User
from app.rbac import can_grant_club_role
from app.security import (
    CLUB_SECRETARY,
    COUNSELOR,
    INSTRUCTOR,
    STUDENT,
    generate_url_token,
    sha256_hex,
    utcnow,
)
from app.services import units
from app.services.audit import record_audit

# CLUB_SECRETARY is appointed by the director from the roster, not by a link,
# until E8 opens it up (spec §5.7).
INVITABLE_ROLES = (STUDENT, COUNSELOR, INSTRUCTOR)
# Roles that always need a named invitee: the club is handing out authority.
STAFF_ROLES = (COUNSELOR, INSTRUCTOR, CLUB_SECRETARY)

SINGLE_USE_DAYS = 7
MULTI_USE_DAYS = 30
MAX_EXPIRY_DAYS = 90
MAX_USES = 200
# Enough for a whole club's intake, low enough that a forgotten link is noticed.
MAX_LIVE_PER_CLUB = 20

# Computed states (nothing is stored: they are derived from the row).
ACTIVE, EXPIRED, REVOKED, EXHAUSTED = "ACTIVE", "EXPIRED", "REVOKED", "EXHAUSTED"

INVITATION = "INVITATION"
# ONE answer for "does not exist", "expired", "revoked" and "used up": a
# stranger poking at /join must learn nothing at all.
INVALID_DETAIL = "Invitación no válida o vencida"


def invalid_invitation() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, INVALID_DETAIL)


def state_of(invitation: ClubInvitation, now=None) -> str:
    now = now or utcnow()
    if invitation.revoked_at is not None:
        return REVOKED
    if invitation.expires_at <= now:
        return EXPIRED
    if invitation.uses >= invitation.max_uses:
        return EXHAUSTED
    return ACTIVE


def requires_approval(invitation: ClubInvitation) -> bool:
    """A multi-use link never activates anybody on its own (D2)."""
    return invitation.max_uses > 1


def join_url(token: str) -> str:
    return f"{settings.frontend_url}/join?t={token}"


def whatsapp_url(club_name: str, token: str) -> str:
    text = f"Te invito a unirte al club {club_name} en Adventist.Club: {join_url(token)}"
    return "https://wa.me/?text=" + urllib.parse.quote(text)


async def live_count(db: AsyncSession, club_id: uuid.UUID) -> int:
    now = utcnow()
    stmt = select(ClubInvitation).where(
        ClubInvitation.club_id == club_id,
        ClubInvitation.revoked_at.is_(None),
        ClubInvitation.expires_at > now,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return len([row for row in rows if row.uses < row.max_uses])


async def create(
    db: AsyncSession,
    *,
    club: Organization,
    actor: User,
    role: str,
    max_uses: int = 1,
    expires_in_days: int | None = None,
    email: str | None = None,
    unit_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> tuple[ClubInvitation, str]:
    """Returns the row and the plain token, which the caller shows ONCE."""
    if role not in INVITABLE_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No se puede invitar con el rol {role} todavía."
        )
    if not can_grant_club_role(actor, role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No puedes conceder el rol {role} en este club."
        )
    if max_uses > 1 and role != STUDENT:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un enlace multiuso sólo puede invitar con el rol STUDENT.",
        )
    email = (email or "").strip().lower() or None
    if role in STAFF_ROLES and (email is None or max_uses > 1):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Las invitaciones al personal del club son de un solo uso y necesitan el correo de la persona.",
        )
    # E5: a link may already point at a unit, and only at one of this club's.
    await units.unit_of_club_or_400(db, club, unit_id)
    if await live_count(db, club.id) >= MAX_LIVE_PER_CLUB:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El club ya tiene {MAX_LIVE_PER_CLUB} invitaciones vigentes; revoca alguna antes de crear otra.",
        )

    default_days = MULTI_USE_DAYS if max_uses > 1 else SINGLE_USE_DAYS
    days = expires_in_days or default_days
    token = generate_url_token()
    invitation = ClubInvitation(
        id=uuid.uuid4(),
        club_id=club.id,
        created_by_id=actor.id,
        role=role,
        unit_id=unit_id,
        email=email,
        token_hash=sha256_hex(token),
        max_uses=max_uses,
        uses=0,
        expires_at=utcnow() + timedelta(days=days),
        created_at=utcnow(),
    )
    db.add(invitation)
    record_audit(
        db,
        action="INVITATION_CREATE",
        entity_type=INVITATION,
        entity_id=invitation.id,
        actor=actor,
        details=f"{role} invitation for club {club.id} ({max_uses} use(s))",
        # No token and no guest address in the audit trail.
        metadata={
            "club_id": str(club.id),
            "role": role,
            "max_uses": max_uses,
            "unit_id": str(unit_id) if unit_id else None,
        },
        request=request,
    )
    return invitation, token


async def by_token(db: AsyncSession, token: str) -> ClubInvitation:
    """Look one up WITHOUT consuming it. Raises the single 404 when unusable."""
    stmt = select(ClubInvitation).where(ClubInvitation.token_hash == sha256_hex(token or ""))
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if invitation is None or state_of(invitation) != ACTIVE:
        raise invalid_invitation()
    return invitation


async def claim(db: AsyncSession, invitation: ClubInvitation) -> bool:
    """
    Spend one use, atomically. A single UPDATE ... RETURNING (the pattern of
    `verification._claim`): two people accepting the last place of a link at
    the same time cannot both win.
    """
    now = utcnow()
    stmt = (
        update(ClubInvitation)
        .where(
            ClubInvitation.id == invitation.id,
            ClubInvitation.uses < ClubInvitation.max_uses,
            ClubInvitation.revoked_at.is_(None),
            ClubInvitation.expires_at > now,
        )
        .values(uses=ClubInvitation.uses + 1)
        .returning(ClubInvitation.id)
    )
    # The ORM-enabled UPDATE also synchronizes the in-session object, so the
    # count must NOT be incremented again in Python.
    return (await db.execute(stmt)).scalars().first() is not None


async def accept(
    db: AsyncSession,
    *,
    member: User,
    token: str,
    guardian_email: str | None = None,
    confirm_transfer: bool = False,
    request: Request | None = None,
) -> tuple[object, Organization, str | None]:
    """
    Use a link. Shared by `POST /memberships/invitations/accept` and by
    `POST /auth/register`, which runs the whole thing in ONE transaction so a
    bad token leaves no account behind.

    Returns the membership, its club and, for a minor, the consent token to
    e-mail. Nothing here commits.
    """
    from app.services import memberships as membership_service

    invitation = await by_token(db, token)
    club = await db.get(Organization, invitation.club_id)
    if club is None or club.status != "active":
        raise invalid_invitation()
    if invitation.email and invitation.email.lower() != member.email.lower():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Esta invitación es para otra cuenta de correo."
        )
    await membership_service.check_can_join(db, member, club, confirm_transfer=confirm_transfer)

    # Spend the use BEFORE creating anything: two people racing for the last
    # place of a link must not both get in.
    if not await claim(db, invitation):
        raise invalid_invitation()

    membership, consent_token = await membership_service.start_membership(
        db,
        member=member,
        club=club,
        role=invitation.role,
        source=membership_service.INVITATION,
        needs_approval=requires_approval(invitation),
        invitation_id=invitation.id,
        guardian_email=guardian_email,
        request=request,
        audit_action="MEMBERSHIP_INVITE_ACCEPT",
    )
    return membership, club, consent_token


async def revoke(
    db: AsyncSession, invitation: ClubInvitation, *, actor: User, request: Request | None = None
) -> ClubInvitation:
    """Idempotent: revoking a dead link answers the same as revoking a live one."""
    if invitation.revoked_at is None:
        invitation.revoked_at = utcnow()
        invitation.revoked_by_id = actor.id
        record_audit(
            db,
            action="INVITATION_REVOKE",
            entity_type=INVITATION,
            entity_id=invitation.id,
            actor=actor,
            metadata={"club_id": str(invitation.club_id), "role": invitation.role},
            request=request,
        )
    return invitation
