"""Course schemas: the card, the lessons with their content blocks and the evaluation plan.

The content blocks are a discriminated union validated on the server: a course is read by
minors, so what an instructor may put in a lesson is a closed list (spec §3.3).
"""
import re
import urllib.parse
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import settings
from app.schemas.honor import HonorReviewIn, ReviewOut  # noqa: F401  (same review contract)
from app.schemas.portfolio import HonorRef, PersonRef  # noqa: F401  (shared shapes)
from app.services.locales import LOCALE_PATTERN

CourseStatus = Literal["DRAFT", "ZONE_REVIEW", "ASSOCIATION_REVIEW", "PUBLISHED", "ARCHIVED"]
# I4 opens `EXAM`, which needs a question bank; until then a course evaluates with people.
Assessment = Literal["REVIEW", "EVIDENCE"]

MAX_LESSONS_PER_COURSE = 30
MAX_BLOCKS_PER_LESSON = 40
MAX_LESSON_BYTES = 200 * 1024
GUIDANCE_MAX_LENGTH = 2000

# Raw HTML never travels in a lesson: the frontend renders Markdown with HTML turned off,
# and anything that looks like a tag is refused here as well (defence in depth).
_HTML_LIKE = re.compile(r"<\s*[a-zA-Z!/?]")
_VIDEO_ID = {
    "youtube": re.compile(r"^[A-Za-z0-9_-]{6,20}$"),
    "vimeo": re.compile(r"^[0-9]{6,12}$"),
}
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


def media_url(value: str) -> str:
    """Only the platform's own media bucket: an `<img>` from anywhere else is a beacon
    that tells a third party which minor opened the lesson, and can change under us."""
    base = settings.R2_PUBLIC_URL.rstrip("/")
    if not value.startswith(f"{base}/") or len(value) > 600:
        raise ValueError(f"El archivo debe estar alojado en {base}")
    return value


def parse_video(url: str) -> tuple[str, str]:
    """(provider, video_id) from a YouTube or Vimeo URL. Only these two, and only the id is
    stored: the frontend embeds it with youtube-nocookie.com / player.vimeo.com."""
    parsed = urllib.parse.urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host in _YOUTUBE_HOSTS:
        if host.endswith("youtu.be"):
            video_id = parsed.path.lstrip("/")
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path[len("/embed/"):]
        else:
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        provider = "youtube"
    elif host in _VIMEO_HOSTS:
        video_id, provider = parsed.path.rstrip("/").rsplit("/", 1)[-1], "vimeo"
    else:
        raise ValueError("Solo se admiten vídeos de YouTube o Vimeo")
    video_id = video_id.split("?")[0].split("&")[0].strip()
    if not _VIDEO_ID[provider].match(video_id):
        raise ValueError("No se reconoce el vídeo en esa dirección")
    return provider, video_id


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

    _clean = field_validator("summary", mode="before")(_blank_to_none)

    @field_validator("cover_url")
    @classmethod
    def _cover_in_our_bucket(cls, value: str | None) -> str | None:
        return media_url(value) if value else None


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=180)
    summary: str | None = Field(default=None, max_length=600)
    cover_url: str | None = None

    _clean = field_validator("summary", mode="before")(_blank_to_none)

    @field_validator("cover_url")
    @classmethod
    def _cover_in_our_bucket(cls, value: str | None) -> str | None:
        return media_url(value) if value else None


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


class CourseStaffDetail(CourseDetail):
    """Author, reviewers in scope and MASTER_GC. The question bank arrives with I4."""

    review_history: list[ReviewOut]


class PaginatedCourses(BaseModel):
    items: list[CourseCard]
    total: int
    limit: int
    offset: int
    has_more: bool
