"""The five award templates made only for the Adventurer Club (aventureros-*): docs/CERTIFICADOS_V4.md
«Reconocimientos de Aventureros», built by tools/build_adventurer_templates.py."""
import re
import struct
import sys
from pathlib import Path

import pytest

from app.certificates.render import (
    EMBLEMS_DIR, TemplateError, default_emblem, fill_svg, load_template, qr_data_url, render_certificate,
)
from app.routers import template_thumbnails as thumbs

SLUGS = ("aventureros-arcoiris", "aventureros-paisaje", "aventureros-estrellas", "aventureros-jardin",
         "aventureros-geometrico")
LOCALES = ("es", "en", "pt", "fr")
SCALE = 0.72          # design units (1100 x 850) -> points (792 x 612)
# The slots the web wizard and the issuance record fill (lib/certificate-templates.ts, services/certificates.render_data)
SLOTS = {"emblem", "recipient_name", "honor_name", "honor_patch", "issued_date", "director_name", "instructor_name",
         "association_name", "church_name", "certificate_no", "qr", "signature_director", "signature_instructor"}
LONG_NAME = "María Fernanda de los Ángeles Rodríguez"
LONG_HONOR = {"es": "Pioneros adventistas de la iglesia primitiva", "en": "Early Adventist Pioneers of the Primitive Church",
              "pt": "Pioneiros adventistas da igreja primitiva", "fr": "Pionniers adventistes de l'Église primitive"}
QR = qr_data_url("https://adventist.club/verify/AV-TEST000001")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_adventurer_templates as builder  # noqa: E402


def _texts(svg: str) -> list[str]:
    lines = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)
    return [line.replace("&amp;", "&").replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">") for line in lines]


def _data(locale: str, long: bool = True) -> dict:
    return {"recipient_name": LONG_NAME if long else "Sofía Ruiz", "honor_name": LONG_HONOR[locale] if long else "Nudos",
            "issued_date": "2026-11-21", "director_name": "Ana Lucía Hernández Villalobos",
            "instructor_name": "Luis Alberto Gómez Castañeda", "certificate_no": "AV-TEST000001",
            "association_name": "Asociación Central Sudamericana del Norte de Chiapas"}


@pytest.mark.parametrize("slug", SLUGS)
def test_adventurer_only_award_templates(slug):
    template = load_template(slug)
    assert (template.width_pt, template.height_pt) == (792.0, 612.0)            # Letter landscape
    assert template.kinds == ["honor"] and template.ministries == ["adventurers"]
    assert template.serves("adventurers", "honor") and template.listed
    assert not template.serves("pathfinders", "honor") and not template.serves("master-guides", "honor")
    assert not template.serves("adventurers", "program")
    assert set(LOCALES) <= set(template.locales) and template.meta["engine"] == "elements"
    assert template.meta["title"].startswith("Aventureros · ")
    assert SLOTS <= set(template.fields)
    assert [(item.key, item.role) for item in template.editable_strings] == [("awarded", "awarded"),
                                                                            ("completion", "completion")]
    assert template.strings["es"]["awarded"] == "Se otorga a"
    assert "especialidad" not in template.svg.lower() + str(template.strings).lower()   # «reconocimientos», not honors
    assert "<script" not in template.svg and 'href="data:image/png' not in template.svg


@pytest.mark.parametrize("slug", SLUGS)
def test_the_emblem_is_a_prominent_slot(slug):
    """The web offers a non-Pathfinder ministry only templates with an `emblem` slot; here the logo is a
    main element of the page (≥ 1.4 in wide), not the small mark of Retícula verde (0.44 in)."""
    template = load_template(slug)
    emblem = re.search(r'<image id="emblem" x="[\d.]+" y="[\d.]+" width="([\d.]+)" height="([\d.]+)"', template.svg)
    width_in = float(emblem.group(1)) * SCALE / 72
    assert width_in >= 1.4, width_in
    # the official artwork is 581 x 541: the slot keeps its proportions (no distortion, «meet»)
    assert abs(float(emblem.group(1)) / float(emblem.group(2)) - 581.35 / 540.84) < 0.03


def test_the_bundled_adventurer_emblem_has_no_text():
    """The owner's shield (2026-11-21): the whole emblem without the «AVENTUREROS / CLUB» band, so a
    certificate in English, Portuguese or French carries no Spanish text. Sky errata corrected."""
    svg = (EMBLEMS_DIR / "adventurers.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 464.66 433.22"' in svg
    assert not re.search(r"<text|<tspan", svg, re.I)                     # language-neutral
    for colour in ("#66cbe0", "#fff", "#a10055"):                         # sky, cross, outline/family
        assert colour in svg.lower(), colour
    assert "#03eefc" not in svg.lower()                                   # Brand Book errata (neon sky) corrected
    assert not re.search(r"<script|<image|href=|on[a-z]+\s*=", svg, re.I)


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("locale", LOCALES)
def test_long_names_fit_and_every_string_is_translated(slug, locale):
    template = load_template(slug)
    svg = fill_svg(template, _data(locale), {"qr": QR}, locale, "adventurers")
    printed = _texts(svg)
    strings = template.strings[locale]
    for key in set(re.findall(r'data-string="([^"]+)"', template.svg)) - {"qr"}:
        expected = strings[key]
        assert expected in printed or expected.upper() in printed, (slug, locale, key)
    assert strings["church_name"] in printed
    assert LONG_NAME in printed                                         # one line, shrunk, never cut
    honor = re.search(r'<text[^>]*id="honor_name"[^>]*>(.*?)</text>', svg).group(1)
    assert " ".join(_texts(honor)) == LONG_HONOR[locale]                # at most two lines, whole
    assert "AV-TEST000001" in printed and "QR" not in printed
    assert default_emblem("adventurers") in svg and default_emblem("pathfinders") not in svg


@pytest.mark.parametrize("slug", SLUGS)
def test_optional_lines_disappear_and_overflow_is_an_error(slug):
    template = load_template(slug)
    data = {"recipient_name": "Sofía Ruiz", "honor_name": "Nudos", "issued_date": "2026-11-21"}
    svg = fill_svg(template, data, {}, "es", "adventurers")
    for absent in ("association_name", "director_name", "instructor_name", "certificate_no"):
        assert f'id="{absent}"' not in svg, absent
    assert "Iglesia Adventista del Séptimo Día" in _texts(svg)
    with pytest.raises(TemplateError, match="recipient_name"):
        fill_svg(template, {**data, "recipient_name": "María Fernanda de los Ángeles Rodríguez Hernández de la Santísima Trinidad"},
                 {}, "es", "adventurers")


@pytest.mark.parametrize("slug", SLUGS)
def test_renders_a_png_with_the_adventurer_emblem(slug):
    png, mime = render_certificate(slug, _data("es"), {}, locale="es", fmt="png", dpi=36, ministry="adventurers")
    assert mime == "image/png" and struct.unpack(">II", png[16:24]) == (396, 306)


def test_thumbnail_sample_is_an_adventurer_award():
    assert thumbs.sample_ministry(load_template("aventureros-jardin")) == "adventurers"
    assert thumbs.sample_ministry(load_template("especialidad-color")) == "pathfinders"
    assert thumbs.sample_ministry(load_template("especialidad-reticula-verde")) == "pathfinders"
    data = thumbs.sample_data(load_template("aventureros-jardin").fields, "es", "adventurers")
    assert data["honor_name"] == "Promesa del arco iris"
    assert thumbs.sample_data([], "en")["honor_name"] == "Camping Skills I"            # Conquistadores unchanged
    assert thumbs._sample_images("adventurers")["honor_patch"] != thumbs._sample_images()["honor_patch"]


@pytest.mark.asyncio
async def test_listing_and_thumbnails(client, monkeypatch):
    thumbs.forget_all()
    monkeypatch.setattr(thumbs, "fonts_installed", lambda: True)
    calls = []
    real = thumbs.render_certificate

    def counting(slug, data, images, **kwargs):
        calls.append((slug, kwargs.get("ministry"), data["honor_name"]))
        return real(slug, data, images, **kwargs)

    monkeypatch.setattr(thumbs, "render_certificate", counting)
    adventurers = {t["slug"] for t in (await client.get(
        "/api/v1/certificates/templates", params={"ministry": "adventurers", "kind": "honor"})).json()}
    pathfinders = {t["slug"] for t in (await client.get(
        "/api/v1/certificates/templates", params={"ministry": "pathfinders", "kind": "honor"})).json()}
    assert set(SLUGS) <= adventurers and not set(SLUGS) & pathfinders
    for slug in SLUGS:
        answer = await client.get(f"/api/v1/certificates/templates/{slug}/thumbnail.png", params={"w": 288})
        assert answer.status_code == 200 and int.from_bytes(answer.content[16:20], "big") == 288
    assert calls == [(slug, "adventurers", "Promesa del arco iris") for slug in SLUGS]
    thumbs.forget_all()


def test_installed_files_are_what_the_builder_writes(tmp_path):
    """template.svg and strings are output of tools/build_adventurer_templates.py, never edited by hand."""
    assert tuple(builder.DESIGNS) == SLUGS
    for slug in SLUGS:
        built = builder.build(slug, tmp_path)
        installed = load_template(slug).directory
        assert {p.name for p in built.iterdir()} == {p.name for p in installed.iterdir()}
        for path in built.iterdir():
            assert path.read_bytes() == (installed / path.name).read_bytes(), (slug, path.name)
