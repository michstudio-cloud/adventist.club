"""Render certificates from SVG templates (PNG / PDF / filled SVG)."""

import asyncio
import base64
import re
import urllib.request
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.certificates.render import (
    TemplateError, fonts_installed, list_templates, load_template, qr_data_url, render_certificate,
)
from app.config import settings
from app.db import SessionLocal
from app.rate_limit import limiter
from app.services.certificates import render_data

router = APIRouter(prefix="/api/v1/certificates", tags=["certificates"])

DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$")
HTTPS_RE = re.compile(r"^https://[^\s]+$")
MAX_FIELD_LEN = 400
MAX_REMOTE_IMAGE_BYTES = 2 * 1024 * 1024
REMOTE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


def _allowed_media_host() -> str:
    return urlsplit(settings.R2_PUBLIC_URL).hostname or ""


def _fetch_media_image(url: str) -> str:
    """resvg never loads remote URLs. Only our own media bucket is fetched (no SSRF surface),
    with a size cap and an image content-type, and handed to the renderer as a data URL."""
    request = urllib.request.Request(url, headers={"User-Agent": "adventist.club-renderer"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - host is allowlisted
        mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if mime not in REMOTE_IMAGE_TYPES:
            raise ValueError("tipo de imagen no permitido")
        body = response.read(MAX_REMOTE_IMAGE_BYTES + 1)
    if len(body) > MAX_REMOTE_IMAGE_BYTES:
        raise ValueError("imagen demasiado grande")
    return f"data:{mime};base64,{base64.b64encode(body).decode()}"


class RenderRequest(BaseModel):
    template: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    locale: str = Field(default="es", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
    format: Literal["png", "pdf", "svg"] = "png"
    ministry: str = Field(default="pathfinders", pattern=r"^[a-z0-9-]{2,40}$")
    dpi: int = Field(default=300, ge=72, le=600)
    # output width in inches; height keeps the template proportions. Pixel budget is capped below.
    width_in: float | None = Field(default=None, gt=1, le=24)
    data: dict[str, str] = Field(default_factory=dict, max_length=40)
    images: dict[str, str] = Field(default_factory=dict, max_length=12)
    certificate_no: str | None = Field(default=None, max_length=80)


def _is_element_template(slug: str) -> bool:
    try:
        return load_template(slug).meta.get("engine") == "elements"
    except TemplateError:
        return False     # the render itself answers 404


@router.get("/templates")
async def templates(
    ministry: str | None = Query(None, pattern=r"^[a-z0-9-]{2,40}$"),
    kind: Literal["honor", "program"] | None = None,
):
    """Bloque F §1.7: `ministry` and `kind` filter by the template's optional meta.json.
    Without filters the answer is exactly what it was before F (every template)."""
    return [{"slug": t.slug, "title": t.meta.get("title"), "width_in": round(t.width_pt / 72, 4), "height_in": round(t.height_pt / 72, 4),
             "locales": t.locales, "fields": t.fields, "kinds": t.kinds, "ministries": t.ministries}
            for t in list_templates() if t.serves(ministry, kind)]


@router.post("/render")
# SEC-10: open and CPU-bound (seconds per page on a 0.15-CPU instance): bounded per client.
@limiter.limit("30/minute")
async def render(request: Request, payload: RenderRequest):
    for key, value in payload.data.items():
        if len(value) > MAX_FIELD_LEN:
            raise HTTPException(422, f"El campo '{key}' es demasiado largo.")
    for key, value in payload.images.items():
        if not (DATA_URL_RE.match(value) or HTTPS_RE.match(value)):
            raise HTTPException(422, f"La imagen '{key}' debe ser una data URL o una URL https.")
    if payload.format != "svg" and not fonts_installed():
        # without the bundled fonts resvg would return a certificate with no text at all
        raise HTTPException(503, "Fuentes tipográficas no instaladas en el servidor.")
    if (payload.width_in or 11) * payload.dpi > 7200:
        raise HTTPException(422, "Combinación de tamaño y DPI demasiado grande.")
    images = dict(payload.images)
    for key, value in list(images.items()):
        if HTTPS_RE.match(value):
            if (urlsplit(value).hostname or "") != _allowed_media_host():
                raise HTTPException(422, f"La imagen '{key}' debe venir de {_allowed_media_host()} o ser una data URL.")
            try:
                images[key] = await asyncio.to_thread(_fetch_media_image, value)
            except Exception as exc:  # unreachable / wrong type / too big: render without it
                raise HTTPException(422, f"No se pudo cargar la imagen '{key}': {exc}") from exc
    data = dict(payload.data)
    if payload.certificate_no and _is_element_template(payload.template):
        # Element templates (docs/CERTIFICADOS_V4.md) print what the issued record says: its
        # names, date and folio, the honor in the certificate's language and the association.
        # The record wins over whatever the caller typed, so a folio'd page is never altered.
        async with SessionLocal() as db:
            issued = await render_data(db, payload.certificate_no, payload.locale)
        if issued:
            fields, patch_url = issued
            data.update(fields)
            if not (images.get("honor_patch") or images.get("honor_image")) and patch_url and \
                    HTTPS_RE.match(patch_url) and (urlsplit(patch_url).hostname or "") == _allowed_media_host():
                try:
                    images["honor_patch"] = await asyncio.to_thread(_fetch_media_image, patch_url)
                except Exception:  # the patch is decoration: the certificate renders without it
                    pass
    if payload.certificate_no:
        data.setdefault("certificate_no", payload.certificate_no)
        images.setdefault("qr", qr_data_url(f"{settings.PUBLIC_WEB_URL.rstrip('/')}/verify/{payload.certificate_no}"))
    try:
        # CPU-bound (seconds on a small instance): off the event loop, so the API keeps answering meanwhile
        body, media_type = await asyncio.to_thread(
            render_certificate, payload.template, data, images, locale=payload.locale, fmt=payload.format,
            dpi=payload.dpi, ministry=payload.ministry, width_in=payload.width_in)
    except TemplateError as exc:
        raise HTTPException(404 if "no existe" in str(exc) else 422, str(exc)) from exc
    ext = {"image/png": "png", "application/pdf": "pdf", "image/svg+xml": "svg"}[media_type]
    name = f"certificado-{payload.certificate_no or payload.template}.{ext}"
    return Response(body, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{name}"'})
