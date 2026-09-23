"""GET /api/v1/honors/{id}/sheet.pdf — the honor's printable PDF, generated from our data.

`modo=hoja` (default) is the worksheet a Pathfinder fills in by hand (fields, checkboxes, answer
lines, approval signatures); `modo=ficha` is the compact catalogue sheet.

Public for published, active honors (cacheable); the creator and the reviewers in scope also
get unpublished ones (never cached by shared caches, never stored). The ETag covers every field
the PDF shows, so a conditional request answers 304 without rendering.

Published honors are stored lazily in the public R2 bucket (`app/services/sheet_cache.py`): the
first request renders and answers with the PDF, uploading it after the response; later requests
are redirected (302) to the CDN copy. `download=1` is the exception: the public domain does not
honour `response-content-disposition`, so the API streams the stored bytes with its own
attachment header (still no render). Any storage error falls back to rendering.
"""
import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_optional_user
from app.models import User
from app.rate_limit import limiter
from app.routers.honors import _can_view_unpublished, _get_honor_or_404
from app.services import sheet_cache
from app.services.honor_sheet import load_sheet_data, render_sheet_pdf
from app.services.locales import LOCALE_PATTERN, SOURCE_LOCALE

router = APIRouter(prefix="/api/v1/honors", tags=["honors"])


@router.get(
    "/{honor_id}/sheet.pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}},
        302: {"description": "Stored copy on the CDN"},
        304: {"description": "Not modified"},
    },
)
@limiter.limit("30/minute")
async def honor_sheet(
    request: Request,
    honor_id: uuid.UUID,
    background: BackgroundTasks,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    download: bool = False,
    paper: Literal["a4", "letter"] = "a4",
    modo: Literal["hoja", "ficha"] = "hoja",
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    honor = await _get_honor_or_404(db, honor_id)
    public = sheet_cache.is_public(honor)
    if not public and not await _can_view_unpublished(db, current_user, honor):
        # 404 rather than 403: do not confirm that an unpublished honor exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Honor not found")

    data = await load_sheet_data(db, honor, locale or SOURCE_LOCALE)
    etag = sheet_cache.etag_for(data, paper, modo)
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=3600" if public else "private, no-store",
    }
    candidates = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
    if etag in candidates or "*" in candidates:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    filename = sheet_cache.sheet_filename(honor.slug)
    disposition = f'{"attachment" if download else "inline"}; filename="{filename}"'
    key = sheet_cache.sheet_key(honor.id, modo, locale, paper, etag) if public and sheet_cache.enabled() else None
    if key is not None:
        if not download:
            if await sheet_cache.exists(key):
                return RedirectResponse(sheet_cache.public_url(key), status_code=status.HTTP_302_FOUND,
                                        headers=headers)
        else:
            stored = await sheet_cache.fetch(key)
            if stored is not None:
                return Response(stored, media_type="application/pdf",
                                headers={**headers, "Content-Disposition": disposition})

    # CPU-bound (and possibly a short patch download): off the event loop.
    body = await asyncio.to_thread(render_sheet_pdf, data, paper=paper, mode=modo)
    if key is not None:
        # After the response: the first request is not slowed down by the upload.
        background.add_task(sheet_cache.store, key, body, filename)
    headers["Content-Disposition"] = disposition
    return Response(body, media_type="application/pdf", headers=headers)
