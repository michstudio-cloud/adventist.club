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

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, Organization, User
from app.people import is_minor_user
from app.rbac import can_grant_club_role
from app.security import (
    CLUB_DIRECTOR,
    CLUB_LEVEL_ROLES,
    CLUB_SCOPED_ROLES,
    STUDENT,
    utcnow,
)
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
