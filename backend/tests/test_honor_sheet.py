"""«Ficha de especialidad»: the honor sheet PDF generated from our own data."""
import io
import time
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import text

from app.db import SessionLocal
from app.services import honor_sheet
from app.services.honor_sheet import (
    SheetData,
    SheetRequirement,
    SheetResource,
    category_accent,
    parse_requirement,
    render_sheet_pdf,
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


async def _honor(factory, label: str, *, status: str = "PUBLISHED", active: bool = True,
                 requirements: list[tuple[str, str, bool, str | None]] = (),
                 resources: list[tuple[str, str, str]] = (), created_by: str | None = None,
                 translations: dict[str, str] | None = None) -> dict:
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
                " locale) VALUES (:id, :honor, :position, :description, :theoretical, :instructions, :locale)"),
                {"id": uuid.uuid4(), "honor": honor_id, "position": position, "description": description,
                 "theoretical": theoretical, "instructions": instructions, "locale": locale})
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


@requires_db
async def test_public_sheet_has_the_honor_its_requirements_and_resources(client, factory):
    honor = await _honor(factory, "alfareria", requirements=[
        ("es", "Explicar qué es la arcilla y cómo se forma en la naturaleza.", True, None),
        ("es", NESTED, True, None),
        ("es", "Hacer una vasija con la técnica de rollos.", False, "El instructor verificará la pieza."),
    ], resources=[
        ("Técnicas de modelado", "https://www.youtube.com/watch?v=demo", "video"),
        ("Guía de seguridad", "https://media.adventist.club/resources/seguridad.pdf", "pdf"),
    ])
    response = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'inline; filename="{honor["slug"]}.pdf"'
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content.startswith(b"%PDF")

    pages, content = pdf_text(response.content)
    assert pages >= 1
    assert flat(honor["name"]) in content
    assert "Explicar qué es la arcilla y cómo se forma en la naturaleza." in content
    for piece in ("Conocer los siguientes términos:", "Barbotina", "Engobe coloreado", "Bizcocho",
                  "Hacer una vasija con la técnica de rollos.", "El instructor verificará la pieza."):
        assert piece in content, piece
    assert "Práctico" in content and "3 requisitos · 1 práctico" in content
    assert "Recursos" in content and "Técnicas de modelado" in content and "Guía de seguridad" in content
    assert f"conquistadores.app/honors/{honor['slug']}" in content
    assert "Fuente: guiasmayores.com" in content and "Página 1 de" in content
    assert "Nivel 1" in content and "Oficial (Asociación General)" in content and "Desde 1929" in content

    download = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf", params={"download": 1})
    assert download.headers["content-disposition"] == f'attachment; filename="{honor["slug"]}.pdf"'


@requires_db
async def test_sheet_without_requirements_says_they_are_in_preparation(client, factory):
    honor = await _honor(factory, "vacia")
    response = await client.get(f"{HONORS}/{honor['id']}/sheet.pdf")
    assert response.status_code == 200
    _, content = pdf_text(response.content)
    assert "Requisitos en preparación" in content
    assert f"www.conquistadores.app/honors/{honor['slug']}" in content
    assert "Recursos" not in content


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


def test_long_sheet_paginates_and_renders_fast():
    render_sheet_pdf(_data(requirements=[]), fetch_image=False)             # warm-up: font registration
    started = time.perf_counter()
    body = render_sheet_pdf(_data(), fetch_image=False)
    assert time.perf_counter() - started < 1.5
    pages, content = pdf_text(body)
    assert pages >= 3 and f"Página {pages} de {pages}" in content
    assert "Versión 3 · actualizada 23 sep 2026" in content
    letter = PdfReader(io.BytesIO(render_sheet_pdf(_data(), paper="letter", fetch_image=False)))
    assert [round(float(v)) for v in letter.pages[0].mediabox.upper_right] == [612, 792]


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


@pytest.mark.parametrize("text_value", ["", "   ", "Una sola palabra" * 40, "https://" + "x" * 400])
def test_degenerate_texts_still_render(text_value):
    data = _data(requirements=[SheetRequirement(1, text_value, False, text_value)], description=text_value,
                 name=text_value or "Sin nombre")
    assert render_sheet_pdf(data, fetch_image=False).startswith(b"%PDF")
