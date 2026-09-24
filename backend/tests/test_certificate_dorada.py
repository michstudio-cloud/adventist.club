"""«Especialidad dorada» (replica-especialidad): compiled into the engine like the v4 designs,
plus what it adds — a 1600 x 1237 page, Poppins/Lato/Advent Sans, one flattened raster background,
numeric dates and the association from the organisation tree (docs/CERTIFICADOS_V4.md)."""
import re
import struct
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.certificates.render import (
    FONTS_DIR, TemplateError, _font, fill_svg, format_numeric_date, load_template, qr_data_url,
    render_certificate,
)
from tests.conftest import SessionLocal, module_factory, requires_db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from compile_element_template import compile_package  # noqa: E402

SLUG = "especialidad-dorada"
LOCALES = ("es", "en", "pt", "fr")
PACKAGE = Path.home() / "Documents" / "DEEL" / "certificados-diseno" / "replica-especialidad"
HONOR = {"es": "Campamento I", "en": "Camping Skills I", "pt": "Acampamento I", "fr": "Camping I"}
ASSOCIATION = {"es": "Asociación Norte de Tamaulipas", "en": "North Tamaulipas Conference",
               "pt": "Associação Norte de Tamaulipas", "fr": "Fédération du Nord de Tamaulipas"}
DATES = {"es": "21/09/2026", "en": "09/21/2026", "pt": "21/09/2026", "fr": "21/09/2026"}
CHURCH = {"es": "Iglesia Adventista del Séptimo Día", "en": "Seventh-day Adventist Church",
          "pt": "Igreja Adventista do Sétimo Dia", "fr": "Église adventiste du septième jour"}
FIXED = {  # the design's own texts, per language (traducciones.json)
    "es": ["CERTIFICADO DE", "ESPECIALIDAD", "SE OTORGA EL PRESENTE CERTIFICADO A:", "Fecha:", "DIRECTOR", "INSTRUCTOR"],
    "en": ["CERTIFICATE OF", "HONOR", "THIS CERTIFICATE IS AWARDED TO:", "Date:", "DIRECTOR", "INSTRUCTOR"],
    "pt": ["CERTIFICADO DE", "ESPECIALIDADE", "O PRESENTE CERTIFICADO É CONCEDIDO A:", "Data:", "DIRETOR", "INSTRUTOR"],
    "fr": ["CERTIFICAT DE", "SPÉCIALITÉ", "CE CERTIFICAT EST DÉCERNÉ À :", "Date :", "DIRECTEUR", "INSTRUCTEUR"],
}
QR = qr_data_url("https://adventist.club/verify/CC-TEST000001")


def _data(locale: str, **extra) -> dict:
    return {"recipient_name": "Matias Islas", "honor_name": HONOR[locale], "issued_date": "2026-09-21",
            "director_name": "Juan Pérez", "instructor_name": "Ana Ruiz", "certificate_no": "CC-TEST000001",
            "association_name": ASSOCIATION[locale], **extra}


def _texts(svg: str) -> list[str]:
    lines = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)
    return [line.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") for line in lines]


def test_installed_as_a_pathfinder_honor_template_in_four_languages():
    template = load_template(SLUG)
    assert (template.width_pt, template.height_pt) == (792.0, 612.0)
    assert template.meta == {"title": "Especialidad dorada", "kinds": ["honor"], "ministries": ["pathfinders"],
                             "engine": "elements", "source": "replica-especialidad", "locales": list(LOCALES)}
    assert template.serves("pathfinders", "honor") and not template.serves("pathfinders", "program")
    assert set(LOCALES) <= set(template.locales)
    assert {"recipient_name", "honor_name", "issued_date", "certificate_no", "honor_patch", "qr",
            "instructor_name", "director_name", "church_name", "association_name", "background"} <= set(template.fields)
    assert "emblem" not in template.fields              # the church logo is fixed artwork, not the ministry slot
    # 1600 x 1237 is not exactly 11:8.5: fitted and centred like the browser does (meet)
    assert '<g transform="translate(0.2037 0) scale(0.494745)">' in template.svg
    for locale in LOCALES:                               # the sample association never becomes a fixed text
        assert "association" not in template.strings[locale] and "title" not in template.strings[locale]


def test_the_raster_artwork_is_one_light_background():
    """frame, logo and seal were four copies of one 3300 x 2550 PNG (1.7 MB each); now one WebP page
    pasted by Pillow, and template.svg carries no raster at all."""
    template = load_template(SLUG)
    background = template.directory / "background.webp"
    assert re.search(r'<image id="background" x="0" y="0" width="792" height="612" preserveAspectRatio="none" '
                     r'href="background.webp"/>', template.svg)
    assert "data:image/png" not in template.svg and len(template.svg) < 20_000
    assert background.stat().st_size < 400_000
    assert sum(p.stat().st_size for p in template.directory.iterdir()) < 1_500_000
    head = background.read_bytes()[:12]
    assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def test_fonts_of_the_design_are_bundled_with_their_licenses():
    for name in ("Poppins-Black.ttf", "Poppins-Bold.ttf", "Poppins-Light.ttf", "Lato-Regular.ttf",
                 "AdventSans-Beta.otf", "OFL-poppins.txt", "OFL-lato.txt", "OFL-adventsans.txt"):
        assert (FONTS_DIR / name).is_file(), name
    # measuring uses the face resvg draws: the exact weight, OpenType-CFF included
    assert _font("Poppins", "900", 20).getname() == ("Poppins", "Black")
    assert _font("Poppins", "700", 20).getname() == ("Poppins", "Bold")
    assert _font("Poppins", "300", 20).getname() == ("Poppins", "Light")
    assert _font("Advent Sans", "400", 20).getname()[0] == "Advent Sans"
    assert _font("Lato", "400", 20).getname() == ("Lato", "Regular")
    assert _font("Noto Sans", "700", 20).getname() == ("Noto Sans", "Bold")    # older templates: unchanged
    assert _font("Noto Sans", "500", 20).getname() == ("Noto Sans", "Regular")


@pytest.mark.parametrize("locale", LOCALES)
def test_every_text_of_the_design_in_the_certificate_language(locale):
    template = load_template(SLUG)
    svg = fill_svg(template, _data(locale), {"qr": QR}, locale)
    printed = _texts(svg)
    for line in FIXED[locale]:
        assert line in printed, (locale, line)
    assert CHURCH[locale] in printed and ASSOCIATION[locale] in printed
    assert HONOR[locale] in printed and DATES[locale] in printed and "CC-TEST000001" in printed
    assert "Matias Islas" in printed and "Juan Pérez" in printed and "Ana Ruiz" in printed
    assert QR[:60] in svg and "data-placeholder-href" not in svg
    others = {line for loc in LOCALES if loc != locale for line in FIXED[loc]} - set(FIXED[locale])
    assert not others & set(printed)
    assert re.search(r'<text id="church_name"[^>]*font-family="Advent Sans"[^>]*font-weight="400"', svg)
    assert re.search(r'<text id="t_title-1"[^>]*font-family="Poppins"[^>]*font-weight="900"', svg)
    assert re.search(r'<text id="t_date-label"[^>]*font-family="Lato"', svg)

    preview = fill_svg(template, _data(locale), {}, locale)                   # no folio yet: grey box
    assert re.search(r'id="qr"[^>]*href="data:image/svg\+xml', preview)


def test_numeric_dates_as_the_design_writes_them():
    assert [format_numeric_date("2026-09-21", loc) for loc in LOCALES] == [DATES[loc] for loc in LOCALES]
    # the assistant sends the long date of the certificate's language: it is read back
    assert format_numeric_date("21 de septiembre de 2026", "es") == "21/09/2026"
    assert format_numeric_date("September 21, 2026", "en") == "09/21/2026"
    assert format_numeric_date("21 de setembro de 2026", "pt-BR") == "21/09/2026"
    assert format_numeric_date("1 septembre 2026", "fr") == "01/09/2026"
    with pytest.raises(TemplateError):
        format_numeric_date("2026-02-40", "es")
    with pytest.raises(TemplateError):
        format_numeric_date("2026-09-21", "de")
    svg = fill_svg(load_template(SLUG), _data("en", issued_date="September 21, 2026"), {}, "en")
    assert "09/21/2026" in _texts(svg)


def test_no_association_leaves_the_line_out_and_the_honor_patch_keeps_its_shape():
    template = load_template(SLUG)
    data = {k: v for k, v in _data("es").items() if k != "association_name"}
    svg = fill_svg(template, data, {"honor_image": QR}, "es")
    assert 'id="association_name"' not in svg and CHURCH["es"] in _texts(svg)
    assert re.search(r'<image id="honor_patch"[^>]*preserveAspectRatio="xMidYMid meet"[^>]*href="data:image/png', svg)
    with pytest.raises(TemplateError, match="recipient_name"):
        fill_svg(template, _data("es", recipient_name="Wolfeschlegelsteinhausenbergerdorff " * 3), {}, "es")


def test_renders_png_pdf_and_svg_at_letter_size():
    png, mime = render_certificate(SLUG, _data("fr"), {"qr": QR}, locale="fr", dpi=72)
    assert mime == "image/png" and struct.unpack(">II", png[16:24]) == (792, 612)
    pdf, mime = render_certificate(SLUG, _data("fr"), {}, locale="fr", fmt="pdf", dpi=72)
    assert mime == "application/pdf" and re.search(rb"/MediaBox\s*\[\s*0 0 792 612\s*\]", pdf)
    svg, mime = render_certificate(SLUG, _data("fr"), {}, locale="fr", fmt="svg")
    assert mime == "image/svg+xml" and "Fédération du Nord de Tamaulipas".encode() in svg


@pytest.mark.skipif(not PACKAGE.exists(), reason="the owner's design folder is not on this machine")
def test_installed_files_are_exactly_what_the_compiler_makes():
    files = compile_package(PACKAGE, SLUG)
    assert set(files) == {p.name for p in load_template(SLUG).directory.iterdir()}
    for name, content in files.items():
        path = load_template(SLUG).directory / name
        assert (path.read_bytes() if isinstance(content, bytes) else path.read_text(encoding="utf-8")) == content, name


# --- HTTP ---------------------------------------------------------------------
factory = module_factory("certs-dorada")


@requires_db
@pytest.mark.asyncio
async def test_api_lists_and_renders_it(client, monkeypatch):
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    listing = await client.get("/api/v1/certificates/templates", params={"ministry": "pathfinders", "kind": "honor"})
    entry = {t["slug"]: t for t in listing.json()}[SLUG]
    assert entry["title"] == "Especialidad dorada" and set(LOCALES) <= set(entry["locales"])
    assert {"instructor_name", "association_name", "honor_patch"} <= set(entry["fields"])
    assert (entry["width_in"], entry["height_in"]) == (11, 8.5)

    body = {"template": SLUG, "locale": "fr", "dpi": 72, "data": _data("fr")}
    for fmt, mime in (("png", "image/png"), ("pdf", "application/pdf"), ("svg", "image/svg+xml")):
        response = await client.post("/api/v1/certificates/render", json={**body, "format": fmt})
        assert response.status_code == 200 and response.headers["content-type"] == mime, response.text
    printed = _texts((await client.post("/api/v1/certificates/render", json={**body, "format": "svg"})).text)
    assert {"CERTIFICAT DE", "SPÉCIALITÉ", "21/09/2026", CHURCH["fr"], ASSOCIATION["fr"], "Camping I"} <= set(printed)


async def _issue(client, factory, label: str) -> str:
    """A certificate from the open prototype batch; returns its folio."""
    batch = await client.post("/api/v1/certificates/prototype-batch", json={
        "recipient_names": [factory.name(label)], "honor_name": factory.name(f"honor {label}"),
        "club_name": factory.name("club"), "issued_date": "2026-09-21", "instructor_name": "Ana Ruiz",
        "director_name": "Juan Pérez", "width_in": 11, "height_in": 8.5})
    assert batch.status_code == 201, batch.text
    return batch.json()[0]["certificate_no"]


async def _link_to_club(factory, certificate_no: str, club: dict | None, label: str) -> None:
    """Hang the certificate from an enrollment of `club` (what a portfolio issuance does)."""
    member = await factory.user(label)
    async with SessionLocal() as db:
        honor_id = (await db.execute(text("SELECT honor_id FROM certificates WHERE certificate_no = :n"),
                                     {"n": certificate_no})).scalar_one()
        enrollment_id = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO honor_enrollments (id, user_id, honor_id, club_id, status) "
            "VALUES (:id, :user, :honor, :club, 'CERTIFIED')"),
            {"id": enrollment_id, "user": uuid.UUID(member["id"]), "honor": honor_id,
             "club": uuid.UUID(club["id"]) if club else None})
        await db.execute(text("UPDATE certificates SET enrollment_id = :e WHERE certificate_no = :n"),
                         {"e": enrollment_id, "n": certificate_no})
        await db.commit()


@requires_db
@pytest.mark.asyncio
async def test_the_association_comes_from_the_tree_of_the_members_club(client, factory, monkeypatch):
    """With a folio the association is the nearest association above the member's club, whatever
    the caller sends; a club outside any association prints no association line at all."""
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    union = await factory.org("union", "union")
    association = await factory.org("asociacion dorada", "association", union)
    district = await factory.org("distrito", "district", association)
    club = await factory.org("club dorado", "club", district)
    loose_club = await factory.org("club suelto", "club", union)

    folio = await _issue(client, factory, "Ana")
    await _link_to_club(factory, folio, club, "ana")
    response = await client.post("/api/v1/certificates/render", json={
        "template": SLUG, "locale": "pt", "format": "svg", "certificate_no": folio,
        "data": {"association_name": "Associação Inventada", "recipient_name": "Outra Pessoa"}})
    assert response.status_code == 200, response.text
    printed = _texts(response.text)
    assert factory.name("asociacion dorada") in printed and "Associação Inventada" not in printed
    assert factory.name("Ana") in printed and "Outra Pessoa" not in printed
    assert "Ana Ruiz" in printed and "21/09/2026" in printed and folio in printed
    assert CHURCH["pt"] in printed and re.search(r'id="qr"[^>]*href="data:image/png', response.text)

    loose = await _issue(client, factory, "Bea")
    await _link_to_club(factory, loose, loose_club, "bea")
    response = await client.post("/api/v1/certificates/render", json={
        "template": SLUG, "locale": "es", "format": "svg", "certificate_no": loose,
        "data": {"association_name": "Asociación Inventada"}})
    assert response.status_code == 200, response.text
    assert 'id="association_name"' not in response.text and "Asociación Inventada" not in response.text
    assert CHURCH["es"] in _texts(response.text)

    # no enrollment (prototype batch): no member's club, so no association either
    bare = await _issue(client, factory, "Caro")
    response = await client.post("/api/v1/certificates/render", json={
        "template": SLUG, "locale": "en", "format": "svg", "certificate_no": bare})
    assert response.status_code == 200 and 'id="association_name"' not in response.text
    assert factory.name("Caro") in _texts(response.text)
