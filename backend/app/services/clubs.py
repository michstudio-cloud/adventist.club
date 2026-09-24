"""Club sign-up: a director requests a club, a coordinator decides.

Everything here only *stages* changes on the caller's session (club row,
director flags, audit row), so the router commits them as one transaction.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, Organization, User
from app.rbac import org_in_user_scope
from app.schemas.org import AdminClubCreate, ClubSignup
from app.security import (
    ADMIN_ROLES,
    CLUB_APPROVED,
    CLUB_DIRECTOR,
    CLUB_PENDING,
    CLUB_REJECTED,
    utcnow,
)
from app.services import memberships as membership_service
from app.services import ministries as ministry_service
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
    church = await _declared_church(db, association, payload.church_id, payload.church_name)
    # Rule 3 of ESTADO.md: a request names its ministries like any new club (422
    # `club_ministry_required` without one); nothing ever guesses them.
    ministries = await ministry_service.require_choice(db, payload)
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
        state=payload.state,
        country=payload.country or association.country,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address=payload.address,
        place_id=payload.place_id,
        maps_url=payload.maps_url,
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
    await ministry_service.set_club_ministries(db, club, ministries)
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
        metadata={
            "association_id": str(association.id),
            "association_code": association.code,
            "ministry": ministries[0].slug,
            "ministries": [row.slug for row in ministries],
        },
        request=request,
    )
    return club


async def _declared_church(
    db: AsyncSession,
    association: Organization,
    church_id: uuid.UUID | None,
    church_name: str | None,
) -> Organization | None:
    """`church_id` must be an ACTIVE church of this association; a `church_name`
    that already exists is silently the same church, so nobody creates a second
    «Iglesia Central» by typing it."""
    if church_id is not None:
        church = await db.get(Organization, church_id)
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
    return await placement.find_church(db, association, church_name or "")


# ----------------------------------------------------------------------------
# The administration opens a club itself (`POST /org-nodes/clubs/admin`)
# ----------------------------------------------------------------------------
ASSOCIATION_NOT_FOUND = "association_not_found"
CLUB_CODE_TAKEN = "club_code_taken"
CLUB_NAME_TAKEN = "club_name_taken"
DIRECTOR_NOT_FOUND = "director_not_found"
DIRECTOR_HAS_CLUB = "director_has_club"
DIRECTOR_NOT_ELIGIBLE = "director_not_eligible"
DIRECTOR_OUT_OF_SCOPE = "director_out_of_scope"
OUTSIDE_SCOPE = "This organization is outside your administrative scope"
VIA_ADMIN = "admin"


async def stage_admin_club(
    db: AsyncSession, actor: User, payload: AdminClubCreate, request: Request | None
) -> tuple[Organization, dict[str, Organization | None], User | None]:
    """Create an ACTIVE club under `payload.association_id`, place it when the
    body says where, and appoint its director — the same end state a request
    reaches once the association approves it, in one transaction.

    Returns the club, its association / zone / church, and the director.
    Every check runs before the first row is staged.
    """
    placement.require_structure_admin(actor)
    association = await db.get(Organization, payload.association_id)
    if (
        association is None
        or association.type != ASSOCIATION_TYPE
        or association.status != STATUS_ACTIVE
        or not association.path
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ASSOCIATION_NOT_FOUND)
    if not await org_in_user_scope(db, actor, association.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, OUTSIDE_SCOPE)

    same_name = select(Organization.id).where(
        Organization.type == CLUB_TYPE,
        Organization.status.in_((STATUS_ACTIVE, STATUS_PENDING)),
        Organization.path.op("<@")(association.path),
        func.lower(Organization.name) == payload.name.lower(),
    )
    if (await db.execute(same_name.limit(1))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, CLUB_NAME_TAKEN)
    if payload.code:
        # `organizations_code_key` makes a code unique across the WHOLE tree,
        # so the check is global: a clash anywhere would otherwise be a 500.
        same_code = select(Organization.id).where(
            func.lower(Organization.code) == payload.code.lower()
        )
        if (await db.execute(same_code.limit(1))).scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, CLUB_CODE_TAKEN)

    director = await _eligible_director(db, actor, payload.director_email)
    # Rule 3 of ESTADO.md: a club the administration opens always says its ministries.
    ministries = await ministry_service.require_choice(db, payload)

    club_id = uuid.uuid4()
    now = utcnow()
    club = Organization(
        id=club_id,
        parent_id=association.id,
        type=CLUB_TYPE,
        name=payload.name,
        code=payload.code,
        status=STATUS_ACTIVE,
        path=f"{association.path}.{club_id.hex}",
        city=payload.city,
        state=payload.state,
        country=payload.country or association.country,
        latitude=payload.latitude,
        longitude=payload.longitude,
        address=payload.address,
        place_id=payload.place_id,
        maps_url=payload.maps_url,
        metadata_json={
            "created_via": VIA_ADMIN,
            "created_by": str(actor.id),
            "decision": {
                "status": CLUB_APPROVED,
                "by": str(actor.id),
                "by_role": actor.role,
                "at": now.isoformat(),
                "reason": None,
            },
        },
        created_at=now,
        updated_at=now,
    )
    db.add(club)
    await ministry_service.set_club_ministries(db, club, ministries)
    await db.flush()
    record_audit(
        db,
        action="CREATE",
        entity_type=placement.ORGANIZATION,
        entity_id=club.id,
        actor=actor,
        details=f"Created CLUB {club.name} under {association.name}",
        metadata={
            "via": VIA_ADMIN,
            "association_id": str(association.id),
            "parent_id": str(association.id),
            "director_id": str(director.id) if director is not None else None,
            "ministry": ministries[0].slug,
            "ministries": [row.slug for row in ministries],
        },
        request=request,
    )

    refs = await _place_admin_club(db, actor, club, association, payload, request)
    if director is not None:
        await _appoint_director(db, actor, club, director, request)
    return club, refs, director


async def _eligible_director(db: AsyncSession, actor: User, email: str | None) -> User | None:
    """The person the administration appoints. They must exist, be an adult
    club-level account (an administrator is never demoted by this path), lead
    no other live club, and — unless unattached — belong to the caller's scope."""
    if not email:
        return None
    person = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, DIRECTOR_NOT_FOUND)
    if person.role in ADMIN_ROLES or person.is_minor or person.status != "ACTIVE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, DIRECTOR_NOT_ELIGIBLE)
    if await _leads_a_club(db, person):
        raise HTTPException(status.HTTP_409_CONFLICT, DIRECTOR_HAS_CLUB)
    if person.organization_id is not None and not await org_in_user_scope(
        db, actor, person.organization_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, DIRECTOR_OUT_OF_SCOPE)
    return person


async def _leads_a_club(db: AsyncSession, person: User) -> bool:
    """Director of an active club, or founder of a request still pending."""
    live = (STATUS_ACTIVE, STATUS_PENDING)
    if person.role == CLUB_DIRECTOR and person.organization_id is not None:
        club = await db.get(Organization, person.organization_id)
        if club is not None and club.type == CLUB_TYPE and club.status in live:
            return True
    stmt = (
        select(ClubMembership.id)
        .join(Organization, Organization.id == ClubMembership.club_id)
        .where(
            ClubMembership.user_id == person.id,
            ClubMembership.status == membership_service.ACTIVE,
            ClubMembership.role == CLUB_DIRECTOR,
            Organization.status.in_(live),
        )
    )
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _place_admin_club(
    db: AsyncSession,
    actor: User,
    club: Organization,
    association: Organization,
    payload: AdminClubCreate,
    request: Request | None,
) -> dict[str, Organization | None]:
    """Hang the club from its church when there is one to hang it from, else
    from its zone, else leave it under the association (then it shows up in
    `GET /org-nodes/unplaced-clubs`, with the church declared if one was named)."""
    refs: dict[str, Organization | None] = {
        placement.ASSOCIATION: association,
        placement.ZONE: None,
        placement.CHURCH: None,
    }
    has_zone = payload.zone_id is not None or payload.zone_name is not None
    has_church = payload.church_id is not None or payload.church_name is not None

    if has_zone and has_church:
        zone, church = await placement.place_club(
            db,
            club,
            actor=actor,
            association=association,
            church_id=payload.church_id,
            church_name=payload.church_name,
            zone_id=payload.zone_id,
            zone_name=payload.zone_name,
            city=payload.city,
            request=request,
        )
        refs.update({placement.ZONE: zone, placement.CHURCH: church})
        return refs

    if has_zone:
        zone = await placement.resolve_zone(
            db,
            actor=actor,
            association=association,
            zone_id=payload.zone_id,
            zone_name=payload.zone_name,
            request=request,
        )
        await placement.move_node(db, club, zone, actor=actor, action="CLUB_PLACE", request=request)
        club.metadata_json = {
            **(club.metadata_json or {}),
            "placement": {"zone_id": str(zone.id), "resolved_at": utcnow().isoformat()},
        }
        refs[placement.ZONE] = zone
        return refs

    if has_church:
        # The same rule as a director's request: a church that already has a
        # zone takes the club now; otherwise the church is only declared.
        church = await _declared_church(db, association, payload.church_id, payload.church_name)
        zone = (await placement.ancestors_of(db, church)).get(placement.ZONE) if church else None
        if church is not None and zone is not None:
            zone, church = await placement.place_club(
                db,
                club,
                actor=actor,
                association=association,
                church_id=church.id,
                zone_id=zone.id,
                city=payload.city,
                request=request,
            )
            refs.update({placement.ZONE: zone, placement.CHURCH: church})
            return refs
        declared_name = church.name if church is not None else payload.church_name
        club.metadata_json = {
            **(club.metadata_json or {}),
            "church": declared_name,
            "placement": {
                "church_id": str(church.id) if church is not None else None,
                "church_name": declared_name,
                "declared_by": str(actor.id),
                "declared_at": utcnow().isoformat(),
            },
        }
    return refs


async def _appoint_director(
    db: AsyncSession, actor: User, club: Organization, director: User, request: Request | None
) -> None:
    """What approval leaves behind for a founder, reached in one step: an
    ACTIVE CLUB_DIRECTOR membership (the membership service is the only writer
    of `organization_id` and the club role) and `club_approval` APPROVED, which
    also starts the grace period of decision D4."""
    membership = membership_service.stage_membership(
        db,
        user_id=director.id,
        club_id=club.id,
        role=CLUB_DIRECTOR,
        status_name=membership_service.PENDING_APPROVAL,
        source=membership_service.ADMIN,
    )
    await db.flush()
    await membership_service.activate(
        db, membership, actor=actor, member=director, request=request,
        audit_action="MEMBERSHIP_APPROVE",
    )
    now = utcnow()
    director.club_approval = CLUB_APPROVED
    director.club_approval_reason = None
    director.club_approval_at = now
    record_audit(
        db,
        action="CLUB_DIRECTOR_ASSIGN",
        entity_type=placement.ORGANIZATION,
        entity_id=club.id,
        actor=actor,
        details=f"{director.email} appointed director of CLUB {club.name}",
        metadata={"via": VIA_ADMIN, "director_id": str(director.id), "membership_id": str(membership.id)},
        request=request,
    )


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
