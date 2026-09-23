"""Printable PDFs of an honor, generated from our own data, in two modes:

  * «hoja» (default) — the WORKSHEET («hoja de trabajo») a Pathfinder fills in by hand, strictly
    black / white / grey (the patch is the only colour): header without a background (patch,
    category, title, «Nivel · año»), ruled fields (name, club, unit, instructor, dates), every
    requirement and sub-item with a checkbox and ruled answer lines (practical ones with an
    evidence/verification row), and a last-page approval block with signatures and a notes box.
    See `_WorksheetRenderer`.
  * «ficha» — the compact catalogue sheet (hero card, requirement cards, resources) in the
    coloured design described below.

Neither mode shows references for now (owner's decision): no «Fuente», no attribution of the
requirement text, no `source_url`; resources hosted on guiasmayores.com are never listed
(BLOCKED_HOSTS). The rows' `source`/`source_url`/`license` stay in `SheetRequirement` so the
attribution can come back without touching the loader.

The «ficha» uses the visual language of conquistadores.app, in its LIGHT theme (it is printed):

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
RENDERER_VERSION = "2"
HONOR_PAGE_BASE = "https://www.conquistadores.app/honors/"
Paper = Literal["a4", "letter"]
Mode = Literal["hoja", "ficha"]
PAGE_SIZES = {"a4": A4, "letter": LETTER}
# Never shown nor linked on a sheet (owner's decision): the platform replaces that site.
BLOCKED_HOSTS = ("guiasmayores.com",)


def is_blocked(url_or_host: str | None) -> bool:
    """True for a URL, a bare host or a `source` label that points to a blocked site."""
    value = (url_or_host or "").strip().lower()
    if not value:
        return False
    host = urlsplit(value).hostname if "://" in value else value.split("/")[0]
    host = host or ""
    return any(host == blocked or host.endswith("." + blocked) for blocked in BLOCKED_HOSTS)


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


CHECKBOX_CHAR = "☐"


@lru_cache(maxsize=1)
def marker_font() -> str | None:
    """A private copy of Noto Sans in which «☐» (absent from it) points at the space glyph.
    It is used only for the invisible text layer of the worksheet's checkboxes, so copying,
    searching or reading the PDF aloud meets a «☐» where a box is drawn. None if unavailable."""
    path = FONTS_DIR / "NotoSans-Regular.ttf"
    if not path.exists():
        return None
    with _font_lock:
        try:
            font = TTFont("CQSans-Marks", str(path))
            # reportlab shares one font object per face name: rename it, or the patch below would
            # be ignored in favour of the already registered Noto Sans
            font.face.name = b"CQSansMarks"
            space = ord(" ")
            font.face.charToGlyph[ord(CHECKBOX_CHAR)] = font.face.charToGlyph[space]
            font.face.charWidths[ord(CHECKBOX_CHAR)] = font.face.charWidths[space]
            pdfmetrics.registerFont(font)
            return "CQSans-Marks"
        except Exception:
            logger.exception("could not register the checkbox marker font")
            return None


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
        "version": "Versión {n} · actualizada {date}",
        "page": "Página {x} de {y}",
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
        # worksheet («hoja de trabajo»)
        "ws_title": "Hoja de trabajo",
        "fields": {"name": "Nombre", "club": "Club", "unit": "Unidad", "instructor": "Instructor/a",
                   "start": "Fecha de inicio", "end": "Fecha de finalización"},
        "evidence": "Evidencia:",
        "evidence_kinds": ["foto", "demostración", "informe"],
        "verified_by": "Verificado por:",
        "date": "Fecha:",
        "approval": "Aprobación",
        "sign_instructor": "Instructor/a",
        "sign_director": "Director/a del club",
        "signature": "Firma",
        "notes": "Notas",
        "generated": "Generado en ",
        "version_short": "Versión {n} · {date}",
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
        "version": "Version {n} · updated {date}",
        "page": "Page {x} of {y}",
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
        "ws_title": "Worksheet",
        "fields": {"name": "Name", "club": "Club", "unit": "Unit", "instructor": "Instructor",
                   "start": "Start date", "end": "Completion date"},
        "evidence": "Evidence:",
        "evidence_kinds": ["photo", "demonstration", "report"],
        "verified_by": "Verified by:",
        "date": "Date:",
        "approval": "Approval",
        "sign_instructor": "Instructor",
        "sign_director": "Club director",
        "signature": "Signature",
        "notes": "Notes",
        "generated": "Generated at ",
        "version_short": "Version {n} · {date}",
    },
}


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

    def fingerprint(self, paper: Paper = "a4", mode: Mode = "hoja") -> str:
        """Everything the PDF depends on. Used as the ETag: any edit changes it."""
        payload = json.dumps({"v": RENDERER_VERSION, "paper": paper, "mode": mode, **asdict(self)},
                             sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def visible_resources(self) -> list[SheetResource]:
        return [r for r in self.resources if not is_blocked(r.url)]


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
        source_url=None if is_blocked(honor.source_url) else honor.source_url,
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
        resources=[SheetResource(r.name, r.url, r.type) for r in resources if not is_blocked(r.url)],
    )


async def render_honor_sheet(db: AsyncSession, honor: Honor, locale: str = SOURCE_LOCALE, *,
                             paper: Paper = "a4", mode: Mode = "hoja", patch_image: bytes | None = None,
                             fetch_image: bool = True) -> bytes:
    """The sheet as PDF bytes. CPU-bound: API callers run `render_sheet_pdf` in a thread."""
    data = await load_sheet_data(db, honor, locale)
    return render_sheet_pdf(data, paper=paper, mode=mode, patch_image=patch_image, fetch_image=fetch_image)


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


# «… términos: a) Barbotina b) Engobe c) Bizcocho» — a marker after a space or punctuation
_INLINE_MARKER_RE = re.compile(r"(?:^|(?<=[\s:;,.]))\(?(?P<letter>[a-z])(?P<punct>[.)])\s+")


def split_inline_items(line: str) -> tuple[str, list[SubItem]]:
    """Sub-items written on the same line as the requirement. Only a run of consecutive letters
    starting at «a» with the same punctuation counts (at least «a)» and «b)»), so a stray
    «(ver punto b)» or an abbreviation never splits the text. Inline «•» bullets split too."""
    matches = list(_INLINE_MARKER_RE.finditer(line))
    for start, first in enumerate(matches):
        if first["letter"] != "a":
            continue
        run = [first]
        for match in matches[start + 1:]:
            if match["punct"] == first["punct"] and ord(match["letter"]) == ord(run[-1]["letter"]) + 1:
                run.append(match)
        if len(run) < 2:
            continue
        head = line[:first.start()].strip()
        items = []
        for i, match in enumerate(run):
            end = run[i + 1].start() if i + 1 < len(run) else len(line)
            body = line[match.end():end].strip().rstrip(";,").strip()
            if body:
                marker = f"{match['letter']}{match['punct']}"
                items.append(SubItem(1, marker, body))
        if len(items) >= 2:
            return head, items
    if "•" in line.lstrip("•").strip():
        head, *rest = [part.strip() for part in line.split("•")]
        items = [SubItem(1, "•", part) for part in rest if part]
        if len(items) >= 1 and head:
            return head, items
    return line, []


def parse_requirement(text: str) -> tuple[str, list[SubItem]]:
    """First line is the requirement; the following lines are its sub-items («a)», «b.», «•»),
    nested by their leading spaces (two per level, as the catalogue stores them). Sub-items
    written inline in the first line («… a) uno b) dos») are split out as well."""
    lines = [line.rstrip() for line in clean_text(text).split("\n") if line.strip()]
    if not lines:
        return "", []
    head, items = split_inline_items(lines[0].strip())
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
    low, high = 0, len(text)              # longest prefix that fits with the ellipsis (binary search)
    while low < high:
        mid = (low + high + 1) // 2
        if _width(text[:mid] + "…", font, size, char_space) <= width:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + "…"


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


def draw_qr(c: canvas.Canvas, url: str, x: float, y: float, size: float, color: Color = TEXT) -> None:
    """A vector QR (runs of dark modules merged into rects) linking to `url`."""
    matrix = qr_matrix(url)
    cell = size / len(matrix)
    c.setFillColor(color)
    for r, row in enumerate(matrix):
        col = 0
        while col < len(row):
            if row[col]:
                start = col
                while col < len(row) and row[col]:
                    col += 1
                c.rect(x + start * cell, y + size - (r + 1) * cell, (col - start) * cell, cell, stroke=0, fill=1)
            else:
                col += 1
    c.linkURL(url, (x, y, x + size, y + size), relative=0)


# ---------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------
MARGIN_X = 34.0  # 12 mm: el propietario pidió menos margen lateral (antes 17 mm)
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
    """The compact «ficha» (modo=ficha)."""

    content_bottom = CONTENT_BOTTOM

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
        return self.y - self.content_bottom

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
        full_page = self.page_h - MARGIN_TOP - self.content_bottom
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
        resources = self.data.visible_resources
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
        draw_qr(c, data.page_url, left, qr_y, qr_size)

        tx = left + qr_size + 12
        brand = "Adventist.Club"
        w = self.text(tx, 80, brand, self.bold, 8.5, TEXT)
        url_text = " · " + data.page_url.removeprefix("https://www.")
        url_text = ellipsize(url_text, self.regular, 8.5, right - 120 - tx - w)
        self.text(tx + w, 80, url_text, self.regular, 8.5, TEXT_2)
        c.linkURL(data.page_url, (tx, 76, tx + w + _width(url_text, self.regular, 8.5), 90), relative=0)
        self.text(tx, 67, labels["version"].format(n=data.version, date=format_date(data.updated_at, data.language)),
                  self.regular, 7.5, MUTED)

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


# ---------------------------------------------------------------------------------------
# Worksheet («hoja de trabajo», modo=hoja): what a Pathfinder fills in by hand
# ---------------------------------------------------------------------------------------
MM = 72.0 / 25.4
WS_FOOTER_RULE = 56.0           # thin footer rule on every page (text below it at 42 pt)
WS_CONTENT_BOTTOM = 70.0
WS_COLOPHON_TOP = 134.0         # the last page's QR block (top at 116 pt) lives under this line
ANSWER_GAP = 8 * MM             # ruled answer lines, 8 mm apart
# Black / white / grey (owner's decision): no bands, no tinted cards. The only colour is the patch
# and the small pills (level/kind/year in the category colour, «Práctico» in green).
WS_INK = HexColor("#111111")      # text, checkboxes, numbers, QR
WS_LABEL = HexColor("#555555")    # labels, category, level/year, instructions
WS_HINT = HexColor("#999999")     # small print, baselines of the fields and signatures
WS_RULE = HexColor("#d9d9d9")     # answer lines, notes box, footer rule
ANSWER_INK = WS_RULE
FIELD_INK = WS_HINT
BOX_INK = WS_INK
NOTES_BORDER = WS_RULE
NOTES_RULE = WS_RULE
CHECKBOX = 4 * MM
SUB_CHECKBOX = 3.2 * MM
EVIDENCE_BOX = 3 * MM
NOTES_H = 60 * MM
NOTES_MIN_H = 48 * MM           # the notes box shrinks this far before the closing block moves page
LONG_REQUIREMENT = 220          # characters: a requirement this long gets 6 answer lines

# «explica / describe / menciona / enumera / define» (and English) → 6 answer lines instead of 4
_LONG_ANSWER_RE = re.compile(r"\b(explic|describ|mencion|enumer|defin|explain|enumerat|list\b|name\b)")
# a sub-item that is itself a question or an instruction to answer in writing
_QUESTION_RE = re.compile(
    r"^(¿|que\b|como\b|cual|cuando|donde|quien|cuant|por que\b|para que\b|explic|describ|mencion|enumer|"
    r"defin|nombr|identific|compar|diferenci|what\b|how\b|why\b|which\b|when\b|where\b|who\b|explain|"
    r"describ|list\b|name\b|defin|identif|compar)")
# a parent asking for the meaning of a list of terms: each short term gets its own inline rule
_TERMS_RE = re.compile(r"(defin|significado|termino|explic|describ|identific|nombr|mencion|meaning|terms?\b|"
                       r"explain|identif|name\b)")


def _plain(value: str) -> str:
    """Lowercase without accents, for keyword matching («Qué» → «que»)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", value.lower()) if not unicodedata.combining(ch))


def answer_line_count(requirement: SheetRequirement, sub_items_answered: bool = False) -> int:
    """Ruled lines under a requirement: practical → 2 (plus the evidence row); theoretical → 4,
    or 6 when the text is long or asks to explain/describe/list/define; 2 when its sub-items
    already carry their own lines (the rest is room for a conclusion)."""
    if not requirement.is_theoretical or sub_items_answered:
        return 2
    text = clean_text(requirement.description)
    return 6 if len(text) > LONG_REQUIREMENT or _LONG_ANSWER_RE.search(_plain(text)) else 4


def sub_item_answer(text: str, parent: str) -> str | None:
    """«lines» (2 ruled lines under it) for a sub-item that reads as a question, «inline» (a
    rule after it) for a short term whose meaning the parent asks for, else None."""
    plain = _plain(text.strip())
    if "?" in text or _QUESTION_RE.match(plain):
        return "lines"
    parent_plain = _plain(parent)
    short = len(text.split()) <= 5 and len(text) <= 45 and not text.rstrip().endswith(".")
    if short and _TERMS_RE.search(parent_plain):
        return "inline"
    if not short and _LONG_ANSWER_RE.search(parent_plain):
        return "lines"          # «Describir: a) Qué ocurre… b) La diferencia entre…» — each is a topic
    return None


@dataclass
class _Flow:
    """One unbreakable strip of the worksheet: `draw(top)` paints it below `top`."""
    height: float
    draw: Callable[[float], None]
    keep_with_next: bool = False
    gap_before: float = 0.0


class _WorksheetRenderer(_SheetRenderer):
    content_bottom = WS_CONTENT_BOTTOM

    def __init__(self, data: SheetData, paper: Paper, patch_png: bytes | None):
        super().__init__(data, paper, patch_png)
        self.left = MARGIN_X
        self.right = MARGIN_X + self.content_w
        self.marker_font = marker_font() if self.regular != "Helvetica" else None
        widest = max([f"{len(data.requirements)}.", "00."], key=lambda s: _width(s, self.bold, 10.5))
        self.text_x = self.left + CHECKBOX + 7 + _width(widest, self.bold, 10.5) + 7

    # ----------------------------------------------------------------- primitives
    def at_top(self) -> bool:
        return self.y >= self.page_h - MARGIN_TOP - 0.01

    def checkbox(self, x: float, y: float, size: float, width: float = 0.6) -> None:
        """An empty square to tick by hand; (x, y) is its bottom-left. It also carries an
        invisible «☐», so text extraction, search and screen readers see the checkbox."""
        c = self.c
        c.setStrokeColor(BOX_INK)
        c.setLineWidth(width)
        c.rect(x, y, size, size, stroke=1, fill=0)
        if self.marker_font:
            c.saveState()
            obj = c.beginText(x, y + size * 0.12)
            obj.setTextRenderMode(3)
            obj.setFont(self.marker_font, size)
            obj.textOut(CHECKBOX_CHAR)
            c.drawText(obj)
            c.restoreState()

    def small_caps(self, x: float, baseline: float, value: str, size: float, color: Color,
                   tracking: float = 0.5) -> float:
        """Label in small caps: capitals stay full size, lowercase letters become smaller
        capitals («Fecha de inicio» → F + ECHA DE INICIO). Returns its width."""
        obj = self.c.beginText(x, baseline)
        obj.setFillColor(color)
        obj.setCharSpace(tracking)
        width = 0.0
        for run in re.findall(r"[^a-záéíóúüñ]+|[a-záéíóúüñ]+", value):
            run_size = size * 0.8 if run[0].islower() else size
            run = run.upper()
            obj.setFont(self.bold, run_size)
            obj.textOut(run)
            width += _width(run, self.bold, run_size, tracking)
        obj.setCharSpace(0)
        self.c.drawText(obj)
        return width

    def rule(self, x0: float, x1: float, y: float, color: Color = FIELD_INK, width: float = 0.5) -> None:
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x0, y, x1, y)

    def flow(self, items: list[_Flow]) -> None:
        """Lay strips top to bottom. A run of `keep_with_next` strips (a requirement's text and
        its first two answer lines) moves to the next page as a whole when it does not fit."""
        full_page = self.page_h - MARGIN_TOP - self.content_bottom
        i = 0
        while i < len(items):
            j, chain = i, items[i].height + items[i].gap_before
            while items[j].keep_with_next and j + 1 < len(items):
                j += 1
                chain += items[j].height + items[j].gap_before
            if chain > self.room() and chain - items[i].gap_before <= full_page and not self.at_top():
                self.new_page()
            for item in items[i:j + 1]:
                gap = 0.0 if self.at_top() else item.gap_before
                if gap + item.height > self.room() and not self.at_top():
                    self.new_page()
                    gap = 0.0
                self.y -= gap
                item.draw(self.y)
                self.y -= item.height
            i = j + 1

    # ----------------------------------------------------------------- header (no background)
    def ws_header(self) -> None:
        """No band or box: the patch at the left, the category in grey small caps, the title and
        the small coloured pills of the «ficha» (level, kind, year) in the category colour."""
        data, labels = self.data, self.labels
        top, patch = self.y, 26 * MM
        text_x = self.left + patch + 18
        text_w = self.right - text_x

        eyebrow = labels["eyebrow"]
        if data.category_name:
            eyebrow += " · " + data.category_name
        eyebrow_size = 8.0
        while _width(eyebrow.upper(), self.bold, eyebrow_size, 0.6) > text_w and len(eyebrow) > 4:
            eyebrow = eyebrow[:-2].rstrip() + "…"

        size = 24.0
        while True:
            title_lines = wrap(data.name, self.bold, size, text_w)
            if len(title_lines) <= 2 or size <= 16:
                break
            size -= 1
        if len(title_lines) > 2:
            title_lines = [title_lines[0], ellipsize(" ".join(title_lines[1:]), self.bold, size, text_w)]
        leading = size * 1.1

        pill_h, pill_gap, pill_size = 17.0, 5.0, 7.5
        rows: list[list[tuple[str, bool, float]]] = [[]]
        row_w = 0.0
        for label, solid in self.hero_pills()[:3]:            # level, kind, year (no code)
            label = ellipsize(label, self.bold, pill_size, text_w - 2 * pill_h * 0.48)
            pw = self.pill_width(label, size=pill_size, height=pill_h)
            if rows[-1] and row_w + pill_gap + pw > text_w:
                rows.append([])
                row_w = 0.0
            row_w += (pill_gap if rows[-1] else 0) + pw
            rows[-1].append((label, solid, pw))
        rows = [r for r in rows if r]
        pills_h = len(rows) * pill_h + max(len(rows) - 1, 0) * pill_gap

        text_h = eyebrow_size + 8 + len(title_lines) * leading + (8 + pills_h if rows else 0)
        block_h = max(patch, text_h)

        patch_y = top - (block_h - patch) / 2 - patch
        drawn = False
        if self.patch_png:
            try:
                self.c.drawImage(ImageReader(BytesIO(self.patch_png)), self.left, patch_y, width=patch,
                                 height=patch, preserveAspectRatio=True, anchor="c", mask="auto")
                drawn = True
            except Exception as exc:  # a corrupt cached file still yields a sheet
                logger.warning("honor sheet: patch not drawable: %s", exc)
        if not drawn:
            self.c.setStrokeColor(WS_RULE)
            self.c.setLineWidth(0.8)
            self.c.circle(self.left + patch / 2, patch_y + patch / 2, patch / 2 - 2, stroke=1, fill=0)
            self.text(self.left + patch / 2, patch_y + patch / 2 - 9, initials(data.name), self.bold, 26,
                      WS_LABEL, align="center")

        cursor = top - (block_h - text_h) / 2
        self.small_caps(text_x, cursor - eyebrow_size, eyebrow, eyebrow_size, WS_LABEL, tracking=0.6)
        cursor -= eyebrow_size + 8
        for line in title_lines:
            self.text(text_x, cursor - size * 0.92, line, self.bold, size, WS_INK, char_space=-size * 0.02)
            cursor -= leading
        if rows:
            cursor -= 8
            for row in rows:
                px = text_x
                for label, solid, pw in row:
                    if solid:
                        self.pill(px, cursor - pill_h, label, fill=self.accent.ink, ink=WHITE, size=pill_size,
                                  height=pill_h)
                    else:
                        self.pill(px, cursor - pill_h, label, fill=WHITE, ink=TEXT_2, size=pill_size,
                                  height=pill_h, stroke=tint(self.accent.bright, 0.35))
                    px += pw + pill_gap
                cursor -= pill_h + pill_gap
        self.y = top - block_h - 22

    def ws_fields(self) -> None:
        """Nombre / Club, Unidad / Instructor/a, Fecha de inicio / Fecha de finalización."""
        fields = self.labels["fields"]
        rows = [("name", "club"), ("unit", "instructor"), ("start", "end")]
        gap = 24.0
        col_w = (self.content_w - gap) / 2
        pitch, top = 27.0, self.y
        for r, pair in enumerate(rows):
            base = top - 20 - r * pitch
            for k, key in enumerate(pair):
                cx = self.left + k * (col_w + gap)
                width = self.small_caps(cx, base + 2, fields[key], 7.5, WS_LABEL)
                self.rule(cx + width + 6, cx + col_w, base)
        self.y = top - 20 - (len(rows) - 1) * pitch - 22

    def ws_section_title(self, title: str, meta: str | None = None) -> None:
        """Bold black title with a 0.5 pt rule under it."""
        self.text(self.left, self.y - 13, title, self.bold, 13, WS_INK, char_space=-13 * 0.01)
        if meta:
            self.text(self.right, self.y - 12, meta, self.regular, 8, WS_LABEL, align="right")
        self.rule(self.left, self.right, self.y - 19, WS_INK, 0.5)
        self.y -= 25

    # ----------------------------------------------------------------- requirements
    def text_flow(self, x: float, line: str, font: str, size: float, color: Color, leading: float,
                  gap_before: float = 0.0, before: Callable[[float, float], None] | None = None,
                  after: Callable[[float, float], None] | None = None) -> _Flow:
        def draw(top: float) -> None:
            mid = top - leading / 2
            baseline = mid - size * 0.36
            if before is not None:
                before(mid, baseline)
            self.text(x, baseline, line, font, size, color)
            if after is not None:
                after(mid, baseline)
        return _Flow(leading, draw, keep_with_next=True, gap_before=gap_before)

    def answer_flows(self, x: float, count: int, keep_last: bool = False) -> list[_Flow]:
        flows = []
        for k in range(count):
            def draw(top: float, x: float = x) -> None:
                self.rule(x, self.right, top - ANSWER_GAP, ANSWER_INK, 0.4)
            keep = (k == 0 and count > 1) or (keep_last and k == count - 1)
            flows.append(_Flow(ANSWER_GAP, draw, keep_with_next=keep))
        return flows

    def evidence_flow(self, x: float) -> _Flow:
        """«Evidencia: ☐ foto ☐ demostración ☐ informe · Verificado por: ____ Fecha: ____»
        on one row, or two when the column is too narrow for useful rules."""
        labels, size = self.labels, 8.0
        kinds = labels["evidence_kinds"]
        evidence_w = _width(labels["evidence"], self.bold, size) + 8 + sum(
            EVIDENCE_BOX + 4 + _width(kind, self.regular, size) + 10 for kind in kinds)
        verify_w = _width(labels["verified_by"], self.bold, size) + 6 + 12 + _width(labels["date"], self.bold, size) + 6
        avail = self.right - x
        one_row = avail - evidence_w - 14 - verify_w >= 60 + 45
        height = 20.0 if one_row else 40.0

        def draw(top: float) -> None:
            mid = top - 10
            baseline = mid - size * 0.36
            cx = x + self.text(x, baseline, labels["evidence"], self.bold, size, WS_LABEL) + 8
            for kind in kinds:
                self.checkbox(cx, mid - EVIDENCE_BOX / 2, EVIDENCE_BOX, width=0.5)
                cx += EVIDENCE_BOX + 4
                cx += self.text(cx, baseline, kind, self.regular, size, WS_LABEL) + 10
            if one_row:
                cx += self.text(cx - 2, baseline, "·", self.bold, size, WS_HINT) + 12
            else:
                cx, baseline = x, baseline - 20
            rules = self.right - cx - verify_w
            cx += self.text(cx, baseline, labels["verified_by"], self.bold, size, WS_LABEL) + 6
            self.rule(cx, cx + rules * 0.6, baseline - 1.5)
            cx += rules * 0.6 + 12
            cx += self.text(cx, baseline, labels["date"], self.bold, size, WS_LABEL) + 6
            self.rule(cx, self.right, baseline - 1.5)
        return _Flow(height, draw, gap_before=4)

    def requirement_flows(self, number: int, requirement: SheetRequirement) -> list[_Flow]:
        labels, left, right = self.labels, self.left, self.right
        text_x = self.text_x
        practical = not requirement.is_theoretical
        head, items = parse_requirement(requirement.description)
        size, leading = 10.5, 14.5
        tag = labels["practical"]
        tag_w = self.pill_width(tag, size=7, height=15, dot=True) if practical else 0.0
        flows: list[_Flow] = []

        def lead(mid: float, baseline: float) -> None:
            self.checkbox(left, mid - CHECKBOX / 2, CHECKBOX)
            self.text(left + CHECKBOX + 7, baseline, f"{number}.", self.bold, size, WS_INK)
            if practical:                             # the one coloured marker inside the list
                self.pill(right - tag_w, mid - 7.5, tag, fill=tint(SUCCESS, 0.16), ink=SUCCESS_INK, size=7,
                          height=15, dot=SUCCESS)

        width = right - text_x
        head_lines = wrap(head, self.regular, size, width, width - tag_w - 8 if practical else None)
        for j, line in enumerate(head_lines):
            flows.append(self.text_flow(text_x, line, self.regular, size, WS_INK, leading,
                                        gap_before=14 if j == 0 else 0, before=lead if j == 0 else None))

        answered = False
        for item in items:
            indent = text_x + max(item.level - 1, 0) * 16
            if item.marker is None:                   # a continuation paragraph
                x = indent if item.level else text_x
                for j, line in enumerate(wrap(item.text, self.regular, 10, right - x)):
                    flows.append(self.text_flow(x, line, self.regular, 10, WS_INK, 14,
                                                gap_before=3 if j == 0 else 0))
                continue
            bullet = not item.marker[0].isalnum() and item.marker[0] != "("
            marker_x = indent + SUB_CHECKBOX + 6
            body_x = marker_x if bullet else marker_x + max(15.0, _width(item.marker, self.bold, 9.5) + 5)
            kind = sub_item_answer(item.text, head)
            lines = wrap(item.text, self.regular, 10, right - body_x)
            if kind == "inline" and right - (body_x + _width(lines[-1], self.regular, 10) + 8) < 90:
                kind = "below"
            for j, line in enumerate(lines):
                def before(mid: float, baseline: float, box_x: float = indent, marker: str = item.marker,
                           bullet: bool = bullet, marker_x: float = marker_x) -> None:
                    self.checkbox(box_x, mid - SUB_CHECKBOX / 2, SUB_CHECKBOX, width=0.55)
                    if not bullet:
                        self.text(marker_x, baseline, marker, self.bold, 9.5, WS_INK)

                def after(mid: float, baseline: float, line: str = line, body_x: float = body_x) -> None:
                    self.rule(body_x + _width(line, self.regular, 10) + 8, right, baseline - 1.5, ANSWER_INK, 0.4)

                last = j == len(lines) - 1
                flows.append(self.text_flow(body_x, line, self.regular, 10, WS_INK, 15 if kind == "inline" else 14,
                                            gap_before=4 if j == 0 else 0, before=before if j == 0 else None,
                                            after=after if kind == "inline" and last else None))
            if kind == "lines":
                flows.extend(self.answer_flows(body_x, 2))
                answered = True
            elif kind == "below":
                flows.extend(self.answer_flows(body_x, 1))
                answered = True
            elif kind == "inline":
                answered = True

        instructions = clean_text(requirement.instructions).strip()
        if instructions:
            first = True
            for block in [b for b in instructions.split("\n") if b.strip()]:
                for line in wrap(block.strip(), self.regular, 8.5, right - text_x):
                    flows.append(self.text_flow(text_x, line, self.regular, 8.5, WS_LABEL, 11.8,
                                                gap_before=5 if first else 0))
                    first = False

        flows.extend(self.answer_flows(text_x, answer_line_count(requirement, answered), keep_last=practical))
        if practical:
            flows.append(self.evidence_flow(text_x))
        return flows

    def ws_requirements(self) -> None:
        data, labels = self.data, self.labels
        reqs = data.requirements
        practical = sum(1 for r in reqs if not r.is_theoretical)
        meta = labels["count_one"] if len(reqs) == 1 else labels["count"].format(n=len(reqs))
        if practical:
            meta += " · " + (labels["count_practical_one"] if practical == 1
                             else labels["count_practical"].format(n=practical))
        self.ensure(24 + 120)
        self.ws_section_title(labels["requirements"], meta)
        req_language = (data.requirements_locale or "").lower().split("-")[0]
        if req_language and req_language != data.language:
            language = labels["languages"].get(req_language, data.requirements_locale)
            self.paragraph(labels["foreign"].format(language=language), size=8.5, color=WS_LABEL)
        flows: list[_Flow] = []
        for number, requirement in enumerate(reqs, 1):
            flows.extend(self.requirement_flows(number, requirement))
        if flows:
            flows[0].gap_before = 4
        self.flow(flows)

    def ws_pending(self) -> None:
        """«Requisitos en preparación»: a thin grey outline, no fill."""
        labels, url = self.labels, self.data.page_url
        pad, width = 18.0, self.content_w - 36
        body = wrap(labels["pending_body"], self.regular, 9.5, width)
        h = 2 * pad + 17 + 4 + len(body) * 13.5 + 15
        top = self.y - 6
        self.c.setStrokeColor(WS_RULE)
        self.c.setLineWidth(0.7)
        self.c.roundRect(self.left, top - h, self.content_w, h, 8, stroke=1, fill=0)
        self.c.linkURL(url, (self.left, top - h, self.right, top), relative=0)
        cursor = top - pad
        self.text(self.left + pad, cursor - 12, labels["pending_title"], self.bold, 12, WS_INK)
        cursor -= 17 + 4
        for line in body:
            self.text(self.left + pad, cursor - 10, line, self.regular, 9.5, WS_LABEL)
            cursor -= 13.5
        self.text(self.left + pad, cursor - 11, url.removeprefix("https://"), self.bold, 9.5, WS_INK)
        self.y = top - h - 4

    # ----------------------------------------------------------------- closing block
    def ws_closing(self, approval: bool) -> None:
        """«Aprobación» (signatures + dates) and the «Notas» box, kept together on the last page
        above the colophon; the notes box shrinks a little before the block changes page."""
        labels = self.labels
        approval_h = 24 + 66.0 if approval else 0.0
        notes_top_gap = 18.0
        lead_gap = 26.0
        need = lead_gap + approval_h + notes_top_gap + 14 + NOTES_H
        room = self.y - WS_COLOPHON_TOP
        notes_h = NOTES_H
        if need > room:
            if need - (NOTES_H - NOTES_MIN_H) <= room:
                notes_h = NOTES_H - (need - room)
            else:
                self.new_page()
        if not self.at_top():
            self.y -= lead_gap

        if approval:
            self.ws_section_title(labels["approval"])
            gap = 28.0
            col_w = (self.content_w - gap) / 2
            sign_y = self.y - 40
            for k, who in enumerate((labels["sign_instructor"], labels["sign_director"])):
                cx = self.left + k * (col_w + gap)
                sign_end = cx + col_w * 0.62
                self.rule(cx, sign_end, sign_y)
                w = self.text(cx, sign_y - 12, who, self.bold, 8.5, WS_INK)
                self.text(cx + w, sign_y - 12, " · " + labels["signature"], self.regular, 8, WS_HINT)
                self.rule(sign_end + 12, cx + col_w, sign_y)
                self.text(sign_end + 12, sign_y - 12, labels["date"].rstrip(":"), self.regular, 8, WS_HINT)
            self.y -= 66
            self.y -= notes_top_gap

        self.small_caps(self.left, self.y - 9, labels["notes"], 8, WS_LABEL)
        self.y -= 14
        box_top, box_bottom = self.y, self.y - notes_h
        self.c.setStrokeColor(NOTES_BORDER)
        self.c.setLineWidth(0.7)
        self.c.roundRect(self.left, box_bottom, self.content_w, notes_h, 10, stroke=1, fill=0)
        line_y = box_top - ANSWER_GAP
        while line_y > box_bottom + ANSWER_GAP * 0.6:
            self.rule(self.left + 12, self.right - 12, line_y, NOTES_RULE, 0.4)
            line_y -= ANSWER_GAP
        self.y = box_bottom

    def ws_colophon(self) -> None:
        """Last page, above the footer: QR, «Generado en Adventist.Club · …/honors/<slug>», version."""
        data, labels = self.data, self.labels
        qr, y0 = 46.0, WS_FOOTER_RULE + 14
        draw_qr(self.c, data.page_url, self.left, y0, qr, WS_INK)
        tx = self.left + qr + 12
        base = y0 + 28
        w = self.text(tx, base, labels["generated"], self.regular, 8, WS_LABEL)
        w += self.text(tx + w, base, "Adventist.Club", self.bold, 8, WS_LABEL)
        url_text = ellipsize(" · " + data.page_url.removeprefix("https://www."), self.regular, 8,
                             self.right - tx - w)
        w += self.text(tx + w, base, url_text, self.regular, 8, WS_LABEL)
        self.c.linkURL(data.page_url, (tx, base - 4, tx + w, base + 10), relative=0)
        self.text(tx, base - 13, labels["version_short"].format(
            n=data.version, date=format_date(data.updated_at, data.language)), self.regular, 7.5, WS_HINT)

    def _footer(self, c: canvas.Canvas, page: int, total: int) -> None:
        labels = self.labels
        self.rule(self.left, self.right, WS_FOOTER_RULE, WS_RULE, 0.5)
        page_label = labels["page"].format(x=page, y=total)
        page_w = self.text(self.right, 42, page_label, self.bold, 8, WS_LABEL, align="right")
        left = f"{self.data.name} · {labels['ws_title']}"
        self.text(self.left, 42, ellipsize(left, self.regular, 7.5, self.content_w - page_w - 20),
                  self.regular, 7.5, WS_HINT)

    # ----------------------------------------------------------------- document
    def render(self) -> bytes:
        c, data = self.c, self.data
        c.setTitle(f"{self.labels['ws_title']} · {data.name}")
        c.setAuthor("Adventist.Club")
        c.setSubject(data.page_url)
        c.setCreator("Adventist.Club API")
        c.setKeywords([data.slug] + ([data.category_name] if data.category_name else []))
        self.ws_header()
        self.ws_fields()
        if data.requirements:
            self.ws_requirements()
            self.ws_closing(approval=True)
        else:
            self.ws_section_title(self.labels["requirements"])
            self.ws_pending()
            self.ws_closing(approval=False)
        self.ws_colophon()
        c.showPage()
        c.save()
        return self.out.getvalue()


def render_sheet_pdf(data: SheetData, *, paper: Paper = "a4", mode: Mode = "hoja",
                     patch_image: bytes | None = None, fetch_image: bool = True) -> bytes:
    """Draw the worksheet (`mode="hoja"`, default) or the compact «ficha» (`mode="ficha"`).
    `patch_image` (any raster) wins over fetching `data.image_url`."""
    patch = None
    if patch_image:
        try:
            patch = normalize_patch(patch_image)
        except Exception as exc:
            logger.warning("honor sheet: supplied patch unreadable: %s", exc)
    elif fetch_image:
        patch = load_patch(data.image_url)
    renderer = _SheetRenderer if mode == "ficha" else _WorksheetRenderer
    return renderer(data, paper, patch).render()
