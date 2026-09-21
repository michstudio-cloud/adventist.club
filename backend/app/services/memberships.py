"""Club membership (Bloque E): the ONLY writer of `users.organization_id` and
of the club role of an account.

    PENDING_CONSENT -> PENDING_APPROVAL -> ACTIVE -> ENDED
    REJECTED (the club said no) · CANCELLED (withdrawn or superseded)

Integrity rules (spec §6):
  1. `users.organization_id` of a club account = the club of its single ACTIVE
     membership, or NULL. Only `activate` and `end` write either of them.
  2. A minor never reaches ACTIVE without `consent_at` for THAT membership, and
     a minor only ever holds STUDENT.
  3. Nobody grants a role they do not outrank (app/rbac.py decides); nobody
     approves or removes themselves through the management path.
  6. One club at a time: a transfer ends A and activates B in one transaction,
     which the partial unique index enforces underneath.

Every function here only *stages* its change (rows and audit) on the caller's
session; the router commits, so a change and its audit row live or die together.
"""
import uuid
from datetime import timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubInvitation, ClubMembership, Guardianship, Organization, User
from app.people import is_minor_user
from app.rbac import CONSENT_GRANTED, can_grant_club_role
from app.security import (
    CLUB_DIRECTOR,
    CLUB_LEVEL_ROLES,
    CLUB_SCOPED_ROLES,
    INSTRUCTOR,
    STUDENT,
    generate_url_token,
    sha256_hex,
    utcnow,
)
from app.services import units
from app.services.audit import record_audit

CLUB_TYPE = "club"

PENDING_CONSENT = "PENDING_CONSENT"
PENDING_APPROVAL = "PENDING_APPROVAL"
ACTIVE = "ACTIVE"
ENDED = "ENDED"
REJECTED = "REJECTED"
CANCELLED = "CANCELLED"
OPEN_STATUSES = (PENDING_CONSENT, PENDING_APPROVAL)

# end_reason
LEFT, REMOVED, TRANSFERRED = "LEFT", "REMOVED", "TRANSFERRED"
CONSENT_REVOKED, DECLINED, EXPIRED = "CONSENT_REVOKED", "DECLINED", "EXPIRED"

# source
INVITATION, REQUEST, ADMIN, FOUNDER, BACKFILL = (
    "INVITATION",
    "REQUEST",
    "ADMIN",
    "FOUNDER",
    "BACKFILL",
)

MEMBERSHIP = "MEMBERSHIP"

NO_MEMBERSHIP_DETAIL = "No perteneces a ningún club."
ONLY_DIRECTOR_DETAIL = (
    "Eres el único director del club: el relevo lo hace la asociación antes de que puedas salir."
)
MINOR_ROLE_DETAIL = "Un menor de edad sólo puede tener el rol STUDENT."


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def active_membership(db: AsyncSession, user_id: uuid.UUID) -> ClubMembership | None:
    stmt = select(ClubMembership).where(
        ClubMembership.user_id == user_id, ClubMembership.status == ACTIVE
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def open_memberships(db: AsyncSession, user_id: uuid.UUID) -> list[ClubMembership]:
    stmt = (
        select(ClubMembership)
        .where(ClubMembership.user_id == user_id, ClubMembership.status.in_(OPEN_STATUSES))
        .order_by(ClubMembership.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_club(db: AsyncSession, club_id: uuid.UUID) -> Organization:
    club = await db.get(Organization, club_id)
    if club is None or club.type != CLUB_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Club no encontrado")
    return club


async def get_membership_of_club(
    db: AsyncSession, club: Organization, membership_id: uuid.UUID
) -> ClubMembership:
    """404 for a membership of another club: an outsider learns nothing."""
    stmt = select(ClubMembership).where(
        ClubMembership.id == membership_id, ClubMembership.club_id == club.id
    )
    membership = (await db.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada en este club")
    return membership


async def count_active_directors(db: AsyncSession, club_id: uuid.UUID) -> int:
    stmt = select(ClubMembership.id).where(
        ClubMembership.club_id == club_id,
        ClubMembership.status == ACTIVE,
        ClubMembership.role == CLUB_DIRECTOR,
    )
    return len((await db.execute(stmt)).scalars().all())


# ----------------------------------------------------------------------------
# The two writers
# ----------------------------------------------------------------------------
async def _on_club_changed(db: AsyncSession, user_id: uuid.UUID, club_id: uuid.UUID | None) -> None:
    """
    Move the member's open portfolio enrollments to their new club, so the
    review queue of block A does not wait for the next write. Imported lazily:
    block E must keep working on a database where 007 has not been applied.
    """
    try:
        from app.services import portfolio
    except Exception:  # pragma: no cover - portfolio is part of the same deploy
        return
    await portfolio.on_club_changed(db, user_id, club_id)


async def activate(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    actor: User | None,
    member: User,
    request: Request | None = None,
    audit_action: str = "MEMBERSHIP_APPROVE",
) -> ClubMembership:
    """
    Turn a membership into the person's ACTIVE one: it writes
    `users.organization_id` and `users.role`, cancels their other open
    memberships and, if they were active elsewhere, ends that one as a
    TRANSFERRED transfer — all in the caller's transaction.
    """
    already_active = membership.status == ACTIVE
    if is_minor_user(member):
        if membership.role != STUDENT:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, MINOR_ROLE_DETAIL)
        if not already_active and membership.consent_at is None:
            # Rule 2. The consent flow (E3) is the only thing that sets it.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Falta el consentimiento de un tutor para este club.",
            )

    now = utcnow()
    current = await active_membership(db, member.id)
    if current is not None and current.id != membership.id:
        if current.club_id == membership.club_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya eres miembro de este club.")
        _close(current, end_reason=TRANSFERRED, actor=actor, now=now)
        record_audit(
            db,
            action="MEMBERSHIP_TRANSFER",
            entity_type="USER",
            entity_id=member.id,
            actor=actor or member,
            details=f"Transfer to club {membership.club_id}",
            metadata={
                "from_club_id": str(current.club_id),
                "to_club_id": str(membership.club_id),
                "from_membership_id": str(current.id),
                "to_membership_id": str(membership.id),
            },
            request=request,
        )

    # Anything else this person had pending dies with the decision.
    for other in await open_memberships(db, member.id):
        if other.id != membership.id:
            other.status = CANCELLED
            other.end_reason = EXPIRED
            other.ended_at = now
            other.updated_at = now

    membership.status = ACTIVE
    membership.started_at = membership.started_at or now
    membership.ended_at = None
    membership.end_reason = None
    membership.updated_at = now
    # E5: an invitation may already point at a unit. A full unit never stops
    # somebody joining the club — they land without one and the club is told.
    if membership.unit_id is None and membership.invitation_id is not None:
        invitation = await db.get(ClubInvitation, membership.invitation_id)
        if invitation is not None:
            await units.place_on_activation(db, membership, invitation.unit_id)
    if actor is not None and membership.decided_by_id is None:
        membership.decided_by_id = actor.id
        membership.decided_at = now

    member.organization_id = membership.club_id
    member.role = membership.role
    await db.flush()
    await _on_club_changed(db, member.id, membership.club_id)

    record_audit(
        db,
        action=audit_action,
        entity_type=MEMBERSHIP,
        entity_id=membership.id,
        actor=actor or member,
        details=f"{member.email} is now {membership.role} of club {membership.club_id}",
        metadata={"club_id": str(membership.club_id), "role": membership.role},
        request=request,
    )
    return membership


async def end(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    end_reason: str,
    actor: User | None,
    member: User,
    reason: str | None = None,
    request: Request | None = None,
    audit_action: str = "MEMBERSHIP_REMOVE",
) -> ClubMembership:
    """
    Close an ACTIVE membership and detach the account: no organization, no
    club-only role, and the open portfolio enrollments lose their club (flow 5
    of block A: nobody can review until there is a club again).
    """
    now = utcnow()
    _close(membership, end_reason=end_reason, actor=actor, now=now, reason=reason)

    # E5: leaving the club leaves its unit, and whoever is gone leads none of
    # the club's units any more.
    await units.detach_member(db, membership)
    await units.release_counselor_posts(db, membership.club_id, member.id)

    member.organization_id = None
    if member.role in CLUB_SCOPED_ROLES:
        # These two only exist inside a club; INSTRUCTOR and STUDENT are the
        # person's own and survive the exit.
        member.role = STUDENT
    await db.flush()
    await _on_club_changed(db, member.id, None)

    record_audit(
        db,
        action=audit_action,
        entity_type=MEMBERSHIP,
        entity_id=membership.id,
        actor=actor or member,
        details=f"Membership of {member.email} ended ({end_reason})",
        metadata={"club_id": str(membership.club_id), "end_reason": end_reason, "reason": reason},
        request=request,
    )
    return membership


def _close(
    membership: ClubMembership,
    *,
    end_reason: str,
    actor: User | None,
    now,
    reason: str | None = None,
) -> None:
    """Mark the row as finished. Split out so `activate` can close the previous
    membership of a transfer without detaching the account in between."""
    membership.status = ENDED
    membership.end_reason = end_reason
    membership.ended_at = now
    membership.ended_by_id = actor.id if actor else None
    membership.updated_at = now
    if reason:
        membership.decision_reason = reason


# ----------------------------------------------------------------------------
# What the routers call
# ----------------------------------------------------------------------------
def stage_membership(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    club_id: uuid.UUID,
    role: str,
    status_name: str,
    source: str,
    invitation_id: uuid.UUID | None = None,
    message: str | None = None,
    guardian_email: str | None = None,
) -> ClubMembership:
    """Add a membership row to the session. The caller decides its state."""
    now = utcnow()
    membership = ClubMembership(
        id=uuid.uuid4(),
        user_id=user_id,
        club_id=club_id,
        role=role,
        status=status_name,
        source=source,
        invitation_id=invitation_id,
        message=message,
        guardian_email=guardian_email,
        created_at=now,
        updated_at=now,
    )
    db.add(membership)
    return membership


async def close_active_for_move(
    db: AsyncSession, member: User, *, end_reason: str = TRANSFERRED, actor: User | None = None
) -> ClubMembership | None:
    """
    Close the ACTIVE membership WITHOUT detaching the account: the caller is
    attaching it somewhere else in the same transaction. Used by the founder
    path, where a director whose club was refused opens a corrected one.
    """
    current = await active_membership(db, member.id)
    if current is not None:
        _close(current, end_reason=end_reason, actor=actor, now=utcnow())
        await db.flush()
    return current


async def leave(
    db: AsyncSession, member: User, request: Request | None = None
) -> ClubMembership:
    """`DELETE /memberships/me`. A minor may leave on their own."""
    membership = await active_membership(db, member.id)
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_MEMBERSHIP_DETAIL)
    if membership.role == CLUB_DIRECTOR and await count_active_directors(db, membership.club_id) <= 1:
        # A club without a director has nobody to certify or to decide.
        raise HTTPException(status.HTTP_409_CONFLICT, ONLY_DIRECTOR_DETAIL)
    return await end(
        db,
        membership,
        end_reason=LEFT,
        actor=member,
        member=member,
        request=request,
        audit_action="MEMBERSHIP_LEAVE",
    )


async def change_role(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    new_role: str,
    actor: User,
    request: Request | None = None,
) -> ClubMembership:
    """`PATCH /clubs/{club_id}/members/{id}`: the role inside the club, which
    is also the account's role while the membership lasts."""
    member = await db.get(User, membership.user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada en este club")
    if member.id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes cambiar tu propio rol.")
    if is_minor_user(member) and new_role != STUDENT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MINOR_ROLE_DETAIL)
    if not can_grant_club_role(actor, new_role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No puedes conceder el rol {new_role} en este club."
        )
    # You cannot act on somebody whose role you do not outrank (a secretary
    # never touches an instructor, a director never touches another director).
    if not can_grant_club_role(actor, membership.role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes gestionar a esta persona en el club."
        )

    previous = membership.role
    membership.role = new_role
    membership.updated_at = utcnow()
    if membership.status == ACTIVE:
        member.role = new_role
    record_audit(
        db,
        action="MEMBERSHIP_ROLE_CHANGE",
        entity_type=MEMBERSHIP,
        entity_id=membership.id,
        actor=actor,
        details=f"{previous} -> {new_role}",
        metadata={"club_id": str(membership.club_id), "from": previous, "to": new_role},
        request=request,
    )
    return membership


async def remove_member(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    reason: str,
    actor: User,
    request: Request | None = None,
) -> tuple[ClubMembership, User]:
    """`POST /clubs/{club_id}/members/{id}/remove`, always with a reason."""
    member = await db.get(User, membership.user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada en este club")
    if member.id == actor.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes darte de baja por esta vía; sal del club."
        )
    if membership.status != ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta membresía ya no está activa.")
    if not can_grant_club_role(actor, membership.role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes dar de baja a esta persona en el club."
        )
    await end(
        db,
        membership,
        end_reason=REMOVED,
        actor=actor,
        member=member,
        reason=reason,
        request=request,
    )
    return membership, member


# ----------------------------------------------------------------------------
# Joining: an invitation accepted, with the guardian's consent when the person
# is a minor (spec §5.9). The state a membership starts in:
#
#   origin                     adult              minor
#   single-use invitation      ACTIVE             PENDING_CONSENT -> ACTIVE
#   multi-use link / request   PENDING_APPROVAL   PENDING_CONSENT -> PENDING_APPROVAL
# ----------------------------------------------------------------------------
CONSENT_DAYS = 14


def _is_administrative(user: User) -> bool:
    return user.role not in CLUB_LEVEL_ROLES


async def check_can_join(
    db: AsyncSession, member: User, club: Organization, *, confirm_transfer: bool
) -> ClubMembership | None:
    """The refusals that are the same however somebody is trying to join."""
    if _is_administrative(member):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Las cuentas administrativas no pertenecen a un club; su organización es su jurisdicción.",
        )
    if member.role == CLUB_DIRECTOR and member.club_approval is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya tienes un club propio; no puedes unirte a otro."
        )
    current = await active_membership(db, member.id)
    if current is not None:
        if current.club_id == club.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya eres miembro de este club.")
        if not confirm_transfer:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Ya perteneces a otro club: confirma el traslado para cambiarte.",
            )
    existing = [row for row in await open_memberships(db, member.id) if row.club_id == club.id]
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya tienes una solicitud pendiente en este club."
        )
    return current


async def approved_guardians(db: AsyncSession, child_id: uuid.UUID) -> list[User]:
    stmt = (
        select(User)
        .join(Guardianship, Guardianship.guardian_id == User.id)
        .where(
            Guardianship.child_id == child_id,
            Guardianship.consent_status == CONSENT_GRANTED,
        )
    )
    return list((await db.execute(stmt)).scalars().all())


def stage_consent_token(membership: ClubMembership) -> str:
    """A fresh consent link for this membership. Single purpose, 14 days, and
    the previous one dies the moment this is called: only the hash is kept."""
    token = generate_url_token()
    membership.consent_token_hash = sha256_hex(token)
    membership.consent_expires_at = utcnow() + timedelta(days=CONSENT_DAYS)
    membership.updated_at = utcnow()
    return token


async def start_membership(
    db: AsyncSession,
    *,
    member: User,
    club: Organization,
    role: str,
    source: str,
    needs_approval: bool,
    invitation_id: uuid.UUID | None = None,
    guardian_email: str | None = None,
    message: str | None = None,
    actor: User | None = None,
    request: Request | None = None,
    audit_action: str,
) -> tuple[ClubMembership, str | None]:
    """
    Create the membership in the state its origin and the member's age dictate.
    Returns the row and, for a minor, the plain consent token to e-mail.
    """
    minor = is_minor_user(member)
    if minor and role != STUDENT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, MINOR_ROLE_DETAIL)

    guardian_email = (guardian_email or "").strip().lower() or None
    guardians = await approved_guardians(db, member.id) if minor else []
    if minor and guardian_email is None and not guardians:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Indica el correo de tu madre, padre o tutor para pedir su autorización.",
        )

    # The row is always born pending and only `activate` turns it ACTIVE: that
    # is what closes a previous membership first, so the "one active club"
    # index is never hit by a transfer.
    membership = stage_membership(
        db,
        user_id=member.id,
        club_id=club.id,
        role=role,
        status_name=PENDING_CONSENT if minor else PENDING_APPROVAL,
        source=source,
        invitation_id=invitation_id,
        message=message,
        guardian_email=guardian_email,
    )
    consent_token = stage_consent_token(membership) if minor else None
    await db.flush()

    record_audit(
        db,
        action=audit_action,
        entity_type=MEMBERSHIP,
        entity_id=membership.id,
        actor=actor or member,
        details=f"{member.email} joins club {club.id} as {role} ({membership.status})",
        metadata={"club_id": str(club.id), "role": role, "status": membership.status},
        request=request,
    )
    if not minor and not needs_approval:
        # An adult with a single-use link is in, right now.
        await activate(
            db,
            membership,
            actor=actor,
            member=member,
            request=request,
            audit_action="MEMBERSHIP_APPROVE",
        )
    return membership, consent_token


async def consent_by_token(db: AsyncSession, token: str) -> ClubMembership:
    """The one 404 for any consent link that does not work."""
    stmt = select(ClubMembership).where(
        ClubMembership.consent_token_hash == sha256_hex(token or ""),
        ClubMembership.status == PENDING_CONSENT,
        ClubMembership.consent_expires_at > utcnow(),
    )
    membership = (await db.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no válida o vencida")
    return membership


async def decide_consent(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    guardian: User,
    approve: bool,
    relationship: str = "PARENT",
    request: Request | None = None,
) -> ClubMembership:
    """
    An adult authorizes (or refuses) this minor joining THIS club. The platform
    cannot verify a family tie: the human control is the director, who sees in
    the roster who authorized each minor.
    """
    member = await db.get(User, membership.user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no válida o vencida")
    if guardian.id == member.id or is_minor_user(guardian):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo una persona adulta puede autorizar a un menor."
        )
    if guardian.verification_status != "VERIFIED":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Verifica tu correo antes de autorizar a un menor."
        )
    if membership.status != PENDING_CONSENT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta solicitud ya fue decidida.")

    now = utcnow()
    # The link is single purpose and single use, whichever way it is answered.
    membership.consent_token_hash = None
    membership.updated_at = now

    if not approve:
        membership.status = CANCELLED
        membership.end_reason = DECLINED
        membership.ended_at = now
        membership.ended_by_id = guardian.id
        record_audit(
            db,
            action="CONSENT_REJECT",
            entity_type=MEMBERSHIP,
            entity_id=membership.id,
            actor=guardian,
            metadata={"club_id": str(membership.club_id)},
            request=request,
        )
        return membership

    await _upsert_guardianship(db, guardian=guardian, child=member, relationship=relationship)
    membership.consent_at = now
    membership.consent_by_id = guardian.id
    record_audit(
        db,
        action="CONSENT_GRANT",
        entity_type=MEMBERSHIP,
        entity_id=membership.id,
        actor=guardian,
        # Ids only: no names of minors and no addresses of guardians.
        metadata={"club_id": str(membership.club_id), "child_id": str(member.id)},
        request=request,
    )

    if await _needs_club_approval(db, membership):
        membership.status = PENDING_APPROVAL
        return membership
    await activate(
        db,
        membership,
        actor=None,
        member=member,
        request=request,
        audit_action="MEMBERSHIP_APPROVE",
    )
    return membership


async def _needs_club_approval(db: AsyncSession, membership: ClubMembership) -> bool:
    """A request always does; a multi-use link does (D2); a single-use link
    was already the club's own decision."""
    if membership.source != INVITATION or membership.invitation_id is None:
        return True
    invitation = await db.get(ClubInvitation, membership.invitation_id)
    return invitation is None or invitation.max_uses > 1


async def _upsert_guardianship(
    db: AsyncSession, *, guardian: User, child: User, relationship: str
) -> Guardianship:
    stmt = select(Guardianship).where(
        Guardianship.guardian_id == guardian.id, Guardianship.child_id == child.id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = Guardianship(
            id=uuid.uuid4(),
            guardian_id=guardian.id,
            child_id=child.id,
            relationship=relationship,
            created_at=utcnow(),
        )
        db.add(row)
    row.consent_status = CONSENT_GRANTED
    row.consent_granted_at = utcnow()
    await db.flush()
    return row


async def revoke_consent(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    guardian: User,
    request: Request | None = None,
) -> ClubMembership:
    """The guardian withdraws the authorization: the club loses access at once
    (block A computes its permissions live, so nothing else has to be undone)."""
    member = await db.get(User, membership.user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada")
    if not any(row.id == guardian.id for row in await approved_guardians(db, member.id)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo un tutor autorizado puede retirar el permiso."
        )
    if membership.status not in (ACTIVE, PENDING_APPROVAL, PENDING_CONSENT):
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta membresía ya no está vigente.")

    if membership.status != ACTIVE:
        now = utcnow()
        membership.status = CANCELLED
        membership.end_reason = CONSENT_REVOKED
        membership.ended_at = now
        membership.ended_by_id = guardian.id
        membership.updated_at = now
    else:
        await end(
            db,
            membership,
            end_reason=CONSENT_REVOKED,
            actor=guardian,
            member=member,
            request=request,
            audit_action="CONSENT_REVOKE",
        )
    return membership


# ----------------------------------------------------------------------------
# Join requests from `/clubs` (E4): the other door, the one the person opens
# from outside. The club decides; nobody walks in.
# ----------------------------------------------------------------------------
MAX_OPEN_REQUESTS = 3
REQUEST_STALE_DAYS = 30
REJECTION_COOLDOWN_DAYS = 30


def accepts_requests(club: Organization) -> bool:
    """A club may close its door; the default is open (spec §5.2)."""
    profile = (club.metadata_json or {}).get("profile") or {}
    return profile.get("accepts_requests", True) is not False


async def expire_stale_requests(
    db: AsyncSession, *, club_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
) -> None:
    """
    A request nobody decided in 30 days is given up as CANCELLED when somebody
    reads the queue. No scheduled task: the read that would show a stale row is
    exactly the moment to close it.
    """
    stmt = select(ClubMembership).where(
        ClubMembership.status.in_(OPEN_STATUSES),
        ClubMembership.source == REQUEST,
        ClubMembership.created_at < utcnow() - timedelta(days=REQUEST_STALE_DAYS),
    )
    if club_id is not None:
        stmt = stmt.where(ClubMembership.club_id == club_id)
    if user_id is not None:
        stmt = stmt.where(ClubMembership.user_id == user_id)
    now = utcnow()
    for membership in (await db.execute(stmt)).scalars().all():
        membership.status = CANCELLED
        membership.end_reason = EXPIRED
        membership.ended_at = now
        membership.updated_at = now


def requested_role(member: User) -> str:
    """What somebody asks to be. An adult who already holds the INSTRUCTOR role
    asks as an instructor; everybody else asks as a member. The club may grant
    something else when it approves."""
    if member.role == INSTRUCTOR and not is_minor_user(member):
        return INSTRUCTOR
    return STUDENT


async def _recent_rejection(db: AsyncSession, member: User, club_id: uuid.UUID) -> bool:
    stmt = select(ClubMembership).where(
        ClubMembership.user_id == member.id,
        ClubMembership.club_id == club_id,
        ClubMembership.status == REJECTED,
        ClubMembership.decided_at > utcnow() - timedelta(days=REJECTION_COOLDOWN_DAYS),
    )
    return (await db.execute(stmt)).scalars().first() is not None


async def request_to_join(
    db: AsyncSession,
    *,
    member: User,
    club: Organization,
    message: str | None = None,
    guardian_email: str | None = None,
    confirm_transfer: bool = False,
    request: Request | None = None,
) -> tuple[ClubMembership, str | None]:
    """`POST /memberships/requests`. Returns the row and, for a minor, the
    consent token to e-mail."""
    if member.verification_status != "VERIFIED":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Verifica tu correo antes de solicitar unirte a un club.",
        )
    if club.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Club no encontrado")
    if not accepts_requests(club):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Este club no recibe solicitudes por ahora."
        )
    await check_can_join(db, member, club, confirm_transfer=confirm_transfer)

    await expire_stale_requests(db, user_id=member.id)
    if len(await open_memberships(db, member.id)) >= MAX_OPEN_REQUESTS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Ya tienes {MAX_OPEN_REQUESTS} solicitudes abiertas; espera una respuesta o cancela alguna.",
        )
    if await _recent_rejection(db, member, club.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Este club rechazó tu solicitud hace poco; puedes volver a pedirlo {REJECTION_COOLDOWN_DAYS} días después.",
        )

    return await start_membership(
        db,
        member=member,
        club=club,
        role=requested_role(member),
        source=REQUEST,
        needs_approval=True,  # a request is ALWAYS the club's decision
        guardian_email=guardian_email,
        message=message,
        request=request,
        audit_action="MEMBERSHIP_REQUEST",
    )


async def cancel_request(
    db: AsyncSession, membership: ClubMembership, *, member: User, request: Request | None = None
) -> ClubMembership:
    if membership.status not in OPEN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta solicitud ya fue decidida.")
    now = utcnow()
    membership.status = CANCELLED
    membership.ended_at = now
    membership.ended_by_id = member.id
    membership.updated_at = now
    record_audit(
        db,
        action="MEMBERSHIP_CANCEL",
        entity_type=MEMBERSHIP,
        entity_id=membership.id,
        actor=member,
        metadata={"club_id": str(membership.club_id)},
        request=request,
    )
    return membership


async def decide_request(
    db: AsyncSession,
    membership: ClubMembership,
    *,
    actor: User,
    approve: bool,
    role: str | None = None,
    reason: str | None = None,
    request: Request | None = None,
) -> tuple[ClubMembership, User]:
    """Approve or reject what is waiting. A minor whose guardian has not
    answered yet is not in this queue at all, so approving cannot bypass rule 2."""
    member = await db.get(User, membership.user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membresía no encontrada en este club")
    if member.id == actor.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No puedes decidir tu propia solicitud.")
    if membership.status != PENDING_APPROVAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta solicitud ya fue decidida.")

    new_role = role or membership.role
    if not can_grant_club_role(actor, new_role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"No puedes conceder el rol {new_role} en este club."
        )

    if not approve:
        now = utcnow()
        membership.status = REJECTED
        membership.decided_by_id = actor.id
        membership.decided_at = now
        membership.decision_reason = reason
        membership.updated_at = now
        record_audit(
            db,
            action="MEMBERSHIP_REJECT",
            entity_type=MEMBERSHIP,
            entity_id=membership.id,
            actor=actor,
            metadata={"club_id": str(membership.club_id), "reason": reason},
            request=request,
        )
        return membership, member

    membership.role = new_role
    await activate(db, membership, actor=actor, member=member, request=request)
    return membership, member


async def pending_requests(db: AsyncSession, club_id: uuid.UUID) -> list[ClubMembership]:
    """What the club has to decide. Minors appear ONLY once their guardian
    authorized: a minor who asks is invisible to the club until then (§7)."""
    await expire_stale_requests(db, club_id=club_id)
    stmt = (
        select(ClubMembership)
        .where(
            ClubMembership.club_id == club_id,
            ClubMembership.status == PENDING_APPROVAL,
        )
        .order_by(ClubMembership.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


async def club_directors(db: AsyncSession, club_id: uuid.UUID) -> list[User]:
    stmt = (
        select(User)
        .join(ClubMembership, ClubMembership.user_id == User.id)
        .where(
            ClubMembership.club_id == club_id,
            ClubMembership.status == ACTIVE,
            ClubMembership.role == CLUB_DIRECTOR,
        )
    )
    return list((await db.execute(stmt)).scalars().all())


# ----------------------------------------------------------------------------
# `PATCH /users/{id}` delegates here (spec §5.3: no router writes these two
# columns on a club account by itself).
# ----------------------------------------------------------------------------
async def apply_admin_change(
    db: AsyncSession,
    *,
    actor: User,
    target: User,
    changes: dict,
    request: Request | None = None,
) -> bool:
    """
    Keep `club_memberships` in step with an administrator's `PATCH /users/{id}`.

    Returns True when this function already wrote `organization_id` and `role`,
    so the router must not assign them again.
    """
    wants_org = "organization_id" in changes
    wants_role = "role" in changes
    if not wants_org and not wants_role:
        return False

    new_role = changes.get("role", target.role)
    new_org_id = changes["organization_id"] if wants_org else target.organization_id
    current = await active_membership(db, target.id)

    club = None
    if new_org_id is not None and new_role in CLUB_LEVEL_ROLES:
        node = await db.get(Organization, new_org_id)
        if node is not None and node.type == CLUB_TYPE:
            club = node

    if club is None:
        # Administrative account, or a club-level role attached to a field
        # office: neither has a membership. Whatever it had is closed.
        if current is not None:
            await end(
                db,
                current,
                end_reason=REMOVED,
                actor=actor,
                member=target,
                reason="Cambio administrativo",
                request=request,
            )
        target.organization_id = new_org_id
        target.role = new_role
        return True

    if current is not None and current.club_id == club.id:
        # Same club: only the role can have moved. No transfer, no re-activation.
        if current.role != new_role:
            current.role = new_role
            current.updated_at = utcnow()
            record_audit(
                db,
                action="MEMBERSHIP_ROLE_CHANGE",
                entity_type=MEMBERSHIP,
                entity_id=current.id,
                actor=actor,
                details=f"{target.role} -> {new_role}",
                metadata={"club_id": str(club.id), "from": target.role, "to": new_role},
                request=request,
            )
        target.organization_id = club.id
        target.role = new_role
        return True

    membership = stage_membership(
        db,
        user_id=target.id,
        club_id=club.id,
        role=new_role if new_role in CLUB_LEVEL_ROLES else STUDENT,
        status_name=PENDING_APPROVAL,
        source=ADMIN,
    )
    await db.flush()
    await activate(
        db, membership, actor=actor, member=target, request=request, audit_action="MEMBERSHIP_APPROVE"
    )
    return True
