"""Organization tree. Reads are public; every write needs an admin in scope.

Club sign-up lives here too: a CLUB_DIRECTOR requests a club (`pending`), a
coordinator of the association approves or rejects it. Pending and rejected
clubs never show up on public reads.
"""
import math
import unicodedata
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import not_, or_, select, true, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_db
from app.deps import get_current_user, get_optional_user, require_roles
from app.models import Organization, User
from app.rbac import can_decide_club, club_scope_paths, get_org_path, is_master, org_in_subtree
from app.schemas.org import (
    ORG_HIERARCHY,
    ClubDecision,
    ClubLocation,
    ClubSignup,
    NearbyClub,
    OrgNodeCreate,
    OrgNodeResponse,
    OrgNodeUpdate,
    OrgRef,
    OrgSearchResult,
    PendingClubResponse,
)
from app.security import ADMIN_ROLES, utcnow
from app.services import clubs as club_service
from app.services import email as email_service
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/org-nodes", tags=["organizations"])

require_org_admin = require_roles(*ADMIN_ROLES)

WRITABLE_COLUMNS = ("name", "code", "city", "state", "country", "latitude", "longitude", "status")


async def _get_node_or_404(db: AsyncSession, node_id: uuid.UUID) -> Organization:
    node = await db.get(Organization, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Org node not found: {node_id}")
    return node


async def _visible_to(db: AsyncSession, viewer: User | None):
    """
    WHERE clause for reads. Club requests (`pending` / `rejected`) are hidden
    from everyone except the director attached to them and the coordinators
    who can decide on them.
    """
    public = not_(Organization.status.in_(club_service.HIDDEN_STATUSES))
    if viewer is None:
        return public
    allowed = [public]
    if viewer.organization_id is not None:
        allowed.append(Organization.id == viewer.organization_id)
    paths = await club_scope_paths(db, viewer)
    if paths is None:
        return true()
    allowed.extend(Organization.path.op("<@")(path) for path in paths)
    return or_(*allowed)


async def _require_in_scope(db: AsyncSession, actor: User, node: Organization) -> None:
    """The node must be the actor's own organization or a descendant of it."""
    if is_master(actor):
        return
    scope_path = await get_org_path(db, actor.organization_id)
    if not await org_in_subtree(db, node.id, scope_path):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This organization is outside your administrative scope"
        )


def _require_decided(node: Organization) -> None:
    """A club request changes state only through approve / reject, which also
    update the director and write the audit trail."""
    if node.status == club_service.STATUS_PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This club request is pending: use approve or reject"
        )


def _require_hierarchy_node(node: Organization) -> None:
    """Free-form rows (e.g. `club_network` from the certificates flow) are not
    part of the managed tree and are never modified through this API."""
    if node.type not in ORG_HIERARCHY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Nodes of type {node.type.upper()} are not managed through this endpoint",
        )


# ----------------------------------------------------------------------------
# Public reads. Literal routes first.
# ----------------------------------------------------------------------------
@router.get("", response_model=list[OrgNodeResponse])
@router.get("/", response_model=list[OrgNodeResponse], include_in_schema=False)
async def list_org_nodes(
    type: str | None = Query(None, description="DIVISION, UNION, ... (case-insensitive)"),
    parent_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    viewer: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Organization).where(await _visible_to(db, viewer))
    if type:
        stmt = stmt.where(Organization.type == type.strip().lower())
    if parent_id:
        stmt = stmt.where(Organization.parent_id == parent_id)
    if q:
        stmt = stmt.where(Organization.name.icontains(q, autoescape=True))
    stmt = stmt.order_by(Organization.name, Organization.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [OrgNodeResponse.from_model(row) for row in rows]


@router.get("/search", response_model=list[OrgSearchResult])
async def search_org_nodes(
    q: str = Query("", max_length=100),
    type: str = Query("association", description="ASSOCIATION, UNION, ... (case-insensitive)"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Public picker search: active nodes of one type, by name or code, with
    the parent's name so that homonyms can be told apart."""
    parent = aliased(Organization)
    term = q.strip()
    stmt = (
        select(Organization, parent.name)
        .outerjoin(parent, parent.id == Organization.parent_id)
        .where(
            Organization.type == type.strip().lower(),
            Organization.status == club_service.STATUS_ACTIVE,
        )
    )
    if term:
        # accent- and case-insensitive: people type "asociacion", the directory stores "Asociación"
        needle = "".join(c for c in unicodedata.normalize("NFKD", term) if not unicodedata.combining(c))
        stmt = stmt.where(
            or_(
                func.unaccent(Organization.name).icontains(needle, autoescape=True),
                Organization.code.icontains(term, autoescape=True),
                func.unaccent(parent.name).icontains(needle, autoescape=True),
            )
        )
    stmt = stmt.order_by(Organization.name, Organization.id).limit(limit)
    return [
        OrgSearchResult(
            id=str(node.id),
            name=node.name,
            type=node.type.upper(),
            code=node.code,
            parent_id=str(node.parent_id) if node.parent_id else None,
            parent_name=parent_name,
            country=node.country,
        )
        for node, parent_name in (await db.execute(stmt)).all()
    ]


@router.get("/pending-clubs", response_model=list[PendingClubResponse])
async def list_pending_clubs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Club requests waiting for a decision, confined to the caller's scope."""
    paths = await club_scope_paths(db, current_user)
    if paths is not None and not paths:
        return []
    association = aliased(Organization)
    stmt = (
        select(Organization, association)
        .outerjoin(association, association.id == Organization.parent_id)
        .where(
            Organization.type == club_service.CLUB_TYPE,
            Organization.status == club_service.STATUS_PENDING,
        )
    )
    if paths is not None:
        stmt = stmt.where(or_(*(Organization.path.op("<@")(path) for path in paths)))
    stmt = stmt.order_by(Organization.created_at, Organization.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    return [
        PendingClubResponse.build(club, parent, await club_service.requester_of(db, club))
        for club, parent in rows
    ]


@router.post("/clubs", response_model=OrgNodeResponse, status_code=status.HTTP_201_CREATED)
async def request_club(
    payload: ClubSignup,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A CLUB_DIRECTOR without a club opens one. It stays `pending` until a
    coordinator of the association approves it."""
    club = await club_service.stage_pending_club(db, current_user, payload, request)
    await db.commit()
    return OrgNodeResponse.from_model(club)


EARTH_RADIUS_KM = 6371.0088


@router.get("/clubs/nearby", response_model=list[NearbyClub])
async def nearby_clubs(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_km: float = Query(25, gt=0, le=500),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Public: active clubs with a pinned location within `radius_km`, nearest first.

    Haversine in SQL (no PostGIS). A bounding box goes first so the
    (latitude, longitude) index prunes the scan; near the poles or the
    antimeridian the box is simply not applied to longitude.
    """
    lat_delta = math.degrees(radius_km / EARTH_RADIUS_KM)
    cos_lat = math.cos(math.radians(lat))
    lon_delta = math.degrees(radius_km / (EARTH_RADIUS_KM * cos_lat)) if cos_lat > 0.01 else 180.0

    club = Organization
    association = aliased(Organization)
    d_lat = func.radians(club.latitude - lat) / 2
    d_lon = func.radians(club.longitude - lon) / 2
    a = func.pow(func.sin(d_lat), 2) + math.cos(math.radians(lat)) * func.cos(func.radians(club.latitude)) * func.pow(func.sin(d_lon), 2)
    distance = (2 * EARTH_RADIUS_KM * func.asin(func.sqrt(func.least(1.0, a)))).label("distance_km")

    stmt = (
        select(club, association, distance)
        .outerjoin(association, association.id == club.parent_id)
        .where(
            club.type == club_service.CLUB_TYPE,
            club.status == club_service.STATUS_ACTIVE,
            club.latitude.is_not(None),
            club.longitude.is_not(None),
            club.latitude.between(lat - lat_delta, lat + lat_delta),
        )
    )
    if lon_delta < 180 and -180 <= lon - lon_delta and lon + lon_delta <= 180:
        stmt = stmt.where(club.longitude.between(lon - lon_delta, lon + lon_delta))
    stmt = stmt.where(distance <= radius_km).order_by(distance, club.name).limit(limit)

    return [
        NearbyClub(
            id=str(node.id), name=node.name, distance_km=round(float(km), 2),
            latitude=round(node.latitude, 5), longitude=round(node.longitude, 5),
            city=node.city, state=node.state, country=node.country,
            church=(node.metadata_json or {}).get("church"),
            association=OrgRef(id=str(parent.id), name=parent.name, code=parent.code) if parent else None,
        )
        for node, parent, km in (await db.execute(stmt)).all()
    ]


@router.put("/clubs/{club_id}/location", response_model=OrgNodeResponse)
async def set_club_location(
    club_id: uuid.UUID,
    payload: ClubLocation,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The director pins (or corrects) where their own club meets. Admins in scope may too."""
    club = await db.get(Organization, club_id)
    if club is None or club.type != club_service.CLUB_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Club not found")
    own = current_user.organization_id == club.id and current_user.role == club_service.CLUB_DIRECTOR
    admin_in_scope = current_user.role in ADMIN_ROLES and await can_decide_club(db, current_user, club)
    if not own and not admin_in_scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only set the location of your own club")
    club.latitude, club.longitude = payload.latitude, payload.longitude
    club.updated_at = utcnow()
    record_audit(db, action="CLUB_LOCATION", entity_type="ORGANIZATION", entity_id=club.id, actor=current_user,
                 details=f"Location of {club.name} set", metadata={"latitude": payload.latitude, "longitude": payload.longitude},
                 request=request)
    await db.commit()
    return OrgNodeResponse.from_model(club)


@router.get("/type/{node_type}", response_model=list[OrgNodeResponse])
async def get_org_nodes_by_type(
    node_type: str,
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    viewer: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Organization)
        .where(Organization.type == node_type.strip().lower(), await _visible_to(db, viewer))
        .order_by(Organization.name, Organization.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [OrgNodeResponse.from_model(row) for row in rows]


@router.get("/{node_id}", response_model=OrgNodeResponse)
async def get_org_node(
    node_id: uuid.UUID,
    viewer: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Organization).where(Organization.id == node_id, await _visible_to(db, viewer))
    node = (await db.execute(stmt)).scalar_one_or_none()
    if node is None:
        # Same answer for "does not exist" and "not yours to see".
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Org node not found: {node_id}")
    return OrgNodeResponse.from_model(node)


@router.get("/{node_id}/children", response_model=list[OrgNodeResponse])
async def get_org_node_children(
    node_id: uuid.UUID,
    viewer: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_node_or_404(db, node_id)
    stmt = (
        select(Organization)
        .where(Organization.parent_id == node_id, await _visible_to(db, viewer))
        .order_by(Organization.name, Organization.id)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [OrgNodeResponse.from_model(row) for row in rows]


# ----------------------------------------------------------------------------
# Writes
# ----------------------------------------------------------------------------
@router.post("", response_model=OrgNodeResponse, status_code=status.HTTP_201_CREATED)
@router.post(
    "/",
    response_model=OrgNodeResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_org_node(
    payload: OrgNodeCreate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    node_id = uuid.uuid4()
    label = node_id.hex

    if payload.parent_id is None:
        if payload.type != ORG_HIERARCHY[0]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Root node must be of type DIVISION")
        if not is_master(current_user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only MASTER_GC can create a root node")
        path = label
    else:
        parent = await db.get(Organization, payload.parent_id)
        if parent is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Parent node not found: {payload.parent_id}"
            )
        await _require_in_scope(db, current_user, parent)
        _validate_hierarchy(parent.type, payload.type)
        if parent.status != "active":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Parent node is inactive")
        if not parent.path:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Parent node has no tree path")
        path = f"{parent.path}.{label}"

    now = utcnow()
    node = Organization(
        id=node_id,
        parent_id=payload.parent_id,
        type=payload.type,
        name=payload.name.strip(),
        code=payload.code,
        status="active",
        path=path,
        city=payload.city,
        state=payload.state,
        country=payload.country,
        latitude=payload.latitude,
        longitude=payload.longitude,
        metadata_json=payload.metadata,
        created_at=now,
        updated_at=now,
    )
    db.add(node)
    record_audit(
        db,
        action="CREATE",
        entity_type="ORGANIZATION",
        entity_id=node.id,
        actor=current_user,
        details=f"Created {node.type.upper()} {node.name}",
        metadata={"parent_id": str(node.parent_id) if node.parent_id else None},
        request=request,
    )
    await db.commit()
    return OrgNodeResponse.from_model(node)


async def _require_no_active_children(db: AsyncSession, node: Organization) -> None:
    active_child = select(Organization.id).where(
        Organization.parent_id == node.id, Organization.status == "active"
    )
    if (await db.execute(active_child.limit(1))).scalar_one_or_none():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Cannot deactivate a node with active children"
        )


def _validate_hierarchy(parent_type: str, child_type: str) -> None:
    if parent_type not in ORG_HIERARCHY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid hierarchy: {parent_type.upper()} cannot have managed children",
        )
    expected_index = ORG_HIERARCHY.index(parent_type) + 1
    if expected_index >= len(ORG_HIERARCHY) or ORG_HIERARCHY[expected_index] != child_type:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid hierarchy: {child_type.upper()} cannot be child of {parent_type.upper()}",
        )


@router.patch("/{node_id}", response_model=OrgNodeResponse)
async def update_org_node(
    node_id: uuid.UUID,
    payload: OrgNodeUpdate,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    node = await _get_node_or_404(db, node_id)
    _require_hierarchy_node(node)
    await _require_in_scope(db, current_user, node)
    _require_decided(node)

    changes = payload.model_dump(exclude_unset=True)
    changes.pop("location", None)  # already folded into latitude/longitude
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No update data provided")
    for required in ("name", "status"):
        if required in changes and changes[required] is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{required} cannot be null")

    if changes.get("status") == "inactive" and node.status != "inactive":
        await _require_no_active_children(db, node)

    for column in WRITABLE_COLUMNS:
        if column in changes:
            setattr(node, column, changes[column])
    if "metadata" in changes:
        node.metadata_json = changes["metadata"]
    node.updated_at = utcnow()

    record_audit(
        db,
        action="UPDATE",
        entity_type="ORGANIZATION",
        entity_id=node.id,
        actor=current_user,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await db.commit()
    return OrgNodeResponse.from_model(node)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_node(
    node_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete. Refused while the node still has active children."""
    node = await _get_node_or_404(db, node_id)
    _require_hierarchy_node(node)
    await _require_in_scope(db, current_user, node)
    _require_decided(node)

    if node.id == current_user.organization_id and not is_master(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot delete your own organization")
    await _require_no_active_children(db, node)

    node.status = "inactive"
    node.updated_at = utcnow()
    record_audit(
        db,
        action="DELETE",
        entity_type="ORGANIZATION",
        entity_id=node.id,
        actor=current_user,
        details=f"Deactivated {node.type.upper()} {node.name}",
        request=request,
    )
    await db.commit()


# ----------------------------------------------------------------------------
# Club requests: approve / reject
# ----------------------------------------------------------------------------
async def _decide_club(
    node_id: uuid.UUID,
    *,
    approve: bool,
    reason: str | None,
    request: Request,
    background: BackgroundTasks,
    actor: User,
    db: AsyncSession,
) -> PendingClubResponse:
    club = await _get_node_or_404(db, node_id)
    # Scope before state, so an outsider learns nothing about the request.
    if not await can_decide_club(db, actor, club):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This club is outside your administrative scope"
        )
    if club.type != club_service.CLUB_TYPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only CLUB nodes can be approved")
    if club.status != club_service.STATUS_PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This club request is already {club.status.upper()}"
        )

    director = await club_service.stage_club_decision(
        db, club, actor, approve=approve, reason=reason, request=request
    )
    association = await db.get(Organization, club.parent_id) if club.parent_id else None
    response = PendingClubResponse.build(club, association, director)
    await db.commit()

    # Only after the commit, and never able to fail the request.
    if director is not None:
        background.add_task(
            email_service.send_club_decision_email,
            director.email,
            director.name,
            club.name,
            approve,
            reason,
        )
    return response


@router.post("/{node_id}/approve", response_model=PendingClubResponse)
async def approve_club(
    node_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _decide_club(
        node_id,
        approve=True,
        reason=None,
        request=request,
        background=background,
        actor=current_user,
        db=db,
    )


@router.post("/{node_id}/reject", response_model=PendingClubResponse)
async def reject_club(
    node_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    payload: ClubDecision | None = None,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await _decide_club(
        node_id,
        approve=False,
        reason=payload.reason if payload else None,
        request=request,
        background=background,
        actor=current_user,
        db=db,
    )
