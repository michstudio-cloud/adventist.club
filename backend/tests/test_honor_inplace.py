"""Ajustes mínimos de MASTER_GC sobre una especialidad en cualquier estado.

«Como cuenta master debo poder editar todo lo visible en todos lados para ajustes mínimos o
bien subir recursos oficiales.» Two tools:

- `PUT /honors/{id}` accepts the light fields (and the resources) from MASTER_GC in any status,
  but never a whole new requirement list outside DRAFT: that would give every requirement a new
  id under the enrollments that point at them.
- `/honors/{id}/requirements[...]` edits requirements IN PLACE (ids preserved): patch, add,
  reorder, delete (only when no progress row references it).
"""

import uuid

import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("hinplace")

HONORS = "/api/v1/honors"
ENROLLMENTS = "/api/v1/portfolio/enrollments"
LOCKED = "Only draft honors can be updated. Create a new version instead."

MC = {
    "question_text": "¿Qué nudo forma un lazo fijo?",
    "question_type": "MULTIPLE_CHOICE",
    "options": ["As de guía", "Vuelta de escota"],
    "correct_answer": "As de guía",
}
TF = {"question_text": "El nudo llano sirve para escalar", "question_type": "TRUE_FALSE",
      "correct_answer": "false"}


@pytest_asyncio.fixture(scope="module")
async def staff(factory):
    association = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", association)
    return {
        "association": association,
        "instructor":await factory.user("instructor", "INSTRUCTOR", zone["id"]),
        "instructor2": await factory.user("instructor2", "INSTRUCTOR", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "member": await factory.user("member", "STUDENT"),
        "member2": await factory.user("member2", "STUDENT"),
    }


async def _create(client, staff, factory, label, author="instructor", publish=False, **extra):
    body = {
        "name": factory.name(label),
        "code": f"{factory.prefix}-{label}"[:40],
        "category": "recreation",
        "requirements": [
            {"description": "Hacer un as de guía", "question_bank": [MC]},
            {"description": "Demostrar un amarre", "question_bank": [TF]},
            {"description": "Atar tres nudos", "is_theoretical": False},
        ],
        **extra,
    }
    response = await client.post(HONORS, json=body, headers=staff[author]["headers"])
    assert response.status_code == 201, response.text
    honor = response.json()
    if publish:
        published = await client.post(f"{HONORS}/{honor['id']}/publish", headers=staff["master"]["headers"])
        assert published.status_code == 200, published.text
        honor = published.json()
    return honor


async def _instructor(client, staff, honor_id, who="master"):
    response = await client.get(f"{HONORS}/{honor_id}/instructor", headers=staff[who]["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def _audit(entity_id):
    return await fetch_all(
        "SELECT action, metadata_json FROM audit_log WHERE entity_type = 'HONOR' AND entity_id = :id"
        " ORDER BY created_at",
        id=str(entity_id),
    )


async def _enroll(client, member, honor_id):
    response = await client.post(ENROLLMENTS, json={"honor_id": honor_id}, headers=member["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _progress(enrollment_id):
    return await fetch_all(
        "SELECT id, requirement_id, requirement_position, is_practical, status FROM requirement_progress"
        " WHERE enrollment_id = :e ORDER BY requirement_position",
        e=uuid.UUID(enrollment_id),
    )


async def _honor_row(honor_id):
    return await fetch_one("SELECT version, status, updated_at FROM honors WHERE id = :id", id=uuid.UUID(honor_id))


# ----------------------------------------------------------------------------
# 1. PUT: light fields in any status for MASTER_GC
# ----------------------------------------------------------------------------
async def test_master_light_edit_on_a_published_honor(client, staff, factory):
    honor = await _create(client, staff, factory, "light", publish=True)
    url = f"{HONORS}/{honor['id']}"
    before = await _honor_row(honor["id"])
    ids = [r["id"] for r in honor["requirements"]]
    media = settings.R2_PUBLIC_URL.rstrip("/")

    body = {
        "name": factory.name("light corregida"),
        "description": "Texto corregido",
        "difficulty_level": "ADVANCED",
        "exam_passing_score": 70,
        "wiki_title": "Knot Tying",
        "resources": [
            {"name": "Manual oficial", "url": f"{media}/resources/manual.pdf", "type": "PDF"},
            {"name": "Vídeo", "url": "https://youtu.be/dQw4w9WgXcQ", "type": "VIDEO"},
        ],
    }
    saved = await client.put(url, json=body, headers=staff["master"]["headers"])
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["status"] == "PUBLISHED" and out["version"] == honor["version"]
    assert out["name"] == body["name"] and out["description"] == "Texto corregido"
    assert out["exam_passing_score"] == 70 and out["difficulty_level"] == "ADVANCED"
    assert [r["name"] for r in out["resources"]] == ["Manual oficial", "Vídeo"]
    assert [r["id"] for r in out["requirements"]] == ids          # untouched
    after = await _honor_row(honor["id"])
    assert after["version"] == before["version"] and after["updated_at"] > before["updated_at"]

    # Still public: the catalogue shows the corrected name.
    public = await client.get(url)
    assert public.status_code == 200 and public.json()["name"] == body["name"]

    audit = await _audit(honor["id"])
    assert audit[-1]["action"] == "UPDATE"
    meta = audit[-1]["metadata_json"]
    assert meta["inplace"] is True
    assert meta["fields"] == sorted(body)
    assert set(meta["changed"]) == set(body)


async def test_master_cannot_replace_requirements_outside_draft(client, staff, factory):
    honor = await _create(client, staff, factory, "locked-reqs", publish=True)
    url = f"{HONORS}/{honor['id']}"
    audit_before = len(await _audit(honor["id"]))
    refused = await client.put(url, json={"name": factory.name("x2"), "requirements": [{"description": "Nuevo"}]},
                               headers=staff["master"]["headers"])
    assert refused.status_code == 400 and refused.json()["detail"] == "requirements_locked_use_inplace"
    # Nothing moved, nothing audited.
    detail = await _instructor(client, staff, honor["id"])
    assert detail["name"] == honor["name"]
    assert [r["id"] for r in detail["requirements"]] == [r["id"] for r in honor["requirements"]]
    assert len(await _audit(honor["id"])) == audit_before

    # `org_scope_id` is not a light field.
    scope = await client.put(url, json={"org_scope_id": staff["association"]["id"]},
                             headers=staff["master"]["headers"])
    assert scope.status_code == 400 and scope.json()["detail"] == LOCKED
    # Explicit nulls for the lists are no-ops, not a replacement.
    nulls = await client.put(url, json={"requirements": None, "description": "Con nulos"},
                             headers=staff["master"]["headers"])
    assert nulls.status_code == 200, nulls.text


async def test_master_light_edit_in_review_and_archived(client, staff, factory):
    in_review = await _create(client, staff, factory, "light-review")
    submitted = await client.post(f"{HONORS}/{in_review['id']}/submit", headers=staff["instructor"]["headers"])
    assert submitted.status_code == 200
    archived = await _create(client, staff, factory, "light-arch")
    assert (await client.delete(f"{HONORS}/{archived['id']}", headers=staff["master"]["headers"])).status_code == 204
    for honor, status in ((in_review, "ZONE_REVIEW"), (archived, "ARCHIVED")):
        saved = await client.put(f"{HONORS}/{honor['id']}", json={"description": "Ajuste"},
                                 headers=staff["master"]["headers"])
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == status and saved.json()["description"] == "Ajuste"


async def test_non_master_keeps_the_draft_only_rule(client, staff, factory):
    honor = await _create(client, staff, factory, "author-locked", publish=True)
    url = f"{HONORS}/{honor['id']}"
    for body in ({"description": "x"}, {"requirements": [{"description": "Nuevo"}]}, {"resources": []}):
        response = await client.put(url, json=body, headers=staff["instructor"]["headers"])
        assert response.status_code == 400, body
        assert response.json()["detail"] == LOCKED
    assert (await client.put(url, json={"description": "x"},
                             headers=staff["instructor2"]["headers"])).status_code == 403
    # A draft still takes the whole list (new ids): that is how the author builds it.
    draft = await _create(client, staff, factory, "author-draft")
    replaced = await client.put(f"{HONORS}/{draft['id']}", json={"requirements": [{"description": "Solo uno"}]},
                                headers=staff["instructor"]["headers"])
    assert replaced.status_code == 200
    assert [r["description"] for r in replaced.json()["requirements"]] == ["Solo uno"]
    assert "inplace" not in (await _audit(draft["id"]))[-1]["metadata_json"]


# ----------------------------------------------------------------------------
# 2. PATCH one requirement in place
# ----------------------------------------------------------------------------
async def test_patch_keeps_the_id_and_the_progress_rows(client, staff, factory):
    honor = await _create(client, staff, factory, "patch", publish=True)
    enrollment = await _enroll(client, staff["member"], honor["id"])
    progress_before = await _progress(enrollment["id"])
    before = await _honor_row(honor["id"])
    first, second, third = honor["requirements"]
    bank_before = (await _instructor(client, staff, honor["id"]))["requirements_with_questions"][1]["question_bank"]

    new_bank = [TF, {**MC, "question_text": "Otra pregunta"}]
    response = await client.patch(
        f"{HONORS}/{honor['id']}/requirements/{first['id']}",
        json={"description": "Hacer un as de guía con una mano", "instructions": "Con cuerda de 1 m",
              "is_theoretical": False, "question_bank": new_bank},
        headers=staff["master"]["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "requirements_with_questions" in body
    patched = body["requirements_with_questions"][0]
    assert patched["id"] == first["id"] and patched["position"] == 1
    assert patched["description"] == "Hacer un as de guía con una mano"
    assert patched["instructions"] == "Con cuerda de 1 m" and patched["is_theoretical"] is False
    assert [q["question_text"] for q in patched["question_bank"]] == [TF["question_text"], "Otra pregunta"]
    # Only that requirement's bank moved.
    assert body["requirements_with_questions"][1]["question_bank"] == bank_before
    assert [r["id"] for r in body["requirements"]] == [first["id"], second["id"], third["id"]]

    # The enrollment did not notice anything but the new text.
    assert await _progress(enrollment["id"]) == progress_before
    detail = (await client.get(f"{ENROLLMENTS}/{enrollment['id']}", headers=staff["member"]["headers"])).json()
    shown = next(r for r in detail["requirements"] if r["requirement_id"] == first["id"])
    assert shown["description"] == "Hacer un as de guía con una mano" and shown["is_practical"] is False

    after = await _honor_row(honor["id"])
    assert after["version"] == before["version"] and after["status"] == "PUBLISHED"
    assert after["updated_at"] > before["updated_at"]
    audit = (await _audit(honor["id"]))[-1]
    assert audit["action"] == "UPDATE"
    assert audit["metadata_json"]["inplace"] is True
    assert audit["metadata_json"]["requirement_id"] == first["id"]
    assert audit["metadata_json"]["fields"] == ["description", "instructions", "is_theoretical", "question_bank"]

    # A partial patch leaves the rest alone; instructions can be cleared.
    partial = await client.patch(f"{HONORS}/{honor['id']}/requirements/{first['id']}",
                                 json={"instructions": None}, headers=staff["master"]["headers"])
    assert partial.status_code == 200
    kept = partial.json()["requirements_with_questions"][0]
    assert kept["instructions"] is None and kept["description"] == "Hacer un as de guía con una mano"
    assert len(kept["question_bank"]) == 2


async def test_patch_permissions_and_validation(client, staff, factory):
    published = await _create(client, staff, factory, "patch-perm", publish=True)
    draft = await _create(client, staff, factory, "patch-draft")
    other = await _create(client, staff, factory, "patch-other")
    req = published["requirements"][0]["id"]
    url = f"{HONORS}/{published['id']}/requirements/{req}"

    locked = await client.patch(url, json={"description": "x"}, headers=staff["instructor"]["headers"])
    assert locked.status_code == 400 and locked.json()["detail"] == LOCKED
    for who in ("instructor2", "assoc_admin", "member"):
        assert (await client.patch(url, json={"description": "x"},
                                   headers=staff[who]["headers"])).status_code == 403, who
    assert (await client.patch(url, json={"description": "x"})).status_code == 401

    draft_req = draft["requirements"][0]["id"]
    own = await client.patch(f"{HONORS}/{draft['id']}/requirements/{draft_req}", json={"description": "Borrador"},
                             headers=staff["instructor"]["headers"])
    assert own.status_code == 200, own.text
    assert own.json()["requirements"][0]["id"] == draft_req

    # A requirement of another honor, or none at all, is a 404.
    assert (await client.patch(f"{HONORS}/{published['id']}/requirements/{other['requirements'][0]['id']}",
                               json={"description": "x"}, headers=staff["master"]["headers"])).status_code == 404
    assert (await client.patch(f"{HONORS}/{published['id']}/requirements/{uuid.uuid4()}",
                               json={"description": "x"}, headers=staff["master"]["headers"])).status_code == 404
    assert (await client.patch(f"{HONORS}/{uuid.uuid4()}/requirements/{req}",
                               json={"description": "x"}, headers=staff["master"]["headers"])).status_code == 404

    for bad in ({"description": ""}, {"question_bank": [{**MC, "correct_answer": "Ninguna"}]}):
        assert (await client.patch(url, json=bad, headers=staff["master"]["headers"])).status_code == 422, bad
    for bad in ({"description": None}, {"is_theoretical": None}, {"question_bank": None}, {}):
        response = await client.patch(url, json=bad, headers=staff["master"]["headers"])
        assert response.status_code == 400, bad


# ----------------------------------------------------------------------------
# 3. Add, reorder, delete
# ----------------------------------------------------------------------------
async def test_add_requirement(client, staff, factory):
    honor = await _create(client, staff, factory, "add", publish=True)
    url = f"{HONORS}/{honor['id']}/requirements"
    ids = [r["id"] for r in honor["requirements"]]

    appended = await client.post(url, json={"description": "Nuevo al final", "question_bank": [TF]},
                                 headers=staff["master"]["headers"])
    assert appended.status_code == 201, appended.text
    reqs = appended.json()["requirements_with_questions"]
    assert [r["id"] for r in reqs[:3]] == ids
    assert reqs[3]["description"] == "Nuevo al final" and reqs[3]["position"] == 4
    assert len(reqs[3]["question_bank"]) == 1
    assert appended.json()["version"] == honor["version"]

    first = await client.post(url, json={"description": "Nuevo primero", "position": 1},
                              headers=staff["master"]["headers"])
    assert first.status_code == 201
    listed = first.json()["requirements"]
    assert [r["description"] for r in listed] == [
        "Nuevo primero", "Hacer un as de guía", "Demostrar un amarre", "Atar tres nudos", "Nuevo al final"]
    assert [r["position"] for r in listed] == [1, 2, 3, 4, 5]
    assert [r["id"] for r in listed[1:4]] == ids

    assert (await client.post(url, json={"description": "x"},
                              headers=staff["instructor"]["headers"])).status_code == 400
    assert (await client.post(url, json={"description": "x"},
                              headers=staff["instructor2"]["headers"])).status_code == 403
    assert (await client.post(url, json={"description": ""},
                              headers=staff["master"]["headers"])).status_code == 422
    draft = await _create(client, staff, factory, "add-draft")
    own = await client.post(f"{HONORS}/{draft['id']}/requirements", json={"description": "Mío"},
                            headers=staff["instructor"]["headers"])
    assert own.status_code == 201
    meta = (await _audit(honor["id"]))[-1]["metadata_json"]
    assert meta["inplace"] is True and meta["operation"] == "requirement_added"


async def test_reorder_requirements(client, staff, factory):
    honor = await _create(client, staff, factory, "order", publish=True)
    url = f"{HONORS}/{honor['id']}/requirements/order"
    a, b, c = [r["id"] for r in honor["requirements"]]
    # The same list in English, imported: it follows the Spanish order.
    async with SessionLocal() as db:
        for position in (1, 2, 3):
            await db.execute(text(
                "INSERT INTO honor_requirements (id, honor_id, position, description, locale, source)"
                " VALUES (:id, :h, :p, :d, 'en', 'pathfinder-wiki')"),
                {"id": uuid.uuid4(), "h": uuid.UUID(honor["id"]), "p": position, "d": f"EN {position}"})
        await db.commit()

    enrollment = await _enroll(client, staff["member2"], honor["id"])
    progress_before = await _progress(enrollment["id"])
    before = await _honor_row(honor["id"])

    done = await client.put(url, json={"ids": [c, a, b]}, headers=staff["master"]["headers"])
    assert done.status_code == 200, done.text
    listed = done.json()["requirements"]
    assert [r["id"] for r in listed] == [c, a, b] and [r["position"] for r in listed] == [1, 2, 3]
    english = await fetch_all(
        "SELECT description FROM honor_requirements WHERE honor_id = :h AND locale = 'en' ORDER BY position",
        h=uuid.UUID(honor["id"]))
    assert [r["description"] for r in english] == ["EN 3", "EN 1", "EN 2"]
    # Progress rows keep their id link (and so their text); nothing else moved.
    assert await _progress(enrollment["id"]) == progress_before
    assert (await _honor_row(honor["id"]))["version"] == before["version"]
    meta = (await _audit(honor["id"]))[-1]["metadata_json"]
    assert meta["operation"] == "requirements_reordered" and meta["ids"] == [c, a, b]

    for bad in ([a, b], [a, b, c, a], [a, b, str(uuid.uuid4())], [a, a, b]):
        response = await client.put(url, json={"ids": bad}, headers=staff["master"]["headers"])
        assert response.status_code == 400, bad
        assert response.json()["detail"] == "requirement_order_mismatch"
    assert (await client.put(url, json={"ids": [a, b, c]},
                             headers=staff["instructor"]["headers"])).status_code == 400
    assert (await client.put(url, json={"ids": [a, b, c]},
                             headers=staff["member"]["headers"])).status_code == 403


async def test_delete_requirement(client, staff, factory):
    honor = await _create(client, staff, factory, "delete", publish=True)
    first, second, third = [r["id"] for r in honor["requirements"]]
    base = f"{HONORS}/{honor['id']}/requirements"

    # Nobody enrolled yet: the requirement and its questions go.
    gone = await client.delete(f"{base}/{first}", headers=staff["master"]["headers"])
    assert gone.status_code == 204, gone.text
    assert await fetch_all("SELECT id FROM honor_questions WHERE requirement_id = :r", r=uuid.UUID(first)) == []
    detail = await _instructor(client, staff, honor["id"])
    assert [r["id"] for r in detail["requirements"]] == [second, third]
    meta = (await _audit(honor["id"]))[-1]["metadata_json"]
    assert meta["operation"] == "requirement_deleted" and meta["requirement_id"] == first

    # Once a member has a progress row on it, it stays.
    await _enroll(client, staff["member"], honor["id"])
    in_use = await client.delete(f"{base}/{second}", headers=staff["master"]["headers"])
    assert in_use.status_code == 409 and in_use.json()["detail"] == "requirement_in_use"
    assert (await client.delete(f"{base}/{uuid.uuid4()}", headers=staff["master"]["headers"])).status_code == 404
    assert (await client.delete(f"{base}/{third}", headers=staff["instructor"]["headers"])).status_code == 400

    # A published honor never loses its last requirement.
    single = await _create(client, staff, factory, "delete-single", publish=True,
                           requirements=[{"description": "Único"}])
    last = await client.delete(f"{HONORS}/{single['id']}/requirements/{single['requirements'][0]['id']}",
                               headers=staff["master"]["headers"])
    assert last.status_code == 400 and last.json()["detail"] == "honor_without_requirements"

    # A draft: the author deletes freely.
    draft = await _create(client, staff, factory, "delete-draft")
    own = await client.delete(f"{HONORS}/{draft['id']}/requirements/{draft['requirements'][0]['id']}",
                              headers=staff["instructor"]["headers"])
    assert own.status_code == 204
    assert (await client.delete(f"{HONORS}/{draft['id']}/requirements/{draft['requirements'][1]['id']}",
                                headers=staff["instructor2"]["headers"])).status_code == 403


# ----------------------------------------------------------------------------
# 4. A published honor whose only list is not Spanish
# ----------------------------------------------------------------------------
async def test_instructor_detail_and_edits_on_an_english_only_list(client, staff, factory):
    honor_id = uuid.uuid4()
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, name, slug, active, status, code)"
            " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED', :code)"),
            {"id": honor_id, "m": ministry, "name": factory.name("solo-en"),
             "slug": f"{factory.prefix}-solo-en", "code": f"{factory.prefix}-solo-en"})
        for position in (1, 2):
            await db.execute(text(
                "INSERT INTO honor_requirements (honor_id, position, description, locale, source, license)"
                " VALUES (:h, :p, :d, 'en', 'pathfinder-wiki', 'CC BY-SA 3.0')"),
                {"h": honor_id, "p": position, "d": f"Tie knot {position}"})
        await db.commit()

    detail = await _instructor(client, staff, honor_id)
    assert detail["requirements_locale"] == "en"
    assert [r["description"] for r in detail["requirements_with_questions"]] == ["Tie knot 1", "Tie knot 2"]
    ids = [r["id"] for r in detail["requirements"]]

    added = await client.post(f"{HONORS}/{honor_id}/requirements", json={"description": "Tie knot 3"},
                              headers=staff["master"]["headers"])
    assert added.status_code == 201, added.text
    assert added.json()["requirements_locale"] == "en"
    assert [r["description"] for r in added.json()["requirements"]] == ["Tie knot 1", "Tie knot 2", "Tie knot 3"]
    stored = await fetch_all("SELECT locale FROM honor_requirements WHERE honor_id = :h", h=honor_id)
    assert {r["locale"] for r in stored} == {"en"}

    new_id = added.json()["requirements"][2]["id"]
    reordered = await client.put(f"{HONORS}/{honor_id}/requirements/order", json={"ids": [new_id, *ids]},
                                 headers=staff["master"]["headers"])
    assert reordered.status_code == 200, reordered.text
    assert [r["id"] for r in reordered.json()["requirements"]] == [new_id, *ids]

    patched = await client.patch(f"{HONORS}/{honor_id}/requirements/{ids[0]}", json={"description": "Tie a bowline"},
                                 headers=staff["master"]["headers"])
    assert patched.status_code == 200
    row = next(r for r in patched.json()["requirements"] if r["id"] == ids[0])
    # The imported attribution stays with the corrected text.
    assert row["description"] == "Tie a bowline" and row["license"] == "CC BY-SA 3.0"
