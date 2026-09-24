"""Rendered honor PDFs kept in the PUBLIC R2 bucket, so each version is rendered once.

Strictly lazy: nothing is rendered ahead of time. The first request for a published honor
renders the PDF, answers with it and (after the response) uploads it; later requests are
redirected to the CDN copy. `scripts/render_honor_sheets.py --warm` exists as a MANUAL tool
only (dry-run by default) and is not wired to any startup job or cron.

Key: `sheets/{honor_id}/{mode}-{locale}-{paper}-{etag12}.pdf`, where etag12 is the first 12 hex
of the content ETag (`SheetData.fingerprint`), so any change of name, requirements, resources,
version, renderer, mode or the SHOW_SOURCES switch (credit line under the requirements) produces a
new key and the stored objects are immutable.

Lean storage: after a successful upload the other versions of THE SAME variant (same mode,
locale and paper, older etag12) under `sheets/{honor_id}/` are deleted, best-effort. Other
variants are left alone: they are current for their own URL and get replaced the next time that
URL is rendered. To wipe an honor by hand (e.g. it was unpublished):
    aws s3 rm --recursive s3://$R2_BUCKET_NAME/sheets/<honor_id>/ --endpoint-url https://<account>.r2.cloudflarestorage.com

Drafts are never stored (the router only calls this for published, active honors). Every
storage error is logged and swallowed: the caller falls back to rendering.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Honor
from app.services import storage
from app.services.honor_sheet import Mode, Paper, SheetData, load_sheet_data, render_sheet_pdf
from app.services.locales import SOURCE_LOCALE
from app.workflow import PUBLISHED

logger = logging.getLogger(__name__)

# Written only by the API itself, never a client upload folder (see storage.ALLOWED_FOLDERS).
SHEETS_FOLDER = "sheets"
OBJECT_CACHE_CONTROL = "public, max-age=31536000, immutable"
PDF_TYPE = "application/pdf"
ETAG_CHARS = 12
KNOWN_TTL_S = 600
_KNOWN_MAX = 10_000

# key -> monotonic expiry. Only positive answers are remembered: a miss costs a HEAD, and a
# remembered miss would keep rendering an object that another request just uploaded.
_known: dict[str, float] = {}


def enabled() -> bool:
    return settings.storage_configured


def is_public(honor: Honor) -> bool:
    return honor.status == PUBLISHED and bool(honor.active)


def sheet_filename(slug: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "especialidad") + ".pdf"


def etag_for(data: SheetData, paper: Paper, mode: Mode) -> str:
    return f'"{data.fingerprint(paper, mode)}"'


def sheet_key(honor_id: uuid.UUID | str, mode: str, locale: str | None, paper: str, etag: str) -> str:
    tag = etag.strip().removeprefix("W/").strip('"')[:ETAG_CHARS]
    return f"{SHEETS_FOLDER}/{honor_id}/{mode}-{(locale or SOURCE_LOCALE).lower()}-{paper}-{tag}.pdf"


def public_url(key: str) -> str:
    return storage.public_url(key)


# ------------------------------------------------------------------ in-process HEAD cache


def _remember(key: str) -> None:
    now = time.monotonic()
    if len(_known) >= _KNOWN_MAX:
        for stale in [k for k, expiry in _known.items() if expiry <= now]:
            del _known[stale]
        if len(_known) >= _KNOWN_MAX:
            _known.clear()
    _known[key] = now + KNOWN_TTL_S


def _forget(key: str) -> None:
    _known.pop(key, None)


def forget_all() -> None:
    _known.clear()


def _is_known(key: str) -> bool:
    expiry = _known.get(key)
    if expiry is None:
        return False
    if expiry <= time.monotonic():
        del _known[key]
        return False
    return True


# ------------------------------------------------------------------ blocking SDK calls


def _is_missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status_code == 404 or response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


def _head(key: str) -> bool:
    try:
        storage.get_client().head_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    except Exception as exc:
        if _is_missing(exc):
            return False
        raise
    return True


def _get(key: str) -> bytes | None:
    try:
        found = storage.get_client().get_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    except Exception as exc:
        if _is_missing(exc):
            return None
        raise
    return found["Body"].read()


def _put(key: str, body: bytes, filename: str) -> None:
    storage.get_client().put_object(
        Bucket=settings.R2_BUCKET_NAME, Key=key, Body=body, ContentType=PDF_TYPE,
        CacheControl=OBJECT_CACHE_CONTROL, ContentDisposition=f'inline; filename="{filename}"',
    )


def _purge(key: str) -> list[str]:
    """Delete the older versions of the same variant as `key`. Returns the deleted keys."""
    folder, name = key.rsplit("/", 1)
    variant = name[: -(ETAG_CHARS + len(".pdf"))]             # "{mode}-{locale}-{paper}-"
    same_variant = re.compile(re.escape(variant) + rf"[0-9a-f]{{{ETAG_CHARS}}}\.pdf")
    client = storage.get_client()
    deleted, token = [], None
    while True:
        params = {"Bucket": settings.R2_BUCKET_NAME, "Prefix": f"{folder}/"}
        if token:
            params["ContinuationToken"] = token
        page = client.list_objects_v2(**params)
        for item in page.get("Contents", []):
            other = item["Key"]
            if other != key and same_variant.fullmatch(other.rsplit("/", 1)[1]):
                client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=other)
                deleted.append(other)
        token = page.get("NextContinuationToken")
        if not page.get("IsTruncated") or not token:
            return deleted


# ------------------------------------------------------------------ async API (never raises)


async def exists(key: str) -> bool:
    if _is_known(key):
        return True
    try:
        found = await anyio.to_thread.run_sync(_head, key)
    except Exception:
        logger.warning("R2 HEAD failed for %s; rendering instead", key, exc_info=True)
        return False
    if found:
        _remember(key)
    return found


async def fetch(key: str) -> bytes | None:
    try:
        body = await anyio.to_thread.run_sync(_get, key)
    except Exception:
        logger.warning("R2 GET failed for %s; rendering instead", key, exc_info=True)
        return None
    if body is None:
        _forget(key)
        return None
    _remember(key)
    return body


async def store(key: str, body: bytes, filename: str) -> bool:
    """Upload, then purge the older versions of the variant. True when the upload worked."""
    try:
        await anyio.to_thread.run_sync(_put, key, body, filename)
    except Exception:
        logger.warning("R2 upload failed for %s", key, exc_info=True)
        return False
    _remember(key)
    try:
        for old in await anyio.to_thread.run_sync(_purge, key):
            _forget(old)
    except Exception:
        logger.warning("R2 purge failed next to %s", key, exc_info=True)
    return True


async def warm_sheet(db: AsyncSession, honor: Honor, *, locale: str = SOURCE_LOCALE, paper: Paper = "a4",
                     mode: Mode = "hoja") -> str:
    """Manual pre-render (scripts/render_honor_sheets.py --warm): the same key and upload the
    endpoint uses. Returns 'skipped' (not public), 'cached', 'uploaded' or 'failed'."""
    if not is_public(honor):
        return "skipped"
    data = await load_sheet_data(db, honor, locale)
    key = sheet_key(honor.id, mode, locale, paper, etag_for(data, paper, mode))
    if await exists(key):
        return "cached"
    body = await asyncio.to_thread(render_sheet_pdf, data, paper=paper, mode=mode)
    return "uploaded" if await store(key, body, sheet_filename(honor.slug)) else "failed"
