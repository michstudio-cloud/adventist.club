"""Certificate rendering from designer-made SVG templates.

A template lives in `templates/certificates/<slug>/`:
  template.svg            physical size in the viewBox (1 unit = 1 pt); dynamic fields are
                          <text id="..."> / <image id="...">; fixed translatable texts are t_*
  strings.<locale>.json   translations for the t_* texts (Spanish is the source language)
  background.png|svg      optional artwork referenced from template.svg (relative href)

Rendering never invents content: unknown fields keep the template's sample text, missing
images are removed, and a missing translation falls back to the Spanish in the SVG.
Contract: docs/I18N_Y_PLANTILLAS.md.
"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import qrcode
import resvg_py
from PIL import ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring as safe_fromstring

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates" / "certificates"
FONTS_DIR = Path(__file__).resolve().parents[3] / "fonts"
EMBLEMS_DIR = Path(__file__).resolve().parents[3] / "templates" / "assets" / "emblems"
IMAGE_FIELDS = ("emblem", "honor_patch", "qr", "issuer_logo", "background")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
POINTS_PER_INCH = 72.0


class TemplateError(ValueError):
    pass


def fonts_installed() -> bool:
    """resvg silently drops text whose font is missing, so rendering requires the bundled fonts."""
    return FONTS_DIR.exists() and any(FONTS_DIR.glob("*.ttf"))


@dataclass
class Template:
    slug: str
    directory: Path
    svg: str
    width_pt: float
    height_pt: float
    strings: dict[str, dict[str, str]] = field(default_factory=dict)   # locale -> key -> text

    @property
    def locales(self) -> list[str]:
        return sorted({"es", *self.strings})

    @property
    def fields(self) -> list[str]:
        root = safe_fromstring(self.svg)
        return sorted(el.get("id") for el in root.iter() if el.get("id") and
                      el.tag in (f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}image"))


def list_templates(base: Path = TEMPLATES_DIR) -> list[Template]:
    return [load_template(p.name, base) for p in sorted(base.iterdir())
            if p.is_dir() and (p / "template.svg").exists()]


@lru_cache(maxsize=32)
def _load(slug: str, base: str) -> Template:
    directory = Path(base) / slug
    svg_path = directory / "template.svg"
    if not SLUG_RE.match(slug) or not svg_path.exists():
        raise TemplateError(f"Plantilla '{slug}' no existe.")
    svg = svg_path.read_text(encoding="utf-8")
    if re.search(r"<script|javascript:|on[a-z]+\s*=", svg, re.I):
        raise TemplateError(f"Plantilla '{slug}' contiene código activo.")
    root = safe_fromstring(svg)
    view = (root.get("viewBox") or "").split()
    if len(view) != 4:
        raise TemplateError(f"Plantilla '{slug}' no declara viewBox.")
    strings = {}
    for path in directory.glob("strings.*.json"):
        locale = path.name[len("strings."):-len(".json")]
        strings[locale] = json.loads(path.read_text(encoding="utf-8"))
    return Template(slug, directory, svg, float(view[2]), float(view[3]), strings)


def load_template(slug: str, base: Path = TEMPLATES_DIR) -> Template:
    return _load(slug, str(base))


def resolve_strings(template: Template, locale: str) -> dict[str, str]:
    """Most specific locale first (pt-BR -> pt), Spanish (the SVG itself) as the final fallback."""
    parts = locale.split("-")
    for i in range(len(parts), 0, -1):
        candidate = "-".join(parts[:i])
        if candidate in template.strings:
            return template.strings[candidate]
    return {}


@lru_cache(maxsize=64)
def _font(family: str, weight: str, size_pt: float):
    """Font used only to MEASURE text for data-fit; resvg does the real drawing."""
    if FONTS_DIR.exists():
        bold = weight in ("700", "bold", "800", "900")
        candidates = [p for p in FONTS_DIR.glob("*.ttf") if family.replace(" ", "").lower() in p.name.replace(" ", "").lower()]
        candidates.sort(key=lambda p: ("Bold" in p.name) != bold)
        if candidates:
            return ImageFont.truetype(str(candidates[0]), max(1, round(size_pt * 4)))  # 4x for precision
    return ImageFont.load_default(size=max(1, round(size_pt * 4)))


def measure_pt(text: str, family: str, weight: str, size_pt: float) -> float:
    font = _font(family, weight, size_pt)
    return font.getlength(text) / 4.0


def fit_text(el: ET.Element, text: str) -> None:
    """Apply data-fit="shrink": reduce font-size down to data-min-size until the text fits."""
    max_width = float(el.get("data-max-width") or 0)
    if el.get("data-fit") != "shrink" or max_width <= 0:
        return
    size = float(el.get("font-size") or 12)
    min_size = float(el.get("data-min-size") or size)
    family = (el.get("font-family") or "sans-serif").split(",")[0].strip().strip("'\"")
    weight = el.get("font-weight") or "400"
    while size > min_size and measure_pt(text, family, weight, size) > max_width:
        size = round(size - 0.5, 2)
    el.set("font-size", f"{max(size, min_size):g}")


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def qr_data_url(payload: str) -> str:
    image = qrcode.make(payload, border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return _data_url(buf.getvalue(), "image/png")


def default_emblem(ministry: str) -> str | None:
    """Official emblem bundled with the API, as a data URL (unmodified artwork)."""
    path = EMBLEMS_DIR / f"{ministry}.svg"
    if not re.fullmatch(r"[a-z0-9-]{2,40}", ministry) or not path.exists():
        return None
    return _data_url(path.read_bytes(), "image/svg+xml")


def fill_svg(template: Template, data: dict[str, str], images: dict[str, str], locale: str = "es",
             ministry: str = "pathfinders") -> str:
    """Return the SVG with fields, translations and images applied."""
    images = dict(images)
    if not images.get("emblem"):
        emblem = default_emblem(ministry)
        if emblem:
            images["emblem"] = emblem
    root = safe_fromstring(template.svg)
    strings = resolve_strings(template, locale)
    rtl = locale.split("-")[0] in ("ar", "he", "fa", "ur")
    for el in root.iter():
        el_id = el.get("id")
        if not el_id:
            continue
        if el.tag == f"{{{SVG_NS}}}text":
            pattern = el.get("data-template-text")
            if el_id.startswith("t_") and el_id in strings:
                value = strings[el_id]
            elif pattern:
                value = re.sub(r"\{(\w+)\}", lambda m: str(data.get(m.group(1), "")), pattern)
                # "a · {missing} · c" -> "a · c": empty parts and their separators disappear
                value = "  ·  ".join(part.strip() for part in value.split("·") if part.strip()) or None
            elif el_id in data and str(data[el_id]).strip():
                value = str(data[el_id])
            elif f"{el_id}_placeholder" in strings:
                value = strings[f"{el_id}_placeholder"]       # e.g. "Club Director" under the signature line
            elif el_id.startswith("t_") or (locale.split("-")[0] == "es" and f"{el_id}_placeholder" in template.strings.get("es", {})):
                continue                                      # Spanish source text stays as designed
            else:
                value = None                                  # sample text never reaches a real certificate
            if value is None:
                el.text = ""
                continue
            for child in list(el):        # drop tspans: the field is a single run
                el.remove(child)
            el.text = value
            fit_text(el, value)
            if rtl:
                el.set("direction", "rtl")
                el.set("unicode-bidi", "bidi-override")
        elif el.tag == f"{{{SVG_NS}}}image" and el_id in IMAGE_FIELDS and el_id != "background":
            href = images.get(el_id)
            if href:
                el.set(f"{{{XLINK_NS}}}href", href)
                el.set("href", href)
            else:
                el.set("opacity", "0")   # keep geometry, show nothing
                el.set(f"{{{XLINK_NS}}}href", "")
    if rtl:
        root.set("direction", "rtl")
    # Design tools export width/height with units ("5.5in"), which resvg rejects: the
    # viewBox (in points) is the single source of truth for the physical size.
    root.set("width", f"{template.width_pt:g}")
    root.set("height", f"{template.height_pt:g}")
    return ET.tostring(root, encoding="unicode")


def output_size_pt(template: Template, width_in: float | None) -> tuple[float, float]:
    """Physical output size. Templates are vector: the same artwork can be produced at another
    size with the same proportions (quarter letter -> letter)."""
    if not width_in:
        return template.width_pt, template.height_pt
    width_pt = width_in * POINTS_PER_INCH
    return width_pt, width_pt * template.height_pt / template.width_pt


def render_png(svg: str, template: Template, dpi: int = 300, width_in: float | None = None) -> bytes:
    width_pt, height_pt = output_size_pt(template, width_in)
    width = round(width_pt / POINTS_PER_INCH * dpi)
    height = round(height_pt / POINTS_PER_INCH * dpi)
    font_dirs = [str(FONTS_DIR)] if FONTS_DIR.exists() else None
    return bytes(resvg_py.svg_to_bytes(
        svg_string=svg, width=width, height=height, resources_dir=str(template.directory),
        font_dirs=font_dirs, skip_system_fonts=bool(font_dirs), sans_serif_family="Noto Sans",
        serif_family="Noto Serif", monospace_family="Noto Sans Mono"))


def png_to_pdf(png: bytes, template: Template, width_in: float | None = None) -> bytes:
    width_pt, height_pt = output_size_pt(template, width_in)
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(width_pt, height_pt))
    pdf.drawImage(ImageReader(io.BytesIO(png)), 0, 0, width=width_pt, height=height_pt)
    pdf.save()
    return out.getvalue()


def render_certificate(slug: str, data: dict[str, str], images: dict[str, str], *, locale: str = "es",
                       fmt: str = "png", dpi: int = 300, base: Path = TEMPLATES_DIR,
                       ministry: str = "pathfinders", width_in: float | None = None) -> tuple[bytes, str]:
    if not LOCALE_RE.match(locale):
        raise TemplateError("Idioma no válido.")
    template = load_template(slug, base)
    svg = fill_svg(template, data, images, locale, ministry)
    if fmt == "svg":
        return svg.encode("utf-8"), "image/svg+xml"
    png = render_png(svg, template, dpi, width_in)
    if fmt == "png":
        return png, "image/png"
    if fmt == "pdf":
        return png_to_pdf(png, template, width_in), "application/pdf"
    raise TemplateError("Formato no válido.")
