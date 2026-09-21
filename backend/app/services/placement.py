"""Where a club sits in the tree: association -> zone -> church -> club (E6).

Decision D3, taken by the owner on 22 sep 2026: **the association administers
its zones**. Only it creates and edits them, and only it decides which churches
and clubs belong to each zone. The director declares their CHURCH and their
club — never the zone — and the association accepts or corrects what was
declared and assigns the zone.

Two consequences run through this module:

  * a club is born under its church when the church already has a zone, and
    under the association otherwise, with the declaration kept in
    `metadata_json.placement` until somebody with authority resolves it;
  * moving a node rewrites the `path` of the node AND of its descendants in one
    transaction and never changes an id, so users, memberships, enrollments and
    certificates are not touched. The audit row keeps the previous parent and
    path: undoing a move is another call to the same endpoint.

Everything here only *stages* its change on the caller's session, so the router
commits a change and its audit row together.
"""
import unicodedata
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, User
from app.rbac import org_in_user_scope
from app.schemas.org import ORG_HIERARCHY, OrgRef
from app.security import ADMIN_ASSOCIATION, ADMIN_DIVISION, ADMIN_UNION, MASTER_GC, utcnow
from app.services.audit import record_audit

ASSOCIATION, ZONE, CHURCH, CLUB = "association", "zone", "church", "club"
ACTIVE = "active"
ORGANIZATION = "ORGANIZATION"

# Creating or editing the STRUCTURE (zones and churches) is the association's
# own act, or that of somebody above it. A zone coordinator administers what is
# inside their zone but never draws the map (decision D3).
STRUCTURE_ADMIN_ROLES = (ADMIN_ASSOCIATION, ADMIN_UNION, ADMIN_DIVISION, MASTER_GC)

NOT_PLACED_DETAIL = "Falta ubicar el club (zona e iglesia)"
OUTSIDE_ASSOCIATION = "Ese nodo no pertenece a la asociación del club"
STRUCTURE_FORBIDDEN = (
    "Sólo la administración de la asociación crea o edita zonas e iglesias."
)


def is_structure_admin(actor: User) -> bool:
    return actor.role in STRUCTURE_ADMIN_ROLES


def require_structure_admin(actor: User) -> None:
    if not is_structure_admin(actor):
        raise HTTPException(status.HTTP_403_FORBIDDEN, STRUCTURE_FORBIDDEN)


def normalize(name: str) -> str:
    """Accent- and case-insensitive comparison: «Iglesia Central» and «iglesia
    central» are the same church, and the association is asked to pick the one
    that exists instead of creating a second one."""
    stripped = unicodedata.normalize("NFKD", " ".join((name or "").split()))
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower()


# ----------------------------------------------------------------------------
# Reading the tree
# ----------------------------------------------------------------------------
async def ancestors_of(db: AsyncSession, node: Organization) -> dict[str, Organization]:
    """The association, zone and church above `node`, by `path`, not by
    `parent_id`: a club may hang from its church, from its zone or straight
    from the association (spec §1, finding 4)."""
    if not node.path:
        return {}
    stmt = select(Organization).where(
        Organization.type.in_((ASSOCIATION, ZONE, CHURCH)),
        Organization.path.op("@>")(node.path),
        Organization.id != node.id,
    )
    return {row.type: row for row in (await db.execute(stmt)).scalars().all()}


async def refs_for(db: AsyncSession, clubs: list[Organization]) -> dict[uuid.UUID, dict]:
    """The same thing for a whole page of clubs, in ONE query."""
    if not clubs:
        return {}
    paths = [club.path for club in clubs if club.path]
    if not paths:
        return {club.id: {} for club in clubs}
    stmt = select(Organization).where(
        Organization.type.in_((ASSOCIATION, ZONE, CHURCH)),
        or_(*[Organization.path.op("@>")(path) for path in paths]),
    )
    ancestors = (await db.execute(stmt)).scalars().all()
    out: dict[uuid.UUID, dict] = {}
    for club in clubs:
        found = {}
        for row in ancestors:
            if club.path and row.id != club.id and _is_ancestor(row.path, club.path):
                found[row.type] = row
        out[club.id] = found
    return out


def _is_ancestor(candidate: str, path: str) -> bool:
    return path == candidate or path.startswith(f"{candidate}.")


def as_ref(node: Organization | None) -> OrgRef | None:
    return OrgRef(id=str(node.id), name=node.name, code=node.code) if node else None


def declared_placement(club: Organization) -> dict:
    return (club.metadata_json or {}).get("placement") or {}


def church_name_of(club: Organization, ancestors: dict[str, Organization] | None = None) -> str | None:
    """The church's name: the node when the club is placed, the declaration
    while it is not. `metadata_json.church` is the pre-E6 free-text field."""
    if ancestors and ancestors.get(CHURCH) is not None:
        return ancestors[CHURCH].name
    metadata = club.metadata_json or {}
    return declared_placement(club).get("church_name") or metadata.get("church")


def is_placed(ancestors: dict[str, Organization]) -> bool:
    return ancestors.get(ZONE) is not None and ancestors.get(CHURCH) is not None


# ----------------------------------------------------------------------------
# Creating structure
# ----------------------------------------------------------------------------
async def _child_by_name(
    db: AsyncSession, parent: Organization, node_type: str, name: str
) -> Organization | None:
    stmt = select(Organization).where(
        Organization.parent_id == parent.id,
        Organization.type == node_type,
        Organization.status == ACTIVE,
    )
    target = normalize(name)
    for row in (await db.execute(stmt)).scalars().all():
        if normalize(row.name) == target:
            return row
    return None


async def _descendant_by_name(
    db: AsyncSession, root: Organization, node_type: str, name: str
) -> Organization | None:
    """Anywhere below `root`: a church may already live under another zone."""
    stmt = select(Organization).where(
        Organization.type == node_type,
        Organization.status == ACTIVE,
        Organization.path.op("<@")(root.path),
    )
    target = normalize(name)
    for row in (await db.execute(stmt)).scalars().all():
        if normalize(row.name) == target:
            return row
    return None


async def find_church(
    db: AsyncSession, association: Organization, name: str
) -> Organization | None:
    """The church of that name anywhere in the association, accents and case
    ignored. Used to stop a second «Iglesia Central» being typed into being."""
    return await _descendant_by_name(db, association, CHURCH, name)


def create_node(
    db: AsyncSession,
    *,
    parent: Organization,
    node_type: str,
    name: str,
    actor: User,
    city: str | None = None,
    metadata: dict | None = None,
    request: Request | None = None,
) -> Organization:
    """The same rules `POST /org-nodes` applies, extracted so approving a club
    can create the missing zone or church in the same transaction (spec §5.5)."""
    if parent.type not in ORG_HIERARCHY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El padre no es parte del árbol")
    expected = ORG_HIERARCHY.index(parent.type) + 1
    if expected >= len(ORG_HIERARCHY) or ORG_HIERARCHY[expected] != node_type:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Jerarquía inválida: {node_type.upper()} no cuelga de {parent.type.upper()}",
        )
    if parent.status != ACTIVE or not parent.path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El nodo padre no está activo")

    node_id = uuid.uuid4()
    now = utcnow()
    node = Organization(
        id=node_id,
        parent_id=parent.id,
        type=node_type,
        name=" ".join(name.split()),
        status=ACTIVE,
        path=f"{parent.path}.{node_id.hex}",
        city=city,
        country=parent.country,
        metadata_json=metadata,
        created_at=now,
        updated_at=now,
    )
    db.add(node)
    record_audit(
        db,
        action="CREATE",
        entity_type=ORGANIZATION,
        entity_id=node.id,
        actor=actor,
        details=f"Created {node_type.upper()} {node.name}",
        metadata={"parent_id": str(parent.id)},
        request=request,
    )
    return node


# ----------------------------------------------------------------------------
# Moving a subtree
# ----------------------------------------------------------------------------
MOVE_SQL = text(
    """
    UPDATE organizations
       SET path = text2ltree(:new_parent_path)
                  || subpath(path, nlevel(text2ltree(:old_path)) - 1),
           updated_at = now()
     WHERE path <@ text2ltree(:old_path)
    """
)


async def move_node(
    db: AsyncSession,
    node: Organization,
    new_parent: Organization,
    *,
    actor: User,
    action: str,
    request: Request | None = None,
) -> Organization:
    """Re-parent `node` and rewrite the `path` of it and of everything under it.

    ONE statement, so the subtree is never half-moved, and the ids never change:
    whoever points at this club (users, memberships, enrollments, certificates)
    keeps pointing at the same row. The audit keeps the previous parent and path
    so that undoing the move is another call.
    """
    if not node.path or not new_parent.path:
        raise HTTPException(status.HTTP_409_CONFLICT, "Falta el `path` de algún nodo del árbol")
    if new_parent.id == node.id or _is_ancestor(node.path, new_parent.path):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Un nodo no puede colgar de sí mismo ni de su descendiente"
        )
    if new_parent.status != ACTIVE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El nodo padre no está activo")

    # Lock the row being moved: two people re-parenting it at once would
    # otherwise each rewrite the subtree from a stale prefix.
    locked = select(Organization).where(Organization.id == node.id).with_for_update()
    await db.execute(locked)

    old_path, old_parent_id = node.path, node.parent_id
    await db.flush()
    await db.execute(
        MOVE_SQL, {"new_parent_path": new_parent.path, "old_path": old_path}
    )
    # The statement above already rewrote `path` in the database; assign it on
    # the instance too, so the response and any later read of it agree.
    label = old_path.split(".")[-1]
    node.parent_id = new_parent.id
    node.path = f"{new_parent.path}.{label}"
    node.updated_at = utcnow()
    await db.flush()

    record_audit(
        db,
        action=action,
        entity_type=ORGANIZATION,
        entity_id=node.id,
        actor=actor,
        details=f"{node.type.upper()} {node.name} moved under {new_parent.type.upper()} {new_parent.name}",
        metadata={
            "previous_parent_id": str(old_parent_id) if old_parent_id else None,
            "previous_path": old_path,
            "parent_id": str(new_parent.id),
        },
        request=request,
    )
    return node


# ----------------------------------------------------------------------------
# Resolving the placement of a club
# ----------------------------------------------------------------------------
async def resolve_zone(
    db: AsyncSession,
    *,
    actor: User,
    association: Organization,
    zone_id: uuid.UUID | None,
    zone_name: str | None,
    request: Request | None = None,
) -> Organization:
    """The zone the association assigns. A zone coordinator may only point at
    their OWN zone and never creates one (decision D3)."""
    if zone_id is not None:
        zone = await db.get(Organization, zone_id)
        if zone is None or zone.type != ZONE or zone.status != ACTIVE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "zone_id no es una zona activa")
        if not zone.path or not _is_ancestor(association.path, zone.path):
            raise HTTPException(status.HTTP_403_FORBIDDEN, OUTSIDE_ASSOCIATION)
        if not await org_in_user_scope(db, actor, zone.id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Esa zona está fuera de tu alcance administrativo"
            )
        return zone

    if not zone_name:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_PLACED_DETAIL)
    existing = await _child_by_name(db, association, ZONE, zone_name)
    if existing is not None:
        if not await org_in_user_scope(db, actor, existing.id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Esa zona está fuera de tu alcance administrativo"
            )
        return existing
    require_structure_admin(actor)
    zone = create_node(
        db,
        parent=association,
        node_type=ZONE,
        name=zone_name,
        actor=actor,
        request=request,
    )
    await db.flush()
    return zone


async def resolve_church(
    db: AsyncSession,
    *,
    actor: User,
    association: Organization,
    zone: Organization,
    church_id: uuid.UUID | None,
    church_name: str | None,
    city: str | None = None,
    request: Request | None = None,
) -> Organization:
    """The church of the club, under `zone`.

    A church that already exists somewhere else in the association is never
    duplicated: either it is already under this zone, or the association moves
    it (`POST /org-nodes/churches/{id}/place`), or the caller is told which row
    to pick (409 with its id).
    """
    if church_id is not None:
        church = await db.get(Organization, church_id)
        if church is None or church.type != CHURCH or church.status != ACTIVE:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "church_id no es una iglesia activa")
        if not church.path or not _is_ancestor(association.path, church.path):
            raise HTTPException(status.HTTP_403_FORBIDDEN, OUTSIDE_ASSOCIATION)
        return await _church_under_zone(db, church, zone, actor=actor, request=request)

    if not church_name:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_PLACED_DETAIL)
    existing = await _descendant_by_name(db, association, CHURCH, church_name)
    if existing is not None:
        if existing.parent_id == zone.id:
            return existing
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Ya existe esa iglesia en la asociación (id {existing.id}): elígela en vez de duplicarla.",
        )
    require_structure_admin(actor)
    church = create_node(
        db,
        parent=zone,
        node_type=CHURCH,
        name=church_name,
        actor=actor,
        city=city,
        request=request,
    )
    await db.flush()
    return church


async def _church_under_zone(
    db: AsyncSession,
    church: Organization,
    zone: Organization,
    *,
    actor: User,
    request: Request | None,
) -> Organization:
    if church.parent_id == zone.id:
        return church
    if church.parent_id == zone.parent_id:
        # Still hanging straight off the association: the association places it.
        require_structure_admin(actor)
        return await move_node(db, church, zone, actor=actor, action="CHURCH_PLACE", request=request)
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        f"Esa iglesia (id {church.id}) ya pertenece a otra zona: muévela primero con"
        " POST /org-nodes/churches/{id}/place",
    )


async def place_club(
    db: AsyncSession,
    club: Organization,
    *,
    actor: User,
    association: Organization,
    church_id: uuid.UUID | None = None,
    church_name: str | None = None,
    zone_id: uuid.UUID | None = None,
    zone_name: str | None = None,
    city: str | None = None,
    request: Request | None = None,
) -> tuple[Organization, Organization]:
    """Assign the zone, resolve the church and move the club under it."""
    zone = await resolve_zone(
        db,
        actor=actor,
        association=association,
        zone_id=zone_id,
        zone_name=zone_name,
        request=request,
    )
    church = await resolve_church(
        db,
        actor=actor,
        association=association,
        zone=zone,
        church_id=church_id,
        church_name=church_name,
        city=city or club.city,
        request=request,
    )
    if club.parent_id != church.id:
        await move_node(db, club, church, actor=actor, action="CLUB_PLACE", request=request)
    # The declaration has been honoured; keep it for the record but say so.
    metadata = dict(club.metadata_json or {})
    placement = dict(metadata.get("placement") or {})
    placement.update(
        {"resolved_at": utcnow().isoformat(), "zone_id": str(zone.id), "church_id": str(church.id)}
    )
    metadata["placement"] = placement
    metadata["church"] = church.name
    club.metadata_json = metadata
    return zone, church


async def association_of(db: AsyncSession, node: Organization) -> Organization | None:
    return (await ancestors_of(db, node)).get(ASSOCIATION)


async def require_association(db: AsyncSession, club: Organization) -> Organization:
    association = await association_of(db, club)
    if association is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El club no cuelga de ninguna asociación"
        )
    return association
