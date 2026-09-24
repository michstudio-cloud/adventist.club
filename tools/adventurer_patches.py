"""Adventurer award patches: from the *Adventurer Award Book 2020* (GC Youth Ministries) to a
ready-to-publish image library (SVG + PNG 1024 + WebP 512), its manifest and the upload to R2.

    python tools/adventurer_patches.py extract  --pdf "~/Documents/DEEL/aventureros/Award Book 2020.pdf"
    python tools/adventurer_patches.py manifest
    python tools/adventurer_patches.py verify
    python tools/adventurer_patches.py upload --r2 --dry-run            # or --r2 (needs R2_* env vars)
    python tools/adventurer_patches.py upload --api https://api.adventist.club --token <bearer>

Default output folder: ~/Documents/DEEL/aventureros/libreria (svg/, png/, webp/, extract.json,
manifest.csv, contact-sheet.png). Index of awards: backend/data/adventurer_awards_index.csv.

How the patch is found (no fixed coordinates): every award page draws a full-page raster (brown
wood + the white ribbon) and, on top of it, the patch as vector paths in the top-left corner.
`extract` copies the page, removes all its text and every piece of it outside a generous
top-left search zone (the background raster overlaps the zone's border, so it goes; line art
fully outside it -footer, off-page leftovers- goes too), renders what is left with a transparent
background and takes the box of the painted pixels, keeping the largest connected run (the
patch) and dropping stray bits. That box (which honours the patch's own clipping paths) is the
patch. The SVG is MuPDF's vector output of that cleaned page cropped to the box + 2 % margin,
then optimised; the PNG/WebP are renders of the same box. Two awards ("Artist", "Building
Blocks") are raster JPEGs with a soft mask in the book: their original pixels are exported
(PNG with alpha, native size) and they get no SVG.

Documented in docs/AVENTUREROS_PARCHES.md. Requirements: requirements-tools.txt.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml.ElementTree import fromstring as safe_fromstring

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "backend" / "data"
INDEX_CSV = DATA / "adventurer_awards_index.csv"
LIBRARY_CSV = DATA / "adventurer_awards_library.csv"
MUNDOJA_MAP_CSV = DATA / "adventurer_awards_mundoja_map.csv"
REPO_ASSETS = DATA / "adventurer_awards"
DEFAULT_PDF = Path.home() / "Documents" / "DEEL" / "aventureros" / "Award Book 2020.pdf"
DEFAULT_LIB = Path.home() / "Documents" / "DEEL" / "aventureros" / "libreria"
DEFAULT_MUNDOJA_DIR = Path.home() / "Documents" / "DEEL" / "aventureros" / "parches-mundoja"
FONT = ROOT / "fonts" / "NotoSans-Regular.ttf"

CATEGORY_ES = {
    "Community": "Comunidad",
    "Crafts": "Manualidades",
    "Home": "Hogar",
    "Nature": "Naturaleza",
    "Recreation": "Recreación",
    "Spiritual": "Espiritual",
}
# DIA names. "Early Bird" is «Castorcitos» on guiasmayores.com; the DIA curriculum says
# «Aves Madrugadoras», which is what we use. The book prints "Helping Hands" (plural).
CLASS_ES = {
    "Little Lamb": "Corderitos",
    "Early Bird": "Aves Madrugadoras",
    "Busy Bee": "Abejas Industriosas",
    "Sunbeam": "Rayos de Sol",
    "Builder": "Constructores",
    "Helping Hand": "Manos Ayudadoras",
    "Helping Hands": "Manos Ayudadoras",
    "Multi-level": "Multinivel",
}

# Where the patch lives on an award page (points, page is 612x792). Generous on purpose: the
# patch is found inside it from the rendered pixels, this only keeps the title and footer out.
SEARCH_ZONE = (40.0, 15.0, 265.0, 175.0)
DETECT_ZOOM = 8            # px per pt for the detection render
GAP_PT = 2.0               # painted runs closer than this belong to the same object
SVG_MARGIN = 0.02          # 2 % of the patch's longer side on every side of the viewBox
PNG_WIDTH = 1024
WEBP_WIDTH = 512
WEBP_QUALITY = 85
WEBP_MAX_BYTES = 120 * 1024
ASPECT_TOLERANCE = 0.15
REPO_MAX_BYTES = 8 * 1024 * 1024
R2_PREFIX = "patches/adventurers"
CACHE_CONTROL = "public, max-age=31536000, immutable"
DEFAULT_PUBLIC_URL = "https://media.adventist.club"

LIBRARY_FIELDS = [
    "slug", "name_en", "formerly_en", "category_en", "category_es", "class_en", "class_es",
    "page", "kind", "has_svg", "webp", "png", "svg", "width_px", "height_px", "sha256_webp",
    "webp_bytes", "svg_bytes", "mundoja_file", "webp_url", "notes",
]

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


# ---------------------------------------------------------------------------------------------
# Index and names
# ---------------------------------------------------------------------------------------------
def split_formerly(title: str) -> tuple[str, str]:
    """'Alphabet I (Formerly ABC’s)' -> ('Alphabet I', 'ABC’s')."""
    m = re.match(r"^(.*?)\s*\(\s*Formerly\s+(.*?)\s*\)\s*$", title.strip(), re.I)
    return (m.group(1).strip(), m.group(2).strip()) if m else (title.strip(), "")


def slugify(name: str) -> str:
    """ASCII kebab-case of the English title: 'God’s World' -> 'gods-world', 'Build & Fly' ->
    'build-and-fly'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.replace("&", " and ")
    text = re.sub(r"['’`´]", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def load_index(path: Path = INDEX_CSV) -> list[dict]:
    """Rows of the index with their slug and Spanish labels. Raises on a duplicate slug."""
    rows, seen = [], set()
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            name, formerly = split_formerly(r["award"])
            slug = slugify(name)
            if slug in seen:
                raise ValueError(f"slug duplicado: {slug}")
            seen.add(slug)
            rows.append({
                "slug": slug, "name_en": name, "formerly_en": formerly,
                "category_en": r["categoria"], "category_es": CATEGORY_ES[r["categoria"]],
                "class_en": r["clase"], "class_es": CLASS_ES[r["clase"]],
                "page": int(r["pagina"]), "title": r["award"],
            })
    return rows


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("&", "and")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


# ---------------------------------------------------------------------------------------------
# SVG optimisation (no external tool needed; scour is used on top when it is installed)
# ---------------------------------------------------------------------------------------------
_NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _fmt(value: float, decimals: int = 2) -> str:
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    if text.startswith("0.") and len(text) > 2:
        text = text[1:]
    elif text.startswith("-0.") and len(text) > 3:
        text = "-" + text[2:]
    return text


def _round_numbers(text: str, decimals: int = 2) -> str:
    out = _NUM.sub(lambda m: " " + _fmt(float(m.group()), decimals), text)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r" (?=-)", "", out)                 # "1 -2" -> "1-2"
    return re.sub(r"(?<=[A-Za-z]) | (?=[A-Za-z])", "", out)   # no spaces around commands


def _round_transform(text: str) -> str:
    def matrix(m: re.Match) -> str:
        nums = [float(n) for n in _NUM.findall(m.group(1))]
        if len(nums) != 6:
            return m.group(0)
        return "matrix(" + " ".join([_fmt(n, 4) for n in nums[:4]] + [_fmt(n, 2) for n in nums[4:]]) + ")"
    text = re.sub(r"matrix\(([^)]*)\)", matrix, text)
    return re.sub(r"(translate|scale|rotate)\(([^)]*)\)",
                  lambda m: f"{m.group(1)}(" + " ".join(_fmt(float(n), 4) for n in _NUM.findall(m.group(2))) + ")", text)


def _is_page_rect(d: str) -> bool:
    nums = [float(n) for n in _NUM.findall(d or "")]
    return re.sub(r"[\d.\s-]", "", d or "").upper() in ("MHVHZ", "MHVH") and \
        sorted(set(round(n) for n in nums)) in ([0, 612, 792], [0, 792, 612])


def optimize_svg(svg: str, use_scour: bool = True) -> tuple[str, int]:
    """Clean MuPDF's SVG: drop <image> elements (the SVG is vector only), clip paths that are
    just the whole page, empty/attribute-less groups, unused defs and editor namespaces; round
    coordinates to 2 decimals (transform scales to 4). Returns (svg, images_removed)."""
    root = safe_fromstring(svg)
    q = lambda tag: f"{{{SVG_NS}}}{tag}"  # noqa: E731
    parent = {c: p for p in root.iter() for c in p}

    removed_images = 0
    for el in list(root.iter(q("image"))):
        parent[el].remove(el)
        removed_images += 1

    useless_clips = set()
    for cp in root.iter(q("clipPath")):
        kids = list(cp)
        if len(kids) == 1 and kids[0].tag == q("path") and _is_page_rect(kids[0].get("d")):
            useless_clips.add(cp.get("id"))
    for el in root.iter():
        ref = el.get("clip-path")
        if ref and ref[5:-1] in useless_clips:
            del el.attrib["clip-path"]

    def prune(node: ET.Element) -> None:
        for child in list(node):
            prune(child)
            if child.tag == q("g"):
                if len(child) == 0:
                    node.remove(child)
                elif not child.attrib:
                    idx = list(node).index(child)
                    node.remove(child)
                    for j, grand in enumerate(list(child)):
                        node.insert(idx + j, grand)
    prune(root)

    while True:  # drop defs nobody references (clip paths may reference each other)
        refs = set()
        for el in root.iter():
            for value in el.attrib.values():
                refs.update(re.findall(r"url\(#([^)]+)\)", value))
                if value.startswith("#"):
                    refs.add(value[1:])
        unused = [(defs, d) for defs in root.iter(q("defs")) for d in list(defs)
                  if d.get("id") and d.get("id") not in refs]
        if not unused:
            break
        for defs, d in unused:
            defs.remove(d)
    for defs in list(root.findall(q("defs"))):
        if len(defs) == 0:
            root.remove(defs)

    for el in root.iter():
        for attr in ("d", "points", "viewBox", "width", "height", "x", "y", "stroke-width",
                     "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset"):
            if attr in el.attrib:
                el.set(attr, _round_numbers(el.get(attr)))
        if "transform" in el.attrib:
            el.set("transform", _round_transform(el.get("transform")))
            if el.get("transform") == "matrix(1 0 0 1 0 0)":
                del el.attrib["transform"]
        for attr in [a for a in el.attrib if a.startswith("{http://www.inkscape.org")]:
            del el.attrib[attr]
    root.attrib.pop("version", None)
    out = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    out = re.sub(r">\s+<", "><", out).replace(" />", "/>")

    if use_scour:
        try:
            from scour import scour as _scour
        except ImportError:
            _scour = None
        if _scour is not None:
            opts = _scour.sanitizeOptions(None)
            opts.digits, opts.cdigits = 6, 6
            opts.strip_ids = opts.shorten_ids = True
            opts.remove_metadata = opts.strip_comments = opts.strip_xml_prolog = True
            opts.newlines = opts.strip_xml_space_attribute = False
            opts.indent_type, opts.quiet = "none", True
            out = _scour.scourString(out, opts)
            out = re.sub(r">\s+<", "><", out).strip()
    if not out.startswith("<?xml"):
        out = '<?xml version="1.0" encoding="UTF-8"?>' + out
    return out + "\n", removed_images


# ---------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------
def _runs(flags: list[bool], gap: int) -> list[tuple[int, int]]:
    """Contiguous runs of True (gaps shorter than `gap` are bridged) as [start, end)."""
    runs: list[list[int]] = []
    for i, on in enumerate(flags):
        if not on:
            continue
        if runs and i - runs[-1][1] < gap:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
    return [tuple(r) for r in runs]


def _occupancy(mask: Image.Image) -> tuple[list[bool], list[bool]]:
    w, h = mask.size
    data = mask.tobytes()
    rows = [bool(data[y * w:(y + 1) * w].strip(b"\0")) for y in range(h)]
    t = mask.transpose(Image.Transpose.TRANSPOSE).tobytes()
    cols = [bool(t[x * h:(x + 1) * h].strip(b"\0")) for x in range(w)]
    return cols, rows


def locate_patch(page, fitz) -> tuple[object, list[str]]:
    """Box (points) of the patch on a page already stripped of text and background, from its
    rendered pixels. Returns (rect, warnings)."""
    zone = fitz.Rect(*SEARCH_ZONE)
    pix = page.get_pixmap(matrix=fitz.Matrix(DETECT_ZOOM, DETECT_ZOOM), clip=zone, alpha=True)
    img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    mask = img.getchannel("A").point(lambda v: 255 if v > 8 else 0)
    if not mask.getbbox():
        raise RuntimeError("no se encontró ningún dibujo en la zona del parche")
    warnings: list[str] = []
    gap = int(GAP_PT * DETECT_ZOOM)
    # Largest object: alternate column/row runs until the box is stable (a stray bit beside the
    # patch is its own run in one axis; its run in the other axis then vanishes inside the box).
    box = (0, 0, mask.width, mask.height)
    for _ in range(4):
        sub = mask.crop(box)
        cols, rows = _occupancy(sub)
        cr, rr = _runs(cols, gap), _runs(rows, gap)
        c0, c1 = max(cr, key=lambda r: r[1] - r[0])
        r0, r1 = max(rr, key=lambda r: r[1] - r[0])
        if len(cr) > 1 or len(rr) > 1:
            warnings.append("restos fuera del parche descartados")
        new = (box[0] + c0, box[1] + r0, box[0] + c1, box[1] + r1)
        tight = mask.crop(new).getbbox()
        new = (new[0] + tight[0], new[1] + tight[1], new[0] + tight[2], new[1] + tight[3])
        if new == box:
            break
        box = new
    rect = fitz.Rect(zone.x0 + box[0] / DETECT_ZOOM, zone.y0 + box[1] / DETECT_ZOOM,
                     zone.x0 + box[2] / DETECT_ZOOM, zone.y0 + box[3] / DETECT_ZOOM)
    return rect, sorted(set(warnings))


def _bands(inner, big: float = 5000.0):
    x0, y0, x1, y1 = inner
    return [(-big, -big, big, y0), (-big, y1, big, big), (-big, -big, x0, big), (x1, -big, big, big)]


def isolate_page(src, pno: int, fitz):
    """A one-page copy of page `pno` with no text, no background raster and no line art outside
    the search zone."""
    doc = fitz.open()
    doc.insert_pdf(src, from_page=pno, to_page=pno)
    page = doc[0]
    page.add_redact_annot(page.rect, fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    for band in _bands(SEARCH_ZONE):
        page.add_redact_annot(fitz.Rect(band), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                          graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    big = [i for i in page.get_image_info() if (i["bbox"][2] - i["bbox"][0]) > 300]
    if big:
        raise RuntimeError("el fondo raster sigue en la página")
    return doc, page


def raster_patch(src, page, fitz):
    """The patch image of a raster award ("Artist", "Building Blocks"): an image inside the
    search zone. Returns (RGBA PIL image, xref) or None."""
    zone = fitz.Rect(*SEARCH_ZONE)
    for info in page.get_image_info(xrefs=True):
        bbox = fitz.Rect(info["bbox"])
        if zone.contains(bbox) and bbox.width > 50 and bbox.height > 30:
            xref = info["xref"]
            smask = next((im[1] for im in page.get_images(full=True) if im[0] == xref), 0)
            pix = fitz.Pixmap(src, xref)
            if smask:
                pix = fitz.Pixmap(pix, fitz.Pixmap(src, smask))
            mode = "RGBA" if pix.alpha else "RGB"
            if pix.n - pix.alpha != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
                mode = "RGBA" if pix.alpha else "RGB"
            img = Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGBA")
            return img, xref
    return None


def _trim(img: Image.Image) -> Image.Image:
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else img


def _save_webp(img: Image.Image, path: Path, width: int = WEBP_WIDTH) -> None:
    if img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)
    img.save(path, "WEBP", quality=WEBP_QUALITY, method=4)


def title_on_page(page, fitz) -> str:
    """The award title printed on the ribbon (text right of the patch), for a name check."""
    words = page.get_text("words", clip=fitz.Rect(200, 20, 612, 160))
    return " ".join(w[4] for w in sorted(words, key=lambda w: (round(w[1] / 5), w[0])))


def extract_one(src, row: dict, out: Path, fitz) -> dict:
    pno = row["page"] - 1
    original = src[pno]
    info = {"slug": row["slug"], "page": row["page"], "warnings": [],
            "title_on_page": title_on_page(original, fitz)}
    if info["title_on_page"] and _norm(row["name_en"]) not in _norm(info["title_on_page"]) \
            and _norm(info["title_on_page"]) not in _norm(row["title"]):
        info["warnings"].append(f"título en la página: «{info['title_on_page']}»")

    raster = raster_patch(src, original, fitz)
    if raster is not None:
        img, xref = raster
        img = _trim(img)
        img.save(out / "png" / f"{row['slug']}.png", optimize=True)
        _save_webp(img, out / "webp" / f"{row['slug']}.webp")
        (out / "svg" / f"{row['slug']}.svg").unlink(missing_ok=True)
        info.update(kind="raster", has_svg=False, source_px=[img.width, img.height],
                    notes=f"raster en el libro ({img.width}×{img.height} px, JPEG + máscara): sin SVG; "
                          "PNG/WebP a tamaño nativo")
        if img.width < 256:
            info["notes"] += "; baja resolución, buscar sustituto"
        return info

    doc, page = isolate_page(src, pno, fitz)
    rect, warnings = locate_patch(page, fitz)
    info["warnings"] += warnings
    # Drop whatever is fully outside the patch (stray bits), so the SVG carries only the patch.
    for band in _bands(tuple(rect + (-1, -1, 1, 1))):
        page.add_redact_annot(fitz.Rect(band), fill=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=fitz.PDF_REDACT_TEXT_NONE)

    # PNG: render at 2x the target and downsample (supersampling), transparent, tight.
    zoom = 2 * PNG_WIDTH / rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect + (-1, -1, 1, 1), alpha=True)
    img = _trim(Image.frombytes("RGBA", (pix.width, pix.height), pix.samples))
    img = img.resize((PNG_WIDTH, max(1, round(img.height * PNG_WIDTH / img.width))), Image.LANCZOS)
    img.save(out / "png" / f"{row['slug']}.png", optimize=True)
    _save_webp(img, out / "webp" / f"{row['slug']}.webp")

    margin = SVG_MARGIN * max(rect.width, rect.height)
    view = (rect + (-margin, -margin, margin, margin)) & page.mediabox
    page.set_cropbox(view)
    svg, removed = optimize_svg(page.get_svg_image(text_as_path=True))
    (out / "svg" / f"{row['slug']}.svg").write_text(svg, encoding="utf-8")
    notes = []
    if removed:
        notes.append(f"SVG sin {removed} detalle(s) raster del original (presentes en PNG/WebP)")
    info.update(kind="vector", has_svg=True, rect_pt=[round(v, 2) for v in rect],
                size_pt=[round(rect.width, 2), round(rect.height, 2)], svg_images_removed=removed,
                notes="; ".join(notes))
    doc.close()
    return info


def cmd_extract(args) -> int:
    import pymupdf as fitz

    out = Path(args.lib).expanduser()
    for sub in ("svg", "png", "webp"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    rows = load_index(Path(args.index))
    if args.pages:
        pages = {int(p) for p in args.pages.split(",")}
        rows = [r for r in rows if r["page"] in pages]
    if args.only:
        wanted = set(args.only.split(","))
        rows = [r for r in rows if r["slug"] in wanted]
    src = fitz.open(str(Path(args.pdf).expanduser()))
    report_path = out / "extract.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    failures = 0
    for i, row in enumerate(rows, 1):
        try:
            info = extract_one(src, row, out, fitz)
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            failures += 1
            info = {"slug": row["slug"], "page": row["page"], "error": str(exc)}
        report[row["slug"]] = info
        flag = info.get("error") or "; ".join(info.get("warnings", [])) or ""
        print(f"[{i}/{len(rows)}] p{row['page']:>3} {row['slug']:<32} {info.get('kind', 'ERROR'):<6} {flag}")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"extraídos {len(rows) - failures}/{len(rows)} -> {out}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------------------------
def load_mundoja_map(path: Path = MUNDOJA_MAP_CSV) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["slug"]: r["mundoja_file"] for r in csv.DictReader(fh) if r.get("slug")}


def read_library(path: Path = LIBRARY_CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_library(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LIBRARY_FIELDS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _dir_bytes(files: list[Path]) -> int:
    return sum(f.stat().st_size for f in files if f.exists())


def cmd_manifest(args) -> int:
    lib = Path(args.lib).expanduser()
    report = json.loads((lib / "extract.json").read_text()) if (lib / "extract.json").exists() else {}
    mundoja = load_mundoja_map(Path(args.mundoja_map))
    previous = {}
    if Path(args.manifest).exists():
        previous = {r["slug"]: r for r in read_library(Path(args.manifest))}
    rows = []
    for r in load_index(Path(args.index)):
        slug, info = r["slug"], report.get(r["slug"], {})
        webp, png, svg = lib / "webp" / f"{slug}.webp", lib / "png" / f"{slug}.png", lib / "svg" / f"{slug}.svg"
        has_svg = svg.exists()
        width = height = ""
        sha = ""
        if webp.exists():
            with Image.open(webp) as im:
                width, height = im.size
            sha = hashlib.sha256(webp.read_bytes()).hexdigest()
        rows.append({
            **r, "kind": info.get("kind", ""), "has_svg": "true" if has_svg else "false",
            "webp": f"webp/{slug}.webp" if webp.exists() else "",
            "png": f"png/{slug}.png" if png.exists() else "",
            "svg": f"svg/{slug}.svg" if has_svg else "",
            "width_px": width, "height_px": height, "sha256_webp": sha,
            "webp_bytes": webp.stat().st_size if webp.exists() else "",
            "svg_bytes": svg.stat().st_size if has_svg else "",
            "mundoja_file": mundoja.get(slug, ""),
            # keep an uploaded URL only while the file it points to is unchanged
            "webp_url": previous.get(slug, {}).get("webp_url", "")
            if previous.get(slug, {}).get("sha256_webp") == sha else "",
            "notes": info.get("notes", "") or ("; ".join(info.get("warnings", []))),
        })
    write_library(rows, Path(args.manifest))
    write_library(rows, lib / "manifest.csv")
    print(f"manifiesto: {len(rows)} filas -> {args.manifest} y {lib / 'manifest.csv'}")

    if not args.no_copy:
        dest = Path(args.repo_assets)
        for kind in ("webp", "svg"):
            files = sorted((lib / kind).glob(f"*.{kind}"))
            wanted = {f"{r['slug']}.{kind}" for r in rows}
            files = [f for f in files if f.name in wanted]
            total = _dir_bytes(files)
            target = dest / kind
            if total > REPO_MAX_BYTES:
                print(f"{kind}: {total / 1e6:.1f} MB > 8 MB, NO se copia al repo")
                continue
            target.mkdir(parents=True, exist_ok=True)
            for old in target.glob(f"*.{kind}"):
                if old.name not in {f.name for f in files}:
                    old.unlink()
            for f in files:
                (target / f.name).write_bytes(f.read_bytes())
            print(f"{kind}: {len(files)} archivos, {total / 1e6:.2f} MB -> {target}")
    return 0


# ---------------------------------------------------------------------------------------------
# Verify + contact sheet
# ---------------------------------------------------------------------------------------------
def _font(size: int):
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def _checker(w: int, h: int, step: int = 10) -> Image.Image:
    img = Image.new("RGB", (w, h), (236, 236, 236))
    draw = ImageDraw.Draw(img)
    for y in range(0, h, step):
        for x in range((y // step) % 2 * step, w, step * 2):
            draw.rectangle((x, y, x + step - 1, y + step - 1), fill=(250, 250, 250))
    return img


def contact_sheet(rows: list[dict], lib: Path, dest: Path, flagged: dict[str, str], per_row: int = 10) -> None:
    cell_w, img_h, label_h, pad = 220, 150, 34, 8
    n_rows = (len(rows) + per_row - 1) // per_row
    sheet = Image.new("RGB", (per_row * cell_w, n_rows * (img_h + label_h) + 40), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 10), f"Adventurer awards · {len(rows)} parches · fondo a cuadros = transparencia · "
                       "rojo = revisar", fill=(0, 0, 0), font=_font(16))
    tile_bg = _checker(cell_w - 2 * pad, img_h - pad)
    small = _font(12)
    for i, r in enumerate(rows):
        x, y = (i % per_row) * cell_w, 40 + (i // per_row) * (img_h + label_h)
        sheet.paste(tile_bg, (x + pad, y + pad // 2))
        webp = lib / "webp" / f"{r['slug']}.webp"
        if webp.exists():
            with Image.open(webp) as im:
                im = im.convert("RGBA")
                box = (cell_w - 4 * pad, img_h - 3 * pad)
                scale = min(box[0] / im.width, box[1] / im.height)   # up or down: same size for all
                im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
                sheet.paste(im, (x + (cell_w - im.width) // 2, y + pad // 2 + (img_h - pad - im.height) // 2), im)
        if r["slug"] in flagged:
            draw.rectangle((x + pad, y + pad // 2, x + cell_w - pad - 1, y + img_h - pad // 2 - 1),
                           outline=(220, 0, 0), width=3)
        label = f"{i + 1}. {r['slug']}" + ("" if r.get("has_svg", "true") == "true" else " (raster)")
        while draw.textlength(label, font=small) > cell_w - 8 and len(label) > 6:
            label = label[:-2] + "…"
        draw.text((x + 4, y + img_h), label, fill=(20, 20, 20), font=small)
        draw.text((x + 4, y + img_h + 15), f"p{r['page']} · {r.get('class_en', '')}", fill=(110, 110, 110), font=small)
    sheet.save(dest, optimize=True)


def mundoja_sheet(rows: list[dict], lib: Path, mundoja_dir: Path, dest: Path) -> int:
    pairs = [r for r in rows if r.get("mundoja_file") and (mundoja_dir / r["mundoja_file"]).exists()]
    if not pairs:
        return 0
    cell = 170
    sheet = Image.new("RGB", (cell * 2 * 4, ((len(pairs) + 3) // 4) * (cell + 20)), (255, 255, 255))
    draw, small = ImageDraw.Draw(sheet), _font(11)
    for i, r in enumerate(pairs):
        x, y = (i % 4) * cell * 2, (i // 4) * (cell + 20)
        for j, path in enumerate((lib / "webp" / f"{r['slug']}.webp", mundoja_dir / r["mundoja_file"])):
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((cell - 10, cell - 10))
                sheet.paste(im, (x + j * cell + 5, y + 5), im)
        draw.text((x + 5, y + cell), f"{r['slug']} = {r['mundoja_file']}", fill=(0, 0, 0), font=small)
    sheet.save(dest, optimize=True)
    return len(pairs)


def cmd_verify(args) -> int:
    lib = Path(args.lib).expanduser()
    rows = read_library(Path(args.manifest))
    index = load_index(Path(args.index))
    problems: list[str] = []
    flagged: dict[str, str] = {}
    if len(rows) != len(index):
        problems.append(f"el manifiesto tiene {len(rows)} filas y el índice {len(index)}")
    ratios = {}
    for r in rows:
        slug = r["slug"]
        webp, png, svg = lib / "webp" / f"{slug}.webp", lib / "png" / f"{slug}.png", lib / "svg" / f"{slug}.svg"
        for path in (webp, png) + ((svg,) if r["has_svg"] == "true" else ()):
            if not path.exists():
                problems.append(f"{slug}: falta {path.relative_to(lib)}")
                flagged[slug] = "falta archivo"
        if webp.exists():
            size = webp.stat().st_size
            if size > WEBP_MAX_BYTES:
                problems.append(f"{slug}: WebP de {size / 1024:.0f} KB > 120 KB")
                flagged[slug] = "pesado"
            with Image.open(webp) as im:
                ratios[slug] = im.width / im.height
                if im.mode != "RGBA":
                    problems.append(f"{slug}: WebP sin canal alfa")
                    flagged[slug] = "sin alfa"
        if png.exists():
            with Image.open(png) as im:
                # an inverted triangle: both bottom corners must be see-through
                corners = (0, im.height - 1), (im.width - 1, im.height - 1)
                if im.mode != "RGBA" or any(im.getpixel(c)[3] > 0 for c in corners):
                    problems.append(f"{slug}: PNG sin transparencia en las esquinas inferiores")
                    flagged[slug] = "sin transparencia"
                if r.get("kind") == "vector" and im.width != PNG_WIDTH:
                    problems.append(f"{slug}: PNG de {im.width} px (esperado {PNG_WIDTH})")
        if svg.exists():
            text = svg.read_text(encoding="utf-8")
            if "<image" in text:
                problems.append(f"{slug}: el SVG contiene <image>")
                flagged[slug] = "SVG con raster"
    if ratios:
        values = sorted(ratios.values())
        median = values[len(values) // 2]
        for slug, ratio in ratios.items():
            if abs(ratio / median - 1) > ASPECT_TOLERANCE:
                problems.append(f"{slug}: proporción {ratio:.2f} vs mediana {median:.2f} (±15 %) — sospechoso")
                flagged.setdefault(slug, "proporción")
    contact_sheet(rows, lib, lib / "contact-sheet.png", flagged)
    n = mundoja_sheet(rows, lib, Path(args.mundoja_dir).expanduser(), lib / "mundoja-compare.png")
    totals = {k: _dir_bytes(list((lib / k).glob(f"*.{k}"))) for k in ("svg", "png", "webp")}
    print(f"{len(rows)} parches · svg {totals['svg'] / 1e6:.2f} MB · png {totals['png'] / 1e6:.2f} MB · "
          f"webp {totals['webp'] / 1e6:.2f} MB · mediana ancho/alto {median:.3f}")
    print(f"hoja de contacto -> {lib / 'contact-sheet.png'}" + (f"; cotejo mundoja ({n}) -> {lib / 'mundoja-compare.png'}" if n else ""))
    for p in problems:
        print("  !", p)
    print("OK" if not problems else f"{len(problems)} aviso(s)")
    return 1 if problems else 0


# ---------------------------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------------------------
def r2_client_from_env():
    import boto3
    from botocore.config import Config

    missing = [k for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"faltan variables de entorno: {', '.join(missing)}")
    return boto3.client(
        "s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto", config=Config(signature_version="s3v4"),
    )


def r2_key(slug: str) -> str:
    return f"{R2_PREFIX}/{slug}.webp"


def upload_r2(client, bucket: str, key: str, data: bytes) -> str:
    """PUT unless the same bytes are already there (ETag of a single-part PUT is its MD5)."""
    md5 = hashlib.md5(data).hexdigest()  # noqa: S324 - ETag comparison, not security
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        if head.get("ETag", "").strip('"') == md5:
            return "igual"
    except Exception as exc:  # noqa: BLE001 - 404 means "not there yet"
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code not in ("404", "NoSuchKey", "NotFound"):
            raise
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="image/webp", CacheControl=CACHE_CONTROL)
    return "subido"


def upload_api(api: str, token: str, name: str, data: bytes, folder: str = "patches") -> dict:
    """POST /api/v1/media/upload (multipart: file + folder). The API picks the key
    (`patches/<uuid>.webp`); returns its JSON ({url, key, content_type, size_bytes})."""
    import urllib.request

    boundary = "----adv" + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="folder"\r\n\r\n{folder}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: image/webp\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        api.rstrip("/") + "/api/v1/media/upload", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - URL given by the operator
        return json.load(resp)


def cmd_upload(args, client=None) -> int:
    lib = Path(args.lib).expanduser()
    manifest = Path(args.manifest)
    rows = read_library(manifest)
    public = (os.environ.get("R2_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/")
    todo = [r for r in rows if r["webp"] and (args.force or not r.get("webp_url"))]
    if args.only:
        wanted = set(args.only.split(","))
        todo = [r for r in todo if r["slug"] in wanted]
    total = 0
    if args.r2 and not args.dry_run and client is None:
        client = r2_client_from_env()
    bucket = os.environ.get("R2_BUCKET_NAME", "adventist-media")
    failures = 0
    for i, r in enumerate(todo, 1):
        data = (lib / r["webp"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != r["sha256_webp"]:
            print(f"  ! {r['slug']}: el WebP no coincide con el manifiesto (vuelve a correr `manifest`)")
            failures += 1
            continue
        total += len(data)
        if args.r2:
            key = r2_key(r["slug"])
            if args.dry_run:
                print(f"[dry-run] PUT s3://{bucket}/{key} ({len(data)} B, image/webp, {CACHE_CONTROL}) -> {public}/{key}")
                continue
            state = upload_r2(client, bucket, key, data)
            r["webp_url"] = f"{public}/{key}"
            print(f"[{i}/{len(todo)}] {state:<6} {r['webp_url']}")
        else:
            if args.dry_run:
                print(f"[dry-run] POST {args.api.rstrip('/')}/api/v1/media/upload folder=patches {r['slug']}.webp ({len(data)} B)")
                continue
            try:
                resp = upload_api(args.api, args.token, f"{r['slug']}.webp", data)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  ! {r['slug']}: {exc}")
                continue
            r["webp_url"] = resp["url"]
            print(f"[{i}/{len(todo)}] subido {resp['url']}")
        if i % 20 == 0:
            write_library(rows, manifest)
    if not args.dry_run:
        write_library(rows, manifest)
        if (lib / "manifest.csv").exists():
            write_library(rows, lib / "manifest.csv")
    print(f"{'simulado' if args.dry_run else 'hecho'}: {len(todo)} archivo(s), {total / 1e6:.2f} MB; fallos {failures}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, lib_flag="--lib"):
        sp.add_argument(lib_flag, dest="lib", default=str(DEFAULT_LIB), help="carpeta de la librería")
        sp.add_argument("--index", default=str(INDEX_CSV))

    e = sub.add_parser("extract", help="PDF -> svg/ png/ webp/ + extract.json")
    e.add_argument("--pdf", default=str(DEFAULT_PDF))
    common(e, "--out")
    e.add_argument("--pages", help="solo estas páginas (coma)")
    e.add_argument("--only", help="solo estos slugs (coma)")

    m = sub.add_parser("manifest", help="escribe el CSV de la librería y copia al repo lo ligero")
    common(m)
    m.add_argument("--manifest", default=str(LIBRARY_CSV))
    m.add_argument("--mundoja-map", default=str(MUNDOJA_MAP_CSV))
    m.add_argument("--repo-assets", default=str(REPO_ASSETS))
    m.add_argument("--no-copy", action="store_true", help="no copiar webp/svg al repo")

    v = sub.add_parser("verify", help="comprobaciones + contact-sheet.png")
    common(v)
    v.add_argument("--manifest", default=str(LIBRARY_CSV))
    v.add_argument("--mundoja-dir", default=str(DEFAULT_MUNDOJA_DIR))

    u = sub.add_parser("upload", help="sube los WebP a R2 (clave estable) o por el API")
    common(u)
    u.add_argument("--manifest", default=str(LIBRARY_CSV))
    mode = u.add_mutually_exclusive_group(required=True)
    mode.add_argument("--r2", action="store_true", help=f"boto3 a R2: {R2_PREFIX}/<slug>.webp (R2_* del entorno)")
    mode.add_argument("--api", metavar="URL", help="POST <URL>/api/v1/media/upload (clave aleatoria patches/<uuid>.webp)")
    u.add_argument("--token", help="bearer para --api")
    u.add_argument("--dry-run", action="store_true", help="solo lista lo que haría")
    u.add_argument("--force", action="store_true", help="volver a subir aunque ya tenga URL")
    u.add_argument("--only", help="solo estos slugs (coma)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "upload" and args.api and not args.token and not args.dry_run:
        raise SystemExit("--api necesita --token")
    return {"extract": cmd_extract, "manifest": cmd_manifest, "verify": cmd_verify, "upload": cmd_upload}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
