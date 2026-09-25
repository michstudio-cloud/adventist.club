"""«Especialidad · Editorial rosa y rojo» (replica-especialidad-editorial): the owner's 2026-09-24 update of
the editorial design, installed over the v4 slug `especialidad-editorial-rojo` so issued certificates keep working."""
import re
import sys
from pathlib import Path

import pytest

from app.certificates.render import (
    TemplateError, balanced_split, check_overrides_fit, fill_svg, fit_lines, load_template, measure_pt,
    render_certificate,
)
from tests.conftest import requires_db

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from compile_element_template import CompileError, compile_package, flow_triggers  # noqa: E402

SLUG = "especialidad-editorial-rojo"
LOCALES = ("es", "en", "pt", "fr")
PACKAGE = Path.home() / "Documents" / "DEEL" / "certificados-diseno" / "replica-especialidad-editorial"
HONOR = {"es": "Campamento I", "en": "Camping Skills I", "pt": "Acampamento I", "fr": "Camping I"}
TITLE = {"es": "ESPECIALIDAD", "en": "HONOR", "pt": "ESPECIALIDADE", "fr": "SPÉCIALITÉ"}


def _data(locale: str) -> dict:
    return {"recipient_name": "Matias Islas", "honor_name": HONOR[locale], "issued_date": "2026-09-21",
            "director_name": "Juan Pérez", "instructor_name": "Ana Ruiz", "certificate_no": "CC-TEST000002",
            "association_name": "Asociación Norte de Tamaulipas"}


def test_installed_with_four_languages_signatures_and_a_flat_background():
    template = load_template(SLUG)
    assert (template.width_pt, template.height_pt) == (792.0, 612.0)
    assert template.meta["title"] == "Especialidad · Editorial rosa y rojo"
    assert template.meta["kinds"] == ["honor"] and template.meta["source"] == "replica-especialidad-editorial"
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


# --- flow_rules and wrap_then_shrink (docs/CERTIFICADOS_V4.md «flow_rules y wrap_then_shrink») ------------
NAME_SIZE, NAME_LINE_HEIGHT = 105, 1.06
# design positions (plantilla.json) of what the name pushes down
AT_REST = {"recipient_line": 678.0, "t_completion": 735.0, "honor_name": 868.0}


def _geometry(svg: str) -> dict:
    """y of the moving elements, and size + lines of the name."""
    line = re.search(r'<line[^>]*id="recipient_line"[^>]*/>', svg).group(0)
    y1, y2 = (float(re.search(rf'{k}="([\d.]+)"', line).group(1)) for k in ("y1", "y2"))
    assert y1 == y2
    found = {"recipient_line": y1}
    for field_id in ("t_completion", "honor_name", "recipient_name"):
        found[field_id] = float(re.search(rf'<text[^>]*id="{field_id}"[^>]*\by="([\d.]+)"', svg).group(1))
    name = re.search(r'<text[^>]*id="recipient_name"[^>]*font-size="([\d.]+)"[^>]*>(.*?)</text>', svg)
    found["name_size"] = float(name.group(1))
    found["name_lines"] = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", name.group(2))
    return found


def test_a_one_line_name_moves_nothing():
    geometry = _geometry(fill_svg(load_template(SLUG), _data("es"), {}, "es"))
    assert geometry["name_lines"] == ["Matias Islas"] and geometry["name_size"] == NAME_SIZE
    assert geometry["recipient_name"] == 617
    for field_id, y in AT_REST.items():
        assert geometry[field_id] == y, field_id


@pytest.mark.parametrize("locale", LOCALES)
def test_a_two_line_name_is_balanced_and_pushes_the_rule_phrase_and_honor(locale):
    """The sample of the design: «María Fernanda / López Hernández» at 105 (wrap before shrinking),
    and the rule, the completion phrase and the honor name exactly one line (105 × 1.06) lower."""
    data = {**_data(locale), "recipient_name": "María Fernanda López Hernández"}
    svg = fill_svg(load_template(SLUG), data, {}, locale)
    geometry = _geometry(svg)
    assert geometry["name_lines"] == ["María Fernanda", "López Hernández"]
    assert geometry["name_size"] == NAME_SIZE and geometry["recipient_name"] == 617
    assert re.search(rf'<tspan x="123" dy="{NAME_SIZE * NAME_LINE_HEIGHT:g}">López Hernández</tspan>', svg)
    for field_id, y in AT_REST.items():
        assert geometry[field_id] == pytest.approx(y + NAME_SIZE * NAME_LINE_HEIGHT), field_id
    # the QR, the date and the signatures stay where they were
    assert re.search(r'<image[^>]*id="qr"[^>]*y="933"', svg) and re.search(r'<text[^>]*id="issued_date"[^>]*y="897"', svg)
    assert re.search(r'<image[^>]*id="signature_director"[^>]*y="1039"', svg)


def test_a_longer_name_shrinks_only_after_two_balanced_lines_fail():
    name = "María Fernanda de los Ángeles López Hernández Villarreal"
    geometry = _geometry(fill_svg(load_template(SLUG), {**_data("es"), "recipient_name": name}, {}, "es"))
    size, lines = geometry["name_size"], geometry["name_lines"]
    assert 64 <= size < NAME_SIZE and len(lines) == 2 and " ".join(lines) == name
    assert all(measure_pt(line, "Poppins", "300", size) <= 1120 for line in lines)
    # one size up, no cut fits: this is the largest size with two lines
    assert balanced_split(name, 1120, lambda text: measure_pt(text, "Poppins", "300", size + 1)) is None
    for field_id, y in AT_REST.items():
        assert geometry[field_id] == pytest.approx(y + size * NAME_LINE_HEIGHT), field_id


def test_balanced_cut_differs_from_greedy_wrapping():
    """Greedy wrapping fills the first line («José Luis Martínez / Gómez»); the balanced strategy of
    the design cuts where both lines are closest in width («José Luis / Martínez Gómez»)."""
    options = dict(family="Poppins", weight="300", size=105, min_size=64, max_width=1120, max_lines=2,
                   field="recipient_name")
    name = "José Luis Martínez Gómez"
    assert fit_lines(name, **options, balanced=True) == (105, ["José Luis", "Martínez Gómez"])
    assert fit_lines(name, **options) == (105, ["José Luis Martínez", "Gómez"])
    assert fit_lines("María Fernanda López Hernández", **options, balanced=True) == \
        (105, ["María Fernanda", "López Hernández"])


def test_an_impossible_name_is_an_error_never_truncated():
    with pytest.raises(TemplateError, match="recipient_name"):
        fill_svg(load_template(SLUG), {**_data("es"), "recipient_name": "Wolfeschlegelsteinhausenbergerdorff " * 4},
                 {}, "es")
    with pytest.raises(TemplateError, match="recipient_name"):     # one word longer than the box
        fill_svg(load_template(SLUG), {**_data("es"), "recipient_name": "W" * 40}, {}, "es")


def test_uppercase_texts_print_in_capitals_whatever_they_receive():
    template = load_template(SLUG)
    svg = fill_svg(template, _data("pt"), {}, "pt")
    texts = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg)
    assert "ASOCIACIÓN NORTE DE TAMAULIPAS" in texts                                  # data
    assert template.strings["pt"]["awarded"].upper() in texts                        # translation
    reworded = fill_svg(template, _data("es"), {}, "es", overrides={"awarded": "Con gratitud reconocemos a"})
    assert "CON GRATITUD RECONOCEMOS A" in re.findall(r"<tspan[^>]*>([^<]*)</tspan>", reworded)
    check_overrides_fit(template, {"awarded": "Con gratitud reconocemos a"})


def test_the_rule_the_name_pushes_is_live_not_in_the_flat_background():
    svg = load_template(SLUG).svg
    assert re.search(r'<line id="recipient_line"[^>]*data-flow-trigger="recipient_name"', svg)
    for field_id in ("t_completion", "honor_name"):
        assert re.search(rf'<text id="{field_id}"[^>]*data-flow-trigger="recipient_name"', svg)
    assert svg.count("data-flow-trigger") == 3 and 'data-wrap="balanced"' in svg


def test_flow_rules_the_compiler_cannot_honour_are_refused():
    text = {"id": "name", "type": "text", "source": {"kind": "data", "key": "recipient_name"}}
    shape = {"id": "rule", "type": "shape", "shape": "line", "attributes": {}}
    spec = lambda **rule: {"elements": [text, shape], "flow_rules": [{"trigger": "name", "shift_elements": ["rule"],  # noqa: E731
                                                                      "offset": "extra_line_height", **rule}]}
    assert flow_triggers(spec()) == {"rule": "recipient_name"}
    for bad in ({"offset": "fixed"}, {"trigger": "rule"}, {"shift_elements": ["nope"]}, {"shift_elements": ["name"]}):
        with pytest.raises(CompileError, match="flow_rules"):
            flow_triggers(spec(**bad))


@requires_db
@pytest.mark.asyncio
async def test_api_answers_422_for_a_name_that_does_not_fit(client, monkeypatch):
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    body = {"template": SLUG, "locale": "es", "format": "svg", "data": _data("es")}
    fine = await client.post("/api/v1/certificates/render", json={
        **body, "data": {**_data("es"), "recipient_name": "María Fernanda López Hernández"}})
    assert fine.status_code == 200 and "López Hernández</tspan>" in fine.text
    overflow = await client.post("/api/v1/certificates/render", json={
        **body, "data": {**_data("es"), "recipient_name": "Wolfeschlegelsteinhausen " * 12}})
    assert overflow.status_code == 422 and "recipient_name" in overflow.json()["detail"]
