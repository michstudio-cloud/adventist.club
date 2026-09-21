"""Bloque C · I5 — Exam attempts: start, answer, resume, hand in.

Thin on purpose: validate, call app/services/exams.py, serialise. The draw, the clock, the
grading and every permission live in the service.

`response_model` is left off the endpoints that answer with different shapes for different
readers (the paper, the result without solutions, the result with them, or just status and
score): the service picks the schema, and a schema that does not declare `correct_answer`
cannot leak it.

Manual grading, voiding an attempt and the in-person session code arrive with I6.
"""
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit import limiter
from app.schemas.exam import AnswerIn, AnswerSaved, AttemptStart, ExamStateOut, ExtraTimeIn
from app.services import exams

router = APIRouter(prefix="/api/v1/exams", tags=["exams"])


@router.get("/enrollments/{enrollment_id}", response_model=ExamStateOut)
async def exam_state(
    enrollment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The exam card: rules, attempts used and left, what is still pending, last result."""
    return await exams.exam_state(db, current_user, enrollment_id)


@router.post("/enrollments/{enrollment_id}/attempts", response_model=None)
@limiter.limit("10/minute")
async def start_attempt(
    enrollment_id: uuid.UUID,
    payload: AttemptStart,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Draw and open an attempt. Idempotent while one is open; 409 when none is possible."""
    return await exams.start_attempt(db, current_user, enrollment_id, payload, request)


@router.put("/enrollments/{enrollment_id}/extra-time", response_model=ExamStateOut)
async def set_extra_time(
    enrollment_id: uuid.UUID,
    payload: ExtraTimeIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accessibility (WCAG 2.2.1): the adult member, the guardian or the course instructor."""
    return await exams.set_extra_time(db, current_user, enrollment_id, payload, request)


@router.get("/attempts/{attempt_id}", response_model=None)
async def get_attempt(
    attempt_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """In progress: the paper, and only for its owner. Finished: the result per §4.5."""
    return await exams.get_attempt(db, current_user, attempt_id)


@router.put("/attempts/{attempt_id}/answers/{position}", response_model=AnswerSaved)
@limiter.limit("120/minute")
async def save_answer(
    attempt_id: uuid.UUID,
    position: int,
    payload: AnswerIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Autosave of one answer. 409 once the attempt is closed or the deadline has passed."""
    return await exams.save_answer(db, current_user, attempt_id, position, payload, request)


@router.post("/attempts/{attempt_id}/submit", response_model=None)
async def submit_attempt(
    attempt_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hand it in; idempotent once handed in."""
    return await exams.submit_attempt(db, current_user, attempt_id, request)
