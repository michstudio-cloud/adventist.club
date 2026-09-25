"""Secretaría del club (Bloque H): cargos, «pasar lista», página pública y puntuación.

Spec: docs/superpowers/specs/2026-09-23-secretaria-club.md. Thin: the permission in
`app/rbac.py`, the work in `app/services/{officers,attendance,club_score}.py`. The roster's
new columns, its CSV and the renewal of an invitation live with the rest of the roster in
`app/routers/clubs.py`.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import ClubMembership, ClubOfficer, Guardianship, Organization, User
from app.rate_limit import limiter
from app.rbac import (
    can_manage_members,
    can_manage_officers,
    can_view_club_score,
    can_view_roster,
    profile_is_minor,
)
from app.schemas.secretaria import (
    AttendanceSummary,
    AttendanceUpdate,
    ClubPublicProfile,
    ClubScore,
    MeetingCounts,
    MeetingCreate,
    MeetingDetail,
    MeetingOut,
    OfficerCreate,
    OfficerOut,
    OfficerUpdate,
    PublicOfficer,
    ServiceHoursSummary,
)
from app.security import utcnow
from app.services import attendance as attendance_service
from app.services import club_score
from app.services import memberships as membership_service
from app.services import ministries as ministry_service
from app.services import officers as officer_service
from app.services import placement
from app.services import service_hours

router = APIRouter(prefix="/api/v1/clubs", tags=["secretaria"])

NOT_ROSTER_DETAIL = "No tienes permiso para ver la nómina de este club"
OFFICERS_FORBIDDEN = "No tienes permiso para gestionar los cargos de este club"
DIRECTION_ONLY = "La secretaría no nombra los cargos de dirección ni subdirección"
SCORE_FORBIDDEN = "No tienes permiso para ver la puntuación de este club"
CLUB_NOT_FOUND = "Club no encontrado"


async def _club_for_reader(db: AsyncSession, actor: User, club_id: uuid.UUID) -> Organization:
    club = await membership_service.get_club(db, club_id)
    if not await can_view_roster(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROSTER_DETAIL)
    return club


async def _officer_gate(db: AsyncSession, actor: User, club: Organization, title: str) -> None:
    if not await can_manage_members(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, OFFICERS_FORBIDDEN)
    if not await can_manage_officers(db, actor, club, title):
        raise HTTPException(status.HTTP_403_FORBIDDEN, DIRECTION_ONLY)


# ----------------------------------------------------------------------------
# 1. Cargos
# ----------------------------------------------------------------------------
@router.get("/{club_id}/officers", response_model=list[OfficerOut])
async def list_officers(
    club_id: uuid.UUID,
    include_closed: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The cargos in force (of active members); `include_closed=true` adds the history."""
    club = await _club_for_reader(db, current_user, club_id)
    rows = await officer_service.list_officers(db, club.id, include_closed=include_closed)
    return [officer_service.as_out(officer, member) for officer, member in rows]


@router.post("/{club_id}/officers", response_model=OfficerOut, status_code=status.HTTP_201_CREATED)
async def create_officer(
    club_id: uuid.UUID,
    payload: OfficerCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await membership_service.get_club(db, club_id)
    await _officer_gate(db, current_user, club, payload.title)
    membership = await membership_service.get_membership_of_club(db, club, payload.membership_id)
    officer, member = await officer_service.create(
        db,
        club=club,
        membership=membership,
        actor=current_user,
        title=payload.title,
        custom_title=payload.custom_title,
        since=payload.since,
        request=request,
    )
    await db.commit()
    return officer_service.as_out(officer, member)


async def _officer_and_member(
    db: AsyncSession, club: Organization, officer_id: uuid.UUID
) -> tuple[ClubOfficer, User]:
    officer = await officer_service.get_officer(db, club, officer_id)
    membership = await db.get(ClubMembership, officer.membership_id)
    member = await db.get(User, membership.user_id) if membership else None
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, officer_service.NOT_FOUND)
    return officer, member


@router.patch("/{club_id}/officers/{officer_id}", response_model=OfficerOut)
async def update_officer(
    club_id: uuid.UUID,
    officer_id: uuid.UUID,
    payload: OfficerUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """`until` and `custom_title` (only for OTRO). A different cargo is a new one."""
    club = await membership_service.get_club(db, club_id)
    if not await can_manage_members(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, OFFICERS_FORBIDDEN)
    officer, member = await _officer_and_member(db, club, officer_id)
    await _officer_gate(db, current_user, club, officer.title)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que actualizar")
    await officer_service.update(db, officer, actor=current_user, changes=changes, request=request)
    await db.commit()
    return officer_service.as_out(officer, member)


@router.delete("/{club_id}/officers/{officer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_officer(
    club_id: uuid.UUID,
    officer_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Closes the cargo with `until = today`. Never a delete; idempotent."""
    club = await membership_service.get_club(db, club_id)
    if not await can_manage_members(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, OFFICERS_FORBIDDEN)
    officer = await officer_service.get_officer(db, club, officer_id)
    await _officer_gate(db, current_user, club, officer.title)
    await officer_service.close(db, officer, actor=current_user, request=request)
    await db.commit()


# ----------------------------------------------------------------------------
# 2. Reuniones y asistencia
# ----------------------------------------------------------------------------
@router.post("/{club_id}/meetings", response_model=MeetingOut, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    club_id: uuid.UUID,
    payload: MeetingCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await membership_service.get_club(db, club_id)
    meeting = await attendance_service.create_meeting(
        db,
        club=club,
        actor=current_user,
        held_on=payload.held_on,
        kind=payload.kind,
        title=payload.title,
        notes=payload.notes,
        request=request,
    )
    await db.commit()
    return attendance_service.as_out(meeting, MeetingCounts())


@router.get("/{club_id}/meetings", response_model=list[MeetingOut])
async def list_meetings(
    club_id: uuid.UUID,
    starts_on: date | None = Query(None, alias="from"),
    ends_on: date | None = Query(None, alias="to"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Newest first, with the count of each status on the list."""
    club = await _club_for_reader(db, current_user, club_id)
    return await attendance_service.list_meetings(db, club, starts_on, ends_on)


@router.get("/{club_id}/meetings/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(
    club_id: uuid.UUID,
    meeting_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every active member (as the roster shows them to the caller) and their status."""
    club = await _club_for_reader(db, current_user, club_id)
    meeting = await attendance_service.get_meeting(db, club, meeting_id)
    return await attendance_service.meeting_detail(db, current_user, club, meeting)


@router.put("/{club_id}/meetings/{meeting_id}/attendance", response_model=MeetingDetail)
async def record_attendance(
    club_id: uuid.UUID,
    meeting_id: uuid.UUID,
    payload: AttendanceUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replaces the list (within the caller's reach) and keeps the attendance logs — and so
    the XP — in step. Sending the same list twice changes nothing."""
    club = await membership_service.get_club(db, club_id)
    await attendance_service.recording_scope(db, current_user, club)
    meeting = await attendance_service.get_meeting(db, club, meeting_id)
    await attendance_service.record(
        db,
        club=club,
        meeting=meeting,
        actor=current_user,
        entries={entry.membership_id: entry.status for entry in payload.entries},
        request=request,
    )
    await db.commit()
    return await attendance_service.meeting_detail(db, current_user, club, meeting)


@router.get("/{club_id}/attendance/summary", response_model=AttendanceSummary)
async def attendance_summary(
    club_id: uuid.UUID,
    starts_on: date | None = Query(None, alias="from"),
    ends_on: date | None = Query(None, alias="to"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per member: meetings on their list, present and %; per unit: the same. Default: the
    last 90 days."""
    club = await _club_for_reader(db, current_user, club_id)
    return await attendance_service.summary(db, current_user, club, starts_on, ends_on)


@router.get("/{club_id}/service-hours", response_model=ServiceHoursSummary)
async def service_hours_summary(
    club_id: uuid.UUID,
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """«Horas» of the club panel: approved service of the month (default: this one) and of the
    year so far, per member and per unit. Totals only; the same readers as the roster."""
    club = await _club_for_reader(db, current_user, club_id)
    return await service_hours.summary(db, current_user, club, month)


# ----------------------------------------------------------------------------
# 5. Página pública del club
# ----------------------------------------------------------------------------
_HAS_GUARDIAN = exists().where(Guardianship.child_id == User.id).correlate(User)


@router.get("/{club_id}/profile", response_model=ClubPublicProfile)
@limiter.limit("60/minute")
async def public_profile(
    request: Request,
    club_id: uuid.UUID,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Signed-in only (owner, 2026-09-24: the club directory is not public), for an ACTIVE club. Never an e-mail, and never the name of a minor: a
    minor who holds a cargo is simply not listed here (the club still sees them)."""
    club = await db.get(Organization, club_id)
    if club is None or club.type != "club" or club.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, CLUB_NOT_FOUND)
    profile = (club.metadata_json or {}).get("profile") or {}
    ancestors = (await placement.refs_for(db, [club])).get(club.id, {})

    rows = (
        await db.execute(
            select(ClubOfficer, User, _HAS_GUARDIAN.label("has_guardian"))
            .join(ClubMembership, ClubMembership.id == ClubOfficer.membership_id)
            .join(User, User.id == ClubMembership.user_id)
            .where(
                ClubOfficer.club_id == club.id,
                officer_service.active_condition(),
                ClubMembership.status == "ACTIVE",
                User.status == "ACTIVE",
            )
        )
    ).all()
    officers = sorted(
        (
            (officer, member)
            for officer, member, has_guardian in rows
            if not profile_is_minor(member, has_guardian)
        ),
        key=lambda row: (officer_service.TITLE_ORDER.index(row[0].title), row[1].name),
    )
    active_members = (
        await db.execute(
            select(func.count()).where(
                ClubMembership.club_id == club.id, ClubMembership.status == "ACTIVE"
            )
        )
    ).scalar_one()
    ministries = await ministry_service.list_of(db, club)
    return ClubPublicProfile(
        id=str(club.id),
        name=club.name,
        church=placement.church_name_of(club, ancestors),
        zone=placement.as_ref(ancestors.get(placement.ZONE)),
        association=placement.as_ref(ancestors.get(placement.ASSOCIATION)),
        city=club.city,
        state=club.state,
        country=club.country,
        meeting_day=profile.get("meeting_day"),
        meeting_time=profile.get("meeting_time"),
        contact=profile.get("contact"),
        description=profile.get("description"),
        logo_url=club.logo_url,
        officers=[
            PublicOfficer(title=officer.title, custom_title=officer.custom_title, name=member.name)
            for officer, member in officers
        ],
        active_members=active_members,
        accepts_requests=membership_service.accepts_requests(club),
        ministry=ministries[0] if ministries else None,
        ministries=ministries,
        address=club.address,
        maps_url=club.maps_url,
        latitude=club.latitude,
        longitude=club.longitude,
    )


# ----------------------------------------------------------------------------
# 6. Puntuación
# ----------------------------------------------------------------------------
@router.get("/{club_id}/score", response_model=ClubScore)
async def get_score(
    club_id: uuid.UUID,
    season: int | None = Query(None, ge=2000, le=2100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The club's points in a season (calendar year; default: this one), by unit and month.
    For the club — staff and active members — and the hierarchy above it."""
    club = await membership_service.get_club(db, club_id)
    if not await can_view_club_score(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, SCORE_FORBIDDEN)
    return await club_score.score(db, club, season or utcnow().date().year)
