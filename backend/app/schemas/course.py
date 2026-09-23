"""Course schemas: the card, the lessons with their content blocks and the evaluation plan.

The content blocks are a discriminated union validated on the server: a course is read by
minors, so what an instructor may put in a lesson is a closed list (spec §3.3).
"""
import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# The media, video and question rules are shared with the honors (app/schemas/content.py);
# re-exported here so importers of this module keep working.
from app.schemas.content import (  # noqa: F401
    MAX_BANK_PER_REQUIREMENT,
    MAX_SHORT_ANSWER_LENGTH,
    MAX_SHORT_ANSWERS,
    MULTIPLE_CHOICE_OPTIONS,
    SHORT_ANSWER_SEPARATOR,
    VIDEO_ID as _VIDEO_ID,
    GradableQuestion,
    QuestionType,
    blank_to_none as _blank_to_none,
    media_url,
    parse_video,
)
from app.schemas.honor import HonorReviewIn, ReviewOut  # noqa: F401  (shared)
from app.schemas.portfolio import ClubRef, Counters, HonorRef, PersonRef  # noqa: F401  (shared)
from app.services.locales import LOCALE_PATTERN

CourseStatus = Literal["DRAFT", "ZONE_REVIEW", "ASSOCIATION_REVIEW", "PUBLISHED", "ARCHIVED"]
# `EXAM` needs a question bank (I4); a position only reaches it through the bank endpoint.
Assessment = Literal["EXAM", "REVIEW", "EVIDENCE"]
ExamMode = Literal["ONLINE", "IN_PERSON"]

MAX_LESSONS_PER_COURSE = 30
MAX_BLOCKS_PER_LESSON = 40
MAX_LESSON_BYTES = 200 * 1024
GUIDANCE_MAX_LENGTH = 2000

# The whole exam draws at most this many questions (spec §4.1).
MAX_DRAWN_QUESTIONS = 60
# The floor of the vision: an instructor may raise the bar, never lower it.
MIN_PASSING_SCORE = 80
TIME_LIMIT_MINUTES = (5, 180)
MAX_EXAM_ATTEMPTS = 10

# Raw HTML never travels in a lesson: the frontend renders Markdown with HTML turned off,
# and anything that looks like a tag is refused here as well (defence in depth).
_HTML_LIKE = re.compile(r"<\s*[a-zA-Z!/?]")


# ----------------------------------------------------------------------------
# Content blocks
# ----------------------------------------------------------------------------
class _Block(BaseModel):
    # Short and stable so the editor can reorder and the frontend can key on it.
    id: str | None = Field(default=None, pattern=r"^[a-z0-9]{6,12}$")


class TextBlock(_Block):
    type: Literal["text"]
    markdown: str = Field(min_length=1, max_length=20_000)

    @field_validator("markdown")
    @classmethod
    def _no_raw_html(cls, value: str) -> str:
        if _HTML_LIKE.search(value):
            raise ValueError("El texto se escribe en Markdown, sin HTML")
        return value


class ImageBlock(_Block):
    type: Literal["image"]
    url: str
    # Mandatory: a lesson has to be readable with a screen reader (WCAG 2.2 AA).
    alt: str = Field(min_length=1, max_length=300)
    caption: str | None = Field(default=None, max_length=500)

    _clean = field_validator("caption", mode="before")(_blank_to_none)
    _media = field_validator("url")(staticmethod(media_url))


class PdfBlock(_Block):
    type: Literal["pdf"]
    url: str
    name: str = Field(min_length=1, max_length=255)

    _media = field_validator("url")(staticmethod(media_url))


class VideoBlock(_Block):
    type: Literal["video"]
    # The client may send the URL (the editor) or provider + id (a block read back).
    url: str | None = Field(default=None, exclude=True)
    provider: Literal["youtube", "vimeo"] | None = None
    video_id: str | None = None
    title: str = Field(min_length=1, max_length=180)
    caption: str | None = Field(default=None, max_length=500)

    _clean = field_validator("caption", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _only_provider_and_id_are_stored(self):
        if self.url:
            self.provider, self.video_id = parse_video(self.url)
        if not self.provider or not self.video_id:
            raise ValueError("Falta la dirección del vídeo")
        if not _VIDEO_ID[self.provider].match(self.video_id):
            raise ValueError("No se reconoce el vídeo")
        return self


Block = Annotated[TextBlock | ImageBlock | PdfBlock | VideoBlock, Field(discriminator="type")]


# ----------------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------------
class CourseCreate(BaseModel):
    honor_id: uuid.UUID
    locale: str = Field(default="es", pattern=LOCALE_PATTERN, max_length=35)
    title: str = Field(min_length=3, max_length=180)
    summary: str | None = Field(default=None, max_length=600)
    cover_url: str | None = None
    # I4 — left out, they start from the honor's own parameters (services/courses.create).
    exam_passing_score: int | None = Field(default=None, ge=MIN_PASSING_SCORE, le=100)
    exam_time_limit_minutes: int | None = Field(
        default=None, ge=TIME_LIMIT_MINUTES[0], le=TIME_LIMIT_MINUTES[1]
    )
    max_exam_attempts: int | None = Field(default=None, ge=1, le=MAX_EXAM_ATTEMPTS)

    _clean = field_validator("summary", mode="before")(_blank_to_none)

    @field_validator("cover_url")
    @classmethod
    def _cover_in_our_bucket(cls, value: str | None) -> str | None:
        return media_url(value) if value else None


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=180)
    summary: str | None = Field(default=None, max_length=600)
    cover_url: str | None = None
    # I4 — exam parameters. They are content: they only move while the course is a draft.
    exam_passing_score: int | None = Field(default=None, ge=MIN_PASSING_SCORE, le=100)
    exam_time_limit_minutes: int | None = Field(
        default=None, ge=TIME_LIMIT_MINUTES[0], le=TIME_LIMIT_MINUTES[1]
    )
    max_exam_attempts: int | None = Field(default=None, ge=1, le=MAX_EXAM_ATTEMPTS)
    exam_mode: ExamMode | None = None

    _clean = field_validator("summary", mode="before")(_blank_to_none)

    @field_validator("cover_url")
    @classmethod
    def _cover_in_our_bucket(cls, value: str | None) -> str | None:
        return media_url(value) if value else None


# ----------------------------------------------------------------------------
# I4 — the course's question bank
# ----------------------------------------------------------------------------
class CourseQuestionIn(GradableQuestion):
    """One question of the course's own bank. Same shape and same rules as the honor's
    (`app.schemas.content.GradableQuestion`): a bank that cannot be graded is a bank nobody
    can sit."""


class RequirementQuestionsIn(BaseModel):
    """Replaces the whole bank of one requirement and marks it `EXAM`."""

    draw_count: int = Field(ge=1, le=MAX_DRAWN_QUESTIONS)
    question_bank: list[CourseQuestionIn] = Field(
        min_length=1, max_length=MAX_BANK_PER_REQUIREMENT
    )

    @model_validator(mode="after")
    def _bank_covers_the_draw(self):
        if self.draw_count > len(self.question_bank):
            raise ValueError("El banco debe tener al menos tantas preguntas como se sortean")
        return self


class LessonIn(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    requirement_positions: list[int] = Field(default_factory=list, max_length=60)
    blocks: list[Block] = Field(default_factory=list, max_length=MAX_BLOCKS_PER_LESSON)


class LessonOrder(BaseModel):
    lesson_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_LESSONS_PER_COURSE)


class PlanItemIn(BaseModel):
    position: int = Field(ge=1)
    assessment: Assessment
    guidance: str | None = Field(default=None, max_length=GUIDANCE_MAX_LENGTH)

    _clean = field_validator("guidance", mode="before")(_blank_to_none)


class CourseVersionCreate(BaseModel):
    changes_description: str = Field(min_length=3, max_length=2000)
    # To move the course onto a newer version of the honor.
    honor_id: uuid.UUID | None = None


class CourseArchive(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)

    _clean = field_validator("reason", mode="before")(_blank_to_none)


class CourseOperation(BaseModel):
    """The only fields that move on a published course."""

    enrollment_open: bool | None = None
    capacity: int | None = Field(default=None, gt=0)


# ----------------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------------
class AssessmentCounts(BaseModel):
    EXAM: int
    REVIEW: int
    EVIDENCE: int


class CourseCard(BaseModel):
    """What the member sees before joining: no lesson content, no personal data."""

    id: str
    title: str
    summary: str | None
    cover_url: str | None
    locale: str
    status: str
    version: int
    instructor: PersonRef
    honor: HonorRef
    # Which association approved it (D3a: the course is global, the badge says who vouched).
    approved_by: str | None
    lesson_count: int
    assessment_counts: AssessmentCounts
    enrollment_open: bool
    capacity: int | None
    enrolled_count: int
    seats_left: int | None
    instructor_verified: bool
    published_at: datetime | None


class LessonOut(BaseModel):
    id: str
    position: int
    title: str
    requirement_positions: list[int]
    block_count: int
    # Only for the enrolled, the author and the reviewers; empty for everyone else. The
    # blocks travel as stored: they were validated on the way in, and re-validating them on
    # every read would turn a change of media domain into a 500 on old lessons.
    blocks: list[dict]


class PlanItemOut(BaseModel):
    position: int
    assessment: str
    draw_count: int
    guidance: str | None
    # From the honor, so the editor can lock the practical ones on EVIDENCE.
    is_practical: bool
    description: str | None


class CourseDetail(CourseCard):
    lessons: list[LessonOut]
    plan: list[PlanItemOut]
    archived_by_authority: bool
    archive_reason: str | None
    previous_version_id: str | None
    changes_description: str | None
    updated_at: datetime
    # I4 — the exam's rules, which the member must know before starting. Never its questions.
    exam_passing_score: int
    exam_time_limit_minutes: int | None
    max_exam_attempts: int
    exam_mode: str


class CourseQuestionOut(BaseModel):
    """STAFF ONLY. It carries `correct_answer` and `explanation`, so it must never appear
    in a response a member can reach: the member's paper is `PaperQuestionOut` (I5), which
    does not declare these fields at all."""

    id: str
    position: int
    question_text: str
    question_type: str
    options: list[str] | None
    correct_answer: str
    points: int
    explanation: str | None


class RequirementBankOut(BaseModel):
    position: int
    draw_count: int
    questions: list[CourseQuestionOut]


class CourseStaffDetail(CourseDetail):
    """Author, reviewers in scope and MASTER_GC: the only view with the answers."""

    review_history: list[ReviewOut]
    question_banks: list[RequirementBankOut] = Field(default_factory=list)
    # Non-blocking notes for the author and the reviewers (a short bank, automatic issuance).
    warnings: list[str] = Field(default_factory=list)


class PaginatedCourses(BaseModel):
    items: list[CourseCard]
    total: int
    limit: int
    offset: int
    has_more: bool


# ----------------------------------------------------------------------------
# I3 — Enrolment in a course
# ----------------------------------------------------------------------------
class CourseMemberRemove(BaseModel):
    """Removing a member is an act the member reads afterwards: the reason is mandatory."""

    reason: str = Field(min_length=3, max_length=2000)

    _clean = field_validator("reason", mode="before")(_blank_to_none)


class JoinedCourse(CourseCard):
    enrollment_id: str
    enrollment_status: str


class CourseMember(BaseModel):
    """What the instructor sees of each member: progress in THEIR course and nothing more.
    No e-mail, no birth date, no way to reach them outside the platform (spec §6)."""

    enrollment_id: str
    member: PersonRef
    club: ClubRef | None
    status: str
    counters: Counters
    joined_at: datetime | None
    updated_at: datetime
