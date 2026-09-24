"""Certificate rendering from designer-made SVG templates.

A template lives in `templates/certificates/<slug>/`:
  template.svg            physical size in the viewBox (1 unit = 1 pt); dynamic fields are
                          <text id="..."> / <image id="...">; fixed translatable texts are t_*
  strings.<locale>.json   translations for the t_* texts (Spanish is the source language)
  background.png|svg      optional artwork referenced from template.svg (relative href)

Rendering never invents content: unknown fields keep the template's sample text, missing
images are removed, and a missing translation falls back to the Spanish in the SVG.
Contract: docs/I18N_Y_PLANTILLAS.md. Element templates compiled from v4 design packages
(data-fit="shrink-wrap", data-string) are stricter — no Spanish fallback, overflow is an error:
docs/CERTIFICADOS_V4.md.
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
from PIL import Image, ImageFont
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


# Bloque F §1.7 — what a template certifies. Every template that existed before F says
# "honor"; an investiture template declares `"kinds": ["program"]` in its meta.json.
HONOR_KIND, PROGRAM_KIND = "honor", "program"


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
    # Bloque F §1.7 — optional meta.json: {"ministries": ["master-guides"], "kinds": ["program"]}.
    # No meta.json = every ministry, kind "honor" (what every template shipped so far is).
    meta: dict = field(default_factory=dict)

    @property
    def locales(self) -> list[str]:
        return sorted({"es", *self.strings})

    @property
    def kinds(self) -> list[str]:
        declared = self.meta.get("kinds")
        return [str(k) for k in declared] if isinstance(declared, list) and declared else [HONOR_KIND]

    @property
    def ministries(self) -> list[str] | None:
        """None = valid for every ministry."""
        declared = self.meta.get("ministries")
        return [str(m) for m in declared] if isinstance(declared, list) and declared else None

    @property
    def listed(self) -> bool:
        """`"listed": false` retires a template from the pickers (owner, 2026-09-24: only the v4
        honor designs and the investiture template are offered) while certificates already issued
        with it keep rendering: `load_template` never looks at this."""
        return self.meta.get("listed", True) is not False

    def serves(self, ministry: str | None = None, kind: str | None = None) -> bool:
        if not self.listed:
            return False
        if kind is not None and kind not in self.kinds:
            return False
        allowed = self.ministries
        return ministry is None or allowed is None or ministry in allowed

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
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if not isinstance(meta, dict):
        raise TemplateError(f"Plantilla '{slug}' tiene un meta.json que no es un objeto.")
    return Template(slug, directory, svg, float(view[2]), float(view[3]), strings, meta)


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


# Style names of the bundled files -> CSS weight (anything else, e.g. «Beta», is a regular face).
_STYLE_WEIGHTS = (("extralight", 200), ("ultralight", 200), ("semibold", 600), ("demibold", 600),
                  ("extrabold", 800), ("ultrabold", 800), ("thin", 100), ("light", 300), ("medium", 500),
                  ("bold", 700), ("black", 900), ("heavy", 900))


def _file_weight(path: Path) -> int:
    style = path.stem.split("-", 1)[1].lower() if "-" in path.stem else ""
    return next((weight for name, weight in _STYLE_WEIGHTS if name in style), 400)


def _css_weight(weight: str) -> int:
    value = {"normal": "400", "bold": "700"}.get(str(weight).strip().lower(), str(weight).strip())
    return int(value) if value.isdigit() else 400


@lru_cache(maxsize=64)
def _font(family: str, weight: str, size_pt: float):
    """Font used only to MEASURE text for data-fit; resvg does the real drawing.

    The face is chosen like CSS (and resvg's fontdb) do: the exact weight of the family, else the
    nearest one on the side CSS prefers (heavier above 500, lighter at or below). TrueType and
    OpenType-CFF (.otf, e.g. Advent Sans) files are both looked at."""
    if FONTS_DIR.exists():
        wanted = family.replace(" ", "").lower()
        fonts = sorted([*FONTS_DIR.glob("*.ttf"), *FONTS_DIR.glob("*.otf")])
        # exact family first ("Noto Sans" is NotoSans-*.ttf, never NotoSansMono-*.ttf)
        candidates = [p for p in fonts if p.stem.split("-")[0].lower() == wanted] or \
            [p for p in fonts if wanted in p.name.replace(" ", "").lower()]
        candidates = [p for p in candidates if "italic" not in p.stem.lower()] or candidates
        if candidates:
            target = _css_weight(weight)

            def rank(path: Path) -> tuple:
                have = _file_weight(path)
                if have == target:
                    return (0, 0, path.name)
                prefer_heavier = target > 500
                on_preferred_side = (have > target) == prefer_heavier
                return (1 if on_preferred_side else 2, abs(have - target), path.name)

            best = min(candidates, key=rank)
            return ImageFont.truetype(str(best), max(1, round(size_pt * 4)))  # 4x for precision
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


# --- Element templates (docs/CERTIFICADOS_V4.md) ----------------------------------------------
# Compiled by tools/compile_element_template.py from a composition-by-elements design package.
# Their <text> fields carry the fitting contract of the package: data-fit="shrink-wrap",
# data-max-width, data-min-size, data-max-lines, data-line-height. Fixed texts carry
# data-string="<key>" (strings.<locale>.json) and a missing translation is an error, never
# Spanish in disguise. Everything below only acts on those attributes: the older templates
# (data-fit="shrink", t_* ids) render exactly as before.

# v4 data keys -> the engine's own names, so both vocabularies work in POST /render
DATA_ALIASES = {"folio": "certificate_no"}
IMAGE_ALIASES = {"honor_image": "honor_patch", "qr_image": "qr"}

MONTHS = {
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
           "octubre", "noviembre", "diciembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"),
    "pt": ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
           "outubro", "novembro", "dezembro"),
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
           "octobre", "novembre", "décembre"),
}
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def format_long_date(value: str, locale: str) -> str:
    """ISO "2026-09-21" -> the long date of the certificate's language, as Intl.DateTimeFormat
    writes it with {day: numeric, month: long, year: numeric} (the design's reference renderer):
    es «21 de septiembre de 2026», en «September 21, 2026», pt «21 de setembro de 2026»,
    fr «21 septembre 2026». Anything that is not an ISO date was already formatted by the caller
    (the assistant sends it in the certificate's language) and is kept as it is."""
    match = ISO_DATE_RE.match(value.strip())
    if not match:
        return value
    year, month, day = (int(g) for g in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise TemplateError(f"Fecha no válida: {value}.")
    language = locale.split("-")[0].lower()
    if language not in MONTHS:
        raise TemplateError(f"No hay formato de fecha para el idioma '{locale}'.")
    name = MONTHS[language][month - 1]
    if language == "en":
        return f"{name} {day}, {year}"
    if language == "fr":
        return f"{day} {name} {year}"
    return f"{day} de {name} de {year}"


# Numeric dates as Intl.DateTimeFormat writes {day: "2-digit", month: "2-digit", year: "numeric"}:
# en-US puts the month first; es, pt and fr the day. Other languages are not guessed.
NUMERIC_DATE_ORDER = {"es": "dmy", "pt": "dmy", "fr": "dmy", "en": "mdy"}


def _parse_long_date(value: str, language: str) -> tuple[int, int, int] | None:
    """The long date of `format_long_date` (what the assistant sends, from Intl `dateStyle: long`)
    back to (year, month, day); None when it is not one."""
    names = MONTHS.get(language)
    if not names:
        return None
    text = " ".join(value.strip().split())
    month_re = "|".join(re.escape(name) for name in names)
    patterns = (rf"^(?P<m>{month_re}) (?P<d>\d{{1,2}}), (?P<y>\d{{4}})$",) if language == "en" else (
        rf"^(?P<d>\d{{1,2}})(?:er)? (?:de )?(?P<m>{month_re}) (?:de )?(?P<y>\d{{4}})$",)
    for pattern in patterns:
        found = re.match(pattern, text, re.I)
        if found:
            month = [name.lower() for name in names].index(found.group("m").lower()) + 1
            return int(found.group("y")), month, int(found.group("d"))
    return None


def format_numeric_date(value: str, locale: str) -> str:
    """ISO "2026-09-21" (or the long date the assistant sends) -> «21/09/2026» (es, pt, fr) or
    «09/21/2026» (en), the design's {day: 2-digit, month: 2-digit, year: numeric} in UTC.
    Anything else is kept as the caller wrote it."""
    language = locale.split("-")[0].lower()
    if language not in NUMERIC_DATE_ORDER:
        raise TemplateError(f"No hay formato de fecha para el idioma '{locale}'.")
    match = ISO_DATE_RE.match(value.strip())
    parts = tuple(int(g) for g in match.groups()) if match else _parse_long_date(value, language)
    if not parts:
        return value
    year, month, day = parts
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise TemplateError(f"Fecha no válida: {value}.")
    if NUMERIC_DATE_ORDER[language] == "mdy":
        return f"{month:02d}/{day:02d}/{year}"
    return f"{day:02d}/{month:02d}/{year}"


def fit_lines(text: str, *, family: str, weight: str, size: float, min_size: float, max_width: float,
              max_lines: int, field: str, step: float = 1.0) -> tuple[float, list[str]]:
    """«Shrink, then wrap up to max_lines, then error» — the algorithm of the design's reference
    renderer: at each size from `size` down to `min_size` try one line, then greedy word wrapping
    into at most `max_lines` lines that all fit. Nothing is ever truncated: if no size fits, the
    certificate is not produced and the error names the field."""
    measure = lambda s, at: measure_pt(s, family, weight, at)  # noqa: E731
    current = size
    while current >= min_size - 1e-9:
        if measure(text, current) <= max_width:
            return current, [text]
        if max_lines > 1:
            lines: list[str] = []
            for word in text.split():
                candidate = f"{lines[-1]} {word}" if lines else word
                if lines and measure(candidate, current) <= max_width:
                    lines[-1] = candidate
                else:
                    lines.append(word)
            if len(lines) <= max_lines and all(measure(line, current) <= max_width for line in lines):
                return current, lines
        current = round(current - step, 4)
    raise TemplateError(f"El texto del campo '{field}' no cabe en su espacio ni al tamaño mínimo "
                        f"({min_size:g}) en {max_lines} línea(s).")


def _apply_fit_wrap(el: ET.Element, text: str, field: str) -> None:
    family = (el.get("font-family") or "sans-serif").split(",")[0].strip().strip("'\"")
    size, lines = fit_lines(
        text, family=family, weight=el.get("font-weight") or "400",
        size=float(el.get("font-size") or 12), min_size=float(el.get("data-min-size") or el.get("font-size") or 12),
        max_width=float(el.get("data-max-width") or 0) or float("inf"),
        max_lines=int(el.get("data-max-lines") or 1), field=field)
    el.set("font-size", f"{size:g}")
    for child in list(el):
        el.remove(child)
    el.text = None
    if el.get("data-synthetic-bold") == "true":
        # Chrome/Skia's fake bold: outline widened by size/24 at 9 px .. size/32 from 36 px on,
        # advances untouched. Used when the design package ships no bold of that family.
        ratio = 1 / 24 + (1 / 32 - 1 / 24) * min(1.0, max(0.0, (size - 9) / 27))
        el.set("stroke", el.get("fill") or "#000")
        el.set("stroke-width", f"{size * ratio:.4g}")
        el.set("stroke-linejoin", "miter")
    line_height = float(el.get("data-line-height") or 1.25)
    for i, line in enumerate(lines):
        tspan = ET.SubElement(el, f"{{{SVG_NS}}}tspan")
        tspan.set("x", el.get("x") or "0")
        tspan.set("dy", "0" if i == 0 else f"{size * line_height:g}")
        tspan.text = line


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
    data = dict(data)
    for alias, key in IMAGE_ALIASES.items():
        if images.get(alias) and not images.get(key):
            images[key] = images[alias]
    for alias, key in DATA_ALIASES.items():
        if str(data.get(alias) or "").strip() and not str(data.get(key) or "").strip():
            data[key] = data[alias]
    if not images.get("emblem"):
        emblem = default_emblem(ministry)
        if emblem:
            images["emblem"] = emblem
    root = safe_fromstring(template.svg)
    strings = resolve_strings(template, locale)
    rtl = locale.split("-")[0] in ("ar", "he", "fa", "ur")
    parents = {child: parent for parent in root.iter() for child in parent}
    drop: list[ET.Element] = []
    for el in root.iter():
        el_id = el.get("id")
        if not el_id:
            continue
        hide_if = el.get("data-hide-if-image")
        if hide_if and images.get(hide_if):
            drop.append(el)                   # e.g. the «QR» label of the placeholder box
            continue
        if el.tag == f"{{{SVG_NS}}}text" and el.get("data-fit") == "shrink-wrap":
            # element template: strict contract (see fit_lines)
            key = el.get("data-string")
            if key:
                if key not in strings:
                    raise TemplateError(f"Falta la traducción '{key}' en '{locale}' (plantilla {template.slug}).")
                value = strings[key]
            else:
                value = str(data.get(el_id) or "").strip()
                fallback = el.get("data-fallback-string")
                if not value and fallback:
                    if fallback not in strings:
                        raise TemplateError(f"Falta la traducción '{fallback}' en '{locale}' (plantilla {template.slug}).")
                    value = strings[fallback]
                if value and el.get("data-format") == "date-long":
                    value = format_long_date(value, locale)
                elif value and el.get("data-format") == "date-numeric":
                    value = format_numeric_date(value, locale)
            if not value:
                if el.get("data-required") == "true":
                    raise TemplateError(f"Falta el dato '{el_id}' para la plantilla {template.slug}.")
                drop.append(el)               # optional and absent: nothing is invented
                continue
            _apply_fit_wrap(el, value, el_id)
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
            href = images.get(el_id) or el.get("data-placeholder-href")   # e.g. the empty QR box of a preview
            if href:
                el.set(f"{{{XLINK_NS}}}href", href)
                el.set("href", href)
            else:
                el.set("opacity", "0")   # keep geometry, show nothing
                el.set(f"{{{XLINK_NS}}}href", "")
            if "data-placeholder-href" in el.attrib:
                del el.attrib["data-placeholder-href"]
    for el in drop:
        parents[el].remove(el)
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


BACKGROUND_RE = re.compile(r'<image\b[^>]*\bid="background"[^>]*/>')


def split_raster_background(svg: str, template: Template) -> tuple[Path, str] | None:
    """A template may put a full-page raster under the live fields (`<image id="background" href="file.png">`).
    resvg spends ~10x longer resampling that page than drawing everything else, so it is taken out
    here and pasted with Pillow. Returns (file, svg without it), or None for a plain vector template."""
    found = BACKGROUND_RE.search(svg)
    href = re.search(r'href="([^"]+)"', found.group(0)) if found else None
    if not href or href.group(1).startswith("data:"):
        return None
    path = (template.directory / href.group(1)).resolve()
    if template.directory.resolve() not in path.parents or not path.is_file():
        return None
    return template.directory / href.group(1), svg[:found.start()] + svg[found.end():]


@lru_cache(maxsize=8)   # thumbnail, preview and the print sizes of the templates in use (~35 MB for a 300-dpi letter page)
def _page_image(path: str, modified: float, width: int, height: int) -> Image.Image:
    with Image.open(path) as page:
        page = page.convert("RGBA")
        # reducing_gap: box-reduce first, then Lanczos — several times faster for thumbnails, same look
        return page if page.size == (width, height) else page.resize((width, height), Image.LANCZOS, reducing_gap=2.0)


def render_png(svg: str, template: Template, dpi: int = 300, width_in: float | None = None) -> bytes:
    width_pt, height_pt = output_size_pt(template, width_in)
    width = round(width_pt / POINTS_PER_INCH * dpi)
    height = round(height_pt / POINTS_PER_INCH * dpi)
    font_dirs = [str(FONTS_DIR)] if FONTS_DIR.exists() else None
    split = split_raster_background(svg, template)
    layer = bytes(resvg_py.svg_to_bytes(
        svg_string=split[1] if split else svg, width=width, height=height, resources_dir=str(template.directory),
        font_dirs=font_dirs, skip_system_fonts=bool(font_dirs), sans_serif_family="Noto Sans",
        serif_family="Noto Serif", monospace_family="Noto Sans Mono"))
    if not split:
        return layer
    page = _page_image(str(split[0]), split[0].stat().st_mtime, width, height).copy()
    page.alpha_composite(Image.open(io.BytesIO(layer)).convert("RGBA"))
    out = io.BytesIO()
    (page.convert("RGB") if page.getextrema()[3][0] == 255 else page).save(out, "PNG", compress_level=3)
    return out.getvalue()


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
