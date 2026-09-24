"""Registered clubs for the certificate assistant's «Club» field (owner, 2026-09-24).

«En el nombre del club sería bueno poder escribir y salgan los clubes registrados.» The field is
used without an account too, so the lookup is public, but it answers only what anybody may read
of an ACTIVE club: its name, its ministry, where it is (city · state) and the association it hangs
from (the line «Marco multicolor» and «Especialidad dorada» print under the church). Never the
director, the members or the coordinates; pending and rejected clubs never show up.

`/clubs/lookup/mine` (session) is the viewer's own club, so the assistant can start with it.

Its own router, not `routers/org.py`: both paths have two or three segments, so the `/{node_id}`
routes of that router never shadow them, whatever the include order.
"""
import unicodedata

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Organization, User
from app.rate_limit import limiter
from app.rbac import member_club
from app.schemas.ministry import MinistryRef
from app.schemas.org import OrgRef
from app.services import clubs as club_service
from app.services import memberships as membership_service
from app.services import ministries as ministry_service
from app.services import placement

router = APIRouter(prefix="/api/v1/org-nodes", tags=["organizations"])

LOOKUP_LIMIT = 8


class ClubLookup(BaseModel):
    """What the assistant shows of a registered club, and nothing else."""

    id: str
    name: str
    ministry: MinistryRef | None = None
    city: str | None = None
    state: str | None = None
    # Nearest ancestor of type association (`organizations.path`); None for an unplaced club.
    association: OrgRef | None = None


def _unaccented(term: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", term) if not unicodedata.combining(c))


async def _lookups(db: AsyncSession, clubs: list[Organization]) -> list[ClubLookup]:
    ministries = await ministry_service.refs_for(db, clubs)
    ancestors = await placement.refs_for(db, clubs)
    return [
        ClubLookup(
            id=str(club.id),
            name=club.name,
            ministry=ministries.get(club.id),
            city=club.city,
            state=club.state,
            association=placement.as_ref(ancestors.get(club.id, {}).get(placement.ASSOCIATION)),
        )
        for club in clubs
    ]


def is_active_club(node: Organization | None) -> bool:
    return node is not None and node.type == club_service.CLUB_TYPE and node.status == club_service.STATUS_ACTIVE


@router.get("/clubs/lookup", response_model=list[ClubLookup])
@limiter.limit("60/minute")
async def lookup_clubs(
    request: Request,
    q: str = Query("", max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """Public: up to 8 ACTIVE clubs whose name contains `q` (2+ letters, accent- and
    case-insensitive: «orion» finds «Club Orión»). Names that start with it come first."""
    term = " ".join(q.split())
    if len(term) < 2:
        return []
    needle = _unaccented(term)
    name = func.unaccent(Organization.name)
    starts = name.istartswith(needle, autoescape=True) | name.istartswith(f"club {needle}", autoescape=True)
    stmt = (
        select(Organization)
        .where(
            Organization.type == club_service.CLUB_TYPE,
            Organization.status == club_service.STATUS_ACTIVE,
            name.icontains(needle, autoescape=True),
        )
        .order_by(starts.desc(), Organization.name, Organization.id)
        .limit(LOOKUP_LIMIT)
    )
    return await _lookups(db, list((await db.execute(stmt)).scalars().all()))


@router.get("/clubs/lookup/mine", response_model=ClubLookup | None)
async def my_club_lookup(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """The viewer's club, when it is active: an active membership first, else the club the account
    is attached to (a director's own club). `null` when there is none."""
    membership = await membership_service.active_membership(db, current_user.id)
    club = await db.get(Organization, membership.club_id) if membership is not None else None
    if not is_active_club(club):
        club = await member_club(db, current_user)
    if not is_active_club(club):
        return None
    return (await _lookups(db, [club]))[0]
