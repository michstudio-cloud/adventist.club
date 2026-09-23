"""«Ficha de especialidad»: the printable sheet of an honor, generated from our own data.

It replaces the third-party requirement PDFs (guiasmayores.com) with a sheet in the visual
language of conquistadores.app, in its LIGHT theme because the sheet is printed:

  * page white, text near-black (#0b0b0d), muted grey (#636a76 = `--muted-foreground`),
    cards #f4f5f7 with 14/20/28 pt corners (the app's `--radius-md/lg/xl`), pills fully rounded
    like `.cq-pill`, eyebrow uppercase with 0.16 em tracking like `.cq-eyebrow`;
  * one accent colour per category (see CATEGORY_ACCENT below), taken from the app's `--sys-*`
    tokens. Each token has a darker "ink" twin used for text and solid pills, so white text on
    it passes WCAG AA (>= 4.5:1) on paper; the bright token is only used for tints and glows;
  * the patch as the hero, a QR to the honor page and the page number on every footer.

Typography is the bundled Noto Sans (the same `fonts/` directory the certificates use); when
those files are not deployed the sheet falls back to Helvetica, which still covers Spanish.

The layout is A4 by default (Letter on request) with margins that also survive printing an
A4 sheet on Letter paper at 100 % (>= 25 pt of the page's top and bottom may be cut).

Rendering never fails because of the patch: it is fetched only from our media bucket, with a
short timeout, cached on disk, and a failed fetch draws a placeholder with the initials.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlsplit

import qrcode
from PIL import Image
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.certificates.render import FONTS_DIR
from app.config import settings
from app.models import (
    Honor,
    HonorCategory,
    HonorCategoryTranslation,
    HonorRequirement,
    HonorResource,
    HonorTranslation,
)
from app.services.locales import SOURCE_LOCALE, best_locale, match_locale

logger = logging.getLogger(__name__)

# Bump when the design changes: it is part of the ETag, so cached sheets are refreshed.
RENDERER_VERSION = "1"
HONOR_PAGE_BASE = "https://www.conquistadores.app/honors/"
Paper = Literal["a4", "letter"]
PAGE_SIZES = {"a4": A4, "letter": LETTER}

# ---------------------------------------------------------------------------------------
# Palette. (bright `--sys-*` token from conquistadores-app/app/globals.css, print ink)
# ---------------------------------------------------------------------------------------
SYS = {
    "red": ("#ff375f", "#c8173f"),     # ink between --sys-red and --sys-red-deep
    "orange": ("#ff9f0a", "#b25e00"),
    "yellow": ("#ffd60a", "#866a00"),
    "green": ("#30d158", "#1b7d37"),   # a shade under --success (#1e8a3c) to reach 4.5:1
    "blue": ("#0a84ff", "#0062cc"),    # ink = --link
    "indigo": ("#5e5ce6", "#4b49c9"),
    "purple": ("#bf5af2", "#8e32bd"),
}
# The 13 categories of the catalogue, grouped by meaning (7 tokens, so some share a colour).
CATEGORY_ACCENT = {
    "nature": "green",
    "outdoor-industries": "green",
    "recreation": "blue",
    "florida-conference": "blue",
    "arts-crafts-hobbies": "purple",
    "masters": "purple",
    "household-arts": "orange",
    "community-services": "orange",
    "health-science": "red",
    "adra": "red",
    "spiritual-growth": "indigo",
    "doctrinal": "indigo",
    "vocational": "yellow",
}
_SYS_ORDER = ("blue", "green", "orange", "purple", "red", "indigo", "yellow")

TEXT = HexColor("#0b0b0d")
TEXT_2 = HexColor("#343a45")
MUTED = HexColor("#636a76")
EYEBROW = HexColor("#4a515d")
CARD = HexColor("#f4f5f7")
LINE = HexColor("#e3e5ea")
WHITE = HexColor("#ffffff")
SUCCESS_INK = HexColor("#0f6b2c")
SUCCESS = HexColor("#30d158")
INFO_INK = HexColor("#0062cc")


@dataclass(frozen=True)
class Accent:
    name: str
    bright: Color
    ink: Color


def category_accent(slug: str | None) -> Accent:
    """Fixed colour for the 13 known categories; any other slug gets a stable one by hash."""
    name = CATEGORY_ACCENT.get(slug or "")
    if name is None:
        digest = hashlib.sha256((slug or "").encode()).digest()
        name = _SYS_ORDER[digest[0] % len(_SYS_ORDER)] if slug else "blue"
    bright, ink = SYS[name]
    return Accent(name, HexColor(bright), HexColor(ink))


def tint(color: Color, amount: float) -> Color:
    """`color` at `amount` opacity over white, as an opaque colour (prints predictably)."""
    return Color(1 - (1 - color.red) * amount, 1 - (1 - color.green) * amount, 1 - (1 - color.blue) * amount)


# ---------------------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------------------
_font_lock = threading.Lock()


@lru_cache(maxsize=1)
def sheet_fonts() -> tuple[str, str]:
    """(regular, bold): Noto Sans when bundled, else the built-in Helvetica."""
    regular, bold = FONTS_DIR / "NotoSans-Regular.ttf", FONTS_DIR / "NotoSans-Bold.ttf"
    if regular.exists() and bold.exists():
        with _font_lock:
            try:
                pdfmetrics.registerFont(TTFont("CQSans", str(regular)))
                pdfmetrics.registerFont(TTFont("CQSans-Bold", str(bold)))
                return "CQSans", "CQSans-Bold"
            except Exception:  # a broken font file must not take the sheet down
                logger.exception("could not register the Noto Sans fonts; using Helvetica")
    return "Helvetica", "Helvetica-Bold"


# ---------------------------------------------------------------------------------------
# Copy (the sheet's own labels; the content comes from the database)
# ---------------------------------------------------------------------------------------
LABELS = {
    "es": {
        "eyebrow": "Especialidad",
        "requirements": "Requisitos",
        "resources": "Recursos",
        "practical": "Práctico",
        "count": "{n} requisitos",
        "count_one": "1 requisito",
        "count_practical": "{n} prácticos",
        "count_practical_one": "1 práctico",
        "pending_title": "Requisitos en preparación",
        "pending_body": "Estamos preparando los requisitos de esta especialidad. "
                        "Consulta la versión más reciente en",
        "foreign": "Requisitos disponibles por ahora solo en {language}; la traducción está en preparación.",
        "req_source": "Texto de los requisitos: {source}",
        "version": "Versión {n} · actualizada {date}",
        "page": "Página {x} de {y}",
        "source": "Fuente: {host}",
        "year": "Desde {year}",
        "code": "Código {code}",
        "level_n": "Nivel {n}",
        "levels": {"BEGINNER": "Básica", "INTERMEDIATE": "Intermedia", "ADVANCED": "Avanzada"},
        "types": {"OFFICIAL_GC": "Oficial (Asociación General)", "DIVISIONAL": "De división", "LOCAL": "Local"},
        "authorities": {
            "GC": "Oficial (Asociación General)", "NAD": "Oficial (División Norteamericana)",
            "SAD": "Oficial (División Sudamericana)", "IAD": "Oficial (División Interamericana)",
        },
        "resource_types": {"pdf": "PDF", "video": "Video", "link": "Enlace", "audio": "Audio", "image": "Imagen"},
        "languages": {"en": "inglés", "es": "español", "pt": "portugués", "fr": "francés"},
        "months": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
        "title": "Ficha de especialidad",
    },
    "en": {
        "eyebrow": "Honor",
        "requirements": "Requirements",
        "resources": "Resources",
        "practical": "Hands-on",
        "count": "{n} requirements",
        "count_one": "1 requirement",
        "count_practical": "{n} hands-on",
        "count_practical_one": "1 hands-on",
        "pending_title": "Requirements in preparation",
        "pending_body": "We are preparing the requirements of this honor. See the latest version at",
        "foreign": "Requirements are only available in {language} for now; the translation is in progress.",
        "req_source": "Requirement text: {source}",
        "version": "Version {n} · updated {date}",
        "page": "Page {x} of {y}",
        "source": "Source: {host}",
        "year": "Since {year}",
        "code": "Code {code}",
        "level_n": "Level {n}",
        "levels": {"BEGINNER": "Basic", "INTERMEDIATE": "Intermediate", "ADVANCED": "Advanced"},
        "types": {"OFFICIAL_GC": "Official (General Conference)", "DIVISIONAL": "Divisional", "LOCAL": "Local"},
        "authorities": {
            "GC": "Official (General Conference)", "NAD": "Official (North American Division)",
            "SAD": "Official (South American Division)", "IAD": "Official (Inter-American Division)",
        },
        "resource_types": {"pdf": "PDF", "video": "Video", "link": "Link", "audio": "Audio", "image": "Image"},
        "languages": {"en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French"},
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "title": "Honor sheet",
    },
}
SOURCE_NAMES = {"pathfinder-wiki": "Pathfinder Wiki"}


def _ui_language(locale: str | None) -> str:
    language = (locale or SOURCE_LOCALE).lower().split("-")[0]
    return language if language in LABELS else SOURCE_LOCALE


# ---------------------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------------------
@dataclass
class SheetRequirement:
    position: int
    description: str
    is_theoretical: bool
    instructions: str | None
    source: str | None = None
    source_url: str | None = None
    license: str | None = None


@dataclass
class SheetResource:
    name: str
    url: str
    type: str | None


@dataclass
class SheetData:
    honor_id: str
    slug: str
    name: str
    language: str                    # language of the sheet's labels (es / en)
    category_slug: str | None
    category_name: str | None
    description: str | None
    image_url: str | None
    source_url: str | None
    difficulty_level: str | None
    skill_level: int | None
    honor_type: str | None
    authority: str | None
    year_introduced: int | None
    code: str | None
    version: int
    updated_at: datetime | None
    requirements_locale: str | None
    requirements: list[SheetRequirement] = field(default_factory=list)
    resources: list[SheetResource] = field(default_factory=list)

    @property
    def page_url(self) -> str:
        return HONOR_PAGE_BASE + self.slug

    def fingerprint(self, paper: Paper = "a4") -> str:
        """Everything the PDF depends on. Used as the ETag: any edit changes it."""
        payload = json.dumps({"v": RENDERER_VERSION, "paper": paper, **asdict(self)},
                             sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


async def load_sheet_data(db: AsyncSession, honor: Honor, locale: str | None = SOURCE_LOCALE) -> SheetData:
    """Everything the sheet shows, resolved to one language with the catalogue's fallbacks:
    names in `locale` when translated (else the Spanish source), requirements in `locale`, else
    Spanish, else English, else whatever exists (`best_locale`)."""
    requested = locale or SOURCE_LOCALE
    language = _ui_language(requested)
    source = requested.lower().split("-")[0] == SOURCE_LOCALE

    translation = None
    if not source:
        stored = (await db.execute(
            select(HonorTranslation.locale).where(HonorTranslation.honor_id == honor.id))).scalars().all()
        chosen = match_locale(list(stored), requested)
        translation = await db.get(HonorTranslation, (honor.id, chosen)) if chosen else None

    category = await db.get(HonorCategory, honor.category_id) if honor.category_id else None
    category_name = category.name if category else None
    if category is not None and not source:
        stored = (await db.execute(select(HonorCategoryTranslation.locale).where(
            HonorCategoryTranslation.category_id == category.id))).scalars().all()
        chosen = match_locale(list(stored), requested)
        if chosen:
            row = await db.get(HonorCategoryTranslation, (category.id, chosen))
            category_name = row.name if row else category_name

    stored_requirements = (await db.execute(
        select(HonorRequirement.locale).where(HonorRequirement.honor_id == honor.id).distinct())).scalars().all()
    requirements_locale = best_locale(list(stored_requirements), requested) if stored_requirements else None
    requirements = []
    if requirements_locale:
        requirements = (await db.execute(
            select(HonorRequirement)
            .where(HonorRequirement.honor_id == honor.id, HonorRequirement.locale == requirements_locale)
            .order_by(HonorRequirement.position, HonorRequirement.created_at, HonorRequirement.id)
        )).scalars().all()
    resources = (await db.execute(
        select(HonorResource).where(HonorResource.honor_id == honor.id)
        .order_by(HonorResource.position, HonorResource.created_at, HonorResource.id)
    )).scalars().all()

    return SheetData(
        honor_id=str(honor.id),
        slug=honor.slug,
        name=(translation.name if translation else None) or honor.name,
        language=language,
        category_slug=category.slug if category else None,
        category_name=category_name,
        description=(translation.description if translation and translation.description else None)
        or honor.description,
        image_url=honor.image_url,
        source_url=honor.source_url,
        difficulty_level=honor.difficulty_level,
        skill_level=honor.skill_level,
        honor_type=honor.honor_type,
        authority=honor.authority,
        year_introduced=honor.year_introduced,
        code=honor.code,
        version=honor.version or 1,
        updated_at=honor.updated_at,
        requirements_locale=requirements_locale,
        requirements=[SheetRequirement(r.position, r.description, bool(r.is_theoretical), r.instructions,
                                       r.source, r.source_url, r.license) for r in requirements],
        resources=[SheetResource(r.name, r.url, r.type) for r in resources],
    )


async def render_honor_sheet(db: AsyncSession, honor: Honor, locale: str = SOURCE_LOCALE, *,
                             paper: Paper = "a4", patch_image: bytes | None = None,
                             fetch_image: bool = True) -> bytes:
    """The sheet as PDF bytes. CPU-bound: API callers run `render_sheet_pdf` in a thread."""
    data = await load_sheet_data(db, honor, locale)
    return render_sheet_pdf(data, paper=paper, patch_image=patch_image, fetch_image=fetch_image)


# ---------------------------------------------------------------------------------------
# Patch image: allowlisted host, short timeout, disk cache, negative cache
# ---------------------------------------------------------------------------------------
PATCH_CACHE_DIR = Path(os.environ.get("HONOR_SHEET_CACHE_DIR", "/tmp/adventist-honor-patches"))
PATCH_TIMEOUT_S = 3.0
PATCH_MAX_BYTES = 4 * 1024 * 1024
PATCH_MAX_PX = 512
PATCH_RETRY_AFTER_S = 600
_patch_failures: dict[str, float] = {}


def _media_host() -> str:
    return urlsplit(settings.R2_PUBLIC_URL).hostname or ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Una redirección podría sacar la petición del host permitido: se rechaza."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - firma de urllib
        raise ValueError(f"redirect refused: {code}")


_opener = urllib.request.build_opener(_NoRedirect)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "adventist.club-honor-sheet"})
    with _opener.open(request, timeout=PATCH_TIMEOUT_S) as response:  # noqa: S310 - host allowlisted, sin redirecciones
        mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not mime.startswith("image/"):
            raise ValueError(f"not an image: {mime}")
        body = response.read(PATCH_MAX_BYTES + 1)
    if len(body) > PATCH_MAX_BYTES:
        raise ValueError("image too large")
    return body


def normalize_patch(raw: bytes) -> bytes:
    """Any raster the patch comes in (webp/png/jpeg) as a PNG no larger than PATCH_MAX_PX."""
    with Image.open(BytesIO(raw)) as image:
        image = image.convert("RGBA")
        image.thumbnail((PATCH_MAX_PX, PATCH_MAX_PX))
        out = BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()


def load_patch(url: str | None) -> bytes | None:
    """The honor's patch as PNG bytes, or None (never raises)."""
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") != _media_host():
        return None  # only our own bucket: no SSRF surface through a staff-edited URL
    path = PATCH_CACHE_DIR / f"{hashlib.sha256(url.encode()).hexdigest()}.png"
    try:
        if path.exists():
            return path.read_bytes()
    except OSError:
        pass
    failed_at = _patch_failures.get(url)
    if failed_at is not None and time.monotonic() - failed_at < PATCH_RETRY_AFTER_S:
        return None
    try:
        png = normalize_patch(_download(url))
    except Exception as exc:
        logger.warning("honor sheet: patch unavailable (%s): %s", url, exc)
        _patch_failures[url] = time.monotonic()
        return None
    try:
        PATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        partial.write_bytes(png)
        os.replace(partial, path)
    except OSError as exc:  # a read-only /tmp only costs the cache
        logger.warning("honor sheet: could not cache the patch: %s", exc)
    return png


# ---------------------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------------------
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# a) b) a. (a) i. iv) 1. 2) • - – *   (a single letter, a roman numeral or a number)
_MARKER_RE = re.compile(r"^(?P<marker>\(?(?:[A-Za-z]|[ivxlcIVXLC]{1,6}|\d{1,3})[.)]|[•\-–—·*▪◦])\s+(?P<text>\S.*)$")


def clean_text(value: str | None) -> str:
    value = unicodedata.normalize("NFC", value or "").replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_RE.sub("", value.replace("\t", "    "))


@dataclass
class SubItem:
    level: int              # 0 = continuation paragraph, 1.. = list depth
    marker: str | None
    text: str


def parse_requirement(text: str) -> tuple[str, list[SubItem]]:
    """First line is the requirement; the following lines are its sub-items («a)», «b.», «•»),
    nested by their leading spaces (two per level, as the catalogue stores them)."""
    lines = [line.rstrip() for line in clean_text(text).split("\n") if line.strip()]
    if not lines:
        return "", []
    head, items = lines[0].strip(), []
    for raw in lines[1:]:
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        match = _MARKER_RE.match(body)
        if match:
            items.append(SubItem(min(max(1, indent // 2), 3), match["marker"], match["text"].strip()))
        else:
            items.append(SubItem(min(indent // 2, 3), None, body))
    return head, items


def _width(text: str, font: str, size: float, char_space: float = 0.0) -> float:
    return pdfmetrics.stringWidth(text, font, size) + char_space * len(text)


def wrap(text: str, font: str, size: float, width: float, first_width: float | None = None) -> list[str]:
    """Greedy word wrap; the first line may be narrower (room for a pill); words longer than a
    line (URLs) are broken by characters."""
    lines: list[str] = []
    current = ""

    def limit() -> float:
        return first_width if first_width is not None and not lines else width

    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if _width(candidate, font, size) <= limit():
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while _width(word, font, size) > limit() and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and _width(word[:cut], font, size) > limit():
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def ellipsize(text: str, font: str, size: float, width: float, char_space: float = 0.0) -> str:
    if _width(text, font, size, char_space) <= width:
        return text
    while text and _width(text + "…", font, size, char_space) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def initials(name: str) -> str:
    words = [w for w in re.split(r"[\s\-–,]+", name) if w and w[0].isalnum()]
    stop = {"de", "del", "la", "las", "los", "el", "y", "en", "a", "of", "the", "and"}
    picked = [w for w in words if w.lower() not in stop] or words
    return "".join(w[0] for w in picked[:2]).upper() or "?"


def host_of(url: str | None) -> str | None:
    host = urlsplit(url or "").hostname
    return host[4:] if host and host.startswith("www.") else host


def format_date(value: datetime | None, language: str) -> str:
    if value is None:
        return "—"
    months = LABELS[language]["months"]
    return f"{value.day} {months[value.month - 1]} {value.year}"


@lru_cache(maxsize=256)
def qr_matrix(payload: str) -> tuple[tuple[bool, ...], ...]:
    code = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(payload)
    code.make(fit=True)
    return tuple(tuple(row) for row in code.get_matrix())


# ---------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------
MARGIN_X = 48.0
MARGIN_TOP = 50.0
FOOTER_TOP = 104.0          # content never goes below this line
CONTENT_BOTTOM = FOOTER_TOP + 14.0


class _NumberedCanvas(canvas.Canvas):
    """Defers every page until the end so the footer knows the page count."""

    def __init__(self, *args, footer: Callable[[canvas.Canvas, int, int], None], **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: list[dict] = []
        self._footer = footer

    def showPage(self):  # noqa: N802 - reportlab API
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            # the annotation counter is document-wide: restoring an old page must not rewind it
            annotations = self._annotationCount
            self.__dict__.update(state)
            self._annotationCount = annotations
            self._footer(self, self._pageNumber, total)
            super().showPage()
        super().save()


@dataclass
class _Row:
    """One line of a card: text at `x` (relative to the card's text column)."""
    text: str
    font: str
    size: float
    color: Color
    leading: float
    x: float = 0.0
    marker: str | None = None
    marker_x: float = 0.0
    gap_before: float = 0.0
    link: str | None = None

    @property
    def height(self) -> float:
        return self.leading + self.gap_before


class _SheetRenderer:
    def __init__(self, data: SheetData, paper: Paper, patch_png: bytes | None):
        self.data = data
        self.labels = LABELS[data.language]
        self.accent = category_accent(data.category_slug)
        self.regular, self.bold = sheet_fonts()
        self.page_w, self.page_h = PAGE_SIZES[paper]
        self.content_w = self.page_w - 2 * MARGIN_X
        self.patch_png = patch_png
        self.out = BytesIO()
        self.c = _NumberedCanvas(self.out, pagesize=(self.page_w, self.page_h), footer=self._footer,
                                 pageCompression=1)
        self.y = self.page_h - MARGIN_TOP

    # ----------------------------------------------------------------- page flow
    def new_page(self) -> None:
        self.c.showPage()
        self.y = self.page_h - MARGIN_TOP

    def room(self) -> float:
        return self.y - CONTENT_BOTTOM

    def ensure(self, height: float) -> None:
        if height > self.room():
            self.new_page()

    # ----------------------------------------------------------------- primitives
    def text(self, x: float, y: float, value: str, font: str, size: float, color: Color,
             char_space: float = 0.0, align: str = "left") -> float:
        width = _width(value, font, size, char_space)
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        obj = self.c.beginText(x, y)
        obj.setFont(font, size)
        obj.setFillColor(color)
        if char_space:
            obj.setCharSpace(char_space)
        obj.textOut(value)
        self.c.drawText(obj)
        return width

    def pill(self, x: float, y: float, label: str, *, fill: Color, ink: Color, size: float = 8.0,
             height: float = 20.0, stroke: Color | None = None, dot: Color | None = None,
             min_width: float = 0.0) -> float:
        """A `.cq-pill`: fully rounded, bold caption. (x, y) is the bottom-left. Returns its width."""
        dot_w = 9.0 if dot is not None else 0.0
        natural = _width(label, self.bold, size) + 2 * height * 0.48 + dot_w
        width = max(natural, min_width)
        pad = height * 0.48 + (width - natural) / 2
        self.c.setFillColor(fill)
        if stroke is not None:
            self.c.setStrokeColor(stroke)
            self.c.setLineWidth(0.6)
        self.c.roundRect(x, y, width, height, height / 2, stroke=1 if stroke is not None else 0, fill=1)
        if dot is not None:
            self.c.setFillColor(dot)
            self.c.circle(x + pad + 2.5, y + height / 2, 2.6, stroke=0, fill=1)
        self.text(x + pad + dot_w, y + height / 2 - size * 0.36, label, self.bold, size, ink)
        return width

    def pill_width(self, label: str, size: float = 8.0, height: float = 20.0, dot: bool = False) -> float:
        return _width(label, self.bold, size) + 2 * height * 0.48 + (9.0 if dot else 0.0)

    # ----------------------------------------------------------------- hero
    def hero_pills(self) -> list[tuple[str, bool]]:
        data, labels = self.data, self.labels
        pills: list[tuple[str, bool]] = []
        level = labels["levels"].get(data.difficulty_level or "")
        if level is None and data.skill_level:
            level = labels["level_n"].format(n=data.skill_level)
        if level:
            pills.append((level, True))
        kind = labels["authorities"].get(data.authority or "") or labels["types"].get(data.honor_type or "")
        if kind:
            pills.append((kind, not pills))
        if data.year_introduced:
            pills.append((labels["year"].format(year=data.year_introduced), False))
        if data.code:
            pills.append((labels["code"].format(code=data.code), False))
        return pills

    def draw_hero(self) -> None:
        c, accent, data = self.c, self.accent, self.data
        pad, tile, stripe = 22.0, 118.0, 6.0
        x, w = MARGIN_X, self.content_w
        text_x = x + pad + tile + 22
        text_w = x + w - pad - text_x

        eyebrow = self.labels["eyebrow"].upper()
        if data.category_name:
            eyebrow += " · " + data.category_name.upper()
        eyebrow_size, eyebrow_space = 7.5, 7.5 * 0.16
        eyebrow = ellipsize(eyebrow, self.bold, eyebrow_size, text_w, eyebrow_space)

        size = 28.0
        while True:
            title_lines = wrap(data.name, self.bold, size, text_w)
            if len(title_lines) <= 3 or size <= 18:
                break
            size -= 1
        title_lines = title_lines[:3] if len(title_lines) <= 3 else title_lines[:2] + [
            ellipsize(" ".join(title_lines[2:]), self.bold, size, text_w)]
        leading = size * 1.08

        pills = self.hero_pills()
        pill_h, pill_gap = 20.0, 6.0
        rows, row_w = [[]], 0.0
        for label, solid in pills:
            label = ellipsize(label, self.bold, 8.0, text_w - 2 * pill_h * 0.48)
            pw = self.pill_width(label)
            if rows[-1] and row_w + pill_gap + pw > text_w:
                rows.append([])
                row_w = 0.0
            row_w += (pill_gap if rows[-1] else 0) + pw
            rows[-1].append((label, solid, pw))
        rows = [r for r in rows if r]
        pills_h = len(rows) * pill_h + max(len(rows) - 1, 0) * pill_gap

        text_h = eyebrow_size + 10 + len(title_lines) * leading + (12 + pills_h if rows else 0)
        inner_h = max(tile, text_h)
        h = stripe + pad + inner_h + pad
        top = self.y
        bottom = top - h

        # card: tinted gradient + a glow in the corner (the app's --app-gradient), accent stripe
        c.saveState()
        path = c.beginPath()
        path.roundRect(x, bottom, w, h, 28)
        c.clipPath(path, stroke=0, fill=0)
        base = tint(accent.bright, 0.08)
        c.setFillColor(base)
        c.rect(x, bottom, w, h, stroke=0, fill=1)
        # the glow ends exactly on the base colour, so its edge is invisible
        c.radialGradient(x + w - 40, top - 10, 260, (tint(accent.bright, 0.30), base), extend=False)
        c.setFillColor(accent.bright)
        c.rect(x, top - stripe, w, stripe, stroke=0, fill=1)
        c.restoreState()

        # patch tile
        tile_x, tile_top = x + pad, top - stripe - pad - (inner_h - tile) / 2
        tile_y = tile_top - tile
        c.setFillColor(WHITE)
        c.setStrokeColor(tint(accent.bright, 0.35))
        c.setLineWidth(0.8)
        c.roundRect(tile_x, tile_y, tile, tile, 24, stroke=1, fill=1)
        drawn = False
        if self.patch_png:
            try:
                inset = 9.0
                c.drawImage(ImageReader(BytesIO(self.patch_png)), tile_x + inset, tile_y + inset,
                            width=tile - 2 * inset, height=tile - 2 * inset,
                            preserveAspectRatio=True, anchor="c", mask="auto")
                drawn = True
            except Exception as exc:  # a corrupt cached file still yields a sheet
                logger.warning("honor sheet: patch not drawable: %s", exc)
        if not drawn:
            c.setFillColor(tint(accent.bright, 0.18))
            c.roundRect(tile_x + 7, tile_y + 7, tile - 14, tile - 14, 18, stroke=0, fill=1)
            self.text(tile_x + tile / 2, tile_y + tile / 2 - 13, initials(data.name), self.bold, 36,
                      accent.ink, align="center")

        # text column, vertically centred against the tile
        cursor = top - stripe - pad - (inner_h - text_h) / 2
        self.text(text_x, cursor - eyebrow_size, eyebrow, self.bold, eyebrow_size, accent.ink, eyebrow_space)
        cursor -= eyebrow_size + 10
        for line in title_lines:
            self.text(text_x, cursor - size * 0.9, line, self.bold, size, TEXT, char_space=-size * 0.02)
            cursor -= leading
        if rows:
            cursor -= 12
            for row in rows:
                px = text_x
                for label, solid, pw in row:
                    if solid:
                        self.pill(px, cursor - pill_h, label, fill=accent.ink, ink=WHITE, height=pill_h)
                    else:
                        self.pill(px, cursor - pill_h, label, fill=WHITE, ink=TEXT_2, height=pill_h,
                                  stroke=tint(accent.bright, 0.35))
                    px += pw + pill_gap
                cursor -= pill_h + pill_gap
        self.y = bottom - 18

    # ----------------------------------------------------------------- paragraphs
    def paragraph(self, value: str, *, size: float = 10.5, color: Color = TEXT_2, leading: float | None = None,
                  font: str | None = None, gap_after: float = 0.0) -> None:
        font = font or self.regular
        leading = leading or size * 1.45
        for block in [b for b in clean_text(value).split("\n") if b.strip()]:
            for line in wrap(block.strip(), font, size, self.content_w):
                self.ensure(leading)
                self.text(MARGIN_X, self.y - leading / 2 - size * 0.36, line, font, size, color)
                self.y -= leading
        self.y -= gap_after

    def section_title(self, title: str, meta: str | None = None, keep_with: float = 60.0) -> None:
        self.ensure(34 + keep_with)
        self.y -= 6
        self.text(MARGIN_X, self.y - 16, title, self.bold, 17, TEXT, char_space=-17 * 0.02)
        if meta:
            self.text(MARGIN_X + self.content_w, self.y - 15, meta, self.bold, 8.5, MUTED, align="right")
        self.y -= 30

    # ----------------------------------------------------------------- cards
    def card_rows(self, requirement: SheetRequirement, first_width: float, width: float) -> list[_Row]:
        head, items = parse_requirement(requirement.description)
        rows: list[_Row] = []
        for line in wrap(head, self.regular, 10.5, width, first_width):
            rows.append(_Row(line, self.regular, 10.5, TEXT, 14.8))
        marker_col, level_indent = 18.0, 16.0
        for item in items:
            gap = 2.5
            if item.marker is None:
                x = (item.level - 1) * level_indent + marker_col if item.level else 0.0
                for j, line in enumerate(wrap(item.text, self.regular, 10, width - x)):
                    rows.append(_Row(line, self.regular, 10, TEXT_2, 14.0, x=x, gap_before=gap if j == 0 else 0))
                continue
            marker_x = (item.level - 1) * level_indent
            marker_w = max(marker_col, _width(item.marker, self.bold, 9.5) + 5)
            x = marker_x + marker_w
            for j, line in enumerate(wrap(item.text, self.regular, 10, width - x)):
                rows.append(_Row(line, self.regular, 10, TEXT_2, 14.0, x=x, gap_before=gap if j == 0 else 0,
                                 marker=item.marker if j == 0 else None, marker_x=marker_x))
        instructions = clean_text(requirement.instructions).strip()
        if instructions:
            first = True
            for block in [b for b in instructions.split("\n") if b.strip()]:
                for line in wrap(block.strip(), self.regular, 8.5, width):
                    rows.append(_Row(line, self.regular, 8.5, MUTED, 11.8, gap_before=5 if first else 0))
                    first = False
        return rows

    def draw_card(self, rows: list[_Row], *, badge: str | None, practical: bool,
                  pad_y: float = 12.0, text_offset: float = 50.0, radius: float = 14.0,
                  fill: Color = CARD, link: str | None = None, spacing: float = 8.0,
                  lead: Callable[[float, float], None] | None = None) -> None:
        """Draw a rounded card holding `rows`, splitting it across pages when needed."""
        x, w = MARGIN_X, self.content_w
        full_page = self.page_h - MARGIN_TOP - CONTENT_BOTTOM
        total = 2 * pad_y + sum(r.height for r in rows)
        min_first = 2 * pad_y + sum(r.height for r in rows[:3])
        if total > self.room() and (total <= full_page or self.room() < min_first):
            self.new_page()
        first_segment = True
        remaining = list(rows)
        while remaining:
            take, used = [], 0.0
            for row in remaining:
                if take and 2 * pad_y + used + row.height > self.room():
                    break
                take.append(row)
                used += row.height
            remaining = remaining[len(take):]
            h = 2 * pad_y + used
            top = self.y
            self.c.setFillColor(fill)
            self.c.roundRect(x, top - h, w, h, radius, stroke=0, fill=1)
            if link:
                self.c.linkURL(link, (x, top - h, x + w, top), relative=0)
            cursor = top - pad_y
            first_row_mid = cursor - (take[0].gap_before + take[0].leading / 2)
            if first_segment and badge is not None:
                self.c.setFillColor(tint(self.accent.bright, 0.18))
                self.c.circle(x + 14 + 12, first_row_mid, 12, stroke=0, fill=1)
                self.text(x + 26, first_row_mid - 10 * 0.36, badge, self.bold, 10 if len(badge) < 3 else 8.5,
                          self.accent.ink, align="center")
            if first_segment and practical:
                label = self.labels["practical"]
                pw = self.pill_width(label, size=7.5, height=18, dot=True)
                self.pill(x + w - 12 - pw, first_row_mid - 9, label, fill=tint(SUCCESS, 0.16), ink=SUCCESS_INK,
                          size=7.5, height=18, dot=SUCCESS)
            if first_segment and lead is not None:
                lead(x, first_row_mid)
            for row in take:
                cursor -= row.gap_before
                baseline = cursor - row.leading / 2 - row.size * 0.36
                if row.marker:
                    self.text(x + text_offset + row.marker_x, baseline, row.marker, self.bold, row.size - 0.5,
                              self.accent.ink)
                self.text(x + text_offset + row.x, baseline, row.text, row.font, row.size, row.color)
                cursor -= row.leading
            self.y = top - h - spacing
            first_segment = False
            if remaining:
                self.new_page()

    # ----------------------------------------------------------------- sections
    def draw_requirements(self) -> None:
        data, labels = self.data, self.labels
        reqs = data.requirements
        if not reqs:
            self.section_title(labels["requirements"], keep_with=90)
            self.draw_pending()
            return
        practical = sum(1 for r in reqs if not r.is_theoretical)
        meta = labels["count_one"] if len(reqs) == 1 else labels["count"].format(n=len(reqs))
        if practical:
            meta += " · " + (labels["count_practical_one"] if practical == 1
                             else labels["count_practical"].format(n=practical))
        self.section_title(labels["requirements"], meta, keep_with=70)

        req_language = (data.requirements_locale or "").lower().split("-")[0]
        if req_language and req_language != data.language:
            language = labels["languages"].get(req_language, data.requirements_locale)
            self.paragraph(labels["foreign"].format(language=language), size=8.5, color=INFO_INK, gap_after=6)

        text_offset = 50.0
        width = self.content_w - text_offset - 14
        pill_room = self.pill_width(labels["practical"], size=7.5, height=18, dot=True) + 10
        for index, requirement in enumerate(reqs, 1):
            first_width = width - pill_room if not requirement.is_theoretical else width
            rows = self.card_rows(requirement, first_width, width)
            self.draw_card(rows, badge=str(index), practical=not requirement.is_theoretical,
                           text_offset=text_offset)

        sources = []
        for r in reqs:
            if not (r.source or r.license):
                continue
            parts = [SOURCE_NAMES.get(r.source or "", r.source or "")]
            if r.license:
                parts.append(r.license)
            if host_of(r.source_url):
                parts.append(host_of(r.source_url))
            label = " · ".join(p for p in parts if p)
            if label not in sources:
                sources.append(label)
        if sources:
            self.y -= 2
            self.paragraph(labels["req_source"].format(source="; ".join(sources)), size=7.5, color=MUTED)

    def draw_pending(self) -> None:
        labels, url = self.labels, self.data.page_url
        width = self.content_w - 2 * 20
        rows = [_Row(labels["pending_title"], self.bold, 12.5, TEXT, 18)]
        for i, line in enumerate(wrap(labels["pending_body"], self.regular, 9.5, width)):
            rows.append(_Row(line, self.regular, 9.5, MUTED, 13.5, gap_before=4 if i == 0 else 0))
        rows.append(_Row(url.removeprefix("https://"), self.bold, 9.5, self.accent.ink, 13.5, gap_before=2))
        self.draw_card(rows, badge=None, practical=False, pad_y=18, text_offset=20, radius=20,
                       fill=tint(self.accent.bright, 0.07), link=url)

    def draw_resources(self) -> None:
        resources = self.data.resources
        if not resources:
            return
        self.y -= 8
        self.section_title(self.labels["resources"], keep_with=50)
        types = self.labels["resource_types"]
        pill_col = max(self.pill_width(types.get((r.type or "link").lower(), (r.type or "link").upper()),
                                       size=7.5, height=18) for r in resources)
        text_offset = 14 + pill_col + 12
        width = self.content_w - text_offset - 14
        for resource in resources:
            kind = types.get((resource.type or "link").lower(), (resource.type or "link").upper())
            name = clean_text(resource.name).strip() or host_of(resource.url) or resource.url
            rows = [_Row(line, self.bold, 10, TEXT, 13.5) for line in wrap(name, self.bold, 10, width)[:2]]
            rows.append(_Row(ellipsize(resource.url, self.regular, 8, width), self.regular, 8, MUTED, 11.5,
                             gap_before=1))

            def lead(x: float, mid: float, kind: str = kind) -> None:
                self.pill(x + 14, mid - 9, kind, fill=HexColor("#e9eaee"), ink=TEXT_2, size=7.5, height=18,
                          min_width=pill_col)

            self.draw_card(rows, badge=None, practical=False, pad_y=11, text_offset=text_offset,
                           link=resource.url if resource.url.startswith(("https://", "http://")) else None,
                           spacing=6, lead=lead)

    # ----------------------------------------------------------------- footer
    def _footer(self, c: canvas.Canvas, page: int, total: int) -> None:
        data, labels = self.data, self.labels
        left, right = MARGIN_X, self.page_w - MARGIN_X
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(left, FOOTER_TOP, right, FOOTER_TOP)

        qr_size, qr_y = 50.0, 42.0
        matrix = qr_matrix(data.page_url)
        cell = qr_size / len(matrix)
        c.setFillColor(TEXT)
        for r, row in enumerate(matrix):
            col = 0
            while col < len(row):
                if row[col]:
                    start = col
                    while col < len(row) and row[col]:
                        col += 1
                    c.rect(left + start * cell, qr_y + qr_size - (r + 1) * cell, (col - start) * cell, cell,
                           stroke=0, fill=1)
                else:
                    col += 1
        c.linkURL(data.page_url, (left, qr_y, left + qr_size, qr_y + qr_size), relative=0)

        tx = left + qr_size + 12
        brand = "Adventist.Club"
        w = self.text(tx, 80, brand, self.bold, 8.5, TEXT)
        url_text = " · " + data.page_url.removeprefix("https://www.")
        url_text = ellipsize(url_text, self.regular, 8.5, right - 120 - tx - w)
        self.text(tx + w, 80, url_text, self.regular, 8.5, TEXT_2)
        c.linkURL(data.page_url, (tx, 76, tx + w + _width(url_text, self.regular, 8.5), 90), relative=0)
        self.text(tx, 67, labels["version"].format(n=data.version, date=format_date(data.updated_at, data.language)),
                  self.regular, 7.5, MUTED)
        host = host_of(data.source_url)
        if host:
            self.text(tx, 55, labels["source"].format(host=host), self.regular, 7.5, MUTED)

        self.text(right, 80, labels["page"].format(x=page, y=total), self.bold, 8, TEXT_2, align="right")
        self.text(right, 67, ellipsize(data.name, self.regular, 7.5, 150), self.regular, 7.5, MUTED, align="right")

    # ----------------------------------------------------------------- document
    def render(self) -> bytes:
        c, data = self.c, self.data
        c.setTitle(f"{self.labels['title']} · {data.name}")
        c.setAuthor("Adventist.Club")
        c.setSubject(data.page_url)
        c.setCreator("Adventist.Club API")
        c.setKeywords([data.slug] + ([data.category_name] if data.category_name else []))
        self.draw_hero()
        description = clean_text(data.description).strip()
        if description:
            self.paragraph(description, size=10.5, color=TEXT_2, gap_after=10)
        self.draw_requirements()
        self.draw_resources()
        c.showPage()
        c.save()
        return self.out.getvalue()


def render_sheet_pdf(data: SheetData, *, paper: Paper = "a4", patch_image: bytes | None = None,
                     fetch_image: bool = True) -> bytes:
    """Draw the sheet. `patch_image` (any raster) wins over fetching `data.image_url`."""
    patch = None
    if patch_image:
        try:
            patch = normalize_patch(patch_image)
        except Exception as exc:
            logger.warning("honor sheet: supplied patch unreadable: %s", exc)
    elif fetch_image:
        patch = load_patch(data.image_url)
    return _SheetRenderer(data, paper, patch).render()
