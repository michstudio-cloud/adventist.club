"""Light per-event branding (027_event_branding.sql): logo, main colour and accent.

Colours are validated by the schema (`#RRGGBB`, stored upper-case) and edited through the
event PATCH. The logo follows the club-logo pattern (`app/services/club_logo.py`, same
limits): the browser sends the square it cropped, WebP/PNG/JPEG, ≤ 512×512 px, ≤ 300 KB,
stored at `events/<event_id>/brand-<sha256[:16]>.<ext>` in the PUBLIC media bucket, so every
new logo is a new URL. Only coordination changes it (the router checks).

A duplicated event copies the URL of its source's logo, so a logo file is deleted only when
no event points at it any more (`drop_unused`).
"""
from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request, UploadFile, status
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Event, User
from app.security import utcnow
from app.services import club_logo, storage
from app.services.audit import record_audit

CODES = "event_logo"
FOLDER = "events"
STORAGE_NOT_CONFIGURED = "Almacenamiento no configurado"
NOT_OURS = "event_logo_not_platform"
ENTITY_EVENT = "EVENT"


def key_for(event: Event, data: bytes, extension: str) -> str:
    return f"{FOLDER}/{event.id}/brand-{hashlib.sha256(data).hexdigest()[:16]}{extension}"


def is_platform_logo(url: str) -> bool:
    """A URL an event may point its logo at through PATCH: our public bucket, under
    `events/` (an uploaded event logo, possibly of the event it was duplicated from) or
    `logos/` (the shared logo library)."""
    base = f"{(settings.R2_PUBLIC_URL or '').rstrip('/')}/"
    if not settings.R2_PUBLIC_URL or not url.startswith(base):
        return False
    rest = url[len(base):]
    return (bool(rest) and ".." not in rest and rest.startswith((f"{FOLDER}/", "logos/"))
            and not any(char.isspace() for char in url))


def check_url(url: str | None) -> str | None:
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    if not is_platform_logo(url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, NOT_OURS)
    return url


def own_key(url: str | None) -> str | None:
    """The bucket key of an uploaded event logo (`events/…`); None for anything else."""
    key = storage.key_from_public_url(url)
    return key if key and key.startswith(f"{FOLDER}/") else None


async def store_logo(db: AsyncSession, actor: User, event: Event, file: UploadFile,
                     request: Request | None) -> str | None:
    """Uploads and points the event at it. -> the previous logo URL (to `drop_unused` after
    the commit)."""
    if not settings.storage_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED)
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if file.size is not None and file.size > club_logo.LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{CODES}_too_large")
    data = await file.read(club_logo.LOGO_MAX_BYTES + 1)
    extension = club_logo.validate(data, content_type, codes=CODES)
    try:
        url, key = await storage.upload_bytes_at(key_for(event, data, extension), data, content_type)
    except storage.StorageNotConfigured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_NOT_CONFIGURED)
    except storage.StorageError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    previous = event.brand_logo_url
    event.brand_logo_url = url
    event.updated_at = utcnow()
    record_audit(db, action="EVENT_BRAND_LOGO_UPDATE", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, details=event.name,
                 metadata={"key": key, "size_bytes": len(data), "content_type": content_type,
                           "previous": previous},
                 request=request)
    return previous if previous != url else None


def clear_logo(db: AsyncSession, actor: User, event: Event, request: Request | None) -> str | None:
    previous = event.brand_logo_url
    if previous is None:
        return None
    event.brand_logo_url = None
    event.updated_at = utcnow()
    record_audit(db, action="EVENT_BRAND_LOGO_DELETE", entity_type=ENTITY_EVENT, entity_id=event.id,
                 actor=actor, details=event.name, metadata={"previous": previous}, request=request)
    return previous


async def drop_unused(db: AsyncSession, url: str | None) -> None:
    """Deletes an uploaded event logo once no event points at it (after the commit)."""
    key = own_key(url)
    if key is None:
        return
    if await db.scalar(select(exists().where(Event.brand_logo_url == url))):
        return
    await storage.delete_quietly(key)
