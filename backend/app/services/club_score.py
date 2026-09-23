"""Puntuación del club (Bloque H §6) — gamification of a whole club over one season.

    Source                                                              Points
    positive xp_awards given IN this club (penalties never subtract)   their sum
    approved ATTENDANCE activity logs of this club                     5 each (XP_ATTENDANCE)
    honors certified from an enrollment of this club (insignias)       100 (+50 / +100 by skill)

A season is a calendar year. Each fact belongs to the club where it happened (`club_id` of
the award, of the log and of the enrollment), to the month it happened in, and to the unit
the person is in TODAY (their ACTIVE membership of this club; `null` otherwise). Three
aggregate queries, whatever the size of the club. Comparing clubs is the association's
business (a later block).
"""
import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization
from app.schemas.secretaria import ClubScore, ScoreBySource, ScoreMonth, ScoreUnit
from app.services import units as unit_service
from app.services import xp

_AWARDS_SQL = text(
    """
    SELECT a.user_id, date_trunc('month', a.occurred_on)::date AS month, sum(a.points) AS points
    FROM xp_awards a
    WHERE a.club_id = :club AND a.points > 0 AND a.occurred_on BETWEEN :starts AND :ends
    GROUP BY a.user_id, month
    """
)

_ATTENDANCE_SQL = text(
    """
    SELECT l.user_id, date_trunc('month', l.performed_on)::date AS month, sum(l.quantity) AS quantity
    FROM activity_logs l
    WHERE l.club_id = :club AND l.category = 'ATTENDANCE' AND l.status = 'APPROVED'
      AND l.performed_on BETWEEN :starts AND :ends
    GROUP BY l.user_id, month
    """
)

_BADGES_SQL = text(
    """
    SELECT c.user_id, date_trunc('month', c.issued_date)::date AS month, h.skill_level
    FROM certificates c
    JOIN honor_enrollments e ON e.id = c.enrollment_id
    JOIN honors h ON h.id = c.honor_id
    WHERE e.club_id = :club AND c.honor_id IS NOT NULL
      AND c.revoked_at IS NULL AND c.status <> :revoked
      AND c.issued_date BETWEEN :starts AND :ends
    """
)

_UNITS_SQL = text(
    """
    SELECT user_id, unit_id FROM club_memberships
    WHERE club_id = :club AND status = 'ACTIVE'
    """
)


async def score(db: AsyncSession, club: Organization, season: int) -> ClubScore:
    params = {"club": club.id, "starts": date(season, 1, 1), "ends": date(season, 12, 31)}
    # (user_id, month) -> points, per source
    facts: list[tuple[str, uuid.UUID, date, int]] = []
    for row in (await db.execute(_AWARDS_SQL, params)).mappings():
        facts.append(("awards", row["user_id"], row["month"], int(row["points"])))
    for row in (await db.execute(_ATTENDANCE_SQL, params)).mappings():
        facts.append(
            ("attendance", row["user_id"], row["month"], int(float(row["quantity"]) * xp.XP_ATTENDANCE))
        )
    badge_params = {**params, "revoked": xp.REVOKED_STATUS}
    for row in (await db.execute(_BADGES_SQL, badge_params)).mappings():
        facts.append(("badges", row["user_id"], row["month"], xp.honor_xp(row["skill_level"])))

    unit_of = {
        row["user_id"]: row["unit_id"]
        for row in (await db.execute(_UNITS_SQL, {"club": club.id})).mappings()
    }
    units = await unit_service.units_by_id(db, club.id)

    by_source: dict[str, int] = defaultdict(int)
    by_unit: dict[uuid.UUID | None, int] = defaultdict(int)
    by_month: dict[str, int] = defaultdict(int)
    for source, user_id, month, points in facts:
        by_source[source] += points
        unit_id = unit_of.get(user_id)
        by_unit[unit_id if unit_id in units else None] += points
        by_month[f"{month.year:04d}-{month.month:02d}"] += points

    return ClubScore(
        club_id=str(club.id),
        season=season,
        total=sum(by_source.values()),
        by_source=ScoreBySource(
            awards=by_source["awards"], attendance=by_source["attendance"], badges=by_source["badges"]
        ),
        units=sorted(
            (
                ScoreUnit(
                    unit_id=str(unit_id) if unit_id else None,
                    name=units[unit_id].name if unit_id else None,
                    total=total,
                )
                for unit_id, total in by_unit.items()
            ),
            key=lambda row: (-row.total, row.name or ""),
        ),
        months=[ScoreMonth(month=month, total=by_month[month]) for month in sorted(by_month)],
    )
