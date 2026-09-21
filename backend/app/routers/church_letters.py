"""Bloque B · I1 — The church letter that verifies a virtual instructor.

Thin on purpose: validate, call app/services/church_letters.py, serialise. Rules, state
transitions, scope and audit rows all live in the service.

Literal routes are declared before the `/{letter_id}` routes.
"""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import User
from app.schemas.church_letter import (
    LetterCreate,
    LetterOut,
    LetterQueueItem,
    LetterReviewIn,
    LetterUpload,
    QueueStatus,
    VerificationChecklist,
)
from app.schemas.portfolio import SignedUrl
from app.services import church_letters
from app.workflow import ZONE_REVIEWERS

router = APIRouter(prefix="/api/v1/church-letters", tags=["church-letters"])


@router.get("/me", response_model=VerificationChecklist)
async def my_verification(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """What is still missing to teach: e-mail, child protection course and the letter."""
    return await church_letters.checklist(db, current_user)


@router.get("/queue", response_model=list[LetterQueueItem])
async def validation_queue(
    status_filter: QueueStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles(*ZONE_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    """Letters waiting for the caller's step: the zone validates, the association authorizes."""
    return await church_letters.queue(db, current_user, status_filter, limit, offset)


@router.post("", response_model=LetterUpload, status_code=status.HTTP_201_CREATED)
async def present_letter(
    payload: LetterCreate,
    request: Request,
    current_user: User = Depends(require_roles(*church_letters.LETTER_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Step 1 of 3. Step 2: PUT the file to `upload.url` with `upload.headers`. Step 3: `/complete`.

    E7 widened this from the virtual instructor of Bloque B to every office of a
    club: director, instructor and counselor need the letter to handle minors,
    and the secretary may present one to lead a unit.
    """
    return await church_letters.create(db, current_user, payload, request)


@router.post("/{letter_id}/complete", response_model=LetterOut)
async def complete_letter(
    letter_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await church_letters.complete(db, current_user, letter_id, request, background)


@router.get("/{letter_id}", response_model=LetterQueueItem)
async def one_letter(
    letter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owner or reviewer in scope; 404 for everybody else. Metadata only — the document
    itself is still reached through `/{id}/url`."""
    return await church_letters.get_one(db, current_user, letter_id)


@router.get("/{letter_id}/url", response_model=SignedUrl)
async def letter_url(
    letter_id: uuid.UUID,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"  # a signed URL is a credential
    return await church_letters.signed_url(db, current_user, letter_id)


@router.post("/{letter_id}/review", response_model=LetterOut)
async def review_letter(
    letter_id: uuid.UUID,
    payload: LetterReviewIn,
    request: Request,
    background: BackgroundTasks,
    current_user: User = Depends(require_roles(*ZONE_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    """VALIDATE (zone) · AUTHORIZE (association) · REJECT · REVOKE. Nobody decides on their own."""
    return await church_letters.review(db, current_user, letter_id, payload, request, background)
