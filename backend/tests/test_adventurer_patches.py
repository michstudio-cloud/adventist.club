"""Adventurer award patch library (tools/adventurer_patches.py, docs/AVENTUREROS_PARCHES.md):
the index/manifest data, the SVG optimiser, the R2 upload contract and, when the Award Book
PDF is on this machine, a real extraction of three pages."""
import csv
import hashlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import adventurer_patches as ap  # noqa: E402

LIBRARY = list(csv.DictReader(open(ap.LIBRARY_CSV, newline="", encoding="utf-8")))
INDEX = ap.load_index()
# The book's tables of contents list 164 awards: the 155 of the six classes + 9 multi-level.
EXPECTED = 164


def test_index_and_library_cover_every_award_of_the_book_once():
    assert len(INDEX) == EXPECTED
    assert len(LIBRARY) == EXPECTED
    assert sum(r["class_en"] != "Multi-level" for r in INDEX) == 155
    slugs = [r["slug"] for r in LIBRARY]
    assert len(set(slugs)) == len(slugs)
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", s) for s in slugs)
    assert slugs == [r["slug"] for r in INDEX]
    assert len({r["page"] for r in LIBRARY}) == EXPECTED


def test_category_and_class_translations_are_complete():
    assert {r["category_en"] for r in LIBRARY} == set(ap.CATEGORY_ES)
    for r in LIBRARY:
        assert r["category_es"] == ap.CATEGORY_ES[r["category_en"]]
        assert r["class_es"] == ap.CLASS_ES[r["class_en"]]
    assert ap.CLASS_ES["Early Bird"] == "Aves Madrugadoras"
    assert {r["class_es"] for r in LIBRARY} == {
        "Corderitos", "Aves Madrugadoras", "Abejas Industriosas", "Rayos de Sol",
        "Constructor", "Manos Ayudadoras", "Multinivel"}


def test_slugs_drop_the_formerly_note_and_stay_ascii():
    assert ap.split_formerly("Alphabet I (Formerly ABC’s)") == ("Alphabet I", "ABC’s")
    assert ap.slugify("God’s World") == "gods-world"
    assert ap.slugify("Jesus’ Special Supper") == "jesus-special-supper"
    assert ap.slugify("Build & Fly") == "build-and-fly"
    by_slug = {r["slug"]: r for r in LIBRARY}
    assert by_slug["basic-knots"]["formerly_en"] == "Knot Tying"


def test_manifest_rows_point_to_consistent_files():
    for r in LIBRARY:
        assert r["webp"] == f"webp/{r['slug']}.webp" and r["png"] == f"png/{r['slug']}.png"
        assert r["has_svg"] == ("true" if r["kind"] == "vector" else "false")
        assert r["svg"] == (f"svg/{r['slug']}.svg" if r["has_svg"] == "true" else "")
        assert re.fullmatch(r"[0-9a-f]{64}", r["sha256_webp"])
        assert int(r["webp_bytes"]) <= ap.WEBP_MAX_BYTES
    assert {r["slug"] for r in LIBRARY if r["kind"] == "raster"} == {"artist", "building-blocks"}


def test_repo_copies_match_the_manifest():
    webp_dir = ap.REPO_ASSETS / "webp"
    if not webp_dir.exists():
        pytest.skip("WebP no copiados al repo")
    for r in LIBRARY:
        data = (webp_dir / f"{r['slug']}.webp").read_bytes()
        assert hashlib.sha256(data).hexdigest() == r["sha256_webp"]
        if r["has_svg"] == "true":
            svg = (ap.REPO_ASSETS / "svg" / f"{r['slug']}.svg").read_text(encoding="utf-8")
            assert "<image" not in svg and "<svg" in svg


def test_mundoja_map_only_points_to_spiritual_awards_of_the_library():
    rows = list(csv.DictReader(open(ap.MUNDOJA_MAP_CSV, newline="", encoding="utf-8")))
    by_slug = {r["slug"]: r for r in LIBRARY}
    mapped = [r for r in rows if r["slug"]]
    assert len({r["slug"] for r in mapped}) == len(mapped)
    for r in mapped:
        assert by_slug[r["slug"]]["category_en"] == "Spiritual"
        assert by_slug[r["slug"]]["mundoja_file"] == r["mundoja_file"]
    assert sum(bool(r["mundoja_file"]) for r in LIBRARY) == len(mapped)


def test_svg_optimiser_drops_images_page_clips_and_rounds():
    raw = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" version="1.1" width="100.24801" '
           'height="65.9" viewBox="0 0 100.24801 65.9"><defs><clipPath id="c1"><path d="M0 0H612V792H0Z"/>'
           '</clipPath><clipPath id="unused"><path d="M0 0H1V1Z"/></clipPath></defs><g clip-path="url(#c1)">'
           '<image width="4" height="4" xlink:href="data:image/png;base64,AAAA"/>'
           '<path transform="matrix(1,0,0,-1,18.432001,.013977051)" d="M0 0 64.7743-.0714C-.003-.004-.002-.003Z" '
           'fill="#27bba6"/></g><g></g></svg>')
    svg, removed = ap.optimize_svg(raw, use_scour=False)
    assert removed == 1
    assert "<image" not in svg and "clip-path" not in svg and "unused" not in svg
    assert "inkscape" not in svg
    assert 'd="M0 0 64.77-.07C0 0 0 0Z"' in svg
    assert "matrix(1 0 0 -1 18.43 .01)" in svg


class _FakeS3:
    def __init__(self):
        self.put, self.objects = [], {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise type("E", (Exception,), {"response": {"Error": {"Code": "404"}}})()
        return {"ETag": f'"{hashlib.md5(self.objects[Key]).hexdigest()}"'}

    def put_object(self, **kw):
        self.put.append(kw)
        self.objects[kw["Key"]] = kw["Body"]


def test_r2_upload_uses_stable_keys_immutable_cache_and_records_urls(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    (lib / "webp").mkdir(parents=True)
    rows = []
    for slug in ("acts-of-kindness", "bible-i"):
        buf = tmp_path / f"{slug}.webp"
        Image.new("RGBA", (8, 6), (255, 0, 0, 128)).save(buf, "WEBP")
        (lib / "webp" / f"{slug}.webp").write_bytes(buf.read_bytes())
        rows.append({"slug": slug, "webp": f"webp/{slug}.webp",
                     "sha256_webp": hashlib.sha256(buf.read_bytes()).hexdigest(), "webp_url": ""})
    manifest = tmp_path / "library.csv"
    ap.write_library(rows, manifest)
    monkeypatch.setenv("R2_BUCKET_NAME", "fake-bucket")
    monkeypatch.setenv("R2_PUBLIC_URL", "https://media.example")
    args = SimpleNamespace(lib=str(lib), manifest=str(manifest), r2=True, api=None, token=None,
                           dry_run=False, force=False, only=None)
    s3 = _FakeS3()
    assert ap.cmd_upload(args, client=s3) == 0
    assert [p["Key"] for p in s3.put] == ["patches/adventurers/acts-of-kindness.webp",
                                          "patches/adventurers/bible-i.webp"]
    assert all(p["CacheControl"] == "public, max-age=31536000, immutable" and p["ContentType"] == "image/webp"
               and p["Bucket"] == "fake-bucket" for p in s3.put)
    urls = [r["webp_url"] for r in ap.read_library(manifest)]
    assert urls == ["https://media.example/patches/adventurers/acts-of-kindness.webp",
                    "https://media.example/patches/adventurers/bible-i.webp"]
    # Re-running with --force does not PUT identical bytes again.
    args.force = True
    assert ap.cmd_upload(args, client=s3) == 0
    assert len(s3.put) == 2


PDF = ap.DEFAULT_PDF


@pytest.mark.skipif(not PDF.exists(), reason="Award Book 2020 PDF not on this machine")
def test_extract_three_pages_gives_vector_svg_and_transparent_png(tmp_path):
    pytest.importorskip("pymupdf")
    args = SimpleNamespace(pdf=str(PDF), lib=str(tmp_path), index=str(ap.INDEX_CSV),
                           pages="15,39,187", only=None)
    assert ap.cmd_extract(args) == 0
    for slug in ("community-helpers", "animal-homes"):
        svg = (tmp_path / "svg" / f"{slug}.svg").read_text(encoding="utf-8")
        assert "<image" not in svg and "viewBox" in svg
        assert len(svg) < 60_000
    assert not (tmp_path / "svg" / "artist.svg").exists()          # raster in the book: no SVG
    for slug in ("community-helpers", "animal-homes", "artist"):
        with Image.open(tmp_path / "png" / f"{slug}.png") as im:
            assert im.mode == "RGBA"
            assert im.getpixel((0, im.height - 1))[3] == 0            # see-through corner
            assert im.getchannel("A").getextrema() == (0, 255)
        with Image.open(tmp_path / "webp" / f"{slug}.webp") as im:
            assert im.mode == "RGBA" and im.width <= ap.WEBP_WIDTH
    with Image.open(tmp_path / "png" / "community-helpers.png") as im:
        assert im.width == ap.PNG_WIDTH and 1.3 < im.width / im.height < 1.5
