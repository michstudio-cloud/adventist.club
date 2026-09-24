"""The ministries of a club (019_club_ministry.sql, 022_club_ministries.sql).

A club may work with SEVERAL ministries (022): the list lives in `organization_ministries`.
`organizations.ministry_id` is the PRINCIPAL one — the first chosen — and is derived: every
writer goes through `set_club_ministries`, which keeps both in step. The invariant readers
rely on: `ministry_id IS NULL` ⇔ the club has no ministry (a bridge row left behind by a
club whose column was cleared is ignored).

Until the backfill of 019 reaches every database, a club whose column is still NULL falls
back to the slug it declared in `metadata_json.ministry` — the only place it lived before.
Nothing here ever GUESSES a ministry (rule 3 of ESTADO.md): a club that declared none has none.
"""
import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ministry, Organization, OrganizationMinistry
from app.schemas.ministry import MinistryRef
from app.security import utcnow

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


# ----------------------------------------------------------------------------
# Several ministries per club (022)
# ----------------------------------------------------------------------------
MAX_MINISTRIES = 12


async def resolve_choice(db: AsyncSession, choice) -> list[Ministry] | None:
    """The ACTIVE ministries a body names (`ministries: [slug…]`, and/or `ministry` /
    `ministry_id` as a list of one), principal first; `None` when it names none.

    With both a list and a single one, the single one must be in the list (422
    `ministry_mismatch` otherwise) and becomes the principal. Any unknown or inactive slug is
    422 `ministry_not_found`."""
    listed: list[str] = list(getattr(choice, "ministries", None) or [])
    single = await resolve(db, getattr(choice, "ministry", None), getattr(choice, "ministry_id", None))
    if not listed:
        return [single] if single is not None else None
    rows = {
        row.slug: row
        for row in (await db.execute(select(Ministry).where(Ministry.slug.in_(listed)))).scalars()
    }
    chosen: list[Ministry] = []
    for slug in listed:
        row = rows.get(slug)
        if row is None or row.status != ACTIVE:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_NOT_FOUND)
        if row not in chosen:
            chosen.append(row)
    if single is not None:
        if single not in chosen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_MISMATCH)
        chosen.remove(single)
        chosen.insert(0, single)
    return chosen


async def require_choice(db: AsyncSession, choice) -> list[Ministry]:
    """Like `resolve_choice`, but naming none is 422 `club_ministry_required` (minimum one)."""
    chosen = await resolve_choice(db, choice)
    if not chosen:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_REQUIRED)
    return chosen


async def set_club_ministries(db: AsyncSession, club: Organization, ministries: list[Ministry]) -> None:
    """Stage the club's ministries: the bridge rows become exactly `ministries` and the
    principal (`ministry_id`) the first of them. Works for a club not flushed yet (the unit
    of work inserts the organization before its bridge rows)."""
    if not ministries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_REQUIRED)
    club.ministry_id = ministries[0].id
    wanted = [ministry.id for ministry in ministries]
    existing: set[uuid.UUID] = set()
    if club.id is not None:
        existing = set(
            (
                await db.execute(
                    select(OrganizationMinistry.ministry_id).where(
                        OrganizationMinistry.organization_id == club.id
                    )
                )
            ).scalars()
        )
        stale = existing - set(wanted)
        if stale:
            await db.execute(
                delete(OrganizationMinistry).where(
                    OrganizationMinistry.organization_id == club.id,
                    OrganizationMinistry.ministry_id.in_(stale),
                )
            )
    # `created_at` keeps the order in which they were chosen (the principal is always first).
    start = utcnow()
    for index, ministry_id in enumerate(wanted):
        if ministry_id not in existing:
            db.add(
                OrganizationMinistry(
                    organization_id=club.id,
                    ministry_id=ministry_id,
                    created_at=start + timedelta(microseconds=index),
                )
            )


async def lists_for(db: AsyncSession, nodes: list[Organization]) -> dict[uuid.UUID, list[MinistryRef]]:
    """Every ministry of each CLUB among `nodes`, principal first, in two queries whatever the
    page size. A club without a principal has none (legacy: its declared slug, as a list of
    one); a principal without bridge rows (a column written by hand) is a list of one."""
    clubs = [node for node in nodes if node.type == CLUB_TYPE]
    if not clubs:
        return {}
    principal = await refs_for(db, clubs)
    with_column = [club.id for club in clubs if club.ministry_id is not None]
    extra: dict[uuid.UUID, list[MinistryRef]] = {}
    if with_column:
        rows = (
            await db.execute(
                select(OrganizationMinistry.organization_id, Ministry)
                .join(Ministry, Ministry.id == OrganizationMinistry.ministry_id)
                .where(OrganizationMinistry.organization_id.in_(with_column))
                .order_by(
                    OrganizationMinistry.organization_id,
                    OrganizationMinistry.created_at,
                    Ministry.slug,
                )
            )
        ).all()
        for club_id, ministry in rows:
            extra.setdefault(club_id, []).append(as_ref(ministry))
    found: dict[uuid.UUID, list[MinistryRef]] = {}
    for club in clubs:
        first = principal.get(club.id)
        listed = [first] if first is not None else []
        if club.ministry_id is not None:
            listed.extend(ref for ref in extra.get(club.id, []) if first is None or ref.id != first.id)
        found[club.id] = listed
    return found


async def list_of(db: AsyncSession, node: Organization) -> list[MinistryRef]:
    return (await lists_for(db, [node])).get(node.id, [])


async def all_of_club(db: AsyncSession, club: Organization) -> tuple[bool, list[Ministry]]:
    """-> (the club declares a ministry, its rows, principal first). What `of_club` is for one
    ministry, for all of them: the classes tab offers the programs of every one."""
    declared, principal = await of_club(db, club)
    if not declared:
        return False, []
    if club.ministry_id is None:
        return True, [principal] if principal is not None else []
    rows = list(
        (
            await db.execute(
                select(Ministry)
                .join(OrganizationMinistry, OrganizationMinistry.ministry_id == Ministry.id)
                .where(OrganizationMinistry.organization_id == club.id)
                .order_by(OrganizationMinistry.created_at, Ministry.slug)
            )
        ).scalars()
    )
    ordered = [principal] if principal is not None else []
    ordered.extend(row for row in rows if principal is None or row.id != principal.id)
    return True, ordered


def club_has_ministry(slug: str):
    """WHERE clause of the `ministry` filter of the club lists: the club works with that
    ministry (any of its ministries, 022), or `none` for the clubs that still have none."""
    if slug == "none":
        return Organization.ministry_id.is_(None)
    wanted = select(Ministry.id).where(Ministry.slug == slug).scalar_subquery()
    in_bridge = exists().where(
        and_(
            OrganizationMinistry.organization_id == Organization.id,
            OrganizationMinistry.ministry_id == wanted,
        )
    )
    # The principal counts on its own: a column written without its bridge row still matches.
    return and_(Organization.ministry_id.is_not(None), or_(Organization.ministry_id == wanted, in_bridge))
