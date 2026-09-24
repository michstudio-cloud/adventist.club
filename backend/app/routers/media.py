"""Media upload to Cloudflare R2."""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rbac import profile_is_minor
from app.security import (
    ADMIN_ASSOCIATION,
    ADMIN_DIVISION,
    ADMIN_UNION,
    CLUB_DIRECTOR,
    CLUB_SECRETARY,
    COORDINATOR_ZONE,
    INSTRUCTOR,
    MASTER_GC,
    utcnow,
)
from app.services import club_logo, storage
from app.services.audit import record_audit
from app.services.profiles import guardianships_of

router = APIRouter(prefix="/api/v1/media", tags=["media"])

UPLOAD_ROLES = (
    INSTRUCTOR,
    COORDINATOR_ZONE,
    ADMIN_ASSOCIATION,
    ADMIN_UNION,
    ADMIN_DIVISION,
    MASTER_GC,
)
STORAGE_NOT_CONFIGURED_DETAIL = "Almacenamiento no configurado"
CLUB_LOGO_FOLDER = "logos"
CLUB_LOGO_ROLES = (CLUB_DIRECTOR, CLUB_SECRETARY)


class UploadResponse(BaseModel):
    url: str
    key: str
    content_type: str
    size_bytes: int


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    folder: str = Form(default=storage.DEFAULT_FOLDER),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Images (JPEG, PNG, WebP, GIF) up to 10 MB or PDF up to 50 MB.
    `folder`: specialties | patches | general | resources | avatars | covers | logos
    (anything else falls back to `general`).
    SVG: MASTER_GC only, only into `patches` or `logos`, and only when it
    carries no scripts, event handlers or javascript: URLs.
    `avatars` and `covers` (Bloque G): any signed-in person, raster images only; a minor
    never uploads a cover, nor a photo until a guardian allowed it.
    `logos` (Bloque H): also CLUB_DIRECTOR and CLUB_SECRETARY, raster images only.
    """
    target_folder = storage.resolve_folder(folder)
    profile_media = target_folder in storage.PROFILE_FOLDERS
    # Bloque H §5: the direction and the secretary of a club upload its logo, and only that.
    club_logo = target_folder == CLUB_LOGO_FOLDER and current_user.role in CLUB_LOGO_ROLES
    if current_user.role not in UPLOAD_ROLES and not profile_media and not club_logo:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Insufficient permissions. Required roles: {list(UPLOAD_ROLES)}",
        )
    if profile_media:
        guardians = await guardianships_of(db, current_user.id)
        if profile_is_minor(current_user, has_guardian=bool(guardians)):
            if target_folder == "covers":
                raise HTTPException(status.HTTP_403_FORBIDDEN, "minor_cannot_upload_cover")
            if not current_user.guardian_allows_avatar:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "minor_avatar_not_allowed")
    if not settings.storage_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED_DETAIL)

    content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if content_type not in storage.ALLOWED_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Tipo de archivo no permitido: {content_type}. "
            "Tipos aceptados: JPEG, PNG, WebP, GIF, PDF",
        )

    is_svg = content_type == storage.SVG_TYPE
    if is_svg:
        if current_user.role != MASTER_GC:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Solo MASTER_GC puede subir archivos SVG"
            )
        if storage.resolve_folder(folder) not in storage.SVG_FOLDERS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Los archivos SVG solo se aceptan en las carpetas: patches, logos",
            )

    if profile_media and content_type not in storage.ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "profile_media_images_only")
    if club_logo and current_user.role not in UPLOAD_ROLES and content_type not in storage.ALLOWED_IMAGE_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "club_logo_images_only")

    max_size = storage.max_size_for(content_type)
    too_large = HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        f"El archivo supera el tamaño máximo de {max_size // (1024 * 1024)} MB",
    )
    if file.size is not None and file.size > max_size:
        raise too_large
    data = await file.read(max_size + 1)
    if len(data) > max_size:
        raise too_large
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El archivo está vacío")
    if not storage.content_matches_type(data, content_type):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "El contenido del archivo no corresponde al tipo declarado",
        )
    if is_svg and not storage.svg_is_safe(data):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "El SVG contiene contenido activo (scripts, eventos on* o enlaces javascript:)",
        )

    try:
        url, key = await storage.upload_bytes(data, content_type, target_folder)
    except storage.StorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED_DETAIL)
    except storage.StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    return UploadResponse(url=url, key=key, content_type=content_type, size_bytes=len(data))


# ----------------------------------------------------------------------------
# The logo of a club (022_club_ministries.sql)
# ----------------------------------------------------------------------------
class ClubLogoOut(BaseModel):
    club_id: str
    logo_url: str | None
    key: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None


@router.post(
    "/clubs/{club_id}/logo", response_model=ClubLogoOut, status_code=status.HTTP_201_CREATED
)
async def upload_club_logo(
    club_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload the club's logo and point the club at it, in one step. Only the club's
    director and the administration of its association or above (403 `club_logo_forbidden`).
    The browser sends the square it cropped: WebP (PNG/JPEG if it cannot write WebP), at most
    512×512 px and 300 KB (413 `club_logo_too_large`, 422 `club_logo_too_big`). Stored at
    `clubs/<id>/logo-<hash>.webp`; the previous logo of that folder is deleted."""
    club = await club_logo.get_club(db, club_id)
    await club_logo.require_logo_rights(db, current_user, club)
    if not settings.storage_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED_DETAIL)

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if file.size is not None and file.size > club_logo.LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, club_logo.LOGO_TOO_LARGE)
    data = await file.read(club_logo.LOGO_MAX_BYTES + 1)
    extension = club_logo.validate(data, content_type)

    key = club_logo.key_for(club, data, extension)
    try:
        url, key = await storage.upload_bytes_at(key, data, content_type)
    except storage.StorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED_DETAIL)
    except storage.StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    previous = club_logo.own_key(club, club.logo_url)
    club.logo_url = url
    club_logo.drop_legacy(club)
    club.updated_at = utcnow()
    record_audit(
        db,
        action="CLUB_LOGO_UPDATE",
        entity_type="ORGANIZATION",
        entity_id=club.id,
        actor=current_user,
        details=f"Logo of {club.name} updated",
        metadata={"key": key, "size_bytes": len(data), "content_type": content_type},
        request=request,
    )
    await db.commit()
    if previous and previous != key:
        await storage.delete_quietly(previous)
    return ClubLogoOut(
        club_id=str(club.id), logo_url=url, key=key, content_type=content_type, size_bytes=len(data)
    )


@router.delete("/clubs/{club_id}/logo", response_model=ClubLogoOut)
async def delete_club_logo(
    club_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove the club's logo (the club goes back to its initial on the ministry's colour).
    Same rights as the upload."""
    club = await club_logo.get_club(db, club_id)
    await club_logo.require_logo_rights(db, current_user, club)
    previous = club_logo.own_key(club, club.logo_url)
    had_logo = club.logo_url is not None
    club.logo_url = None
    club_logo.drop_legacy(club)
    if had_logo:
        club.updated_at = utcnow()
        record_audit(
            db,
            action="CLUB_LOGO_DELETE",
            entity_type="ORGANIZATION",
            entity_id=club.id,
            actor=current_user,
            details=f"Logo of {club.name} removed",
            request=request,
        )
    await db.commit()
    if previous:
        await storage.delete_quietly(previous)
    return ClubLogoOut(club_id=str(club.id), logo_url=None)
