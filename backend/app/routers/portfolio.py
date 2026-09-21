"""Portfolio (Bloque A): enrollment, progress per requirement, private evidence, review and
the certificate linked to the account. Everything requires a session.

Thin on purpose: validate, call app/services/portfolio.py, serialise. Rules, state
transitions, permissions and audit rows all live in the service.
"""
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas.portfolio import (
    CertificateIssue,
    CertificateOut,
    EnrollmentCreate,
    EnrollmentDetail,
    EnrollmentStatus,
    EnrollmentSummary,
    EvidenceCreate,
    EvidenceOut,
    EvidenceUpload,
    PortfolioOut,
    QueueReady,
    QueueRequirement,
    QueueStatus,
    RequirementUpdate,
    ReviewIn,
    SignedUrl,
)
from app.services import portfolio

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])


# ----------------------------------------------------------------------------
# Enrollments
# ----------------------------------------------------------------------------
@router.post("/enrollments", response_model=EnrollmentDetail, status_code=status.HTTP_201_CREATED)
async def enroll(
    payload: EnrollmentCreate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start an honor. Idempotent: 201 the first time, 200 with the same enrollment afterwards."""
    detail, created = await portfolio.enroll(db, current_user, payload, request)
    if not created:
        response.status_code = status.HTTP_200_OK
    return detail


@router.get("/enrollments", response_model=list[EnrollmentSummary])
async def my_enrollments(
    status_filter: EnrollmentStatus | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mine, with counters. Withdrawn ones only when asked for (`?status=WITHDRAWN`)."""
    return await portfolio.list_enrollments(db, current_user, status_filter)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentDetail)
async def get_enrollment(
    enrollment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio.get_detail(db, current_user, enrollment_id)


@router.delete("/enrollments/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw(
    enrollment_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await portfolio.withdraw(db, current_user, enrollment_id, request)


@router.put("/enrollments/{enrollment_id}/requirements/{position}", response_model=EnrollmentDetail)
async def update_requirement(
    enrollment_id: uuid.UUID,
    position: int,
    payload: RequirementUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio.update_requirement(db, current_user, enrollment_id, position, payload, request)


# ----------------------------------------------------------------------------
# Evidence: the file goes browser -> private bucket, never through this API
# ----------------------------------------------------------------------------
@router.post(
    "/enrollments/{enrollment_id}/requirements/{position}/evidences",
    response_model=EvidenceUpload,
    status_code=status.HTTP_201_CREATED,
)
async def add_evidence(
    enrollment_id: uuid.UUID,
    position: int,
    payload: EvidenceCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Step 1 of 3. Step 2: PUT the file to `upload.url` with `upload.headers`. Step 3: `/complete`."""
    return await portfolio.add_evidence(db, current_user, enrollment_id, position, payload, request)


@router.post("/evidences/{evidence_id}/complete", response_model=EvidenceOut)
async def complete_evidence(
    evidence_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio.complete_evidence(db, current_user, evidence_id, request)


@router.get("/evidences/{evidence_id}/url", response_model=SignedUrl)
async def evidence_url(
    evidence_id: uuid.UUID,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"  # a signed URL is a credential
    return await portfolio.evidence_url(db, current_user, evidence_id)


@router.delete("/evidences/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_evidence(
    evidence_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await portfolio.remove_evidence(db, current_user, evidence_id, request)


# ----------------------------------------------------------------------------
# Review and certificate
# ----------------------------------------------------------------------------
@router.get("/review/queue", response_model=list[QueueRequirement] | list[QueueReady])
async def review_queue(
    queue_status: QueueStatus = Query("SUBMITTED", alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SUBMITTED: requirements waiting for a verdict. READY: enrollments waiting for a certificate."""
    return await portfolio.review_queue(db, current_user, queue_status, limit, offset)


@router.post("/enrollments/{enrollment_id}/requirements/{position}/review", response_model=EnrollmentDetail)
async def review_requirement(
    enrollment_id: uuid.UUID,
    position: int,
    payload: ReviewIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio.review_requirement(db, current_user, enrollment_id, position, payload, request)


@router.post(
    "/enrollments/{enrollment_id}/certificate",
    response_model=CertificateOut,
    status_code=status.HTTP_201_CREATED,
)
async def issue_certificate(
    enrollment_id: uuid.UUID,
    payload: CertificateIssue,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio.issue(db, current_user, enrollment_id, payload, request)


# ----------------------------------------------------------------------------
# A person's portfolio
# ----------------------------------------------------------------------------
@router.get("/me", response_model=PortfolioOut)
async def my_portfolio(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await portfolio.portfolio_of(db, current_user, current_user.id)


@router.get("/users/{user_id}", response_model=PortfolioOut)
async def user_portfolio(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await portfolio.portfolio_of(db, current_user, user_id)
