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
                          (data-fit="shrink-wrap").
  background.webp         only when the first layers (paper, frame, fixed artwork) carry raster: they
                          are flattened once, here, into one page at the package's raster size
                          (300 dpi) — the engine pastes it with Pillow instead of resampling
                          megabytes of embedded PNG in resvg on every render.
  strings.<locale>.json   one per locale of the package (+ church_name from the sample data)
  meta.json               kinds ["honor"], ministries, title, source package
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


class CompileError(ValueError):
    pass


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


def _leading_fixed_layers(elements: list[dict], folder: Path, emblem: Path) -> list[dict]:
    """The run of fixed layers at the bottom of the page: shapes and asset images (not the emblem
    slot). Nothing dynamic is under them, so drawing them once changes nothing on the page."""
    run = []
    for e in elements:
        fixed_image = e["type"] == "image" and e["source"]["kind"] == "asset" and \
            not _same_file(folder / e["source"]["path"], emblem)
        if e["type"] != "shape" and not fixed_image:
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

    leading = _leading_fixed_layers(spec["elements"], folder, emblem)
    flatten = any(_has_raster(folder / e["source"]["path"]) for e in leading if e["type"] == "image")
    for index, e in enumerate(spec["elements"]):
        kind = e["type"]
        target = flat if flatten and index < len(leading) else body
        if kind == "shape":
            if e["shape"] not in SHAPES:
                raise CompileError(f"Forma no soportada: {e['shape']} ({e['id']})")
            target.append(f"<{e['shape']} {_attrs({'id': claim(e['id']), **e['attributes']})}/>")
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
                    body.append(f"<image {_attrs({'id': claim('emblem'), **box})} href=\"\"/>")
                else:
                    clip = {}
                    if asset.suffix.lower() == ".svg":
                        # an SVG asset may be a window (viewBox) onto a bigger drawing — the three
                        # artwork files of «Especialidad dorada» are crops of one full page. Browsers
                        # clip an <image> to its box; resvg does not, so the box is clipped explicitly.
                        clip_id = f"clip-{e['id']}"
                        target.append(f'<clipPath id="{escape(clip_id)}"><rect {_attrs({k: box[k] for k in ("x", "y", "width", "height")})}/></clipPath>')
                        clip = {"clip-path": f"url(#{clip_id})"}
                    target.append(f"<image {_attrs({'id': claim(e['id']), **box, **clip})} href={quoteattr(_data_url(asset))}/>")
            elif source["kind"] == "data":
                slot = IMAGE_SLOT.get(source["key"])
                if not slot:
                    raise CompileError(f"Imagen de datos desconocida: {source['key']}")
                extra = {}
                if e.get("placeholder_asset"):
                    extra["data-placeholder-href"] = _data_url(folder / e["placeholder_asset"])
                body.append(f"<image {_attrs({'id': claim(slot), **box, **extra})} href=\"\"/>")
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
            anchor = e.get("anchor") or e.get("align") or "start"
            anchor = {"left": "start", "center": "middle", "right": "end"}.get(anchor, anchor)
            visible = e.get("visible_if")
            if visible:
                if not visible.get("is_null") or visible.get("data_key") not in IMAGE_SLOT:
                    raise CompileError(f"visible_if no soportado en {e['id']}: {visible}")
                attrs["data-hide-if-image"] = IMAGE_SLOT[visible["data_key"]]
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
                "data-line-height": _num(e.get("line_height", 1.25)), **attrs,
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
    meta = {"title": spec.get("title", spec["id"]), "kinds": [engine.HONOR_KIND], "ministries": [ministry],
            "engine": "elements", "source": source, "locales": locales}
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
