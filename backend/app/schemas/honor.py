"""Honor (legacy "specialty") schemas."""
import uuid
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.schemas.content import (  # noqa: F401  (QuestionType is re-exported)
    MAX_BANK_PER_REQUIREMENT,
    GradableQuestion,
    QuestionType,
    canonical_video_url,
    http_url,
    media_url,
)

DifficultyLevel = Literal["BEGINNER", "INTERMEDIATE", "ADVANCED"]
HonorType = Literal["OFFICIAL_GC", "DIVISIONAL", "LOCAL"]
HonorStatus = Literal["DRAFT", "ZONE_REVIEW", "ASSOCIATION_REVIEW", "PUBLISHED", "ARCHIVED"]
# `ALL` is a staff-only filter of GET /honors: every status inside the caller's scope.
HonorStatusFilter = Literal["DRAFT", "ZONE_REVIEW", "ASSOCIATION_REVIEW", "PUBLISHED", "ARCHIVED", "ALL"]
# Staff-only filter of GET /honors by requirements: none at all, only in a foreign language
# (no `es` rows), or with the source (`es`) text.
HonorContentFilter = Literal["missing_requirements", "foreign_only", "complete"]
ReviewAction = Literal["APPROVE", "REJECT", "REQUEST_CHANGES"]
CATEGORY_SLUG_PATTERN = r"^[a-z0-9-]{2,120}$"


class ResourceType(str, Enum):
    VIDEO = "VIDEO"
    PDF = "PDF"
    LINK = "LINK"
    IMAGE = "IMAGE"


def _resource_type(value):
    """Lowercase is accepted; nothing sent means a plain link."""
    if value is None:
        return ResourceType.LINK
    return value.strip().upper() if isinstance(value, str) else value


def _optional_http_url(value: str | None) -> str | None:
    return http_url(value) if value is not None else None


# ----- requests -----------------------------------------------------------
class QuestionIn(GradableQuestion):
    """Same rules as a course question (app/schemas/content.py): the honor's bank is what an
    instructor imports into a course, so it must already be gradable."""


class RequirementIn(BaseModel):
    # `order` is the legacy name of `position`.
    position: int = Field(default=0, validation_alias=AliasChoices("position", "order"))
    description: str = Field(min_length=1)
    is_theoretical: bool = True
    instructions: str | None = None
    question_bank: list[QuestionIn] = Field(default_factory=list, max_length=MAX_BANK_PER_REQUIREMENT)


class RequirementPatch(BaseModel):
    """PATCH /honors/{id}/requirements/{requirement_id}: only the fields present are applied and
    the requirement keeps its id (and so the progress rows that reference it). `instructions`
    may be cleared with null; the other three may not be null. `question_bank` replaces this
    requirement's questions only."""

    description: str | None = Field(default=None, min_length=1)
    instructions: str | None = None
    is_theoretical: bool | None = None
    question_bank: list[QuestionIn] | None = Field(default=None, max_length=MAX_BANK_PER_REQUIREMENT)


class RequirementOrderIn(BaseModel):
    """PUT /honors/{id}/requirements/order: every id of the edited list, in the new order."""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class ResourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2000)
    type: ResourceType = ResourceType.LINK

    _type = field_validator("type", mode="before")(_resource_type)

    @model_validator(mode="after")
    def _url_matches_type(self):
        """A video is a YouTube / Vimeo one (stored as its canonical watch URL), a PDF or an
        image lives in our own bucket, and a link is an absolute http(s) URL."""
        if self.type == ResourceType.VIDEO:
            self.url = canonical_video_url(self.url)
        elif self.type in (ResourceType.PDF, ResourceType.IMAGE):
            self.url = media_url(self.url.strip())
        else:
            self.url = http_url(self.url)
        return self


class HonorCreate(BaseModel):
    ministry: str = "pathfinders"
    name: str = Field(min_length=2, max_length=180)
    code: str = Field(min_length=1, max_length=40)
    description: str | None = None
    # Category slug within the ministry (see GET /honors/categories).
    category: str | None = None
    difficulty_level: DifficultyLevel | None = None
    honor_type: HonorType | None = Field(
        default=None, validation_alias=AliasChoices("honor_type", "type")
    )
    org_scope_id: uuid.UUID | None = None
    requirements: list[RequirementIn] = Field(default_factory=list)
    resources: list[ResourceIn] = Field(default_factory=list)
    estimated_hours: int | None = Field(default=None, ge=0)
    exam_passing_score: int = Field(default=80, ge=0, le=100)
    exam_time_limit_minutes: int | None = Field(default=None, ge=1)
    max_exam_attempts: int = Field(default=3, ge=1)
    thumbnail_url: str | None = None
    image_url: str | None = Field(
        default=None, validation_alias=AliasChoices("image_url", "patch_image_url")
    )


class HonorUpdate(BaseModel):
    """Only fields present in the body are applied. `code` is immutable."""

    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = None
    category: str | None = None
    difficulty_level: DifficultyLevel | None = None
    honor_type: HonorType | None = Field(
        default=None, validation_alias=AliasChoices("honor_type", "type")
    )
    requirements: list[RequirementIn] | None = None
    resources: list[ResourceIn] | None = None
    estimated_hours: int | None = Field(default=None, ge=0)
    exam_passing_score: int | None = Field(default=None, ge=0, le=100)
    exam_time_limit_minutes: int | None = Field(default=None, ge=1)
    max_exam_attempts: int | None = Field(default=None, ge=1)
    thumbnail_url: str | None = None
    image_url: str | None = Field(
        default=None, validation_alias=AliasChoices("image_url", "patch_image_url")
    )
    # Catalogue facts (Constructor de especialidades).
    source_url: str | None = Field(default=None, max_length=2000)
    wiki_title: str | None = Field(default=None, max_length=200)
    authority: str | None = Field(default=None, max_length=10)
    skill_level: int | None = Field(default=None, ge=1, le=3)
    year_introduced: int | None = Field(default=None, ge=1900, le=2100)
    # `org_scope_id` is MASTER_GC only, and only on a draft. Outside DRAFT, MASTER_GC may still
    # send every field here except `requirements` and `org_scope_id` (routers/honors.LIGHT_FIELDS);
    # `active: false` hides a published honor from the public catalogue.
    org_scope_id: uuid.UUID | None = None
    active: bool | None = None

    _source_url = field_validator("source_url")(_optional_http_url)


class HonorTranslationIn(BaseModel):
    """The honor's name and description in one language other than the source (Spanish).
    PUT replaces the whole row: a description that is not sent is cleared."""

    name: str = Field(min_length=2, max_length=180)
    description: str | None = None


class HonorReviewIn(BaseModel):
    action: ReviewAction
    comments: str | None = None


def _strip_text(value):
    return value.strip() if isinstance(value, str) else value


class CategoryCreate(BaseModel):
    ministry: str = "pathfinders"
    name: str = Field(min_length=2, max_length=120)
    # Taken from the name when it is not sent.
    slug: str | None = Field(default=None, pattern=CATEGORY_SLUG_PATTERN)

    _strip = field_validator("name", mode="before")(_strip_text)


class CategoryUpdate(BaseModel):
    """Only fields present in the body are applied; neither may be null."""

    name: str | None = Field(default=None, min_length=2, max_length=120)
    slug: str | None = Field(default=None, pattern=CATEGORY_SLUG_PATTERN)

    _strip = field_validator("name", mode="before")(_strip_text)


class CategoryTranslationIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)

    _strip = field_validator("name", mode="before")(_strip_text)


class HonorVersionCreate(BaseModel):
    changes_description: str = Field(min_length=1)
    description: str | None = None
    requirements: list[RequirementIn] | None = None


# ----- responses ----------------------------------------------------------
class CategoryOut(BaseModel):
    id: str
    name: str
    slug: str


class CategoryCountOut(CategoryOut):
    """GET /honors/categories?include_counts=true, for staff: honors of every status in scope."""

    honor_count: int


class HonorListItem(BaseModel):
    # The first six fields are the original public contract of GET /honors.
    id: str
    name: str
    slug: str
    image_url: str | None
    source_url: str | None
    active: bool
    code: str | None
    description: str | None
    category: CategoryOut | None
    difficulty_level: str | None
    honor_type: str | None
    status: str
    thumbnail_url: str | None
    patch_image_url: str | None
    estimated_hours: int | None
    version: int
    created_at: datetime
    published_at: datetime | None
    # Language of `name` ("es" is the source text) and the source name when it was translated.
    name_locale: str = "es"
    original_name: str | None = None
    # Public facts from the official catalogue (wiki.pathfindersonline.org).
    wiki_title: str | None = None
    authority: str | None = None
    skill_level: int | None = None
    year_introduced: int | None = None


class HonorStaffListItem(HonorListItem):
    """What staff get from GET /honors: how many requirements the honor has in its source
    language (`es` when there is any, else the language with rows); locale None = no rows."""

    requirements_count: int = 0
    requirements_locale: str | None = None


class QuestionOut(BaseModel):
    id: str
    position: int
    question_text: str
    question_type: str
    options: list[str] | None
    correct_answer: str
    points: int
    explanation: str | None


class RequirementOut(BaseModel):
    id: str
    position: int
    order: int
    description: str
    is_theoretical: bool
    instructions: str | None
    question_count: int
    # Imported text keeps its attribution (the official wiki is CC BY-SA): show it next to the text.
    source: str | None = None
    source_url: str | None = None
    license: str | None = None


class RequirementWithQuestionsOut(RequirementOut):
    question_bank: list[QuestionOut]


class ResourceOut(BaseModel):
    id: str
    position: int
    name: str
    url: str
    type: ResourceType

    @field_validator("type", mode="before")
    @classmethod
    def _legacy_type(cls, value):
        """Rows written before the enum carry free text or NULL: anything unknown is a link."""
        value = _resource_type(value)
        return value if value in ResourceType._value2member_map_ else ResourceType.LINK


class ReviewOut(BaseModel):
    id: str
    reviewer_id: str | None
    reviewer_name: str | None
    reviewer_role: str | None
    action: str
    comments: str | None
    reviewed_at: datetime


class VersionMetadata(BaseModel):
    version: int
    previous_version_id: str | None
    changes_description: str | None


class HonorDetail(HonorListItem):
    ministry: str | None
    org_scope_id: str | None
    exam_passing_score: int
    exam_time_limit_minutes: int | None
    max_exam_attempts: int
    created_by_id: str | None
    created_by_name: str | None
    approved_zone_org_id: str | None
    approved_association_org_id: str | None
    version_metadata: VersionMetadata
    requirements: list[RequirementOut]
    requirements_locale: str | None = None
    resources: list[ResourceOut]
    updated_at: datetime


class HonorStaffDetail(HonorDetail):
    """Adds what only the author and reviewers may see."""

    review_history: list[ReviewOut]


class HonorInstructorDetail(HonorStaffDetail):
    requirements_with_questions: list[RequirementWithQuestionsOut]


class PaginatedHonors(BaseModel):
    items: list[HonorListItem]
    total: int
    limit: int
    offset: int
    has_more: bool


class HonorStats(BaseModel):
    total_honors: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_difficulty: dict[str, int]
    recently_published: list[HonorListItem]
    # Honors without any requirement row, and with rows only in a foreign language (no `es`).
    missing_requirements: int = 0
    foreign_only: int = 0
