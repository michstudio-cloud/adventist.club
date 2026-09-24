"""Compile a composition-by-elements certificate design (v4 package) into an engine template.

    python tools/compile_element_template.py <v4-folder> --slug especialidad-editorial-rojo [--install]
                                             [--out DIR] [--ministry pathfinders] [--preview DIR]

The v4 folder (e.g. ~/Documents/DEEL/certificados-diseno/especialidades-v4/01-editorial-rojo/) holds:
  plantilla.json      page 1100 x 850 (Letter landscape) and `elements[]` back to front:
                      shape (rect/path + attributes) · image (asset / data) · text (translation / data / date)
  traducciones.json   {locale: {key: text}} for the fixed texts
  datos-ejemplo.json  sample data (church_name per locale becomes the default church line)
  recursos/           emblem, placeholder QR, fonts (OFL)

What comes out (templates/certificates/<slug>/ with --install, else --out DIR):
  template.svg            viewBox in points (792 x 612); the design keeps its own units inside a
                          <g transform="scale(0.72)">. Shapes stay vector; every text is a live
                          field with the package's fitting contract (data-fit="shrink-wrap").
  strings.<locale>.json   one per locale of the package (+ church_name from the sample data)
  meta.json               kinds ["honor"], ministries, title, source package
  README.md               where it came from and how to rebuild it

The design folder is only read. Nothing is rasterised. Contract: docs/CERTIFICADOS_V4.md.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.certificates import render as engine  # noqa: E402

PT_PER_INCH = 72.0
# v4 data keys -> engine field ids (the assistant and POST /render already speak these)
DATA_FIELD = {"folio": "certificate_no"}
IMAGE_SLOT = {"honor_image": "honor_patch", "qr_image": "qr"}
REQUIRED = {"recipient_name", "honor_name", "issued_date"}
# data texts whose default comes from the template's strings when the caller sends nothing
STRING_DEFAULTS = {"church_name"}
SHAPES = {"rect", "path", "circle", "ellipse", "line", "polyline", "polygon"}


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


def compile_package(folder: Path, slug: str, ministry: str = "pathfinders") -> dict[str, str]:
    """Return {file name: content} of the engine template. Raises CompileError on any gap."""
    spec = json.loads((folder / "plantilla.json").read_text(encoding="utf-8"))
    translations = json.loads((folder / "traducciones.json").read_text(encoding="utf-8"))
    sample = json.loads((folder / "datos-ejemplo.json").read_text(encoding="utf-8"))
    page = spec["page"]
    width_pt = float(page["width_inches"]) * PT_PER_INCH
    height_pt = float(page["height_inches"]) * PT_PER_INCH
    scale = width_pt / float(page["width"])
    if abs(height_pt / float(page["height"]) - scale) > 1e-6:
        raise CompileError("La página no conserva la proporción entre unidades y pulgadas.")
    locales = list(spec.get("supported_locales") or translations)
    for locale in locales:
        if locale not in translations:
            raise CompileError(f"traducciones.json no tiene el idioma '{locale}'.")

    supplied = {(f["family"], int(f["weight"])) for f in spec.get("fonts", [])}
    body: list[str] = []
    used_keys: set[str] = set()
    ids: set[str] = set()
    emblem = ROOT / "templates" / "assets" / "emblems" / f"{ministry}.svg"

    def claim(field_id: str) -> str:
        if field_id in ids:
            raise CompileError(f"Id repetido en la plantilla: {field_id}")
        ids.add(field_id)
        return field_id

    for e in spec["elements"]:
        kind = e["type"]
        if kind == "shape":
            if e["shape"] not in SHAPES:
                raise CompileError(f"Forma no soportada: {e['shape']} ({e['id']})")
            body.append(f"<{e['shape']} {_attrs({'id': claim(e['id']), **e['attributes']})}/>")
        elif kind == "image":
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
                    body.append(f"<image {_attrs({'id': claim(e['id']), **box})} href={quoteattr(_data_url(asset))}/>")
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
                    attrs["data-format"] = "date-long"
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
        table = dict(translations[locale])
        missing = sorted(k for k in used_keys if not str(table.get(k) or "").strip())
        if missing:
            raise CompileError(f"Faltan traducciones en '{locale}': {', '.join(missing)}")
        if not church.get(locale):
            raise CompileError(f"datos-ejemplo.json no trae church_name en '{locale}'.")
        table["church_name"] = church[locale]
        strings[locale] = table

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {width_pt:g} {height_pt:g}" width="{page["width_inches"]:g}in" height="{page["height_inches"]:g}in" '
        f'data-template="{slug}" data-source="{escape(spec["id"])}" data-version="{escape(str(spec.get("schema_version", "1")))}">\n'
        f"<!-- Compilada con tools/compile_element_template.py desde el paquete v4 «{escape(spec.get('title', spec['id']))}». "
        f"No editar a mano: regenerar. Unidades del diseño ({page['width']:g} x {page['height']:g}) dentro del grupo escalado. -->\n"
        f'<g transform="scale({scale:g})">\n' + "\n".join(body) + "\n</g>\n</svg>\n"
    )
    meta = {"title": spec.get("title", spec["id"]), "kinds": [engine.HONOR_KIND], "ministries": [ministry],
            "engine": "elements", "source": f"especialidades-v4/{spec['id']}", "locales": locales}
    readme = (
        f"Plantilla compilada del paquete de diseño v4 `{spec['id']}` («{spec.get('title', spec['id'])}»).\n"
        f"Fuente: `~/Documents/DEEL/certificados-diseno/especialidades-v4/{spec['id']}/` (plantilla.json + traducciones.json).\n"
        f"Regenerar: `python tools/compile_element_template.py <carpeta> --slug {slug} --install`.\n"
        f"Contrato y datos: `docs/CERTIFICADOS_V4.md`. No editar template.svg a mano.\n"
    )
    files = {"template.svg": svg, "meta.json": json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "README.md": readme}
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
        (target / name).write_text(content, encoding="utf-8")
    print(f"{args.slug}: {len(files)} archivos en {target}")
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
