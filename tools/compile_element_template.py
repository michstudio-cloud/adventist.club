"""Compile a composition-by-elements certificate design (v4 package) into an engine template.

    python tools/compile_element_template.py <v4-folder> --slug especialidad-editorial-rojo [--install]
                                             [--out DIR] [--ministry pathfinders] [--preview DIR]

The package folder (schema 1.0: e.g. ~/Documents/DEEL/certificados-diseno/especialidades-v4/01-editorial-rojo/
or ~/Documents/DEEL/certificados-diseno/replica-especialidad/) holds:
  plantilla.json      page (1100 x 850 in v4, 1600 x 1237 in «Especialidad dorada»; Letter landscape)
                      and `elements[]` back to front:
                      shape (rect/path + attributes) · image (asset / data) · text (translation / data / date)
  traducciones.json   {locale: {key: text}} for the fixed texts
  datos-ejemplo.json  sample data (church_name per locale becomes the default church line)
  recursos/           emblem, placeholder QR, fonts (OFL), fixed artwork (SVG, possibly with raster inside)

What comes out (templates/certificates/<slug>/ with --install, else --out DIR):
  template.svg            viewBox in points (792 x 612); the design keeps its own units inside a
                          <g transform="scale(0.72)"> (or translate+scale when the design's page is
                          not exactly 11:8.5 — fitted like a browser does, `meet`). Shapes stay vector;
                          every text is a live field with the package's fitting contract
                          (data-fit="shrink-wrap"; data-wrap="balanced", data-text-transform and
                          data-flow-trigger only when the package uses wrap_strategy, text_transform
                          or flow_rules).
  background.webp         only when the first layers (paper, frame, fixed artwork) carry raster: they
                          are flattened once, here, into one page at the package's raster size
                          (300 dpi) — the engine pastes it with Pillow instead of resampling
                          megabytes of embedded PNG in resvg on every render.
                          A signature slot (<image id="signature_director|signature_instructor">) is
                          derived over the line of each signer's name (docs «Firmas»).
  strings.<locale>.json   one per locale of the package (+ church_name from the sample data)
  meta.json               kinds ["honor"], ministries, title, source package, editable_strings
  README.md               where it came from and how to rebuild it

The design folder is only read. Only already-raster artwork is flattened; vectors and texts stay
live. --install also copies into fonts/ any face of the package the engine does not have yet
(with the license files next to it). Contract: docs/CERTIFICADOS_V4.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.certificates import render as engine  # noqa: E402
from PIL import Image, ImageFont  # noqa: E402
import resvg_py  # noqa: E402

PT_PER_INCH = 72.0
# v4 data keys -> engine field ids (the assistant and POST /render already speak these)
DATA_FIELD = {"folio": "certificate_no"}
IMAGE_SLOT = {"honor_image": "honor_patch", "qr_image": "qr"}
REQUIRED = {"recipient_name", "honor_name", "issued_date"}
# data texts whose default comes from the template's strings when the caller sends nothing
STRING_DEFAULTS = {"church_name"}
SHAPES = {"rect", "path", "circle", "ellipse", "line", "polyline", "polygon"}
# translation keys that only carry a SAMPLE of a data field: never copied into strings.<locale>.json,
# so nothing can ever print them in place of the real value (the association comes from the
# organisation tree of the member's club, docs/CERTIFICADOS_V4.md).
SAMPLE_ONLY_STRINGS = {"association"}
# Intl.DateTimeFormat options of a `date` text -> the engine's data-format
DATE_FORMATS = {("numeric", "long", "numeric"): "date-long", ("2-digit", "2-digit", "numeric"): "date-numeric"}
# page meet: a design page up to 1 % off 11:8.5 is fitted uniformly and centred, as a browser does
MAX_PAGE_DISTORTION = 0.01
BACKGROUND_FILE = "background.webp"
BACKGROUND_WEBP = {"quality": 90, "method": 6}
RASTER_IN_SVG = re.compile(rb"data:image/(png|jpeg|jpg|webp)", re.I)
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# Signature slots (docs/CERTIFICADOS_V4.md «Firmas»): an <image> over the line of each signer,
# as wide as the line and SIGNATURE_BODIES times the name's font size tall. The printed name
# does not move: the box ends at the name's cap height (name above the line) or on the line
# (name under it). Derived for any package that has these data texts, so a new design gets them.
SIGNATURE_SLOT = {"director_name": "signature_director", "instructor_name": "signature_instructor"}
SIGNATURE_BODIES = 2.2
CAP_HEIGHT = 0.75            # Noto Sans capitals are ~0.71 em: the box stops just above them
HLINE_RE = re.compile(r"^\s*M\s*(-?[\d.]+)[\s,]+(-?[\d.]+)\s*h\s*(-?[\d.]+)\s*$")
# Fitting contract of a text (docs «flow_rules y wrap_then_shrink»). Both overflow modes are the
# reference renderer's loop (one line → wrap → next size down → error), so neither needs an
# attribute; the balanced cut and the uppercase do, and only appear when the design asks for them.
OVERFLOWS = {None, "shrink_then_wrap_or_error", "wrap_then_shrink_or_error"}
WRAP_STRATEGIES = {None, "greedy", "balanced"}
TEXT_TRANSFORMS = {None, "none", "uppercase"}
FLOW_OFFSETS = {"extra_line_height"}


class CompileError(ValueError):
    pass


def _horizontal_lines(elements: list[dict]) -> list[tuple[float, float, float]]:
    """(x, y, width) of every straight horizontal rule of the design: `M x y h w` paths and
    <line> with y1 == y2. Signature lines are drawn like that in every v4 package."""
    found = []
    for e in elements:
        if e["type"] != "shape":
            continue
        a = e.get("attributes") or {}
        if e["shape"] == "path":
            m = HLINE_RE.match(str(a.get("d", "")))
            if m:
                x, y, w = (float(v) for v in m.groups())
                found.append((min(x, x + w), y, abs(w)))
        elif e["shape"] == "line" and a.get("y1") is not None and float(a["y1"]) == float(a.get("y2", a["y1"])):
            x1, x2 = float(a.get("x1", 0)), float(a.get("x2", 0))
            found.append((min(x1, x2), float(a["y1"]), abs(x2 - x1)))
    return found


def signature_align(e: dict) -> str:
    """preserveAspectRatio of the slot: contain (`meet`), lined up with the printed name —
    a left-aligned name gets its signature from the same left edge (xMin), a centred one centred —
    and resting on the bottom of the box (YMax), right over the name or the line."""
    anchor = e.get("anchor") or e.get("align") or "start"
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(anchor, anchor)
    return {"start": "xMinYMax", "middle": "xMidYMax", "end": "xMaxYMax"}.get(anchor, "xMidYMax") + " meet"


def signature_box(e: dict, lines: list[tuple[float, float, float]]) -> dict[str, float]:
    """Box of the signature slot of a signer's name `e` (a data text) in design units."""
    size, base, x = float(e["font_size"]), float(e["baseline_y"]), float(e["x"])
    width = float(e["max_width"])
    anchor = e.get("anchor") or e.get("align") or "start"
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(anchor, anchor)
    left = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
    near = [(abs(y - base), lx, y, lw) for lx, y, lw in lines
            if abs(y - base) <= 3 * size and lx < left + width and lx + lw > left]
    line_y = None
    if near:
        _, left, line_y, width = min(near)
    height = SIGNATURE_BODIES * size
    bottom = line_y if line_y is not None and line_y < base else base - CAP_HEIGHT * size
    return {"x": left, "y": bottom - height, "width": width, "height": height}


def _num(value) -> str:
    return f"{float(value):g}"


def _attrs(pairs: dict) -> str:
    return " ".join(f"{k}={quoteattr(str(v))}" for k, v in pairs.items() if v is not None)


def _data_url(path: Path) -> str:
    mime = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}[path.suffix.lower()]
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _same_file(a: Path, b: Path) -> bool:
    return a.exists() and b.exists() and hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()


def _page_transform(page: dict, width_pt: float, height_pt: float) -> str:
    """Design units -> points. Uniform scale; a design page slightly off 11:8.5 (1600 x 1237) is
    fitted and centred exactly like its own <svg viewBox> in a browser (preserveAspectRatio meet)."""
    width, height = float(page["width"]), float(page["height"])
    sx, sy = width_pt / width, height_pt / height
    if abs(sx - sy) / max(sx, sy) > MAX_PAGE_DISTORTION:
        raise CompileError("La página del diseño no tiene la proporción de su tamaño en pulgadas.")
    scale = min(sx, sy)
    tx, ty = (width_pt - width * scale) / 2, (height_pt - height * scale) / 2
    if abs(tx) < 1e-6 and abs(ty) < 1e-6:
        return f"scale({scale:g})"
    return f"translate({tx:.4g} {ty:.4g}) scale({scale:.6g})"


def _source_name(folder: Path, spec: dict) -> str:
    """Where the package lives under ~/Documents/DEEL/certificados-diseno/ (meta.json `source`)."""
    for parent in folder.parents:
        if parent.name == "certificados-diseno":
            return folder.relative_to(parent).as_posix()
    return f"{folder.parent.name}/{spec['id']}"


def _has_raster(asset: Path) -> bool:
    return asset.suffix.lower() in RASTER_SUFFIXES or (
        asset.suffix.lower() == ".svg" and bool(RASTER_IN_SVG.search(asset.read_bytes())))


def _text_field_id(e: dict) -> str:
    """The id a text element of the package gets in template.svg."""
    source = e["source"]
    return f"t_{e['id']}" if source["kind"] == "translation" else DATA_FIELD.get(source["key"], source["key"])


def flow_triggers(spec: dict) -> dict[str, str]:
    """`flow_rules` -> {package id of a moving element: template.svg id of the text that pushes it}.
    Only `offset: "extra_line_height"` exists: the element moves down by what the trigger grows
    beyond its first line ((lines - 1) × size × line_height), decided by the engine at render time."""
    by_id = {e["id"]: e for e in spec["elements"]}
    moving: dict[str, str] = {}
    for rule in spec.get("flow_rules") or []:
        trigger = by_id.get(rule.get("trigger"))
        if not trigger or trigger["type"] != "text":
            raise CompileError(f"flow_rules: el disparador {rule.get('trigger')!r} no es un texto del diseño.")
        if rule.get("offset") not in FLOW_OFFSETS:
            raise CompileError(f"flow_rules: desplazamiento no soportado {rule.get('offset')!r}.")
        for element_id in rule.get("shift_elements") or []:
            if element_id not in by_id or element_id == trigger["id"]:
                raise CompileError(f"flow_rules: no se puede desplazar {element_id!r}.")
            if element_id in moving:
                raise CompileError(f"flow_rules: {element_id!r} está en dos reglas.")
            moving[element_id] = _text_field_id(trigger)
    return moving


def _leading_fixed_layers(elements: list[dict], folder: Path, emblem: Path,
                          moving: dict[str, str] | None = None) -> list[dict]:
    """The run of fixed layers at the bottom of the page: shapes and asset images (not the emblem
    slot, nor anything a flow rule moves). Nothing dynamic is under them, so drawing them once
    changes nothing on the page."""
    run = []
    for e in elements:
        fixed_image = e["type"] == "image" and e["source"]["kind"] == "asset" and \
            not _same_file(folder / e["source"]["path"], emblem)
        if (e["type"] != "shape" and not fixed_image) or e["id"] in (moving or {}):
            break
        run.append(e)
    return run


def _flatten(transform: str, layers: list[str], page: dict, width_pt: float, height_pt: float) -> bytes:
    """Draw the leading layers once with resvg at the package's raster size (300 dpi) on white
    paper and encode them as WebP. Every raster inside them was already there in the design (e.g.
    frame, logo and seal cut from the same 3300 x 2550 page of the original PDF): one page instead
    of four copies, and the hidden part under the card becomes plain white."""
    width = int(page.get("raster_width") or round(width_pt / PT_PER_INCH * 300))
    height = int(page.get("raster_height") or round(height_pt / PT_PER_INCH * 300))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width_pt:g} {height_pt:g}" '
           f'width="{width_pt:g}" height="{height_pt:g}">\n'
           f'<rect width="{width_pt:g}" height="{height_pt:g}" fill="#fff"/>\n'
           f'<g transform="{transform}">\n' + "\n".join(layers) + "\n</g>\n</svg>\n")
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height))
    with Image.open(io.BytesIO(png)) as layer:
        page_image = Image.new("RGB", layer.size, "white")
        page_image.paste(layer.convert("RGB"), mask=layer.convert("RGBA").getchannel("A"))
    out = io.BytesIO()
    page_image.save(out, "WEBP", **BACKGROUND_WEBP)
    return out.getvalue()


def _face_weight(path: Path) -> tuple[str, int]:
    family, style = ImageFont.truetype(str(path), 12).getname()
    style = (style or "").lower()
    return family, next((w for name, w in engine._STYLE_WEIGHTS if name in style), 400)


def missing_fonts(folder: Path, spec: dict) -> list[Path]:
    """Font files of the package whose (family, weight) the engine's fonts/ does not have."""
    have = {_face_weight(p) for p in [*engine.FONTS_DIR.glob("*.ttf"), *engine.FONTS_DIR.glob("*.otf")]}
    missing = []
    for font in spec.get("fonts", []):
        path = folder / font["path"]
        if (font["family"], int(font["weight"])) in have:
            continue
        if not path.exists():
            raise CompileError(f"Falta la fuente {font['path']} del paquete.")
        if _face_weight(path) != (font["family"], int(font["weight"])):
            raise CompileError(f"{font['path']} no es {font['family']} {font['weight']}.")
        missing.append(path)
    return missing


# 024 · Selector de ministerio: the designs that ALSO serve Aventureros, by slug. Only those whose
# ministry art is swappable: the engine puts the ministry's emblem in the `emblem` slot and swaps
# these brand words (meta.json `ministry_strings`), or the design names no ministry at all
# (editorial-rojo: only the church logo). «Marco multicolor», «Especialidad dorada» and «Modular
# azul» have the Pathfinder shield / seal baked into background.webp: Conquistadores only until
# the owner provides an Adventurer version of that art.
EXTRA_MINISTRIES = {
    "especialidad-academico": ["adventurers"],
    "especialidad-reticula-verde": ["adventurers"],
    "especialidad-editorial-rojo": ["adventurers"],
}
MINISTRY_BRAND = {
    "adventurers": {"conquistadores": {"es": "AVENTUREROS", "en": "ADVENTURERS", "pt": "AVENTUREIROS", "fr": "AVENTURIERS"}},
}


def compile_package(folder: Path, slug: str, ministry: str = "pathfinders") -> dict[str, str | bytes]:
    """Return {file name: content} of the engine template. Raises CompileError on any gap."""
    spec = json.loads((folder / "plantilla.json").read_text(encoding="utf-8"))
    translations = json.loads((folder / "traducciones.json").read_text(encoding="utf-8"))
    sample = json.loads((folder / "datos-ejemplo.json").read_text(encoding="utf-8"))
    page = spec["page"]
    width_pt = float(page["width_inches"]) * PT_PER_INCH
    height_pt = float(page["height_inches"]) * PT_PER_INCH
    transform = _page_transform(page, width_pt, height_pt)
    locales = list(spec.get("supported_locales") or translations)
    for locale in locales:
        if locale not in translations:
            raise CompileError(f"traducciones.json no tiene el idioma '{locale}'.")

    supplied = {(f["family"], int(f["weight"])) for f in spec.get("fonts", [])}
    body: list[str] = []
    flat: list[str] = []          # leading fixed layers flattened into background.webp
    used_keys: set[str] = set()
    ids: set[str] = set()
    emblem = ROOT / "templates" / "assets" / "emblems" / f"{ministry}.svg"

    def claim(field_id: str) -> str:
        if field_id in ids:
            raise CompileError(f"Id repetido en la plantilla: {field_id}")
        ids.add(field_id)
        return field_id

    moving = flow_triggers(spec)
    leading = _leading_fixed_layers(spec["elements"], folder, emblem, moving)
    flatten = any(_has_raster(folder / e["source"]["path"]) for e in leading if e["type"] == "image")
    lines = _horizontal_lines(spec["elements"])
    for index, e in enumerate(spec["elements"]):
        kind = e["type"]
        target = flat if flatten and index < len(leading) else body
        flow = {"data-flow-trigger": moving.get(e["id"])}      # None (no attribute) unless a flow rule moves it
        if kind == "shape":
            if e["shape"] not in SHAPES:
                raise CompileError(f"Forma no soportada: {e['shape']} ({e['id']})")
            target.append(f"<{e['shape']} {_attrs({'id': claim(e['id']), **e['attributes'], **flow})}/>")
        elif kind == "image":
            if e.get("fit", "contain") != "contain" or e.get("crop"):
                raise CompileError(f"Sólo se admite fit=contain sin recorte ({e['id']}).")
            box = {"x": _num(e["x"]), "y": _num(e["y"]), "width": _num(e["width"]), "height": _num(e["height"]),
                   "preserveAspectRatio": "xMidYMid meet"}          # contain, centred, never cropped
            source = e["source"]
            if source["kind"] == "asset":
                asset = folder / source["path"]
                if not asset.exists():
                    raise CompileError(f"Falta el recurso {source['path']}")
                if _same_file(asset, emblem):
                    # the official emblem: the engine's `emblem` slot (default = the ministry emblem)
                    body.append(f"<image {_attrs({'id': claim('emblem'), **box, **flow})} href=\"\"/>")
                else:
                    clip = {}
                    if asset.suffix.lower() == ".svg":
                        # an SVG asset may be a window (viewBox) onto a bigger drawing — the three
                        # artwork files of «Especialidad dorada» are crops of one full page. Browsers
                        # clip an <image> to its box; resvg does not, so the box is clipped explicitly.
                        clip_id = f"clip-{e['id']}"
                        target.append(f'<clipPath id="{escape(clip_id)}"><rect {_attrs({**{k: box[k] for k in ("x", "y", "width", "height")}, **flow})}/></clipPath>')
                        clip = {"clip-path": f"url(#{clip_id})"}
                    target.append(f"<image {_attrs({'id': claim(e['id']), **box, **clip, **flow})} href={quoteattr(_data_url(asset))}/>")
            elif source["kind"] == "data":
                slot = IMAGE_SLOT.get(source["key"])
                if not slot:
                    raise CompileError(f"Imagen de datos desconocida: {source['key']}")
                extra = {}
                if e.get("placeholder_asset"):
                    extra["data-placeholder-href"] = _data_url(folder / e["placeholder_asset"])
                body.append(f"<image {_attrs({'id': claim(slot), **box, **extra, **flow})} href=\"\"/>")
            else:
                raise CompileError(f"Fuente de imagen desconocida: {source['kind']}")
        elif kind == "text":
            source = e["source"]
            attrs: dict = {}
            if source["kind"] == "translation":
                field_id, sample_text = f"t_{e['id']}", translations[spec.get('default_locale', 'es')][source["key"]]
                attrs["data-string"] = source["key"]
                used_keys.add(source["key"])
            elif source["kind"] in ("data", "date"):
                field_id = DATA_FIELD.get(source["key"], source["key"])
                sample_text = ""
                if source["kind"] == "date":
                    fmt = source.get("format") or {}
                    wanted = (fmt.get("day", "numeric"), fmt.get("month", "long"), fmt.get("year", "numeric"))
                    if wanted not in DATE_FORMATS:
                        raise CompileError(f"Formato de fecha no soportado en {e['id']}: {fmt}")
                    attrs["data-format"] = DATE_FORMATS[wanted]
                if field_id in STRING_DEFAULTS:
                    attrs["data-fallback-string"] = field_id
                if field_id in REQUIRED:
                    attrs["data-required"] = "true"
            else:
                raise CompileError(f"Fuente de texto desconocida: {source['kind']}")
            if source["kind"] == "data" and field_id in SIGNATURE_SLOT:
                # drawn before the name: a signature that reaches down never covers the printed name
                box = {k: _num(round(v, 3)) for k, v in signature_box(e, lines).items()}
                body.append(f"<image {_attrs({'id': claim(SIGNATURE_SLOT[field_id]), **box, 'preserveAspectRatio': signature_align(e), **flow})} href=\"\"/>")
            anchor = e.get("anchor") or e.get("align") or "start"
            anchor = {"left": "start", "center": "middle", "right": "end"}.get(anchor, anchor)
            visible = e.get("visible_if")
            if visible:
                if not visible.get("is_null") or visible.get("data_key") not in IMAGE_SLOT:
                    raise CompileError(f"visible_if no soportado en {e['id']}: {visible}")
                attrs["data-hide-if-image"] = IMAGE_SLOT[visible["data_key"]]
            if e.get("overflow") not in OVERFLOWS:
                raise CompileError(f"overflow no soportado en {e['id']}: {e.get('overflow')}")
            if e.get("wrap_strategy") not in WRAP_STRATEGIES:
                raise CompileError(f"wrap_strategy no soportado en {e['id']}: {e.get('wrap_strategy')}")
            if e.get("wrap_strategy") == "balanced":
                if int(e.get("max_lines", 1)) != 2:
                    raise CompileError(f"wrap_strategy balanced sólo con max_lines 2 ({e['id']}).")
                attrs["data-wrap"] = "balanced"
            if e.get("text_transform") not in TEXT_TRANSFORMS:
                raise CompileError(f"text_transform no soportado en {e['id']}: {e.get('text_transform')}")
            if e.get("text_transform") == "uppercase":
                attrs["data-text-transform"] = "uppercase"          # the engine uppercases what it prints
                sample_text = sample_text.upper()
            weight = int(e["font_weight"])
            if (e["font_family"], weight) not in supplied:
                # The package does not ship this weight: its previews (and any browser) draw a
                # synthetic bold of the lighter face. Reproduce that look, not a real bold the
                # designer never saw — same advances, outline widened like Chrome/Skia does.
                lighter = sorted(w for f, w in supplied if f == e["font_family"] and w < weight)
                if not lighter or weight < 600:
                    raise CompileError(f"El paquete no incluye {e['font_family']} {weight} ({e['id']}).")
                weight = lighter[-1]
                attrs["data-synthetic-bold"] = "true"
            text_attrs = {
                "id": claim(field_id), "x": _num(e["x"]), "y": _num(e["baseline_y"]),
                "font-family": e["font_family"], "font-weight": str(weight),
                "font-size": _num(e["font_size"]), "fill": e["color"],
                "text-anchor": None if anchor == "start" else anchor,
                "transform": f"rotate({_num(e['rotate'])} {_num(e['x'])} {_num(e['baseline_y'])})" if e.get("rotate") else None,
                "data-fit": "shrink-wrap", "data-max-width": _num(e["max_width"]),
                "data-min-size": _num(e.get("min_font_size", e["font_size"])),
                "data-max-lines": str(int(e.get("max_lines", 1))),
                "data-line-height": _num(e.get("line_height", 1.25)), **attrs, **flow,
            }
            body.append(f"<text {_attrs(text_attrs)}>{escape(sample_text)}</text>")
        else:
            raise CompileError(f"Tipo de elemento desconocido: {kind}")

    strings: dict[str, dict[str, str]] = {}
    church = sample.get("church_name") if isinstance(sample.get("church_name"), dict) else {}
    for locale in locales:
        table = {k: v for k, v in translations[locale].items()
                 if isinstance(v, str) and k not in SAMPLE_ONLY_STRINGS}   # e.g. "title": [..] is layout only
        missing = sorted(k for k in used_keys if not str(table.get(k) or "").strip())
        if missing:
            raise CompileError(f"Faltan traducciones en '{locale}': {', '.join(missing)}")
        if not church.get(locale):
            raise CompileError(f"datos-ejemplo.json no trae church_name en '{locale}'.")
        table["church_name"] = church[locale]
        strings[locale] = table

    source = _source_name(folder, spec)
    label = "v4" if source.startswith("especialidades-v4/") else "por elementos"
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {width_pt:g} {height_pt:g}" width="{page["width_inches"]:g}in" height="{page["height_inches"]:g}in" '
        f'data-template="{slug}" data-source="{escape(spec["id"])}" data-version="{escape(str(spec.get("schema_version", "1")))}">\n'
    )
    background = ""
    files: dict[str, str | bytes] = {}
    if flat:
        # one opaque page under every live element: split out and pasted by render_png (Pillow)
        files[BACKGROUND_FILE] = _flatten(transform, flat, page, width_pt, height_pt)
        background = (f'<image id="background" x="0" y="0" width="{width_pt:g}" height="{height_pt:g}" '
                      f'preserveAspectRatio="none" href="{BACKGROUND_FILE}"/>\n')
    svg = (
        header +
        f"<!-- Compilada con tools/compile_element_template.py desde el paquete {label} «{escape(spec.get('title', spec['id']))}». "
        f"No editar a mano: regenerar. Unidades del diseño ({page['width']:g} x {page['height']:g}) dentro del grupo escalado. -->\n"
        + background +
        f'<g transform="{transform}">\n' + "\n".join(body) + "\n</g>\n</svg>\n"
    )
    served = [ministry, *(EXTRA_MINISTRIES.get(slug, []) if ministry == "pathfinders" else [])]
    meta = {"title": spec.get("title", spec["id"]), "kinds": [engine.HONOR_KIND], "ministries": served,
            "engine": "elements", "source": source, "locales": locales}
    # «Frases editables» (docs/CERTIFICADOS_V4.md): the award phrases a person with an account may
    # reword, when the package names them with the usual keys.
    editable = [key for key in engine.EDITABLE_ROLES if any(key in table for table in strings.values())]
    if editable:
        meta["editable_strings"] = editable
    # 024: the brand words the design prints («CONQUISTADORES») for the other ministries it serves.
    swapped = {
        other: {
            locale: {key: words[locale] for key, words in MINISTRY_BRAND[other].items()
                     if key in strings.get(locale, {}) and locale in words}
            for locale in locales
        }
        for other in served[1:] if other in MINISTRY_BRAND
    }
    swapped = {other: {loc: table for loc, table in tables.items() if table} for other, tables in swapped.items()}
    swapped = {other: tables for other, tables in swapped.items() if tables}
    if swapped:
        meta["ministry_strings"] = swapped
    readme = (
        f"Plantilla compilada del paquete de diseño {label} `{spec['id']}` («{spec.get('title', spec['id'])}»).\n"
        f"Fuente: `~/Documents/DEEL/certificados-diseno/{source}/` (plantilla.json + traducciones.json).\n"
        f"Regenerar: `python tools/compile_element_template.py <carpeta> --slug {slug} --install`.\n"
        + (f"`{BACKGROUND_FILE}`: papel, marco y arte fijo del paquete aplanados una vez (raster ya presente en el diseño).\n" if flat else "")
        + f"Contrato y datos: `docs/CERTIFICADOS_V4.md`. No editar template.svg a mano.\n"
    )
    files.update({"template.svg": svg, "meta.json": json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "README.md": readme})
    for locale, table in strings.items():
        files[f"strings.{locale}.json"] = json.dumps(table, ensure_ascii=False, indent=2) + "\n"
    return files


def sample_render_input(folder: Path, locale: str) -> tuple[dict[str, str], dict[str, str]]:
    """datos-ejemplo.json as POST /render would receive it for `locale` (for previews and tests)."""
    sample = json.loads((folder / "datos-ejemplo.json").read_text(encoding="utf-8"))
    data, images = {}, {}
    for key, value in sample.items():
        if key in IMAGE_SLOT:
            if value:
                path = folder / value
                images[IMAGE_SLOT[key]] = _data_url(path) if path.exists() else value
            continue
        if isinstance(value, dict):
            value = value.get(locale)
        if value:
            data[DATA_FIELD.get(key, key)] = str(value)
    return data, images


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--ministry", default="pathfinders")
    ap.add_argument("--out", type=Path, help="output folder (default: ./salida/<slug>, never inside the design folder)")
    ap.add_argument("--install", action="store_true", help="write into templates/certificates/<slug>/")
    ap.add_argument("--preview", type=Path, help="also render <locale>.png of the sample data into this folder")
    args = ap.parse_args()
    folder = args.folder.expanduser().resolve()
    if not engine.SLUG_RE.match(args.slug):
        sys.exit(f"Slug no válido: {args.slug}")
    try:
        files = compile_package(folder, args.slug, args.ministry)
    except (CompileError, KeyError, OSError, ValueError) as exc:
        sys.exit(f"No se pudo compilar {folder.name}: {exc}")
    target = ROOT / "templates" / "certificates" / args.slug if args.install else (args.out or Path("salida") / args.slug)
    target = target.resolve()
    if folder == target or folder in target.parents:
        sys.exit("La salida no puede ir dentro de la carpeta del diseño (sólo se lee).")
    if target.exists() and args.install:
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if isinstance(content, bytes):
            (target / name).write_bytes(content)
        else:
            (target / name).write_text(content, encoding="utf-8")
    print(f"{args.slug}: {len(files)} archivos en {target}")
    if args.install:
        spec = json.loads((folder / "plantilla.json").read_text(encoding="utf-8"))
        for path in missing_fonts(folder, spec):
            shutil.copyfile(path, engine.FONTS_DIR / path.name)
            print(f"fuente instalada: {path.name}")
            for license_file in sorted(path.parent.glob("OFL*.txt")):
                if not (engine.FONTS_DIR / license_file.name).exists():
                    shutil.copyfile(license_file, engine.FONTS_DIR / license_file.name)
                    print(f"licencia: {license_file.name}")
        engine._font.cache_clear()
    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        engine._load.cache_clear()
        template = engine.load_template(args.slug, target.parent)
        for locale in json.loads(files["meta.json"])["locales"]:
            data, images = sample_render_input(folder, locale)
            svg = engine.fill_svg(template, data, images, locale, args.ministry)
            (args.preview / f"{args.slug}-{locale}.png").write_bytes(engine.render_png(svg, template, dpi=300))
        print(f"Vistas previas en {args.preview}")


if __name__ == "__main__":
    main()
