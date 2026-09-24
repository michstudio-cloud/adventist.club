"""«Horas» del panel del club: el mes de servicio por miembro y por unidad.

What the director (and the secretary, and a counselor for their units) reads to follow the
club's service: APPROVED `SERVICE` hours of the month and of the year so far, the month's
attendance, and how many of each member's logs still wait for a decision. Totals only —
the description and the place of a log say where a minor was, and stay in the member's
portfolio (`can_view_portfolio`).

Only the logs recorded FOR this club count (`activity_logs.club_id`): what a member did in
a previous club is that club's business. Reading follows the roster
(`attendance.reading_scope`): a counselor reads their units, staff without a church letter
in force do not see minors.
"""
import calendar
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, Organization, User
from app.schemas.secretaria import ServiceHoursMember, ServiceHoursSummary, ServiceHoursUnit
from app.security import utcnow
from app.services import attendance
from app.services import units as unit_service

SERVICE, ATTENDANCE = "SERVICE", "ATTENDANCE"
APPROVED, SUBMITTED = "APPROVED", "SUBMITTED"


def month_bounds(month: str | None) -> tuple[str, date, date]:
    """`YYYY-MM` (default: this month) -> (label, first day, last day)."""
    if month is None:
        today = utcnow().date()
        year, number = today.year, today.month
    else:
        try:
            year, number = (int(part) for part in month.split("-"))
            date(year, number, 1)
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_month")
    last = calendar.monthrange(year, number)[1]
    return f"{year:04d}-{number:02d}", date(year, number, 1), date(year, number, last)


async def summary(
    db: AsyncSession, actor: User, club: Organization, month: str | None
) -> ServiceHoursSummary:
    label, starts_on, ends_on = month_bounds(month)
    units, hide_minors = await attendance.reading_scope(db, actor, club)
    members = await attendance._active_members(db, club, units, hide_minors)
    user_ids = [user.id for _, user in members]

    in_month = ActivityLog.performed_on.between(starts_on, ends_on)
    in_year = ActivityLog.performed_on.between(date(starts_on.year, 1, 1), ends_on)
    approved = ActivityLog.status == APPROVED
    service = ActivityLog.category == SERVICE
    totals: dict = {}
    if user_ids:
        stmt = (
            select(
                ActivityLog.user_id,
                func.coalesce(func.sum(ActivityLog.quantity).filter(approved, service, in_month), 0),
                func.coalesce(func.sum(ActivityLog.quantity).filter(approved, service, in_year), 0),
                func.coalesce(
                    func.sum(ActivityLog.quantity).filter(
                        approved, ActivityLog.category == ATTENDANCE, in_month
                    ),
                    0,
                ),
                func.count().filter(ActivityLog.status == SUBMITTED, service),
            )
            .where(ActivityLog.club_id == club.id, ActivityLog.user_id.in_(user_ids))
            .group_by(ActivityLog.user_id)
        )
        totals = {row[0]: row[1:] for row in (await db.execute(stmt)).all()}

    unit_rows = await unit_service.units_by_id(db, club.id)
    rows, by_unit = [], {}
    for membership, member in members:
        month_total, year_total, attended, pending = totals.get(member.id, (0, 0, 0, 0))
        rows.append(
            ServiceHoursMember(
                membership_id=str(membership.id),
                user_id=str(member.id),
                name=member.name,
                unit_id=str(membership.unit_id) if membership.unit_id else None,
                service_month=float(month_total),
                service_year=float(year_total),
                attendance_month=float(attended),
                pending=int(pending),
            )
        )
        if membership.unit_id in unit_rows:
            count, hours = by_unit.get(membership.unit_id, (0, 0.0))
            by_unit[membership.unit_id] = (count + 1, hours + float(month_total))
    return ServiceHoursSummary(
        month=label,
        starts_on=starts_on,
        ends_on=ends_on,
        service_month=round(sum(row.service_month for row in rows), 1),
        members=rows,
        units=sorted(
            (
                ServiceHoursUnit(
                    unit_id=str(unit_id), name=unit_rows[unit_id].name, members=count,
                    service_month=round(hours, 1),
                )
                for unit_id, (count, hours) in by_unit.items()
            ),
            key=lambda row: row.name,
        ),
    )
