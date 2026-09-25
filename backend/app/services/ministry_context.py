"""024 · The ministry and club a signed-in person works in (the shell's selector).

Conquistadores, Aventureros, Guías Mayores (and Jóvenes where a club has it) share one app: the
selector decides which catalogue, classes and certifications the screens show. Rules:

* Available ministries: those of the person's clubs (active membership + the club their account
  is attached to, e.g. a director's) plus `pathfinders`, which is the public default everybody
  already sees without an account. MASTER_GC and the administration (ADMIN_ROLES: zone and
  above) oversee clubs of every ministry: every active one.
* The active ministry: the saved choice (`users.active_ministry_id`) while it is still
  available; otherwise the principal ministry of the active club; otherwise `pathfinders`.
  A choice that stops being available (the person left the club) is ignored, never an error.
* The club switcher: the clubs above, each once. Today a person has at most one ACTIVE
  membership (`club_memberships_one_active_key`), so this is a saved preference that the screens
  read; it never grants anything (permissions keep coming from memberships and roles).

Nothing here guesses a ministry for a club (rule 3 of ESTADO.md): a club without one adds none.
"""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ministry, Organization, User
from app.schemas.ministry import ClubContext, MinistryContext, MinistryRef, PreferencesUpdate
from app.security import ADMIN_ROLES
from app.services import memberships as membership_service
from app.services import ministries as ministry_service

DEFAULT_MINISTRY = "pathfinders"
ACTIVE = "active"
CLUB_TYPE = "club"
# The order the selector offers them in; unknown ones go last, by name.
ORDER = ("pathfinders", "adventurers", "master-guides", "youth")

MINISTRY_NOT_AVAILABLE = "ministry_not_available"
CLUB_NOT_AVAILABLE = "club_not_available"


def _sorted(refs: list[MinistryRef]) -> list[MinistryRef]:
    def rank(ref: MinistryRef) -> tuple[int, str]:
        return (ORDER.index(ref.slug) if ref.slug in ORDER else len(ORDER), ref.name)

    unique: dict[str, MinistryRef] = {}
    for ref in refs:
        unique.setdefault(ref.slug, ref)
    return sorted(unique.values(), key=rank)


async def _clubs(db: AsyncSession, user: User) -> list[tuple[Organization, str | None]]:
    """(club, role) for the active membership's club and the account's club, each once."""
    found: list[tuple[Organization, str | None]] = []
    membership = await membership_service.active_membership(db, user.id)
    if membership is not None:
        club = await db.get(Organization, membership.club_id)
        if club is not None and club.type == CLUB_TYPE and club.status == ACTIVE:
            found.append((club, membership.role))
    if user.organization_id is not None and all(club.id != user.organization_id for club, _ in found):
        club = await db.get(Organization, user.organization_id)
        if club is not None and club.type == CLUB_TYPE and club.status == ACTIVE:
            found.append((club, user.role))
    return found


async def _all_active(db: AsyncSession) -> list[MinistryRef]:
    rows = (await db.execute(select(Ministry).where(Ministry.status == ACTIVE))).scalars().all()
    return [ministry_service.as_ref(row) for row in rows]


async def context_for(db: AsyncSession, user: User) -> MinistryContext:
    clubs = await _clubs(db, user)
    lists = await ministry_service.lists_for(db, [club for club, _ in clubs])
    clubs_available = [
        ClubContext(id=str(club.id), name=club.name, role=role, ministries=lists.get(club.id, []))
        for club, role in clubs
    ]

    if user.role in ADMIN_ROLES:
        available = await _all_active(db)
    else:
        available = [ref for club in clubs_available for ref in club.ministries]
        default = await db.scalar(
            select(Ministry).where(Ministry.slug == DEFAULT_MINISTRY, Ministry.status == ACTIVE)
        )
        if default is not None:
            available.append(ministry_service.as_ref(default))
    available = _sorted(available)
    slugs = [ref.slug for ref in available]

    active_club = next((club for club in clubs_available if club.id == str(user.active_club_id)), None)
    if active_club is None and clubs_available:
        active_club = clubs_available[0]

    active: str | None = None
    if user.active_ministry_id is not None:
        chosen = next((ref for ref in available if ref.id == str(user.active_ministry_id)), None)
        active = chosen.slug if chosen else None
    if active is None and active_club is not None and active_club.ministries:
        principal = active_club.ministries[0].slug
        active = principal if principal in slugs else None
    if active is None:
        active = DEFAULT_MINISTRY if DEFAULT_MINISTRY in slugs else (slugs[0] if slugs else None)

    return MinistryContext(
        ministries_available=available,
        active_ministry=active,
        clubs_available=clubs_available,
        active_club_id=active_club.id if active_club else None,
    )


async def update_preferences(db: AsyncSession, user: User, payload: PreferencesUpdate) -> None:
    """Save the selector's choice. Only fields present in the body change; `null` resets.
    Accepts only what `context_for` offers: anything else is 422 and nothing is written."""
    fields = payload.model_fields_set
    context = await context_for(db, user)
    if "club_id" in fields:
        if payload.club_id is None:
            user.active_club_id = None
        elif str(payload.club_id) in {club.id for club in context.clubs_available}:
            user.active_club_id = payload.club_id
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, CLUB_NOT_AVAILABLE)
    if "ministry" in fields:
        if payload.ministry is None:
            user.active_ministry_id = None
        else:
            chosen = next((ref for ref in context.ministries_available if ref.slug == payload.ministry), None)
            if chosen is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MINISTRY_NOT_AVAILABLE)
            user.active_ministry_id = uuid.UUID(chosen.id)
    await db.commit()
    await db.refresh(user)


async def me_response(db: AsyncSession, user: User):
    """The person's own record with the selector's context (`MeResponse`)."""
    from app.schemas.user import MeResponse, UserResponse

    base = UserResponse.from_model(user, own=True)
    context = await context_for(db, user)
    return MeResponse(**base.model_dump(), **context.model_dump())
