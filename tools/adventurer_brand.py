"""Adventurer Club logos from the *Adventurer Club Brand Book* (GC Youth Ministries, 2016): the
eight variants of page 6 and the two «world» logos of page 7, as SVG (vector, optimised like the
award patches), PNG 1024 px and WebP 512 px with a transparent background.

    python tools/adventurer_brand.py [--pdf ~/Documents/DEEL/aventureros/brand-book-2016.pdf]
                                     [--out ~/Documents/DEEL/aventureros/marca]

How: the page is copied, its text removed, and everything that TOUCHES the area outside the logo's
box is redacted (so the page decoration — the maroon curve of page 7 — goes, while every path of
the logo, fully inside the box, stays). The boxes below are the unions of the drawings of each
logo (clusters of the page's vector paths, measured with pymupdf, 2 pt of margin). The SVG is
MuPDF's output of that cleaned page cropped to the box, optimised by `adventurer_patches.optimize_svg`.

Palette and rules: docs/AVENTUREROS_MARCA.md. Requirements: requirements-tools.txt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adventurer_patches import _bands, _save_webp, _trim, optimize_svg  # noqa: E402

DEFAULT_PDF = Path.home() / "Documents" / "DEEL" / "aventureros" / "brand-book-2016.pdf"
DEFAULT_OUT = Path.home() / "Documents" / "DEEL" / "aventureros" / "marca"
PNG_WIDTH = 1024
MARGIN = 2.0  # pt

# slug -> (page, box in points (x0, y0, x1, y1), label printed under it in the book)
LOGOS = {
    "aventureros-color": (6, (381, 34, 521, 165), "Full Color"),
    "aventureros-grises": (6, (549, 34, 689, 165), "Grayscale"),
    "aventureros-tinta-negra": (6, (293, 197, 433, 328), "One Tint / Black"),
    "aventureros-tinta-negativo": (6, (467, 197, 607, 328), "One Tint / Negative"),
    "aventureros-tinta": (6, (640, 197, 780, 328), "One Tint"),
    "aventureros-contorno-negro": (6, (293, 393, 433, 524), "Outline / Black"),
    "aventureros-contorno-negativo": (6, (443, 368, 631, 548), "Outline / Negative (on blue)"),
    "aventureros-contorno-color": (6, (640, 393, 780, 524), "Outline / Color"),
    "aventureros-mundial-color": (7, (45, 229, 383, 463), "Adventurer Club World Logo · Full Color"),
    "aventureros-mundial-una-tinta": (7, (472, 230, 809, 465), "Adventurer Club World Logo · One Color"),
}


def extract(pdf: Path, out: Path) -> list[dict]:
    import pymupdf as fitz

    src = fitz.open(pdf)
    for sub in ("svg", "png", "webp"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    manifest = []
    for slug, (pno, box, label) in LOGOS.items():
        doc = fitz.open()
        doc.insert_pdf(src, from_page=pno - 1, to_page=pno - 1)
        page = doc[0]
        rect = fitz.Rect(box) + (-MARGIN, -MARGIN, MARGIN, MARGIN)
        page.add_redact_annot(page.rect, fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        for band in _bands(tuple(rect)):
            page.add_redact_annot(fitz.Rect(band), fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,
                              graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        zoom = 2 * PNG_WIDTH / rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=True)
        img = _trim(Image.frombytes("RGBA", (pix.width, pix.height), pix.samples))
        img = img.resize((PNG_WIDTH, max(1, round(img.height * PNG_WIDTH / img.width))), Image.LANCZOS)
        img.save(out / "png" / f"{slug}.png", optimize=True)
        _save_webp(img, out / "webp" / f"{slug}.webp")
        page.set_cropbox(rect & page.mediabox)
        svg, removed = optimize_svg(page.get_svg_image(text_as_path=True))
        (out / "svg" / f"{slug}.svg").write_text(svg, encoding="utf-8")
        manifest.append({"slug": slug, "page": pno, "label": label, "box_pt": list(box),
                         "png_px": list(img.size), "svg_bytes": len(svg.encode()),
                         "svg_images_removed": removed})
        doc.close()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    for row in extract(Path(args.pdf).expanduser(), Path(args.out).expanduser()):
        print(f"{row['slug']}: p{row['page']} {row['label']} → PNG {row['png_px']}, SVG {row['svg_bytes']} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
