"""Bloque F · F1 — the catalogue of programs and everything the portfolio needs to show a
program enrollment as a digital card.

Where the rules live:
  * who publishes            -> app/rbac.py (`can_publish_program`)
  * who issues an investiture -> app/rbac.py (`can_issue`, branch on `programs.issuer_level`)
  * what a requirement is     -> app/services/curriculum.py (the adapter)
  * the enrollment itself     -> app/services/portfolio.py, unchanged

This module never touches `honors`, `honor_requirements` or any honors query: a program is
not an honor and must never be listed, searched or certified as one.
"""
import uuid

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.certificates.render import PROGRAM_KIND, Template, TemplateError, list_templates, load_template
from app.models import (
    Certificate,
    Honor,
    HonorCategory,
    HonorEnrollment,
    Ministry,
    Program,
    ProgramRequirement,
    RequirementProgress,
    User,
)
from app.rbac import can_publish_program
from app.schemas.program import (
    Attribution,
    CategoryTargetRef,
    EnrollmentSection,
    HonorTargetRef,
    ProgramDetail,
    ProgramListItem,
    ProgramRef,
    ProgramRequirementOut,
    ProgramSectionOut,
    RequirementTarget,
    SatisfiedBy,
)
from app.security import utcnow
from app.services import curriculum, program_recommendations
from app.services.audit import record_audit

DRAFT, PUBLISHED, ARCHIVED = "DRAFT", "PUBLISHED", "ARCHIVED"
PROGRAM = "PROGRAM"
DEFAULT_CLASS_TEMPLATE = "investidura-clase"
NO_TEMPLATE_DETAIL = "No hay plantilla de investidura para este ministerio"


# ----------------------------------------------------------------------------
# Lookups
# ----------------------------------------------------------------------------
async def _ministry_slugs(db: AsyncSession, ministry_ids) -> dict[uuid.UUID, str]:
    ids = {i for i in ministry_ids if i}
    if not ids:
        return {}
    rows = await db.execute(select(Ministry.id, Ministry.slug).where(Ministry.id.in_(ids)))
    return dict(rows.all())


async def _get_ministry(db: AsyncSession, slug: str) -> Ministry | None:
    stmt = select(Ministry).where(Ministry.slug == slug, Ministry.status == "active")
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_program_or_404(db: AsyncSession, program_id: uuid.UUID) -> Program:
    program = await db.get(Program, program_id)
    if program is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programa no encontrado")
    return program


async def get_visible_program_or_404(
    db: AsyncSession, program_id: uuid.UUID, actor: User | None
) -> Program:
    """A program that is not published does not exist for anyone but whoever may publish it."""
    program = await get_program_or_404(db, program_id)
    if program.status != PUBLISHED and not can_view_unpublished(actor):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Programa no encontrado")
    return program


ALL_STATUSES = (DRAFT, PUBLISHED, ARCHIVED)


def visible_statuses(actor: User | None, requested: str | None) -> tuple[str, ...]:
    """Which statuses the catalogue lists for `actor`. Only whoever may publish chooses
    (`ALL` = every one); without a filter, and for everybody else, it is the public list."""
    if requested is None or not can_view_unpublished(actor):
        return (PUBLISHED,)
    if requested == "ALL":
        return ALL_STATUSES
    return (requested,)


async def _requirement_counts(db: AsyncSession, program_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not program_ids:
        return {}
    stmt = (
        select(ProgramRequirement.program_id, func.count())
        .where(ProgramRequirement.program_id.in_(program_ids))
        .group_by(ProgramRequirement.program_id)
    )
    return dict((await db.execute(stmt)).all())


# ----------------------------------------------------------------------------
# Catalogue
# ----------------------------------------------------------------------------
async def list_programs(
    db: AsyncSession,
    ministry_slug: str,
    kind: str | None,
    locale: str | None,
    statuses: tuple[str, ...] = (PUBLISHED,),
) -> list[ProgramListItem]:
    """Programs of ONE ministry in `statuses` (the public: published only). `ministry` is
    mandatory at the router: nothing in block F carries a hidden default ministry."""
    ministry = await _get_ministry(db, ministry_slug)
    if ministry is None:
        return []
    conditions = [Program.ministry_id == ministry.id, Program.status.in_(statuses)]
    if kind:
        conditions.append(Program.kind == kind)
    rows = (
        await db.execute(
            select(Program).where(*conditions).order_by(Program.sort_order, Program.name, Program.id)
        )
    ).scalars().all()
    counts = await _requirement_counts(db, [row.id for row in rows])
    return [
        ProgramListItem(
            id=str(row.id),
            slug=row.slug,
            name=await curriculum.program_name(db, row, locale),
            kind=row.kind,
            ministry=ministry.slug,
            image_url=row.image_url,
            sort_order=row.sort_order,
            authority=row.authority,
            status=row.status,
            version=row.version,
            requirement_count=counts.get(row.id, 0),
        )
        for row in rows
    ]


async def _target_out(
    db: AsyncSession, spec: curriculum.RequirementSpec, locale: str | None
) -> RequirementTarget | None:
    if spec.kind == curriculum.FREE:
        return None
    honor = await db.get(Honor, spec.target_honor_id) if spec.target_honor_id else None
    category = await db.get(HonorCategory, spec.target_category_id) if spec.target_category_id else None
    target_program = await db.get(Program, spec.target_program_id) if spec.target_program_id else None
    return RequirementTarget(
        kind=spec.kind,
        honor=HonorTargetRef(id=str(honor.id), name=honor.name, slug=honor.slug, image_url=honor.image_url)
        if honor
        else None,
        category=CategoryTargetRef(id=str(category.id), name=category.name, slug=category.slug)
        if category
        else None,
        program=await _program_ref(db, target_program, locale) if target_program else None,
        quantity=spec.target_quantity,
        activity_category=spec.activity_category,
        open_choice=spec.kind == curriculum.HONOR and honor is None,
    )


async def _program_ref(db: AsyncSession, program: Program, locale: str | None) -> ProgramRef:
    slugs = await _ministry_slugs(db, [program.ministry_id])
    return ProgramRef(
        id=str(program.id),
        slug=program.slug,
        name=await curriculum.program_name(db, program, locale),
        kind=program.kind,
        image_url=program.image_url,
        ministry=slugs.get(program.ministry_id),
    )


async def program_detail(
    db: AsyncSession, program: Program, locale: str | None
) -> ProgramDetail:
    specs, resolved = await curriculum.load_program_specs(db, program.id, locale)
    slugs = await _ministry_slugs(db, [program.ministry_id])
    sections: dict[uuid.UUID, ProgramSectionOut] = {}
    for spec in specs:
        if spec.section is None:
            continue
        section = sections.setdefault(
            spec.section.id,
            ProgramSectionOut(
                position=spec.section.position,
                slug=spec.section.slug,
                name=spec.section.name,
                requirements=[],
            ),
        )
        section.requirements.append(
            ProgramRequirementOut(
                position=spec.position,
                label=spec.label or str(spec.position),
                description=spec.description,
                instructions=spec.instructions,
                kind=spec.kind,
                evidence_required=spec.evidence_required,
                target=await _target_out(db, spec, locale),
                attribution=Attribution(
                    source=spec.source, source_url=spec.source_url, license=spec.license
                )
                if spec.source or spec.source_url or spec.license
                else None,
            )
        )
    return ProgramDetail(
        id=str(program.id),
        slug=program.slug,
        name=await curriculum.program_name(db, program, locale),
        kind=program.kind,
        ministry=slugs.get(program.ministry_id, ""),
        image_url=program.image_url,
        sort_order=program.sort_order,
        authority=program.authority,
        status=program.status,
        version=program.version,
        requirement_count=len(specs),
        description=program.description,
        issuer_level=program.issuer_level,
        locale=resolved,
        attribution=Attribution(
            source=program.source, source_url=program.source_url, license=program.license
        )
        if program.source or program.source_url or program.license
        else None,
        sections=sorted(sections.values(), key=lambda s: s.position),
        recommendations_count=await program_recommendations.count_recommendations(
            db, program, specs, resolved
        ),
    )


def can_view_unpublished(actor: User | None) -> bool:
    return actor is not None and can_publish_program(actor)


# ----------------------------------------------------------------------------
# DRAFT -> PUBLISHED -> ARCHIVED (D2: nothing reaches the public until a person
# has checked it against the manual in force)
# ----------------------------------------------------------------------------
async def _unpublished_targets(db: AsyncSession, program_id: uuid.UUID) -> list[str]:
    """Requirements pointing at something that is not published yet."""
    problems = []
    rows = (
        await db.execute(
            select(ProgramRequirement).where(ProgramRequirement.program_id == program_id)
        )
    ).scalars().all()
    for row in rows:
        if row.target_honor_id:
            honor = await db.get(Honor, row.target_honor_id)
            if honor is None or honor.status != PUBLISHED:
                problems.append(f"{row.label}: la especialidad de destino no está publicada")
        if row.target_program_id:
            target = await db.get(Program, row.target_program_id)
            if target is None or target.status != PUBLISHED:
                problems.append(f"{row.label}: el programa de destino no está publicado")
    return problems


async def publish(
    db: AsyncSession, actor: User, program_id: uuid.UUID, request: Request | None
) -> ProgramDetail:
    program = await get_program_or_404(db, program_id)
    _require_publisher(actor)
    if program.status == PUBLISHED:
        return await program_detail(db, program, None)
    if program.status == ARCHIVED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Un programa archivado no se vuelve a publicar: crea una versión nueva"
        )
    specs, _ = await curriculum.load_program_specs(db, program.id, None)
    if not specs:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El programa todavía no tiene requisitos cargados"
        )
    problems = await _unpublished_targets(db, program.id)
    if problems:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No se puede publicar: " + "; ".join(problems),
        )
    program.status = PUBLISHED
    program.published_at = utcnow()
    program.updated_at = utcnow()
    record_audit(
        db,
        action="PROGRAM_PUBLISH",
        entity_type=PROGRAM,
        entity_id=program.id,
        actor=actor,
        metadata={"slug": program.slug, "version": program.version, "authority": program.authority,
                  "requirements": len(specs)},
        request=request,
    )
    await db.commit()
    return await program_detail(db, program, None)


async def archive(
    db: AsyncSession, actor: User, program_id: uuid.UUID, request: Request | None
) -> ProgramDetail:
    program = await get_program_or_404(db, program_id)
    _require_publisher(actor)
    if program.status == ARCHIVED:
        return await program_detail(db, program, None)
    program.status = ARCHIVED
    program.updated_at = utcnow()
    record_audit(
        db,
        action="PROGRAM_ARCHIVE",
        entity_type=PROGRAM,
        entity_id=program.id,
        actor=actor,
        metadata={"slug": program.slug, "version": program.version},
        request=request,
    )
    await db.commit()
    return await program_detail(db, program, None)


def _require_publisher(actor: User) -> None:
    if not can_publish_program(actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo MASTER_GC publica o archiva un programa oficial"
        )


# ----------------------------------------------------------------------------
# What app/services/portfolio.py asks this module for (one call per hunk)
# ----------------------------------------------------------------------------
async def program_refs(
    db: AsyncSession, enrollments: list[HonorEnrollment]
) -> dict[uuid.UUID, ProgramRef]:
    """The program of each program enrollment, named in the enrollment's language."""
    ids = {e.program_id for e in enrollments if e.program_id}
    if not ids:
        return {}
    rows = {
        row.id: row for row in (await db.execute(select(Program).where(Program.id.in_(ids)))).scalars()
    }
    slugs = await _ministry_slugs(db, [row.ministry_id for row in rows.values()])
    refs = {}
    for enrollment in enrollments:
        program = rows.get(enrollment.program_id) if enrollment.program_id else None
        if program is None:
            continue
        refs[enrollment.id] = ProgramRef(
            id=str(program.id),
            slug=program.slug,
            name=await curriculum.program_name(db, program, enrollment.locale),
            kind=program.kind,
            image_url=program.image_url,
            ministry=slugs.get(program.ministry_id),
        )
    return refs


def sections_of(
    specs: list[curriculum.RequirementSpec], progress_rows: list[RequirementProgress]
) -> list[EnrollmentSection] | None:
    """The card: sections in order, each with the positions it holds and its counters.
    None for an honor enrollment, so block A's payload does not grow a meaningless key."""
    if not any(spec.section for spec in specs):
        return None
    complete_positions = {p.requirement_position for p in progress_rows if p.status == "COMPLETE"}
    known = {p.requirement_position for p in progress_rows}
    grouped: dict[uuid.UUID, EnrollmentSection] = {}
    for spec in specs:
        if spec.section is None or spec.position not in known:
            continue
        section = grouped.setdefault(
            spec.section.id,
            EnrollmentSection(
                position=spec.section.position,
                slug=spec.section.slug,
                name=spec.section.name,
                positions=[],
                complete=0,
                total=0,
            ),
        )
        section.positions.append(spec.position)
        section.total += 1
        if spec.position in complete_positions:
            section.complete += 1
    return sorted(grouped.values(), key=lambda s: s.position)


async def _satisfied_by(
    db: AsyncSession, progress_rows: list[RequirementProgress]
) -> dict[uuid.UUID, SatisfiedBy]:
    """Which achievement completed each automatically completed requirement (F2)."""
    ids = {p.satisfied_by_enrollment_id for p in progress_rows if p.satisfied_by_enrollment_id}
    if not ids:
        return {}
    sources = {
        row.id: row
        for row in (
            await db.execute(select(HonorEnrollment).where(HonorEnrollment.id.in_(ids)))
        ).scalars()
    }
    certificates = {
        row.enrollment_id: row
        for row in (
            await db.execute(select(Certificate).where(Certificate.enrollment_id.in_(ids)))
        ).scalars()
    }
    out = {}
    for progress in progress_rows:
        source = sources.get(progress.satisfied_by_enrollment_id)
        if source is None:
            continue
        award = await curriculum.award_for(db, source)
        certificate = certificates.get(source.id)
        out[progress.id] = SatisfiedBy(
            type=award.kind,
            name=award.name,
            certificate_no=certificate.certificate_no if certificate else None,
        )
    return out


async def _approved_totals(db: AsyncSession, enrollment: HonorEnrollment) -> dict[str, float]:
    """Approved hours / attendances that count for this enrollment, by category (F2)."""
    from app.services import activity

    return await activity.approved_totals(db, enrollment)


def require_manual_route(progress: RequirementProgress) -> None:
    """F2, rule 8: a requirement of HOURS has ONE route, the activity log. Neither the member
    sends it nor a reviewer signs it — otherwise the bar and the verdict would disagree."""
    if progress.kind == curriculum.HOURS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Este requisito se completa con horas aprobadas: regístralas y pide su aprobación",
        )


async def requirement_extras(
    db: AsyncSession,
    enrollment: HonorEnrollment,
    progress_rows: list[RequirementProgress],
    specs: list[curriculum.RequirementSpec],
) -> dict[uuid.UUID, dict]:
    """The fields a program requirement adds to block A's `RequirementOut`, by progress id.

    Empty for an honor enrollment: block A's serializer keeps answering exactly as before.
    """
    if enrollment.program_id is None:
        return {}
    by_position = {spec.position: spec for spec in specs}
    satisfied = await _satisfied_by(db, progress_rows)
    totals = await _approved_totals(db, enrollment)
    extras: dict[uuid.UUID, dict] = {}
    for progress in progress_rows:
        spec = by_position.get(progress.requirement_position)
        entry: dict = {
            "kind": progress.kind,
            "label": spec.label if spec and spec.label else str(progress.requirement_position),
            "satisfied_by": satisfied.get(progress.id),
        }
        if spec is not None and spec.kind != curriculum.FREE:
            entry["target"] = await _target_out(db, spec, enrollment.locale)
        if spec is not None and spec.kind == curriculum.HOURS and spec.target_quantity:
            entry["quantity"] = {
                "approved": totals.get(spec.activity_category, 0.0),
                "target": spec.target_quantity,
            }
        extras[progress.id] = entry
    return extras


# ----------------------------------------------------------------------------
# The investiture certificate (§1.7)
# ----------------------------------------------------------------------------
async def program_template(
    db: AsyncSession, slug: str | None, award: curriculum.Award
) -> Template:
    """The template an investiture is printed on: one of kind `program` for that ministry.

    Without one the answer is 409, never a certificate on an honor template: an investiture
    that looks like an honor certificate is exactly what §1.7 forbids.
    """
    slugs = await _ministry_slugs(db, [award.ministry_id])
    ministry = slugs.get(award.ministry_id)
    if slug is not None:
        try:
            template = load_template(slug)
        except TemplateError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        if not template.serves(ministry, PROGRAM_KIND):
            raise HTTPException(status.HTTP_409_CONFLICT, NO_TEMPLATE_DETAIL)
        return template
    # No slug: the investiture template of this ministry, preferring the generic class one.
    candidates = [t for t in list_templates() if t.serves(ministry, PROGRAM_KIND)]
    candidates.sort(key=lambda t: (t.slug != DEFAULT_CLASS_TEMPLATE, t.slug))
    if not candidates:
        raise HTTPException(status.HTTP_409_CONFLICT, NO_TEMPLATE_DETAIL)
    return candidates[0]
