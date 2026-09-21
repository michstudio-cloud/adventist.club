"""Club sign-up: a director requests a club, a coordinator decides.

Everything here only *stages* changes on the caller's session (club row,
director flags, audit row), so the router commits them as one transaction.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, User
from app.schemas.org import ClubSignup
from app.security import CLUB_APPROVED, CLUB_DIRECTOR, CLUB_PENDING, CLUB_REJECTED, utcnow
from app.services import memberships as membership_service
from app.services import placement
from app.services.audit import record_audit

CLUB_TYPE = "club"
ASSOCIATION_TYPE = "association"
STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"
# Never shown on public reads.
HIDDEN_STATUSES = (STATUS_PENDING, STATUS_REJECTED)


async def stage_pending_club(
    db: AsyncSession, director: User, payload: ClubSignup, request: Request | None
) -> Organization:
    """Create the `pending` club under the association and attach the director to it."""
    if director.role != CLUB_DIRECTOR:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a CLUB_DIRECTOR can request a club")
    if director.is_minor:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minors cannot request a club")
    if director.club_approval in (CLUB_PENDING, CLUB_APPROVED):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You already have a club request. Wait for the decision."
        )

    association = await db.get(Organization, payload.association_id)
    if association is None or association.type != ASSOCIATION_TYPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "association_id is not an association")
    if association.status != STATUS_ACTIVE or not association.path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The association is not active")

    duplicate = select(Organization.id).where(
        Organization.type == CLUB_TYPE,
        Organization.status.in_((STATUS_ACTIVE, STATUS_PENDING)),
        Organization.path.op("<@")(association.path),
        func.lower(Organization.name) == payload.name.lower(),
    )
    if (await db.execute(duplicate.limit(1))).scalar_one_or_none():
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A club with this name already exists in the association"
        )

    # E6 / decision D3: the director declares their CHURCH, never the zone.
    # With a church that already has a zone the club is born in its final
    # place; otherwise it is born under the association, as before, and the
    # declaration waits in `metadata_json.placement` for the association.
    church = await _declared_church(db, association, payload)
    parent = association
    if church is not None and (await placement.ancestors_of(db, church)).get(placement.ZONE):
        parent = church

    club_id = uuid.uuid4()
    now = utcnow()
    club = Organization(
        id=club_id,
        parent_id=parent.id,
        type=CLUB_TYPE,
        name=payload.name,
        status=STATUS_PENDING,
        path=f"{parent.path}.{club_id.hex}",
        city=payload.city,
        country=association.country,
        latitude=payload.latitude,
        longitude=payload.longitude,
        metadata_json={
            "requested_by": str(director.id),
            "requested_at": now.isoformat(),
            # Kept for the pre-E6 readers; the node is the truth once placed.
            "church": church.name if church is not None else payload.church_name,
            "placement": {
                "church_id": str(church.id) if church is not None else None,
                "church_name": church.name if church is not None else payload.church_name,
                "declared_by": str(director.id),
                "declared_at": now.isoformat(),
            },
            "contact": payload.contact or director.email,
        },
        created_at=now,
        updated_at=now,
    )
    db.add(club)
    # The ORM has no relationship() metadata: the club must exist before the
    # user row points at it.
    await db.flush()

    # A director whose previous request was refused opens a corrected club:
    # that membership closes here, so there is never a second ACTIVE one.
    await membership_service.close_active_for_move(db, director)
    # The founder is a member from the first minute, so `organization_id` and
    # `club_memberships` never disagree (spec §6, rule 1). Their authority is
    # still governed by `director_blocked` until the association approves.
    membership = membership_service.stage_membership(
        db,
        user_id=director.id,
        club_id=club.id,
        role=CLUB_DIRECTOR,
        status_name=membership_service.ACTIVE,
        source=membership_service.FOUNDER,
    )
    membership.started_at = now
    director.organization_id = club.id
    director.club_approval = CLUB_PENDING
    director.club_approval_reason = None
    director.club_approval_at = None

    record_audit(
        db,
        action="CLUB_REQUEST",
        entity_type="ORGANIZATION",
        entity_id=club.id,
        actor=director,
        details=f"Requested CLUB {club.name} under {association.name}",
        metadata={"association_id": str(association.id), "association_code": association.code},
        request=request,
    )
    return club


async def _declared_church(
    db: AsyncSession, association: Organization, payload: ClubSignup
) -> Organization | None:
    """`church_id` must be an ACTIVE church of this association; a `church_name`
    that already exists is silently the same church, so nobody creates a second
    «Iglesia Central» by typing it."""
    if payload.church_id is not None:
        church = await db.get(Organization, payload.church_id)
        if (
            church is None
            or church.type != placement.CHURCH
            or church.status != STATUS_ACTIVE
            or not church.path
            or not church.path.startswith(f"{association.path}.")
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "church_id no es una iglesia activa de esa asociación"
            )
        return church
    return await placement.find_church(db, association, payload.church_name or "")


async def requester_of(db: AsyncSession, club: Organization) -> User | None:
    raw = (club.metadata_json or {}).get("requested_by")
    try:
        return await db.get(User, uuid.UUID(str(raw))) if raw else None
    except ValueError:
        return None


async def stage_club_decision(
    db: AsyncSession,
    club: Organization,
    actor: User,
    *,
    approve: bool,
    reason: str | None,
    request: Request | None,
    extra_metadata: dict | None = None,
) -> User | None:
    """Flip the club and its director. Returns the director (for the email)."""
    now = utcnow()
    club.status = STATUS_ACTIVE if approve else STATUS_REJECTED
    club.updated_at = now
    # Reassign a new dict: in-place JSONB mutations are not tracked.
    club.metadata_json = {
        **(club.metadata_json or {}),
        "decision": {
            "status": CLUB_APPROVED if approve else CLUB_REJECTED,
            "by": str(actor.id),
            "by_role": actor.role,
            "at": now.isoformat(),
            "reason": reason,
        },
    }

    director = await requester_of(db, club)
    # Only touch the flag while the director is still tied to this request.
    if director is not None and director.organization_id == club.id:
        director.club_approval = CLUB_APPROVED if approve else CLUB_REJECTED
        director.club_approval_reason = None if approve else reason
        director.club_approval_at = now

    record_audit(
        db,
        action="CLUB_APPROVE" if approve else "CLUB_REJECT",
        entity_type="ORGANIZATION",
        entity_id=club.id,
        actor=actor,
        details=f"{'Approved' if approve else 'Rejected'} CLUB {club.name}",
        metadata={
            "director_id": str(director.id) if director else None,
            "parent_id": str(club.parent_id) if club.parent_id else None,
            "reason": reason,
            # E6: what was declared and what the association finally decided.
            **(extra_metadata or {}),
        },
        request=request,
    )
    return director
