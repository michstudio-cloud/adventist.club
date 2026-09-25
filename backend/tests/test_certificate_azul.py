"""«Especialidad · Bloques azules» (replica-especialidad-azul): the owner's 2026-09-24 update of the
blue design, installed over the v4 slug `especialidad-modular-azul` so issued certificates keep working."""
import re
import sys
from pathlib import Path

import pytest

from app.certificates.render import fill_svg, load_template, render_certificate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from compile_element_template import compile_package  # noqa: E402

SLUG = "especialidad-modular-azul"
LOCALES = ("es", "en", "pt", "fr")
PACKAGE = Path.home() / "Documents" / "DEEL" / "certificados-diseno" / "replica-especialidad-azul"
HONOR = {"es": "Campamento I", "en": "Camping Skills I", "pt": "Acampamento I", "fr": "Camping I"}
TITLE = {"es": "ESPECIALIDAD", "en": "HONOR", "pt": "ESPECIALIDADE", "fr": "SPÉCIALITÉ"}


def _data(locale: str) -> dict:
    return {"recipient_name": "Matias Islas", "honor_name": HONOR[locale], "issued_date": "2026-09-21",
            "director_name": "Juan Pérez", "instructor_name": "Ana Ruiz", "certificate_no": "CC-TEST000002",
            "association_name": "Asociación Norte de Tamaulipas"}


def test_installed_with_four_languages_signatures_and_a_flat_background():
    template = load_template(SLUG)
    assert (template.width_pt, template.height_pt) == (792.0, 612.0)
    assert template.meta["title"] == "Especialidad · Bloques azules"
    assert template.meta["kinds"] == ["honor"] and template.meta["source"] == "replica-especialidad-azul"
    assert template.listed and template.serves("pathfinders", "honor")
    assert set(LOCALES) <= set(template.locales)
    assert {"honor_patch", "qr", "association_name", "signature_director", "signature_instructor",
            "background"} <= set(template.fields)
    assert (template.directory / "background.webp").stat().st_size < 200_000



@pytest.mark.parametrize("locale", LOCALES)
def test_every_language_prints_its_own_title_and_honor_name(locale):
    svg = fill_svg(load_template(SLUG), _data(locale), {}, locale)
    texts = " ".join(re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg))
    assert TITLE[locale] in texts and HONOR[locale] in texts and "Matias Islas" in texts


def test_renders_png_and_pdf():
    png, mime = render_certificate(SLUG, _data("es"), {}, dpi=72)
    assert mime == "image/png" and png[:4] == b"\x89PNG"
    pdf, mime = render_certificate(SLUG, _data("fr"), {}, fmt="pdf", dpi=72)
    assert mime == "application/pdf" and pdf[:4] == b"%PDF"


@pytest.mark.skipif(not PACKAGE.exists(), reason="the owner's design folder is not on this machine")
def test_installed_files_are_exactly_what_the_compiler_makes():
    files = compile_package(PACKAGE, SLUG)
    installed = load_template(SLUG).directory
    assert set(files) == {path.name for path in installed.iterdir()}
    for name, content in files.items():
        expected = content if isinstance(content, bytes) else content.encode("utf-8")
        assert (installed / name).read_bytes() == expected, name


def test_uppercase_texts_of_the_design_print_in_capitals():
    """`text_transform: uppercase` (association and award phrase), as in the design's muestra.png."""
    svg = fill_svg(load_template(SLUG), _data("en"), {}, "en")
    texts = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)
    assert "ASOCIACIÓN NORTE DE TAMAULIPAS" in texts and "AWARDS THIS CERTIFICATE TO:" in texts
