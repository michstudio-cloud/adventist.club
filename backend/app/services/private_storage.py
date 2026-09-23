"""Private R2 bucket for portfolio evidence (photos and documents of minors).

Nothing here is ever public: the browser uploads straight to the bucket with a
presigned PUT and reads with a short presigned GET, so the API never receives
the file and no URL is stored. Same R2 credentials as `storage.py`, other bucket.
"""
import logging
import uuid
from dataclasses import dataclass

import anyio

from app.config import settings
from app.services import storage

logger = logging.getLogger(__name__)

NOT_CONFIGURED_DETAIL = "Almacenamiento privado no configurado"

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
MAX_PDF_SIZE_BYTES = 20 * 1024 * 1024
PUT_EXPIRES_SECONDS = 10 * 60
GET_EXPIRES_SECONDS = 5 * 60
# The evidence album shows up to four photos per honor: a page the member browses for a
# while, so its links live longer than the single "open this file" link above.
ALBUM_GET_EXPIRES_SECONDS = 15 * 60

# content type -> (evidence kind, stored extension). The extension comes from the
# validated MIME type, never from a client-supplied file name.
ALLOWED_TYPES = {
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("image", ".webp"),
    "application/pdf": ("pdf", ".pdf"),
}


class PrivateStorageNotConfigured(RuntimeError):
    pass


class PrivateStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    size_bytes: int
    content_type: str


def kind_for(content_type: str) -> str:
    if content_type not in ALLOWED_TYPES:
        raise ValueError(f"Tipo de archivo no permitido: {content_type}")
    return ALLOWED_TYPES[content_type][0]


def max_size_for(content_type: str) -> int:
    return MAX_PDF_SIZE_BYTES if kind_for(content_type) == "pdf" else MAX_IMAGE_SIZE_BYTES


def build_key(
    user_id: uuid.UUID, enrollment_id: uuid.UUID, evidence_id: uuid.UUID, content_type: str
) -> str:
    kind_for(content_type)  # rejects anything outside the whitelist
    return f"evidence/{user_id}/{enrollment_id}/{evidence_id}{ALLOWED_TYPES[content_type][1]}"


def build_letter_key(user_id: uuid.UUID, letter_id: uuid.UUID, content_type: str) -> str:
    """Church letters (Bloque B) share this bucket: a signed document is personal data
    and never belongs in the public media bucket."""
    kind_for(content_type)  # rejects anything outside the whitelist
    return f"letters/{user_id}/{letter_id}{ALLOWED_TYPES[content_type][1]}"


def _bucket() -> str:
    if not settings.private_storage_configured:
        raise PrivateStorageNotConfigured(NOT_CONFIGURED_DETAIL)
    return settings.R2_PRIVATE_BUCKET_NAME


def get_client():
    """The shared R2 client. Tests replace this function with an in-memory fake."""
    if not settings.private_storage_configured:
        raise PrivateStorageNotConfigured(NOT_CONFIGURED_DETAIL)
    return storage.get_client()


def presign_put(key: str, content_type: str, size_bytes: int) -> dict:
    """Upload URL that only accepts the declared type and size: both are signed
    headers, so a different Content-Type or Content-Length fails the signature."""
    bucket = _bucket()
    url = get_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
        },
        ExpiresIn=PUT_EXPIRES_SECONDS,
    )
    # Content-Length is set by the browser itself; Content-Type must be sent as signed.
    return {
        "url": url,
        "method": "PUT",
        "headers": {"Content-Type": content_type},
        "expires_in": PUT_EXPIRES_SECONDS,
    }


def presign_get(key: str, expires_in: int = GET_EXPIRES_SECONDS) -> dict:
    """Local HMAC signing, no network call: cheap enough to sign a page of thumbnails."""
    bucket = _bucket()
    url = get_client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_in
    )
    return {"url": url, "expires_in": expires_in}


def head_object(key: str) -> StoredObject | None:
    """Blocking. None when the object does not exist."""
    from botocore.exceptions import ClientError

    try:
        found = get_client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status_code == 404 or exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    content_type = (found.get("ContentType") or "").split(";")[0].strip().lower()
    return StoredObject(size_bytes=int(found.get("ContentLength") or 0), content_type=content_type)


def delete_object(key: str) -> None:
    """Blocking; deleting a missing object is not an error in S3/R2."""
    get_client().delete_object(Bucket=_bucket(), Key=key)


async def _in_thread(function, key: str):
    try:
        return await anyio.to_thread.run_sync(function, key)
    except PrivateStorageNotConfigured:
        raise
    except Exception as exc:
        logger.exception("Private R2 call failed for key %s", key)
        raise PrivateStorageError("Error al consultar el almacenamiento privado") from exc


async def head(key: str) -> StoredObject | None:
    return await _in_thread(head_object, key)


async def delete(key: str) -> None:
    await _in_thread(delete_object, key)
