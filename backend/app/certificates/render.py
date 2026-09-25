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
import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
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
# signature_*: a handwritten signature over each signature line (app/certificates/signatures.py);
# without one the slot draws nothing and the printed name stays exactly where it was.
IMAGE_FIELDS = ("emblem", "honor_patch", "qr", "issuer_logo", "background", "signature_director", "signature_instructor")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
POINTS_PER_INCH = 72.0


# Bloque F §1.7 — what a template certifies. Every template that existed before F says
# "honor"; an investiture template declares `"kinds": ["program"]` in its meta.json.
HONOR_KIND, PROGRAM_KIND = "honor", "program"


class TemplateError(ValueError):
    pass


# --- Editable phrases (docs/CERTIFICADOS_V4.md «Frases editables») ------------------------------
# Owner, 2026-09-24: «Se otorga el presente certificado a:» and «por haber cumplido
# satisfactoriamente los requisitos de…» can be reworded, ONLY by someone with an account. A
# template opts in with `"editable_strings"` in its meta.json: a list of the keys of its
# strings.<locale>.json that may be replaced («awarded», or {"key": "t_awarded_to", "role":
# "awarded"} when the key is named otherwise). The role tells the interface which label to show.
EDITABLE_ROLES = ("awarded", "completion")
# Hard ceiling of a phrase, whatever its box: the box usually allows less (`max_length`).
MAX_OVERRIDE_LENGTH = 160


class OverrideError(TemplateError):
    """A replacement phrase that is refused before anything is drawn or written. `code` is what
    the API answers (422 {"code", "detail", "key"})."""

    def __init__(self, code: str, message: str, key: str | None = None):
        super().__init__(message)
        self.code = code
        self.key = key


@dataclass(frozen=True)
class EditableString:
    key: str          # key in strings.<locale>.json (the data-string of an element template, the t_* id otherwise)
    role: str         # «awarded» | «completion»: what the phrase says, for the interface's label
    max_length: int   # characters the box takes at its minimum size (≤ MAX_OVERRIDE_LENGTH)
    defaults: dict[str, str]   # locale -> the template's own text


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

    @cached_property
    def editable_strings(self) -> list[EditableString]:
        """The fixed phrases this template lets a person with an account reword (meta.json
        `editable_strings`), each with its box's character budget and its text per locale."""
        declared = self.meta.get("editable_strings")
        if not isinstance(declared, list):
            return []
        root = safe_fromstring(self.svg)
        out = []
        for item in declared:
            key, role = (item, item) if isinstance(item, str) else (item.get("key"), item.get("role")) \
                if isinstance(item, dict) else (None, None)
            if not isinstance(key, str) or role not in EDITABLE_ROLES:
                raise TemplateError(f"Plantilla '{self.slug}': editable_strings mal declarado ({item!r}).")
            el = _string_element(root, key)
            if el is None:
                raise TemplateError(f"Plantilla '{self.slug}': la frase editable '{key}' no está en el SVG.")
            defaults = {loc: texts[key] for loc, texts in sorted(self.strings.items()) if isinstance(texts.get(key), str)}
            if "es" not in defaults:
                defaults["es"] = "".join(el.itertext()).strip()     # the SVG itself is the Spanish source
            out.append(EditableString(key, role, _phrase_budget(el, defaults["es"]), defaults))
        return out

    def editable(self, key: str) -> EditableString | None:
        return next((item for item in self.editable_strings if item.key == key), None)


def _string_element(root: ET.Element, key: str) -> ET.Element | None:
    """The <text> a phrase key prints in: `data-string="key"` (element templates) or `id="key"`."""
    for el in root.iter(f"{{{SVG_NS}}}text"):
        if el.get("data-string") == key or (el.get("id") == key and not el.get("data-string")):
            return el
    return None


def _phrase_budget(el: ET.Element, sample: str) -> int:
    """How many characters the box of `el` takes: its width × lines at the minimum size, over
    the average advance of the template's own phrase in that font. An estimate for the form's
    counter and the API's limit; whether a given text fits is still decided by `fit_lines`."""
    width = float(el.get("data-max-width") or 0)
    if width <= 0 or not sample:
        return MAX_OVERRIDE_LENGTH
    lines = max(1, int(el.get("data-max-lines") or 1))
    size = float(el.get("data-min-size") or el.get("font-size") or 12)
    family = (el.get("font-family") or "sans-serif").split(",")[0].strip().strip("'\"")
    sample = _transform_text(el, sample)
    advance = measure_pt(sample, family, el.get("font-weight") or "400", size) / len(sample)
    if advance <= 0:
        return MAX_OVERRIDE_LENGTH
    # wrapping at word boundaries wastes part of every line but the last
    budget = width * lines / advance * (1 if lines == 1 else 0.9)
    return max(len(sample), min(MAX_OVERRIDE_LENGTH, math.floor(budget)))


def clean_text_overrides(template: Template, overrides: dict[str, str] | None,
                         locale: str | None = None) -> dict[str, str]:
    """The phrases a caller wants reworded, checked: only the template's editable keys, one line
    of printable text, within the box's budget. Blank = the template's own text (dropped), and with
    `locale` a text equal to the template's in that language is dropped too, so only real changes
    are kept. Raises OverrideError; returns {key: text}."""
    cleaned: dict[str, str] = {}
    for key, raw in (overrides or {}).items():
        item = template.editable(key)
        if item is None:
            raise OverrideError("string_not_editable", f"La frase '{key}' no se puede cambiar en esta plantilla.", key)
        if not isinstance(raw, str):
            raise OverrideError("string_invalid", f"La frase '{key}' debe ser texto.", key)
        if any(unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp") for ch in raw):
            raise OverrideError("string_invalid", f"La frase '{key}' va en una sola línea, sin caracteres de control.", key)
        text = " ".join(raw.split())
        if not text:
            continue
        if len(text) > item.max_length:
            raise OverrideError("string_too_long",
                                f"La frase '{key}' admite como máximo {item.max_length} caracteres en esta plantilla.", key)
        if locale is not None and text == _default_in(item, locale):
            continue
        cleaned[key] = text
    return cleaned


def _default_in(item: EditableString, locale: str) -> str | None:
    parts = locale.split("-")
    for i in range(len(parts), 0, -1):
        candidate = "-".join(parts[:i])
        if candidate in item.defaults:
            return item.defaults[candidate]
    return item.defaults.get("es")


def check_overrides_fit(template: Template, overrides: dict[str, str]) -> None:
    """Before issuing: each reworded phrase fits its box (shrink → wrap → error, as the render
    does), so a certificate is never kept with a text its template cannot print."""
    if not overrides:
        return
    root = safe_fromstring(template.svg)
    for key, text in overrides.items():
        el = _string_element(root, key)
        if el is None:
            raise OverrideError("string_not_editable", f"La frase '{key}' no se puede cambiar en esta plantilla.", key)
        try:
            _apply_fit_wrap(el, text, key)
        except OverrideError:
            raise
        except TemplateError as exc:
            raise OverrideError("string_does_not_fit", str(exc), key) from exc


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


def _by_locale(table: dict, locale: str) -> dict[str, str]:
    """Most specific locale first (pt-BR -> pt); nothing when no locale matches."""
    parts = locale.split("-")
    for i in range(len(parts), 0, -1):
        candidate = "-".join(parts[:i])
        if candidate in table:
            return table[candidate]
    return {}


def resolve_strings(template: Template, locale: str) -> dict[str, str]:
    """Most specific locale first (pt-BR -> pt), Spanish (the SVG itself) as the final fallback."""
    return _by_locale(template.strings, locale)


def ministry_strings(template: Template, ministry: str | None, locale: str) -> dict[str, str]:
    """024: the brand words a design prints for ANOTHER ministry, from meta.json
    `ministry_strings: {ministry: {locale: {key: text}}}` — e.g. «CONQUISTADORES» becomes
    «AVENTUREROS» on an Adventurer certificate. Nothing for a ministry the design does not list."""
    table = (template.meta.get("ministry_strings") or {}).get(ministry or "") or {}
    return dict(_by_locale(table, locale)) if isinstance(table, dict) else {}


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
# Spanish in disguise. Optional, only where the design asks for them: data-wrap="balanced",
# data-text-transform="uppercase" and data-flow-trigger (flow_rules, see `_apply_flow`).
# Everything below only acts on those attributes: the older templates
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


def balanced_split(text: str, max_width: float, measure) -> list[str] | None:
    """The two lines of «wrap_strategy: balanced» (renderizador.js `fit`): of every cut between
    words, the one whose two lines differ least in width, both within `max_width` (first one on a
    tie). None when no cut fits."""
    words = text.split()
    best: tuple[float, list[str]] | None = None
    for i in range(1, len(words)):
        lines = [" ".join(words[:i]), " ".join(words[i:])]
        widths = [measure(line) for line in lines]
        score = abs(widths[0] - widths[1])
        if max(widths) <= max_width and (best is None or score < best[0]):
            best = (score, lines)
    return best[1] if best else None


def fit_lines(text: str, *, family: str, weight: str, size: float, min_size: float, max_width: float,
              max_lines: int, field: str, step: float = 1.0, balanced: bool = False) -> tuple[float, list[str]]:
    """«Wrap, then shrink, then error» — the loop of the design's reference renderer (the same for
    `shrink_then_wrap_or_error` and `wrap_then_shrink_or_error`): at each size from `size` down to
    `min_size` try one line, then word wrapping into at most `max_lines` lines that all fit, and only
    then the next size down. Wrapping is greedy, or with `balanced` (`wrap_strategy: "balanced"`,
    two lines) the cut that leaves both lines closest in width. Nothing is ever truncated: if no
    size fits, the certificate is not produced and the error names the field."""
    measure = lambda s, at: measure_pt(s, family, weight, at)  # noqa: E731
    current = size
    while current >= min_size - 1e-9:
        if measure(text, current) <= max_width:
            return current, [text]
        if balanced and max_lines == 2:
            split = balanced_split(text, max_width, lambda s: measure(s, current))
            if split:
                return current, split
        elif max_lines > 1:
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


def _transform_text(el: ET.Element, text: str) -> str:
    """`text_transform: "uppercase"` of the design package (data-text-transform), applied to whatever
    the field prints — translation, data or a reworded phrase — before it is measured."""
    return text.upper() if el.get("data-text-transform") == "uppercase" else text


def _apply_fit_wrap(el: ET.Element, text: str, field: str) -> None:
    family = (el.get("font-family") or "sans-serif").split(",")[0].strip().strip("'\"")
    text = _transform_text(el, text)
    size, lines = fit_lines(
        text, family=family, weight=el.get("font-weight") or "400",
        size=float(el.get("font-size") or 12), min_size=float(el.get("data-min-size") or el.get("font-size") or 12),
        max_width=float(el.get("data-max-width") or 0) or float("inf"),
        max_lines=int(el.get("data-max-lines") or 1), field=field, balanced=el.get("data-wrap") == "balanced")
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
             ministry: str = "pathfinders", overrides: dict[str, str] | None = None) -> str:
    """Return the SVG with fields, translations and images applied.

    `overrides` ({phrase key: text}, already checked by `clean_text_overrides`) replace the
    template's own editable phrases; the box's fitting contract applies to them as to any field."""
    overrides = overrides or {}
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
    strings = {**resolve_strings(template, locale), **ministry_strings(template, ministry, locale)}
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
            if key and overrides.get(key):
                _apply_fit_wrap(el, overrides[key], key)   # a reworded phrase: same box, same rules
                continue
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
        if el.tag == f"{{{SVG_NS}}}text" and overrides.get(el_id) and not el.get("data-string"):
            _apply_fit_wrap(el, overrides[el_id], el_id)      # older template: its box is data-max-width
            if rtl:
                el.set("direction", "rtl")
                el.set("unicode-bidi", "bidi-override")
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
    _apply_flow(root, set(drop))
    for el in drop:
        parents[el].remove(el)
    if rtl:
        root.set("direction", "rtl")
    # Design tools export width/height with units ("5.5in"), which resvg rejects: the
    # viewBox (in points) is the single source of truth for the physical size.
    root.set("width", f"{template.width_pt:g}")
    root.set("height", f"{template.height_pt:g}")
    return ET.tostring(root, encoding="unicode")


def _extra_line_height(trigger: ET.Element | None) -> float:
    """What a fitted text grew beyond its first line: (lines - 1) × size × line height."""
    if trigger is None:
        return 0.0
    lines = len(trigger.findall(f"{{{SVG_NS}}}tspan"))
    size = float(trigger.get("font-size") or 0)
    return max(0, lines - 1) * size * float(trigger.get("data-line-height") or 1.25)


def _shift(el: ET.Element, delta: float) -> None:
    """Move an element `delta` design units down: y / y1 / y2 as the reference renderer does
    (<text>, <line>, <rect>, <image>); anything placed otherwise (a <path>, a rotated text) gets
    a translate in front of its own transform."""
    if el.get("transform") or not any(el.get(key) is not None for key in ("y", "y1", "y2")):
        el.set("transform", f"translate(0 {delta:g}) {el.get('transform') or ''}".strip())
        return
    for key in ("y", "y1", "y2"):
        if el.get(key) is not None:
            el.set(key, f"{float(el.get(key)) + delta:g}")


def _apply_flow(root: ET.Element, dropped: set[ET.Element]) -> None:
    """`flow_rules` of the design package (docs/CERTIFICADOS_V4.md «flow_rules y wrap_then_shrink»):
    an element with data-flow-trigger="<text id>" moves down by the extra line height of that text
    once it is fitted — the name that takes two lines pushes the rule, the phrase and the honor
    under it. Depends on the data, so it is done here, after every text has its size and lines."""
    moving = [el for el in root.iter() if el.get("data-flow-trigger")]
    if not moving:
        return
    texts = {el.get("id"): el for el in root.iter(f"{{{SVG_NS}}}text") if el.get("id")}
    for el in moving:
        trigger = texts.get(el.get("data-flow-trigger"))
        delta = _extra_line_height(None if trigger in dropped else trigger)
        if delta:
            _shift(el, delta)


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
                       ministry: str = "pathfinders", width_in: float | None = None,
                       overrides: dict[str, str] | None = None) -> tuple[bytes, str]:
    if not LOCALE_RE.match(locale):
        raise TemplateError("Idioma no válido.")
    template = load_template(slug, base)
    svg = fill_svg(template, data, images, locale, ministry, overrides)
    if fmt == "svg":
        return svg.encode("utf-8"), "image/svg+xml"
    png = render_png(svg, template, dpi, width_in)
    if fmt == "png":
        return png, "image/png"
    if fmt == "pdf":
        return png_to_pdf(png, template, width_in), "application/pdf"
    raise TemplateError("Formato no válido.")
