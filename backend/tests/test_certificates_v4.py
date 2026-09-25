"""Element templates compiled from the v4 honor-certificate designs (docs/CERTIFICADOS_V4.md)."""
import re
import struct
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.certificates.render import (
    TemplateError, fill_svg, fit_lines, format_long_date, load_template, qr_data_url, render_certificate,
)
from tests.conftest import SessionLocal, module_factory, requires_db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from compile_element_template import compile_package  # noqa: E402

# «modular-azul» se recompila desde replica-especialidad-azul (2026-09-24): ver test_certificate_azul.py.
V4 = {
    "especialidad-reticula-verde": "03-reticula-verde",
    "especialidad-academico": "04-academico",
}
LOCALES = ("es", "en", "pt", "fr")
DESIGNS = Path.home() / "Documents" / "DEEL" / "certificados-diseno" / "especialidades-v4"
HONOR = {"es": "Campamento I", "en": "Camping Skills I", "pt": "Acampamento I", "fr": "Camping I"}
DATES = {"es": "21 de septiembre de 2026", "en": "September 21, 2026", "pt": "21 de setembro de 2026",
         "fr": "21 septembre 2026"}
QR = qr_data_url("https://adventist.club/verify/CC-TEST000001")


def _data(locale: str, **extra) -> dict:
    return {"recipient_name": "María Fernanda López Hernández", "honor_name": HONOR[locale],
            "issued_date": "2026-09-21", "director_name": "Juan Pérez", "instructor_name": "Ana Ruiz",
            "certificate_no": "CC-TEST000001", "organization_name": "Asociación Norte de Tamaulipas", **extra}


def _texts(svg: str) -> list[str]:
    """Every rendered line (tspans), unescaped."""
    lines = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)
    return [line.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") for line in lines]


def _field(svg: str, field_id: str) -> tuple[float, list[str]] | None:
    found = re.search(rf'<text[^>]*id="{field_id}"[^>]*font-size="([\d.]+)"[^>]*>(.*?)</text>', svg)
    if not found:
        return None
    return float(found.group(1)), re.findall(r"<tspan[^>]*>([^<]*)</tspan>", found.group(2))


@pytest.mark.parametrize("slug", V4)
def test_installed_as_pathfinder_honor_templates_in_four_languages(slug):
    template = load_template(slug)
    assert (template.width_pt, template.height_pt) == (792.0, 612.0)          # Letter landscape, in points
    # 024: the element designs serve Aventureros too (emblem and brand words follow the ministry).
    assert template.kinds == ["honor"] and template.ministries == ["pathfinders", "adventurers"]
    assert template.serves("pathfinders", "honor") and template.serves("adventurers", "honor")
    assert not template.serves("master-guides", "honor")
    assert set(LOCALES) <= set(template.locales)
    assert {"recipient_name", "honor_name", "issued_date", "certificate_no", "honor_patch", "qr", "emblem"} <= set(template.fields)
    assert template.meta["engine"] == "elements" and template.meta["title"]
    assert "<image" in template.svg and 'href="data:image/png' not in template.svg      # shapes stay vector


@pytest.mark.parametrize("slug", V4)
@pytest.mark.parametrize("locale", LOCALES)
def test_every_translated_string_is_printed_and_the_qr_label_only_on_previews(slug, locale):
    template = load_template(slug)
    strings = template.strings[locale]
    used = set(re.findall(r'data-string="([^"]+)"', template.svg)) - {"qr"}
    svg = fill_svg(template, _data(locale), {"qr": QR}, locale)
    printed = _texts(svg)
    for key in used:
        assert strings[key] in printed, (slug, locale, key)
    assert strings["church_name"] in printed                                   # default church line, translated
    assert HONOR[locale] in printed and DATES[locale] in printed and "CC-TEST000001" in printed
    assert "QR" not in printed and 'id="t_qr_placeholder_label"' not in svg   # a real QR hides the marker
    assert QR[:60] in svg and "data-placeholder-href" not in svg
    other = {loc: template.strings[loc] for loc in LOCALES if loc != locale}
    foreign = {s for table in other.values() for k, s in table.items() if k in used and s not in strings.values()}
    assert not foreign & set(printed), foreign & set(printed)                 # no string of another language

    preview = fill_svg(template, _data(locale), {}, locale)                   # no folio yet: grey box + «QR»
    assert "QR" in _texts(preview) and re.search(r'id="qr"[^>]*href="data:image/svg\+xml', preview)


def test_dates_are_written_in_the_certificate_language():
    assert [format_long_date("2026-09-21", loc) for loc in LOCALES] == [DATES[loc] for loc in LOCALES]
    assert format_long_date("2026-01-01", "en-US") == "January 1, 2026"
    assert format_long_date("21 de septiembre de 2026", "es") == "21 de septiembre de 2026"   # already formatted
    with pytest.raises(TemplateError):
        format_long_date("2026-13-01", "es")


def test_long_names_shrink_then_wrap_and_never_truncate():
    template = load_template("especialidad-reticula-verde")
    long_name = "María Fernanda de los Ángeles López Hernández Villarreal"
    two_lines = "Liderazgo al aire libre y técnicas avanzadas de campamento en montaña"
    svg = fill_svg(template, _data("es", recipient_name=long_name, honor_name=two_lines), {}, "es")
    size, lines = _field(svg, "recipient_name")
    assert 22 <= size < 39 and lines == [long_name]                          # max_lines 1: shrinks
    size, lines = _field(svg, "honor_name")
    assert 20 <= size <= 36 and len(lines) == 2 and " ".join(lines) == two_lines
    assert re.search(r'<tspan x="196" dy="0">', svg) and re.search(rf'dy="{size * 1.25:g}"', svg)

    with pytest.raises(TemplateError, match="recipient_name"):
        fill_svg(template, _data("es", recipient_name="Wolfeschlegelsteinhausenbergerdorff " * 4), {}, "es")
    with pytest.raises(TemplateError, match="honor_name"):
        fit_lines("palabra " * 80, family="Noto Sans", weight="700", size=36, min_size=20, max_width=620,
                  max_lines=2, field="honor_name")


def test_missing_language_data_and_translations_are_errors():
    template = load_template("especialidad-reticula-verde")
    with pytest.raises(TemplateError, match="traducción"):
        fill_svg(template, _data("es"), {}, "de")                              # no silent Spanish
    with pytest.raises(TemplateError, match="recipient_name"):
        fill_svg(template, {k: v for k, v in _data("es").items() if k != "recipient_name"}, {}, "es")
    svg = fill_svg(template, {k: v for k, v in _data("es").items() if k != "director_name"}, {}, "es")
    assert 'id="director_name"' not in svg                                     # optional: nothing is invented


def test_v4_data_keys_are_accepted_as_aliases():
    template = load_template("especialidad-reticula-verde")
    data = {**_data("pt"), "folio": "CC-ALIAS00001"}
    del data["certificate_no"]
    svg = fill_svg(template, data, {"qr_image": QR, "honor_image": QR}, "pt")
    assert "CC-ALIAS00001" in _texts(svg) and re.search(r'id="honor_patch"[^>]*href="data:image/png', svg)


def test_synthetic_bold_reproduces_the_approved_serif_heading():
    """04-académico asks Noto Serif 700 but ships only Noto Serif 400: its previews show the browser's
    synthetic bold, so the compiled field widens the regular outline instead of switching faces."""
    svg = fill_svg(load_template("especialidad-academico"), _data("fr"), {}, "fr")
    heading = re.search(r'<text[^>]*id="t_specialty"[^>]*>', svg).group(0)
    assert 'font-family="Noto Serif"' in heading and 'font-weight="400"' in heading
    assert 'stroke="#81394a"' in heading and 'stroke-width="1.84' in heading    # 59 / 32


@pytest.mark.parametrize("slug", V4)
def test_renders_png_pdf_and_svg_at_letter_size(slug):
    png, mime = render_certificate(slug, _data("en"), {"qr": QR}, locale="en", dpi=72)
    assert mime == "image/png" and struct.unpack(">II", png[16:24]) == (792, 612)
    pdf, mime = render_certificate(slug, _data("fr"), {}, locale="fr", fmt="pdf", dpi=72)
    assert mime == "application/pdf" and re.search(rb"/MediaBox\s*\[\s*0 0 792 612\s*\]", pdf)
    svg, mime = render_certificate(slug, _data("pt"), {}, locale="pt", fmt="svg")
    assert mime == "image/svg+xml" and "Acampamento I".encode() in svg


@pytest.mark.skipif(not DESIGNS.exists(), reason="the owner's design folder is not on this machine")
@pytest.mark.parametrize("slug", V4)
def test_installed_files_are_exactly_what_the_compiler_makes(slug):
    """Nobody edits a compiled template by hand: re-running the compiler changes nothing."""
    files = compile_package(DESIGNS / V4[slug], slug)
    for name, content in files.items():
        assert (load_template(slug).directory / name).read_text(encoding="utf-8") == content, name


# --- HTTP ---------------------------------------------------------------------
factory = module_factory("certs-v4")


@requires_db
@pytest.mark.asyncio
async def test_api_lists_and_renders_the_v4_templates(client, monkeypatch):
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    listing = await client.get("/api/v1/certificates/templates", params={"ministry": "pathfinders", "kind": "honor"})
    by_slug = {t["slug"]: t for t in listing.json()}
    for slug in V4:
        assert set(LOCALES) <= set(by_slug[slug]["locales"]) and by_slug[slug]["title"]
        assert "honor_patch" in by_slug[slug]["fields"]
    assert not set(V4) & {t["slug"] for t in (await client.get(
        "/api/v1/certificates/templates", params={"kind": "program"})).json()}

    body = {"template": "especialidad-reticula-verde", "locale": "fr", "dpi": 72, "data": _data("fr")}
    for fmt, mime in (("png", "image/png"), ("pdf", "application/pdf"), ("svg", "image/svg+xml")):
        response = await client.post("/api/v1/certificates/render", json={**body, "format": fmt})
        assert response.status_code == 200 and response.headers["content-type"] == mime, response.text
    svg = (await client.post("/api/v1/certificates/render", json={**body, "format": "svg"})).text
    assert "EXPLORATEURS" in svg and "21 septembre 2026" in svg and "Église adventiste du septième jour" in svg
    missing = await client.post("/api/v1/certificates/render", json={
        **body, "format": "svg", "data": {"honor_name": "Nudos", "issued_date": "2026-09-21"}})
    assert missing.status_code == 422 and "recipient_name" in missing.json()["detail"]
    overflow = await client.post("/api/v1/certificates/render", json={
        **body, "format": "svg", "data": {**_data("fr"), "recipient_name": "Wolfeschlegelsteinhausen " * 12}})
    assert overflow.status_code == 422 and "recipient_name" in overflow.json()["detail"]


@requires_db
@pytest.mark.asyncio
async def test_an_issued_certificate_prints_its_own_record(client, factory, monkeypatch):
    """With a folio, the record wins: localized honor name, association, names and date; the QR is real."""
    from app.config import settings

    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    association = await factory.org("asociacion", "association")
    code = f"{factory.prefix}-ISS"
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": code, "id": uuid.UUID(association["id"])})
        await db.commit()
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", code)
    honor_name = factory.name("Campamento")
    batch = await client.post("/api/v1/certificates/prototype-batch", json={
        "recipient_names": [factory.name("Ana")], "honor_name": honor_name, "club_name": factory.name("club"),
        "issued_date": "2026-09-21", "instructor_name": "Ana Ruiz", "director_name": "Juan Pérez",
        "width_in": 11, "height_in": 8.5})
    assert batch.status_code == 201, batch.text
    certificate_no = batch.json()[0]["certificate_no"]
    async with SessionLocal() as db:
        honor_id = (await db.execute(text("SELECT id FROM honors WHERE name = :n"), {"n": honor_name})).scalar_one()
        await db.execute(text("INSERT INTO honor_translations (honor_id, locale, name) VALUES (:id, 'en', :name)"),
                         {"id": honor_id, "name": f"{honor_name} (EN)"})
        await db.commit()

    response = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-reticula-verde", "locale": "en", "format": "svg", "certificate_no": certificate_no,
        "data": {"recipient_name": "Someone Else", "honor_name": honor_name, "issued_date": "2026-01-01"}})
    assert response.status_code == 200, response.text
    printed = _texts(response.text)
    joined = " ".join(printed)                                                          # long names may wrap into several tspans
    assert factory.name("Ana") in joined and "Someone Else" not in joined            # the record wins
    assert f"{honor_name} (EN)" in joined and "September 21, 2026" in joined
    assert factory.name("asociacion") in joined and certificate_no in joined
    assert "Juan Pérez" in joined and "Ana Ruiz" in joined and "QR" not in printed
    assert re.search(r'id="qr"[^>]*href="data:image/png', response.text)
    spanish = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-reticula-verde", "locale": "es", "format": "svg", "certificate_no": certificate_no})
    assert spanish.status_code == 200 and honor_name in _texts(spanish.text)         # no translation: Spanish
