"""Bloque F · F1 — the public catalogue of programs (classes, Master Guide, EMC, CMJA).

Thin on purpose, like every router here: validate, call app/services/programs.py, serialise.
There is deliberately NO create or edit endpoint — an official curriculum enters through
`migrations/import_programs.py` as a DRAFT and is published only after a person has checked
it against the manual in force (decision D2).
"""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user, get_optional_user
from app.models import User
from app.schemas.program import (
    ProgramDetail,
    ProgramKind,
    ProgramListItem,
    ProgramRecommendations,
    ProgramStatusFilter,
)
from app.services import program_recommendations, programs
from app.services.locales import LOCALE_PATTERN

router = APIRouter(prefix="/api/v1/programs", tags=["programs"])


@router.get("", response_model=list[ProgramListItem])
async def list_programs(
    # No default: nothing in block F guesses a ministry (regla 3 of ESTADO.md).
    ministry: str = Query(min_length=2, max_length=60),
    kind: ProgramKind | None = None,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    status_filter: ProgramStatusFilter | None = Query(None, alias="status"),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Public: the PUBLISHED programs of one ministry, named in `locale` when a translation
    exists. An unknown ministry is an empty list, not an error.

    `/admin/clases`: whoever may publish a program (MASTER_GC) filters by `status`
    (`ALL` = every status). For everybody else `status` is ignored: drafts do not exist."""
    statuses = programs.visible_statuses(current_user, status_filter)
    return await programs.list_programs(db, ministry, kind, locale, statuses)


@router.get("/{program_id}", response_model=ProgramDetail)
async def get_program(
    program_id: uuid.UUID,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """Sections -> requirements, with the attribution of every row. A program that is not
    published does not exist for anyone but whoever may publish it."""
    program = await programs.get_visible_program_or_404(db, program_id, current_user)
    return await programs.program_detail(db, program, locale)


@router.get("/{program_id}/recommendations", response_model=ProgramRecommendations)
async def program_recommendations_list(
    program_id: uuid.UUID,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """The honors each requirement asks for — named, from a category, of free choice, or
    mentioned in the text — so a leader can plan the class. Same visibility as the detail."""
    program = await programs.get_visible_program_or_404(db, program_id, current_user)
    items, resolved = await program_recommendations.build_recommendations(db, program, locale)
    return ProgramRecommendations(program_id=str(program.id), locale=resolved, items=items)


@router.post("/{program_id}/publish", response_model=ProgramDetail)
async def publish_program(
    program_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """D2: the switch that makes a curriculum exist for the public."""
    return await programs.publish(db, current_user, program_id, request)


@router.post("/{program_id}/archive", response_model=ProgramDetail)
async def archive_program(
    program_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await programs.archive(db, current_user, program_id, request)
