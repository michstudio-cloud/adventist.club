"""Organization tree. Reads are public; every write needs an admin in scope."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import require_roles
from app.models import Organization, User
from app.rbac import get_org_path, is_master, org_in_subtree
from app.schemas.org import ORG_HIERARCHY, OrgNodeCreate, OrgNodeResponse, OrgNodeUpdate
from app.security import ADMIN_ROLES, utcnow
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/org-nodes", tags=["organizations"])

require_org_admin = require_roles(*ADMIN_ROLES)

WRITABLE_COLUMNS = ("name", "code", "city", "state", "country", "latitude", "longitude", "status")


async def _get_node_or_404(db: AsyncSession, node_id: uuid.UUID) -> Organization:
    node = await db.get(Organization, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Org node not found: {node_id}")
    return node


async def _require_in_scope(db: AsyncSession, actor: User, node: Organization) -> None:
    """The node must be the actor's own organization or a descendant of it."""
    if is_master(actor):
        return
    scope_path = await get_org_path(db, actor.organization_id)
    if not await org_in_subtree(db, node.id, scope_path):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This organization is outside your administrative scope"
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
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Organization)
    if type:
        stmt = stmt.where(Organization.type == type.strip().lower())
    if parent_id:
        stmt = stmt.where(Organization.parent_id == parent_id)
    if q:
        stmt = stmt.where(Organization.name.icontains(q, autoescape=True))
    stmt = stmt.order_by(Organization.name, Organization.id).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [OrgNodeResponse.from_model(row) for row in rows]


@router.get("/type/{node_type}", response_model=list[OrgNodeResponse])
async def get_org_nodes_by_type(
    node_type: str,
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Organization)
        .where(Organization.type == node_type.strip().lower())
        .order_by(Organization.name, Organization.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [OrgNodeResponse.from_model(row) for row in rows]


@router.get("/{node_id}", response_model=OrgNodeResponse)
async def get_org_node(node_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return OrgNodeResponse.from_model(await _get_node_or_404(db, node_id))


@router.get("/{node_id}/children", response_model=list[OrgNodeResponse])
async def get_org_node_children(node_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await _get_node_or_404(db, node_id)
    stmt = (
        select(Organization)
        .where(Organization.parent_id == node_id)
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
