"""GET /api/v1/certificates/templates/{slug}/thumbnail.png — the picker's sample of a template.

The certificate assistant used to draw every thumbnail with `POST /certificates/render`
(sample data, one POST per template and language, 0.5-8 s each on the 0.15-CPU instance and
bounded to 30/minute per client): after going back and forth a couple of times the limit ran
out and the thumbnails stayed grey. The sample never changes, so it is rendered once:

- fixed sample data on the server (`SAMPLE`): «María López», the honor «Campamento I» in the
  template's language (its patch ships in `templates/assets/samples/honor-patch.webp`),
  «Club Orión», 2026-09-21; no QR, no signatures;
- `w` is 288, 576 or 1152 px (anything else is a 422), `locale` one of the template's
  languages (else 404, like an unknown slug);
- stored lazily in the public R2 bucket like the honor PDFs (`app/services/sheet_cache.py`):
  key `thumbs/{slug}/{locale}-{w}-{etag12}.png`, where the etag hashes template.svg, its
  strings, meta and background, the sample patch, the sample data and the engine
  (`app/certificates/render.py`), so any change produces a new key. Later requests get a 302 to
  the CDN copy. Without R2 (local) the PNG is answered directly from an in-process cache;
- nothing is rendered ahead of time (0.15 CPU): the first request renders, under a per-key lock
  so two simultaneous first requests render once, and at most `RENDER_SLOTS` thumbnails are
  drawn at the same time.

No specific rate limit: it is a cacheable GET with a bounded set of variants
(templates × languages × 3 widths), each rendered at most once per process.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path as PathParam, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.certificates import render as engine
from app.certificates.render import (
    TemplateError, fonts_installed, format_long_date, load_template, render_certificate,
)
from app.services import sheet_cache

router = APIRouter(prefix="/api/v1/certificates", tags=["certificates"])

THUMB_WIDTHS = (288, 576, 1152)
THUMBS_FOLDER = "thumbs"
PNG_TYPE = "image/png"
# The API answer (302 or the PNG itself) is cacheable only briefly: after a template is reinstalled
# the key changes (the etag is in it) and a browser holding a day-old redirect would keep showing the
# old design (owner report, 2026-09-24). The R2 object under that key is immutable and cached for a year.
CACHE_CONTROL = "public, max-age=300"
SAMPLE_PATCH = engine.TEMPLATES_DIR.parent / "assets" / "samples" / "honor-patch.webp"
SAMPLE_DATE = "2026-09-21"
# The patch above is «Campamento I» (design package replica-especialidad/recursos): the name matches it.
SAMPLE_HONOR = {"es": "Campamento I", "en": "Camping Skills I", "pt": "Acampamento I", "fr": "Camping I"}
SAMPLE = {
    "recipient_name": "María López",
    "club_name": "Club Orión",
    "director_name": "Ana Ruiz",
    "signer_name": "Luis Gómez",
}
# 0.15 CPU: two thumbnails at a time at most; the rest wait their turn (a cold «dorada» takes seconds).
RENDER_SLOTS = 2
MEMORY_BUDGET = 24 * 1024 * 1024   # bytes of PNG kept in process (local without R2, and the upload window)


def sample_data(fields: list[str], locale: str) -> dict[str, str]:
    """What the assistant would send for the sample (lib/certificate-templates.ts `buildRenderData`)."""
    language = locale.split("-")[0]
    data = {
        "recipient_name": SAMPLE["recipient_name"],
        "honor_name": SAMPLE_HONOR.get(language, SAMPLE_HONOR["es"]),
        "club_name": SAMPLE["club_name"],
        "director_name": SAMPLE["director_name"],
    }
    data["coordinator_name" if "coordinator_name" in fields else "instructor_name"] = SAMPLE["signer_name"]
    if "issued_day" in fields:
        data["issued_year"], data["issued_month"], data["issued_day"] = SAMPLE_DATE.split("-")
    else:
        try:
            data["issued_date"] = format_long_date(SAMPLE_DATE, locale)
        except TemplateError:               # a language without a long-date format: the engine decides
            data["issued_date"] = SAMPLE_DATE
    return data


@lru_cache(maxsize=1)
def _sample_images() -> dict[str, str]:
    if not SAMPLE_PATCH.is_file():
        return {}
    return {"honor_patch": "data:image/webp;base64," + base64.b64encode(SAMPLE_PATCH.read_bytes()).decode()}


@lru_cache(maxsize=64)
def _template_hash(slug: str) -> str:
    """Hash of everything the picture depends on. Templates ship with the deploy: computed once."""
    template = load_template(slug)
    digest = hashlib.sha256()
    for path in sorted(template.directory.iterdir()):
        if path.is_file() and (path.name == "template.svg" or path.name == "meta.json"
                               or path.name.startswith(("strings.", "background."))):
            digest.update(path.name.encode() + b"\0" + path.read_bytes())
    for extra in (SAMPLE_PATCH, Path(engine.__file__)):
        if extra.is_file():
            digest.update(extra.read_bytes())
    digest.update(json.dumps([SAMPLE, SAMPLE_HONOR, SAMPLE_DATE], sort_keys=True).encode())
    return digest.hexdigest()


def thumbnail_version(slug: str) -> str:
    """Short hash of everything a thumbnail depends on: `GET /templates` exposes it so the web app
    appends `?v=` and never shows a stale thumbnail after a redeploy, whatever the browser cached."""
    return _template_hash(slug)[:12]


def thumb_etag(slug: str, locale: str, width: int) -> str:
    return '"' + hashlib.sha256(f"{_template_hash(slug)}|{locale}|{width}".encode()).hexdigest()[:32] + '"'


def thumb_key(slug: str, locale: str, width: int, etag: str) -> str:
    return f"{THUMBS_FOLDER}/{slug}/{locale}-{width}-{etag.strip(chr(34))[:sheet_cache.ETAG_CHARS]}.png"


def render_thumbnail(slug: str, locale: str, width: int) -> bytes:
    template = load_template(slug)
    dpi = width / (template.width_pt / engine.POINTS_PER_INCH)     # exactly `width` px wide
    body, _ = render_certificate(slug, sample_data(template.fields, locale), dict(_sample_images()),
                                 locale=locale, fmt="png", dpi=dpi)
    return body


# ------------------------------------------------------------------ in-process cache + locks


class _Memory:
    """LRU of rendered PNGs bounded by bytes (not by count: 288 px ≈ 10-45 KB, 1152 px ≈ 55-425 KB)."""

    def __init__(self, budget: int):
        self.budget = budget
        self.size = 0
        self.items: OrderedDict[str, bytes] = OrderedDict()

    def get(self, key: str) -> bytes | None:
        body = self.items.get(key)
        if body is not None:
            self.items.move_to_end(key)
        return body

    def put(self, key: str, body: bytes) -> None:
        if key in self.items:
            self.size -= len(self.items.pop(key))
        self.items[key] = body
        self.size += len(body)
        while self.size > self.budget and len(self.items) > 1:
            _, dropped = self.items.popitem(last=False)
            self.size -= len(dropped)

    def clear(self) -> None:
        self.items.clear()
        self.size = 0


_memory = _Memory(MEMORY_BUDGET)
_locks: dict[str, asyncio.Lock] = {}
_slots: asyncio.Semaphore | None = None


def _render_slots() -> asyncio.Semaphore:
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(RENDER_SLOTS)
    return _slots


def forget_all() -> None:
    """Tests: start from an empty cache."""
    global _slots
    _memory.clear()
    _locks.clear()
    _slots = None


@router.get(
    "/templates/{slug}/thumbnail.png",
    response_class=Response,
    responses={
        200: {"content": {PNG_TYPE: {}}},
        302: {"description": "Stored copy on the CDN"},
        304: {"description": "Not modified"},
        404: {"description": "No such template, or not in that language"},
    },
)
async def template_thumbnail(
    request: Request,
    background: BackgroundTasks,
    slug: str = PathParam(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$"),
    locale: str = Query("es", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$", max_length=35),
    # Literal[288, 576, 1152] would reject the query string "288" (a str): checked by hand below.
    w: int = Query(576, description="Ancho en px: 288, 576 o 1152"),
) -> Response:
    if w not in THUMB_WIDTHS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El ancho (w) debe ser 288, 576 o 1152.")
    try:
        template = load_template(slug)
    except TemplateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plantilla no encontrada.") from exc
    if locale not in template.locales:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "La plantilla no existe en ese idioma.")
    if not fonts_installed():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Fuentes tipográficas no instaladas en el servidor.")

    etag = thumb_etag(slug, locale, w)
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL}
    candidates = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
    if etag in candidates or "*" in candidates:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    key = thumb_key(slug, locale, w, etag)
    stored = sheet_cache.enabled()
    if stored and await sheet_cache.exists(key):
        return RedirectResponse(sheet_cache.public_url(key), status_code=status.HTTP_302_FOUND, headers=headers)

    body = _memory.get(key)
    if body is None:
        lock = _locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                body = _memory.get(key)          # rendered by whoever held the lock before us
                if body is None:
                    async with _render_slots():
                        try:
                            # CPU-bound: off the event loop, so the API keeps answering meanwhile.
                            body = await asyncio.to_thread(render_thumbnail, slug, locale, w)
                        except TemplateError as exc:
                            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
                    _memory.put(key, body)
                    if stored:
                        # After the response: the first visitor is not slowed down by the upload.
                        background.add_task(sheet_cache.store, key, body, f"{slug}-{locale}-{w}.png", PNG_TYPE)
        finally:
            if not lock.locked():
                _locks.pop(key, None)
    return Response(body, media_type=PNG_TYPE, headers=headers)
