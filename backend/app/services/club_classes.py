"""Bloque F · F3 — el club sigue una CLASE en grupo (spec F §1.8).

Nothing here is a second engine. Every act is block A's, applied to many members at once:
  * enrol        -> `portfolio.stage_enrollment`, the very function a self-enrolment uses;
  * sign         -> the verdict COMPLETE of `portfolio.review_requirement`, without the
                    SUBMITTED step (the leader vouches in person: «firma en bloque»), then
                    `portfolio.recompute_ready`, rule 2 of A;
  * invest       -> `portfolio.issue`, one certificate per READY enrollment.

Who may do what is decided in app/rbac.py (`can_enroll_member`, `can_bulk_sign`,
`can_invest_in_club`); what a reader of the club sees follows the roster
(`attendance.reading_scope`: a counselor reads their units, staff without a church letter
do not see minors).

Where the club's ministries come from (rule 3 of ESTADO.md: never a hidden default): the
bridge `organization_ministries` (022_club_ministries.sql; the principal is the column
`organizations.ministry_id`, 019) and, while that column is still NULL, the slug the club
declared in `metadata_json.ministry` (`app.services.ministries.all_of_club`). A club works with
the classes of EVERY one of its ministries; a club that declares none is offered the classes of
every ministry, and the screen warns about it.
"""
import uuid
from datetime import date

from fastapi import HTTPException, Request, status
from sqlalchemy import and_, cast, exists, func, select
from sqlalchemy.types import Date
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import violated_constraint
from app.models import (
    ActivityLog,
    ClubMembership,
    ClubUnit,
    Guardianship,
    HonorEnrollment,
    Ministry,
    Organization,
    Program,
    ProgramRequirement,
    ProgramSection,
    RequirementProgress,
    User,
)
from app.people import is_minor, is_minor_user
from app.rbac import (
    ISSUER_CLUB,
    can_bulk_sign,
    can_enroll_member,
    can_invest_in_club,
    can_issue,
    can_manage_members,
    led_unit_ids,
    may_sign_in_club,
    profile_is_minor,
    visible_avatar,
)
from app.schemas.club_classes import (
    AvailableClass,
    BlockSignIn,
    BlockSignOut,
    ClassEnrollIn,
    ClassEnrollOut,
    ClassMatrix,
    ClubClass,
    ClubClasses,
    EnrolledMember,
    InvestedEnrollment,
    InvestIn,
    InvestOut,
    MatrixMember,
    MatrixProgram,
    MatrixQuantity,
    MatrixRequirement,
    MatrixSection,
    MatrixUser,
    SkippedCell,
    SkippedEnrollment,
    SkippedMember,
)
from app.schemas.portfolio import CertificateIssue
from app.schemas.program import ProgramRef
from app.schemas.unit import UnitRef
from app.security import STUDENT, utcnow
from app.services import attendance, certificate_signatures, curriculum, portfolio, portfolio_links, programs
from app.services import ministries as ministry_service
from app.services import units as unit_service
from app.services.audit import record_audit

ACTIVE = "ACTIVE"
PUBLISHED, DRAFT = "PUBLISHED", "DRAFT"
CLASS_KINDS = ("CLASS", "CURRICULUM")
IN_PROGRESS, READY, CERTIFIED, WITHDRAWN = (
    portfolio.IN_PROGRESS, portfolio.READY, portfolio.CERTIFIED, portfolio.WITHDRAWN
)
COMPLETE = portfolio.COMPLETE
BLOCK_SIGN = "block_sign"
VIA_CLUB = "club"

# Error details the frontend reads as codes (contract of F3).
PROGRAM_NOT_PUBLISHED = "program_not_published"
PROGRAM_OTHER_MINISTRY = "program_not_in_club_ministry"
PROGRAM_BY_ASSOCIATION = "program_issued_by_association"
PROGRAM_NOT_FOUND = "Programa no encontrado"
ENROLL_FORBIDDEN = "No puedes inscribir miembros de este club en una clase"
UNIT_FORBIDDEN = "Sólo puedes trabajar con los miembros de tu unidad"
SIGN_FORBIDDEN = "No puedes firmar requisitos en este club"
INVEST_FORBIDDEN = "Sólo el director del club inviste"
MEMBERSHIP_NOT_FOUND = "Membresía no encontrada en este club"

# A member's guardian exists: with no birth date, that is what makes them a minor (Bloque G).
_HAS_GUARDIAN = exists().where(Guardianship.child_id == User.id).correlate(User)


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def _program(db: AsyncSession, program_id: uuid.UUID) -> Program:
    """A program the club may work with. A DRAFT does not exist for a club (404); an
    ARCHIVED one still serves the members enrolled before it was archived."""
    program = await db.get(Program, program_id)
    if program is None or program.status == DRAFT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROGRAM_NOT_FOUND)
    return program


async def _club_ministry(db: AsyncSession, club: Organization) -> tuple[bool, list[Ministry]]:
    """-> (the club declares a ministry, EVERY one of its rows, principal first). An unknown
    declared slug is declared but matches no program (an empty list)."""
    return await ministry_service.all_of_club(db, club)


def _live_in_club(club: Organization):
    """Join condition: the enrolled person is an ACTIVE member of `club` today."""
    return and_(
        ClubMembership.user_id == HonorEnrollment.user_id,
        ClubMembership.club_id == club.id,
        ClubMembership.status == ACTIVE,
    )


# ----------------------------------------------------------------------------
# 1. The classes of the club
# ----------------------------------------------------------------------------
async def list_classes(db: AsyncSession, actor: User, club: Organization) -> ClubClasses:
    """Every class with at least one member enrolled (counted within the reader's reach),
    and the published classes nobody follows yet."""
    units, hide_minors = await attendance.reading_scope(db, actor, club)
    counts: dict[uuid.UUID, dict[str, int]] = {}
    if units is None or units:
        stmt = (
            select(HonorEnrollment.program_id, HonorEnrollment.status, User.birth_date, User.is_minor)
            .join(ClubMembership, _live_in_club(club))
            .join(User, User.id == HonorEnrollment.user_id)
            .where(HonorEnrollment.program_id.is_not(None), HonorEnrollment.status != WITHDRAWN)
        )
        if units is not None:
            stmt = stmt.where(ClubMembership.unit_id.in_(units))
        for program_id, enrollment_status, birth_date, declared_minor in (await db.execute(stmt)).all():
            if hide_minors and is_minor(birth_date, declared_minor):
                continue
            by_status = counts.setdefault(program_id, {})
            by_status[enrollment_status] = by_status.get(enrollment_status, 0) + 1

    declared, ministries = await _club_ministry(db, club)
    offered = and_(Program.status == PUBLISHED, Program.kind.in_(CLASS_KINDS))
    if declared:
        # The classes of every ministry of the club (022); none matches an unknown slug.
        offered = and_(offered, Program.ministry_id.in_([row.id for row in ministries]))
    if counts:
        # A class the club follows stays listed even once archived: its members are mid-way.
        offered = offered | Program.id.in_(list(counts))
    rows = (
        await db.execute(
            select(Program).where(offered).order_by(Program.sort_order, Program.name, Program.id)
        )
    ).scalars().all()
    ministry_ids = {row.ministry_id for row in rows}
    slugs = dict(
        (await db.execute(select(Ministry.id, Ministry.slug).where(Ministry.id.in_(ministry_ids)))).all()
    ) if ministry_ids else {}

    classes, available = [], []
    for program in rows:
        if program.status == DRAFT:
            continue
        by_status = counts.get(program.id)
        if by_status:
            classes.append(
                ClubClass(
                    program=ProgramRef(
                        id=str(program.id), slug=program.slug, name=program.name, kind=program.kind,
                        image_url=program.image_url, ministry=slugs.get(program.ministry_id),
                    ),
                    enrolled=sum(by_status.values()),
                    complete=by_status.get(READY, 0),
                    invested=by_status.get(CERTIFIED, 0),
                    in_progress=by_status.get(IN_PROGRESS, 0),
                )
            )
        elif program.status == PUBLISHED:
            available.append(
                AvailableClass(
                    id=str(program.id), slug=program.slug, name=program.name, kind=program.kind,
                    image_url=program.image_url, ministry=slugs.get(program.ministry_id),
                )
            )
    return ClubClasses(
        classes=classes,
        available=available,
        ministry=ministry_service.as_ref(ministries[0] if ministries else None),
        ministries=[ministry_service.as_ref(row) for row in ministries],
    )


# ----------------------------------------------------------------------------
# 2. Enrol the club, a unit or a list
# ----------------------------------------------------------------------------
async def enroll(
    db: AsyncSession,
    actor: User,
    club: Organization,
    program_id: uuid.UUID,
    payload: ClassEnrollIn,
    request: Request | None,
) -> ClassEnrollOut:
    """Enrol each ACTIVE member not already enrolled. Idempotent: running it twice only
    reports `already_enrolled`. Without `membership_ids` nor `unit_id`: every active
    STUDENT of the club (for a counselor, of their units)."""
    program = await db.get(Program, program_id)
    if program is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, PROGRAM_NOT_FOUND)
    manages = await can_manage_members(db, actor, club)
    led = [] if manages else await led_unit_ids(db, actor, club)
    if not manages and not led:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ENROLL_FORBIDDEN)
    if payload.unit_id is not None:
        await unit_service.get_unit(db, club, payload.unit_id)   # 404 for another club's unit
        if not await can_enroll_member(db, actor, club, payload.unit_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, UNIT_FORBIDDEN)
    if program.status != PUBLISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, PROGRAM_NOT_PUBLISHED)
    declared, ministries = await _club_ministry(db, club)
    if declared and program.ministry_id not in {row.id for row in ministries}:
        raise HTTPException(status.HTTP_409_CONFLICT, PROGRAM_OTHER_MINISTRY)
    # Resolved ONCE: every member gets the same requirement list, in the program's source
    # language (a leader enrols the club; nobody chose a language for each child).
    source = await curriculum.resolve(db, None, program.id, None)

    base = select(ClubMembership, User).join(User, User.id == ClubMembership.user_id).where(
        ClubMembership.club_id == club.id
    )
    if payload.membership_ids:
        wanted = list(dict.fromkeys(payload.membership_ids))
        found = {
            membership.id: (membership, member)
            for membership, member in (
                await db.execute(base.where(ClubMembership.id.in_(wanted)))
            ).all()
        }
        if len(found) != len(wanted):
            raise HTTPException(status.HTTP_404_NOT_FOUND, MEMBERSHIP_NOT_FOUND)
        candidates = [found[membership_id] for membership_id in wanted]
    else:
        stmt = base.where(ClubMembership.status == ACTIVE, ClubMembership.role == STUDENT)
        if payload.unit_id is not None:
            stmt = stmt.where(ClubMembership.unit_id == payload.unit_id)
        elif not manages:
            stmt = stmt.where(ClubMembership.unit_id.in_(led))
        candidates = (await db.execute(stmt.order_by(User.name, User.id))).all()

    user_ids = [member.id for _, member in candidates]
    live = set(
        (
            await db.execute(
                select(HonorEnrollment.user_id).where(
                    HonorEnrollment.program_id == program.id,
                    HonorEnrollment.user_id.in_(user_ids),
                    HonorEnrollment.status != WITHDRAWN,
                )
            )
        ).scalars().all()
    ) if user_ids else set()

    enrolled: list[EnrolledMember] = []
    skipped: list[SkippedMember] = []
    new_members: list[User] = []
    for membership, member in candidates:
        reason = None
        if membership.status != ACTIVE or member.status != ACTIVE:
            reason = "not_active"
        elif (payload.unit_id is not None and membership.unit_id != payload.unit_id) or (
            not manages and membership.unit_id not in led
        ):
            reason = "out_of_unit"
        elif member.id in live:
            reason = "already_enrolled"
        if reason is None:
            try:
                async with db.begin_nested():
                    enrollment = await portfolio.stage_enrollment(
                        db, actor, member, source, request,
                        audit_extra={"via": VIA_CLUB, "on_behalf": member.id != actor.id,
                                     "club_id": str(club.id), "membership_id": str(membership.id)},
                    )
            except IntegrityError as exc:
                if violated_constraint(exc) not in portfolio.ACTIVE_ENROLLMENT_CONSTRAINTS:
                    raise
                reason = "already_enrolled"   # enrolled by somebody else a moment ago
            else:
                live.add(member.id)
                new_members.append(member)
                enrolled.append(
                    EnrolledMember(membership_id=str(membership.id), enrollment_id=str(enrollment.id))
                )
        if reason is not None:
            skipped.append(SkippedMember(membership_id=str(membership.id), reason=reason))

    # Bloque F · F2, exactly as after a self-enrolment: what the member already holds
    # completes its requirement now. Same transaction.
    for member in new_members:
        await portfolio_links.sync(db, actor, user_id=member.id, request=request)
    await db.commit()
    return ClassEnrollOut(enrolled=enrolled, skipped=skipped)


# ----------------------------------------------------------------------------
# 3. The matrix members × requirements
# ----------------------------------------------------------------------------
async def matrix(
    db: AsyncSession,
    actor: User,
    club: Organization,
    program_id: uuid.UUID,
    unit_id: uuid.UUID | None,
) -> ClassMatrix:
    """Members (within the reader's reach) × requirements. A fixed number of queries,
    whatever the members: program, requirements, members, progress."""
    units, hide_minors = await attendance.reading_scope(db, actor, club)
    if unit_id is not None:
        if units is not None and unit_id not in units:
            raise HTTPException(status.HTTP_403_FORBIDDEN, UNIT_FORBIDDEN)
        if units is None:
            await unit_service.get_unit(db, club, unit_id)
        units = [unit_id]
    program = await _program(db, program_id)

    sections: dict[uuid.UUID, MatrixSection] = {}
    requirement_rows = (
        await db.execute(
            select(ProgramRequirement, ProgramSection)
            .join(ProgramSection, ProgramSection.id == ProgramRequirement.section_id)
            .where(ProgramRequirement.program_id == program.id)
            .order_by(ProgramSection.position, ProgramRequirement.position)
        )
    ).all()
    for requirement, section in requirement_rows:
        sections.setdefault(
            section.id, MatrixSection(slug=section.slug, name=section.name, requirements=[])
        ).requirements.append(
            MatrixRequirement(
                id=str(requirement.id), position=requirement.position,
                label=requirement.label or str(requirement.position), kind=requirement.kind,
            )
        )
    by_position = {requirement.position: str(requirement.id) for requirement, _ in requirement_rows}

    members = []
    if units is None or units:
        stmt = (
            select(ClubMembership, User, HonorEnrollment, ClubUnit.name, _HAS_GUARDIAN.label("guardian"))
            .join(User, User.id == ClubMembership.user_id)
            .join(
                HonorEnrollment,
                and_(
                    HonorEnrollment.user_id == ClubMembership.user_id,
                    HonorEnrollment.program_id == program.id,
                    HonorEnrollment.status != WITHDRAWN,
                ),
            )
            .outerjoin(ClubUnit, ClubUnit.id == ClubMembership.unit_id)
            .where(ClubMembership.club_id == club.id, ClubMembership.status == ACTIVE)
            .order_by(User.name, User.id)
        )
        if units is not None:
            stmt = stmt.where(ClubMembership.unit_id.in_(units))
        members = [
            row for row in (await db.execute(stmt)).all()
            if not (hide_minors and is_minor_user(row[1]))
        ]

    cells: dict[uuid.UUID, dict[str, str]] = {row[2].id: {} for row in members}
    if cells:
        progress_rows = await db.execute(
            select(
                RequirementProgress.enrollment_id,
                RequirementProgress.program_requirement_id,
                RequirementProgress.requirement_position,
                RequirementProgress.status,
            ).where(RequirementProgress.enrollment_id.in_(list(cells)))
        )
        for enrollment_id, requirement_id, position, progress_status in progress_rows.all():
            key = str(requirement_id) if requirement_id else by_position.get(position)
            if key is not None:
                cells[enrollment_id][key] = progress_status

    hours = await _approved_hours(db, [row[2].id for row in members], requirement_rows)

    out = []
    for membership, member, enrollment, unit_name, has_guardian in members:
        row = cells[enrollment.id]
        complete = sum(1 for value in row.values() if value == COMPLETE)
        out.append(
            MatrixMember(
                membership_id=str(membership.id),
                enrollment_id=str(enrollment.id),
                user=MatrixUser(
                    id=str(member.id),
                    name=member.name,
                    handle=member.handle,
                    avatar_url=visible_avatar(member, is_minor=profile_is_minor(member, has_guardian)),
                ),
                unit=UnitRef(id=str(membership.unit_id), name=unit_name) if unit_name else None,
                status=enrollment.status,
                progress_pct=round(complete * 100 / len(row)) if row else 0,
                cells=row,
                hours=hours.get(enrollment.id, {}),
            )
        )
    return ClassMatrix(
        program=MatrixProgram(id=str(program.id), slug=program.slug, name=program.name),
        sections=list(sections.values()),
        members=out,
    )


async def _approved_hours(
    db: AsyncSession, enrollment_ids: list[uuid.UUID], requirement_rows
) -> dict[uuid.UUID, dict[str, MatrixQuantity]]:
    """«3 / 5 h» for every `HOURS` requirement of every enrollment, in ONE query and with the
    rule of the member's own card (`activity.approved_totals`): only APPROVED rows, and only
    from the day the enrollment started. Nothing is asked when the class has no `HOURS`."""
    goals = [
        (str(requirement.id), requirement.activity_category, float(requirement.target_quantity))
        for requirement, _ in requirement_rows
        if requirement.kind == curriculum.HOURS and requirement.target_quantity
    ]
    if not goals or not enrollment_ids:
        return {}
    stmt = (
        select(HonorEnrollment.id, ActivityLog.category, func.sum(ActivityLog.quantity))
        .join(ActivityLog, ActivityLog.user_id == HonorEnrollment.user_id)
        .where(
            HonorEnrollment.id.in_(enrollment_ids),
            ActivityLog.status == "APPROVED",
            ActivityLog.performed_on >= func.coalesce(cast(HonorEnrollment.started_at, Date), date.min),
        )
        .group_by(HonorEnrollment.id, ActivityLog.category)
    )
    totals: dict[uuid.UUID, dict[str, float]] = {}
    for enrollment_id, category, total in (await db.execute(stmt)).all():
        totals.setdefault(enrollment_id, {})[category] = float(total)
    return {
        enrollment_id: {
            requirement_id: MatrixQuantity(
                approved=totals.get(enrollment_id, {}).get(category or "", 0.0), target=target
            )
            for requirement_id, category, target in goals
        }
        for enrollment_id in enrollment_ids
    }


# ----------------------------------------------------------------------------
# 4. «Firma en bloque»
# ----------------------------------------------------------------------------
async def block_sign(
    db: AsyncSession,
    actor: User,
    club: Organization,
    program_id: uuid.UUID,
    payload: BlockSignIn,
    request: Request | None,
) -> BlockSignOut:
    """Mark each (enrollment, requirement) COMPLETE. What is out of the actor's reach, closed,
    already complete or completed only by hours is skipped with its reason, never an error.

    A practical requirement is signed without evidence: the leader saw it done. The audit row
    says so (`decided_via: block_sign`, `is_practical`), so rule 1 of A is waived knowingly."""
    led = await led_unit_ids(db, actor, club)
    if not may_sign_in_club(actor, club, led):
        raise HTTPException(status.HTTP_403_FORBIDDEN, SIGN_FORBIDDEN)
    program = await _program(db, program_id)

    entries = list(dict.fromkeys((e.enrollment_id, e.requirement_id) for e in payload.entries))
    enrollment_ids = sorted({enrollment_id for enrollment_id, _ in entries})
    requirement_ids = list({requirement_id for _, requirement_id in entries})
    # Locked in a stable order: serializes this with a verdict or a certificate on the same card.
    found = {
        enrollment.id: (enrollment, unit_id, member)
        for enrollment, unit_id, member in (
            await db.execute(
                select(HonorEnrollment, ClubMembership.unit_id, User)
                .join(ClubMembership, _live_in_club(club))
                .join(User, User.id == HonorEnrollment.user_id)
                .where(HonorEnrollment.id.in_(enrollment_ids), HonorEnrollment.program_id == program.id)
                .order_by(HonorEnrollment.id)
                .with_for_update(of=HonorEnrollment)
            )
        ).all()
    }
    progress = {}
    if found:
        progress = {
            (row.enrollment_id, row.program_requirement_id): row
            for row in (
                await db.execute(
                    select(RequirementProgress).where(
                        RequirementProgress.enrollment_id.in_(list(found)),
                        RequirementProgress.program_requirement_id.in_(requirement_ids),
                    )
                )
            ).scalars()
        }

    now = utcnow()
    bulk_id = str(uuid.uuid4())
    reach: dict[uuid.UUID, bool] = {}
    signed: list[tuple[RequirementProgress, HonorEnrollment, str]] = []
    touched: dict[uuid.UUID, HonorEnrollment] = {}
    skipped: list[SkippedCell] = []
    for enrollment_id, requirement_id in entries:
        row = found.get(enrollment_id)
        cell = progress.get((enrollment_id, requirement_id))
        reason = None
        if row is None or cell is None:
            reason = "not_found"
        else:
            enrollment, unit_id, member = row
            if enrollment_id not in reach:
                reach[enrollment_id] = await can_bulk_sign(db, actor, enrollment, member, unit_id, led)
            if not reach[enrollment_id]:
                reason = "own_enrollment" if enrollment.user_id == actor.id else "out_of_reach"
            elif enrollment.status in portfolio.FROZEN:
                reason = "closed"
            elif cell.status == COMPLETE:
                reason = "already_complete"
            elif cell.kind == curriculum.HOURS:
                reason = "hours_only"   # F2, rule 8: an HOURS requirement has ONE route
        if reason is not None:
            skipped.append(SkippedCell(enrollment_id=str(enrollment_id),
                                       requirement_id=str(requirement_id), reason=reason))
            continue
        previous = cell.status
        cell.status = COMPLETE
        cell.completed_via = "REVIEW"
        cell.reviewed_by_id = actor.id
        cell.reviewed_at = now
        cell.review_note = payload.note
        signed.append((cell, enrollment, previous))
        touched[enrollment.id] = enrollment

    if signed:
        await db.flush()
        # Rule 2 of A, through the same function every automatic change uses.
        for enrollment in touched.values():
            await portfolio.touch(db, enrollment)
            await portfolio.recompute_ready(db, enrollment)
        # One audit row per requirement and member, so each minor's trail is complete.
        for cell, enrollment, previous in signed:
            record_audit(
                db,
                action="REQUIREMENT_REVIEW",
                entity_type=portfolio.PROGRESS,
                entity_id=cell.id,
                actor=actor,
                details=payload.note,
                metadata={"enrollment_id": str(enrollment.id), "position": cell.requirement_position,
                          "from": previous, "verdict": COMPLETE,
                          "enrollment_status": enrollment.status, "decided_via": BLOCK_SIGN,
                          "bulk_id": bulk_id, "program_id": str(program.id),
                          "club_id": str(club.id), "is_practical": cell.is_practical},
                request=request,
            )
        await db.commit()
    return BlockSignOut(signed=len(signed), skipped=skipped)


# ----------------------------------------------------------------------------
# 5. Investidura
# ----------------------------------------------------------------------------
async def invest(
    db: AsyncSession,
    actor: User,
    club: Organization,
    program_id: uuid.UUID,
    payload: InvestIn,
    request: Request | None,
) -> InvestOut:
    """Issue the investiture of each READY enrollment through `portfolio.issue` — the same
    certificate, folio, hash and audit a single investiture gets."""
    if not can_invest_in_club(actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, INVEST_FORBIDDEN)
    program = await _program(db, program_id)
    if program.issuer_level != ISSUER_CLUB:
        # D3: Master Guide, EMC, CMJA are invested by the Association, never by a club.
        raise HTTPException(status.HTTP_409_CONFLICT, PROGRAM_BY_ASSOCIATION)

    wanted = list(dict.fromkeys(payload.enrollment_ids))
    rows = (
        await db.execute(
            select(HonorEnrollment)
            .join(ClubMembership, _live_in_club(club))
            .where(
                HonorEnrollment.id.in_(wanted),
                HonorEnrollment.program_id == program.id,
                HonorEnrollment.status != WITHDRAWN,
            )
        )
    ).scalars().all()
    # Plain values: `portfolio.issue` commits after each certificate.
    statuses = {row.id: row.status for row in rows}
    ready = [row for row in rows if row.status == READY]
    if ready:
        # No template of kind `program` for the ministry: 409 before a single certificate.
        await programs.program_template(db, payload.template, await curriculum.award_for(db, ready[0]))
    allowed = {row.id: await can_issue(db, actor, row) for row in ready}
    # 021: checked (and the saved signature read) once, before a single certificate is issued.
    signatures = await certificate_signatures.prepare(
        {"signature_director": payload.signature_director, "signature_instructor": payload.signature_instructor},
        actor,
    ) if ready else {}

    certificate = CertificateIssue(
        template=payload.template,
        issued_date=payload.issued_date or utcnow().date(),
        place=payload.place,
        instructor_name=payload.instructor_name,
        strings=payload.strings,
    )
    bulk_id = str(uuid.uuid4())
    invested: list[InvestedEnrollment] = []
    skipped: list[SkippedEnrollment] = []
    for enrollment_id in wanted:
        current = statuses.get(enrollment_id)
        reason = None
        if current is None:
            reason = "not_found"
        elif current == CERTIFIED:
            reason = "already_invested"
        elif current != READY:
            reason = "not_ready"
        elif not allowed.get(enrollment_id):
            reason = "out_of_reach"   # their own card, or a minor without a church letter
        if reason is not None:
            skipped.append(SkippedEnrollment(enrollment_id=str(enrollment_id), reason=reason))
            continue
        issued = await portfolio.issue(
            db, actor, enrollment_id, certificate, request,
            audit_extra={"via": VIA_CLUB, "club_id": str(club.id), "bulk_id": bulk_id},
            signatures=signatures,
        )
        invested.append(
            InvestedEnrollment(enrollment_id=str(enrollment_id), certificate_no=issued.certificate_no)
        )
    return InvestOut(invested=invested, skipped=skipped)
