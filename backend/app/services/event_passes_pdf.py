"""Printable club passes of an event (SPEC_RED_LOCAL §1): `GET /events/{id}/passes.pdf`.

One pass per registered club, US Letter, two per sheet with a dashed cut line (or one large pass
per sheet, `per_page=1`). Each pass carries the event's brand (logo and colours when the event has
them), the event's name and dates, the club's name, a big QR with the pass URL (the same
`pass_url` the screens show: printing never rotates the pass), the code printed in groups so it can
be typed when a camera fails, and one line of instructions. Nothing about any member (no minors'
data): only the club.

Reuses the honor-sheet PDF stack (`app/services/honor_sheet.py`): ReportLab, the vector QR
(a vector QR like its `draw_qr`, here with error correction Q), the bundled Noto Sans (Helvetica
when the fonts are not deployed) and `load_patch` for the logo — HTTPS from our own media bucket
only, 3 s timeout, disk cache; any failure just prints the pass without a logo.

Pure rendering: who may print what, and which token each club has, is decided by the router
(`app/routers/events.py`) through `event_scores.current_pass`.
"""
from __future__ import annotations

import logging
import threading

import qrcode
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.services.honor_sheet import load_patch, sheet_fonts

logger = logging.getLogger(__name__)

DEFAULT_BRAND = "#305CE8"   # the app's default event colour (frontend lib/events-brand.ts)
INK = HexColor("#19253B")
TEXT = HexColor("#0B0B0D")
MUTED = HexColor("#5B6270")
RULE = HexColor("#C9CDD4")
PT_PER_CM = 72 / 2.54

INSTRUCTIONS = "Muestra este pase en la mesa de registro y en cada actividad."
TYPE_HINT = "Si la cámara no lee el QR, teclea este código:"
EYEBROW = "PASE DEL CLUB"
STABLE_NOTE = "Este pase sirve hasta que alguien lo regenere; si se regenera, este impreso deja de servir."
CUT_HERE = "Cortar por la línea"

MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre")


@dataclass(frozen=True)
class EventBrand:
    name: str
    starts_on: date
    ends_on: date
    venue: str | None = None
    city: str | None = None
    color: str | None = None
    accent: str | None = None
    logo_url: str | None = None


@dataclass(frozen=True)
class ClubPass:
    club_name: str
    club_city: str | None
    token: str
    url: str


# ---------------------------------------------------------------------------------------
# Text helpers (pure; tested)
# ---------------------------------------------------------------------------------------
def date_range(start: date, end: date) -> str:
    """«20 de noviembre de 2026», «20–22 de noviembre de 2026», «30 de noviembre – 2 de
    diciembre de 2026», «30 de diciembre de 2026 – 2 de enero de 2027»."""
    def full(day: date, year: bool = True) -> str:
        return f"{day.day} de {MONTHS[day.month - 1]}" + (f" de {day.year}" if year else "")

    if end <= start:
        return full(start)
    if start.year != end.year:
        return f"{full(start)} – {full(end)}"
    if start.month != end.month:
        return f"{full(start, year=False)} – {full(end)}"
    return f"{start.day}–{full(end)}"


def code_lines(token: str, group: int = 4, per_line: int = 4) -> list[str]:
    """The token as typed by a person: the `evp_` prefix, then groups of four characters, four
    groups per line. Readers drop the spaces (`event_pass.token_from`)."""
    prefix, rest = ("evp_", token[4:]) if token.startswith("evp_") else ("", token)
    groups = [rest[i:i + group] for i in range(0, len(rest), group)]
    lines = [" ".join(groups[i:i + per_line]) for i in range(0, len(groups), per_line)] or [""]
    pad = " " * (len(prefix) + 1) if prefix else ""
    return [(f"{prefix} " if prefix and i == 0 else pad) + line for i, line in enumerate(lines)]


def _hex(value: str | None, fallback: str) -> Color:
    try:
        return HexColor(value) if value else HexColor(fallback)
    except (ValueError, TypeError):
        return HexColor(fallback)


def _luminance(color: Color) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.red) + 0.7152 * channel(color.green) + 0.0722 * channel(color.blue)


def contrast(a: Color, b: Color) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def on_color(fill: Color) -> Color:
    """White or ink, whichever reads better on `fill`."""
    return white if contrast(white, fill) >= contrast(INK, fill) else INK


def readable_on_white(color: Color, ratio: float = 4.5) -> Color:
    """`color` darkened towards the ink until it reaches `ratio` on paper (WCAG AA)."""
    for step in range(0, 21):
        k = step / 20
        candidate = Color(color.red + (INK.red - color.red) * k, color.green + (INK.green - color.green) * k,
                          color.blue + (INK.blue - color.blue) * k)
        if contrast(candidate, white) >= ratio:
            return candidate
    return INK


# ---------------------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------------------
_font_lock = threading.Lock()


# Noto Sans Mono cut down to what a pass code uses (A–Z a–z 0–9 _ - and space, 6 KB; SIL OFL, see
# OFL.txt next to it). It ships inside `app/` because the Docker image only copies `app/`: the
# repository's `fonts/` (Noto Sans for the words) is not deployed, and Courier — the fallback — does
# not tell 0 from O, which matters for a code people type.
PASS_CODE_FONT = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansMono-PassCode.ttf"


@lru_cache(maxsize=1)
def mono_font() -> str:
    """The font of the printed code: the bundled Noto Sans Mono subset (clear 0/O and 1/l/I),
    else the built-in Courier-Bold."""
    if PASS_CODE_FONT.exists():
        with _font_lock:
            try:
                pdfmetrics.registerFont(TTFont("CQPassCode", str(PASS_CODE_FONT)))
                return "CQPassCode"
            except Exception:  # a broken font file must not take the passes down
                logger.exception("could not register the pass-code font; using Courier")
    return "Courier-Bold"


def _fit(text: str, font: str, size: float, width: float, *, min_size: float, max_lines: int) -> tuple[list[str], float]:
    """`text` wrapped to `width`, shrinking from `size` to `min_size` until it fits in
    `max_lines`; at the minimum size the last line is cut with «…»."""
    while True:
        lines = simpleSplit(text, font, size, width) or [""]
        if len(lines) <= max_lines or size <= min_size:
            break
        size = max(min_size, size - 1)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and pdfmetrics.stringWidth(f"{last}…", font, size) > width:
            last = last[:-1]
        lines[-1] = f"{last.rstrip()}…"
    return lines, size


# ---------------------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Box:
    x: float
    y: float      # bottom
    w: float
    h: float


@lru_cache(maxsize=1024)
def qr_matrix(payload: str) -> tuple[tuple[bool, ...], ...]:
    """Error correction Q (25 %): a printed pass gets folded, smudged and rained on."""
    code = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_Q)
    code.add_data(payload)
    code.make(fit=True)
    return tuple(tuple(row) for row in code.get_matrix())


def draw_qr(c: canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    """A vector QR, black on the paper (the white around it is its quiet zone). Runs of dark
    modules become one rectangle each, overlapping their neighbours by a hair so no viewer or
    printer leaves hairline seams between them."""
    matrix = qr_matrix(url)
    cell = size / len(matrix)
    bleed = cell * 0.04
    c.setFillColor(HexColor("#000000"))
    for r, row in enumerate(matrix):
        col = 0
        while col < len(row):
            if row[col]:
                start = col
                while col < len(row) and row[col]:
                    col += 1
                c.rect(x + start * cell - bleed / 2, y + size - (r + 1) * cell - bleed / 2,
                       (col - start) * cell + bleed, cell + bleed, stroke=0, fill=1)
            else:
                col += 1
    c.linkURL(url, (x, y, x + size, y + size), relative=0)


def _logo_reader(png: bytes | None) -> ImageReader | None:
    if not png:
        return None
    try:
        return ImageReader(BytesIO(png))
    except Exception:  # an unreadable image only costs the logo
        logger.warning("event passes: logo unreadable; printing without it")
        return None


def _header(c: canvas.Canvas, box: _Box, brand: EventBrand, logo: ImageReader | None, *, big: bool) -> float:
    """The brand band across the top of the pass. -> its height."""
    regular, bold = sheet_fonts()
    fill = _hex(brand.color, DEFAULT_BRAND)
    ink = on_color(fill)
    height = 92.0 if big else 70.0
    top = box.y + box.h
    c.setFillColor(fill)
    c.roundRect(box.x, top - height, box.w, height, 10, stroke=0, fill=1)
    c.rect(box.x, top - height, box.w, 12, stroke=0, fill=1)   # square bottom corners
    if brand.accent:
        c.setFillColor(_hex(brand.accent, DEFAULT_BRAND))
        c.rect(box.x, top - height, box.w, 4, stroke=0, fill=1)
    pad = 16.0 if big else 12.0
    x = box.x + pad
    if logo is not None:
        tile = height - 2 * pad + 4
        c.setFillColor(white)
        c.roundRect(x, top - height / 2 - tile / 2, tile, tile, 6, stroke=0, fill=1)
        inset = 3.0
        c.drawImage(logo, x + inset, top - height / 2 - tile / 2 + inset, tile - 2 * inset, tile - 2 * inset,
                    preserveAspectRatio=True, anchor="c", mask="auto")
        x += tile + pad
    width = box.x + box.w - pad - x
    name_size = 22.0 if big else 16.0
    lines, name_size = _fit(brand.name, bold, name_size, width, min_size=11 if big else 10, max_lines=2)
    meta = date_range(brand.starts_on, brand.ends_on)
    place = " · ".join(part for part in (brand.venue, brand.city) if part)
    meta_size = 12.0 if big else 9.5
    meta_lines = [meta] + ([place] if place else [])
    meta_lines = [(simpleSplit(line, regular, meta_size, width) or [line])[0] for line in meta_lines]
    name_lead, meta_lead = name_size * 1.1, meta_size * 1.3
    # cap height ≈ 0.72 em: the block (first cap to last baseline) is centred in the band
    block = name_size * 0.72 + (len(lines) - 1) * name_lead + 4 + len(meta_lines) * meta_lead
    y = top - (height - block) / 2 - name_size * 0.72
    c.setFillColor(ink)
    c.setFont(bold, name_size)
    for line in lines:
        c.drawString(x, y, line)
        y -= name_lead
    y += name_lead - 4 - meta_lead
    c.setFont(regular, meta_size)
    for line in meta_lines:
        c.drawString(x, y, line)
        y -= meta_lead
    return height


def _draw_pass(c: canvas.Canvas, box: _Box, brand: EventBrand, item: ClubPass, logo: ImageReader | None,
               *, big: bool) -> None:
    regular, bold = sheet_fonts()
    mono = mono_font()
    accent = readable_on_white(_hex(brand.accent or brand.color, DEFAULT_BRAND))
    # outline of the pass (where the cut lands is the page's business; this frames the card)
    c.setStrokeColor(RULE)
    c.setLineWidth(0.75)
    c.roundRect(box.x, box.y, box.w, box.h, 10, stroke=1, fill=0)
    head = _header(c, box, brand, logo, big=big)
    pad = 22.0 if big else 16.0
    inner_top = box.y + box.h - head - pad
    inner_bottom = box.y + pad

    if big:
        # One pass per sheet: everything stacked and centred, the QR as large as the page allows.
        y = inner_top
        c.setFillColor(accent)
        c.setFont(bold, 11)
        c.drawCentredString(box.x + box.w / 2, y - 11, EYEBROW)
        y -= 11 + 10
        lines, size = _fit(item.club_name, bold, 34, box.w - 2 * pad, min_size=18, max_lines=2)
        c.setFillColor(TEXT)
        for line in lines:
            y -= size
            c.setFont(bold, size)
            c.drawCentredString(box.x + box.w / 2, y, line)
            y -= size * 0.18
        if item.club_city:
            c.setFillColor(MUTED)
            c.setFont(regular, 13)
            y -= 16
            c.drawCentredString(box.x + box.w / 2, y, item.club_city)
        # The QR keeps its quiet zone (4 modules) clear of any text above and below it.
        modules = len(qr_matrix(item.url))
        footer = 15 + 26 + 24 + 24 + 22   # instructions, hint, two code lines, note
        qr = min(box.w - 2 * pad - 40, (y - inner_bottom - footer) / (1 + 8 / modules))
        quiet = 4 * qr / modules
        qx = box.x + (box.w - qr) / 2
        draw_qr(c, item.url, qx, y - quiet - qr, qr)
        y -= quiet + qr + quiet + 15
        c.setFillColor(TEXT)
        c.setFont(bold, 15)
        c.drawCentredString(box.x + box.w / 2, y, INSTRUCTIONS)
        y -= 26
        c.setFillColor(MUTED)
        c.setFont(regular, 11)
        c.drawCentredString(box.x + box.w / 2, y, TYPE_HINT)
        y -= 24
        c.setFillColor(TEXT)
        for line in code_lines(item.token):
            c.setFont(mono, 19)
            c.drawCentredString(box.x + box.w / 2, y, line)
            y -= 24
        c.setFillColor(MUTED)
        c.setFont(regular, 8.5)
        c.drawCentredString(box.x + box.w / 2, inner_bottom, STABLE_NOTE)
        return

    # Two per sheet: QR on the right, words on the left.
    # The QR with its quiet zone (4 modules) clear of the band, the words and the card's edge.
    modules = len(qr_matrix(item.url))
    area = box.h - head
    qr = min(area / (1 + 8 / modules), box.w * 0.44)
    quiet = 4 * qr / modules
    qx = box.x + box.w - quiet - qr
    qy = box.y + (area - qr) / 2
    draw_qr(c, item.url, qx, qy, qr)
    left = box.x + pad
    width = qx - quiet - left
    y = inner_top
    c.setFillColor(accent)
    c.setFont(bold, 9)
    c.drawString(left, y - 9, EYEBROW)
    y -= 9 + 8
    lines, size = _fit(item.club_name, bold, 26, width, min_size=14, max_lines=3)
    c.setFillColor(TEXT)
    for line in lines:
        y -= size
        c.setFont(bold, size)
        c.drawString(left, y, line)
        y -= size * 0.16
    if item.club_city:
        c.setFillColor(MUTED)
        c.setFont(regular, 10.5)
        y -= 14
        c.drawString(left, y, simpleSplit(item.club_city, regular, 10.5, width)[0])
    # Laid out bottom-up (instructions, hint, code, note sit on the bottom edge), drawn top-down
    # so the text layer reads in order when copied, searched or read aloud.
    ops: list[tuple[float, str, float, Color, str]] = []
    y = inner_bottom
    for line in reversed(simpleSplit(STABLE_NOTE, regular, 7.5, width)):
        ops.append((y, regular, 7.5, MUTED, line))
        y += 9.5
    y += 6
    codes = code_lines(item.token)
    code_size = 13.0
    while code_size > 8 and max(pdfmetrics.stringWidth(line, mono, code_size) for line in codes) > width:
        code_size -= 0.5
    for line in reversed(codes):
        ops.append((y, mono, code_size, TEXT, line))
        y += code_size * 1.3
    ops.append((y + 1, regular, 8.5, MUTED, simpleSplit(TYPE_HINT, regular, 8.5, width)[0]))
    y += 8.5 + 12
    for line in reversed(simpleSplit(INSTRUCTIONS, bold, 11, width)):
        ops.append((y, bold, 11, TEXT, line))
        y += 13.5
    for line_y, font, size, color, line in reversed(ops):
        c.setFillColor(color)
        c.setFont(font, size)
        c.drawString(left, line_y, line)


def _cut_line(c: canvas.Canvas, y: float, page_w: float) -> None:
    regular, _ = sheet_fonts()
    c.saveState()
    c.setStrokeColor(MUTED)
    c.setLineWidth(0.6)
    c.setDash(5, 4)
    c.line(14, y, page_w - 14, y)
    c.restoreState()
    c.setFillColor(MUTED)
    c.setFont(regular, 7)
    c.drawRightString(page_w - 18, y + 3, CUT_HERE)


def render_passes_pdf(brand: EventBrand, items: list[ClubPass], *, per_page: int = 2,
                      logo_png: bytes | None = None, fetch_logo: bool = True) -> bytes:
    """The PDF: one page per `per_page` passes, in the order given. `logo_png` (any raster)
    wins over fetching `brand.logo_url`; a missing or failing logo prints the pass without one."""
    if per_page not in (1, 2):
        raise ValueError("per_page must be 1 or 2")
    if logo_png is None and fetch_logo and brand.logo_url:
        logo_png = load_patch(brand.logo_url)
    logo = _logo_reader(logo_png)
    page_w, page_h = LETTER
    margin = 36.0   # 1/2 in: inside the printable area of any office printer
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=LETTER, pageCompression=1)
    c.setTitle(f"Pases · {brand.name}")
    c.setAuthor("adventist.club")
    c.setSubject("Pases de club para imprimir")
    for start in range(0, len(items), per_page):
        if start:
            c.showPage()
        if per_page == 1:
            _draw_pass(c, _Box(margin, margin, page_w - 2 * margin, page_h - 2 * margin), brand, items[start],
                       logo, big=True)
            continue
        half = page_h / 2
        gutter = 22.0   # each pass stays this far from the cut line
        for slot, item in enumerate(items[start:start + 2]):
            bottom = half + gutter if slot == 0 else margin
            top = page_h - margin if slot == 0 else half - gutter
            _draw_pass(c, _Box(margin, bottom, page_w - 2 * margin, top - bottom), brand, item, logo, big=False)
        _cut_line(c, half, page_w)
    c.save()
    return out.getvalue()
