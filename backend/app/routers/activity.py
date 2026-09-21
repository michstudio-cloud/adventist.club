"""Bloque F · F2 — Actividades: horas de servicio y asistencia a reuniones.

Thin: validate, call app/services/activity.py, serialise. Everything requires a session —
where a minor was on a Saturday is never public.
"""
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas.activity import ActivityCreate, ActivityDecision, ActivityListOut, ActivityOut
from app.schemas.program import ActivityCategory
from app.services import activity

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])


@router.post("/logs", response_model=list[ActivityOut], status_code=status.HTTP_201_CREATED)
async def create_logs(
    payload: ActivityCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mine (SUBMITTED), or the club's outing for several members at once (APPROVED)."""
    return await activity.create_logs(db, current_user, payload, request)


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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """«Horas por aprobar» of the club: what this person may decide right now."""
    return await activity.queue(db, current_user, limit, offset)


@router.post("/logs/{log_id}/decision", response_model=ActivityOut)
async def decide(
    log_id: uuid.UUID,
    payload: ActivityDecision,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approving or rejecting moves the sum, so it re-evaluates the member's `HOURS`
    requirements in the same transaction."""
    return await activity.decide(db, current_user, log_id, payload, request)


@router.delete("/logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log(
    log_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await activity.delete_log(db, current_user, log_id, request)
