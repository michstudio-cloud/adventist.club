"""
Honors (legacy "specialties"): public catalogue plus the authoring workflow

    DRAFT -> ZONE_REVIEW -> ASSOCIATION_REVIEW -> PUBLISHED   (-> ARCHIVED -> DRAFT on restore)

Every mutation writes its audit_log row in the same transaction.
Literal routes are declared before the `/{honor_id}` routes.
"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, violated_constraint
from app.deps import get_current_user, get_optional_user, require_roles
from app.models import (
    Honor,
    HonorCategory,
    HonorCategoryTranslation,
    HonorQuestion,
    HonorRequirement,
    HonorResource,
    HonorReview,
    HonorTranslation,
    Ministry,
    Organization,
    ProgramRequirement,
    User,
)
from app.rbac import get_org_path, is_master, org_in_user_scope
from app.schemas.honor import (
    CategoryCountOut,
    CategoryCreate,
    CategoryOut,
    CategoryTranslationIn,
    CategoryUpdate,
    HonorCreate,
    HonorDetail,
    HonorInstructorDetail,
    HonorListItem,
    HonorReviewIn,
    HonorStaffDetail,
    HonorStats,
    HonorStatus,
    HonorStatusFilter,
    HonorTranslationIn,
    HonorUpdate,
    HonorVersionCreate,
    PaginatedHonors,
    QuestionOut,
    RequirementIn,
    RequirementOut,
    RequirementWithQuestionsOut,
    ResourceIn,
    ResourceOut,
    ReviewOut,
    VersionMetadata,
)
from app.security import (
    ADMIN_ASSOCIATION,
    COORDINATOR_ZONE,
    INSTRUCTOR,
    MASTER_GC,
    utcnow,
)
from app.services.audit import record_audit
from app.services.locales import (
    LOCALE_PATTERN,
    SOURCE_LOCALE,
    best_locale,
    canonical_locale,
    is_source_locale,
)
from app.text import escape_like, slugify
# The stages and their reviewers are shared with the courses of Bloque B: one definition,
# two routers (app/workflow.py). Re-exported here so importers of this module keep working.
from app.workflow import (  # noqa: F401
    ARCHIVED,
    ASSOCIATION_REVIEW,
    ASSOCIATION_REVIEWERS,
    DRAFT,
    PUBLISHED,
    STAGE_REVIEWERS,
    ZONE_REVIEW,
    ZONE_REVIEWERS,
)

SPANISH_COLLATION = "es-x-icu"

router = APIRouter(prefix="/api/v1/honors", tags=["honors"])

AUTHOR_ROLES = (INSTRUCTOR, ADMIN_ASSOCIATION, MASTER_GC)
INSTRUCTOR_VIEW_ROLES = (INSTRUCTOR, COORDINATOR_ZONE, ADMIN_ASSOCIATION, MASTER_GC)

ENTITY = "HONOR"
CATEGORY_ENTITY = "HONOR_CATEGORY"
HONOR_UNIQUE_CONSTRAINTS = {"honors_ministry_id_code_key", "honors_ministry_id_slug_key"}
CATEGORY_SLUG_CONSTRAINT = "honor_categories_ministry_id_slug_key"
CATEGORY_SLUG_MAX_LENGTH = 120
CODE_MAX_LENGTH = 40
SLUG_MAX_LENGTH = 180


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def _get_ministry(db: AsyncSession, slug: str) -> Ministry | None:
    return (await db.execute(select(Ministry).where(Ministry.slug == slug))).scalar_one_or_none()


async def _get_category(db: AsyncSession, ministry_id: uuid.UUID, slug: str) -> HonorCategory:
    stmt = select(HonorCategory).where(
        HonorCategory.ministry_id == ministry_id, HonorCategory.slug == slug
    )
    category = (await db.execute(stmt)).scalar_one_or_none()
    if category is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown category '{slug}'")
    return category


async def _get_honor_or_404(db: AsyncSession, honor_id: uuid.UUID, lock: bool = False) -> Honor:
    stmt = select(Honor).where(Honor.id == honor_id)
    if lock:
        # Serializes concurrent workflow transitions on the same honor.
        stmt = stmt.with_for_update()
    honor = (await db.execute(stmt)).scalar_one_or_none()
    if honor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Honor not found")
    return honor


async def _unique_slug(db: AsyncSession, ministry_id: uuid.UUID | None, base: str) -> str:
    base = base[: SLUG_MAX_LENGTH - 8]
    slug, suffix = base, 2
    while True:
        stmt = select(Honor.id).where(Honor.ministry_id == ministry_id, Honor.slug == slug)
        if (await db.execute(stmt.limit(1))).scalar_one_or_none() is None:
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


async def _code_taken(db: AsyncSession, ministry_id: uuid.UUID | None, code: str) -> bool:
    stmt = select(Honor.id).where(Honor.ministry_id == ministry_id, Honor.code == code)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


# ----------------------------------------------------------------------------
# Permissions
# ----------------------------------------------------------------------------
async def _is_reviewer_in_scope(db: AsyncSession, user: User, honor: Honor) -> bool:
    if user.role not in ZONE_REVIEWERS:
        return False
    return await org_in_user_scope(db, user, honor.org_scope_id)


async def _can_view_unpublished(db: AsyncSession, user: User | None, honor: Honor) -> bool:
    if user is None:
        return False
    if honor.created_by_id == user.id:
        return True
    return await _is_reviewer_in_scope(db, user, honor)


# ----------------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------------
async def _resolve_locale(db: AsyncSession, model, requested: str | None) -> str | None:
    """The stored locale that best serves the request: exact, then the bare language, then any
    region of that language (pt -> pt-BR). None means "answer with the source text"."""
    if not requested or requested.lower().split("-")[0] == SOURCE_LOCALE:
        return None
    language = requested.lower().split("-")[0]
    stored = (
        await db.execute(
            select(model.locale)
            .where(or_(func.lower(model.locale) == language, func.lower(model.locale).like(f"{language}-%")))
            .distinct()
        )
    ).scalars().all()
    by_lower = {value.lower(): value for value in sorted(stored)}
    return by_lower.get(requested.lower()) or by_lower.get(language) or next(iter(by_lower.values()), None)


def _category_out(category: HonorCategory | None, translated: str | None = None) -> CategoryOut | None:
    if category is None:
        return None
    return CategoryOut(id=str(category.id), name=translated or category.name, slug=category.slug)


def _list_fields(
    honor: Honor,
    category: HonorCategory | None,
    translated: str | None = None,
    locale: str | None = None,
    translated_category: str | None = None,
) -> dict:
    return {
        "id": str(honor.id),
        "name": translated or honor.name,
        "name_locale": locale if translated else SOURCE_LOCALE,
        "original_name": honor.name if translated else None,
        "wiki_title": honor.wiki_title,
        "authority": honor.authority,
        "skill_level": honor.skill_level,
        "year_introduced": honor.year_introduced,
        "slug": honor.slug,
        "image_url": honor.image_url,
        "source_url": honor.source_url,
        "active": honor.active,
        "code": honor.code,
        "description": honor.description,
        "category": _category_out(category, translated_category),
        "difficulty_level": honor.difficulty_level,
        "honor_type": honor.honor_type,
        "status": honor.status,
        "thumbnail_url": honor.thumbnail_url,
        "patch_image_url": honor.image_url,
        "estimated_hours": honor.estimated_hours,
        "version": honor.version,
        "created_at": honor.created_at,
        "published_at": honor.published_at,
    }


def _list_item(honor: Honor, category: HonorCategory | None) -> HonorListItem:
    return HonorListItem(**_list_fields(honor, category))


def _with_category(stmt):
    return stmt.select_from(Honor).outerjoin(
        HonorCategory, HonorCategory.id == Honor.category_id
    )


async def _build_detail(
    db: AsyncSession,
    honor: Honor,
    *,
    staff: bool = False,
    with_questions: bool = False,
    locale: str | None = None,
) -> HonorDetail:
    category = await db.get(HonorCategory, honor.category_id) if honor.category_id else None
    name_locale = await _resolve_locale(db, HonorTranslation, locale)
    translation = await db.get(HonorTranslation, (honor.id, name_locale)) if name_locale else None
    ministry = await db.get(Ministry, honor.ministry_id) if honor.ministry_id else None
    creator = await db.get(User, honor.created_by_id) if honor.created_by_id else None

    # One requirement list per language: the requested one, else the source language, else whatever exists.
    stored = (
        await db.execute(
            select(HonorRequirement.locale).where(HonorRequirement.honor_id == honor.id).distinct()
        )
    ).scalars().all()
    requirements_locale = best_locale(stored, locale)
    requirements = (
        await db.execute(
            select(HonorRequirement)
            .where(HonorRequirement.honor_id == honor.id, HonorRequirement.locale == requirements_locale)
            .order_by(HonorRequirement.position, HonorRequirement.created_at)
        )
    ).scalars().all()
    requirement_ids = [r.id for r in requirements]

    questions_by_requirement: dict[uuid.UUID, list[HonorQuestion]] = {}
    if requirement_ids:
        questions = (
            await db.execute(
                select(HonorQuestion)
                .where(HonorQuestion.requirement_id.in_(requirement_ids))
                .order_by(HonorQuestion.position, HonorQuestion.created_at)
            )
        ).scalars().all()
        for question in questions:
            questions_by_requirement.setdefault(question.requirement_id, []).append(question)

    resources = (
        await db.execute(
            select(HonorResource)
            .where(HonorResource.honor_id == honor.id)
            .order_by(HonorResource.position, HonorResource.created_at)
        )
    ).scalars().all()

    def requirement_fields(req: HonorRequirement) -> dict:
        return {
            "id": str(req.id),
            "position": req.position,
            "order": req.position,
            "description": req.description,
            "is_theoretical": req.is_theoretical,
            "instructions": req.instructions,
            "question_count": len(questions_by_requirement.get(req.id, [])),
            "source": req.source,
            "source_url": req.source_url,
            "license": req.license,
        }

    fields = {
        **_list_fields(honor, category, translation.name if translation else None, name_locale),
        "ministry": ministry.slug if ministry else None,
        "requirements_locale": requirements_locale if requirements else None,
        "org_scope_id": str(honor.org_scope_id) if honor.org_scope_id else None,
        "exam_passing_score": honor.exam_passing_score,
        "exam_time_limit_minutes": honor.exam_time_limit_minutes,
        "max_exam_attempts": honor.max_exam_attempts,
        "created_by_id": str(honor.created_by_id) if honor.created_by_id else None,
        "created_by_name": creator.name if creator else None,
        "approved_zone_org_id": (
            str(honor.approved_zone_org_id) if honor.approved_zone_org_id else None
        ),
        "approved_association_org_id": (
            str(honor.approved_association_org_id) if honor.approved_association_org_id else None
        ),
        "version_metadata": VersionMetadata(
            version=honor.version,
            previous_version_id=(
                str(honor.previous_version_id) if honor.previous_version_id else None
            ),
            changes_description=honor.changes_description,
        ),
        # The public shape has no question fields at all: `correct_answer`
        # cannot leak through it by construction.
        "requirements": [RequirementOut(**requirement_fields(r)) for r in requirements],
        "resources": [
            ResourceOut(id=str(r.id), position=r.position, name=r.name, url=r.url, type=r.type)
            for r in resources
        ],
        "updated_at": honor.updated_at,
    }
    if not staff and not with_questions:
        return HonorDetail(**fields)

    reviews = (
        await db.execute(
            select(HonorReview)
            # `honor_reviews` is shared with the courses of Bloque B: their rows carry
            # `course_id` and belong to the course's history, not to the honor's.
            .where(HonorReview.honor_id == honor.id, HonorReview.course_id.is_(None))
            .order_by(HonorReview.reviewed_at)
        )
    ).scalars().all()
    fields["review_history"] = [
        ReviewOut(
            id=str(r.id),
            reviewer_id=str(r.reviewer_id) if r.reviewer_id else None,
            reviewer_name=r.reviewer_name,
            reviewer_role=r.reviewer_role,
            action=r.action,
            comments=r.comments,
            reviewed_at=r.reviewed_at,
        )
        for r in reviews
    ]
    if not with_questions:
        return HonorStaffDetail(**fields)

    fields["requirements_with_questions"] = [
        RequirementWithQuestionsOut(
            **requirement_fields(req),
            question_bank=[
                QuestionOut(
                    id=str(q.id),
                    position=q.position,
                    question_text=q.question_text,
                    question_type=q.question_type,
                    options=q.options,
                    correct_answer=q.correct_answer,
                    points=q.points,
                    explanation=q.explanation,
                )
                for q in questions_by_requirement.get(req.id, [])
            ],
        )
        for req in requirements
    ]
    return HonorInstructorDetail(**fields)


# ----------------------------------------------------------------------------
# Content helpers
# ----------------------------------------------------------------------------
def _question_row(requirement_id: uuid.UUID, position: int, source) -> HonorQuestion:
    """`source` is a QuestionIn or an existing HonorQuestion: same field names."""
    return HonorQuestion(
        id=uuid.uuid4(),
        requirement_id=requirement_id,
        position=position,
        question_text=source.question_text,
        question_type=source.question_type,
        options=source.options,
        correct_answer=source.correct_answer,
        points=source.points,
        explanation=source.explanation,
    )


async def _stage_requirements(
    db: AsyncSession, honor_id: uuid.UUID, requirements: list[RequirementIn]
) -> None:
    """
    The models declare no relationship(), so the unit of work cannot order
    inserts by foreign key: requirements are flushed before their questions
    are staged. Still the caller's transaction; nothing is committed here.
    """
    questions = []
    for req_index, req in enumerate(requirements):
        requirement = HonorRequirement(
            id=uuid.uuid4(),
            honor_id=honor_id,
            position=req.position or req_index + 1,
            description=req.description,
            is_theoretical=req.is_theoretical,
            instructions=req.instructions,
        )
        db.add(requirement)
        questions += [
            _question_row(requirement.id, q_index + 1, question)
            for q_index, question in enumerate(req.question_bank)
        ]
    await db.flush()
    db.add_all(questions)


def _stage_resources(db: AsyncSession, honor_id: uuid.UUID, resources: list[ResourceIn]) -> None:
    for index, resource in enumerate(resources):
        db.add(
            HonorResource(
                id=uuid.uuid4(),
                honor_id=honor_id,
                position=index + 1,
                name=resource.name,
                url=resource.url,
                type=resource.type.value,
            )
        )


async def _copy_requirements(db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID) -> None:
    requirements = (
        await db.execute(select(HonorRequirement).where(HonorRequirement.honor_id == source_id))
    ).scalars().all()
    new_ids = {req.id: uuid.uuid4() for req in requirements}
    for req in requirements:
        db.add(
            HonorRequirement(
                id=new_ids[req.id],
                honor_id=target_id,
                position=req.position,
                description=req.description,
                is_theoretical=req.is_theoretical,
                instructions=req.instructions,
                locale=req.locale,
                source=req.source,
                source_url=req.source_url,
                license=req.license,
            )
        )
    await db.flush()  # requirements before the questions that reference them
    if not new_ids:
        return
    questions = (
        await db.execute(
            select(HonorQuestion).where(HonorQuestion.requirement_id.in_(list(new_ids)))
        )
    ).scalars().all()
    db.add_all(_question_row(new_ids[q.requirement_id], q.position, q) for q in questions)


async def _copy_resources(db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID) -> None:
    resources = (
        await db.execute(select(HonorResource).where(HonorResource.honor_id == source_id))
    ).scalars().all()
    for r in resources:
        db.add(
            HonorResource(
                id=uuid.uuid4(),
                honor_id=target_id,
                position=r.position,
                name=r.name,
                url=r.url,
                type=r.type,
            )
        )


async def _copy_translations(db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID) -> None:
    """A new version keeps the names it already had in other languages."""
    rows = (
        await db.execute(select(HonorTranslation).where(HonorTranslation.honor_id == source_id))
    ).scalars().all()
    for row in rows:
        db.add(
            HonorTranslation(
                honor_id=target_id,
                locale=row.locale,
                name=row.name,
                description=row.description,
                source=row.source,
                source_url=row.source_url,
                license=row.license,
            )
        )


async def _insert_honor(db: AsyncSession, honor: Honor, conflict_detail: str) -> None:
    """
    INSERT the honor row now, inside the caller's transaction, so its children
    can reference it. The earlier "is this code taken?" check cannot see a
    concurrent request: the unique index is the real guard, and losing that
    race is a 409. Any other integrity error is a bug and must stay visible.
    """
    db.add(honor)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) not in HONOR_UNIQUE_CONSTRAINTS:
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, conflict_detail) from exc


async def _require_requirements(db: AsyncSession, honor: Honor) -> None:
    """Nothing reaches review or the catalogue without requirements, in any language."""
    requirement_count = (
        await db.execute(
            select(func.count(HonorRequirement.id)).where(HonorRequirement.honor_id == honor.id)
        )
    ).scalar_one()
    if requirement_count == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "honor_without_requirements")


def _publish(honor: Honor) -> None:
    now = utcnow()
    honor.status = PUBLISHED
    honor.active = True
    honor.published_at = now
    honor.updated_at = now


# ----------------------------------------------------------------------------
# Public catalogue
# ----------------------------------------------------------------------------
@router.get("", response_model=list[HonorListItem])
async def list_honors(
    response: Response,
    q: str | None = Query(None, max_length=100),
    ministry: str = "pathfinders",
    category: str | None = Query(None, description="Category slug"),
    status_filter: HonorStatusFilter | None = Query(None, alias="status"),
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Public list: only PUBLISHED + active honors. Names come in `locale` when a translation
    exists, otherwise in the source language. The language is always the caller's explicit choice
    (the UI language), never Accept-Language: a Spanish screen must not fill up with English names. `status` is honoured for
    signed-in reviewers (their scope) and instructors (their own honors);
    for anyone else it is ignored. `status=ALL` (every status, hidden ones included, inside the
    caller's scope) is staff-only: anyone else gets 403. Total row count: `X-Total-Count` header.
    """
    staff_scope = None
    wants_unpublished = status_filter is not None and status_filter != PUBLISHED
    if wants_unpublished and current_user is not None:
        staff_scope = await _staff_list_conditions(db, current_user)
    if status_filter == "ALL" and staff_scope is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "status_all_staff_only")

    ministry_row = await _get_ministry(db, ministry)
    if ministry_row is None:
        response.headers["X-Total-Count"] = "0"
        return []

    conditions = [Honor.ministry_id == ministry_row.id]
    if status_filter == "ALL":
        conditions += staff_scope
    elif staff_scope is not None:
        conditions += [Honor.status == status_filter, *staff_scope]
    else:
        conditions += [Honor.status == PUBLISHED, Honor.active.is_(True)]

    if category:
        conditions.append(HonorCategory.slug == category)

    name_locale = await _resolve_locale(db, HonorTranslation, locale)
    category_locale = await _resolve_locale(db, HonorCategoryTranslation, locale)
    shown_name = func.coalesce(HonorTranslation.name, Honor.name)

    def localized(stmt):
        # The locale is a constant in both joins, so they never multiply rows.
        return _with_category(stmt).outerjoin(
            HonorTranslation,
            (HonorTranslation.honor_id == Honor.id) & (HonorTranslation.locale == name_locale),
        ).outerjoin(
            HonorCategoryTranslation,
            (HonorCategoryTranslation.category_id == HonorCategory.id)
            & (HonorCategoryTranslation.locale == category_locale),
        )

    # The database collation is C.UTF-8, which sorts "Árboles" and "Óptica" after "Z".
    order_by = [shown_name.collate(SPANISH_COLLATION if name_locale is None else "und-x-icu"), Honor.id]
    search = q.strip() if q else None
    if search:
        # Substring match plus pg_trgm similarity so small typos still hit, in either language.
        pattern = f"%{escape_like(search)}%"
        conditions.append(
            or_(
                Honor.name.ilike(pattern, escape="\\"),
                Honor.code.ilike(pattern, escape="\\"),
                Honor.name.op("%")(search),
                HonorTranslation.name.ilike(pattern, escape="\\"),
                HonorTranslation.name.op("%")(search),
            )
        )
        order_by.insert(
            0, func.greatest(func.similarity(Honor.name, search), func.similarity(shown_name, search)).desc()
        )

    total = (await db.execute(localized(select(func.count(Honor.id))).where(*conditions))).scalar_one()
    rows = (
        await db.execute(
            localized(select(Honor, HonorCategory, HonorTranslation.name, HonorCategoryTranslation.name))
            .where(*conditions)
            .order_by(*order_by)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["Content-Language"] = name_locale or SOURCE_LOCALE
    return [
        HonorListItem(**_list_fields(honor, category_row, translated, name_locale, translated_category))
        for honor, category_row, translated, translated_category in rows
    ]


async def _staff_list_conditions(db: AsyncSession, user: User) -> list | None:
    """Extra WHERE clauses for a non-public listing, or None if the caller is public."""
    if is_master(user):
        return []
    if user.role in ZONE_REVIEWERS:
        scope_path = await get_org_path(db, user.organization_id)
        if not scope_path:
            return [Honor.created_by_id == user.id]
        in_scope = select(Organization.id).where(Organization.path.op("<@")(scope_path))
        return [or_(Honor.org_scope_id.in_(in_scope), Honor.created_by_id == user.id)]
    if user.role == INSTRUCTOR:
        return [Honor.created_by_id == user.id]
    return None


@router.get(
    "/categories", response_model=None, responses={200: {"model": list[CategoryCountOut]}}
)
async def list_categories(
    ministry: str = "pathfinders",
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    include_counts: bool = Query(False, description="Staff only: honor_count per category"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[CategoryOut]:
    """Public list. With `include_counts=true` a staff caller also gets `honor_count`: the
    honors of every status inside their scope (as `GET /honors?status=ALL`). For anyone else
    the flag is ignored."""
    ministry_row = await _get_ministry(db, ministry)
    if ministry_row is None:
        return []
    resolved = await _resolve_locale(db, HonorCategoryTranslation, locale)
    stmt = (
        select(HonorCategory, HonorCategoryTranslation.name)
        .outerjoin(
            HonorCategoryTranslation,
            (HonorCategoryTranslation.category_id == HonorCategory.id)
            & (HonorCategoryTranslation.locale == resolved),
        )
        .where(HonorCategory.ministry_id == ministry_row.id)
        .order_by(HonorCategory.name.collate(SPANISH_COLLATION))
    )
    rows = (await db.execute(stmt)).all()

    scope = None
    if include_counts and current_user is not None:
        scope = await _staff_list_conditions(db, current_user)
    if scope is None:
        return [_category_out(row, translated) for row, translated in rows]
    counts = dict(
        (
            await db.execute(
                select(Honor.category_id, func.count(Honor.id))
                .where(Honor.ministry_id == ministry_row.id, Honor.category_id.is_not(None), *scope)
                .group_by(Honor.category_id)
            )
        ).all()
    )
    return [
        CategoryCountOut(
            **_category_out(row, translated).model_dump(), honor_count=counts.get(row.id, 0)
        )
        for row, translated in rows
    ]


# ----------------------------------------------------------------------------
# Categories (MASTER_GC). Declared before the `/{honor_id}` routes.
# ----------------------------------------------------------------------------
def _category_slug(name: str) -> str:
    slug = slugify(name, "category")[:CATEGORY_SLUG_MAX_LENGTH].strip("-")
    return slug if len(slug) >= 2 else "category"


async def _get_category_or_404(
    db: AsyncSession, category_id: uuid.UUID, lock: bool = False
) -> HonorCategory:
    stmt = select(HonorCategory).where(HonorCategory.id == category_id)
    if lock:
        stmt = stmt.with_for_update()
    category = (await db.execute(stmt)).scalar_one_or_none()
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return category


async def _category_slug_taken(
    db: AsyncSession, ministry_id: uuid.UUID | None, slug: str, exclude: uuid.UUID | None = None
) -> bool:
    stmt = select(HonorCategory.id).where(
        HonorCategory.ministry_id == ministry_id, HonorCategory.slug == slug
    )
    if exclude is not None:
        stmt = stmt.where(HonorCategory.id != exclude)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _commit_category(db: AsyncSession) -> None:
    """The unique index is the real guard against two requests taking the same slug."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != CATEGORY_SLUG_CONSTRAINT:
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, "category_slug_taken") from exc


def _category_audit(
    db: AsyncSession, action: str, category: HonorCategory, user: User, request: Request, details: str,
    metadata: dict | None = None,
) -> None:
    record_audit(
        db,
        action=action,
        entity_type=CATEGORY_ENTITY,
        entity_id=category.id,
        actor=user,
        details=details,
        metadata=metadata,
        request=request,
    )


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreate,
    request: Request,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    ministry_row = await _get_ministry(db, payload.ministry)
    if ministry_row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown ministry '{payload.ministry}'")
    slug = payload.slug or _category_slug(payload.name)
    if await _category_slug_taken(db, ministry_row.id, slug):
        raise HTTPException(status.HTTP_409_CONFLICT, "category_slug_taken")
    now = utcnow()
    category = HonorCategory(
        id=uuid.uuid4(),
        ministry_id=ministry_row.id,
        name=payload.name,
        slug=slug,
        created_at=now,
        updated_at=now,
    )
    db.add(category)
    _category_audit(
        db, "CREATE", category, current_user, request, f"Created honor category: {category.name}",
        {"ministry": ministry_row.slug, "slug": slug},
    )
    await _commit_category(db)
    return _category_out(category)


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    request: Request,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    category = await _get_category_or_404(db, category_id, lock=True)
    changes = payload.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name and slug cannot be null")
    if "slug" in changes and await _category_slug_taken(
        db, category.ministry_id, changes["slug"], exclude=category.id
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "category_slug_taken")
    for column, value in changes.items():
        setattr(category, column, value)
    category.updated_at = utcnow()
    _category_audit(
        db, "UPDATE", category, current_user, request, f"Updated honor category: {category.name}",
        {"fields": sorted(changes)},
    )
    await _commit_category(db)
    return _category_out(category)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    """Only a category nothing points at: no honor in any status, no program requirement."""
    category = await _get_category_or_404(db, category_id, lock=True)
    in_use = False
    for column in (Honor.category_id, ProgramRequirement.target_category_id):
        stmt = select(column).where(column == category.id).limit(1)
        in_use = in_use or (await db.execute(stmt)).scalar_one_or_none() is not None
    if in_use:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "category_in_use")
    # Its translations go with it (ON DELETE CASCADE).
    await db.delete(category)
    _category_audit(
        db, "DELETE", category, current_user, request, f"Deleted honor category: {category.name}",
        {"slug": category.slug},
    )
    await db.commit()


# honor_category_translations.locale is VARCHAR(16), like honor_translations.locale.
CategoryLocale = Path(pattern=LOCALE_PATTERN, max_length=16)


def _category_locale(locale: str) -> str:
    if is_source_locale(locale):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{SOURCE_LOCALE}' is the source language: edit it with PUT /honors/categories/{{category_id}}",
        )
    return canonical_locale(locale)


@router.put("/categories/{category_id}/translations/{locale}", response_model=CategoryOut)
async def put_category_translation(
    category_id: uuid.UUID,
    payload: CategoryTranslationIn,
    request: Request,
    locale: str = CategoryLocale,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace the category's name in `locale`; answers with the name in it."""
    locale = _category_locale(locale)
    # The category row is locked: two requests for a new locale cannot both INSERT.
    category = await _get_category_or_404(db, category_id, lock=True)
    translation = await db.get(HonorCategoryTranslation, (category.id, locale))
    created = translation is None
    if created:
        translation = HonorCategoryTranslation(category_id=category.id, locale=locale)
        db.add(translation)
    translation.name = payload.name
    translation.updated_at = utcnow()
    _category_audit(
        db, "UPDATE", category, current_user, request,
        f"{'Added' if created else 'Replaced'} {locale} translation of honor category: {category.name}",
        {"translation": locale, "created": created},
    )
    await db.commit()
    return _category_out(category, translation.name)


@router.delete(
    "/categories/{category_id}/translations/{locale}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_category_translation(
    category_id: uuid.UUID,
    request: Request,
    locale: str = CategoryLocale,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    locale = _category_locale(locale)
    category = await _get_category_or_404(db, category_id, lock=True)
    translation = await db.get(HonorCategoryTranslation, (category.id, locale))
    if translation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Translation not found")
    await db.delete(translation)
    _category_audit(
        db, "UPDATE", category, current_user, request,
        f"Removed {locale} translation of honor category: {category.name}",
        {"translation": locale, "removed": True},
    )
    await db.commit()


@router.get("/my/created", response_model=PaginatedHonors)
async def my_created_honors(
    status_filter: HonorStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles(*AUTHOR_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    conditions = [Honor.created_by_id == current_user.id]
    if status_filter:
        conditions.append(Honor.status == status_filter)
    total = (await db.execute(select(func.count(Honor.id)).where(*conditions))).scalar_one()
    rows = (
        await db.execute(
            _with_category(select(Honor, HonorCategory))
            .where(*conditions)
            .order_by(Honor.updated_at.desc(), Honor.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [_list_item(honor, category) for honor, category in rows]
    return PaginatedHonors(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


@router.get("/pending/reviews", response_model=list[HonorListItem])
async def pending_reviews(
    ministry: str | None = None,
    current_user: User = Depends(require_roles(*ZONE_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    """Honors waiting for the caller's review level, inside the caller's subtree."""
    if current_user.role == COORDINATOR_ZONE:
        stages = [ZONE_REVIEW]
    elif is_master(current_user):
        stages = [ZONE_REVIEW, ASSOCIATION_REVIEW]
    else:
        stages = [ASSOCIATION_REVIEW]

    conditions = [Honor.status.in_(stages)]
    if not is_master(current_user):
        scope_path = await get_org_path(db, current_user.organization_id)
        if not scope_path:
            return []
        in_scope = select(Organization.id).where(Organization.path.op("<@")(scope_path))
        conditions.append(Honor.org_scope_id.in_(in_scope))
    if ministry:
        ministry_row = await _get_ministry(db, ministry)
        if ministry_row is None:
            return []
        conditions.append(Honor.ministry_id == ministry_row.id)

    rows = (
        await db.execute(
            _with_category(select(Honor, HonorCategory))
            .where(*conditions)
            .order_by(Honor.updated_at, Honor.id)
            .limit(200)
        )
    ).all()
    return [_list_item(honor, category) for honor, category in rows]


@router.get("/stats/overview", response_model=HonorStats)
async def stats_overview(
    ministry: str = "pathfinders",
    current_user: User = Depends(require_roles(*ASSOCIATION_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    ministry_row = await _get_ministry(db, ministry)
    if ministry_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown ministry '{ministry}'")

    conditions = [Honor.ministry_id == ministry_row.id]
    if not is_master(current_user):
        scope_path = await get_org_path(db, current_user.organization_id)
        if not scope_path:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You have no organization scope")
        in_scope = select(Organization.id).where(Organization.path.op("<@")(scope_path))
        # Published honors are common catalogue; drafts only from the own subtree.
        conditions.append(or_(Honor.status == PUBLISHED, Honor.org_scope_id.in_(in_scope)))

    async def grouped(column) -> dict[str, int]:
        stmt = select(column, func.count(Honor.id)).where(*conditions).group_by(column)
        if column is HonorCategory.slug:
            stmt = _with_category(stmt)
        return {(key or "unset"): count for key, count in (await db.execute(stmt)).all()}

    by_status = await grouped(Honor.status)
    recent = (
        await db.execute(
            _with_category(select(Honor, HonorCategory))
            .where(*conditions, Honor.status == PUBLISHED, Honor.published_at.is_not(None))
            .order_by(Honor.published_at.desc())
            .limit(5)
        )
    ).all()
    return HonorStats(
        total_honors=sum(by_status.values()),
        by_status=by_status,
        by_category=await grouped(HonorCategory.slug),
        by_difficulty=await grouped(Honor.difficulty_level),
        recently_published=[_list_item(honor, category) for honor, category in recent],
    )


# ----------------------------------------------------------------------------
# Authoring
# ----------------------------------------------------------------------------
@router.post("", response_model=HonorStaffDetail, status_code=status.HTTP_201_CREATED)
async def create_honor(
    payload: HonorCreate,
    request: Request,
    current_user: User = Depends(require_roles(*AUTHOR_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    ministry_row = await _get_ministry(db, payload.ministry)
    if ministry_row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown ministry '{payload.ministry}'")

    category = None
    if payload.category:
        category = await _get_category(db, ministry_row.id, payload.category)

    code = payload.code.strip()
    if await _code_taken(db, ministry_row.id, code):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Honor with code '{code}' already exists")

    org_scope_id = payload.org_scope_id or current_user.organization_id
    if payload.org_scope_id and not await org_in_user_scope(db, current_user, org_scope_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "org_scope_id is outside your scope")

    now = utcnow()
    honor = Honor(
        id=uuid.uuid4(),
        ministry_id=ministry_row.id,
        category_id=category.id if category else None,
        name=payload.name.strip(),
        slug=await _unique_slug(db, ministry_row.id, slugify(payload.name, "honor")),
        image_url=payload.image_url,
        active=True,
        code=code,
        description=payload.description,
        difficulty_level=payload.difficulty_level,
        honor_type=payload.honor_type,
        status=DRAFT,
        org_scope_id=org_scope_id,
        estimated_hours=payload.estimated_hours,
        exam_passing_score=payload.exam_passing_score,
        exam_time_limit_minutes=payload.exam_time_limit_minutes,
        max_exam_attempts=payload.max_exam_attempts,
        created_by_id=current_user.id,
        thumbnail_url=payload.thumbnail_url,
        version=1,
        created_at=now,
        updated_at=now,
    )
    await _insert_honor(db, honor, f"Honor with code '{code}' already exists")
    await _stage_requirements(db, honor.id, payload.requirements)
    _stage_resources(db, honor.id, payload.resources)
    record_audit(
        db,
        action="CREATE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Created honor: {honor.name} ({honor.code})",
        metadata={"ministry": ministry_row.slug, "category": payload.category},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.get("/{honor_id}", response_model=None)
async def get_honor(
    honor_id: uuid.UUID,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> HonorDetail | HonorStaffDetail:
    """Public detail of a published honor. Never includes the question bank."""
    honor = await _get_honor_or_404(db, honor_id)
    staff = await _can_view_unpublished(db, current_user, honor)
    if honor.status != PUBLISHED and not staff:
        # 404 rather than 403: do not confirm that an unpublished honor exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Honor not found")
    return await _build_detail(db, honor, staff=staff, locale=locale)


@router.get("/{honor_id}/instructor", response_model=HonorInstructorDetail)
async def get_honor_for_instructor(
    honor_id: uuid.UUID,
    current_user: User = Depends(require_roles(*INSTRUCTOR_VIEW_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Full detail including the question bank with correct answers.

    Bloque C §4.1 (hallazgo 2): `INSTRUCTOR` is a self-registration role, so handing the
    answers of every published honor to any instructor meant handing them to anyone with a
    second account. Only the creator, the reviewers in scope and MASTER_GC read them now —
    a published honor answers 403 (it exists in public), an unpublished one 404.
    """
    honor = await _get_honor_or_404(db, honor_id)
    if not await _can_view_unpublished(db, current_user, honor):
        if honor.status != PUBLISHED:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Honor not found")
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El banco de preguntas solo lo ve quien creó la especialidad o quien la revisa",
        )
    return await _build_detail(db, honor, staff=True, with_questions=True)


@router.put("/{honor_id}", response_model=HonorStaffDetail)
async def update_honor(
    honor_id: uuid.UUID,
    payload: HonorUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Content moves only while the honor is a DRAFT. The one exception is `active`, which
    MASTER_GC toggles in any status to hide a published honor from the public catalogue (or
    show it again). `org_scope_id` is MASTER_GC's alone."""
    honor = await _get_honor_or_404(db, honor_id, lock=True)
    master = is_master(current_user)
    if honor.created_by_id != current_user.id and not master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the creator can update this honor")

    changes = payload.model_dump(exclude_unset=True)
    if "org_scope_id" in changes and not master:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "org_scope_master_only")
    only_visibility = master and set(changes) == {"active"}
    if honor.status != DRAFT and not only_visibility:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only draft honors can be updated. Create a new version instead.",
        )
    if changes.get("name", "") is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name cannot be null")
    if changes.get("org_scope_id") is not None and await db.get(
        Organization, changes["org_scope_id"]
    ) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "org_scope_not_found")

    if "category" in changes:
        slug = changes["category"]
        category = await _get_category(db, honor.ministry_id, slug) if slug else None
        honor.category_id = category.id if category else None
    if "name" in changes:
        honor.name = changes["name"].strip()

    for column in (
        "description",
        "difficulty_level",
        "honor_type",
        "estimated_hours",
        "exam_time_limit_minutes",
        "thumbnail_url",
        "image_url",
        "source_url",
        "wiki_title",
        "authority",
        "skill_level",
        "year_introduced",
        "org_scope_id",
    ):
        if column in changes:
            setattr(honor, column, changes[column])
    for column in ("exam_passing_score", "max_exam_attempts", "active"):  # NOT NULL columns
        if changes.get(column) is not None:
            setattr(honor, column, changes[column])

    if payload.requirements is not None:
        # Questions go with their requirement (ON DELETE CASCADE). The editor works on the source
        # language; lists in other languages (imported, with attribution) are left alone.
        await db.execute(
            delete(HonorRequirement).where(
                HonorRequirement.honor_id == honor.id, HonorRequirement.locale == SOURCE_LOCALE
            )
        )
        await _stage_requirements(db, honor.id, payload.requirements)
    if payload.resources is not None:
        await db.execute(delete(HonorResource).where(HonorResource.honor_id == honor.id))
        _stage_resources(db, honor.id, payload.resources)

    honor.updated_at = utcnow()
    record_audit(
        db,
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Updated honor: {honor.name}",
        metadata={"fields": sorted(changes)},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


# honor_translations.locale is VARCHAR(16): a longer tag is a 422, not a 500 at INSERT.
TranslationLocale = Path(pattern=LOCALE_PATTERN, max_length=16)


async def _honor_for_translation(
    db: AsyncSession, honor_id: uuid.UUID, locale: str, user: User
) -> tuple[Honor, str]:
    """
    The honor, locked, and the stored spelling of `locale`. Same gate as update_honor (the
    creator or MASTER_GC) but in any status: a translation is content in parallel to the
    source text that went through review, not a change to it.
    """
    if is_source_locale(locale):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{SOURCE_LOCALE}' is the source language: edit it with PUT /honors/{{honor_id}}",
        )
    honor = await _get_honor_or_404(db, honor_id, lock=True)
    if honor.created_by_id != user.id and not is_master(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the creator can translate this honor"
        )
    return honor, canonical_locale(locale)


@router.put("/{honor_id}/translations/{locale}", response_model=HonorStaffDetail)
async def put_honor_translation(
    honor_id: uuid.UUID,
    payload: HonorTranslationIn,
    request: Request,
    locale: str = TranslationLocale,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or replace the honor's name and description in `locale`."""
    honor, locale = await _honor_for_translation(db, honor_id, locale, current_user)
    # The honor row is locked: two requests for a new locale cannot both INSERT.
    translation = await db.get(HonorTranslation, (honor.id, locale))
    created = translation is None
    if created:
        translation = HonorTranslation(honor_id=honor.id, locale=locale)
        db.add(translation)
    # Attribution (source, source_url, license) of an imported row is kept: a corrected
    # CC BY-SA text is still a derivative of it.
    translation.name = payload.name.strip()
    translation.description = payload.description
    translation.updated_at = utcnow()
    record_audit(
        db,
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"{'Added' if created else 'Replaced'} {locale} translation of honor: {honor.name}",
        metadata={"translation": locale, "created": created},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.delete("/{honor_id}/translations/{locale}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_honor_translation(
    honor_id: uuid.UUID,
    request: Request,
    locale: str = TranslationLocale,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a translation loaded by mistake. The honor then answers in the source text."""
    honor, locale = await _honor_for_translation(db, honor_id, locale, current_user)
    translation = await db.get(HonorTranslation, (honor.id, locale))
    if translation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Translation not found")
    await db.delete(translation)
    # UPDATE, not DELETE: on an HONOR entity, DELETE means the honor was archived.
    record_audit(
        db,
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Removed {locale} translation of honor: {honor.name}",
        metadata={"translation": locale, "removed": True},
        request=request,
    )
    await db.commit()


@router.post("/{honor_id}/submit", response_model=HonorStaffDetail)
async def submit_honor(
    honor_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """DRAFT -> ZONE_REVIEW. The creator, or MASTER_GC on their behalf."""
    honor = await _get_honor_or_404(db, honor_id, lock=True)
    if honor.created_by_id != current_user.id and not is_master(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the creator can submit this honor")
    if honor.status != DRAFT:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only draft honors can be submitted for review"
        )
    await _require_requirements(db, honor)

    honor.status = ZONE_REVIEW
    honor.updated_at = utcnow()
    record_audit(
        db,
        action="SUBMIT",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Submitted honor for zone review: {honor.name}",
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.post("/{honor_id}/review", response_model=HonorStaffDetail)
async def review_honor(
    honor_id: uuid.UUID,
    payload: HonorReviewIn,
    request: Request,
    current_user: User = Depends(require_roles(*ZONE_REVIEWERS)),
    db: AsyncSession = Depends(get_db),
):
    """
    APPROVE:  ZONE_REVIEW -> ASSOCIATION_REVIEW -> PUBLISHED
    REJECT / REQUEST_CHANGES: back to DRAFT
    """
    honor = await _get_honor_or_404(db, honor_id, lock=True)

    if current_user.role not in STAGE_REVIEWERS.get(honor.status, ()):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"You cannot review honors at stage {honor.status}"
        )
    if not await org_in_user_scope(db, current_user, honor.org_scope_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This honor is outside your scope")
    if honor.created_by_id == current_user.id and not is_master(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You cannot review your own honor")

    previous_status = honor.status
    if payload.action == "APPROVE":
        if honor.status == ZONE_REVIEW:
            honor.status = ASSOCIATION_REVIEW
            honor.approved_zone_org_id = current_user.organization_id
        else:
            honor.approved_association_org_id = current_user.organization_id
            _publish(honor)
    else:
        honor.status = DRAFT
    honor.updated_at = utcnow()

    # Review history is append-only: one row per decision.
    db.add(
        HonorReview(
            id=uuid.uuid4(),
            honor_id=honor.id,
            reviewer_id=current_user.id,
            reviewer_name=current_user.name,
            reviewer_role=current_user.role,
            action=payload.action,
            comments=payload.comments,
            reviewed_at=utcnow(),
        )
    )
    record_audit(
        db,
        action=payload.action,
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"{payload.action} honor: {honor.name}",
        metadata={"from": previous_status, "to": honor.status, "comments": payload.comments},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.post("/{honor_id}/publish", response_model=HonorStaffDetail)
async def publish_honor(
    honor_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    """MASTER_GC shortcut: publish from any state except ARCHIVED."""
    honor = await _get_honor_or_404(db, honor_id, lock=True)
    if honor.status == ARCHIVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Archived honors cannot be published")
    await _require_requirements(db, honor)

    previous_status = honor.status
    _publish(honor)
    record_audit(
        db,
        action="PUBLISH",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Published directly (MASTER_GC): {honor.name}",
        metadata={"from": previous_status, "to": PUBLISHED},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.post("/{honor_id}/restore", response_model=HonorStaffDetail)
async def restore_honor(
    honor_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_roles(MASTER_GC)),
    db: AsyncSession = Depends(get_db),
):
    """ARCHIVED -> DRAFT, hidden (`active=false`) until it is published again."""
    honor = await _get_honor_or_404(db, honor_id, lock=True)
    if honor.status != ARCHIVED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "honor_not_archived")
    honor.status = DRAFT
    honor.active = False
    honor.updated_at = utcnow()
    record_audit(
        db,
        action="RESTORE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Restored archived honor to draft: {honor.name}",
        metadata={"from": ARCHIVED, "to": DRAFT},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.post(
    "/{honor_id}/version", response_model=HonorStaffDetail, status_code=status.HTTP_201_CREATED
)
async def create_honor_version(
    honor_id: uuid.UUID,
    payload: HonorVersionCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    New DRAFT row based on a published honor; the published row is archived.
    Both happen in one transaction: there is never a moment with two live
    versions or with none recorded.
    """
    original = await _get_honor_or_404(db, honor_id, lock=True)
    if original.status != PUBLISHED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only published honors can be versioned")

    allowed = original.created_by_id == current_user.id or is_master(current_user)
    if not allowed and current_user.role == ADMIN_ASSOCIATION:
        allowed = await org_in_user_scope(db, current_user, original.org_scope_id)
    if not allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the creator or an admin can create new versions"
        )

    new_version = original.version + 1
    suffix = f"_v{new_version}"
    new_code = None
    if original.code:
        base_code = re.sub(r"_v\d+$", "", original.code)
        new_code = f"{base_code[: CODE_MAX_LENGTH - len(suffix)]}{suffix}"
        if await _code_taken(db, original.ministry_id, new_code):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Version {new_version} already exists")
    base_slug = re.sub(r"-v\d+$", "", original.slug)

    now = utcnow()
    honor = Honor(
        id=uuid.uuid4(),
        ministry_id=original.ministry_id,
        category_id=original.category_id,
        name=original.name,
        slug=await _unique_slug(db, original.ministry_id, f"{base_slug}-v{new_version}"),
        image_url=original.image_url,
        source_url=original.source_url,
        active=True,
        code=new_code,
        description=payload.description or original.description,
        difficulty_level=original.difficulty_level,
        honor_type=original.honor_type,
        status=DRAFT,
        org_scope_id=original.org_scope_id,
        estimated_hours=original.estimated_hours,
        exam_passing_score=original.exam_passing_score,
        exam_time_limit_minutes=original.exam_time_limit_minutes,
        max_exam_attempts=original.max_exam_attempts,
        # Whoever starts the version owns its draft (catalogue rows have no creator).
        created_by_id=current_user.id,
        thumbnail_url=original.thumbnail_url,
        version=new_version,
        previous_version_id=original.id,
        changes_description=payload.changes_description,
        wiki_title=original.wiki_title,
        authority=original.authority,
        skill_level=original.skill_level,
        year_introduced=original.year_introduced,
        created_at=now,
        updated_at=now,
    )
    await _insert_honor(db, honor, f"Version {new_version} already exists")
    if payload.requirements is not None:
        await _stage_requirements(db, honor.id, payload.requirements)
    else:
        await _copy_requirements(db, original.id, honor.id)
    await _copy_resources(db, original.id, honor.id)
    await _copy_translations(db, original.id, honor.id)

    original.status = ARCHIVED
    original.active = False
    original.updated_at = now

    record_audit(
        db,
        action="CREATE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Created new version of honor: {honor.name} (v{new_version})",
        metadata={
            "previous_version_id": str(original.id),
            "changes": payload.changes_description,
        },
        request=request,
    )
    record_audit(
        db,
        action="ARCHIVE",
        entity_type=ENTITY,
        entity_id=original.id,
        actor=current_user,
        details=f"Archived v{original.version}, superseded by v{new_version}",
        metadata={"superseded_by": str(honor.id)},
        request=request,
    )
    await db.commit()
    return await _build_detail(db, honor, staff=True)


@router.delete("/{honor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_honor(
    honor_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete: the honor becomes ARCHIVED. Certificates keep pointing at it."""
    honor = await _get_honor_or_404(db, honor_id, lock=True)
    if honor.created_by_id != current_user.id and not is_master(current_user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the creator or MASTER_GC can delete this honor"
        )
    if honor.status == PUBLISHED and not is_master(current_user):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only MASTER_GC can archive a published honor"
        )

    previous_status = honor.status
    honor.status = ARCHIVED
    honor.active = False
    honor.updated_at = utcnow()
    record_audit(
        db,
        action="DELETE",
        entity_type=ENTITY,
        entity_id=honor.id,
        actor=current_user,
        details=f"Archived honor: {honor.name}",
        metadata={"from": previous_status},
        request=request,
    )
    await db.commit()
