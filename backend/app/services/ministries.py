"""The ministry of a club (019_club_ministry.sql).

`organizations.ministry_id` is the truth. Until the backfill of 019 reaches every database,
a club whose column is still NULL falls back to the slug it declared in
`metadata_json.ministry` — the only place it lived before. Nothing here ever GUESSES a
ministry (rule 3 of ESTADO.md): a club that declared none has none.
"""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ministry, Organization
from app.schemas.ministry import MinistryRef

CLUB_TYPE = "club"
ACTIVE = "active"

# Error details the frontend reads as codes.
MINISTRY_REQUIRED = "club_ministry_required"
MINISTRY_NOT_FOUND = "ministry_not_found"
MINISTRY_MISMATCH = "ministry_mismatch"
MINISTRY_NOT_A_CLUB = "ministry_only_for_clubs"
MINISTRY_FORBIDDEN = (
    "Sólo la administración de la asociación (o superior) cambia el ministerio de un club"
)


def as_ref(ministry: Ministry | None) -> MinistryRef | None:
    if ministry is None:
        return None
    return MinistryRef(id=str(ministry.id), slug=ministry.slug, name=ministry.name)


def _declared_slug(node: Organization) -> str | None:
    slug = (node.metadata_json or {}).get("ministry")
    return slug if isinstance(slug, str) and slug else None


async def resolve(
    db: AsyncSession, slug: str | None, ministry_id: uuid.UUID | str | None
) -> Ministry | None:
    """The ACTIVE ministry a body names, by slug and/or id; `None` when it names none.
    An unknown or inactive one is 422 `ministry_not_found`; a slug and an id of two
    different rows is 422 `ministry_mismatch`."""
    if not slug and ministry_id is None:
        return None
    by_id = by_slug = None
    if ministry_id is not None:
        try:
            wanted = ministry_id if isinstance(ministry_id, uuid.UUID) else uuid.UUID(str(ministry_id))
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_NOT_FOUND)
        by_id = await db.get(Ministry, wanted)
        if by_id is None or by_id.status != ACTIVE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_NOT_FOUND)
    if slug:
        by_slug = await db.scalar(select(Ministry).where(Ministry.slug == slug))
        if by_slug is None or by_slug.status != ACTIVE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_NOT_FOUND)
    if by_id is not None and by_slug is not None and by_id.id != by_slug.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_MISMATCH)
    return by_id or by_slug


async def require(db: AsyncSession, slug: str | None, ministry_id: uuid.UUID | str | None) -> Ministry:
    """Like `resolve`, but a new club without a ministry is 422 `club_ministry_required`."""
    ministry = await resolve(db, slug, ministry_id)
    if ministry is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_REQUIRED)
    return ministry


async def of_club(db: AsyncSession, club: Organization) -> tuple[bool, Ministry | None]:
    """-> (the club declares a ministry, the row). The column first; while it is NULL, the
    slug of `metadata_json.ministry` (an unknown slug is declared but matches no row)."""
    if club.ministry_id is not None:
        return True, await db.get(Ministry, club.ministry_id)
    slug = _declared_slug(club)
    if not slug:
        return False, None
    return True, await db.scalar(select(Ministry).where(Ministry.slug == slug))


async def refs_for(db: AsyncSession, nodes: list[Organization]) -> dict[uuid.UUID, MinistryRef]:
    """The ministry of each CLUB among `nodes`, in one query whatever the page size.
    Other node types, and clubs without a ministry, are simply absent."""
    clubs = [node for node in nodes if node.type == CLUB_TYPE]
    ids = {club.ministry_id for club in clubs if club.ministry_id is not None}
    slugs = {_declared_slug(club) for club in clubs if club.ministry_id is None} - {None}
    if not ids and not slugs:
        return {}
    conditions = []
    if ids:
        conditions.append(Ministry.id.in_(ids))
    if slugs:
        conditions.append(Ministry.slug.in_(slugs))

    rows = list((await db.execute(select(Ministry).where(or_(*conditions)))).scalars())
    by_id = {row.id: row for row in rows}
    by_slug = {row.slug: row for row in rows}
    found: dict[uuid.UUID, MinistryRef] = {}
    for club in clubs:
        if club.ministry_id is not None:
            row = by_id.get(club.ministry_id)
        else:
            row = by_slug.get(_declared_slug(club) or "")
        if row is not None:
            found[club.id] = as_ref(row)
    return found


async def ref_of(db: AsyncSession, node: Organization) -> MinistryRef | None:
    return (await refs_for(db, [node])).get(node.id)
