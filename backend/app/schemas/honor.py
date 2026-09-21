"""Honor (legacy "specialty") schemas."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

DifficultyLevel = Literal["BEGINNER", "INTERMEDIATE", "ADVANCED"]
HonorType = Literal["OFFICIAL_GC", "DIVISIONAL", "LOCAL"]
HonorStatus = Literal["DRAFT", "ZONE_REVIEW", "ASSOCIATION_REVIEW", "PUBLISHED", "ARCHIVED"]
QuestionType = Literal["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY"]
ReviewAction = Literal["APPROVE", "REJECT", "REQUEST_CHANGES"]


# ----- requests -----------------------------------------------------------
class QuestionIn(BaseModel):
    question_text: str = Field(min_length=1)
    question_type: QuestionType
    options: list[str] | None = None
    correct_answer: str
    points: int = Field(default=1, ge=0)
    explanation: str | None = None


class RequirementIn(BaseModel):
    # `order` is the legacy name of `position`.
    position: int = Field(default=0, validation_alias=AliasChoices("position", "order"))
    description: str = Field(min_length=1)
    is_theoretical: bool = True
    instructions: str | None = None
    question_bank: list[QuestionIn] = Field(default_factory=list)


class ResourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1)
    type: str | None = Field(default=None, max_length=40)


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


class HonorReviewIn(BaseModel):
    action: ReviewAction
    comments: str | None = None


class HonorVersionCreate(BaseModel):
    changes_description: str = Field(min_length=1)
    description: str | None = None
    requirements: list[RequirementIn] | None = None


# ----- responses ----------------------------------------------------------
class CategoryOut(BaseModel):
    id: str
    name: str
    slug: str


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
    type: str | None


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
