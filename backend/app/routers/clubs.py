"""Managing a club: its roster and its control data (Bloque E).

Thin: check the permission in `app/rbac.py`, call `app/services/memberships.py`,
serialize. The roster is the one read of this API that shows minors, so its
serializer is deliberately narrow (spec §7): years of age, never a birth date,
never an e-mail, and `guardian_email` only for the director and the
administrators above them — not even for the club's own secretary.
"""
import csv
import io
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import ClubInvitation, ClubMembership, Guardianship, Organization, User
from app.people import age_in_years, is_minor_user
from app.rbac import (
    CONSENT_GRANTED,
    can_appoint_counselor,
    can_grant_club_role,
    can_manage_members,
    can_view_guardian_contact,
    can_view_roster,
    is_admin_role,
    is_master,
    may_handle_minors,
    profile_is_minor,
    visible_avatar,
)
from app.schemas.membership import (
    BulkApproval,
    ClubProfileOut,
    ClubProfileUpdate,
    ClubRole,
    ConsentSummary,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    ManagedMemberRow,
    MemberRemoval,
    MemberRoleUpdate,
    MemberRow,
    MembershipEnded,
    MembershipOut,
    MembershipStatus,
    RequestDecision,
    RequestRow,
    as_invitation_out,
    as_membership_out,
)
from app.schemas.secretaria import Completeness
from app.schemas.unit import (
    CounselorAssign,
    MemberUnitAssign,
    MemberUnitOut,
    UnitCreate,
    UnitOut,
    UnitUpdate,
)
from app.security import COUNSELOR, STUDENT, utcnow
from app.services import attendance as attendance_service
from app.services import email as email_service
from app.services import invitations as invitation_service
from app.services import memberships as membership_service
from app.services import ministries as ministry_service
from app.services import notifications
from app.services import officers as officer_service
from app.services import units as unit_service
from app.services.audit import record_audit

router = APIRouter(prefix="/api/v1/clubs", tags=["clubs"])

NOT_MANAGER_DETAIL = "No tienes permiso para gestionar los miembros de este club"
NOT_ROSTER_DETAIL = "No tienes permiso para ver la nómina de este club"


async def _club_for_manager(db: AsyncSession, actor: User, club_id: uuid.UUID) -> Organization:
    club = await membership_service.get_club(db, club_id)
    if not await can_manage_members(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_MANAGER_DETAIL)
    return club


# ----------------------------------------------------------------------------
# Roster
# ----------------------------------------------------------------------------
@router.get("/{club_id}/members", response_model=None)
async def list_members(
    club_id: uuid.UUID,
    status_filter: MembershipStatus | None = Query(None, alias="status"),
    role: ClubRole | None = None,
    unit_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MemberRow | ManagedMemberRow]:
    """`response_model` is left off on purpose: each row is serialized by its
    own class, so `guardian_email` is missing — not null — for the rest of the
    staff. Declaring one model would put the key back into every payload."""
    club = await membership_service.get_club(db, club_id)
    if not await can_view_roster(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROSTER_DETAIL)
    if (
        status_filter not in (None, membership_service.ACTIVE, membership_service.PENDING_APPROVAL)
        and not (is_master(current_user) or is_admin_role(current_user))
    ):
        # SEC-07: a minor waiting for consent is invisible to the club until a guardian says
        # yes, and one whose guardian withdrew it leaves the club "at once". The club's own
        # staff list who is in, or asking to be; the history stays with the hierarchy.
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROSTER_DETAIL)
    manages = await can_manage_members(db, current_user, club)
    sees_guardians = await can_view_guardian_contact(db, current_user, club)

    stmt = (
        # `has_guardian` rides along (the profile's minor rule for accounts without a birth
        # date), so the photo of every row costs no query of its own.
        select(ClubMembership, User, _HAS_GUARDIAN, _HAS_APPROVED_GUARDIAN)
        .join(User, User.id == ClubMembership.user_id)
        .where(
            ClubMembership.club_id == club.id,
            # The roster is the people who are IN the club unless asked otherwise.
            ClubMembership.status == (status_filter or membership_service.ACTIVE),
        )
    )
    if not manages and current_user.role == COUNSELOR:
        # A counselor sees the members of THEIR units and nobody else (D7).
        mine = await unit_service.counselor_unit_ids(db, club.id, current_user.id)
        if not mine:
            return []
        stmt = stmt.where(ClubMembership.unit_id.in_(mine))
    if role:
        stmt = stmt.where(ClubMembership.role == role)
    if unit_id:
        stmt = stmt.where(ClubMembership.unit_id == unit_id)
    rows = (await db.execute(stmt.order_by(User.name, User.id))).all()
    units = await unit_service.units_by_id(db, club.id)
    # E7: for an INSTRUCTOR or a COUNSELOR the rows of MINORS only come through
    # when they may handle minors. Whoever manages the club (director in grace,
    # secretary, administrators) reads the whole roster: that is how a club is
    # run and how an unverified instructor is noticed in the first place.
    hide_minors = not manages and not may_handle_minors(current_user)
    rows = [row for row in rows if not (hide_minors and is_minor_user(row[1]))]
    # Bloque H: cargos and attendance for the whole page in two queries, never one per row.
    extras = await _roster_extras(db, club.id, [row[0].id for row in rows])

    return [
        await _member_row(
            db,
            membership,
            member,
            include_guardian_email=sees_guardians,
            units=units,
            has_guardian=has_guardian,
            has_approved_guardian=has_approved_guardian,
            extras=extras,
        )
        for membership, member, has_guardian, has_approved_guardian in rows
    ]


_HAS_GUARDIAN = (
    exists().where(Guardianship.child_id == User.id).correlate(User).label("has_guardian")
)
# Bloque H: the «guardian» flag of the roster's completeness.
_HAS_APPROVED_GUARDIAN = (
    exists()
    .where(Guardianship.child_id == User.id, Guardianship.consent_status == CONSENT_GRANTED)
    .correlate(User)
    .label("has_approved_guardian")
)


async def _roster_extras(
    db: AsyncSession, club_id: uuid.UUID, membership_ids: list[uuid.UUID]
) -> tuple[dict, dict]:
    """(cargos by membership, attendance % of the last 90 days by membership)."""
    return (
        await officer_service.titles_by_membership(db, membership_ids),
        await attendance_service.pct_90d(db, club_id, membership_ids),
    )


def _completeness(
    membership: ClubMembership, member: User, *, minor: bool, has_approved_guardian: bool
) -> Completeness:
    """Flags, never the data. Consent and guardian only apply to a minor."""
    return Completeness(
        birth_date=member.birth_date is not None,
        consent=not minor or membership.consent_at is not None,
        guardian=not minor or has_approved_guardian,
        email_verified=member.verification_status == "VERIFIED",
    )


async def _member_row(
    db: AsyncSession,
    membership: ClubMembership,
    member: User,
    *,
    include_guardian_email: bool,
    units: dict | None = None,
    has_guardian: bool | None = None,
    has_approved_guardian: bool | None = None,
    extras: tuple[dict, dict] | None = None,
) -> MemberRow | ManagedMemberRow:
    minor = is_minor_user(member)
    if extras is None:
        # A single row (after a role change): the same data, for one membership.
        extras = await _roster_extras(db, membership.club_id, [membership.id])
    if has_approved_guardian is None:
        has_approved_guardian = bool(
            await db.scalar(
                select(
                    exists().where(
                        Guardianship.child_id == member.id,
                        Guardianship.consent_status == CONSENT_GRANTED,
                    )
                )
            )
        )
    titles, attendance = extras
    if has_guardian is None:
        # A single row (after a role change): ask only when it can change the answer.
        has_guardian = (
            not minor
            and member.birth_date is None
            and bool(await db.scalar(select(exists().where(Guardianship.child_id == member.id))))
        )
    consent = None
    if minor:
        consent = ConsentSummary(
            status="APPROVED" if membership.consent_at else None,
            guardian_name=await _guardian_name(db, membership, member),
        )
    if units is None:
        units = await unit_service.units_by_id(db, membership.club_id)
    fields = dict(
        membership_id=str(membership.id),
        user_id=str(member.id),
        name=member.name,
        handle=member.handle,
        avatar_url=visible_avatar(member, is_minor=profile_is_minor(member, has_guardian)),
        role=membership.role,
        status=membership.status,
        is_minor=minor,
        # Years, never the date: the roster is read by the whole staff.
        age=age_in_years(member.birth_date),
        since=membership.started_at,
        consent=consent,
        unit=unit_service.unit_ref(units.get(membership.unit_id)),
        officer_titles=titles.get(membership.id, []),
        completeness=_completeness(
            membership, member, minor=minor, has_approved_guardian=has_approved_guardian
        ),
        attendance_pct_90d=attendance.get(membership.id),
    )
    if not include_guardian_email:
        return MemberRow(**fields)
    return ManagedMemberRow(**fields, guardian_email=membership.guardian_email)


async def _guardian_name(db: AsyncSession, membership: ClubMembership, member: User) -> str | None:
    """Who authorized this minor. The club's human control: the director sees
    that a real adult said yes."""
    if membership.consent_by_id is not None:
        guardian = await db.get(User, membership.consent_by_id)
        return guardian.name if guardian else None
    stmt = (
        select(User.name)
        .join(Guardianship, Guardianship.guardian_id == User.id)
        .where(
            Guardianship.child_id == member.id,
            Guardianship.consent_status == CONSENT_GRANTED,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


# ----------------------------------------------------------------------------
# The roster as a spreadsheet (Bloque H §3)
# ----------------------------------------------------------------------------
CSV_COLUMNS = [
    "Nombre", "Cargo", "Rol", "Unidad", "Edad", "Estado", "Fecha de ingreso", "Asistencia 90 d (%)",
]
CSV_CONTACT_COLUMNS = ["Correo", "Tutor"]
# A cell that starts like a formula is neutralized (CSV injection).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


async def _guardian_names(db: AsyncSession, rows: list) -> dict[uuid.UUID, str]:
    """Who authorized each minor of the page, in two queries (see `_guardian_name`)."""
    minors = [(membership, member) for membership, member in rows if is_minor_user(member)]
    if not minors:
        return {}
    by_consent = {m.consent_by_id for m, _ in minors if m.consent_by_id is not None}
    consent_names = (
        dict((await db.execute(select(User.id, User.name).where(User.id.in_(by_consent)))).all())
        if by_consent
        else {}
    )
    approved: dict[uuid.UUID, str] = {}
    stmt = (
        select(Guardianship.child_id, User.name)
        .join(User, User.id == Guardianship.guardian_id)
        .where(
            Guardianship.child_id.in_([member.id for _, member in minors]),
            Guardianship.consent_status == CONSENT_GRANTED,
        )
        .order_by(Guardianship.created_at, Guardianship.id)
    )
    for child_id, name in (await db.execute(stmt)).all():
        approved.setdefault(child_id, name)
    out = {}
    for membership, member in minors:
        name = consent_names.get(membership.consent_by_id) or approved.get(member.id)
        if name:
            out[member.id] = name
    return out


@router.get("/{club_id}/members/export.csv", response_class=Response)
async def export_members(
    club_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The ACTIVE roster as CSV (UTF-8 with BOM, so a spreadsheet opens the accents right).
    The director and the administrators above also get the adult member's e-mail and the
    name of a minor's guardian; the secretary never does (spec §5.7). Audited."""
    club = await _club_for_manager(db, current_user, club_id)
    with_contact = await can_view_guardian_contact(db, current_user, club)
    rows = (
        await db.execute(
            select(ClubMembership, User)
            .join(User, User.id == ClubMembership.user_id)
            .where(
                ClubMembership.club_id == club.id,
                ClubMembership.status == membership_service.ACTIVE,
            )
            .order_by(User.name, User.id)
        )
    ).all()
    titles, attendance = await _roster_extras(db, club.id, [row[0].id for row in rows])
    units = await unit_service.units_by_id(db, club.id)
    guardians = await _guardian_names(db, rows) if with_contact else {}

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS + (CSV_CONTACT_COLUMNS if with_contact else []))
    for membership, member in rows:
        unit = units.get(membership.unit_id)
        pct = attendance.get(membership.id)
        line = [
            member.name,
            "; ".join(titles.get(membership.id, [])),
            membership.role,
            unit.name if unit is not None else "",
            age_in_years(member.birth_date),
            membership.status,
            membership.started_at.date().isoformat() if membership.started_at else "",
            f"{pct:.1f}" if pct is not None else "",
        ]
        if with_contact:
            # A minor's e-mail never leaves the backend (spec E §7).
            line += ["" if is_minor_user(member) else member.email, guardians.get(member.id, "")]
        writer.writerow([_cell(value) for value in line])

    record_audit(
        db,
        action="EXPORT",
        entity_type="CLUB_ROSTER",
        entity_id=club.id,
        actor=current_user,
        details=f"Roster of club {club.id} as CSV ({len(rows)} rows)",
        metadata={"rows": len(rows), "with_contact": with_contact},
        request=request,
    )
    await db.commit()
    filename = f"nomina-{utcnow().date().isoformat()}.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------------------------------------------------------
# Roles and removals
# ----------------------------------------------------------------------------
@router.patch("/{club_id}/members/{membership_id}", response_model=None)
async def change_member_role(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberRoleUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MemberRow | ManagedMemberRow:
    club = await _club_for_manager(db, current_user, club_id)
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    await membership_service.change_role(
        db, membership, new_role=payload.role, actor=current_user, request=request
    )
    await db.commit()
    member = await db.get(User, membership.user_id)
    sees_guardians = await can_view_guardian_contact(db, current_user, club)
    return await _member_row(db, membership, member, include_guardian_email=sees_guardians)


@router.post("/{club_id}/members/{membership_id}/remove", response_model=MembershipEnded)
async def remove_member(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberRemoval,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take somebody off the roster, always with a reason: it reaches them."""
    club = await _club_for_manager(db, current_user, club_id)
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    membership, member = await membership_service.remove_member(
        db, membership, reason=payload.reason, actor=current_user, request=request
    )
    await notifications.queue_membership_decision(
        db,
        background,
        membership=membership,
        member=member,
        club=club,
        approved=False,
        reason=payload.reason,
    )
    await db.commit()
    return MembershipEnded(
        membership_id=str(membership.id),
        club_id=str(membership.club_id),
        status=membership.status,
        end_reason=membership.end_reason,
    )


# ----------------------------------------------------------------------------
# The queue of people asking to join (E4)
# ----------------------------------------------------------------------------
@router.get("/{club_id}/requests", response_model=list[RequestRow])
async def list_requests(
    club_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What the club has to decide. A minor who asked is NOT here until their
    guardian authorized: the club does not get to know about them first (§7)."""
    club = await _club_for_manager(db, current_user, club_id)
    rows = await membership_service.pending_requests(db, club.id)
    # `pending_requests` gives up on stale ones; that write belongs to this read.
    await db.commit()

    out = []
    for membership in rows:
        member = await db.get(User, membership.user_id)
        if member is None:
            continue
        minor = is_minor_user(member)
        out.append(
            RequestRow(
                membership_id=str(membership.id),
                user_id=str(member.id),
                name=member.name,
                role=membership.role,
                status=membership.status,
                is_minor=minor,
                age=age_in_years(member.birth_date),
                message=membership.message,
                source=membership.source,
                created_at=membership.created_at,
                consent=(
                    ConsentSummary(
                        status="APPROVED" if membership.consent_at else None,
                        guardian_name=await _guardian_name(db, membership, member),
                    )
                    if minor
                    else None
                ),
            )
        )
    return out


async def _decide(
    db: AsyncSession,
    background: BackgroundTasks,
    *,
    club: Organization,
    membership_id: uuid.UUID,
    actor: User,
    approve: bool,
    role: str | None,
    reason: str | None,
    request: Request,
) -> ClubMembership:
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    membership, member = await membership_service.decide_request(
        db,
        membership,
        actor=actor,
        approve=approve,
        role=role,
        reason=reason,
        request=request,
    )
    await notifications.queue_membership_decision(
        db,
        background,
        membership=membership,
        member=member,
        club=club,
        approved=approve,
        reason=reason,
    )
    await db.commit()
    return membership


@router.post("/{club_id}/requests/{membership_id}/approve", response_model=MembershipOut)
async def approve_request(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: RequestDecision,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Let somebody in, with the role they asked for or another the actor may
    grant. If they belonged to another club, this is the transfer."""
    club = await _club_for_manager(db, current_user, club_id)
    membership = await _decide(
        db,
        background,
        club=club,
        membership_id=membership_id,
        actor=current_user,
        approve=True,
        role=payload.role,
        reason=None,
        request=request,
    )
    return as_membership_out(membership, club)


@router.post("/{club_id}/requests/{membership_id}/reject", response_model=MembershipOut)
async def reject_request(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: RequestDecision,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await _club_for_manager(db, current_user, club_id)
    membership = await _decide(
        db,
        background,
        club=club,
        membership_id=membership_id,
        actor=current_user,
        approve=False,
        role=None,
        reason=payload.reason,
        request=request,
    )
    return as_membership_out(membership, club)


@router.post("/{club_id}/requests/approve-all", response_model=BulkApproval)
async def approve_all_requests(
    club_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One touch per intake (decision D2). Only STUDENT requests: a role that
    carries authority over minors is always decided one by one."""
    club = await _club_for_manager(db, current_user, club_id)
    approved = 0
    for membership in await membership_service.pending_requests(db, club.id):
        if membership.role != STUDENT:
            continue
        membership, member = await membership_service.decide_request(
            db, membership, actor=current_user, approve=True, request=request
        )
        await notifications.queue_membership_decision(
            db, background, membership=membership, member=member, club=club, approved=True
        )
        approved += 1
    await db.commit()
    return BulkApproval(approved=approved)


# ----------------------------------------------------------------------------
# Invitations (E3)
# ----------------------------------------------------------------------------
@router.post(
    "/{club_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    club_id: uuid.UUID,
    payload: InvitationCreate,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The link the club shares. Its token comes back ONCE: it is never stored
    in the clear and no later read can show it again."""
    club = await _club_for_manager(db, current_user, club_id)
    invitation, token = await invitation_service.create(
        db,
        club=club,
        actor=current_user,
        role=payload.role,
        max_uses=payload.max_uses,
        expires_in_days=payload.expires_in_days,
        email=payload.email,
        unit_id=payload.unit_id,
        request=request,
    )
    log = None
    if invitation.email:
        log = notifications.stage_log(
            db,
            kind=notifications.CLUB_INVITATION,
            email=invitation.email,
            entity_type=notifications.INVITATION,
            entity_id=invitation.id,
        )
    await db.commit()

    if log is not None:
        # After the commit, and never able to fail the request (pattern of org.py).
        background.add_task(
            notifications.send_and_record,
            email_service.send_club_invitation_email,
            log.id,
            invitation.email,
            club.name,
            invitation.role,
            invitation_service.join_url(token),
            current_user.name,
        )
    return InvitationCreated(
        invitation=as_invitation_out(
            invitation,
            state=invitation_service.state_of(invitation),
            requires_approval=invitation_service.requires_approval(invitation),
        ),
        token=token,
        url=invitation_service.join_url(token),
        whatsapp_url=invitation_service.whatsapp_url(club.name, token),
    )


@router.get("/{club_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    club_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await _club_for_manager(db, current_user, club_id)
    stmt = (
        select(ClubInvitation)
        .where(ClubInvitation.club_id == club.id)
        .order_by(ClubInvitation.created_at.desc())
    )
    return [
        as_invitation_out(
            row,
            state=invitation_service.state_of(row),
            requires_approval=invitation_service.requires_approval(row),
        )
        for row in (await db.execute(stmt)).scalars().all()
    ]


@router.delete("/{club_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    club_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kill a link. Idempotent: a link that was already dead answers the same."""
    club = await _club_for_manager(db, current_user, club_id)
    stmt = select(ClubInvitation).where(
        ClubInvitation.id == invitation_id, ClubInvitation.club_id == club.id
    )
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitación no encontrada en este club")
    if not can_grant_club_role(current_user, invitation.role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes revocar invitaciones de ese rol."
        )
    await invitation_service.revoke(db, invitation, actor=current_user, request=request)
    await db.commit()


@router.post(
    "/{club_id}/invitations/{invitation_id}/renew",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def renew_invitation(
    club_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bloque H §4: a new multi-use link with the same rules (role, uses, unit, length,
    within the 90-day maximum); the old one is revoked. Its token comes back ONCE."""
    club = await _club_for_manager(db, current_user, club_id)
    stmt = select(ClubInvitation).where(
        ClubInvitation.id == invitation_id, ClubInvitation.club_id == club.id
    )
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitación no encontrada en este club")
    if not can_grant_club_role(current_user, invitation.role):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "No puedes renovar invitaciones de ese rol."
        )
    renewed, token = await invitation_service.renew(
        db, invitation, club=club, actor=current_user, request=request
    )
    await db.commit()
    return InvitationCreated(
        invitation=as_invitation_out(
            renewed,
            state=invitation_service.state_of(renewed),
            requires_approval=invitation_service.requires_approval(renewed),
        ),
        token=token,
        url=invitation_service.join_url(token),
        whatsapp_url=invitation_service.whatsapp_url(club.name, token),
    )


# ----------------------------------------------------------------------------
# Control data of the club
# ----------------------------------------------------------------------------
@router.patch("/{club_id}/profile", response_model=ClubProfileOut)
async def update_club_profile(
    club_id: uuid.UUID,
    payload: ClubProfileUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Meeting day and time, contact, whether the club takes join requests and (Bloque H)
    the description and logo of its public page (`GET /clubs/{id}/profile`).
    A whitelist inside `metadata_json.profile`: it never touches the name, the
    location or the place of the club in the tree."""
    club = await _club_for_manager(db, current_user, club_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que actualizar")

    metadata = dict(club.metadata_json or {})
    profile = {**metadata.get("profile", {}), **changes}
    # Reassign a new dict: in-place JSONB mutations are not tracked.
    club.metadata_json = {**metadata, "profile": profile}
    club.updated_at = utcnow()
    record_audit(
        db,
        action="CLUB_PROFILE_UPDATE",
        entity_type="ORGANIZATION",
        entity_id=club.id,
        actor=current_user,
        details=f"Updated fields: {', '.join(sorted(changes))}",
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await db.commit()
    return ClubProfileOut(
        club_id=str(club.id),
        name=club.name,
        profile=profile,
        ministry=await ministry_service.ref_of(db, club),
    )


# ----------------------------------------------------------------------------
# Units (E5). A unit is a light table, not a node of the tree: the member keeps
# hanging from the club, which is what the whole RBAC reads.
# ----------------------------------------------------------------------------
@router.get("/{club_id}/units", response_model=list[UnitOut])
async def list_units(
    club_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Active units with their headcount, cap, age bracket and counselor."""
    club = await membership_service.get_club(db, club_id)
    if not await can_view_roster(db, current_user, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROSTER_DETAIL)
    counts = await unit_service.member_counts(db, club.id)
    return [
        await unit_service.as_out(db, unit, members=counts.get(unit.id, 0))
        for unit in await unit_service.active_units(db, club.id)
    ]


@router.post("/{club_id}/units", response_model=UnitOut, status_code=status.HTTP_201_CREATED)
async def create_unit(
    club_id: uuid.UUID,
    payload: UnitCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    club = await _club_for_manager(db, current_user, club_id)
    unit = await unit_service.create(
        db,
        club=club,
        actor=current_user,
        name=payload.name,
        min_age=payload.min_age,
        max_age=payload.max_age,
        capacity=payload.capacity,
        request=request,
    )
    await db.commit()
    return await unit_service.as_out(db, unit, members=0)


@router.patch("/{club_id}/units/{unit_id}", response_model=UnitOut)
async def update_unit(
    club_id: uuid.UUID,
    unit_id: uuid.UUID,
    payload: UnitUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The cap is hard, so it never drops below the people already inside."""
    club = await _club_for_manager(db, current_user, club_id)
    unit = await unit_service.get_unit(db, club, unit_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No hay nada que actualizar")
    await unit_service.update(db, unit, actor=current_user, changes=changes, request=request)
    await db.commit()
    return await unit_service.as_out(db, unit)


@router.delete("/{club_id}/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_unit(
    club_id: uuid.UUID,
    unit_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Archive, never delete: the year's history stays and the name is freed."""
    club = await _club_for_manager(db, current_user, club_id)
    unit = await unit_service.get_unit(db, club, unit_id)
    await unit_service.archive(db, unit, actor=current_user, request=request)
    await db.commit()


@router.put("/{club_id}/units/{unit_id}/counselor", response_model=UnitOut)
async def set_unit_counselor(
    club_id: uuid.UUID,
    unit_id: uuid.UUID,
    payload: CounselorAssign,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The director's act, not the secretary's: whoever leads a unit is alone
    with a group of minors (spec §5.7)."""
    club = await membership_service.get_club(db, club_id)
    if not await can_appoint_counselor(db, current_user, club):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Sólo la dirección del club nombra consejeros de unidad"
        )
    unit = await unit_service.get_unit(db, club, unit_id)
    membership = (
        await membership_service.get_membership_of_club(db, club, payload.membership_id)
        if payload.membership_id is not None
        else None
    )
    await unit_service.set_counselor(
        db, unit, membership=membership, actor=current_user, request=request
    )
    await db.commit()
    return await unit_service.as_out(db, unit)


@router.put("/{club_id}/members/{membership_id}/unit", response_model=MemberUnitOut)
async def set_member_unit(
    club_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberUnitAssign,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Put a member in a unit, move them, or take them out (`unit_id: null`).
    The cap is hard; the age bracket only comes back as `age_warning`."""
    club = await _club_for_manager(db, current_user, club_id)
    membership = await membership_service.get_membership_of_club(db, club, membership_id)
    result = await unit_service.assign_member(
        db,
        club=club,
        membership=membership,
        unit_id=payload.unit_id,
        actor=current_user,
        request=request,
    )
    await db.commit()
    return result
