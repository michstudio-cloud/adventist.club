"""«Pedir ser director de este club» (sign-up) and the administration that decides it,
plus «Actividad» (the admin's read-only log). Thin: `services/director_requests.py` and
`services/admin_activity.py` do the work.

  GET  /api/v1/director-requests/clubs?association_id=&q=   public, rate-limited: the
       association's active clubs (name, church, city; never a person)
  GET  /api/v1/director-requests                            pending requests in my scope
  POST /api/v1/director-requests/{id}/approve               «Aprobar»
  POST /api/v1/director-requests/{id}/reject                «Rechazar»
  GET  /api/v1/admin/activity                               «Actividad»
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit import limiter
from app.services import admin_activity
from app.services import director_requests as service
from app.services import org_invitations

router = APIRouter(prefix="/api/v1/director-requests", tags=["director-requests"])
activity_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ExistingClubOut(BaseModel):
    id: str
    name: str
    church: str | None = None
    city: str | None = None
    has_director: bool
    request_pending: bool
    # The club is in an open event: the form asks through the event («Mi club ya está en el evento»).
    event_registration_id: str | None = None
    event_name: str | None = None


class Ref(BaseModel):
    id: str
    name: str


class DirectorRequestOut(BaseModel):
    id: str
    club: Ref
    association_name: str | None = None
    church: str | None = None
    city: str | None = None
    name: str | None = None
    email: str
    has_account: bool
    verified: bool
    source: str
    event: Ref | None = None
    created_at: datetime
    expires_at: datetime


class DecisionOut(BaseModel):
    # approved: the person is the director now; invite: the invitation was e-mailed to them.
    action: str
    club: Ref
    email: str


class ActivityActor(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None


class ActivityNode(BaseModel):
    id: str
    name: str
    type: str


class ActivityItem(BaseModel):
    id: str
    action: str
    created_at: datetime
    actor: ActivityActor | None = None
    organization: ActivityNode | None = None
    via: str | None = None
    role: str | None = None
    reason: str | None = None


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    has_more: bool


@router.get("/clubs", response_model=list[ExistingClubOut])
@limiter.limit("60/minute")
async def search_clubs(request: Request, association_id: uuid.UUID, q: str = Query(default="", max_length=80),
                       db: AsyncSession = Depends(get_db)):
    """Sign-up form, «Director(a) de club», once the association is chosen (no session)."""
    return [ExistingClubOut(**row) for row in await service.search_clubs(db, association_id, q)]


@router.get("", response_model=list[DirectorRequestOut])
async def list_requests(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                        current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Pending director requests of the clubs I may decide on (sign-up and events), oldest first."""
    if not await admin_activity.may_read(db, current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, service.FORBIDDEN)
    return [DirectorRequestOut(**row) for row in await service.pending(db, current_user, limit=limit, offset=offset)]


@router.post("/{invitation_id}/approve", response_model=DecisionOut)
async def approve(invitation_id: uuid.UUID, request: Request, background: BackgroundTasks,
                  current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """A VERIFIED account is the director now; otherwise the invitation goes to that address."""
    club, invitation, token = await service.approve(db, current_user, invitation_id, request)
    log = org_invitations.stage_email(db, invitation) if token else None
    await db.commit()
    if token:
        org_invitations.queue_email(background, log, invitation, token, club, current_user)
    return DecisionOut(action="invite" if token else "approved", club=Ref(id=str(club.id), name=club.name),
                       email=invitation.email)


@router.post("/{invitation_id}/reject", response_model=DecisionOut)
async def reject(invitation_id: uuid.UUID, request: Request, current_user: User = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    """The request is revoked; the account stays."""
    club, invitation = await service.reject(db, current_user, invitation_id, request)
    await db.commit()
    return DecisionOut(action="rejected", club=Ref(id=str(club.id), name=club.name), email=invitation.email)


@activity_router.get("/activity", response_model=ActivityPage)
async def activity(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   action: str | None = Query(None, pattern=r"^[A-Z_]{2,40}$"),
                   current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """«Actividad»: latest account, club and director events in my scope, newest first."""
    if not await admin_activity.may_read(db, current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sólo la administración de asociación o superior.")
    if action is not None and action not in admin_activity.ACTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Acción desconocida.")
    return await admin_activity.latest(db, current_user, limit=limit, offset=offset, action=action)
