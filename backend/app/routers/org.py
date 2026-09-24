"""Organization tree. Reads are public; every write needs an admin in scope.

Club sign-up lives here too: a CLUB_DIRECTOR requests a club (`pending`), a
coordinator of the association approves or rejects it. Pending and rejected
clubs never show up on public reads.
"""
import math
import unicodedata
import uuid
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import not_, or_, select, true, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_db, violated_constraint
from app.deps import get_current_user, get_optional_user, require_roles
from app.models import ClubMembership, Ministry, Organization, User
from app.rbac import can_decide_club, club_scope_paths, get_org_path, is_master, org_in_subtree
from app.schemas.org import (
    CLUB_LIST_STATUSES,
    ORG_HIERARCHY,
    AdminClubCreate,
    AdminClubRow,
    ChurchPlacement,
    ClubApproval,
    ClubDecision,
    ClubLocation,
    ClubPlacement,
    ClubSignup,
    NearbyClub,
    OrgNodeCreate,
    OrgNodeResponse,
    OrgNodeUpdate,
    OrgSearchResult,
    PendingClubResponse,
    PlacementProposal,
    UnplacedClub,
    person_ref,
)
from app.security import ADMIN_ROLES, CLUB_DIRECTOR, utcnow
from app.services import clubs as club_service
from app.services import memberships as membership_service
from app.services import ministries as ministry_service
from app.services import email as email_service
from app.services import placement
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/org-nodes", tags=["organizations"])

require_org_admin = require_roles(*ADMIN_ROLES)

WRITABLE_COLUMNS = (
    "name", "code", "city", "state", "country", "latitude", "longitude", "status",
    # 022: the meeting place as Google Places named it.
    "address", "place_id", "maps_url",
)


async def _get_node_or_404(db: AsyncSession, node_id: uuid.UUID) -> Organization:
    node = await db.get(Organization, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Org node not found: {node_id}")
    return node


async def _out(db: AsyncSession, node: Organization) -> OrgNodeResponse:
    """A node as the API answers it, with the ministries when it is a club."""
    ministries = await ministry_service.list_of(db, node)
    return OrgNodeResponse.from_model(node, ministries[0] if ministries else None, ministries)


async def _outs(db: AsyncSession, nodes: list[Organization]) -> list[OrgNodeResponse]:
    """A page of nodes: the ministries of its clubs come in two queries."""
    lists = await ministry_service.lists_for(db, nodes)
    out = []
    for node in nodes:
        ministries = lists.get(node.id, [])
        out.append(OrgNodeResponse.from_model(node, ministries[0] if ministries else None, ministries))
    return out


async def _club_ministries(db: AsyncSession, club: Organization) -> dict:
    """`ministry` (principal) and `ministries` (all) of one club, as keyword arguments."""
    ministries = await ministry_service.list_of(db, club)
    return {"ministry": ministries[0] if ministries else None, "ministries": ministries}


def _ministry_condition(slug: str):
    """WHERE clause of the `ministry` filter of the club lists: the club works with that
    ministry (any of them, 022), or `none` for the clubs that still have none."""
    return ministry_service.club_has_ministry(slug)


MINISTRY_FILTER_PATTERN = r"^[a-z0-9-]{2,60}$"
MINISTRY_FILTER_HELP = "Ministry slug, or `none` for clubs without one"


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
    rows = list((await db.execute(stmt)).scalars().all())
    return await _outs(db, rows)


@router.get("/search", response_model=list[OrgSearchResult])
async def search_org_nodes(
    q: str = Query("", max_length=100),
    type: str = Query("association", description="ASSOCIATION, UNION, ... (case-insensitive)"),
    within: uuid.UUID | None = Query(
        None, description="Only nodes under this ancestor (zone and church pickers)"
    ),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Public picker search: active nodes of one type, by name or code, with
    the parent's name so that homonyms can be told apart. `within` narrows the
    search to one subtree, which is what the zone and church pickers need."""
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
    if within is not None:
        within_path = await get_org_path(db, within)
        if not within_path:
            return []
        stmt = stmt.where(Organization.path.op("<@")(within_path))
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
    ministry: str | None = Query(
        None, pattern=MINISTRY_FILTER_PATTERN, description=MINISTRY_FILTER_HELP
    ),
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Club requests waiting for a decision, confined to the caller's scope.
    `?ministry=` narrows them to one ministry, or `none` for the requests that
    still have to be given one when approved."""
    paths = await club_scope_paths(db, current_user)
    if paths is not None and not paths:
        return []
    stmt = select(Organization).where(
        Organization.type == club_service.CLUB_TYPE,
        Organization.status == club_service.STATUS_PENDING,
    )
    if paths is not None:
        stmt = stmt.where(or_(*(Organization.path.op("<@")(path) for path in paths)))
    if ministry:
        stmt = stmt.where(_ministry_condition(ministry))
    stmt = stmt.order_by(Organization.created_at, Organization.id).limit(limit).offset(offset)
    clubs = list((await db.execute(stmt)).scalars().all())
    # `club_scope_paths` is the coarse SQL filter; the last word is the same
    # rule that decides (E6): a zone coordinator never sees another zone's.
    clubs = [club for club in clubs if await can_decide_club(db, current_user, club)]
    return await _pending_rows(db, clubs)


async def _pending_rows(db: AsyncSession, clubs: list[Organization]) -> list[PendingClubResponse]:
    """The association, zone and church of each club come from its ANCESTORS:
    a club may hang from its church, and an unplaced one from the association."""
    refs = await placement.refs_for(db, clubs)
    lists = await ministry_service.lists_for(db, clubs)
    rows = []
    for club in clubs:
        found = refs.get(club.id, {})
        ministries = lists.get(club.id, [])
        rows.append(
            PendingClubResponse.build(
                club,
                found.get(placement.ASSOCIATION),
                await club_service.requester_of(db, club),
                zone=found.get(placement.ZONE),
                church=found.get(placement.CHURCH),
                declared=placement.declared_placement(club) or None,
                ministry=ministries[0] if ministries else None,
                ministries=ministries,
            )
        )
    return rows


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
    return await _out(db, club)


# ----------------------------------------------------------------------------
# The administration opens and lists clubs itself. Literal routes: they must
# stay above every `/{node_id}` route.
# ----------------------------------------------------------------------------
require_structure_role = require_roles(*placement.STRUCTURE_ADMIN_ROLES)


@router.post(
    "/clubs/admin", response_model=PendingClubResponse, status_code=status.HTTP_201_CREATED
)
async def create_admin_club(
    payload: AdminClubCreate,
    request: Request,
    current_user: User = Depends(require_structure_role),
    db: AsyncSession = Depends(get_db),
):
    """Open a club already ACTIVE under an association of the caller's scope,
    optionally placed in its zone and church and with its director appointed
    — the state a director's request reaches once approved, in one step."""
    try:
        club, refs, director = await club_service.stage_admin_club(
            db, current_user, payload, request
        )
        response = PendingClubResponse.build(
            club,
            refs.get(placement.ASSOCIATION),
            zone=refs.get(placement.ZONE),
            church=refs.get(placement.CHURCH),
            declared=placement.declared_placement(club) or None,
            director=director,
            **await _club_ministries(db, club),
        )
        await db.commit()
    except IntegrityError as exc:
        # Two people typing the same code at once: the unique index decides.
        await db.rollback()
        if violated_constraint(exc) != "organizations_code_key":
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, club_service.CLUB_CODE_TAKEN)
    return response


@router.get("/clubs/admin", response_model=list[AdminClubRow])
async def list_admin_clubs(
    response: Response,
    q: str | None = Query(None, max_length=100),
    status_filter: Literal[CLUB_LIST_STATUSES] = Query("all", alias="status"),
    association_id: uuid.UUID | None = None,
    ministry: str | None = Query(
        None, pattern=MINISTRY_FILTER_PATTERN, description=MINISTRY_FILTER_HELP
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Every club of the caller's subtree, any status, with its association,
    zone, church, director, ministry and active members. MASTER_GC sees them all; a zone
    coordinator reads their own zone and changes nothing here. Total rows in
    `X-Total-Count`."""
    conditions = [Organization.type == club_service.CLUB_TYPE]
    if not is_master(current_user):
        own_path = await get_org_path(db, current_user.organization_id)
        if not own_path:
            response.headers["X-Total-Count"] = "0"
            return []
        conditions.append(Organization.path.op("<@")(own_path))
    if association_id is not None:
        association_path = await get_org_path(db, association_id)
        if not association_path:
            response.headers["X-Total-Count"] = "0"
            return []
        conditions.append(Organization.path.op("<@")(association_path))
    if status_filter != "all":
        conditions.append(Organization.status == status_filter)
    if ministry:
        conditions.append(_ministry_condition(ministry))
    term = (q or "").strip()
    if term:
        needle = placement.normalize(term)
        conditions.append(
            or_(
                func.unaccent(Organization.name).icontains(needle, autoescape=True),
                func.unaccent(func.coalesce(Organization.code, "")).icontains(
                    needle, autoescape=True
                ),
            )
        )

    total = await db.scalar(select(func.count()).select_from(Organization).where(*conditions))
    response.headers["X-Total-Count"] = str(total or 0)
    stmt = (
        select(Organization)
        .where(*conditions)
        .order_by(Organization.name, Organization.id)
        .limit(limit)
        .offset(offset)
    )
    clubs = list((await db.execute(stmt)).scalars().all())
    return await _admin_rows(db, clubs)


async def _admin_rows(db: AsyncSession, clubs: list[Organization]) -> list[AdminClubRow]:
    """Ancestors, directors and member counts for a whole page: three grouped
    queries, whatever the page size."""
    if not clubs:
        return []
    ids = [club.id for club in clubs]
    refs = await placement.refs_for(db, clubs)
    lists = await ministry_service.lists_for(db, clubs)

    directors: dict[uuid.UUID, User] = {}
    director_rows = (
        await db.execute(
            select(User)
            .where(User.organization_id.in_(ids), User.role == CLUB_DIRECTOR)
            .order_by(User.created_at, User.id)
        )
    ).scalars()
    for person in director_rows:
        directors.setdefault(person.organization_id, person)

    counts = dict(
        (
            await db.execute(
                select(ClubMembership.club_id, func.count())
                .where(
                    ClubMembership.club_id.in_(ids),
                    ClubMembership.status == membership_service.ACTIVE,
                )
                .group_by(ClubMembership.club_id)
            )
        ).all()
    )

    rows = []
    for club in clubs:
        found = refs.get(club.id, {})
        rows.append(
            AdminClubRow(
                id=str(club.id),
                name=club.name,
                code=club.code,
                status=club.status.upper(),
                city=club.city,
                state=club.state,
                country=club.country,
                latitude=club.latitude,
                longitude=club.longitude,
                association=placement.as_ref(found.get(placement.ASSOCIATION)),
                zone=placement.as_ref(found.get(placement.ZONE)),
                church=placement.as_ref(found.get(placement.CHURCH)),
                director=person_ref(directors.get(club.id)),
                members_count=int(counts.get(club.id, 0)),
                ministry=(lists.get(club.id) or [None])[0],
                ministries=lists.get(club.id, []),
                address=club.address,
                place_id=club.place_id,
                maps_url=club.maps_url,
                logo_url=club.logo_url,
                created_at=club.created_at,
                updated_at=club.updated_at,
            )
        )
    return rows


EARTH_RADIUS_KM = 6371.0088


@router.get("/clubs/nearby", response_model=list[NearbyClub])
async def nearby_clubs(
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    radius_km: float = Query(25, gt=0, le=500),
    limit: int = Query(50, ge=1, le=200),
    ministry: str | None = Query(
        None, pattern=MINISTRY_FILTER_PATTERN, description=MINISTRY_FILTER_HELP
    ),
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
    d_lat = func.radians(club.latitude - lat) / 2
    d_lon = func.radians(club.longitude - lon) / 2
    a = func.pow(func.sin(d_lat), 2) + math.cos(math.radians(lat)) * func.cos(func.radians(club.latitude)) * func.pow(func.sin(d_lon), 2)
    distance = (2 * EARTH_RADIUS_KM * func.asin(func.sqrt(func.least(1.0, a)))).label("distance_km")

    stmt = select(club, distance).where(
        club.type == club_service.CLUB_TYPE,
        club.status == club_service.STATUS_ACTIVE,
        club.latitude.is_not(None),
        club.longitude.is_not(None),
        club.latitude.between(lat - lat_delta, lat + lat_delta),
    )
    if lon_delta < 180 and -180 <= lon - lon_delta and lon + lon_delta <= 180:
        stmt = stmt.where(club.longitude.between(lon - lon_delta, lon + lon_delta))
    if ministry:
        stmt = stmt.where(_ministry_condition(ministry))
    stmt = stmt.where(distance <= radius_km).order_by(distance, club.name).limit(limit)

    rows = (await db.execute(stmt)).all()
    # Association, zone and church come from the ANCESTORS of each club: since
    # E6 a club hangs from its church, and an unplaced one from the association.
    refs = await placement.refs_for(db, [node for node, _ in rows])
    lists = await ministry_service.lists_for(db, [node for node, _ in rows])
    return [
        NearbyClub(
            id=str(node.id), name=node.name, distance_km=round(float(km), 2),
            latitude=round(node.latitude, 5), longitude=round(node.longitude, 5),
            city=node.city, state=node.state, country=node.country,
            church=placement.church_name_of(node, refs.get(node.id, {})),
            church_ref=placement.as_ref(refs.get(node.id, {}).get(placement.CHURCH)),
            zone=placement.as_ref(refs.get(node.id, {}).get(placement.ZONE)),
            accepts_requests=membership_service.accepts_requests(node),
            association=placement.as_ref(refs.get(node.id, {}).get(placement.ASSOCIATION)),
            ministry=(lists.get(node.id) or [None])[0],
            ministries=lists.get(node.id, []),
            address=node.address,
            maps_url=node.maps_url,
            logo_url=node.logo_url,
        )
        for node, km in rows
    ]


@router.put("/clubs/{club_id}/location", response_model=OrgNodeResponse)
async def set_club_location(
    club_id: uuid.UUID,
    payload: ClubLocation,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The director pins (or corrects) where their own club meets. Admins in scope may too.

    Since 022 the body may also carry the address Google Places named (`address`,
    `place_id`, `maps_url`) and the city / state / country it filled in; only what the body
    names changes."""
    club = await db.get(Organization, club_id)
    if club is None or club.type != club_service.CLUB_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Club not found")
    own = current_user.organization_id == club.id and current_user.role == club_service.CLUB_DIRECTOR
    admin_in_scope = current_user.role in ADMIN_ROLES and await can_decide_club(db, current_user, club)
    if not own and not admin_in_scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only set the location of your own club")
    changes = payload.model_dump(exclude_unset=True)
    for column in ("latitude", "longitude", "address", "place_id", "maps_url", "city", "state", "country"):
        if column in changes:
            setattr(club, column, changes[column])
    if "address" in changes and "place_id" not in changes:
        # A typed address is not the place Google named before: the old id would lie.
        club.place_id = None
    club.updated_at = utcnow()
    record_audit(db, action="CLUB_LOCATION", entity_type="ORGANIZATION", entity_id=club.id, actor=current_user,
                 details=f"Location of {club.name} set", metadata={key: changes[key] for key in sorted(changes)},
                 request=request)
    await db.commit()
    return await _out(db, club)


# ----------------------------------------------------------------------------
# Zone and church (E6). The association draws the map: it creates zones and
# churches and decides which of them each club belongs to (decision D3).
# ----------------------------------------------------------------------------
@router.get("/unplaced-clubs", response_model=list[UnplacedClub])
async def list_unplaced_clubs(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Active clubs that still hang straight off their association, with the
    church their director declared. Nothing about them is blocked: they keep
    working exactly as before (spec §5.5)."""
    paths = await club_scope_paths(db, current_user)
    if paths is not None and not paths:
        return []
    association = aliased(Organization)
    stmt = (
        select(Organization, association)
        .join(association, association.id == Organization.parent_id)
        .where(
            Organization.type == club_service.CLUB_TYPE,
            Organization.status == club_service.STATUS_ACTIVE,
            association.type == placement.ASSOCIATION,
        )
    )
    if paths is not None:
        stmt = stmt.where(or_(*(Organization.path.op("<@")(path) for path in paths)))
    stmt = stmt.order_by(Organization.name, Organization.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    return [
        UnplacedClub(
            id=str(club.id),
            name=club.name,
            city=club.city,
            association=placement.as_ref(parent),
            declared=placement.declared_placement(club) or None,
            created_at=club.created_at,
        )
        for club, parent in rows
        if await can_decide_club(db, current_user, club)
    ]


@router.put("/clubs/{club_id}/placement-proposal", response_model=OrgNodeResponse)
async def propose_placement(
    club_id: uuid.UUID,
    payload: PlacementProposal,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The director declares their CHURCH — never the zone (decision D3). It
    moves nothing: it writes the declaration the association will resolve."""
    club = await _get_club_or_404(db, club_id)
    if not (current_user.organization_id == club.id and current_user.role == CLUB_DIRECTOR):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo la dirección de ese club declara su iglesia"
        )
    association = await placement.require_association(db, club)
    church = None
    if payload.church_id is not None:
        church = await db.get(Organization, payload.church_id)
        if (
            church is None
            or church.type != placement.CHURCH
            or church.status != club_service.STATUS_ACTIVE
            or not church.path
            or not church.path.startswith(f"{association.path}.")
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "church_id no es una iglesia activa de tu asociación"
            )

    metadata = dict(club.metadata_json or {})
    metadata["placement"] = {
        **(metadata.get("placement") or {}),
        "church_id": str(church.id) if church is not None else None,
        "church_name": church.name if church is not None else payload.church_name,
        "declared_by": str(current_user.id),
        "declared_at": utcnow().isoformat(),
    }
    metadata["church"] = church.name if church is not None else payload.church_name
    club.metadata_json = metadata
    club.updated_at = utcnow()
    record_audit(
        db,
        action="CLUB_PLACEMENT_PROPOSE",
        entity_type="ORGANIZATION",
        entity_id=club.id,
        actor=current_user,
        details=f"Declared church for {club.name}",
        metadata={"placement": metadata["placement"]},
        request=request,
    )
    await db.commit()
    return await _out(db, club)


@router.post("/clubs/{club_id}/place", response_model=PendingClubResponse)
async def place_club(
    club_id: uuid.UUID,
    payload: ClubPlacement,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Move a club under its church, assigning the zone. Ids never change, so
    members, memberships, enrollments and certificates are untouched."""
    club = await _get_club_or_404(db, club_id)
    if not await can_decide_club(db, current_user, club):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This club is outside your administrative scope"
        )
    association = await placement.require_association(db, club)
    zone, church = await placement.place_club(
        db,
        club,
        actor=current_user,
        association=association,
        church_id=payload.church_id,
        church_name=payload.church_name,
        zone_id=payload.zone_id,
        zone_name=payload.zone_name,
        city=payload.city,
        request=request,
    )
    response = PendingClubResponse.build(
        club,
        association,
        await club_service.requester_of(db, club),
        zone=zone,
        church=church,
        declared=placement.declared_placement(club) or None,
        **await _club_ministries(db, club),
    )
    await db.commit()
    return response


@router.post("/churches/{church_id}/place", response_model=OrgNodeResponse)
async def place_church(
    church_id: uuid.UUID,
    payload: ChurchPlacement,
    request: Request,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """The association moves a church — with its clubs — from one of its zones
    to another. The zone coordinators' scope follows the tree, so it changes on
    its own (spec §5.5)."""
    church = await _get_node_or_404(db, church_id)
    if church.type != placement.CHURCH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ese nodo no es una iglesia")
    placement.require_structure_admin(current_user)
    await _require_in_scope(db, current_user, church)
    zone = await db.get(Organization, payload.zone_id)
    if zone is None or zone.type != placement.ZONE or zone.status != club_service.STATUS_ACTIVE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "zone_id no es una zona activa")
    await _require_in_scope(db, current_user, zone)
    association = await placement.association_of(db, church)
    if association is None or not zone.path or not zone.path.startswith(f"{association.path}."):
        raise HTTPException(status.HTTP_403_FORBIDDEN, placement.OUTSIDE_ASSOCIATION)
    await placement.move_node(
        db, church, zone, actor=current_user, action="CHURCH_PLACE", request=request
    )
    await db.commit()
    return await _out(db, church)


async def _get_club_or_404(db: AsyncSession, club_id: uuid.UUID) -> Organization:
    club = await db.get(Organization, club_id)
    if club is None or club.type != club_service.CLUB_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Club not found")
    return club


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
    rows = list((await db.execute(stmt)).scalars().all())
    return await _outs(db, rows)


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
    return await _out(db, node)


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
    rows = list((await db.execute(stmt)).scalars().all())
    return await _outs(db, rows)


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

    if payload.type in (placement.ZONE, placement.CHURCH):
        # E6 / decision D3: the association draws its own map. A zone
        # coordinator administers what is inside their zone but never creates
        # structure, and a director never creates any.
        placement.require_structure_admin(current_user)

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

    # A club always says its ministries (the schema already refused a club without one).
    ministries = (
        await ministry_service.require_choice(db, payload)
        if payload.type == club_service.CLUB_TYPE
        else None
    )

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
        address=payload.address,
        place_id=payload.place_id,
        maps_url=payload.maps_url,
        created_at=now,
        updated_at=now,
    )
    db.add(node)
    if ministries:
        await ministry_service.set_club_ministries(db, node, ministries)
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
    return await _out(db, node)


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
    if node.type in (placement.ZONE, placement.CHURCH):
        placement.require_structure_admin(current_user)
    await _require_in_scope(db, current_user, node)
    _require_decided(node)

    changes = payload.model_dump(exclude_unset=True)
    changes.pop("location", None)  # already folded into latitude/longitude
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No update data provided")
    for required in ("name", "status"):
        if required in changes and changes[required] is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{required} cannot be null")
    ministries = previous = None
    if "ministry" in changes or "ministry_id" in changes or "ministries" in changes:
        ministries, previous = await _new_club_ministries(db, current_user, node, payload)

    if changes.get("status") == "inactive" and node.status != "inactive":
        await _require_no_active_children(db, node)

    for column in WRITABLE_COLUMNS:
        if column in changes:
            setattr(node, column, changes[column])
    if "metadata" in changes:
        node.metadata_json = changes["metadata"]
    if "address" in changes and "place_id" not in changes:
        # A typed address is not the place Google named before: the old id would lie.
        node.place_id = None
    audit_metadata: dict = {"fields": sorted(changes)}
    if ministries is not None:
        await ministry_service.set_club_ministries(db, node, ministries)
        audit_metadata["ministry"] = {"from": previous[0] if previous else None, "to": ministries[0].slug}
        audit_metadata["ministries"] = {"from": previous, "to": [m.slug for m in ministries]}
    node.updated_at = utcnow()

    record_audit(
        db,
        action="UPDATE",
        entity_type="ORGANIZATION",
        entity_id=node.id,
        actor=current_user,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata=audit_metadata,
        request=request,
    )
    await db.commit()
    return await _out(db, node)


async def _new_club_ministries(
    db: AsyncSession, actor: User, node: Organization, payload: OrgNodeUpdate
) -> tuple[list[Ministry], list[str]]:
    """The ministries a PATCH gives a club (principal first), and the slugs it had. Only a
    CLUB has them; only the administration of its association or above changes them (a zone
    coordinator and the club's own director never do); and never fewer than one."""
    if node.type != club_service.CLUB_TYPE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ministry_service.MINISTRY_NOT_A_CLUB)
    if not placement.is_structure_admin(actor):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ministry_service.MINISTRY_FORBIDDEN)
    ministries = await ministry_service.require_choice(db, payload)
    _declared, previous = await ministry_service.all_of_club(db, node)
    return ministries, [row.slug for row in previous]


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
    approval: ClubApproval | None = None,
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

    declared = placement.declared_placement(club)
    zone = church = None
    association = await placement.association_of(db, club)
    extra: dict = {}
    if approve:
        # E6: whoever approves may correct what the director declared, and
        # ASSIGNS the zone. A club never becomes `active` without both.
        if association is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "El club no cuelga de ninguna asociación"
            )
        extra = {"declared": declared or None}
        # Rule 3 of ESTADO.md: a club never becomes `active` without a ministry.
        ministries, previous = await _approved_club_ministries(db, actor, club, approval)
        await ministry_service.set_club_ministries(db, club, ministries)
        extra["ministry"] = {"from": previous[0] if previous else None, "to": ministries[0].slug}
        extra["ministries"] = {"from": previous, "to": [m.slug for m in ministries]}
        if approval is not None and approval.club_name:
            await _require_unique_club_name(db, association, approval.club_name, club.id)
            club.name = approval.club_name
        if approval is not None and approval.city:
            club.city = approval.city
        zone, church = await _resolve_club_placement(
            db, club, actor=actor, association=association, approval=approval, request=request
        )
        extra["zone_id"] = str(zone.id)
        extra["church_id"] = str(church.id)
        extra["final_name"] = club.name

    director = await club_service.stage_club_decision(
        db, club, actor, approve=approve, reason=reason, request=request, extra_metadata=extra
    )
    if association is None:
        association = await placement.association_of(db, club)
    response = PendingClubResponse.build(
        club,
        association,
        director,
        zone=zone,
        church=church,
        declared=declared or None,
        **await _club_ministries(db, club),
    )
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


async def _approved_club_ministries(
    db: AsyncSession, actor: User, club: Organization, approval: ClubApproval | None
) -> tuple[list[Ministry], list[str]]:
    """The ministries a club is approved with (principal first), and the slugs it had. The
    request's own stand unless the body names others; a request without any (older than the
    registration asking for them) is 422 `club_ministry_required` until the body names one.
    Filling in missing ministries is part of deciding the request; CHANGING the set the
    director chose — adding, removing or swapping one — is the administration's (association
    or above), as in `PATCH /org-nodes/{id}`. The same set in another order is no change."""
    wanted = None
    if approval is not None and approval.names_a_ministry:
        wanted = await ministry_service.require_choice(db, approval)
    _declared, current = await ministry_service.all_of_club(db, club)
    previous = [row.slug for row in current]
    if wanted is None:
        if not current:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, ministry_service.MINISTRY_REQUIRED
            )
        return current, previous
    if current and {row.id for row in wanted} == {row.id for row in current}:
        return current, previous
    if current and not placement.is_structure_admin(actor):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ministry_service.MINISTRY_FORBIDDEN)
    return wanted, previous


async def _require_unique_club_name(
    db: AsyncSession, association: Organization, name: str, club_id: uuid.UUID
) -> None:
    duplicate = select(Organization.id).where(
        Organization.type == club_service.CLUB_TYPE,
        Organization.status.in_((club_service.STATUS_ACTIVE, club_service.STATUS_PENDING)),
        Organization.path.op("<@")(association.path),
        func.lower(Organization.name) == name.lower(),
        Organization.id != club_id,
    )
    if (await db.execute(duplicate.limit(1))).scalar_one_or_none():
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A club with this name already exists in the association"
        )


async def _resolve_club_placement(
    db: AsyncSession,
    club: Organization,
    *,
    actor: User,
    association: Organization,
    approval: ClubApproval | None,
    request: Request,
) -> tuple[Organization, Organization]:
    """The zone and the church the club ends up under. What the body says wins;
    what it leaves out falls back to where the club already is and, failing
    that, to what the director declared."""
    ancestors = await placement.ancestors_of(db, club)
    declared = placement.declared_placement(club)
    body = approval or ClubApproval()

    zone_id, zone_name = body.zone_id, body.zone_name
    if zone_id is None and zone_name is None and ancestors.get(placement.ZONE) is not None:
        zone_id = ancestors[placement.ZONE].id
    if zone_id is None and zone_name is None:
        raise HTTPException(status.HTTP_409_CONFLICT, placement.NOT_PLACED_DETAIL)

    church_id, church_name = body.church_id, body.church_name
    if church_id is None and church_name is None:
        if ancestors.get(placement.CHURCH) is not None:
            church_id = ancestors[placement.CHURCH].id
        elif declared.get("church_id"):
            church_id = uuid.UUID(str(declared["church_id"]))
        elif declared.get("church_name"):
            church_name = declared["church_name"]
        else:
            raise HTTPException(status.HTTP_409_CONFLICT, placement.NOT_PLACED_DETAIL)

    return await placement.place_club(
        db,
        club,
        actor=actor,
        association=association,
        church_id=church_id,
        church_name=church_name,
        zone_id=zone_id,
        zone_name=zone_name,
        city=body.city,
        request=request,
    )


@router.post("/{node_id}/approve", response_model=PendingClubResponse)
async def approve_club(
    node_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    payload: ClubApproval | None = None,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve the request and, in the same act, place the club: whoever
    approves may correct the club's name, city and church, and ASSIGNS the
    zone. Without a zone and a church the club does not become `active`."""
    return await _decide_club(
        node_id,
        approve=True,
        reason=None,
        request=request,
        background=background,
        actor=current_user,
        db=db,
        approval=payload,
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
