"""Bloque F §1.1 — the adapter that lets ONE engine serve two catalogues.

Block A's engine identifies a requirement by `(enrollment_id, requirement_position)` and
hangs evidence off the progress row: it is already agnostic. The only thing tied to honors
was `honor_enrollments.honor_id`. So instead of generalising `honors` (and having to add
`kind = 'HONOR'` to every single honors query in production) a parallel `programs`
catalogue feeds the very same progress and evidence tables through this module.

Everything below returns `RequirementSpec`s. What happens after that — drafts, submission,
evidence, verdicts, READY, the certificate, permissions and audit — is block A's code with
no branch at all.
"""
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Honor,
    HonorEnrollment,
    HonorTranslation,
    Program,
    ProgramRequirement,
    ProgramRequirementText,
    ProgramSection,
    ProgramSectionTranslation,
    ProgramTranslation,
)
from app.services.locales import SOURCE_LOCALE, best_locale, match_locale

PUBLISHED = "PUBLISHED"
FREE, HONOR, PROGRAM, HOURS = "FREE", "HONOR", "PROGRAM", "HOURS"


@dataclass(frozen=True)
class SectionSpec:
    id: uuid.UUID
    position: int
    slug: str
    name: str


@dataclass(frozen=True)
class RequirementSpec:
    """One requirement of an enrollment, whatever catalogue it came from.

    `source_id` is the catalogue row the member saw: `honor_requirements.id` for an honor,
    `program_requirements.id` for a program. `position` is what block A stores.
    """

    position: int
    source_id: uuid.UUID | None
    description: str | None
    instructions: str | None = None
    kind: str = FREE
    evidence_required: bool = False
    label: str | None = None
    section: SectionSpec | None = None
    target_honor_id: uuid.UUID | None = None
    target_category_id: uuid.UUID | None = None
    target_program_id: uuid.UUID | None = None
    target_quantity: float | None = None
    activity_category: str | None = None
    source: str | None = None
    source_url: str | None = None
    license: str | None = None

@dataclass(frozen=True)
class Curriculum:
    """What an enrollment is in, resolved once: one of the two ids, never both."""

    honor_id: uuid.UUID | None
    program_id: uuid.UUID | None
    locale: str
    specs: list[RequirementSpec]


@dataclass(frozen=True)
class Award:
    """What a certificate says was obtained."""

    ministry_id: uuid.UUID | None
    honor_id: uuid.UUID | None
    program_id: uuid.UUID | None
    name: str
    kind: str  # "honor" | "program"


# ----------------------------------------------------------------------------
# Version lineage: a requirement aimed at "Knots v1" is met with "Knots v2"
# ----------------------------------------------------------------------------
_LINEAGE_SQL = """
WITH RECURSIVE up AS (
    SELECT id, previous_version_id FROM {table} WHERE id = :root
    UNION
    SELECT t.id, t.previous_version_id FROM {table} t JOIN up ON t.id = up.previous_version_id
), down AS (
    SELECT id, previous_version_id FROM {table} WHERE id = :root
    UNION
    SELECT t.id, t.previous_version_id FROM {table} t JOIN down ON t.previous_version_id = down.id
)
SELECT id FROM up UNION SELECT id FROM down
"""


async def _lineage_ids(db: AsyncSession, table: str, root: uuid.UUID) -> list[uuid.UUID]:
    rows = await db.execute(text(_LINEAGE_SQL.format(table=table)), {"root": root})
    return [row[0] for row in rows]


async def honor_lineage_ids(db: AsyncSession, honor_id: uuid.UUID) -> list[uuid.UUID]:
    return await _lineage_ids(db, "honors", honor_id)


async def program_lineage_ids(db: AsyncSession, program_id: uuid.UUID) -> list[uuid.UUID]:
    return await _lineage_ids(db, "programs", program_id)


# ----------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------
async def load_honor_specs(
    db: AsyncSession, honor_id: uuid.UUID, requested_locale: str | None
) -> tuple[list[RequirementSpec], str]:
    """Block A's own requirement list, unchanged, wrapped in specs."""
    # Local import: app/services/portfolio.py imports this module. `_requirement_list` stays
    # where it is so block A's list (and its fallbacks) has exactly one implementation.
    from app.services.portfolio import _requirement_list

    listed, locale = await _requirement_list(db, honor_id, requested_locale)
    return [
        RequirementSpec(
            position=position,
            source_id=row.id,
            description=row.description,
            instructions=row.instructions,
            evidence_required=not row.is_theoretical,
            source=row.source,
            source_url=row.source_url,
            license=row.license,
        )
        for position, row in listed
    ], locale


async def program_text_locales(db: AsyncSession, program_id: uuid.UUID) -> list[str]:
    stmt = (
        select(ProgramRequirementText.locale)
        .join(ProgramRequirement, ProgramRequirement.id == ProgramRequirementText.requirement_id)
        .where(ProgramRequirement.program_id == program_id)
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())


async def load_program_specs(
    db: AsyncSession, program_id: uuid.UUID, requested_locale: str | None
) -> tuple[list[RequirementSpec], str]:
    """Sections -> requirements of a program, in the best language for the request.

    The STRUCTURE (position, kind, target) is one row per requirement and never differs
    between languages; only the text does.
    """
    stored = await program_text_locales(db, program_id)
    if not stored:
        return [], SOURCE_LOCALE
    locale = best_locale(stored, requested_locale)

    sections = {
        row.id: row
        for row in (
            await db.execute(select(ProgramSection).where(ProgramSection.program_id == program_id))
        ).scalars()
    }
    section_names: dict[uuid.UUID, str] = {}
    if sections:
        stmt = select(ProgramSectionTranslation).where(
            ProgramSectionTranslation.section_id.in_(sections),
            ProgramSectionTranslation.locale == locale,
        )
        section_names = {row.section_id: row.name for row in (await db.execute(stmt)).scalars()}

    # Columns, not the entity: a requirement with no text in this language joins to NULLs,
    # and SQLAlchemy warns when it is asked to build an object out of that.
    body_columns = (
        ProgramRequirementText.description,
        ProgramRequirementText.instructions,
        ProgramRequirementText.source,
        ProgramRequirementText.source_url,
        ProgramRequirementText.license,
    )
    rows = (
        await db.execute(
            select(ProgramRequirement, *body_columns)
            .outerjoin(
                ProgramRequirementText,
                (ProgramRequirementText.requirement_id == ProgramRequirement.id)
                & (ProgramRequirementText.locale == locale),
            )
            .where(ProgramRequirement.program_id == program_id)
            .order_by(ProgramRequirement.position)
        )
    ).all()

    specs = []
    for requirement, description, instructions, text_source, text_url, text_license in rows:
        section = sections.get(requirement.section_id)
        specs.append(
            RequirementSpec(
                position=requirement.position,
                source_id=requirement.id,
                description=description,
                instructions=instructions,
                kind=requirement.kind,
                # Rule of §1.3: a linked requirement is born practical, because the manual
                # route ("I earned it on paper") demands evidence; the automatic route does
                # not go through rule 1 at all. HOURS is never practical: its only route is
                # the activity log.
                evidence_required=(
                    True
                    if requirement.kind in (HONOR, PROGRAM)
                    else False
                    if requirement.kind == HOURS
                    else requirement.evidence_required
                ),
                label=requirement.label,
                section=SectionSpec(
                    id=section.id,
                    position=section.position,
                    slug=section.slug,
                    name=section_names.get(section.id) or section.name,
                )
                if section
                else None,
                target_honor_id=requirement.target_honor_id,
                target_category_id=requirement.target_category_id,
                target_program_id=requirement.target_program_id,
                target_quantity=float(requirement.target_quantity)
                if requirement.target_quantity is not None
                else None,
                activity_category=requirement.activity_category,
                source=text_source,
                source_url=text_url,
                license=text_license,
            )
        )
    return specs, locale


# ----------------------------------------------------------------------------
# What the portfolio asks for
# ----------------------------------------------------------------------------
async def resolve(db: AsyncSession, honor_id, program_id, requested_locale) -> Curriculum:
    """Validate what the member is enrolling in and hand back its requirement list.

    Integrity rule 6: an enrollment is in an honor OR in a program, never in both nor in
    neither. The schema rejects the shape; this rejects the content.
    """
    if (honor_id is None) == (program_id is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Envía honor_id o program_id, exactamente uno de los dos",
        )
    if program_id is not None:
        program = await db.get(Program, program_id)
        if program is None or program.status != PUBLISHED:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Programa no encontrado")
        specs, locale = await load_program_specs(db, program.id, requested_locale)
        if not specs:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "El programa todavía no tiene requisitos cargados"
            )
        return Curriculum(honor_id=None, program_id=program.id, locale=locale, specs=specs)

    honor = await db.get(Honor, honor_id)
    if honor is None or honor.status != PUBLISHED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Especialidad no encontrada")
    specs, locale = await load_honor_specs(db, honor.id, requested_locale)
    if not specs:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La especialidad todavía no tiene requisitos cargados"
        )
    return Curriculum(honor_id=honor.id, program_id=None, locale=locale, specs=specs)


async def enrollment_specs(db: AsyncSession, enrollment: HonorEnrollment) -> list[RequirementSpec]:
    """The requirement list of an enrollment, in the language it was started in."""
    if enrollment.program_id is not None:
        specs, _ = await load_program_specs(db, enrollment.program_id, enrollment.locale)
        return specs
    specs, _ = await load_honor_specs(db, enrollment.honor_id, enrollment.locale)
    return specs


async def program_name(db: AsyncSession, program: Program, locale: str | None) -> str:
    """The program's name in `locale`, falling back to the source text exactly as honors do."""
    if locale and locale.lower().split("-")[0] == SOURCE_LOCALE:
        return program.name
    stmt = select(ProgramTranslation).where(ProgramTranslation.program_id == program.id)
    names = {row.locale: row.name for row in (await db.execute(stmt)).scalars()}
    chosen = match_locale(list(names), locale) if names else None
    return names.get(chosen) or program.name


async def award_for(db: AsyncSession, enrollment: HonorEnrollment) -> Award:
    """What the certificate of this enrollment says was obtained (§1.7)."""
    if enrollment.program_id is not None:
        program = await db.get(Program, enrollment.program_id)
        return Award(
            ministry_id=program.ministry_id,
            honor_id=None,
            program_id=program.id,
            name=await program_name(db, program, enrollment.locale),
            kind="program",
        )
    honor = await db.get(Honor, enrollment.honor_id)
    name = honor.name
    if enrollment.locale.lower().split("-")[0] != SOURCE_LOCALE:
        stmt = select(HonorTranslation).where(HonorTranslation.honor_id == honor.id)
        names = {row.locale: row.name for row in (await db.execute(stmt)).scalars()}
        chosen = match_locale(list(names), enrollment.locale) if names else None
        name = names.get(chosen) or honor.name
    return Award(
        ministry_id=honor.ministry_id,
        honor_id=honor.id,
        program_id=None,
        name=name,
        kind="honor",
    )
