"""Constructor de especialidades: what the admin-only honor builder needs from the API.

Strict question banks (the course rules), typed resources, the extra editable fields, the
staff listing with `status=ALL`, restore, and the categories CRUD with its translations.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("hbuilder")

HONORS = "/api/v1/honors"
CATEGORIES = f"{HONORS}/categories"

MC = {
    "question_text": "¿Qué nudo forma un lazo fijo?",
    "question_type": "MULTIPLE_CHOICE",
    "options": ["As de guía", "Vuelta de escota"],
    "correct_answer": "As de guía",
    "points": 2,
}
TF = {"question_text": "El nudo llano sirve para escalar", "question_type": "TRUE_FALSE",
      "correct_answer": "false"}


@pytest_asyncio.fixture(scope="module")
async def staff(factory):
    division = await factory.org("div", "division")
    association = await factory.org("assoc", "association", division)
    zone = await factory.org("zone", "zone", association)
    other_assoc = await factory.org("other-assoc", "association", division)
    other_zone = await factory.org("other-zone", "zone", other_assoc)
    return {
        "zone": zone,
        "association": association,
        "other_zone": other_zone,
        "instructor": await factory.user("instructor", "INSTRUCTOR", zone["id"]),
        "instructor2": await factory.user("instructor2", "INSTRUCTOR", zone["id"]),
        "coordinator": await factory.user("coordinator", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "other_coordinator": await factory.user("other-coord", "COORDINATOR_ZONE", other_zone["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "student": await factory.user("student", "STUDENT", zone["id"]),
    }


def _body(factory, label, **extra):
    return {
        "name": factory.name(label),
        "code": f"{factory.prefix}-{label}"[:40],
        "category": "recreation",
        "requirements": [
            {"description": "Hacer un as de guía", "question_bank": [MC, TF]},
            {"description": "Demostrar un amarre", "is_theoretical": False},
        ],
        **extra,
    }


async def _create(client, staff, factory, label, author="instructor", **extra):
    response = await client.post(HONORS, json=_body(factory, label, **extra),
                                 headers=staff[author]["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _bank(client, staff, honor_id, who="master"):
    response = await client.get(f"{HONORS}/{honor_id}/instructor", headers=staff[who]["headers"])
    assert response.status_code == 200, response.text
    return response.json()["requirements_with_questions"][0]["question_bank"]


async def _audit(entity_id, entity_type="HONOR"):
    return await fetch_all(
        "SELECT action, metadata_json FROM audit_log WHERE entity_type = :t AND entity_id = :id"
        " ORDER BY created_at",
        t=entity_type, id=str(entity_id),
    )


# ----------------------------------------------------------------------------
# 1. Question banks follow the course rules
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "broken",
    [
        {**MC, "options": ["Uno"]},
        {**MC, "options": ["Uno", "Uno"]},
        {**MC, "options": ["a", "b", "c", "d", "e", "f", "g"]},
        {**MC, "correct_answer": "Ninguna de las opciones"},
        {**MC, "options": None},
        {**TF, "correct_answer": "quizás"},
        {**TF, "options": ["Sí", "No"]},
        {"question_text": "Ensayo", "question_type": "ESSAY", "correct_answer": "Rúbrica",
         "options": ["a", "b"]},
        {"question_text": "Corta", "question_type": "SHORT_ANSWER",
         "correct_answer": "|".join(str(n) for n in range(11))},
        {"question_text": "Corta", "question_type": "SHORT_ANSWER", "correct_answer": "x" * 121},
        {**MC, "points": 0},
        {**MC, "points": 11},
        {**MC, "question_text": "x" * 1001},
        {**TF, "explanation": "x" * 1001},
        {"question_text": "Ensayo", "question_type": "ESSAY", "correct_answer": "x" * 2001},
    ],
)
async def test_an_invalid_honor_question_is_refused(client, staff, factory, broken):
    body = _body(factory, "bad-q", requirements=[{"description": "R", "question_bank": [broken]}])
    created = await client.post(HONORS, json=body, headers=staff["instructor"]["headers"])
    assert created.status_code == 422, created.text


async def test_honor_questions_are_normalized_like_course_questions(client, staff, factory):
    bank = [
        {**MC, "options": [" As de guía ", "Vuelta de escota"], "correct_answer": "As de guía "},
        {**TF, "correct_answer": " TRUE "},
        {"question_text": "Nombre", "question_type": "SHORT_ANSWER", "correct_answer": " llano | cuadrado |"},
        {"question_text": "Explica", "question_type": "ESSAY", "correct_answer": "Rúbrica", "options": []},
    ]
    honor = await _create(client, staff, factory, "normal",
                          requirements=[{"description": "R", "question_bank": bank}])
    saved = await _bank(client, staff, honor["id"])
    assert saved[0]["options"] == ["As de guía", "Vuelta de escota"]
    assert saved[0]["correct_answer"] == "As de guía"
    assert saved[1]["correct_answer"] == "true" and saved[1]["options"] is None
    assert saved[2]["correct_answer"] == "llano|cuadrado"
    assert saved[3]["options"] is None


async def test_a_requirement_bank_holds_at_most_two_hundred_questions(client, staff, factory):
    many = [{**TF, "question_text": f"Afirmación {n}"} for n in range(201)]
    body = _body(factory, "too-many", requirements=[{"description": "R", "question_bank": many}])
    assert (await client.post(HONORS, json=body, headers=staff["instructor"]["headers"])).status_code == 422
    honor = await _create(client, staff, factory, "two-hundred",
                          requirements=[{"description": "R", "question_bank": many[:200]}])
    assert honor["requirements"][0]["question_count"] == 200
    # PUT and new versions go through the same rules.
    edited = await client.put(f"{HONORS}/{honor['id']}", json={"requirements": [
        {"description": "R", "question_bank": [{**MC, "correct_answer": "Otra"}]}]},
        headers=staff["instructor"]["headers"])
    assert edited.status_code == 422


# ----------------------------------------------------------------------------
# 2. Typed resources
# ----------------------------------------------------------------------------
async def test_resources_are_typed_and_validated(client, staff, factory):
    media = settings.R2_PUBLIC_URL.rstrip("/")
    resources = [
        {"name": "Vídeo", "url": "https://youtu.be/dQw4w9WgXcQ", "type": "video"},
        {"name": "Vimeo", "url": "https://player.vimeo.com/video/123456789", "type": "VIDEO"},
        {"name": "Manual", "url": f"{media}/resources/manual.pdf", "type": "pdf"},
        {"name": "Foto", "url": f"{media}/resources/nudo.png", "type": "IMAGE"},
        {"name": "Wiki", "url": "https://wiki.pathfindersonline.org/w/AY_Honors/Knot_Tying"},
    ]
    honor = await _create(client, staff, factory, "res", resources=resources)
    out = honor["resources"]
    assert [r["type"] for r in out] == ["VIDEO", "VIDEO", "PDF", "IMAGE", "LINK"]
    assert out[0]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert out[1]["url"] == "https://vimeo.com/123456789"
    assert out[2]["url"] == f"{media}/resources/manual.pdf"
    stored = await fetch_all(
        "SELECT type FROM honor_resources WHERE honor_id = :id ORDER BY position", id=uuid.UUID(honor["id"])
    )
    assert [r["type"] for r in stored] == ["VIDEO", "VIDEO", "PDF", "IMAGE", "LINK"]

    for bad in (
        {"name": "x", "url": "https://example.com/video", "type": "VIDEO"},
        {"name": "x", "url": "https://example.com/manual.pdf", "type": "PDF"},
        {"name": "x", "url": "https://example.com/a.png", "type": "image"},
        {"name": "x", "url": "javascript:alert(1)", "type": "LINK"},
        {"name": "x", "url": "ftp://example.com/file"},
        {"name": "x", "url": "https://example.com/" + "a" * 2000},
        {"name": "x", "url": "https://example.com", "type": "AUDIO"},
    ):
        response = await client.put(f"{HONORS}/{honor['id']}", json={"resources": [bad]},
                                    headers=staff["instructor"]["headers"])
        assert response.status_code == 422, bad


async def test_legacy_resource_types_still_serialize(client, staff, factory):
    honor = await _create(client, staff, factory, "legacy-res")
    for position, legacy in enumerate((None, "manual", "pdf"), start=1):
        async with SessionLocal() as db:
            await db.execute(text(
                "INSERT INTO honor_resources (id, honor_id, position, name, url, type)"
                " VALUES (:id, :h, :p, 'Viejo', 'https://example.com/old', :t)"),
                {"id": uuid.uuid4(), "h": uuid.UUID(honor["id"]), "p": position, "t": legacy})
            await db.commit()
    detail = await client.get(f"{HONORS}/{honor['id']}", headers=staff["instructor"]["headers"])
    assert detail.status_code == 200, detail.text
    assert [r["type"] for r in detail.json()["resources"]] == ["LINK", "LINK", "PDF"]


# ----------------------------------------------------------------------------
# 3. The extra editable fields
# ----------------------------------------------------------------------------
async def test_update_catalogue_fields(client, staff, factory):
    honor = await _create(client, staff, factory, "fields")
    url = f"{HONORS}/{honor['id']}"
    body = {
        "source_url": "https://wiki.pathfindersonline.org/w/AY_Honors/Knot_Tying",
        "wiki_title": "Knot Tying",
        "authority": "GC",
        "skill_level": 2,
        "year_introduced": 1928,
    }
    saved = await client.put(url, json=body, headers=staff["instructor"]["headers"])
    assert saved.status_code == 200, saved.text
    assert {key: saved.json()[key] for key in body} == body

    for bad in (
        {"source_url": "ftp://example.com"},
        {"source_url": "https://example.com/" + "a" * 2000},
        {"wiki_title": "x" * 201},
        {"authority": "x" * 11},
        {"skill_level": 0},
        {"skill_level": 4},
        {"year_introduced": 1899},
        {"year_introduced": 2101},
    ):
        response = await client.put(url, json=bad, headers=staff["instructor"]["headers"])
        assert response.status_code == 422, bad

    cleared = await client.put(url, json={"source_url": None, "skill_level": None},
                               headers=staff["instructor"]["headers"])
    assert cleared.status_code == 200
    assert cleared.json()["source_url"] is None and cleared.json()["skill_level"] is None


async def test_org_scope_is_master_only(client, staff, factory):
    honor = await _create(client, staff, factory, "scope")
    url = f"{HONORS}/{honor['id']}"
    denied = await client.put(url, json={"org_scope_id": staff["other_zone"]["id"]},
                              headers=staff["instructor"]["headers"])
    assert denied.status_code == 403 and denied.json()["detail"] == "org_scope_master_only"

    moved = await client.put(url, json={"org_scope_id": staff["other_zone"]["id"]},
                             headers=staff["master"]["headers"])
    assert moved.status_code == 200, moved.text
    assert moved.json()["org_scope_id"] == staff["other_zone"]["id"]
    unknown = await client.put(url, json={"org_scope_id": str(uuid.uuid4())},
                               headers=staff["master"]["headers"])
    assert unknown.status_code == 400


async def test_master_toggles_active_on_a_published_honor(client, staff, factory):
    honor = await _create(client, staff, factory, "active")
    url = f"{HONORS}/{honor['id']}"
    assert (await client.post(f"{url}/publish", headers=staff["master"]["headers"])).status_code == 200

    # The author cannot touch a published honor; MASTER_GC cannot replace its requirement list
    # (the light fields and in-place requirement edits are in test_honor_inplace.py).
    for who, body in (("instructor", {"active": False}),
                      ("master", {"active": False, "requirements": [{"description": "x"}]})):
        response = await client.put(url, json=body, headers=staff[who]["headers"])
        assert response.status_code == 400, who

    hidden = await client.put(url, json={"active": False}, headers=staff["master"]["headers"])
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["active"] is False and hidden.json()["status"] == "PUBLISHED"
    public = await client.get(HONORS, params={"q": factory.name("active")})
    assert honor["id"] not in {row["id"] for row in public.json()}

    shown = await client.put(url, json={"active": True}, headers=staff["master"]["headers"])
    assert shown.status_code == 200 and shown.json()["active"] is True
    public = await client.get(HONORS, params={"q": factory.name("active")})
    assert honor["id"] in {row["id"] for row in public.json()}

    audit = await _audit(honor["id"])
    assert [r["action"] for r in audit] == ["CREATE", "PUBLISH", "UPDATE", "UPDATE"]
    assert audit[-1]["metadata_json"]["fields"] == ["active"]


# ----------------------------------------------------------------------------
# 4. The staff listing
# ----------------------------------------------------------------------------
async def test_status_all_lists_every_status_in_scope(client, staff, factory):
    draft = await _create(client, staff, factory, "all-draft")
    published = await _create(client, staff, factory, "all-pub")
    await client.post(f"{HONORS}/{published['id']}/publish", headers=staff["master"]["headers"])
    archived = await _create(client, staff, factory, "all-arch")
    assert (await client.delete(f"{HONORS}/{archived['id']}",
                                headers=staff["instructor"]["headers"])).status_code == 204
    foreign = await _create(client, staff, factory, "all-foreign", author="instructor2")
    ours = {draft["id"], published["id"], archived["id"]}

    async def listed(who):
        response = await client.get(HONORS, params={"q": factory.prefix, "status": "ALL"},
                                    headers=staff[who]["headers"] if who else None)
        return response

    master = await listed("master")
    assert master.status_code == 200, master.text
    ids = {row["id"] for row in master.json()}
    assert ours | {foreign["id"]} <= ids
    assert int(master.headers["X-Total-Count"]) >= len(ours) + 1
    assert {row["status"] for row in master.json() if row["id"] in ours} == {"DRAFT", "PUBLISHED", "ARCHIVED"}

    own = {row["id"] for row in (await listed("instructor")).json()}
    assert ours <= own and foreign["id"] not in own
    reviewer = {row["id"] for row in (await listed("coordinator")).json()}
    assert ours | {foreign["id"]} <= reviewer
    elsewhere = {row["id"] for row in (await listed("other_coordinator")).json()}
    assert not (ours & elsewhere)

    for who in (None, "student"):
        assert (await listed(who)).status_code == 403, who
    assert "correct_answer" not in master.text


# ----------------------------------------------------------------------------
# 5. Publish and submit
# ----------------------------------------------------------------------------
async def test_publish_needs_requirements(client, staff, factory):
    empty = await _create(client, staff, factory, "pub-empty", requirements=[])
    refused = await client.post(f"{HONORS}/{empty['id']}/publish", headers=staff["master"]["headers"])
    assert refused.status_code == 400
    assert refused.json()["detail"] == "honor_without_requirements"
    submit = await client.post(f"{HONORS}/{empty['id']}/submit", headers=staff["instructor"]["headers"])
    assert submit.status_code == 400 and submit.json()["detail"] == "honor_without_requirements"


async def test_master_submits_on_behalf_of_the_author(client, staff, factory):
    honor = await _create(client, staff, factory, "on-behalf")
    url = f"{HONORS}/{honor['id']}/submit"
    assert (await client.post(url, headers=staff["instructor2"]["headers"])).status_code == 403
    done = await client.post(url, headers=staff["master"]["headers"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "ZONE_REVIEW"
    assert [r["action"] for r in await _audit(honor["id"])] == ["CREATE", "SUBMIT"]


# ----------------------------------------------------------------------------
# 6. Restore
# ----------------------------------------------------------------------------
async def test_restore_brings_an_archived_honor_back_as_a_hidden_draft(client, staff, factory):
    honor = await _create(client, staff, factory, "restore")
    url = f"{HONORS}/{honor['id']}/restore"
    not_archived = await client.post(url, headers=staff["master"]["headers"])
    assert not_archived.status_code == 400 and not_archived.json()["detail"] == "honor_not_archived"

    assert (await client.delete(f"{HONORS}/{honor['id']}",
                                headers=staff["instructor"]["headers"])).status_code == 204
    for who in ("instructor", "assoc_admin"):
        assert (await client.post(url, headers=staff[who]["headers"])).status_code == 403, who
    assert (await client.post(url)).status_code == 401
    assert (await client.post(f"{HONORS}/{uuid.uuid4()}/restore",
                              headers=staff["master"]["headers"])).status_code == 404

    restored = await client.post(url, headers=staff["master"]["headers"])
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["status"] == "DRAFT" and body["active"] is False and "review_history" in body
    assert [r["action"] for r in await _audit(honor["id"])] == ["CREATE", "DELETE", "RESTORE"]


# ----------------------------------------------------------------------------
# 7. Categories
# ----------------------------------------------------------------------------
async def test_category_crud(client, staff, factory):
    master = staff["master"]["headers"]
    name = factory.name("Artes y oficios")
    body = {"name": name}
    assert (await client.post(CATEGORIES, json=body)).status_code == 401
    for who in ("instructor", "assoc_admin"):
        assert (await client.post(CATEGORIES, json=body, headers=staff[who]["headers"])).status_code == 403

    created = await client.post(CATEGORIES, json=body, headers=master)
    assert created.status_code == 201, created.text
    category = created.json()
    assert set(category) == {"id", "name", "slug"}
    assert category["name"] == name and category["slug"] == f"{factory.prefix}-artes-y-oficios"

    clash = await client.post(CATEGORIES, json={"name": factory.name("Otra"), "slug": category["slug"]},
                              headers=master)
    assert clash.status_code == 409 and clash.json()["detail"] == "category_slug_taken"
    assert (await client.post(CATEGORIES, json={**body, "ministry": "nope"}, headers=master)).status_code == 400
    for bad in ({"name": "x"}, {"name": "x" * 121}, {"name": factory.name("Bad"), "slug": "Con Espacios"},
                {"name": factory.name("Bad"), "slug": "a"}):
        assert (await client.post(CATEGORIES, json=bad, headers=master)).status_code == 422, bad

    explicit = await client.post(
        CATEGORIES, json={"ministry": "pathfinders", "name": factory.name("Explicit"),
                          "slug": f"{factory.prefix}-explicit"}, headers=master)
    assert explicit.status_code == 201 and explicit.json()["slug"] == f"{factory.prefix}-explicit"

    public = await client.get(CATEGORIES)
    assert category["slug"] in {row["slug"] for row in public.json()}

    cat_url = f"{CATEGORIES}/{category['id']}"
    renamed = await client.put(cat_url, json={"name": factory.name("Artes")}, headers=master)
    assert renamed.status_code == 200 and renamed.json()["name"] == factory.name("Artes")
    assert renamed.json()["slug"] == category["slug"]
    clash = await client.put(cat_url, json={"slug": explicit.json()["slug"]}, headers=master)
    assert clash.status_code == 409 and clash.json()["detail"] == "category_slug_taken"
    assert (await client.put(cat_url, json={"name": "x"}, headers=staff["instructor"]["headers"])).status_code == 403
    assert (await client.put(f"{CATEGORIES}/{uuid.uuid4()}", json={"name": factory.name("Z")},
                             headers=master)).status_code == 404

    # In use by any honor, in any status: it stays.
    honor = await _create(client, staff, factory, "cat-user", category=category["slug"])
    assert (await client.delete(f"{HONORS}/{honor['id']}", headers=master)).status_code == 204
    in_use = await client.delete(cat_url, headers=master)
    assert in_use.status_code == 400 and in_use.json()["detail"] == "category_in_use"

    exp_url = f"{CATEGORIES}/{explicit.json()['id']}"
    assert (await client.delete(exp_url, headers=staff["instructor"]["headers"])).status_code == 403
    deleted = await client.delete(exp_url, headers=master)
    assert deleted.status_code == 204
    assert await fetch_one("SELECT id FROM honor_categories WHERE id = :id",
                           id=uuid.UUID(explicit.json()["id"])) is None
    assert (await client.delete(exp_url, headers=master)).status_code == 404

    actions = [r["action"] for r in await _audit(category["id"], "HONOR_CATEGORY")]
    assert actions == ["CREATE", "UPDATE"]
    assert [r["action"] for r in await _audit(explicit.json()["id"], "HONOR_CATEGORY")] == ["CREATE", "DELETE"]


async def test_category_translations(client, staff, factory):
    master = staff["master"]["headers"]
    created = await client.post(CATEGORIES, json={"name": factory.name("Naturaleza viva")}, headers=master)
    assert created.status_code == 201, created.text
    category = created.json()
    url = f"{CATEGORIES}/{category['id']}/translations"

    assert (await client.put(f"{url}/en", json={"name": factory.name("Nature")},
                             headers=staff["instructor"]["headers"])).status_code == 403
    for source in ("es", "ES", "es-MX"):
        assert (await client.put(f"{url}/{source}", json={"name": factory.name("x")},
                                 headers=master)).status_code == 422, source
    assert (await client.put(f"{url}/not a locale", json={"name": factory.name("x")},
                             headers=master)).status_code == 422
    assert (await client.put(f"{url}/en", json={"name": "x"}, headers=master)).status_code == 422

    saved = await client.put(f"{url}/pt-br", json={"name": factory.name("Natureza")}, headers=master)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {**category, "name": factory.name("Natureza")}
    rows = await fetch_all("SELECT locale, name FROM honor_category_translations WHERE category_id = :id",
                           id=uuid.UUID(category["id"]))
    assert [(r["locale"], r["name"]) for r in rows] == [("pt-BR", factory.name("Natureza"))]

    listed = await client.get(CATEGORIES, params={"locale": "pt-BR"})
    assert next(r for r in listed.json() if r["id"] == category["id"])["name"] == factory.name("Natureza")

    assert (await client.delete(f"{url}/en", headers=master)).status_code == 404
    assert (await client.delete(f"{url}/pt-BR", headers=staff["instructor"]["headers"])).status_code == 403
    gone = await client.delete(f"{url}/PT-br", headers=master)
    assert gone.status_code == 204
    assert await fetch_all("SELECT locale FROM honor_category_translations WHERE category_id = :id",
                           id=uuid.UUID(category["id"])) == []
    assert [r["action"] for r in await _audit(category["id"], "HONOR_CATEGORY")] == ["CREATE", "UPDATE", "UPDATE"]


async def test_category_counts_for_staff(client, staff, factory):
    master = staff["master"]["headers"]
    created = await client.post(CATEGORIES, json={"name": factory.name("Contada")}, headers=master)
    category = created.json()
    await _create(client, staff, factory, "count-a", category=category["slug"])
    archived = await _create(client, staff, factory, "count-b", category=category["slug"], author="instructor2")
    await client.delete(f"{HONORS}/{archived['id']}", headers=master)

    public = await client.get(CATEGORIES, params={"include_counts": "true"})
    assert all(set(row) == {"id", "name", "slug"} for row in public.json())

    def count(response):
        return next(r for r in response.json() if r["id"] == category["id"])["honor_count"]

    assert count(await client.get(CATEGORIES, params={"include_counts": "true"}, headers=master)) == 2
    own = await client.get(CATEGORIES, params={"include_counts": "true"}, headers=staff["instructor"]["headers"])
    assert own.status_code == 200 and count(own) == 1
    plain = await client.get(CATEGORIES, headers=master)
    assert all(set(row) == {"id", "name", "slug"} for row in plain.json())


async def test_category_routes_are_not_captured_by_honor_routes(client, staff):
    # `categories` is not a UUID: it must never reach /{honor_id}.
    response = await client.put(f"{CATEGORIES}/{uuid.uuid4()}/translations/en", json={"name": "Nature"},
                                headers=staff["master"]["headers"])
    assert response.status_code == 404
    assert response.json()["detail"] != "Honor not found"
