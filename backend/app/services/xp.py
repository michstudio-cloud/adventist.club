"""XP, levels and the «barra de buena conducta» (Bloque G §4 and §4.1).

Everything except the director's awards is DERIVED: there is no XP column anywhere.

    Source                                   XP
    requirement approved (COMPLETE)          10
    honor certified                          100 (+50 skill level 2, +100 skill level 3)
    class / program invested                 500
    approved service hour                    5   (at most 200 per calendar month)
    approved attendance                      5
    course completed (COURSE certificate)    50
    director's awards (xp_awards)            their net sum, never below 0

The facts are read with a handful of aggregate queries (`fetch_*`), turned into numbers by
pure functions (`breakdown`, `level_for`, `conduct_score`) and the total is cached in
process for 10 minutes per user (`xp_of`). Awards and certificates invalidate it.
"""
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClubMembership, Organization, User, XpAward
from app.security import utcnow

# ----------------------------------------------------------------------------
# The table of the spec
# ----------------------------------------------------------------------------
XP_REQUIREMENT = 10
XP_HONOR = 100
XP_HONOR_SKILL_BONUS = {2: 50, 3: 100}
XP_INVESTITURE = 500
XP_SERVICE_HOUR = 5
XP_SERVICE_MONTH_CAP = 200
XP_ATTENDANCE = 5
XP_COURSE = 50

# (threshold, name): level N is the N-th row (1-based).
LEVELS = (
    (0, "Explorador"),
    (250, "Rastreador"),
    (750, "Excursionista"),
    (1500, "Guía"),
    (3000, "Pionero"),
    (6000, "Maestro"),
)

AWARD_CATEGORIES = ("conducta", "puntualidad", "uniforme", "participacion", "servicio", "otro")
CONDUCT_CATEGORIES = ("conducta", "puntualidad", "uniforme")
CONDUCT_START = 70
CONDUCT_MIN, CONDUCT_MAX = 0, 100
CONDUCT_WEEKS = 8
WEEKLY_CAP = 100
AWARD_MAX_DAYS_BACK = 90

CACHE_TTL_SECONDS = 600
_CACHE_MAX_ENTRIES = 10_000

XP_AWARD = "XP_AWARD"
MASTER_GUIDE_SLUG = re.compile(r"^guia-mayor(-v\d+)?$")
_VERSION_SUFFIX = re.compile(r"-v\d+$")

REVOKED_STATUS = "revoked"


# ----------------------------------------------------------------------------
# Pure functions
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Level:
    level: int
    name: str
    next_level_at: int | None


def level_for(total: int) -> Level:
    index = 0
    for position, (threshold, _) in enumerate(LEVELS):
        if total >= threshold:
            index = position
    next_at = LEVELS[index + 1][0] if index + 1 < len(LEVELS) else None
    return Level(level=index + 1, name=LEVELS[index][1], next_level_at=next_at)


def honor_xp(skill_level: int | None) -> int:
    return XP_HONOR + XP_HONOR_SKILL_BONUS.get(skill_level or 1, 0)


def service_xp(hours_by_month: dict[tuple[int, int], float]) -> int:
    """5 XP per approved hour, never more than 200 in one calendar month."""
    return sum(
        min(math.floor(hours * XP_SERVICE_HOUR), XP_SERVICE_MONTH_CAP)
        for hours in hours_by_month.values()
    )


def conduct_score(points: int) -> int:
    """The bar: 70 plus the net points of the window, kept inside 0–100."""
    return max(CONDUCT_MIN, min(CONDUCT_MAX, CONDUCT_START + int(points)))


def conduct_window_start(today: date) -> date:
    """First day counted by the bar: the last 8 weeks = the 56 days ending today."""
    return today - timedelta(days=CONDUCT_WEEKS * 7 - 1)


def is_master_guide(slug: str | None) -> bool:
    return bool(slug and MASTER_GUIDE_SLUG.match(slug))


def base_slug(slug: str) -> str:
    """`amigo-v2` -> `amigo`: a badge belongs to the lineage, not to one version."""
    return _VERSION_SUFFIX.sub("", slug)


def parse_iso_week(value: str | None, today: date) -> tuple[date, date, str]:
    """`2026-W39` -> (monday, sunday, label). None = the week of `today`."""
    if value is None:
        year, week, _ = today.isocalendar()
    else:
        match = re.fullmatch(r"(\d{4})-W(\d{2})", value.strip())
        if match is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_week")
        year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_week")
    return monday, monday + timedelta(days=6), f"{year:04d}-W{week:02d}"


@dataclass
class Facts:
    """What the XP is computed from. Filled by `facts_from` out of the `fetch_*` rows."""

    requirements_complete: int = 0
    honor_skill_levels: list[int | None] = field(default_factory=list)
    investitures: int = 0
    courses: int = 0
    service_by_month: dict[tuple[int, int], float] = field(default_factory=dict)
    attendance: float = 0.0
    awards_net: int = 0


@dataclass(frozen=True)
class Breakdown:
    requirements: int
    honors: int
    investitures: int
    service: int
    attendance: int
    courses: int
    awards: int

    @property
    def total(self) -> int:
        return (
            self.requirements + self.honors + self.investitures + self.service
            + self.attendance + self.courses + self.awards
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "requirements": self.requirements,
            "honors": self.honors,
            "investitures": self.investitures,
            "service": self.service,
            "attendance": self.attendance,
            "courses": self.courses,
            "awards": self.awards,
        }


def breakdown(facts: Facts) -> Breakdown:
    return Breakdown(
        requirements=facts.requirements_complete * XP_REQUIREMENT,
        honors=sum(honor_xp(level) for level in facts.honor_skill_levels),
        investitures=facts.investitures * XP_INVESTITURE,
        service=service_xp(facts.service_by_month),
        attendance=math.floor(facts.attendance * XP_ATTENDANCE),
        courses=facts.courses * XP_COURSE,
        # A mistake is corrected with a negative row (§4.1), so the net sum counts; the
        # director's penalties never take the XP below what the member earned elsewhere.
        awards=max(0, int(facts.awards_net)),
    )


# ----------------------------------------------------------------------------
# The facts, read with aggregate queries. The profile reuses the same rows.
# ----------------------------------------------------------------------------
_ENROLLMENTS_SQL = text(
    """
    SELECT e.id, e.honor_id, e.program_id, e.status, e.mode, e.updated_at,
           h.name AS honor_name, h.image_url AS honor_image,
           p.kind AS program_kind, p.slug AS program_slug, p.name AS program_name,
           p.image_url AS program_image,
           (SELECT count(*) FROM requirement_progress rp WHERE rp.enrollment_id = e.id) AS total,
           (SELECT count(*) FROM requirement_progress rp
             WHERE rp.enrollment_id = e.id AND rp.status = 'COMPLETE') AS complete
    FROM honor_enrollments e
    LEFT JOIN honors h ON h.id = e.honor_id
    LEFT JOIN programs p ON p.id = e.program_id
    WHERE e.user_id = :user_id AND e.status <> 'WITHDRAWN'
    ORDER BY e.updated_at DESC
    """
)

_CERTIFICATES_SQL = text(
    """
    SELECT c.id, c.certificate_no, c.issued_date, c.honor_id, c.program_id,
           c.honor_name_snapshot,
           h.name AS honor_name, h.image_url AS honor_image, h.skill_level, h.category_id,
           p.kind AS program_kind, p.slug AS program_slug, p.name AS program_name,
           p.image_url AS program_image,
           e.mode AS enrollment_mode
    FROM certificates c
    LEFT JOIN honors h ON h.id = c.honor_id
    LEFT JOIN programs p ON p.id = c.program_id
    LEFT JOIN honor_enrollments e ON e.id = c.enrollment_id
    WHERE c.user_id = :user_id AND c.revoked_at IS NULL AND c.status <> :revoked
    ORDER BY c.issued_date DESC, c.created_at DESC
    """
)

_ACTIVITY_SQL = text(
    """
    SELECT category, performed_on, sum(quantity) AS quantity
    FROM activity_logs
    WHERE user_id = :user_id AND status = 'APPROVED'
    GROUP BY category, performed_on
    ORDER BY performed_on
    """
)


async def fetch_enrollments(db: AsyncSession, user_id: uuid.UUID) -> list:
    return list((await db.execute(_ENROLLMENTS_SQL, {"user_id": user_id})).mappings().all())


async def fetch_certificates(db: AsyncSession, user_id: uuid.UUID) -> list:
    params = {"user_id": user_id, "revoked": REVOKED_STATUS}
    return list((await db.execute(_CERTIFICATES_SQL, params)).mappings().all())


async def fetch_activity(db: AsyncSession, user_id: uuid.UUID) -> list:
    return list((await db.execute(_ACTIVITY_SQL, {"user_id": user_id})).mappings().all())


async def fetch_award_totals(
    db: AsyncSession, user_id: uuid.UUID, today: date | None = None
) -> tuple[int, int]:
    """(net points of every award, net conduct points of the bar's window)."""
    today = today or utcnow().date()
    conduct = func.coalesce(
        func.sum(XpAward.points).filter(
            XpAward.category.in_(CONDUCT_CATEGORIES),
            XpAward.occurred_on >= conduct_window_start(today),
            XpAward.occurred_on <= today,
        ),
        0,
    )
    stmt = select(func.coalesce(func.sum(XpAward.points), 0), conduct).where(
        XpAward.user_id == user_id
    )
    total, window = (await db.execute(stmt)).one()
    return int(total), int(window)


def facts_from(enrollments: list, certificates: list, activity: list, awards_net: int) -> Facts:
    facts = Facts(awards_net=awards_net)
    facts.requirements_complete = sum(int(row["complete"]) for row in enrollments)
    for row in certificates:
        if row["honor_id"] is not None:
            facts.honor_skill_levels.append(row["skill_level"])
            if row["enrollment_mode"] == "COURSE":
                facts.courses += 1
        elif row["program_id"] is not None:
            facts.investitures += 1
    for row in activity:
        quantity = float(row["quantity"])
        if row["category"] == "SERVICE":
            key = (row["performed_on"].year, row["performed_on"].month)
            facts.service_by_month[key] = facts.service_by_month.get(key, 0.0) + quantity
        elif row["category"] == "ATTENDANCE":
            facts.attendance += quantity
    return facts


# ----------------------------------------------------------------------------
# The 10-minute cache (per process)
# ----------------------------------------------------------------------------
_cache: dict[uuid.UUID, tuple[float, Breakdown]] = {}


def cached(user_id: uuid.UUID) -> Breakdown | None:
    entry = _cache.get(user_id)
    if entry is None:
        return None
    expires, value = entry
    if time.monotonic() >= expires:
        _cache.pop(user_id, None)
        return None
    return value


def prime(user_id: uuid.UUID, value: Breakdown) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        _cache.clear()
    _cache[user_id] = (time.monotonic() + CACHE_TTL_SECONDS, value)


def invalidate(user_id: uuid.UUID | None) -> None:
    """Called when an award is created and when a certificate is issued or annulled."""
    if user_id is not None:
        _cache.pop(user_id, None)


async def xp_of(db: AsyncSession, user_id: uuid.UUID) -> Breakdown:
    value = cached(user_id)
    if value is not None:
        return value
    awards_net, _ = await fetch_award_totals(db, user_id)
    value = breakdown(
        facts_from(
            await fetch_enrollments(db, user_id),
            await fetch_certificates(db, user_id),
            await fetch_activity(db, user_id),
            awards_net,
        )
    )
    prime(user_id, value)
    return value


# ----------------------------------------------------------------------------
# Conduct and weekly points, for many members at once
# ----------------------------------------------------------------------------
async def conduct_of(
    db: AsyncSession, user_ids: list[uuid.UUID], today: date | None = None
) -> dict[uuid.UUID, int]:
    """The bar of each member (70 for whoever has no conduct award in the window)."""
    today = today or utcnow().date()
    scores = {user_id: CONDUCT_START for user_id in user_ids}
    if not user_ids:
        return scores
    stmt = (
        select(XpAward.user_id, func.sum(XpAward.points))
        .where(
            XpAward.user_id.in_(user_ids),
            XpAward.category.in_(CONDUCT_CATEGORIES),
            XpAward.occurred_on >= conduct_window_start(today),
            XpAward.occurred_on <= today,
        )
        .group_by(XpAward.user_id)
    )
    for user_id, points in (await db.execute(stmt)).all():
        scores[user_id] = conduct_score(int(points or 0))
    return scores


async def week_points(
    db: AsyncSession, club_id: uuid.UUID, user_ids: list[uuid.UUID], monday: date
) -> dict[uuid.UUID, tuple[int, int]]:
    """(net, positive) points awarded in `club_id` during the ISO week starting `monday`."""
    result = {user_id: (0, 0) for user_id in user_ids}
    if not user_ids:
        return result
    positive = func.coalesce(func.sum(XpAward.points).filter(XpAward.points > 0), 0)
    stmt = (
        select(XpAward.user_id, func.coalesce(func.sum(XpAward.points), 0), positive)
        .where(
            XpAward.club_id == club_id,
            XpAward.user_id.in_(user_ids),
            XpAward.occurred_on >= monday,
            XpAward.occurred_on <= monday + timedelta(days=6),
        )
        .group_by(XpAward.user_id)
    )
    for user_id, net, pos in (await db.execute(stmt)).all():
        result[user_id] = (int(net), int(pos))
    return result


# ----------------------------------------------------------------------------
# Creating an award
# ----------------------------------------------------------------------------
async def create_award(
    db: AsyncSession,
    *,
    actor: User,
    club: Organization,
    membership: ClubMembership,
    member: User,
    category: str,
    points: int,
    note: str | None,
    occurred_on: date | None,
    request: Request | None,
) -> tuple[XpAward, int]:
    """Validate, apply the weekly cap and stage the award with its audit row.

    -> (award, positive points of that ISO week in this club, the new award included).
    The caller has already checked the permission (`rbac.can_award_xp`) and commits.
    """
    from app.services.audit import record_audit

    today = utcnow().date()
    if points == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "xp_points_zero")
    occurred_on = occurred_on or today
    if occurred_on > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "occurred_on_in_future")
    if occurred_on < today - timedelta(days=AWARD_MAX_DAYS_BACK):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "occurred_on_too_old")
    note = (note or "").strip() or None

    # Serializes the awards of this member: two directors pressing «+» at once cannot
    # both slip under the cap.
    await db.execute(
        select(ClubMembership.id).where(ClubMembership.id == membership.id).with_for_update()
    )
    monday = occurred_on - timedelta(days=occurred_on.weekday())
    _, positive = (await week_points(db, club.id, [member.id], monday))[member.id]
    if points > 0 and positive + points > WEEKLY_CAP:
        raise HTTPException(status.HTTP_409_CONFLICT, "xp_weekly_cap")

    award = XpAward(
        id=uuid.uuid4(),
        user_id=member.id,
        club_id=club.id,
        awarded_by_id=actor.id,
        category=category,
        points=points,
        note=note,
        occurred_on=occurred_on,
        created_at=utcnow(),
    )
    db.add(award)
    record_audit(
        db,
        action="CREATE",
        entity_type=XP_AWARD,
        entity_id=award.id,
        actor=actor,
        metadata={
            "user_id": str(member.id),
            "club_id": str(club.id),
            "membership_id": str(membership.id),
            "category": category,
            "points": points,
            "occurred_on": occurred_on.isoformat(),
        },
        request=request,
    )
    await db.flush()
    invalidate(member.id)
    return award, positive + max(points, 0)
