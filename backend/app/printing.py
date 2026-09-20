"""Print imposition: how many certificates fit on a sheet, where, and the PDF.

Everything is computed in inches at real physical size. Certificates are never
scaled unless the caller explicitly allows scaling down.
"""
from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass, field
from io import BytesIO
from typing import Literal

from pydantic import BaseModel, Field
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

POINTS_PER_INCH = 72.0
UNIT_TO_INCH = {"in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54}
CROP_MARK_LENGTH_IN = 0.25
CROP_MARK_MIN_OFFSET_IN = 1 / 16
EPS = 1e-9

Orientation = Literal["auto", "portrait", "landscape"]


class LayoutRequest(BaseModel):
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    item_width: float = Field(gt=0)
    item_height: float = Field(gt=0)
    unit: Literal["in", "mm", "cm"] = "in"
    orientation: Orientation = "auto"
    margin_top: float = Field(default=0, ge=0)
    margin_right: float = Field(default=0, ge=0)
    margin_bottom: float = Field(default=0, ge=0)
    margin_left: float = Field(default=0, ge=0)
    gap_x: float = Field(default=0, ge=0)
    gap_y: float = Field(default=0, ge=0)
    bleed: float = Field(default=0, ge=0)
    allow_rotation: bool = True
    allow_scale_down: bool = False
    total_items: int = Field(default=1, ge=0)


@dataclass(frozen=True)
class Slot:
    """One certificate position on the sheet, inches from the top-left corner."""
    page: int
    index: int
    x: float          # cell origin (includes bleed)
    y: float
    trim_x: float     # artwork origin (the trim box)
    trim_y: float


@dataclass
class Layout:
    page_width: float
    page_height: float
    cols: int
    rows: int
    per_page: int
    rotated: bool
    scale: float
    cell_width: float
    cell_height: float
    trim_width: float
    trim_height: float
    bleed: float
    margins: tuple[float, float, float, float]   # top, right, bottom, left
    gap_x: float
    gap_y: float
    total_items: int
    pages: int
    slots: list[Slot] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "page_width_in": self.page_width, "page_height_in": self.page_height,
            "cols": self.cols, "rows": self.rows, "per_page": self.per_page,
            "rotated": self.rotated, "scale": self.scale,
            "item_width_in": self.trim_width, "item_height_in": self.trim_height,
            "cell_width_in": self.cell_width, "cell_height_in": self.cell_height,
            "bleed_in": self.bleed, "total_items": self.total_items, "pages": self.pages,
            "slots": [{"page": s.page, "index": s.index, "x_in": s.x, "y_in": s.y,
                       "trim_x_in": s.trim_x, "trim_y_in": s.trim_y} for s in self.slots],
        }


def _grid(usable_w: float, usable_h: float, cell_w: float, cell_h: float, gap_x: float, gap_y: float):
    cols = int(math.floor((usable_w + gap_x + EPS) / (cell_w + gap_x)))
    rows = int(math.floor((usable_h + gap_y + EPS) / (cell_h + gap_y)))
    return max(cols, 0), max(rows, 0)


def compute_layout(req: LayoutRequest) -> Layout:
    k = UNIT_TO_INCH[req.unit]
    page_w, page_h = req.page_width * k, req.page_height * k
    item_w, item_h = req.item_width * k, req.item_height * k
    top, right, bottom, left = (req.margin_top * k, req.margin_right * k, req.margin_bottom * k, req.margin_left * k)
    gap_x, gap_y, bleed = req.gap_x * k, req.gap_y * k, req.bleed * k

    page_options = {
        "portrait": [(min(page_w, page_h), max(page_w, page_h))],
        "landscape": [(max(page_w, page_h), min(page_w, page_h))],
        "auto": [(page_w, page_h), (page_h, page_w)],
    }[req.orientation]

    best: tuple | None = None
    for pw, ph in page_options:
        usable_w, usable_h = pw - left - right, ph - top - bottom
        if usable_w <= 0 or usable_h <= 0:
            continue
        for rotated in ([False, True] if req.allow_rotation else [False]):
            tw, th = (item_h, item_w) if rotated else (item_w, item_h)
            cw, ch = tw + 2 * bleed, th + 2 * bleed
            scale = 1.0
            cols, rows = _grid(usable_w, usable_h, cw, ch, gap_x, gap_y)
            if cols * rows == 0 and req.allow_scale_down:
                # shrink just enough for a single copy to fit; never enlarge
                scale = min(usable_w / cw, usable_h / ch)
                cw, ch = cw * scale, ch * scale
                cols, rows = _grid(usable_w, usable_h, cw, ch, gap_x, gap_y)
            count = cols * rows
            # more per page wins; then unrotated, unscaled, and the requested orientation
            key = (count, not rotated, scale, (pw, ph) == page_options[0])
            if best is None or key > best[0]:
                best = (key, pw, ph, cols, rows, rotated, scale, cw, ch, tw * scale, th * scale)

    if best is None:
        best = ((0,), page_options[0][0], page_options[0][1], 0, 0, False, 1.0, item_w, item_h, item_w, item_h)
    _, pw, ph, cols, rows, rotated, scale, cw, ch, tw, th = best
    per_page = cols * rows
    pages = math.ceil(req.total_items / per_page) if per_page and req.total_items else 0
    # Margins are minimums: the grid is centred in the printable area so the
    # leftover paper is split evenly (easier trimming, symmetric marks).
    grid_w = cols * cw + max(cols - 1, 0) * gap_x
    grid_h = rows * ch + max(rows - 1, 0) * gap_y
    origin_x = left + max(0.0, (pw - left - right - grid_w) / 2) if per_page else left
    origin_y = top + max(0.0, (ph - top - bottom - grid_h) / 2) if per_page else top
    layout = Layout(pw, ph, cols, rows, per_page, rotated, scale, cw, ch, tw, th, bleed * scale,
                    (origin_y, right, bottom, origin_x), gap_x, gap_y, req.total_items, pages)
    for index in range(req.total_items if per_page else 0):
        page, slot = divmod(index, per_page)
        row, col = divmod(slot, cols)
        x = origin_x + col * (cw + gap_x)
        y = origin_y + row * (ch + gap_y)
        layout.slots.append(Slot(page, index, x, y, x + layout.bleed, y + layout.bleed))
    return layout


def _grid_cells(layout: Layout) -> list[tuple[float, float, float, float]]:
    """Trim boxes of every cell of the grid, occupied or not."""
    top, _, _, left = layout.margins
    cells = []
    for row in range(layout.rows):
        for col in range(layout.cols):
            x = left + col * (layout.cell_width + layout.gap_x) + layout.bleed
            y = top + row * (layout.cell_height + layout.gap_y) + layout.bleed
            cells.append((x, y, x + layout.trim_width, y + layout.trim_height))
    return cells


def crop_mark_segments(layout: Layout, page: int = 0) -> list[tuple[float, float, float, float]]:
    """Trim marks for every certificate corner on `page`, as (x0, y0, x1, y1) in inches.

    A segment is kept only if it stays on the sheet and touches no cell of the
    grid (occupied or not), so with zero gaps only the outer perimeter gets marks.
    """
    offset = max(layout.bleed, CROP_MARK_MIN_OFFSET_IN)
    length = CROP_MARK_LENGTH_IN
    trims = _grid_cells(layout)
    occupied = [(s.trim_x, s.trim_y, s.trim_x + layout.trim_width, s.trim_y + layout.trim_height)
                for s in layout.slots if s.page == page]

    def crosses_artwork(x0, y0, x1, y1):
        # Strict overlap along the mark's own axis, inclusive across it: a mark
        # running along the edge of a neighbour's artwork counts as touching it.
        lo_x, hi_x, lo_y, hi_y = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        horizontal = abs(y0 - y1) < EPS
        for tx0, ty0, tx1, ty1 in trims:
            if horizontal:
                if lo_x < tx1 - EPS and hi_x > tx0 + EPS and ty0 - EPS <= lo_y <= ty1 + EPS:
                    return True
            elif lo_y < ty1 - EPS and hi_y > ty0 + EPS and tx0 - EPS <= lo_x <= tx1 + EPS:
                return True
        return False

    def on_page(x0, y0, x1, y1):
        return (min(x0, x1) >= -EPS and max(x0, x1) <= layout.page_width + EPS
                and min(y0, y1) >= -EPS and max(y0, y1) <= layout.page_height + EPS)

    segments = []
    for tx0, ty0, tx1, ty1 in occupied:
        for cx, cy, dx, dy in ((tx0, ty0, -1, -1), (tx1, ty0, 1, -1), (tx0, ty1, -1, 1), (tx1, ty1, 1, 1)):
            horizontal = (cx + dx * offset, cy, cx + dx * (offset + length), cy)
            vertical = (cx, cy + dy * offset, cx, cy + dy * (offset + length))
            for seg in (horizontal, vertical):
                if on_page(*seg) and not crosses_artwork(*seg):
                    segments.append(seg)
    return segments


def _decode_image(data: str) -> ImageReader:
    raw = data.split(",", 1)[1] if "," in data else data
    try:
        return ImageReader(BytesIO(base64.b64decode(raw, validate=True)))
    except (binascii.Error, ValueError, OSError) as exc:
        raise ValueError("Una de las imágenes no es válida.") from exc


def render_pdf(images: list[str], layout: Layout, crop_marks: bool) -> bytes:
    """Impose `images` (data URLs) following `layout`; one image per slot, in order."""
    if layout.per_page < 1:
        raise ValueError("El certificado no cabe físicamente en la hoja.")
    pt = POINTS_PER_INCH
    readers = [_decode_image(image) for image in images]
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=(layout.page_width * pt, layout.page_height * pt))
    slots = [s for s in layout.slots if s.index < len(readers)]
    pages = max((s.page for s in slots), default=-1) + 1
    for page in range(pages):
        if page:
            pdf.showPage()
        for slot in (s for s in slots if s.page == page):
            reader = readers[slot.index]
            x_pt = slot.trim_x * pt
            y_pt = (layout.page_height - slot.trim_y - layout.trim_height) * pt   # PDF origin is bottom-left
            w_pt, h_pt = layout.trim_width * pt, layout.trim_height * pt
            if layout.rotated:
                pdf.saveState()
                pdf.translate(x_pt + w_pt / 2, y_pt + h_pt / 2)
                pdf.rotate(90)
                pdf.drawImage(reader, -h_pt / 2, -w_pt / 2, width=h_pt, height=w_pt,
                              preserveAspectRatio=True, anchor="c", mask="auto")
                pdf.restoreState()
            else:
                pdf.drawImage(reader, x_pt, y_pt, width=w_pt, height=h_pt,
                              preserveAspectRatio=True, anchor="c", mask="auto")
        marks = crop_mark_segments(layout, page) if crop_marks else []
        if marks:
            pdf.setLineWidth(0.25)
            pdf.setStrokeColorRGB(0, 0, 0)
            for x0, y0, x1, y1 in marks:
                pdf.line(x0 * pt, (layout.page_height - y0) * pt, x1 * pt, (layout.page_height - y1) * pt)
    pdf.save()
    return out.getvalue()
