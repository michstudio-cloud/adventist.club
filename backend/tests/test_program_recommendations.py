"""Bloque F — /admin/clases: the classes stay DRAFT until MASTER_GC says so, and every class
carries a list of the honors its requirements ask for.

Two halves:
  1. the text matcher, pure (no database): which honors / categories a requirement written
     in prose names, and whether the member picks one of them or needs all of them;
  2. the endpoints: the status filter of the catalogue for MASTER vs everybody else, and
     `/programs/{id}/recommendations` for each kind of requirement, with the same
     visibility as the detail.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.services.program_recommendations import (
    NameIndex,
    category_index,
    find_mentions,
    skill_levels,
)
from tests.conftest import module_factory, requires_db

PROGRAMS = "/api/v1/programs"


# ----------------------------------------------------------------------------
# 1. The matcher (pure)
# ----------------------------------------------------------------------------
CATALOGUE = [
    "Seguridad básica en el agua",
    "Natación I",
    "Natación II",
    "Natación I, avanzado",
    "Criptografía - avanzado",
    "Criptografía",
    "Campamento I",
    "Campamento II",
    "Campamento III",
    "Campamento IV",
    "Alerta roja",
    "Nudos",
    "Risa",
    "Gusto",
    "Arena",
    "Vida familiar",
    "Orientación",
    "Excursionismo",
]
CATEGORIES = [
    ("arts-crafts-hobbies", "Artes y Habilidades Manuales"),
    ("household-arts", "Artes Domésticas"),
    ("recreation", "Recreación"),
    ("vocational", "Vocacional"),
    ("outdoor-industries", "Industrias al Aire Libre"),
    ("nature", "Naturaleza"),
]


@pytest.fixture(scope="module")
def honors():
    return NameIndex((name, name) for name in CATALOGUE)


@pytest.fixture(scope="module")
def categories():
    return category_index((slug, name, slug) for slug, name in CATEGORIES)


def _mentions(text, honors, categories):
    return find_mentions(text, honors, categories)


def test_either_of_two_honors_is_one_to_choose(honors, categories):
    found = _mentions(
        "Completar la especialidad de Seguridad Básica en el Agua O Natación I si aún no la"
        " haya completado.", honors, categories)
    assert found.honors == ["Seguridad básica en el agua", "Natación I"]
    assert found.choose == "one"
    assert found.categories == []


def test_several_honors_without_or_are_all_required(honors, categories):
    found = _mentions(
        "Tener o desarrollar las siguientes especialidades:\na. Nudos\nb. Vida familiar",
        honors, categories)
    # The « o » of «Tener o desarrollar» is not BETWEEN the honors.
    assert found.honors == ["Nudos", "Vida familiar"]
    assert found.choose == "all"


def test_one_of_the_following_is_one_to_choose(honors, categories):
    found = _mentions(
        "Completar una de las siguientes especialidades no obtenida previamente:\n"
        "  Orientación\n  Excursionismo", honors, categories)
    assert found.honors == ["Orientación", "Excursionismo"]
    assert found.choose == "one"


def test_the_advanced_variant_is_matched_whatever_its_punctuation(honors, categories):
    for text_ in ("Completar la especialidad de Natación I - Avanzado.",
                  "Completar la especialidad de Natación I, avanzado.",
                  "Completar la especialidad de natacion i avanzado."):
        assert _mentions(text_, honors, categories).honors == ["Natación I, avanzado"], text_
    found = _mentions("Completar la especialidad de Criptografía, Avanzado.", honors, categories)
    assert found.honors == ["Criptografía - avanzado"]


def test_the_basic_honor_is_not_taken_for_its_advanced_variant(honors, categories):
    # «Natación II» is not «Natación I»; «Alerta roja II» is not «Alerta roja».
    assert _mentions("Completar la especialidad de Natación II.", honors, categories).honors == [
        "Natación II"]
    assert _mentions("Completar la especialidad de Alerta Roja II.", honors, categories).honors == []
    assert _mentions("Completar la especialidad de Alerta Roja.", honors, categories).honors == [
        "Alerta roja"]


def test_level_wording_and_numeral_lists(honors, categories):
    found = _mentions("Completar la especialidad de Campamento Nivel II.", honors, categories)
    assert found.honors == ["Campamento II"]
    found = _mentions("Tener las siguientes especialidades:\nb. Campamento I, II, III y IV",
                      honors, categories)
    assert found.honors == ["Campamento I", "Campamento II", "Campamento III", "Campamento IV"]
    assert found.choose == "all"


def test_no_false_positives_on_short_common_words(honors, categories):
    # Lower-case common words are not names of honors.
    assert _mentions("Completar la especialidad con gusto, con risa y sobre la arena.",
                     honors, categories).honors == []
    # Without any mention of an honor, nothing is matched at all.
    assert _mentions("Hacer Nudos con cuerda en la Arena.", honors, categories).honors == []
    # A capitalised word that merely opens a sentence is not a name either.
    assert _mentions("Conversar sobre la especialidad. Risa y juegos.",
                     honors, categories).honors == []
    # A name is a whole name: «Nudos» inside «Nudosos» is not a match.
    assert _mentions("Completar la especialidad de Nudosos.", honors, categories).honors == []


def test_no_false_positives_inside_other_proper_names_or_prose():
    index = NameIndex([("Física", "fisica"), ("La iglesia", "la-iglesia"), ("Nudos", "nudos")])
    # «Física» glued to «Aptitud» is part of another name (real text of «Guía» V.1).
    assert find_mentions("Enseñar la especialidad de Alerta Roja I\nEnseñar el requisito #1 de la"
                         " sección de Salud y Aptitud Física para Amigos", index, None).honors == []
    # A name that opens with an article is only a name when written as one (Guía Mayor II.7).
    assert find_mentions("Aumentar su conocimiento de la herencia de la iglesia al completar la"
                         " especialidad de Herencia.", index, None).honors == []
    assert find_mentions("Completar la especialidad de La Iglesia.", index, None).honors == [
        "la-iglesia"]
    # A list of one-word names, one per line, is still a list of names.
    assert find_mentions("Completar las siguientes especialidades:\nNudos\nFísica",
                         index, None).honors == ["nudos", "fisica"]


def test_two_categories_or_three_are_found(honors, categories):
    found = _mentions(
        "Completar una especialidad aún no obtenida en el área de Artes y Habilidades Manuales"
        " o en Artes Domésticas. (Nivel de destreza 2 ó 3).", honors, categories)
    assert found.categories == ["arts-crafts-hobbies", "household-arts"]
    assert found.honors == [] and found.choose == "one"
    found = _mentions(
        "Completar una especialidad a su nivel aún no adquirida en el área de Recreación,"
        " Vocación o Industrias Agropecuarias. (Nivel de destreza 1)", honors, categories)
    assert found.categories == ["recreation", "vocational", "outdoor-industries"]


def test_a_category_is_only_read_where_an_honor_of_it_is_asked_for(honors, categories):
    found = _mentions("Completar la especialidad de Nudos en Recreación.", honors, categories)
    assert found.categories == []
    assert found.honors == ["Nudos"]


def test_skill_levels_are_read_from_the_text():
    assert skill_levels("(Nivel de destreza 2 ó 3).") == {2, 3}
    assert skill_levels("(Nivel de destreza 1)") == {1}
    assert skill_levels("(Skill Level 2 or 3)") == {2, 3}
    assert skill_levels("Completar la especialidad de Nudos.") == set()


def test_english_honors_match_the_english_catalogue():
    index = NameIndex([("Swimming I", "swim-1"), ("Basic Water Safety", "bws")])
    found = find_mentions("Complete the Basic Water Safety or Swimming I honor.", index, None)
    assert found.honors == ["bws", "swim-1"] and found.choose == "one"


# ----------------------------------------------------------------------------
# 2. The endpoints
# ----------------------------------------------------------------------------
factory = module_factory("recs")


@pytest_asyncio.fixture(scope="module")
async def people(factory):
    return {
        "master": await factory.user("master", "MASTER_GC"),
        "director": await factory.user("director", "CLUB_DIRECTOR"),
    }


async def _ministry_id(db):
    return (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()


async def _category(factory, label: str) -> dict:
    category_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(text(
            "INSERT INTO honor_categories (id, ministry_id, name, slug) VALUES (:id, :m, :n, :s)"),
            {"id": category_id, "m": await _ministry_id(db), "n": factory.name(label),
             "s": f"{factory.prefix}-{label}"})
        await db.commit()
    return {"id": str(category_id), "name": factory.name(label), "slug": f"{factory.prefix}-{label}"}


async def _honor(factory, label: str, *, name=None, category=None, skill_level=None,
                 status="PUBLISHED", active=True) -> dict:
    honor_id = uuid.uuid4()
    name = name or factory.name(label)
    async with SessionLocal() as db:
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, category_id, name, slug, active, status,"
            " skill_level, image_url) VALUES (:id, :m, :c, :n, :s, :a, :st, :sk, :img)"),
            {"id": honor_id, "m": await _ministry_id(db),
             "c": uuid.UUID(category["id"]) if category else None, "n": name,
             "s": f"{factory.prefix}-{label}", "a": active, "st": status, "sk": skill_level,
             "img": f"https://cdn.example.com/{label}.png"})
        await db.commit()
    return {"id": str(honor_id), "name": name, "slug": f"{factory.prefix}-{label}"}


async def _program(factory, label: str, requirements: list[dict], *, status="PUBLISHED") -> dict:
    """One section; each requirement is {kind, text, honor?, category?, program?}."""
    program_id, section_id = uuid.uuid4(), uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(text(
            "INSERT INTO programs (id, ministry_id, kind, slug, name, status)"
            " VALUES (:id, :m, 'CLASS', :slug, :name, :status)"),
            {"id": program_id, "m": await _ministry_id(db), "slug": f"{factory.prefix}-{label}",
             "name": factory.name(label), "status": status})
        await db.execute(text(
            "INSERT INTO program_sections (id, program_id, position, slug, name)"
            " VALUES (:id, :p, 1, 'salud', 'Salud y aptitud física')"),
            {"id": section_id, "p": program_id})
        ids = []
        for position, requirement in enumerate(requirements, start=1):
            requirement_id = uuid.uuid4()
            ids.append(str(requirement_id))
            await db.execute(text(
                "INSERT INTO program_requirements (id, program_id, section_id, position, label,"
                " kind, evidence_required, target_honor_id, target_category_id, target_program_id)"
                " VALUES (:id, :p, :s, :pos, :label, :kind, false, :h, :c, :prog)"),
                {"id": requirement_id, "p": program_id, "s": section_id, "pos": position,
                 "label": f"V.{position}", "kind": requirement["kind"],
                 "h": uuid.UUID(requirement["honor"]) if requirement.get("honor") else None,
                 "c": uuid.UUID(requirement["category"]) if requirement.get("category") else None,
                 "prog": uuid.UUID(requirement["program"]) if requirement.get("program") else None})
            await db.execute(text(
                "INSERT INTO program_requirement_texts (requirement_id, locale, description)"
                " VALUES (:r, 'es', :d)"), {"r": requirement_id, "d": requirement["text"]})
        await db.commit()
    return {"id": str(program_id), "requirements": ids}


@requires_db
async def test_master_filters_the_catalogue_by_status(client, factory, people):
    published = await _program(factory, "pub", [{"kind": "FREE", "text": "Uno"}])
    draft = await _program(factory, "draft", [{"kind": "FREE", "text": "Uno"}], status="DRAFT")
    archived = await _program(factory, "arch", [{"kind": "FREE", "text": "Uno"}], status="ARCHIVED")
    master = people["master"]["headers"]

    async def ids(params, headers=None):
        response = await client.get(PROGRAMS, params={"ministry": "pathfinders", **params},
                                    headers=headers or {})
        assert response.status_code == 200, response.text
        return {row["id"]: row["status"] for row in response.json()}

    everything = await ids({"status": "ALL"}, master)
    assert everything[published["id"]] == "PUBLISHED"
    assert everything[draft["id"]] == "DRAFT"
    assert everything[archived["id"]] == "ARCHIVED"
    only_drafts = await ids({"status": "DRAFT"}, master)
    assert draft["id"] in only_drafts and published["id"] not in only_drafts
    assert set(only_drafts.values()) == {"DRAFT"}
    assert set((await ids({"status": "ARCHIVED"}, master)).values()) == {"ARCHIVED"}
    # MASTER without a filter: exactly what the public sees.
    default = await ids({}, master)
    assert published["id"] in default and draft["id"] not in default

    # Everybody else: the filter is ignored, only the published programs exist.
    for headers in (None, people["director"]["headers"]):
        seen = await ids({"status": "ALL"}, headers)
        assert published["id"] in seen
        assert draft["id"] not in seen and archived["id"] not in seen
        assert set(seen.values()) == {"PUBLISHED"}


@requires_db
async def test_an_unknown_status_is_rejected(client, people):
    response = await client.get(PROGRAMS, params={"ministry": "pathfinders", "status": "BORRADOR"},
                                headers=people["master"]["headers"])
    assert response.status_code == 422


@requires_db
async def test_recommendations_for_each_kind_of_requirement(client, factory, people):
    category = await _category(factory, "naturaleza")
    # Ten published honors of the category, plus two that must never be suggested.
    for index in range(10):
        await _honor(factory, f"nat-{index:02d}", category=category,
                     skill_level=2 if index < 5 else 1)
    await _honor(factory, "nat-borrador", category=category, skill_level=1, status="DRAFT")
    await _honor(factory, "nat-inactiva", category=category, skill_level=1, active=False)
    target = await _honor(factory, "rcp", skill_level=1)
    water = await _honor(factory, "agua", name=f"{factory.prefix} Seguridad básica en el agua")
    swim = await _honor(factory, "natacion", name=f"{factory.prefix} Natación I")
    other_class = await _program(factory, "previa", [{"kind": "FREE", "text": "Uno"}])

    program = await _program(factory, "recs", [
        {"kind": "HONOR", "honor": target["id"], "text": "Obtener la especialidad de RCP."},
        {"kind": "HONOR", "category": category["id"],
         "text": "Completar una especialidad sobre la naturaleza."},
        {"kind": "HONOR", "text": "Completar una especialidad a elección."},
        {"kind": "FREE", "text": f"Completar la especialidad de {water['name']} O {swim['name']}"
                                 " si aún no la haya completado."},
        {"kind": "FREE", "text": "Estar en el 5º grado o su equivalente."},
        {"kind": "PROGRAM", "program": other_class["id"], "text": "Completar Amigo."},
    ], status="DRAFT")

    response = await client.get(f"{PROGRAMS}/{program['id']}/recommendations",
                                params={"locale": "es"}, headers=people["master"]["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["program_id"] == program["id"] and body["locale"] == "es"
    items = body["items"]
    assert [item["requirement_id"] for item in items] == program["requirements"][:4]
    by_kind = {item["kind"]: item for item in items}
    assert list(by_kind) == ["HONOR", "HONOR_FROM_CATEGORY", "HONOR_ANY", "TEXT"]

    honor = by_kind["HONOR"]
    assert honor["choose"] == "all" and honor["category"] is None
    assert [h["id"] for h in honor["honors"]] == [target["id"]]
    assert honor["honors"][0]["skill_level"] == 1
    assert honor["honors"][0]["image_url"].endswith("rcp.png")
    assert honor["section"] == {"slug": "salud", "name": "Salud y aptitud física"}
    assert honor["label"] == "V.1" and honor["text"] == "Obtener la especialidad de RCP."

    from_category = by_kind["HONOR_FROM_CATEGORY"]
    assert from_category["choose"] == "one"
    assert from_category["category"] == {"id": category["id"], "name": category["name"],
                                         "slug": category["slug"]}
    assert from_category["categories"] == [from_category["category"]]
    suggested = from_category["honors"]
    assert len(suggested) == 8
    slugs = [h["slug"] for h in suggested]
    assert not any(s.endswith(("borrador", "inactiva")) for s in slugs)
    # Basic first, then by name.
    assert [h["skill_level"] for h in suggested] == [1] * 5 + [2] * 3
    assert slugs[:5] == sorted(slugs[:5])
    assert all(h["category_slug"] == category["slug"] for h in suggested)

    anything = by_kind["HONOR_ANY"]
    assert anything["choose"] == "any" and anything["honors"] == [] and anything["category"] is None

    mentioned = by_kind["TEXT"]
    assert mentioned["choose"] == "one"
    assert [h["id"] for h in mentioned["honors"]] == [water["id"], swim["id"]]

    detail = await client.get(f"{PROGRAMS}/{program['id']}", headers=people["master"]["headers"])
    assert detail.json()["recommendations_count"] == 4


@requires_db
async def test_the_requested_skill_level_orders_the_suggestions(client, factory, people):
    category = await _category(factory, "manuales")
    for index in range(4):
        await _honor(factory, f"man-{index}", category=category, skill_level=1 + index % 3)
    program = await _program(factory, "nivel", [
        {"kind": "HONOR", "category": category["id"],
         "text": "Completar una especialidad de la categoría. (Nivel de destreza 2 ó 3)"},
    ])
    items = (await client.get(f"{PROGRAMS}/{program['id']}/recommendations")).json()["items"]
    assert [h["skill_level"] for h in items[0]["honors"]] == [2, 3]


@requires_db
async def test_a_text_naming_two_categories_suggests_from_both(client, factory, people):
    first = await _category(factory, "artes")
    second = await _category(factory, "domesticas")
    await _honor(factory, "art-1", category=first, skill_level=1)
    await _honor(factory, "dom-1", category=second, skill_level=1)
    program = await _program(factory, "dos-areas", [
        {"kind": "FREE", "text": f"Completar una especialidad en el área de {first['name']} o en"
                                 f" {second['name']}."},
    ])
    items = (await client.get(f"{PROGRAMS}/{program['id']}/recommendations")).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "TEXT" and item["choose"] == "one"
    assert [c["id"] for c in item["categories"]] == [first["id"], second["id"]]
    assert item["category"]["id"] == first["id"]
    assert {h["slug"] for h in item["honors"]} == {f"{factory.prefix}-art-1", f"{factory.prefix}-dom-1"}


@requires_db
async def test_recommendations_of_a_draft_are_hidden_like_the_detail(client, factory, people):
    draft = await _program(factory, "oculta", [{"kind": "HONOR", "text": "Una a elección."}],
                           status="DRAFT")
    url = f"{PROGRAMS}/{draft['id']}/recommendations"
    assert (await client.get(url)).status_code == 404
    assert (await client.get(url, headers=people["director"]["headers"])).status_code == 404
    assert (await client.get(url, headers=people["master"]["headers"])).status_code == 200
    assert (await client.get(f"{PROGRAMS}/{uuid.uuid4()}/recommendations")).status_code == 404

    published = await _program(factory, "visible", [{"kind": "HONOR", "text": "Una a elección."}])
    public = await client.get(f"{PROGRAMS}/{published['id']}/recommendations")
    assert public.status_code == 200 and len(public.json()["items"]) == 1
    assert (await client.get(f"{PROGRAMS}/{published['id']}")).json()["recommendations_count"] == 1
