"""SVG certificate templates: filling, fitting, translations, images, and the HTTP endpoint."""
import base64
import re
import struct
import zlib
from pathlib import Path

import pytest

from app.certificates.render import (
    TEMPLATES_DIR, TemplateError, fill_svg, list_templates, load_template, render_certificate,
)
from tests.conftest import requires_db


def _png(width, height, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


RED_PNG = "data:image/png;base64," + base64.b64encode(_png(8, 8, (220, 30, 30))).decode()


def test_repo_templates_load_and_declare_fields():
    slugs = {t.slug for t in list_templates()}
    assert {"especialidad-basica", "ntam-maestria"} <= slugs
    basic = load_template("especialidad-basica")
    assert (basic.width_pt, basic.height_pt) == (396.0, 306.0)
    assert {"recipient_name", "honor_name", "emblem", "honor_patch", "qr", "certificate_no"} <= set(basic.fields)
    assert "en" in basic.locales and "es" in basic.locales


def test_fill_replaces_fields_translates_and_handles_images():
    template = load_template("especialidad-basica")
    svg = fill_svg(template, {"recipient_name": "María & José <Pérez>", "honor_name": "Nudos",
                              "club_name": "Club Orión", "place": "", "issued_date": "2026-09-21",
                              "certificate_no": "CC-ABC"}, {"honor_patch": RED_PNG}, locale="en")
    assert "María &amp; José &lt;Pérez&gt;" in svg                      # escaped, never injected
    assert "HONOR CERTIFICATE" in svg and "CERTIFICADO DE ESPECIALIDAD" not in svg
    assert "Club Orión  ·  2026-09-21" in svg                            # empty parts of the line are dropped
    assert 'id="honor_patch"' in svg and RED_PNG[:40] in svg
    assert re.search(r'id="emblem"[^>]*href="data:image/svg\+xml', svg)   # official emblem by default
    assert re.search(r'id="qr"[^>]*opacity="0"', svg)                    # missing image hidden, geometry kept
    assert "Especialidad</text>" not in svg


def test_locale_fallback_and_rtl():
    template = load_template("especialidad-basica")
    assert "CERTIFICADO DE ESPECIALIDAD" in fill_svg(template, {}, {}, locale="pt-BR")   # no pt: Spanish source
    assert "HONOR CERTIFICATE" in fill_svg(template, {}, {}, locale="en-US")             # en-US -> en
    arabic = fill_svg(template, {"recipient_name": "أحمد"}, {}, locale="ar")
    assert 'direction="rtl"' in arabic


def test_long_names_shrink_but_never_below_the_minimum():
    template = load_template("especialidad-basica")
    short = fill_svg(template, {"recipient_name": "Ana Ruiz"}, {})
    long = fill_svg(template, {"recipient_name": "María Fernanda de los Ángeles López Hernández Castellanos"}, {})
    size = lambda svg: float(re.search(r'id="recipient_name"[^>]*font-size="([\d.]+)"', svg).group(1))
    assert size(short) == 27.5 and 14 <= size(long) < 27.5


def test_render_png_pdf_and_svg_at_physical_size():
    png, mime = render_certificate("especialidad-basica", {"recipient_name": "Prueba"}, {}, dpi=150)
    assert mime == "image/png" and png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (825, 638)                                 # 5.5 x 4.25 in at 150 dpi
    pdf, mime = render_certificate("especialidad-basica", {}, {}, fmt="pdf", dpi=72)
    assert mime == "application/pdf" and re.search(rb"/MediaBox\s*\[\s*0 0 396 306\s*\]", pdf)
    svg, mime = render_certificate("especialidad-basica", {"honor_name": "Nudos"}, {}, fmt="svg")
    assert mime == "image/svg+xml" and b">Nudos</text>" in svg


def test_ntam_template_uses_the_official_background():
    template = load_template("ntam-maestria")
    assert (template.width_pt, template.height_pt) == (792.0, 612.0)
    assert (template.directory / "background.png").stat().st_size > 100_000
    png, _ = render_certificate("ntam-maestria", {"recipient_name": "Prueba", "honor_name": "Nudos",
                                                  "issued_day": "21", "issued_month": "09", "issued_year": "2026"}, {}, dpi=72)
    assert struct.unpack(">II", png[16:24]) == (792, 612)


def test_template_safety(tmp_path: Path):
    bad = tmp_path / "evil"
    bad.mkdir()
    (bad / "template.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>1</script></svg>')
    with pytest.raises(TemplateError):
        load_template("evil", tmp_path)
    with pytest.raises(TemplateError):
        load_template("../etc", TEMPLATES_DIR)
    with pytest.raises(TemplateError):
        load_template("does-not-exist", TEMPLATES_DIR)


# --- HTTP ---------------------------------------------------------------------
pytestmark_http = requires_db


@pytest.mark.asyncio
async def test_render_endpoint(client, monkeypatch):
    no_fonts = await client.post("/api/v1/certificates/render", json={"template": "especialidad-basica"})
    if no_fonts.status_code == 503:                      # fonts are not bundled in this checkout
        assert "Fuentes" in no_fonts.json()["detail"]
    svg_only = await client.post("/api/v1/certificates/render", json={"template": "especialidad-basica", "format": "svg"})
    assert svg_only.status_code == 200                   # the filled SVG never needs fonts
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)

    listing = await client.get("/api/v1/certificates/templates")
    assert listing.status_code == 200 and any(t["slug"] == "ntam-maestria" for t in listing.json())

    ok = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-basica", "locale": "en", "format": "png", "dpi": 72,
        "data": {"recipient_name": "Prueba", "honor_name": "Knots"}, "images": {"honor_patch": RED_PNG},
        "certificate_no": "CC-TEST00001"})
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert struct.unpack(">II", ok.content[16:24]) == (396, 306)

    assert (await client.post("/api/v1/certificates/render", json={"template": "nope"})).status_code == 404
    bad_img = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-basica", "images": {"qr": "http://evil.example/x.png"}})
    assert bad_img.status_code == 422
    foreign = await client.post("/api/v1/certificates/render", json={           # https but not our bucket
        "template": "especialidad-basica", "images": {"honor_patch": "https://evil.example/x.png"}})
    assert foreign.status_code == 422 and "media.adventist.club" in foreign.json()["detail"]
    local_file = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-basica", "images": {"honor_patch": "file:///etc/passwd"}})
    assert local_file.status_code == 422
    monkeypatch.setattr("app.routers.render._fetch_media_image", lambda url: RED_PNG)
    ours = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-basica", "format": "svg",
        "images": {"honor_patch": "https://media.adventist.club/patches/abc.webp"}})
    assert ours.status_code == 200 and RED_PNG[:40].encode() in ours.content
    too_long = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-basica", "data": {"recipient_name": "x" * 500}})
    assert too_long.status_code == 422


def test_defaults_emblem_placeholders_and_no_sample_text():
    template = load_template("especialidad-basica")
    english = fill_svg(template, {"recipient_name": "Ana"}, {}, locale="en")
    assert "Club Director" in english and "Director(a) del club" not in english     # translated placeholder
    assert "Organización emisora" not in english and "CC-XXXXXXXXXX" not in english  # sample text never ships
    assert re.search(r'id="emblem"[^>]*href="data:image/svg\+xml', english)         # official emblem by default
    spanish = fill_svg(template, {"recipient_name": "Ana"}, {}, locale="es")
    assert "Director(a) del club" in spanish
    other = fill_svg(template, {}, {}, locale="es", ministry="no-such-ministry")
    assert re.search(r'id="emblem"[^>]*opacity="0"', other)


def test_other_physical_sizes():
    png, _ = render_certificate("especialidad-basica", {"recipient_name": "Ana"}, {}, dpi=100, width_in=11)
    assert struct.unpack(">II", png[16:24]) == (1100, 850)               # letter, same proportions
    pdf, _ = render_certificate("especialidad-basica", {}, {}, fmt="pdf", dpi=72, width_in=11)
    assert re.search(rb"/MediaBox\s*\[\s*0 0 792 612\s*\]", pdf)
    half = load_template("especialidad-basica-media")
    assert (half.width_pt, half.height_pt) == (612.0, 396.0)
    png, _ = render_certificate("especialidad-basica-media", {"recipient_name": "Ana"}, {}, dpi=100)
    assert struct.unpack(">II", png[16:24]) == (850, 550)
