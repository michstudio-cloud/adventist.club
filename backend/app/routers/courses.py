"""Bloque B · I2 — Courses: authoring, review and publication.

Thin on purpose: validate, call app/services/courses.py, serialise. Rules, state
transitions, scope and audit rows all live in the service.

Literal routes are declared before the `/{course_id}` ones, and `/lessons/order`
before `/lessons/{lesson_id}`.
"""
import uuid

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, get_optional_user, require_roles
from app.models import User
from app.schemas.course import (
    CourseArchive,
    CourseCard,
    CourseCreate,
    CourseDetail,
    CourseMember,
    CourseMemberRemove,
    CourseOperation,
    CourseStaffDetail,
    CourseStatus,
    CourseUpdate,
    CourseVersionCreate,
    JoinedCourse,
    LessonIn,
    LessonOrder,
    LessonOut,
    PaginatedCourses,
    PlanItemIn,
    RequirementQuestionsIn,
)
from app.schemas.honor import HonorReviewIn
from app.schemas.portfolio import EnrollmentDetail
from app.security import INSTRUCTOR
from app.services import course_enrollment, courses
from app.services.locales import LOCALE_PATTERN
from app.workflow import ZONE_REVIEWERS

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


# ----------------------------------------------------------------------------
# Discovery and lists
# ----------------------------------------------------------------------------
@router.get("", response_model=list[CourseCard])
async def discover_courses(
    honor_id: uuid.UUID | None = None,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Public: published courses, open for enrolment and with a verified instructor."""
    return await courses.discover(db, honor_id, locale, limit, offset)


@router.get("/my/created", response_model=PaginatedCourses)
async def my_courses(
    status_filter: CourseStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles(INSTRUCTOR)),
    db: AsyncSession = Depends(get_db),
):
    return await courses.my_created(db, current_user, status_filter, limit, offset)


@router.get("/pending/reviews", response_model=list[CourseCard])
async def pending_reviews(
    current_user: User = Depends(require_roles(*ZONE_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    """Courses waiting for the caller's review level, inside the caller's subtree."""
    return await courses.pending_reviews(db, current_user, limit=200)


@router.get("/my/joined", response_model=list[JoinedCourse])
async def my_joined_courses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The courses I am studying in, with the enrollment each one belongs to."""
    return await course_enrollment.my_joined(db, current_user)


# ----------------------------------------------------------------------------
# Authoring
# ----------------------------------------------------------------------------
@router.post("", response_model=CourseStaffDetail, status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: CourseCreate,
    request: Request,
    current_user: User = Depends(require_roles(INSTRUCTOR)),
    db: AsyncSession = Depends(get_db),
):
    """201 DRAFT with the plan preloaded from the honor. A draft needs no verified letter."""
    return await courses.create(db, current_user, payload, request)


@router.get("/{course_id}", response_model=CourseDetail)
async def get_course(
    course_id: uuid.UUID,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Public page of a published course: lessons, plan and seats. The lesson content is
    for the author, the reviewers in scope and (from I3) the enrolled member."""
    return await courses.get_detail(db, current_user, course_id)


@router.get("/{course_id}/instructor", response_model=CourseStaffDetail)
async def get_course_for_staff(
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Everything: blocks, plan and review history. Author, reviewers in scope, MASTER_GC."""
    return await courses.get_detail(db, current_user, course_id, staff_view=True)


@router.put("/{course_id}", response_model=CourseStaffDetail)
async def update_course(
    course_id: uuid.UUID,
    payload: CourseUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await courses.update(db, current_user, course_id, payload, request)


@router.put("/{course_id}/lessons/order", response_model=CourseStaffDetail)
async def reorder_lessons(
    course_id: uuid.UUID,
    payload: LessonOrder,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await courses.reorder_lessons(db, current_user, course_id, payload, request)


@router.post("/{course_id}/lessons", response_model=LessonOut, status_code=status.HTTP_201_CREATED)
async def add_lesson(
    course_id: uuid.UUID,
    payload: LessonIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await courses.add_lesson(db, current_user, course_id, payload, request)


@router.put("/{course_id}/lessons/{lesson_id}", response_model=LessonOut)
async def update_lesson(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    payload: LessonIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await courses.update_lesson(db, current_user, course_id, lesson_id, payload, request)


@router.delete("/{course_id}/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await courses.delete_lesson(db, current_user, course_id, lesson_id, request)


@router.put("/{course_id}/plan", response_model=CourseStaffDetail)
async def set_plan(
    course_id: uuid.UUID,
    payload: list[PlanItemIn],
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The whole plan at once: one entry per requirement, never more lenient than the honor."""
    return await courses.set_plan(db, current_user, course_id, payload, request)


@router.put("/{course_id}/requirements/{position}/questions", response_model=CourseStaffDetail)
async def set_requirement_questions(
    course_id: uuid.UUID,
    position: int,
    payload: RequirementQuestionsIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the bank of one requirement and mark it `EXAM`. 409 if the honor marks that
    requirement practical: a test never replaces evidence."""
    return await courses.set_questions(db, current_user, course_id, position, payload, request)


@router.post("/{course_id}/import-honor-bank", response_model=CourseStaffDetail)
async def import_honor_bank(
    course_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Copy the bank of the honor into the course, only for an honor the instructor wrote."""
    return await courses.import_honor_bank(db, current_user, course_id, request)


# ----------------------------------------------------------------------------
# Review workflow
# ----------------------------------------------------------------------------
@router.post("/{course_id}/submit", response_model=CourseStaffDetail)
async def submit_course(
    course_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """DRAFT -> ZONE_REVIEW. 400 lists what is still missing; 403 without the letter."""
    return await courses.submit(db, current_user, course_id, request)


@router.post("/{course_id}/review", response_model=CourseStaffDetail)
async def review_course(
    course_id: uuid.UUID,
    payload: HonorReviewIn,
    request: Request,
    current_user: User = Depends(require_roles(*ZONE_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    return await courses.review(db, current_user, course_id, payload, request)


@router.post(
    "/{course_id}/version", response_model=CourseStaffDetail, status_code=status.HTTP_201_CREATED
)
async def create_version(
    course_id: uuid.UUID,
    payload: CourseVersionCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A new DRAFT copy; the published course stays live until this one is published."""
    return await courses.create_version(db, current_user, course_id, payload, request)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_course(
    course_id: uuid.UUID,
    request: Request,
    payload: CourseArchive | None = Body(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The author retires it; a reviewer in scope withdraws it, always with a reason."""
    await courses.archive(db, current_user, course_id, payload, request)


@router.patch("/{course_id}/operation", response_model=CourseStaffDetail)
async def set_operation(
    course_id: uuid.UUID,
    payload: CourseOperation,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Seats and open/closed enrolment on a published course: no content, no new review."""
    return await courses.set_operation(db, current_user, course_id, payload, request)


# ----------------------------------------------------------------------------
# I3 — Enrolment: joining, leaving and the roster
# ----------------------------------------------------------------------------
@router.post("/{course_id}/join", response_model=EnrollmentDetail)
async def join_course(
    course_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take this honor inside this course. Idempotent; 409 when the course is full or
    closed, 403 for a minor without a guardian's consent."""
    return await course_enrollment.join(db, current_user, course_id, request)


@router.post("/{course_id}/leave", response_model=EnrollmentDetail)
async def leave_course(
    course_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Back to club mode. Nothing is lost: every verdict already given stays."""
    return await course_enrollment.leave(db, current_user, course_id, request)


@router.get("/{course_id}/members", response_model=list[CourseMember])
async def course_members(
    course_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Author and MASTER_GC: name, club and progress. No contact details of any kind."""
    return await course_enrollment.members(db, current_user, course_id, limit, offset)


@router.delete("/{course_id}/members/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_course_member(
    course_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    payload: CourseMemberRemove,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Same effect as leaving, plus the reason the member then reads."""
    await course_enrollment.remove_member(
        db, current_user, course_id, enrollment_id, payload, request
    )
