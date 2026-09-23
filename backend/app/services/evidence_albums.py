"""Álbum de evidencias: a read-only view of block A's evidence.

One album per honor the member is working on or has earned (never a withdrawn one, never
a program: an album is the folder of a specialty, with its patch as the sticker), with
its counters and the four newest photos; and, for one enrollment, every evidence it has.

Nothing here writes: no state changes, no audit row. The files stay in the PRIVATE bucket
and are reached through presigned GETs signed on every call (local HMAC, no network).

Query budget for the albums of a person (spec: at most 4, whatever the number of albums):
  1. enrollments + honor + category + certificate + the three counters (correlated counts)
  2. honor translations, only when an enrollment is not in the source language
  3. the four newest evidences per enrollment (row_number() window)
"""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Certificate,
    Evidence,
    Honor,
    HonorCategory,
    HonorEnrollment,
    HonorTranslation,
    RequirementProgress,
    User,
)
from app.rbac import can_view_enrollment, can_view_portfolio
from app.schemas.portfolio import (
    AlbumCategory,
    AlbumCertificate,
    AlbumHonor,
    EvidenceAlbum,
    EvidenceItem,
    EvidencePreview,
)
from app.services import curriculum, private_storage
from app.services.locales import SOURCE_LOCALE
from app.services.portfolio import ACTIVE, COMPLETE, WITHDRAWN, honor_name_in

MAX_ALBUMS = 200
LATEST_PER_ALBUM = 4
# What an album shows: confirmed photos and PDFs whose object is still in the bucket.
SHOWN_KINDS = ("image", "pdf")


def _shown_evidence():
    return (
        Evidence.status == ACTIVE,
        Evidence.purged_at.is_(None),
        Evidence.kind.in_(SHOWN_KINDS),
    )


def _signed(storage_key: str) -> str:
    try:
        return private_storage.presign_get(
            storage_key, expires_in=private_storage.ALBUM_GET_EXPIRES_SECONDS
        )["url"]
    except private_storage.PrivateStorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, private_storage.NOT_CONFIGURED_DETAIL)


def _verify_url(certificate_no: str) -> str:
    # The same address the certificate's QR carries (app/routers/render.py).
    return f"{settings.PUBLIC_WEB_URL.rstrip('/')}/verify/{certificate_no}"


async def _target(db: AsyncSession, actor: User, user_id: uuid.UUID | None) -> User:
    """Whose albums. Anyone the actor may not see is simply not found: a 403 would tell a
    stranger that the account exists (and these are photos of minors)."""
    if user_id is None or user_id == actor.id:
        return actor
    target = await db.get(User, user_id)
    if target is None or not await can_view_portfolio(db, actor, target):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    return target


async def albums_of(db: AsyncSession, actor: User, user_id: uuid.UUID | None) -> list[EvidenceAlbum]:
    target = await _target(db, actor, user_id)

    per_enrollment = RequirementProgress.enrollment_id == HonorEnrollment.id
    total = select(func.count()).where(per_enrollment).correlate(HonorEnrollment).scalar_subquery()
    done = (
        select(func.count())
        .where(per_enrollment, RequirementProgress.status == COMPLETE)
        .correlate(HonorEnrollment)
        .scalar_subquery()
    )
    evidence_count = (
        select(func.count())
        .select_from(Evidence)
        .join(RequirementProgress, RequirementProgress.id == Evidence.progress_id)
        .where(per_enrollment, *_shown_evidence())
        .correlate(HonorEnrollment)
        .scalar_subquery()
    )
    stmt = (
        select(HonorEnrollment, Honor, HonorCategory, Certificate, total, done, evidence_count)
        .join(Honor, Honor.id == HonorEnrollment.honor_id)       # programs have no honor: no album
        .outerjoin(HonorCategory, HonorCategory.id == Honor.category_id)
        .outerjoin(Certificate, Certificate.id == HonorEnrollment.certificate_id)
        .where(HonorEnrollment.user_id == target.id, HonorEnrollment.status != WITHDRAWN)
        .order_by(HonorEnrollment.updated_at.desc(), HonorEnrollment.id)
        .limit(MAX_ALBUMS)
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    translated = {
        enrollment.honor_id
        for enrollment, *_ in rows
        if enrollment.locale.lower().split("-")[0] != SOURCE_LOCALE
    }
    names: dict[uuid.UUID, dict[str, str]] = {}
    if translated:
        found = select(HonorTranslation.honor_id, HonorTranslation.locale, HonorTranslation.name).where(
            HonorTranslation.honor_id.in_(translated)
        )
        for honor_id, locale, name in await db.execute(found):
            names.setdefault(honor_id, {})[locale] = name

    latest = await _latest(db, [enrollment.id for enrollment, *_ in rows])

    albums = []
    for enrollment, honor, category, certificate, total_count, done_count, evidences in rows:
        albums.append(
            EvidenceAlbum(
                enrollment_id=str(enrollment.id),
                honor=AlbumHonor(
                    id=str(honor.id),
                    name=honor_name_in(honor.name, names.get(honor.id, {}), enrollment.locale),
                    slug=honor.slug,
                    image_url=honor.image_url,
                    category=AlbumCategory(id=str(category.id), name=category.name, slug=category.slug)
                    if category
                    else None,
                ),
                status=enrollment.status,
                requirements_total=total_count,
                requirements_done=done_count,
                evidence_count=evidences,
                latest=latest.get(enrollment.id, []),
                certificate=AlbumCertificate(
                    certificate_no=certificate.certificate_no,
                    issued_date=certificate.issued_date,
                    verify_url=_verify_url(certificate.certificate_no),
                    status=certificate.status,
                )
                if certificate
                else None,
                updated_at=enrollment.updated_at,
            )
        )
    return albums


async def _latest(
    db: AsyncSession, enrollment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[EvidencePreview]]:
    """The newest LATEST_PER_ALBUM shown evidences of each enrollment, in ONE query."""
    rank = func.row_number().over(
        partition_by=RequirementProgress.enrollment_id,
        order_by=(Evidence.created_at.desc(), Evidence.id.desc()),
    )
    ranked = (
        select(
            RequirementProgress.enrollment_id,
            RequirementProgress.requirement_id,
            RequirementProgress.program_requirement_id,
            RequirementProgress.requirement_position,
            Evidence.id,
            Evidence.storage_key,
            Evidence.content_type,
            Evidence.created_at,
            rank.label("rank"),
        )
        .join(RequirementProgress, RequirementProgress.id == Evidence.progress_id)
        .where(RequirementProgress.enrollment_id.in_(enrollment_ids), *_shown_evidence())
        .subquery()
    )
    stmt = (
        select(ranked)
        .where(ranked.c.rank <= LATEST_PER_ALBUM)
        .order_by(ranked.c.enrollment_id, ranked.c.rank)
    )
    latest: dict[uuid.UUID, list[EvidencePreview]] = {}
    for row in await db.execute(stmt):
        url = _signed(row.storage_key)
        latest.setdefault(row.enrollment_id, []).append(
            EvidencePreview(
                id=str(row.id),
                requirement_id=_requirement_id(row.requirement_id, row.program_requirement_id),
                requirement_position=row.requirement_position,
                content_type=row.content_type,
                url=url,
                thumbnail_url=url,
                created_at=row.created_at,
            )
        )
    return latest


def _requirement_id(requirement_id, program_requirement_id) -> str | None:
    source = requirement_id or program_requirement_id
    return str(source) if source else None


async def enrollment_evidence(
    db: AsyncSession, actor: User, enrollment_id: uuid.UUID
) -> list[EvidenceItem]:
    """Every shown evidence of one enrollment, by requirement and then oldest first.

    Same door as the enrollment detail (`can_view_enrollment`): 404 when it does not
    exist, 403 when it does and the actor may not read it.
    """
    enrollment = await db.get(HonorEnrollment, enrollment_id)
    if enrollment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inscripción no encontrada")
    if not await can_view_enrollment(db, actor, enrollment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para ver este portafolio")

    stmt = (
        select(Evidence, RequirementProgress)
        .join(RequirementProgress, RequirementProgress.id == Evidence.progress_id)
        .where(RequirementProgress.enrollment_id == enrollment.id, *_shown_evidence())
        .order_by(RequirementProgress.requirement_position, Evidence.created_at, Evidence.id)
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    # The texts the member saw, resolved exactly like the enrollment detail does.
    specs = await curriculum.enrollment_specs(db, enrollment)
    by_id = {spec.source_id: spec for spec in specs if spec.source_id is not None}
    by_position = {spec.position: spec for spec in specs}

    items = []
    for evidence, progress in rows:
        spec = by_id.get(progress.program_requirement_id or progress.requirement_id) or by_position.get(
            progress.requirement_position
        )
        url = _signed(evidence.storage_key)
        items.append(
            EvidenceItem(
                id=str(evidence.id),
                requirement_id=_requirement_id(progress.requirement_id, progress.program_requirement_id),
                requirement_position=progress.requirement_position,
                content_type=evidence.content_type,
                url=url,
                thumbnail_url=url,
                created_at=evidence.created_at,
                requirement_description=spec.description if spec else None,
                status=progress.status,
                note=evidence.caption,
            )
        )
    return items
