"""GET /api/v1/honors/{id}/sheet.pdf — the «Ficha de especialidad» generated from our data.

Public for published, active honors (cacheable); the creator and the reviewers in scope also
get unpublished ones (never cached by shared caches). The ETag covers every field the PDF
shows, so a conditional request answers 304 without rendering.
"""
import asyncio
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_optional_user
from app.models import User
from app.rate_limit import limiter
from app.routers.honors import _can_view_unpublished, _get_honor_or_404
from app.services.honor_sheet import load_sheet_data, render_sheet_pdf
from app.services.locales import LOCALE_PATTERN, SOURCE_LOCALE
from app.workflow import PUBLISHED

router = APIRouter(prefix="/api/v1/honors", tags=["honors"])


def _filename(slug: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "especialidad") + ".pdf"


@router.get(
    "/{honor_id}/sheet.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}, 304: {"description": "Not modified"}},
)
@limiter.limit("30/minute")
async def honor_sheet(
    request: Request,
    honor_id: uuid.UUID,
    locale: str | None = Query(None, pattern=LOCALE_PATTERN, max_length=35),
    download: bool = False,
    paper: Literal["a4", "letter"] = "a4",
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    honor = await _get_honor_or_404(db, honor_id)
    public = honor.status == PUBLISHED and bool(honor.active)
    if not public and not await _can_view_unpublished(db, current_user, honor):
        # 404 rather than 403: do not confirm that an unpublished honor exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Honor not found")

    data = await load_sheet_data(db, honor, locale or SOURCE_LOCALE)
    etag = f'"{data.fingerprint(paper)}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=3600" if public else "private, no-store",
    }
    candidates = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
    if etag in candidates or "*" in candidates:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    # CPU-bound (and possibly a short patch download): off the event loop.
    body = await asyncio.to_thread(render_sheet_pdf, data, paper=paper)
    headers["Content-Disposition"] = f'{"attachment" if download else "inline"}; filename="{_filename(honor.slug)}"'
    return Response(body, media_type="application/pdf", headers=headers)
