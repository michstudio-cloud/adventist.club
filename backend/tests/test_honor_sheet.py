"""Honor PDFs generated from our own data: the worksheet («hoja», default) and the «ficha»."""
import io
import time
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import text

from app.config import Settings, settings
from app.db import SessionLocal
from app.services import honor_sheet
from app.services.honor_sheet import (
    SheetData,
    SheetRequirement,
    SheetResource,
    answer_line_count,
    category_accent,
    credit_line,
    parse_requirement,
    render_sheet_pdf,
    sub_item_answer,
)
from tests.conftest import module_factory, requires_db

factory = module_factory("hsheet")
HONORS = "/api/v1/honors"

NESTED = "Conocer los siguientes términos:\n  a) Barbotina\n  b) Engobe\n    i. Engobe coloreado\n  c) Bizcocho"


def pdf_text(body: bytes) -> tuple[int, str]:
    reader = PdfReader(io.BytesIO(body))
    return len(reader.pages), " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)


def flat(value: str) -> str:
    return " ".join(value.split())


def no_references(content: str) -> bool:
    """«por ahora no mostremos referencias»: no source, attribution or third-party site."""
    squashed = content.lower().replace(" ", "")
    return not any(ref in squashed for ref in ("guiasmayores", "wiki.pathfindersonline", "pathfinderwiki",
                                                "fuente:", "textodelosrequisitos"))


async def _honor(factory, label: str, *, status: str = "PUBLISHED", active: bool = True,
                 requirements: list[tuple[str, str, bool, str | None]] = (),
                 resources: list[tuple[str, str, str]] = (), created_by: str | None = None,
                 translations: dict[str, str] | None = None, source: str | None = None) -> dict:
    honor_id, name, slug = uuid.uuid4(), factory.name(label), f"{factory.prefix}-{label}"
    async with SessionLocal() as db:
        category = (await db.execute(text(
            "SELECT id FROM honor_categories WHERE slug='arts-crafts-hobbies' LIMIT 1"))).scalar()
        await db.execute(text(
            "INSERT INTO honors (id, category_id, name, slug, active, status, description, authority, skill_level,"
            " year_introduced, source_url, created_by_id) VALUES (:id, :category, :name, :slug, :active, :status,"
            " 'Modelar el barro a mano.', 'GC', 1, 1929, 'https://www.guiasmayores.com/alfareria.html', :creator)"),
            {"id": honor_id, "category": category, "name": name, "slug": slug, "active": active, "status": status,
             "creator": uuid.UUID(created_by) if created_by else None})
        for position, (locale, description, theoretical, instructions) in enumerate(requirements, 1):
            await db.execute(text(
                "INSERT INTO honor_requirements (id, honor_id, position, description, is_theoretical, instructions,"
                " locale, source) VALUES (:id, :honor, :position, :description, :theoretical, :instructions,"
                " :locale, :source)"),
                {"id": uuid.uuid4(), "honor": honor_id, "position": position, "description": description,
                 "theoretical": theoretical, "instructions": instructions, "locale": locale, "source": source})
        for position, (resource_name, url, kind) in enumerate(resources, 1):
            await db.execute(text(
                "INSERT INTO honor_resources (id, honor_id, position, name, url, type)"
                " VALUES (:id, :honor, :position, :name, :url, :type)"),
                {"id": uuid.uuid4(), "honor": honor_id, "position": position, "name": resource_name, "url": url,
                 "type": kind})
        for locale, translated in (translations or {}).items():
            await db.execute(text(
                "INSERT INTO honor_translations (honor_id, locale, name) VALUES (:id, :locale, :name)"),
                {"id": honor_id, "locale": locale, "name": translated})
        await db.commit()
    return {"id": str(honor_id), "name": name, "slug": slug}


# ------------------------------------------------------------------ endpoint


ALFARERIA_REQUIREMENTS = [
    ("es", "Explicar qué es la arcilla y cómo se forma en la naturaleza.", True, None),
    ("es", NESTED, True, None),
    ("es", "Hacer una vasija con la técnica de rollos.", False, "El instructor verificará la pieza."),
]
ALFARERIA_RESOURCES = [
    ("Técnicas de modelado", "https://www.youtube.com/watch?v=demo", "video"),
    ("Guía de seguridad", "https://media.adventist.club/resources/seguridad.pdf", "pdf"),
    ("Requisitos en guiasmayores", "https://www.guiasmayores.com/alfareria.html", "link"),
]


@requires_db
async def test_default_sheet_is_the_worksheet(client, factory):
    honor = await _honor(factory, "alfareria", requirements=ALFARERIA_REQUIREMENTS, resources=ALFARERIA_RESOURCES,
                         source="guiasmayores.com")
    response = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'inline; filename="{honor["slug"]}.pdf"'
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content.startswith(b"%PDF")

    pages, content = pdf_text(response.content)
    assert pages >= 1
    assert flat(honor["name"]) in content
    lowered = content.lower()
    for field in ("nombre", "club", "unidad", "instructor/a", "fecha de inicio", "fecha de finalización"):
        assert field in lowered, field                                       # the fill-in block (small caps)
    assert "Aprobación" in content and "Instructor/a" in content and "Director/a del club" in content
    assert "NOTAS" in content or "Notas" in content
    assert "ESPECIALIDAD · ARTES" in content and "Nivel 1" in content and "Desde 1929" in content
    for piece in ("Explicar qué es la arcilla y cómo se forma en la naturaleza.", "Conocer los siguientes términos:",
                  "Barbotina", "Engobe coloreado", "Bizcocho", "Hacer una vasija con la técnica de rollos.",
                  "El instructor verificará la pieza.", "Evidencia:", "demostración", "Verificado por:"):
        assert piece in content, piece
    # one checkbox per requirement, per sub-item (a, b, i, c) and per evidence kind
    assert content.count("☐") >= 3 + 4 + 3
    assert f"Generado en Adventist.Club · conquistadores.app/honors/{honor['slug']}" in content
    assert "Página 1 de" in content
    # no references: no «Fuente», no guiasmayores resource, no attribution of the rows
    assert no_references(content)
    assert "Recursos" not in content                                          # the worksheet has no resource list

    download = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"download": 1})
    assert download.headers["content-disposition"] == f'attachment; filename="{honor["slug"]}.pdf"'
    assert (await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"modo": "otro"})).status_code == 422


@requires_db
async def test_ficha_mode_keeps_the_compact_sheet_without_guiasmayores(client, factory):
    honor = await _honor(factory, "ficha", requirements=ALFARERIA_REQUIREMENTS, resources=ALFARERIA_RESOURCES,
                         source="guiasmayores.com")
    response = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"modo": "ficha", "download": 1})
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="{honor["slug"]}.pdf"'
    _, content = pdf_text(response.content)
    assert flat(honor["name"]) in content
    for piece in ("Conocer los siguientes términos:", "Barbotina", "Engobe coloreado", "Bizcocho",
                  "Hacer una vasija con la técnica de rollos.", "El instructor verificará la pieza."):
        assert piece in content, piece
    assert "Práctico" in content and "3 requisitos · 1 práctico" in content
    assert "Recursos" in content and "Técnicas de modelado" in content and "Guía de seguridad" in content
    assert "Requisitos en guiasmayores" not in content and no_references(content)
    assert f"conquistadores.app/honors/{honor['slug']}" in content and "Página 1 de" in content
    assert "Nivel 1" in content and "Oficial (Asociación General)" in content and "Desde 1929" in content
    assert "Aprobación" not in content and "☐" not in content


@requires_db
async def test_sheet_without_requirements_says_they_are_in_preparation(client, factory):
    honor = await _honor(factory, "vacia")
    response = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf")
    assert response.status_code == 200
    pages, content = pdf_text(response.content)
    assert pages == 1
    assert "Requisitos en preparación" in content and "nombre" in content.lower()
    assert "NOTAS" in content and "Aprobación" not in content and "☐" not in content
    assert f"www.conquistadores.app/honors/{honor['slug']}" in content
    assert "Recursos" not in content and no_references(content)

    ficha = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"modo": "ficha"})
    _, content = pdf_text(ficha.content)
    assert "Requisitos en preparación" in content and "Recursos" not in content and no_references(content)


@requires_db
async def test_unpublished_sheet_is_404_for_the_public_but_not_for_its_author(client, factory):
    author = await factory.user("autor", "INSTRUCTOR")
    draft = await _honor(factory, "borrador", status="DRAFT", created_by=author["id"],
                         requirements=[("es", "Requisito del borrador.", True, None)])
    assert (await client.get(f"{HONORS}/{draft['id']}/sheet.pdf")).status_code == 404
    stranger = await factory.user("otro", "INSTRUCTOR")
    assert (await client.get(f"{HONORS}/{draft['id']}/sheet.pdf", headers=stranger["headers"])).status_code == 404

    own = await client.get(f"{HONORS}/{draft['id']}/sheet.pdf", headers=author["headers"])
    assert own.status_code == 200 and own.content.startswith(b"%PDF")
    assert own.headers["cache-control"] == "private, no-store"

    inactive = await _honor(factory, "inactiva", active=False)
    assert (await client.get(f"{HONORS}/{inactive['id']}/sheet.pdf")).status_code == 404
    assert (await client.get(f"{HONORS}/{uuid.uuid4()}/sheet.pdf")).status_code == 404


@requires_db
async def test_etag_is_stable_answers_304_and_changes_with_the_content(client, factory):
    honor = await _honor(factory, "etag", requirements=[("es", "Texto original del requisito.", True, None)])
    url = f"{HONORS}/{honor['id']}/sheet.pdf"
    first, second = await client.get(url), await client.get(url)
    etag = first.headers["etag"]
    assert etag == second.headers["etag"] and len(etag) == 66

    cached = await client.get(url, headers={"If-None-Match": etag})
    assert cached.status_code == 304 and cached.content == b"" and cached.headers["etag"] == etag

    assert (await client.get(url, params={"locale": "en"})).headers["etag"] != etag
    assert (await client.get(url, params={"paper": "letter"})).headers["etag"] != etag
    ficha = await client.get(url, params={"modo": "ficha"})
    assert ficha.headers["etag"] not in (etag, None)
    assert (await client.get(url, params={"modo": "hoja"})).headers["etag"] == etag      # hoja is the default
    assert (await client.get(url, params={"modo": "ficha"},
                             headers={"If-None-Match": ficha.headers["etag"]})).status_code == 304
    assert (await client.get(url, headers={"If-None-Match": ficha.headers["etag"]})).status_code == 200

    async with SessionLocal() as db:
        await db.execute(text("UPDATE honor_requirements SET description = 'Texto corregido del requisito.'"
                              " WHERE honor_id = :id"), {"id": uuid.UUID(honor["id"])})
        await db.commit()
    edited = await client.get(url, headers={"If-None-Match": etag})
    assert edited.status_code == 200 and edited.headers["etag"] != etag
    assert "Texto corregido del requisito." in pdf_text(edited.content)[1]


@requires_db
async def test_locale_falls_back_to_english_requirements_and_translates_the_labels(client, factory):
    honor = await _honor(factory, "solo-ingles", requirements=[
        ("en", "Explain what clay is and how it forms.", True, None),
        ("en", "Make a coil pot at least 10 cm tall.", False, None),
    ], translations={"en": factory.name("Pottery")})
    spanish = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf")
    _, content = pdf_text(spanish.content)
    assert flat(honor["name"]) in content                                    # Spanish title
    assert "Explain what clay is and how it forms." in content                # English rows: better than nothing
    assert "solo en inglés" in content and "Requisitos" in content

    english = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"locale": "en-US"})
    _, content = pdf_text(english.content)
    assert flat(factory.name("Pottery")) in content and "Requirements" in content and "Hands-on" in content
    assert "solo en inglés" not in content
    assert (await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"locale": "no valid"})).status_code == 422


# ------------------------------------------------------------------ renderer (no database)


def _data(**overrides) -> SheetData:
    fields = dict(
        honor_id=str(uuid.uuid4()), slug="alfareria", name="Alfarería", language="es",
        category_slug="arts-crafts-hobbies", category_name="Artes y Habilidades Manuales",
        description="Modelar el barro.", image_url=None, source_url=None, difficulty_level=None, skill_level=2,
        honor_type="OFFICIAL_GC", authority=None, year_introduced=1929, code=None, version=3,
        updated_at=datetime(2026, 9, 23, tzinfo=timezone.utc), requirements_locale="es",
        requirements=[SheetRequirement(i, f"Requisito número {i}: {NESTED}", i % 3 != 0,
                                       "Indicación." if i % 4 == 0 else None) for i in range(1, 41)],
        resources=[SheetResource("Manual", "https://media.adventist.club/resources/a.pdf", "pdf")],
    )
    fields.update(overrides)
    return SheetData(**fields)


def test_parse_requirement_splits_inline_sub_items():
    head, items = parse_requirement("Conocer los términos: a) Barbotina b) Engobe; c) Bizcocho")
    assert head == "Conocer los términos:"
    assert [(i.level, i.marker, i.text) for i in items] == [(1, "a)", "Barbotina"), (1, "b)", "Engobe"),
                                                            (1, "c)", "Bizcocho")]
    head, items = parse_requirement("Nombrar: a. el torno b. el horno")
    assert head == "Nombrar:" and [i.marker for i in items] == ["a.", "b."]
    head, items = parse_requirement("Hacer una pieza • con rollos • con placas")
    assert head == "Hacer una pieza" and [(i.marker, i.text) for i in items] == [("•", "con rollos"),
                                                                                 ("•", "con placas")]
    # a lone marker, a run not starting at «a» or mixed punctuation never split the text
    for text_value in ("Repasar el punto b) del requisito anterior.", "Ver a) y luego c) del manual.",
                       "Explicar a) la cocción b. el esmaltado"):
        assert parse_requirement(text_value) == (text_value, [])


def test_answer_lines_follow_the_kind_of_requirement():
    def req(text_value, theoretical=True):
        return SheetRequirement(1, text_value, theoretical, None)
    assert answer_line_count(req("Conocer las causas del choque.")) == 4
    assert answer_line_count(req("Explicar qué es la arcilla.")) == 6
    assert answer_line_count(req("Describir el proceso de cocción.")) == 6
    assert answer_line_count(req("Conocer " + "mucho " * 60)) == 6                  # long text
    assert answer_line_count(req("Explicar la cocción.", theoretical=False)) == 2   # practical: + evidence
    assert answer_line_count(req("Explicar la cocción."), sub_items_answered=True) == 2
    assert sub_item_answer("¿Qué ocurre con la arcilla?", "Responder:") == "lines"
    assert sub_item_answer("Qué ocurre con la arcilla durante la cocción", "Describir el proceso:") == "lines"
    assert sub_item_answer("Barbotina", "Conocer el significado de los siguientes términos:") == "inline"
    assert sub_item_answer("Una vasija con la técnica de pellizco", "Hacer a mano las siguientes piezas:") is None


def test_parse_requirement_nests_sub_items():
    head, items = parse_requirement(NESTED)
    assert head == "Conocer los siguientes términos:"
    assert [(i.level, i.marker, i.text) for i in items] == [
        (1, "a)", "Barbotina"), (1, "b)", "Engobe"), (2, "i.", "Engobe coloreado"), (1, "c)", "Bizcocho")]
    _, items = parse_requirement("Hacer:\n• Uno\nNota. Texto libre")
    assert [(i.level, i.marker) for i in items] == [(1, "•"), (0, None)]


def test_category_palette_is_fixed_for_the_catalogue_and_stable_for_the_rest():
    assert category_accent("nature").name == "green" and category_accent("health-science").name == "red"
    assert category_accent("una-categoria-nueva") == category_accent("una-categoria-nueva")
    assert category_accent(None).name == "blue"


@pytest.mark.parametrize("mode, version", [("hoja", "Versión 3 · 23 sep 2026"),
                                           ("ficha", "Versión 3 · actualizada 23 sep 2026")])
def test_long_sheet_paginates_and_renders_fast(mode, version):
    render_sheet_pdf(_data(requirements=[]), mode=mode, fetch_image=False)   # warm-up: font registration
    started = time.perf_counter()
    body = render_sheet_pdf(_data(), mode=mode, fetch_image=False)
    assert time.perf_counter() - started < 2.5
    pages, content = pdf_text(body)
    assert pages >= 3 and f"Página {pages} de {pages}" in content
    assert version in content
    for number in (1, 20, 40):
        assert f"Requisito número {number}:" in content
    letter = PdfReader(io.BytesIO(render_sheet_pdf(_data(), paper="letter", mode=mode, fetch_image=False)))
    assert [round(float(v)) for v in letter.pages[0].mediabox.upper_right] == [612, 792]


def test_worksheet_has_a_checkbox_per_requirement_and_sub_item():
    body = render_sheet_pdf(_data(), fetch_image=False)
    _, content = pdf_text(body)
    # 40 requirements, each with 4 sub-items (a, b, i, c), and 3 evidence boxes per practical one
    practical = sum(1 for i in range(1, 41) if i % 3 == 0)
    assert content.count("☐") == 40 + 40 * 4 + 3 * practical
    assert content.count("Evidencia:") == practical and "Aprobación" in content


def _sourced(source, url=None, license_=None, n=3):
    return [SheetRequirement(i, f"Requisito {i}.", True, None, source, url, license_) for i in range(1, n + 1)]


@pytest.mark.parametrize("mode", ["hoja", "ficha"])
def test_no_references_are_shown_in_either_mode(mode):
    wiki = _data(requirements=_sourced("pathfinder-wiki", "https://wiki.pathfindersonline.org/w/Pottery",
                                       "CC BY-SA 3.0"), source_url="https://wiki.pathfindersonline.org/w/Pottery")
    _, content = pdf_text(render_sheet_pdf(wiki, mode=mode, fetch_image=False))
    assert "Requisito 1." in content and no_references(content) and "Pathfinder Wiki" not in content

    imported = _data(requirements=_sourced("guiasmayores.com", "https://www.guiasmayores.com/alfareria.html"),
                     source_url="https://www.guiasmayores.com/alfareria.html",
                     resources=[SheetResource("Requisitos", "https://guiasmayores.com/a.pdf", "pdf"),
                                SheetResource("Manual", "https://media.adventist.club/resources/a.pdf", "pdf")])
    _, content = pdf_text(render_sheet_pdf(imported, mode=mode, fetch_image=False))
    assert no_references(content)
    assert ("Manual" in content) == (mode == "ficha")               # the other resource is listed (ficha)


WIKI_URL = "https://wiki.pathfindersonline.org/w/AY_Honors/Pottery"
WIKI_CREDIT = "Texto de los requisitos: Pathfinder Wiki (NAD) · CC BY-SA 3.0 · wiki.pathfindersonline.org"


@pytest.mark.parametrize("mode", ["hoja", "ficha"])
def test_show_sources_prints_one_credit_line_under_the_requirements(mode):
    wiki = _data(requirements=_sourced("pathfinder-wiki", WIKI_URL, "CC BY-SA 3.0"), resources=[],
                 show_sources=True)
    _, content = pdf_text(render_sheet_pdf(wiki, mode=mode, fetch_image=False))
    assert content.count(WIKI_CREDIT) == 1
    assert content.index("Requisito 3.") < content.index(WIKI_CREDIT)             # after the requirements
    assert "guiasmayores" not in content.lower() and "Fuente" not in content

    mixed = _data(requirements=_sourced("pathfinder-wiki", WIKI_URL, "CC BY-SA 3.0")
                  + [SheetRequirement(4, "Requisito 4.", True, None, "guiasmayores.com",
                                      "https://www.guiasmayores.com/alfareria.html", None)],
                  resources=[SheetResource("Requisitos", "https://guiasmayores.com/a.pdf", "pdf")],
                  show_sources=True)
    _, content = pdf_text(render_sheet_pdf(mixed, mode=mode, fetch_image=False))
    assert WIKI_CREDIT + "; Guías Mayores" in content
    assert "guiasmayores" not in content.lower().replace(" ", "")                # named, never linked/listed


def test_credit_line_groups_sources_and_never_links_a_blocked_site():
    assert credit_line(_data(requirements=_sourced("pathfinder-wiki", WIKI_URL, "CC BY-SA 3.0"))) is None
    assert credit_line(_data(requirements=_sourced(None), show_sources=True)) is None      # nothing to credit
    wiki = credit_line(_data(requirements=_sourced("pathfinder-wiki", WIKI_URL, "CC BY-SA 3.0"), show_sources=True))
    assert wiki == (WIKI_CREDIT, WIKI_URL)
    only_gm = credit_line(_data(requirements=_sourced("guiasmayores.com", "https://www.guiasmayores.com/a.html",
                                                      "CC BY-SA 3.0"), show_sources=True))
    assert only_gm == ("Texto de los requisitos: Guías Mayores", None)
    english = credit_line(_data(language="en", show_sources=True,
                                requirements=_sourced("pathfinder-wiki", None, "CC BY-SA 3.0")))
    assert english == ("Requirements text: Pathfinder Wiki (NAD) · CC BY-SA 3.0 · wiki.pathfindersonline.org",
                       "https://wiki.pathfindersonline.org")


def test_show_sources_is_part_of_the_fingerprint_only_when_on():
    off, on = _data(), _data(show_sources=True)
    assert off.fingerprint() != on.fingerprint() and off.fingerprint("a4", "ficha") != on.fingerprint("a4", "ficha")
    # off keeps the ETags (and R2 keys) the sheets had before the switch existed
    import hashlib
    import json
    from dataclasses import asdict

    legacy = asdict(off)
    del legacy["show_sources"]
    payload = json.dumps({"v": honor_sheet.RENDERER_VERSION, "paper": "a4", "mode": "hoja", **legacy},
                         sort_keys=True, default=str, ensure_ascii=False)
    assert off.fingerprint() == hashlib.sha256(payload.encode()).hexdigest()


def test_show_sources_setting_is_off_by_default_and_lenient(monkeypatch):
    monkeypatch.delenv("SHOW_SOURCES", raising=False)
    assert Settings(DATABASE_URL="postgresql://x", _env_file=None).SHOW_SOURCES is False
    for raw, expected in (("true", True), ("1", True), ("off", False), ("", False), ("quizás", False)):
        monkeypatch.setenv("SHOW_SOURCES", raw)
        assert Settings(DATABASE_URL="postgresql://x", _env_file=None).SHOW_SOURCES is expected, raw


@requires_db
async def test_show_sources_switch_adds_the_credit_and_changes_the_etag(client, factory, monkeypatch):
    honor = await _honor(factory, "creditos", requirements=ALFARERIA_REQUIREMENTS, resources=ALFARERIA_RESOURCES,
                         source="pathfinder-wiki")
    async with SessionLocal() as db:
        await db.execute(text("UPDATE honor_requirements SET source_url = :url, license = 'CC BY-SA 3.0'"
                              " WHERE honor_id = :id"), {"url": WIKI_URL, "id": uuid.UUID(honor["id"])})
        await db.commit()
    url = f"{HONORS}/{honor['id']}/sheet.pdf"
    monkeypatch.setattr(settings, "SHOW_SOURCES", False)
    off = {mode: await client.get(url, params={"modo": mode}) for mode in ("hoja", "ficha")}
    monkeypatch.setattr(settings, "SHOW_SOURCES", True)
    on = {mode: await client.get(url, params={"modo": mode}) for mode in ("hoja", "ficha")}
    for mode in ("hoja", "ficha"):
        assert off[mode].status_code == on[mode].status_code == 200
        assert no_references(pdf_text(off[mode].content)[1])
        content = pdf_text(on[mode].content)[1]
        assert WIKI_CREDIT in content and "Requisitos en guiasmayores" not in content
        assert on[mode].headers["etag"] != off[mode].headers["etag"]
        assert (await client.get(url, params={"modo": mode},
                                 headers={"If-None-Match": off[mode].headers["etag"]})).status_code == 200
    monkeypatch.setattr(settings, "SHOW_SOURCES", False)
    assert (await client.get(url)).headers["etag"] == off["hoja"].headers["etag"]


def _colours(body: bytes) -> set[tuple[str, str, str]]:
    import re

    found = set()
    for page in PdfReader(io.BytesIO(body)).pages:
        stream = page.get_contents().get_data().decode("latin-1")
        found |= set(re.findall(r"([\d.]+) ([\d.]+) ([\d.]+) (?:rg|RG)\b", stream))
    return found


def test_worksheet_is_black_white_and_grey_apart_from_the_pills():
    """No band, no tinted cards: without pills (no level/kind/year) and without practical
    requirements every fill/stroke colour is a grey (r == g == b); the patch is an image."""
    plain = _data(skill_level=None, honor_type=None, year_introduced=None,
                  requirements=[SheetRequirement(i, f"Requisito {i}: {NESTED}", True, None) for i in range(1, 30)])
    colours = _colours(render_sheet_pdf(plain, fetch_image=False))
    assert colours and all(r == g == b for r, g, b in colours), colours
    # the pills (level/kind/year, «Práctico») are the only coloured marks
    assert any(len(set(c)) > 1 for c in _colours(render_sheet_pdf(_data(), fetch_image=False)))
    assert any(len(set(c)) > 1 for c in _colours(render_sheet_pdf(_data(), mode="ficha", fetch_image=False)))


def test_fingerprint_depends_on_the_mode():
    data = _data()
    assert data.fingerprint("a4", "hoja") == data.fingerprint() != data.fingerprint("a4", "ficha")


def test_unreachable_patch_never_breaks_the_sheet(monkeypatch, tmp_path):
    calls = []

    def unreachable(url):
        calls.append(url)
        raise TimeoutError("timed out")

    monkeypatch.setattr(honor_sheet, "_download", unreachable)
    monkeypatch.setattr(honor_sheet, "PATCH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(honor_sheet, "_patch_failures", {})
    data = _data(image_url="https://media.adventist.club/patches/nope.webp", requirements=[])
    assert render_sheet_pdf(data).startswith(b"%PDF")
    assert render_sheet_pdf(data).startswith(b"%PDF")
    assert len(calls) == 1                                                  # the failure is remembered
    # any other host is never fetched at all (no SSRF through a staff-edited URL)
    assert honor_sheet.load_patch("https://evil.example/patch.png") is None and len(calls) == 1


def test_patch_is_fetched_once_and_cached_on_disk(monkeypatch, tmp_path):
    image = io.BytesIO()
    Image.new("RGBA", (900, 900), (200, 30, 60, 255)).save(image, format="WEBP")
    calls = []

    def download(url):
        calls.append(url)
        return image.getvalue()

    monkeypatch.setattr(honor_sheet, "_download", download)
    monkeypatch.setattr(honor_sheet, "PATCH_CACHE_DIR", tmp_path)
    url = "https://media.adventist.club/patches/cached.webp"
    first = honor_sheet.load_patch(url)
    assert first.startswith(b"\x89PNG") and Image.open(io.BytesIO(first)).size == (512, 512)
    assert honor_sheet.load_patch(url) == first and len(calls) == 1
    assert list(tmp_path.glob("*.png"))
    assert render_sheet_pdf(_data(image_url=url, requirements=[])).startswith(b"%PDF")


@pytest.mark.parametrize("mode", ["hoja", "ficha"])
@pytest.mark.parametrize("text_value", ["", "   ", "Una sola palabra" * 40, "https://" + "x" * 400,
                                        "Explicar:\n" + "\n".join(f"  a) {'palabra ' * 30}" for _ in range(60))],
                         ids=["empty", "blank", "long-word", "long-url", "many-long-sub-items"])
def test_degenerate_texts_still_render(text_value, mode):
    data = _data(requirements=[SheetRequirement(1, text_value, False, text_value)], description=text_value,
                 name=text_value or "Sin nombre")
    assert render_sheet_pdf(data, mode=mode, fetch_image=False).startswith(b"%PDF")
