"""Cloudflare R2 (S3-compatible) object storage."""
import html
import logging
import re
import uuid
from functools import lru_cache

import anyio

from app.config import settings

logger = logging.getLogger(__name__)

SVG_TYPE = "image/svg+xml"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_DOCUMENT_TYPES = {"application/pdf"}
# SVG is markup that browsers execute, so it has extra rules: see the router.
ALLOWED_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_DOCUMENT_TYPES | {SVG_TYPE}
ALLOWED_FOLDERS = {
    "specialties", "patches", "general", "resources", "avatars", "logos", "courses", "covers",
}
# The bucket also holds `sheets/` (rendered honor PDFs, app/services/sheet_cache.py). It is
# written only by the API itself and deliberately NOT in the client upload whitelist above.
# Bloque G: the profile photo and cover. Any signed-in person uploads here (their own
# profile), raster images only; a minor never uploads a cover (spec §1, rule 4).
PROFILE_FOLDERS = {"avatars", "covers"}
SVG_FOLDERS = {"patches", "logos"}
DEFAULT_FOLDER = "general"

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_SIZE_BYTES = 50 * 1024 * 1024

# The stored extension comes from the validated MIME type, never from the
# client-supplied file name.
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    SVG_TYPE: ".svg",
}

_SVG_EVENT_HANDLER = re.compile(r"(?<![\w-])on[a-z]+\s*=")
_WHITESPACE_AND_CONTROL = re.compile(r"[\s\x00-\x1f]+")


class StorageNotConfigured(RuntimeError):
    pass


class StorageError(RuntimeError):
    pass


def max_size_for(content_type: str) -> int:
    if content_type in ALLOWED_IMAGE_TYPES or content_type == SVG_TYPE:
        return MAX_IMAGE_SIZE_BYTES
    return MAX_DOCUMENT_SIZE_BYTES


def resolve_folder(folder: str | None) -> str:
    """Whitelist, never a client-controlled path: unknown folders go to the default."""
    return folder if folder in ALLOWED_FOLDERS else DEFAULT_FOLDER


def svg_is_safe(data: bytes) -> bool:
    """
    Reject SVGs that could run code when opened from the media domain:
    <script> elements, on*= event handlers and javascript: URLs. Character
    references are decoded and whitespace is ignored first, because browsers
    do the same (`&#106;avascript:` and `java\nscript:` both execute).
    """
    try:
        text = html.unescape(data.decode("utf-8")).lower()
    except UnicodeDecodeError:
        return False
    compact = _WHITESPACE_AND_CONTROL.sub("", text)
    if "<script" in compact or "javascript:" in compact:
        return False
    return _SVG_EVENT_HANDLER.search(text) is None


def content_matches_type(data: bytes, content_type: str) -> bool:
    """Cheap magic-number check so the declared MIME type cannot be a lie."""
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if content_type == "application/pdf":
        return data.startswith(b"%PDF-")
    if content_type == SVG_TYPE:
        return b"<svg" in data.lower()
    return False


def build_key(folder: str, content_type: str) -> str:
    return f"{resolve_folder(folder)}/{uuid.uuid4().hex}{EXTENSIONS.get(content_type, '')}"


def public_url(key: str) -> str:
    return f"{settings.R2_PUBLIC_URL.rstrip('/')}/{key}"


@lru_cache(maxsize=1)
def get_client():
    """boto3 client for R2, created once. boto3 is imported lazily so the API
    boots even where the storage integration is not used."""
    if not settings.storage_configured:
        raise StorageNotConfigured("R2 credentials are not configured")
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        # R2 only speaks SigV4; explicit so presigned URLs (private_storage.py) never fall back to V2.
        config=Config(signature_version="s3v4"),
    )


def _put_object(key: str, data: bytes, content_type: str) -> None:
    get_client().put_object(
        Bucket=settings.R2_BUCKET_NAME, Key=key, Body=data, ContentType=content_type
    )


async def upload_bytes(data: bytes, content_type: str, folder: str) -> tuple[str, str]:
    """Upload and return (public_url, key). The blocking SDK call runs in a thread."""
    if not settings.storage_configured:
        raise StorageNotConfigured("R2 credentials are not configured")
    key = build_key(folder, content_type)
    try:
        await anyio.to_thread.run_sync(_put_object, key, data, content_type)
    except StorageNotConfigured:
        raise
    except Exception as exc:
        logger.exception("R2 upload failed for key %s", key)
        raise StorageError("Error al subir el archivo") from exc
    return public_url(key), key
