"""Bloque F · F2 — Actividades: horas de servicio y asistencia a reuniones.

Thin: validate, call app/services/activity.py, serialise. Everything requires a session —
where a minor was on a Saturday is never public.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas.activity import ActivityCreate, ActivityDecision, ActivityListOut, ActivityOut
from app.schemas.program import ActivityCategory
from app.security import utcnow
from app.services import activity, notifications

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


@router.post("/logs", response_model=list[ActivityOut], status_code=status.HTTP_201_CREATED)
async def create_logs(
    payload: ActivityCreate,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mine (SUBMITTED), or the club's outing for several members at once (APPROVED)."""
    since = utcnow()
    created = await activity.create_logs(db, current_user, payload, request)
    await _notify_approved(db, background, created, since)
    return created


@router.get("/logs", response_model=ActivityListOut)
async def list_logs(
    user_id: uuid.UUID | None = None,
    category: ActivityCategory | None = None,
    status_filter: str | None = Query(None, alias="status", pattern="^(SUBMITTED|APPROVED|REJECTED)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mine by default; somebody else's with `can_view_portfolio`, and never wider."""
    return await activity.list_logs(db, current_user, user_id, category, status_filter)


@router.get("/queue", response_model=list[ActivityOut])
async def queue(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    club_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """«Horas por aprobar» of the club: what this person may decide right now."""
    return await activity.queue(db, current_user, limit, offset, club_id)


@router.post("/logs/{log_id}/decision", response_model=ActivityOut)
async def decide(
    log_id: uuid.UUID,
    payload: ActivityDecision,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approving or rejecting moves the sum, so it re-evaluates the member's `HOURS`
    requirements in the same transaction."""
    since = utcnow()
    decided = await activity.decide(db, current_user, log_id, payload, request)
    await _notify_approved(db, background, [decided], since)
    return decided


async def _notify_approved(db: AsyncSession, background, logs, since) -> None:
    """«Avisos», after the logs' own commit: the members whose hours were approved (in the
    inbox; by e-mail at most once per member every 12 hours), and E9's «lista» notice for
    any card a `HOURS` requirement just finished."""
    approved = [log for log in logs if log.status == "APPROVED"]
    if not approved:
        return
    await notifications.queue_hours_approved(
        db, background, log_ids=[uuid.UUID(log.id) for log in approved]
    )
    await notifications.queue_ready_since(
        db, background, user_ids=[uuid.UUID(log.user.id) for log in approved], since=since
    )
    await db.commit()


@router.delete("/logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log(
    log_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await activity.delete_log(db, current_user, log_id, request)
