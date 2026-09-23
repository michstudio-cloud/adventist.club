"""Public profile and XP (Bloque G).

Spec: docs/superpowers/specs/2026-09-23-perfil-publico.md §3 and §4.1.

Thin: permissions in `app/rbac.py`, the work in `app/services/profiles.py` and
`app/services/xp.py`. A profile the viewer may not see is a 404, never a 403: the answer
must not reveal that the person exists.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import get_db
from app.deps import get_current_user, get_optional_user
from app.models import ClubMembership, User, XpAward
from app.people import is_minor_user
from app.rbac import can_award_xp, can_manage_members, can_view_roster, may_handle_minors
from app.rate_limit import limiter
from app.schemas.profile import (
    ClubXpMember,
    ClubXpSummary,
    ClubXpUnit,
    MyProfile,
    MyXp,
    ProfileXp,
    PublicProfile,
    XpAwardCreate,
    XpAwardCreated,
    XpAwardOut,
    XpAwardPage,
    XpBySource,
)
from app.security import COUNSELOR, utcnow
from app.services import memberships as membership_service
from app.services import profiles as profile_service
from app.services import units as unit_service
from app.services import xp

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])
clubs_router = APIRouter(prefix="/api/v1/clubs", tags=["profiles"])

AWARDS_PAGE = 50


def _award_out(award: XpAward, awarded_by_name: str | None) -> XpAwardOut:
    return XpAwardOut(
        id=str(award.id),
        user_id=str(award.user_id),
        club_id=str(award.club_id),
        category=award.category,
        points=award.points,
        note=award.note,
        occurred_on=award.occurred_on,
        created_at=award.created_at,
        awarded_by_name=awarded_by_name,
    )


# ----------------------------------------------------------------------------
# Profiles. `/me` and `/me/xp` are declared before `/{handle_or_id}` on purpose
# (and `me` is a reserved handle).
# ----------------------------------------------------------------------------
@router.get("/me", response_model=MyProfile)
async def my_profile(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await profile_service.build_profile(db, current_user, current_user, as_owner=True)


@router.get("/me/xp", response_model=MyXp)
async def my_xp(
    limit: int = Query(AWARDS_PAGE, ge=1, le=AWARDS_PAGE),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Own XP by source, the conduct bar and the history of awards (newest first)."""
    breakdown = await xp.xp_of(db, current_user.id)
    _, conduct_points = await xp.fetch_award_totals(db, current_user.id)
    level = xp.level_for(breakdown.total)
    awarded_by = aliased(User)
    rows = (
        await db.execute(
            select(XpAward, awarded_by.name)
            .outerjoin(awarded_by, awarded_by.id == XpAward.awarded_by_id)
            .where(XpAward.user_id == current_user.id)
            .order_by(XpAward.occurred_on.desc(), XpAward.created_at.desc(), XpAward.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    total = (
        await db.execute(select(func.count()).where(XpAward.user_id == current_user.id))
    ).scalar_one()
    return MyXp(
        xp=ProfileXp(
            total=breakdown.total,
            level=level.level,
            level_name=level.name,
            next_level_at=level.next_level_at,
        ),
        by_source=XpBySource(**breakdown.as_dict()),
        conduct=profile_service.conduct_bar(xp.conduct_score(conduct_points)),
        awards=XpAwardPage(
            items=[_award_out(award, name) for award, name in rows],
            total=total,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/{handle_or_id}", response_model=PublicProfile)
@limiter.limit("60/minute")
async def get_profile(
    request: Request,
    handle_or_id: str,
    viewer: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Anyone may ask; only an adult with `public` visibility answers without a session."""
    return await profile_service.profile_for(db, viewer, handle_or_id)


# ----------------------------------------------------------------------------
# XP awards (§4.1)
# ----------------------------------------------------------------------------
@clubs_router.post(
    "/{club_id}/members/{membership_id}/xp",
    response_model=XpAwardCreated,
    status_code=status.HTTP_201_CREATED,
)
async def award_xp(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: XpAwardCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await membership_service.get_club(db, club_id)
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    member = await db.get(User, membership.user_id)
    if member is None or not await can_award_xp(db, current_user, club, membership, member):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "xp_award_forbidden")
    if membership.status != membership_service.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "membership_not_active")

    award, positive = await xp.create_award(
        db,
        actor=current_user,
        club=club,
        membership=membership,
        member=member,
        category=payload.category,
        points=payload.points,
        note=payload.note,
        occurred_on=payload.occurred_on,
        request=request,
    )
    await db.commit()
    conduct = (await xp.conduct_of(db, [member.id]))[member.id]
    return XpAwardCreated(
        **_award_out(award, current_user.name).model_dump(),
        week_positive_points=positive,
        week_cap_remaining=max(0, xp.WEEKLY_CAP - positive),
        conduct=profile_service.conduct_bar(conduct),
    )


@clubs_router.get("/{club_id}/xp", response_model=ClubXpSummary)
async def club_xp(
    club_id: uuid.UUID,
    week: str | None = Query(None, description="ISO week, e.g. 2026-W39; default: this week"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Points of the week and conduct bar of every ACTIVE member, for the club's staff.
    The same reading scope as the roster: a counselor sees their units, and staff who may
    not handle minors (E7) do not see minors."""
    club = await membership_service.get_club(db, club_id)
    if not await can_view_roster(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "club_xp_forbidden")
    monday, sunday, label = xp.parse_iso_week(week, utcnow().date())
    manages = await can_manage_members(db, current_user, club)

    stmt = (
        select(ClubMembership, User)
        .join(User, User.id == ClubMembership.user_id)
        .where(ClubMembership.club_id == club.id, ClubMembership.status == membership_service.ACTIVE)
    )
    if not manages and current_user.role == COUNSELOR:
        mine = await unit_service.counselor_unit_ids(db, club.id, current_user.id)
        stmt = stmt.where(ClubMembership.unit_id.in_(mine or [uuid.uuid4()]))
    rows = (await db.execute(stmt.order_by(User.name, User.id))).all()
    hide_minors = not manages and not may_handle_minors(current_user)
    rows = [(m, u) for m, u in rows if not (hide_minors and is_minor_user(u))]

    user_ids = [user.id for _, user in rows]
    points = await xp.week_points(db, club.id, user_ids, monday)
    conduct = await xp.conduct_of(db, user_ids)
    units = await unit_service.units_by_id(db, club.id)

    members = []
    by_unit: dict[uuid.UUID, int] = {}
    for membership, user in rows:
        net, positive = points[user.id]
        members.append(
            ClubXpMember(
                membership_id=str(membership.id),
                user_id=str(user.id),
                name=user.name,
                handle=user.handle,
                unit_id=str(membership.unit_id) if membership.unit_id else None,
                points_week=net,
                positive_points_week=positive,
                cap_remaining=max(0, xp.WEEKLY_CAP - positive),
                conduct=conduct[user.id],
            )
        )
        if membership.unit_id in units:
            by_unit[membership.unit_id] = by_unit.get(membership.unit_id, 0) + net
    return ClubXpSummary(
        week=label,
        starts_on=monday,
        ends_on=sunday,
        club_points_week=sum(member.points_week for member in members),
        members=members,
        units=sorted(
            (
                ClubXpUnit(unit_id=str(unit_id), name=units[unit_id].name, points_week=total)
                for unit_id, total in by_unit.items()
            ),
            key=lambda unit: (-unit.points_week, unit.name),
        ),
    )
