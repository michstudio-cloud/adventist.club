"""The logo of a club (022_club_ministries.sql: `organizations.logo_url`).

Who changes it (owner, 2026-09-24): the club's DIRECTOR and the administration of its
association or above (association, union, division, MASTER_GC) within their scope. Nobody
else — not the secretary, not a zone coordinator, not a member.

What is stored: the square the browser already cropped (`lib/profile-image.ts`, kind
`logo`): WebP — or PNG/JPEG when the browser cannot write WebP — at most 512×512 px and
300 KB, under `clubs/<club_id>/logo-<sha256[:16]>.<ext>` in the PUBLIC media bucket. The
hash in the name makes every new logo a new URL, so caches never serve the old one.
"""
import hashlib
import io
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Organization, User
from app.rbac import is_master, org_in_user_scope
from app.security import CLUB_DIRECTOR
from app.services import placement, storage

CLUB_TYPE = "club"
LIVE_STATUSES = ("active", "pending")

LOGO_TYPES = {"image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg"}
LOGO_MAX_BYTES = 300 * 1024
LOGO_MAX_PX = 512

# Error details the frontend reads as codes.
LOGO_FORBIDDEN = "club_logo_forbidden"
LOGO_TOO_LARGE = "club_logo_too_large"
LOGO_TOO_BIG = "club_logo_too_big"
LOGO_BAD_TYPE = "club_logo_images_only"
LOGO_UNREADABLE = "club_logo_unreadable"
CLUB_NOT_LIVE = "club_not_active"
CLUB_NOT_FOUND = "Club not found"


async def get_club(db: AsyncSession, club_id: uuid.UUID) -> Organization:
    club = await db.get(Organization, club_id)
    if club is None or club.type != CLUB_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CLUB_NOT_FOUND)
    return club


async def can_change_logo(db: AsyncSession, actor: User, club: Organization) -> bool:
    if is_master(actor):
        return True
    if placement.is_structure_admin(actor):
        return await org_in_user_scope(db, actor, club.id)
    return actor.role == CLUB_DIRECTOR and actor.organization_id == club.id


async def require_logo_rights(db: AsyncSession, actor: User, club: Organization) -> None:
    if not await can_change_logo(db, actor, club):
        raise HTTPException(status.HTTP_403_FORBIDDEN, LOGO_FORBIDDEN)
    if club.status not in LIVE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, CLUB_NOT_LIVE)


def validate(data: bytes, content_type: str, *, codes: str = "club_logo") -> str:
    """-> the extension. 415 for anything but a real WebP/PNG/JPEG, 413 over 300 KB, 422 over
    512 px on a side. `codes` prefixes the error details (the event logo reuses these rules
    with `event_logo_*`)."""
    extension = LOGO_TYPES.get(content_type)
    if extension is None or not data or not storage.content_matches_type(data, content_type):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"{codes}_images_only")
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{codes}_too_large")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"{codes}_unreadable")
    if width > LOGO_MAX_PX or height > LOGO_MAX_PX or width < 1 or height < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{codes}_too_big")
    return extension


def key_for(club: Organization, data: bytes, extension: str) -> str:
    return f"clubs/{club.id}/logo-{hashlib.sha256(data).hexdigest()[:16]}{extension}"


def own_key(club: Organization, url: str | None) -> str | None:
    """The bucket key of a logo this module stored for `club`, or None (a logo of the
    `logos/` folder of bloque H, or of another host, is never deleted from here)."""
    key = storage.key_from_public_url(url)
    return key if key and key.startswith(f"clubs/{club.id}/") else None


def is_platform_logo(url: str) -> bool:
    """A URL a club may point its logo at: our bucket, `logos/` (bloque H) or `clubs/`."""
    base = f"{(settings.R2_PUBLIC_URL or '').rstrip('/')}/"
    if not settings.R2_PUBLIC_URL or not url.startswith(base):
        return False
    rest = url[len(base):]
    return bool(rest) and ".." not in rest and rest.startswith(("logos/", "clubs/")) and not any(
        char.isspace() for char in url
    )


def drop_legacy(club: Organization) -> None:
    """The column is the truth: the bloque H copy in `metadata_json.profile` goes."""
    metadata = dict(club.metadata_json or {})
    profile = dict(metadata.get("profile") or {})
    if "logo_url" in profile:
        profile.pop("logo_url")
        club.metadata_json = {**metadata, "profile": profile}
