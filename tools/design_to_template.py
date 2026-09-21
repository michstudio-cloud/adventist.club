"""Turn a designer's export into a certificate template (templates/certificates/<slug>/).

    python tools/design_to_template.py <design-folder> --slug ntam-especialidad [--install]

The design folder holds (any design tool: Canva, Illustrator, Figma, Affinity…):
  fondo.svg|png|jpg    the art WITHOUT the texts that change (name, honour, date, signers…)   [required]
  muestra.svg|png|jpg  the same art WITH sample texts in place                                [optional]
  campos.json          what each detected box is (written on the first run; edit and re-run)  [optional]

What it does:
  1. rasterises `fondo` once at 300 dpi -> background.png (texts converted to curves are fine);
  2. compares `muestra` with `fondo` and finds the box of every sample text: position, height,
     colour and alignment come from the design itself, nobody types coordinates;
  3. writes salida/<slug>/ : template.svg, strings.es.json, background.png,
     campos-detectados.png (numbered boxes) and vista-previa.png (rendered by the real engine);
  4. --install copies the result to templates/certificates/<slug>/.

Only the background is an image: the live fields stay real text, so names fit, translate and
print sharp at any size. Fonts must be in fonts/ (OFL); tell us the family and it gets bundled.
"""
from __future__ import annotations

import argparse, io, json, shutil, sys
from collections import deque
from pathlib import Path

import resvg_py
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.certificates import render as engine  # noqa: E402

DETECT_DPI, PRINT_DPI = 100, 300
FIELD_IDS = ["recipient_name", "honor_name", "club_name", "place", "issued_date", "issued_day", "issued_month",
             "issued_year", "director_name", "instructor_name", "coordinator_name", "certificate_no", "qr"]
SAMPLES = {"recipient_name": "María Fernanda López Hernández", "honor_name": "Nudos", "club_name": "Club Orión",
           "place": "Reynosa, Tamaulipas", "issued_date": "21 de septiembre de 2026", "issued_day": "21",
           "issued_month": "09", "issued_year": "2026", "director_name": "Nombre del director",
           "instructor_name": "Nombre del instructor", "coordinator_name": "Nombre del coordinador"}


def find(folder: Path, stem: str) -> Path | None:
    return next((folder / f"{stem}.{ext}" for ext in ("svg", "png", "jpg", "jpeg") if (folder / f"{stem}.{ext}").exists()), None)


def rasterise(path: Path, dpi: int, size_in: tuple[float, float] | None = None) -> Image.Image:
    if path.suffix.lower() != ".svg":
        image = Image.open(path).convert("RGBA")
        if size_in:
            image = image.resize((round(size_in[0] * dpi), round(size_in[1] * dpi)), Image.LANCZOS)
        return image
    png = bytes(resvg_py.svg_to_bytes(svg_path=str(path), dpi=dpi, zoom=dpi / 96 if not size_in else None,
                                      width=round(size_in[0] * dpi) if size_in else None,
                                      height=round(size_in[1] * dpi) if size_in else None))
    return Image.open(io.BytesIO(png)).convert("RGBA")


def page_size_in(path: Path, given: str | None) -> tuple[float, float]:
    if given:
        width, height = (float(v) for v in given.lower().split("x"))
        return width, height
    image = rasterise(path, 96)
    width, height = image.size[0] / 96, image.size[1] / 96
    # snap to the usual paper sizes when the export is within 2 %
    for w, h in ((11, 8.5), (8.5, 11), (8.5, 5.5), (5.5, 8.5), (5.5, 4.25), (11.69, 8.27), (8.27, 11.69)):
        if abs(width / w - 1) < 0.02 and abs(height / h - 1) < 0.02:
            return w, h
    ratio = width / height
    for w, h in ((11, 8.5), (8.5, 11), (8.5, 5.5)):
        if abs(ratio / (w / h) - 1) < 0.01:
            return w, h
    return round(width, 3), round(height, 3)


def text_boxes(background: Image.Image, sample: Image.Image) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (px at DETECT_DPI) of what `sample` has and `background` does not, one per text line."""
    diff = ImageChops.difference(background.convert("RGB"), sample.convert("RGB")).convert("L")
    mask = diff.point(lambda v: 255 if v > 40 else 0)
    # letters of one line touch after a wide, short dilation; separate lines stay apart
    merged = mask.filter(ImageFilter.MaxFilter(3)).resize((mask.width // 2, mask.height // 2)).point(lambda v: 255 if v else 0)
    span = max(3, merged.width // 60) | 1
    wide = Image.new("L", merged.size)
    pixels, out = merged.load(), wide.load()
    for y in range(merged.height):
        run = 0
        for x in range(merged.width):
            run = span if pixels[x, y] else run - 1
            if run > 0:
                out[x, y] = 255
    data, seen, boxes = wide.load(), set(), []
    for y in range(wide.height):
        for x in range(wide.width):
            if not data[x, y] or (x, y) in seen:
                continue
            queue, x0, y0, x1, y1 = deque([(x, y)]), x, y, x, y
            seen.add((x, y))
            while queue:
                cx, cy = queue.popleft()
                x0, y0, x1, y1 = min(x0, cx), min(y0, cy), max(x1, cx), max(y1, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < wide.width and 0 <= ny < wide.height and data[nx, ny] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        queue.append((nx, ny))
            tight = mask.crop((x0 * 2, y0 * 2, (x1 + 1) * 2, (y1 + 1) * 2)).getbbox()
            if tight and (tight[2] - tight[0]) * (tight[3] - tight[1]) > 30:
                boxes.append((x0 * 2 + tight[0], y0 * 2 + tight[1], x0 * 2 + tight[2], y0 * 2 + tight[3]))
    return sorted(boxes, key=lambda b: (round(b[1] / 8), b[0]))


def ink_colour(sample: Image.Image, background: Image.Image, box) -> str:
    region, under = sample.crop(box).convert("RGB"), background.crop(box).convert("RGB")
    pairs = zip(list(region.get_flattened_data() if hasattr(region, "get_flattened_data") else region.getdata()),
                list(under.get_flattened_data() if hasattr(under, "get_flattened_data") else under.getdata()))
    changed = [p for p, q in pairs if sum(abs(a - b) for a, b in zip(p, q)) > 200]
    if not changed:
        return "#1f1a10"
    changed.sort(key=sum)
    core = changed[: max(1, len(changed) // 3)]                      # the darkest third: solid ink, no antialias
    return "#%02x%02x%02x" % tuple(round(sum(c[i] for c in core) / len(core)) for i in range(3))


def describe(boxes, sample, background, page_pt) -> list[dict]:
    scale = 72 / DETECT_DPI
    fields = []
    for number, (x0, y0, x1, y1) in enumerate(boxes, 1):
        centre, width_pt = (x0 + x1) / 2 * scale, (x1 - x0) * scale
        centred = abs(centre - page_pt[0] / 2) < page_pt[0] * 0.03
        # A line of mixed text measures about 1 em from the top of its accents to the bottom of its
        # descenders, and the baseline sits ~0.21 em above the bottom. It is a first estimate: compare
        # comparacion.png and fine-tune font_size / baseline in campos.json (digits need a bigger size).
        size = (y1 - y0) * scale
        fields.append({"n": number, "id": "", "x": round(centre if centred else x0 * scale, 1),
                       "baseline": round(y1 * scale - 0.21 * size, 1), "font_size": round(size, 1),
                       "align": "middle" if centred else "start", "max_width": round(max(width_pt * 1.15, 60), 0),
                       "color": ink_colour(sample, background, (x0, y0, x1, y1)),
                       "font": "Noto Serif", "weight": "700"})
    return fields


def write_template(out: Path, slug: str, page_pt, fields: list[dict]) -> None:
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"',
             f'     viewBox="0 0 {page_pt[0]:g} {page_pt[1]:g}" width="{page_pt[0] / 72:g}in" height="{page_pt[1] / 72:g}in"'
             f' data-template="{slug}" data-version="1">',
             '  <!-- Generado por tools/design_to_template.py: el arte es background.png; los campos son texto vivo. -->',
             f'  <image id="background" x="0" y="0" width="{page_pt[0]:g}" height="{page_pt[1]:g}" xlink:href="background.png"'
             ' preserveAspectRatio="none"/>']
    for f in (f for f in fields if f["id"]):
        if f["id"] == "qr":
            side = f.get("size", 60)
            lines.append(f'  <image id="qr" x="{f["x"]:g}" y="{f["baseline"] - side:g}" width="{side:g}" height="{side:g}" xlink:href=""/>')
            continue
        lines.append(f'  <text id="{f["id"]}" x="{f["x"]:g}" y="{f["baseline"]:g}" text-anchor="{f["align"]}"'
                     f' font-family="{f["font"]}" font-weight="{f["weight"]}" font-size="{f["font_size"]:g}" fill="{f["color"]}"'
                     f' data-fit="shrink" data-min-size="{max(5, f["font_size"] / 2):g}" data-max-width="{f["max_width"]:g}"></text>')
    lines.append("</svg>")
    (out / "template.svg").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "strings.es.json").write_text("{}\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--size", help='page size in inches, e.g. "11x8.5" (default: taken from the file)')
    ap.add_argument("--install", action="store_true", help="copy the result into templates/certificates/<slug>/")
    args = ap.parse_args()
    folder, fondo = args.folder.expanduser(), find(args.folder.expanduser(), "fondo")
    if not fondo:
        sys.exit("Falta fondo.svg / fondo.png (el arte SIN los textos que cambian).")
    size_in = page_size_in(fondo, args.size)
    page_pt = (size_in[0] * 72, size_in[1] * 72)
    out = folder / "salida" / args.slug
    out.mkdir(parents=True, exist_ok=True)
    print(f"Página: {size_in[0]:g} x {size_in[1]:g} in · rasterizando el arte a {PRINT_DPI} dpi…")
    art = rasterise(fondo, PRINT_DPI, size_in)
    (art.convert("RGB") if art.getextrema()[3][0] == 255 else art).save(out / "background.png", optimize=True)

    campos, muestra = folder / "campos.json", find(folder, "muestra")
    if campos.exists():
        fields = json.loads(campos.read_text(encoding="utf-8"))
    elif muestra:
        small_art, small_sample = rasterise(fondo, DETECT_DPI, size_in), rasterise(muestra, DETECT_DPI, size_in)
        boxes = text_boxes(small_art, small_sample)
        fields = describe(boxes, small_sample, small_art, page_pt)
        marked, draw = small_sample.convert("RGB"), None
        draw = ImageDraw.Draw(marked)
        for f, box in zip(fields, boxes):
            draw.rectangle(box, outline=(255, 0, 160), width=2)
            draw.text((box[0] + 2, max(0, box[1] - 12)), str(f["n"]), fill=(255, 0, 160))
        marked.save(out / "campos-detectados.png")
        campos.write_text(json.dumps(fields, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{len(fields)} textos detectados -> {campos.name}. Pon en \"id\" qué es cada número"
              f" ({', '.join(FIELD_IDS)}), deja \"\" lo que sea arte fijo, y vuelve a ejecutar.")
    else:
        sys.exit("Falta muestra.svg / muestra.png (el arte CON textos de ejemplo) o un campos.json.")

    write_template(out, args.slug, page_pt, fields)
    named = [f["id"] for f in fields if f["id"]]
    if named:
        template = engine.load_template(args.slug, out.parent)
        engine._load.cache_clear()
        svg = engine.fill_svg(template, {k: v for k, v in SAMPLES.items() if k in named}, {})
        (out / "vista-previa.png").write_bytes(engine.render_png(svg, template, dpi=150))
        print(f"Vista previa: {out / 'vista-previa.png'}")
        if muestra:   # the designer's sample in red under our render in black: misplaced fields show at a glance
            ours = Image.open(out / "vista-previa.png").convert("L")
            theirs = rasterise(muestra, 150, size_in).convert("L").resize(ours.size)
            Image.merge("RGB", (theirs, ours, ours)).save(out / "comparacion.png")
            print(f"Comparación con la muestra: {out / 'comparacion.png'}")
    if args.install:
        if not named:
            sys.exit("Nada que instalar todavía: ningún campo tiene id.")
        target = ROOT / "templates" / "certificates" / args.slug
        target.mkdir(parents=True, exist_ok=True)
        for name in ("template.svg", "strings.es.json", "background.png"):
            shutil.copy2(out / name, target / name)
        print(f"Instalada en {target}")


if __name__ == "__main__":
    main()
