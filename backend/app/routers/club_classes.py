"""Bloque F · F3 — el club sigue una CLASE en grupo (`/api/v1/clubs/{club_id}/classes`).

Thin, like every router here: the permission in `app/rbac.py`, the work in
`app/services/club_classes.py`, which reuses block A's enrollment, verdict and certificate.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rbac import can_view_roster
from app.schemas.club_classes import (
    BlockSignIn,
    BlockSignOut,
    ClassEnrollIn,
    ClassEnrollOut,
    ClassMatrix,
    ClubClasses,
    InvestIn,
    InvestOut,
)
from app.security import utcnow
from app.services import club_classes, notifications
from app.services import memberships as membership_service

router = APIRouter(prefix="/api/v1/clubs", tags=["club-classes"])

NOT_ROSTER_DETAIL = "No tienes permiso para ver la nómina de este club"


async def _club_for_reader(db: AsyncSession, actor: User, club_id: uuid.UUID):
    club = await membership_service.get_club(db, club_id)
    if not await can_view_roster(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, NOT_ROSTER_DETAIL)
    return club


@router.get("/{club_id}/classes", response_model=ClubClasses)
async def list_classes(
    club_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The classes the club follows, with their counters, and the published ones it could."""
    club = await _club_for_reader(db, current_user, club_id)
    return await club_classes.list_classes(db, current_user, club)


@router.post("/{club_id}/classes/{program_id}/enroll", response_model=ClassEnrollOut)
async def enroll(
    club_id: uuid.UUID,
    program_id: uuid.UUID,
    payload: ClassEnrollIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enrol the club (every active student), a unit or a list. Idempotent."""
    club = await membership_service.get_club(db, club_id)
    return await club_classes.enroll(db, current_user, club, program_id, payload, request)


@router.get("/{club_id}/classes/{program_id}/matrix", response_model=ClassMatrix)
async def matrix(
    club_id: uuid.UUID,
    program_id: uuid.UUID,
    unit_id: uuid.UUID | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Members × requirements, for the signing screen. A counselor reads their unit only."""
    club = await _club_for_reader(db, current_user, club_id)
    return await club_classes.matrix(db, current_user, club, program_id, unit_id)


@router.post("/{club_id}/classes/{program_id}/sign", response_model=BlockSignOut)
async def block_sign(
    club_id: uuid.UUID,
    program_id: uuid.UUID,
    payload: BlockSignIn,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """«Firma en bloque»: each cell to COMPLETE; what the caller cannot sign is skipped.

    «Avisos», after the signatures' own commit: each member finds what was signed in their
    inbox, and a card that became READY gets E9's «lista» notice and e-mail."""
    club = await membership_service.get_club(db, club_id)
    since = utcnow()
    result = await club_classes.block_sign(db, current_user, club, program_id, payload, request)
    if result.signed:
        skipped = {(cell.enrollment_id, cell.requirement_id) for cell in result.skipped}
        signed: dict[uuid.UUID, int] = {}
        for enrollment_id, requirement_id in dict.fromkeys(
            (entry.enrollment_id, entry.requirement_id) for entry in payload.entries
        ):
            if (str(enrollment_id), str(requirement_id)) not in skipped:
                signed[enrollment_id] = signed.get(enrollment_id, 0) + 1
        await notifications.queue_block_signed(
            db, background, signed_by_enrollment=signed, since=since
        )
        await db.commit()
    return result


@router.post("/{club_id}/classes/{program_id}/invest", response_model=InvestOut)
async def invest(
    club_id: uuid.UUID,
    program_id: uuid.UUID,
    payload: InvestIn,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The investiture of every READY enrollment listed. The director's act.

    Each invested member — and the guardians of a minor — hear about it exactly as a single
    certificate of the portfolio (E9 `PROGRESS_CERTIFIED`), after the certificates commit."""
    club = await membership_service.get_club(db, club_id)
    result = await club_classes.invest(db, current_user, club, program_id, payload, request)
    for invested in result.invested:
        await notifications.queue_certificate_issued(
            db, background, enrollment_id=uuid.UUID(invested.enrollment_id)
        )
    if result.invested:
        await db.commit()
    return result
