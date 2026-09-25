"""Bloque F — Programas: the catalogue of classes and curricula, and the pieces the
portfolio adds to an enrollment when it is a program (sections, requirement kind, target,
what satisfied it and how many hours are approved).

Kept apart from `schemas/portfolio.py` on purpose: block A's contract for honors does not
change, it only gains optional fields that are None for every honor enrollment.
"""
from typing import Literal

from pydantic import BaseModel

# 024: MEDALLION / MASTERY / TRAINING are the certifications of Guías Mayores (`master-guides`).
ProgramKind = Literal["CLASS", "CURRICULUM", "MEDALLION", "MASTERY", "TRAINING"]
CERTIFICATION_KINDS = ("MEDALLION", "MASTERY", "TRAINING")
ProgramStatus = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]
# The filter of `/admin/clases` (MASTER_GC only; everybody else sees PUBLISHED).
ProgramStatusFilter = Literal["ALL", "DRAFT", "PUBLISHED", "ARCHIVED"]
RequirementKind = Literal["FREE", "HONOR", "PROGRAM", "HOURS"]
ActivityCategory = Literal["SERVICE", "ATTENDANCE"]
IssuerLevel = Literal["CLUB", "ASSOCIATION"]


class Attribution(BaseModel):
    """Where the text comes from. CC BY-SA 3.0 obliges the frontend to show it wherever the
    requirement is displayed (docs/ESPECIALIDADES_WIKI.md)."""

    source: str | None = None
    source_url: str | None = None
    license: str | None = None


class ProgramRef(BaseModel):
    """A program inside somebody's portfolio. Deliberately NOT a `HonorRef`: an investiture
    is not an honor and no screen may treat them as the same thing."""

    id: str
    slug: str
    name: str
    kind: ProgramKind
    image_url: str | None = None
    ministry: str | None = None


class HonorTargetRef(BaseModel):
    id: str
    name: str
    slug: str
    image_url: str | None = None


class CategoryTargetRef(BaseModel):
    id: str
    name: str
    slug: str


class RequirementTarget(BaseModel):
    """What a non-FREE requirement points at, summarised for the card."""

    kind: RequirementKind
    honor: HonorTargetRef | None = None
    category: CategoryTargetRef | None = None
    program: ProgramRef | None = None
    quantity: float | None = None
    activity_category: ActivityCategory | None = None
    # HONOR with neither honor nor category: "an honor of your choice".
    open_choice: bool = False


class SatisfiedBy(BaseModel):
    """The achievement that completed a requirement on its own (F2)."""

    type: Literal["honor", "program"]
    name: str
    certificate_no: str | None = None


class QuantityOut(BaseModel):
    approved: float
    target: float


class ProgramRequirementOut(BaseModel):
    position: int
    label: str
    description: str | None
    instructions: str | None
    kind: RequirementKind
    evidence_required: bool
    target: RequirementTarget | None = None
    attribution: Attribution | None = None


class ProgramSectionOut(BaseModel):
    position: int
    slug: str
    name: str
    requirements: list[ProgramRequirementOut]


class ProgramListItem(BaseModel):
    id: str
    slug: str
    name: str
    kind: ProgramKind
    ministry: str
    image_url: str | None
    sort_order: int
    authority: str | None
    status: ProgramStatus
    version: int
    requirement_count: int


class ProgramDetail(ProgramListItem):
    description: str | None
    issuer_level: IssuerLevel
    locale: str
    attribution: Attribution | None
    sections: list[ProgramSectionOut]
    # Requirements that carry a list of honors to plan with (see /recommendations).
    recommendations_count: int = 0


# ----------------------------------------------------------------------------
# /programs/{id}/recommendations — the honors a class asks for
# ----------------------------------------------------------------------------
RecommendationKind = Literal["HONOR", "HONOR_FROM_CATEGORY", "HONOR_ANY", "TEXT"]
# one = pick one of `honors` (or of the categories); all = every one of `honors`;
# any = any honor of the member's choice.
RecommendationChoice = Literal["one", "all", "any"]


class RecommendedHonor(BaseModel):
    id: str
    name: str
    slug: str
    image_url: str | None = None
    category_slug: str | None = None
    skill_level: int | None = None


class RecommendationSection(BaseModel):
    slug: str
    name: str


class RecommendationItem(BaseModel):
    requirement_id: str
    section: RecommendationSection | None
    label: str
    text: str | None
    kind: RecommendationKind
    choose: RecommendationChoice
    # `category` is the first of `categories` (a text may name two: «Artes Domésticas o …»).
    category: CategoryTargetRef | None = None
    categories: list[CategoryTargetRef] = []
    honors: list[RecommendedHonor] = []


class ProgramRecommendations(BaseModel):
    program_id: str
    locale: str
    items: list[RecommendationItem]


class EnrollmentSection(BaseModel):
    """A section of the member's card: the requirements of the enrollment, grouped."""

    position: int
    slug: str
    name: str
    positions: list[int]
    complete: int
    total: int
