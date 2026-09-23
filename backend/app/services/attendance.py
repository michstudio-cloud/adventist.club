"""«Pasar lista» por reunión (Bloque H §2).

A meeting (`club_meetings`) has a list (`club_attendance`: PRESENT | ABSENT | JUSTIFIED per
membership). The list is what the club reads; what earns XP and counts for an `HOURS`
requirement is still `activity_logs`, so every PRESENT entry keeps exactly one
`ATTENDANCE` `APPROVED` log (quantity 1, `performed_on = held_on`, `meeting_id`) and every
other status keeps none. Writing the list again with the same content changes nothing.

Who records: the director and the secretary for the whole club, a counselor for their own
unit (`rbac.can_record_attendance`). Nobody records themselves — the log is an approval,
and nobody approves their own attendance (`rbac.can_approve_activity`).
"""
import uuid
from datetime import date, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivityLog, ClubAttendance, ClubMeeting, ClubMembership, Organization, User
from app.people import is_minor_user
from app.rbac import can_manage_members, can_record_attendance, may_handle_minors
from app.schemas.secretaria import (
    AttendanceSummary,
    AttendanceSummaryMember,
    AttendanceSummaryUnit,
    MeetingAttendee,
    MeetingCounts,
    MeetingDetail,
    MeetingOut,
)
from app.security import COUNSELOR, utcnow
from app.services import units as unit_service
from app.services import xp
from app.services.audit import record_audit

MEETING = "CLUB_MEETING"
PRESENT, ABSENT, JUSTIFIED = "PRESENT", "ABSENT", "JUSTIFIED"
ATTENDANCE, APPROVED = "ATTENDANCE", "APPROVED"
ROSTER_WINDOW_DAYS = 90
SUMMARY_DEFAULT_DAYS = 90

FORBIDDEN = "No tienes permiso para pasar lista en este club"
MEETING_NOT_FOUND = "Reunión no encontrada en este club"
DUPLICATE_MEETING = "Ya existe una reunión con esa fecha, tipo y título"
FUTURE_MEETING = "No se pasa lista de una reunión futura"
OUTSIDE_UNIT = "Sólo puedes pasar lista de los miembros de tu unidad"
OWN_ENTRY = "Nadie pasa lista de sí mismo: otra persona del club marca tu asistencia"
NOT_ACTIVE = "Sólo se pasa lista de miembros activos del club"
MINOR_WITHOUT_LETTER = "Sin carta de iglesia vigente no se pasa lista de menores"
MEMBERSHIP_NOT_FOUND = "Membresía no encontrada en este club"
KIND_LABELS = {"REUNION": "Reunión", "CAMPAMENTO": "Campamento", "SERVICIO": "Servicio", "OTRO": "Actividad"}


def _today() -> date:
    return utcnow().date()


def pct(present: int, meetings: int) -> float | None:
    """Present over the meetings the person was on the list of (a JUSTIFIED absence counts
    as a meeting). None without any meeting."""
    return round(present * 100.0 / meetings, 1) if meetings else None


# ----------------------------------------------------------------------------
# Who may record, and for whom
# ----------------------------------------------------------------------------
async def recording_scope(
    db: AsyncSession, actor: User, club: Organization
) -> set[uuid.UUID] | None:
    """None = the whole club; a set = only the members of those units. 403 otherwise."""
    if await can_record_attendance(db, actor, club):
        return None
    led = await unit_service.counselor_unit_ids(db, club.id, actor.id)
    units = {unit_id for unit_id in led if await can_record_attendance(db, actor, club, unit_id)}
    if not units:
        raise HTTPException(status.HTTP_403_FORBIDDEN, FORBIDDEN)
    return units


async def reading_scope(
    db: AsyncSession, actor: User, club: Organization
) -> tuple[list[uuid.UUID] | None, bool]:
    """The roster's reading rules (`GET /clubs/{id}/members`): a counselor reads their units,
    and staff who may not handle minors (E7) do not see minors. -> (units or None, hide_minors)."""
    manages = await can_manage_members(db, actor, club)
    units = None
    if not manages and actor.role == COUNSELOR:
        units = await unit_service.counselor_unit_ids(db, club.id, actor.id)
    return units, (not manages and not may_handle_minors(actor))


# ----------------------------------------------------------------------------
# Meetings
# ----------------------------------------------------------------------------
async def get_meeting(db: AsyncSession, club: Organization, meeting_id: uuid.UUID) -> ClubMeeting:
    stmt = select(ClubMeeting).where(ClubMeeting.id == meeting_id, ClubMeeting.club_id == club.id)
    meeting = (await db.execute(stmt)).scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, MEETING_NOT_FOUND)
    return meeting


async def counts_by_meeting(
    db: AsyncSession, meeting_ids: list[uuid.UUID]
) -> dict[uuid.UUID, MeetingCounts]:
    out = {meeting_id: MeetingCounts() for meeting_id in meeting_ids}
    if not meeting_ids:
        return out
    stmt = (
        select(ClubAttendance.meeting_id, ClubAttendance.status, func.count())
        .where(ClubAttendance.meeting_id.in_(meeting_ids))
        .group_by(ClubAttendance.meeting_id, ClubAttendance.status)
    )
    for meeting_id, entry_status, count in (await db.execute(stmt)).all():
        setattr(out[meeting_id], entry_status.lower(), count)
    return out


def as_out(meeting: ClubMeeting, counts: MeetingCounts) -> MeetingOut:
    return MeetingOut(
        id=str(meeting.id),
        club_id=str(meeting.club_id),
        held_on=meeting.held_on,
        kind=meeting.kind,
        title=meeting.title,
        notes=meeting.notes,
        created_at=meeting.created_at,
        counts=counts,
    )


async def create_meeting(
    db: AsyncSession,
    *,
    club: Organization,
    actor: User,
    held_on: date,
    kind: str,
    title: str | None,
    notes: str | None,
    request: Request | None = None,
) -> ClubMeeting:
    await recording_scope(db, actor, club)
    duplicate = select(ClubMeeting.id).where(
        ClubMeeting.club_id == club.id,
        ClubMeeting.held_on == held_on,
        ClubMeeting.kind == kind,
        func.lower(func.coalesce(ClubMeeting.title, "")) == (title or "").lower(),
    )
    if (await db.execute(duplicate.limit(1))).scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE_MEETING)
    meeting = ClubMeeting(
        id=uuid.uuid4(),
        club_id=club.id,
        held_on=held_on,
        kind=kind,
        title=title,
        notes=notes,
        created_by_id=actor.id,
        created_at=utcnow(),
    )
    db.add(meeting)
    record_audit(
        db,
        action="MEETING_CREATE",
        entity_type=MEETING,
        entity_id=meeting.id,
        actor=actor,
        details=f"{kind} {held_on.isoformat()} of club {club.id}",
        metadata={"club_id": str(club.id), "held_on": held_on.isoformat(), "kind": kind,
                  "title": title},
        request=request,
    )
    try:
        await db.flush()
    except IntegrityError:
        # Two people creating the same meeting at once: the unique index answers.
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE_MEETING)
    return meeting


async def list_meetings(
    db: AsyncSession, club: Organization, starts_on: date | None, ends_on: date | None
) -> list[MeetingOut]:
    stmt = select(ClubMeeting).where(ClubMeeting.club_id == club.id)
    if starts_on is not None:
        stmt = stmt.where(ClubMeeting.held_on >= starts_on)
    if ends_on is not None:
        stmt = stmt.where(ClubMeeting.held_on <= ends_on)
    meetings = (
        await db.execute(stmt.order_by(ClubMeeting.held_on.desc(), ClubMeeting.created_at.desc()))
    ).scalars().all()
    counts = await counts_by_meeting(db, [meeting.id for meeting in meetings])
    return [as_out(meeting, counts[meeting.id]) for meeting in meetings]


async def _active_members(
    db: AsyncSession, club: Organization, units: list[uuid.UUID] | None, hide_minors: bool
) -> list[tuple[ClubMembership, User]]:
    stmt = (
        select(ClubMembership, User)
        .join(User, User.id == ClubMembership.user_id)
        .where(ClubMembership.club_id == club.id, ClubMembership.status == "ACTIVE")
    )
    if units is not None:
        if not units:
            return []
        stmt = stmt.where(ClubMembership.unit_id.in_(units))
    rows = (await db.execute(stmt.order_by(User.name, User.id))).all()
    return [(m, u) for m, u in rows if not (hide_minors and is_minor_user(u))]


async def meeting_detail(
    db: AsyncSession, actor: User, club: Organization, meeting: ClubMeeting
) -> MeetingDetail:
    """Every ACTIVE member the caller may read, with their status on this meeting's list."""
    units, hide_minors = await reading_scope(db, actor, club)
    members = await _active_members(db, club, units, hide_minors)
    statuses = dict(
        (
            await db.execute(
                select(ClubAttendance.membership_id, ClubAttendance.status).where(
                    ClubAttendance.meeting_id == meeting.id
                )
            )
        ).all()
    )
    unit_rows = await unit_service.units_by_id(db, club.id)
    counts = (await counts_by_meeting(db, [meeting.id]))[meeting.id]
    return MeetingDetail(
        **as_out(meeting, counts).model_dump(),
        attendees=[
            MeetingAttendee(
                membership_id=str(membership.id),
                user_id=str(member.id),
                name=member.name,
                unit=unit_service.unit_ref(unit_rows.get(membership.unit_id)),
                status=statuses.get(membership.id),
            )
            for membership, member in members
        ],
    )


# ----------------------------------------------------------------------------
# «Pasar lista»
# ----------------------------------------------------------------------------
async def record(
    db: AsyncSession,
    *,
    club: Organization,
    meeting: ClubMeeting,
    actor: User,
    entries: dict[uuid.UUID, str],
    request: Request | None = None,
) -> None:
    """REPLACE the list of `meeting` within what `actor` may record (the whole club, or
    their units; never their own entry) and keep `activity_logs` in step. Idempotent."""
    scope = await recording_scope(db, actor, club)
    if meeting.held_on > _today():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, FUTURE_MEETING)

    # Serializes two people passing the list of the same meeting at once.
    await db.execute(select(ClubMeeting.id).where(ClubMeeting.id == meeting.id).with_for_update())

    requested = (
        await db.execute(
            select(ClubMembership, User)
            .join(User, User.id == ClubMembership.user_id)
            .where(ClubMembership.id.in_(list(entries)), ClubMembership.club_id == club.id)
        )
    ).all() if entries else []
    if len(requested) != len(entries):
        raise HTTPException(status.HTTP_404_NOT_FOUND, MEMBERSHIP_NOT_FOUND)
    for membership, member in requested:
        if member.id == actor.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, OWN_ENTRY)
        if membership.status != "ACTIVE":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, NOT_ACTIVE)
        if scope is not None and membership.unit_id not in scope:
            raise HTTPException(status.HTTP_403_FORBIDDEN, OUTSIDE_UNIT)
        if is_minor_user(member) and not may_handle_minors(actor):
            raise HTTPException(status.HTTP_403_FORBIDDEN, MINOR_WITHOUT_LETTER)

    # What is on the list today, with who it is about.
    existing = {
        membership.id: (entry, membership)
        for entry, membership in (
            await db.execute(
                select(ClubAttendance, ClubMembership)
                .join(ClubMembership, ClubMembership.id == ClubAttendance.membership_id)
                .where(ClubAttendance.meeting_id == meeting.id)
            )
        ).all()
    }
    # Within the caller's reach and not in the payload: off the list.
    removed = [
        (entry, membership)
        for membership_id, (entry, membership) in existing.items()
        if membership_id not in entries
        and membership.user_id != actor.id
        and (scope is None or membership.unit_id in scope)
    ]

    now = utcnow()
    changes: list[tuple[ClubMembership, str | None, str | None]] = []
    for membership, _ in requested:
        new_status = entries[membership.id]
        previous = existing.get(membership.id)
        old_status = previous[0].status if previous else None
        if old_status == new_status:
            continue
        stmt = pg_insert(ClubAttendance).values(
            meeting_id=meeting.id, membership_id=membership.id, status=new_status,
            recorded_by_id=actor.id, recorded_at=now,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[ClubAttendance.meeting_id, ClubAttendance.membership_id],
                set_={"status": new_status, "recorded_by_id": actor.id, "recorded_at": now},
            )
        )
        changes.append((membership, old_status, new_status))
    for entry, membership in removed:
        await db.execute(
            delete(ClubAttendance).where(
                ClubAttendance.meeting_id == meeting.id,
                ClubAttendance.membership_id == membership.id,
            )
        )
        changes.append((membership, entry.status, None))

    if not changes:
        return
    for membership, old_status, new_status in changes:
        await _sync_log(db, club, meeting, membership.user_id, new_status == PRESENT, actor, now)
        # One audit row per member: the trail of each minor has to be complete on its own.
        record_audit(
            db,
            action="ATTENDANCE_RECORD",
            entity_type=MEETING,
            entity_id=meeting.id,
            actor=actor,
            metadata={"club_id": str(club.id), "membership_id": str(membership.id),
                      "user_id": str(membership.user_id), "from": old_status, "to": new_status,
                      "held_on": meeting.held_on.isoformat()},
            request=request,
        )
    await db.flush()
    from app.services import portfolio_links

    for user_id in dict.fromkeys(membership.user_id for membership, _, _ in changes):
        # The sum of attendance moved: a `HOURS` requirement may complete, or come back,
        # and the XP cached for this person is stale.
        await portfolio_links.sync(db, actor, user_id=user_id, request=request)
        xp.invalidate(user_id)


async def _sync_log(
    db: AsyncSession,
    club: Organization,
    meeting: ClubMeeting,
    user_id: uuid.UUID,
    present: bool,
    actor: User,
    now,
) -> None:
    """Exactly one APPROVED ATTENDANCE log while present; none otherwise."""
    stmt = select(ActivityLog).where(
        ActivityLog.meeting_id == meeting.id, ActivityLog.user_id == user_id
    )
    log = (await db.execute(stmt)).scalar_one_or_none()
    if not present:
        if log is not None:
            await db.delete(log)
        return
    if log is not None:
        if log.status != APPROVED:
            log.status, log.decided_by_id, log.decided_at, log.updated_at = APPROVED, actor.id, now, now
        return
    db.add(
        ActivityLog(
            id=uuid.uuid4(),
            user_id=user_id,
            club_id=club.id,
            category=ATTENDANCE,
            performed_on=meeting.held_on,
            quantity=1,
            description=f"Asistencia: {meeting.title or KIND_LABELS.get(meeting.kind, meeting.kind)}",
            place=None,
            status=APPROVED,
            created_by_id=actor.id,
            decided_by_id=actor.id,
            decided_at=now,
            created_at=now,
            updated_at=now,
            meeting_id=meeting.id,
        )
    )


# ----------------------------------------------------------------------------
# Percentages: the roster (90 days) and the summary
# ----------------------------------------------------------------------------
async def tallies(
    db: AsyncSession,
    club_id: uuid.UUID,
    starts_on: date,
    ends_on: date,
    membership_ids: list[uuid.UUID] | None = None,
) -> dict[uuid.UUID, tuple[int, int]]:
    """-> {membership_id: (meetings on the list, present)} in ONE query."""
    present = func.count().filter(ClubAttendance.status == PRESENT)
    stmt = (
        select(ClubAttendance.membership_id, func.count(), present)
        .join(ClubMeeting, ClubMeeting.id == ClubAttendance.meeting_id)
        .where(
            ClubMeeting.club_id == club_id,
            ClubMeeting.held_on >= starts_on,
            ClubMeeting.held_on <= ends_on,
        )
        .group_by(ClubAttendance.membership_id)
    )
    if membership_ids is not None:
        if not membership_ids:
            return {}
        stmt = stmt.where(ClubAttendance.membership_id.in_(membership_ids))
    return {row[0]: (int(row[1]), int(row[2])) for row in (await db.execute(stmt)).all()}


async def pct_90d(
    db: AsyncSession, club_id: uuid.UUID, membership_ids: list[uuid.UUID]
) -> dict[uuid.UUID, float | None]:
    today = _today()
    counted = await tallies(
        db, club_id, today - timedelta(days=ROSTER_WINDOW_DAYS), today, membership_ids
    )
    out = {}
    for membership_id in membership_ids:
        meetings, present = counted.get(membership_id, (0, 0))
        out[membership_id] = pct(present, meetings)
    return out


async def summary(
    db: AsyncSession,
    actor: User,
    club: Organization,
    starts_on: date | None,
    ends_on: date | None,
) -> AttendanceSummary:
    ends_on = ends_on or _today()
    starts_on = starts_on or ends_on - timedelta(days=SUMMARY_DEFAULT_DAYS)
    if starts_on > ends_on:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_range")
    units, hide_minors = await reading_scope(db, actor, club)
    members = await _active_members(db, club, units, hide_minors)
    counted = await tallies(db, club.id, starts_on, ends_on, [m.id for m, _ in members])
    meetings = (
        await db.execute(
            select(func.count()).where(
                ClubMeeting.club_id == club.id,
                ClubMeeting.held_on >= starts_on,
                ClubMeeting.held_on <= ends_on,
            )
        )
    ).scalar_one()
    unit_rows = await unit_service.units_by_id(db, club.id)

    member_rows, by_unit = [], {}
    for membership, member in members:
        total, present = counted.get(membership.id, (0, 0))
        member_rows.append(
            AttendanceSummaryMember(
                membership_id=str(membership.id),
                user_id=str(member.id),
                name=member.name,
                unit_id=str(membership.unit_id) if membership.unit_id else None,
                meetings=total,
                present=present,
                pct=pct(present, total),
            )
        )
        if membership.unit_id in unit_rows:
            seen, here = by_unit.get(membership.unit_id, (0, 0))
            by_unit[membership.unit_id] = (seen + total, here + present)
    return AttendanceSummary(
        starts_on=starts_on,
        ends_on=ends_on,
        meetings=meetings,
        members=member_rows,
        units=sorted(
            (
                AttendanceSummaryUnit(
                    unit_id=str(unit_id), name=unit_rows[unit_id].name, meetings=total,
                    present=present, pct=pct(present, total),
                )
                for unit_id, (total, present) in by_unit.items()
            ),
            key=lambda row: row.name,
        ),
    )

