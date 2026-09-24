"""«Avisos» — the in-app inbox of the person with the session (`/api/v1/notifications`).

Only ever one's own: every query is scoped to `current_user.id`, and somebody else's
notice is a 404 (never a 403), so an id reveals nothing. The rows are written by
`app/services/notifications.py`, in the transaction of the event that caused them.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Notification, User
from app.schemas.notification import NotificationOut, ReadAllOut, UnreadCount
from app.security import utcnow

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

# The bell never counts past this: «99+» is all a badge needs to say.
UNREAD_COUNT_CAP = 100


def _out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(row.id),
        kind=row.kind,
        title=row.title,
        body=row.body,
        link=row.link,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        count=row.count,
        data=row.data or {},
        read_at=row.read_at,
        created_at=row.created_at,
    )


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    response: Response,
    unread: bool = Query(False, description="Only the ones not read yet"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mine, newest first. The total of the filter goes in `X-Total-Count`."""
    conditions = [Notification.user_id == current_user.id]
    if unread:
        conditions.append(Notification.read_at.is_(None))
    total = await db.scalar(select(func.count()).select_from(Notification).where(*conditions))
    response.headers["X-Total-Count"] = str(total or 0)
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await db.execute(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [_out(row) for row in rows]


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The number on the bell (at most 100: a badge says «99+» anyway)."""
    capped = (
        select(Notification.id)
        .where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .limit(UNREAD_COUNT_CAP)
        .subquery()
    )
    count = await db.scalar(select(func.count()).select_from(capped))
    response.headers["Cache-Control"] = "no-store"
    return UnreadCount(count=int(count or 0))


@router.post("/read-all", response_model=ReadAllOut)
async def read_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await db.commit()
    return ReadAllOut(updated=result.rowcount or 0)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def read_one(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Idempotent: a notice already read keeps the moment it was first read."""
    row = await db.get(Notification, notification_id)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aviso no encontrado")
    if row.read_at is None:
        row.read_at = utcnow()
        await db.commit()
    return _out(row)
