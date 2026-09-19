"""Media upload to Cloudflare R2."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import settings
from app.deps import require_roles
from app.models import User
from app.security import (
    ADMIN_ASSOCIATION,
    ADMIN_DIVISION,
    ADMIN_UNION,
    COORDINATOR_ZONE,
    INSTRUCTOR,
    MASTER_GC,
)
from app.services import storage

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


class UploadResponse(BaseModel):
    url: str
    key: str
    content_type: str
    size_bytes: int


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    folder: str = Form(default=storage.DEFAULT_FOLDER),
    current_user: User = Depends(require_roles(*UPLOAD_ROLES)),
):
    """
    Images (JPEG, PNG, WebP, GIF) up to 10 MB or PDF up to 50 MB.
    `folder`: specialties | patches | general | resources | avatars | logos
    (anything else falls back to `general`).
    SVG: MASTER_GC only, only into `patches` or `logos`, and only when it
    carries no scripts, event handlers or javascript: URLs.
    """
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
        url, key = await storage.upload_bytes(data, content_type, folder)
    except storage.StorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED_DETAIL)
    except storage.StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    return UploadResponse(url=url, key=key, content_type=content_type, size_bytes=len(data))
