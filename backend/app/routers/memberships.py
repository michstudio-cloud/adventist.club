"""What a person does about their own membership (Bloque E).

Thin: validate, call `app/services/memberships.py`, serialize. Everything a
club does TO its members lives in `app/routers/clubs.py` instead.
"""
from fastapi import APIRouter, Depends, Request

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import Organization, User
from app.schemas.membership import MembershipEnded, MyMembership, as_membership_out
from app.services import memberships as membership_service

router = APIRouter(prefix="/api/v1/memberships", tags=["memberships"])


@router.get("/me", response_model=MyMembership)
async def my_membership(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """My club today, plus everything of mine still waiting for a decision."""
    active = await membership_service.active_membership(db, current_user.id)
    pending = await membership_service.open_memberships(db, current_user.id)

    async def _out(membership):
        club = await db.get(Organization, membership.club_id)
        return as_membership_out(membership, club)

    return MyMembership(
        active=await _out(active) if active is not None else None,
        pending=[await _out(row) for row in pending],
    )


@router.delete("/me", response_model=MembershipEnded)
async def leave_club(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Leave the club. A minor may do it alone; the only director may not
    (the association appoints the replacement first)."""
    membership = await membership_service.leave(db, current_user, request)
    await db.commit()
    return MembershipEnded(
        membership_id=str(membership.id),
        club_id=str(membership.club_id),
        status=membership.status,
        end_reason=membership.end_reason,
    )
