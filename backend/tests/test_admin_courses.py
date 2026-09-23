"""Cursos de la administración — admin.adventist.club/admin/cursos.

La Asociación (ADMIN_ASSOCIATION) y MASTER_GC escriben cursos igual que un INSTRUCTOR:
lecciones, plan y bancos de preguntas. Tres diferencias, y ninguna más:
  1. no se les pide la carta de iglesia (regla 4) en ningún paso;
  2. al enviar su curso se publica directamente (la institución ya es quien revisa), con
     las mismas validaciones del envío normal y un audit `PUBLISH` con `via: institutional`;
  3. MASTER, que no cuelga de ninguna organización, elige el ámbito del curso.

El INSTRUCTOR no cambia en nada. Además: `GET /courses/admin`, el listado de gestión.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db
from tests.test_courses import _courses_table_exists

COURSES = "/api/v1/courses"

TEXT_BLOCK = {"type": "text", "markdown": "## Nudos\nPractica el nudo llano."}
MC = {
    "question_text": "¿Cuál es el nudo llano?",
    "question_type": "MULTIPLE_CHOICE",
    "options": ["El de la izquierda", "RESPUESTA-SECRETA-ADMIN"],
    "correct_answer": "RESPUESTA-SECRETA-ADMIN",
    "points": 2,
}
MC_OTHER = {**MC, "question_text": "¿Y el ballestrinque?"}

pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _courses_table_exists(),
        reason="apply migrations/009b_courses.sql and 010_exams.sql to the test database",
    ),
]
factory = module_factory("admin-courses")


# ----------------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------------
async def _authorize(user_id: str, organization_id: str) -> None:
    letter_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO church_letters (id, user_id, organization_id, church_name, storage_key,"
                " content_type, size_bytes, status) VALUES (:id, :user, :org, 'Iglesia', :key,"
                " 'application/pdf', 1024, 'AUTHORIZED')"
            ),
            {"id": letter_id, "user": uuid.UUID(user_id), "org": uuid.UUID(organization_id),
             "key": f"letters/{user_id}/{letter_id}.pdf"},
        )
        await db.execute(
            text(
                "UPDATE users SET verification_status = 'VERIFIED', child_protection_completed = true"
                " WHERE id = :id"
            ),
            {"id": uuid.UUID(user_id)},
        )
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("Asociación Central", "association")
    zone = await factory.org("zone", "zone", association)
    club = await factory.org("club", "club", association)
    other_association = await factory.org("other-assoc", "association")
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "unverified": await factory.user("unverified", "INSTRUCTOR", club["id"]),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "other_admin": await factory.user("other-admin", "ADMIN_ASSOCIATION", other_association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "student": await factory.user("student", "STUDENT", club["id"]),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
    }
    await _authorize(people["instructor"]["id"], club["id"])
    return {**people, "association": association, "zone": zone, "club": club,
            "other_association": other_association}


async def _honor(factory, label, theoretical=(True, False), *, name=None) -> dict:
    """A published honor with one Spanish requirement per flag (False = practical)."""
    honor_id, name = uuid.uuid4(), name or factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(
            text(
                "INSERT INTO honors (id, ministry_id, name, slug, active, status)"
                " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED')"
            ),
            {"id": honor_id, "m": ministry, "name": name,
             "slug": f"{factory.prefix}-{label}-{honor_id.hex[:6]}"},
        )
        for position, is_theoretical in enumerate(theoretical, start=1):
            await db.execute(
                text(
                    "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale)"
                    " VALUES (:h, :p, :d, :t, 'es')"
                ),
                {"h": honor_id, "p": position, "d": f"Requisito {position}", "t": is_theoretical},
            )
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _create(client, user, honor, **extra):
    body = {"honor_id": honor["id"], "title": "Nudos de la Asociación", **extra}
    return await client.post(COURSES, json=body, headers=user["headers"])


async def _course(client, user, honor, **extra) -> dict:
    response = await _create(client, user, honor, **extra)
    assert response.status_code == 201, response.text
    return response.json()


async def _add_lesson(client, user, course_id, title="Lección 1", blocks=None):
    body = {"title": title, "blocks": blocks if blocks is not None else [TEXT_BLOCK]}
    return await client.post(f"{COURSES}/{course_id}/lessons", json=body, headers=user["headers"])


async def _submit(client, user, course_id):
    return await client.post(f"{COURSES}/{course_id}/submit", headers=user["headers"])


async def _audit(course_id, action) -> dict | None:
    return await fetch_one(
        "SELECT action, user_id, metadata_json FROM audit_log WHERE entity_id = :id"
        " AND action = :action ORDER BY created_at DESC LIMIT 1",
        id=str(course_id), action=action,
    )


async def _admin_list(client, user, **params):
    return await client.get(f"{COURSES}/admin", params=params, headers=user["headers"])


# ----------------------------------------------------------------------------
# MASTER_GC: writes, and publishes without a letter or a review
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_master_writes_a_whole_course_and_publishes_it_directly(client, world, factory):
    honor = await _honor(factory, "master-flow", (True, False))
    course = await _course(client, world["master"], honor, org_scope_id=world["association"]["id"])
    assert course["status"] == "DRAFT"
    course_id = course["id"]
    headers = world["master"]["headers"]

    added = await _add_lesson(client, world["master"], course_id)
    assert added.status_code == 201, added.text
    lesson_id = added.json()["id"]
    edited = await client.put(f"{COURSES}/{course_id}/lessons/{lesson_id}",
                              json={"title": "Editada", "blocks": [TEXT_BLOCK]}, headers=headers)
    assert edited.status_code == 200, edited.text
    second = await _add_lesson(client, world["master"], course_id, "Dos")
    reordered = await client.put(f"{COURSES}/{course_id}/lessons/order",
                                 json={"lesson_ids": [second.json()["id"], lesson_id]}, headers=headers)
    assert reordered.status_code == 200, reordered.text

    bank = await client.put(f"{COURSES}/{course_id}/requirements/1/questions",
                            json={"draw_count": 1, "question_bank": [MC, MC_OTHER]}, headers=headers)
    assert bank.status_code == 200, bank.text
    plan = await client.put(f"{COURSES}/{course_id}/plan",
                            json=[{"position": 1, "assessment": "EXAM"},
                                  {"position": 2, "assessment": "EVIDENCE"}], headers=headers)
    assert plan.status_code == 200, plan.text

    published = await _submit(client, world["master"], course_id)
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["status"] == "PUBLISHED"
    assert body["published_at"] is not None
    assert body["instructor_verified"] is True
    assert body["approved_by"] == factory.name("Asociación Central")

    audit = await _audit(course_id, "PUBLISH")
    assert audit is not None
    assert str(audit["user_id"]) == world["master"]["id"]
    assert audit["metadata_json"]["via"] == "institutional"
    assert "SUBMIT" not in [
        row["action"] for row in await fetch_all(
            "SELECT action FROM audit_log WHERE entity_id = :id", id=course_id)
    ]
    # It never went through the review queues, and nothing was written to the review history.
    assert body["review_history"] == []

    # The shop window shows it, and the public page never carries the answers.
    listed = await client.get(f"{COURSES}?honor_id={honor['id']}")
    assert course_id in [row["id"] for row in listed.json()]
    public = await client.get(f"{COURSES}/{course_id}", headers=world["student"]["headers"])
    assert public.status_code == 200
    assert "RESPUESTA-SECRETA-ADMIN" not in public.text
    assert "question_banks" not in public.json()


@pytest.mark.asyncio
async def test_master_without_an_organization_must_choose_the_scope(client, world, factory):
    honor = await _honor(factory, "master-scope")
    refused = await _create(client, world["master"], honor)
    assert refused.status_code == 409
    missing = await _create(client, world["master"], honor, org_scope_id=str(uuid.uuid4()))
    assert missing.status_code == 404


# ----------------------------------------------------------------------------
# ADMIN_ASSOCIATION: the same, inside their own subtree
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_association_admin_writes_and_publishes_without_a_letter(client, world, factory):
    honor = await _honor(factory, "assoc-flow")
    course = await _course(client, world["assoc_admin"], honor)
    headers = world["assoc_admin"]["headers"]

    # The plan, the lessons and the course fields: every authoring step is theirs.
    assert (await client.put(f"{COURSES}/{course['id']}", json={"summary": "Resumen"},
                             headers=headers)).status_code == 200
    assert (await _add_lesson(client, world["assoc_admin"], course["id"])).status_code == 201
    assert (await client.put(f"{COURSES}/{course['id']}/plan",
                             json=[{"position": 1, "assessment": "REVIEW"},
                                   {"position": 2, "assessment": "EVIDENCE"}],
                             headers=headers)).status_code == 200

    published = await _submit(client, world["assoc_admin"], course["id"])
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "PUBLISHED"
    assert (await _audit(course["id"], "PUBLISH"))["metadata_json"]["via"] == "institutional"

    # Operating it needs no letter either: seats, enrolment and the in-person session.
    operation = await client.patch(f"{COURSES}/{course['id']}/operation",
                                   json={"capacity": 30}, headers=headers)
    assert operation.status_code == 200, operation.text
    session = await client.post(f"{COURSES}/{course['id']}/exam-session", headers=headers)
    assert session.status_code == 200, session.text

    # A member joins it, and the author sees the roster.
    joined = await client.post(f"{COURSES}/{course['id']}/join", headers=world["student"]["headers"])
    assert joined.status_code == 200, joined.text
    members = await client.get(f"{COURSES}/{course['id']}/members", headers=headers)
    assert members.status_code == 200, members.text
    assert [row["member"]["id"] for row in members.json()] == [world["student"]["id"]]


@pytest.mark.asyncio
async def test_institutional_publication_keeps_every_submit_validation(client, world, factory):
    honor = await _honor(factory, "assoc-empty")
    course = await _course(client, world["assoc_admin"], honor)
    empty = await _submit(client, world["assoc_admin"], course["id"])
    assert empty.status_code == 400
    assert "lección" in empty.json()["detail"]
    detail = await client.get(f"{COURSES}/{course['id']}/instructor",
                              headers=world["assoc_admin"]["headers"])
    assert detail.json()["status"] == "DRAFT"
    assert await _audit(course["id"], "PUBLISH") is None


@pytest.mark.asyncio
async def test_association_admin_publishes_only_inside_their_scope(client, world, factory):
    honor = await _honor(factory, "assoc-scope")
    outside = await _create(client, world["assoc_admin"], honor,
                            org_scope_id=world["other_association"]["id"])
    assert outside.status_code == 403
    inside = await _course(client, world["assoc_admin"], honor, org_scope_id=world["club"]["id"])
    assert (await _add_lesson(client, world["assoc_admin"], inside["id"])).status_code == 201

    # The admin moves to another association: the course stays theirs, the publication does not.
    async with SessionLocal() as db:
        await db.execute(text("UPDATE users SET organization_id = :org WHERE id = :id"),
                         {"org": uuid.UUID(world["other_association"]["id"]),
                          "id": uuid.UUID(world["assoc_admin"]["id"])})
        await db.commit()
    try:
        assert (await _submit(client, world["assoc_admin"], inside["id"])).status_code == 403
    finally:
        async with SessionLocal() as db:
            await db.execute(text("UPDATE users SET organization_id = :org WHERE id = :id"),
                             {"org": uuid.UUID(world["association"]["id"]),
                              "id": uuid.UUID(world["assoc_admin"]["id"])})
            await db.commit()
    assert (await _submit(client, world["assoc_admin"], inside["id"])).status_code == 200


# ----------------------------------------------------------------------------
# The author is the author, whatever the role
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nobody_else_edits_an_institutional_course(client, world, factory):
    honor = await _honor(factory, "author-only", (True, True))
    course = await _course(client, world["master"], honor, org_scope_id=world["association"]["id"])
    url = f"{COURSES}/{course['id']}"
    for who in ("assoc_admin", "zone_coordinator", "other_admin", "instructor", "student"):
        headers = world[who]["headers"]
        attempts = [
            await _add_lesson(client, world[who], course["id"]),
            await client.put(f"{url}/plan", json=[{"position": 1, "assessment": "REVIEW"},
                                                  {"position": 2, "assessment": "REVIEW"}],
                             headers=headers),
            await client.put(f"{url}/requirements/1/questions",
                             json={"draw_count": 1, "question_bank": [MC]}, headers=headers),
            await client.put(url, json={"title": "Ajeno"}, headers=headers),
            await _submit(client, world[who], course["id"]),
        ]
        for response in attempts:
            assert response.status_code in (403, 404), f"{who}: {response.status_code} {response.text}"

    # ...and the institutional author never touches an instructor's course either.
    instructor_course = await _course(client, world["instructor"], honor)
    for who in ("assoc_admin", "master"):
        response = await _add_lesson(client, world[who], instructor_course["id"])
        assert response.status_code in (403, 404), f"{who}: {response.status_code}"


# ----------------------------------------------------------------------------
# INSTRUCTOR: nothing changes
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_instructor_still_needs_the_letter_and_the_review(client, world, factory):
    honor = await _honor(factory, "instructor-flow")
    blocked = await _course(client, world["unverified"], honor)
    assert (await _add_lesson(client, world["unverified"], blocked["id"])).status_code == 201
    assert (await _submit(client, world["unverified"], blocked["id"])).status_code == 403

    course = await _course(client, world["instructor"], honor)
    assert (await _add_lesson(client, world["instructor"], course["id"])).status_code == 201
    sent = await _submit(client, world["instructor"], course["id"])
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "ZONE_REVIEW"
    assert await _audit(course["id"], "PUBLISH") is None

    # An instructor never picks the scope of the course: that decides who reviews it.
    elsewhere = await _create(client, world["instructor"], await _honor(factory, "instr-scope"),
                              org_scope_id=world["other_association"]["id"])
    assert elsewhere.status_code == 403


@pytest.mark.asyncio
async def test_create_and_my_created_are_open_to_the_author_roles_only(client, world, factory):
    honor = await _honor(factory, "roles")
    mine = await _course(client, world["assoc_admin"], honor)
    master_course = await _course(client, world["master"], honor, org_scope_id=world["club"]["id"])

    for who, course in (("assoc_admin", mine), ("master", master_course)):
        listed = await client.get(f"{COURSES}/my/created", headers=world[who]["headers"])
        assert listed.status_code == 200, listed.text
        ids = [row["id"] for row in listed.json()["items"]]
        assert course["id"] in ids
        drafts = await client.get(f"{COURSES}/my/created?status=PUBLISHED",
                                  headers=world[who]["headers"])
        assert course["id"] not in [row["id"] for row in drafts.json()["items"]]

    for who in ("zone_coordinator", "director", "student"):
        assert (await _create(client, world[who], honor)).status_code == 403, who
        assert (await client.get(f"{COURSES}/my/created",
                                 headers=world[who]["headers"])).status_code == 403, who


# ----------------------------------------------------------------------------
# GET /courses/admin
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_admin_list_is_scoped_filtered_and_counted(client, world, factory):
    tag = f"{factory.prefix}-lst"
    honor = await _honor(factory, "admin-list", name=f"{tag} Árboles Frutales")
    other_honor = await _honor(factory, "admin-list-2", name=f"{tag} otra")
    by_instructor = await _course(client, world["instructor"], honor, title="Curso de nudos")
    by_admin = await _course(client, world["assoc_admin"], other_honor, title="Óptica básica")
    assert (await _add_lesson(client, world["assoc_admin"], by_admin["id"])).status_code == 201
    assert (await _submit(client, world["assoc_admin"], by_admin["id"])).status_code == 200
    by_other = await _course(client, world["other_admin"], honor, title="Curso de fuera")

    q = tag
    listed = await _admin_list(client, world["assoc_admin"], q=q)
    assert listed.status_code == 200, listed.text
    ids = [row["id"] for row in listed.json()]
    assert by_instructor["id"] in ids and by_admin["id"] in ids
    assert by_other["id"] not in ids
    assert listed.headers["X-Total-Count"] == str(len(ids))

    # Newest change first.
    stamps = [row["updated_at"] for row in listed.json()]
    assert stamps == sorted(stamps, reverse=True)

    row = next(item for item in listed.json() if item["id"] == by_instructor["id"])
    assert row["instructor"] == {
        "id": world["instructor"]["id"], "name": factory.name("instructor"),
        "email": world["instructor"]["email"], "role": "INSTRUCTOR",
    }
    assert row["association"]["id"] == world["association"]["id"]
    assert row["association"]["name"] == factory.name("Asociación Central")
    assert row["enrolled_count"] == 0
    assert row["status"] == "DRAFT"
    assert row["honor"]["id"] == honor["id"]

    # Filters: status (exact values, lower-case too, and "all"), instructor, association.
    published = await _admin_list(client, world["assoc_admin"], q=q, status="PUBLISHED")
    assert [item["id"] for item in published.json()] == [by_admin["id"]]
    drafts = await _admin_list(client, world["assoc_admin"], q=q, status="draft")
    assert [item["id"] for item in drafts.json()] == [by_instructor["id"]]
    everything = await _admin_list(client, world["assoc_admin"], q=q, status="all")
    assert everything.headers["X-Total-Count"] == listed.headers["X-Total-Count"]
    assert (await _admin_list(client, world["assoc_admin"], status="LIVE")).status_code == 422
    own = await _admin_list(client, world["assoc_admin"], q=q, instructor_id=world["assoc_admin"]["id"])
    assert [item["id"] for item in own.json()] == [by_admin["id"]]

    # Accent-insensitive on the course title and the honor name.
    optica = await _admin_list(client, world["assoc_admin"], q="optica basica")
    assert by_admin["id"] in [item["id"] for item in optica.json()]
    arboles = await _admin_list(client, world["assoc_admin"], q=f"{q} arboles")
    assert [item["id"] for item in arboles.json()] == [by_instructor["id"]]

    # Pagination keeps the total.
    page = await _admin_list(client, world["assoc_admin"], q=q, limit=1, offset=1)
    assert len(page.json()) == 1
    assert page.headers["X-Total-Count"] == listed.headers["X-Total-Count"]
    assert (await _admin_list(client, world["assoc_admin"], limit=201)).status_code == 422

    # MASTER sees everything and filters by association; a zone coordinator reads their association.
    master_all = await _admin_list(client, world["master"], q=q)
    assert {by_instructor["id"], by_admin["id"], by_other["id"]} <= {
        item["id"] for item in master_all.json()}
    outside = await _admin_list(client, world["master"], q=q,
                                association_id=world["other_association"]["id"])
    assert [item["id"] for item in outside.json()] == [by_other["id"]]
    zone = await _admin_list(client, world["zone_coordinator"], q=q)
    assert by_instructor["id"] in [item["id"] for item in zone.json()]  # legacy club, no zone
    assert by_other["id"] not in [item["id"] for item in zone.json()]

    # ...but never what hangs from ANOTHER zone of the same association (the rows carry e-mails).
    far_zone = await factory.org("far-zone", "zone", world["association"])
    far_club = await factory.org("far-club", "club", far_zone)
    far_instructor = await factory.user("far-instructor", "INSTRUCTOR", far_club["id"])
    far = await _course(client, far_instructor, honor, title="Curso lejano")
    assert far["id"] not in [
        item["id"] for item in (await _admin_list(client, world["zone_coordinator"], q=q)).json()]
    assert far["id"] in [
        item["id"] for item in (await _admin_list(client, world["assoc_admin"], q=q)).json()]
    # An association filter outside the caller's scope finds nothing, never a leak.
    leak = await _admin_list(client, world["assoc_admin"], q=q,
                             association_id=world["other_association"]["id"])
    assert leak.json() == [] and leak.headers["X-Total-Count"] == "0"


@pytest.mark.asyncio
async def test_the_admin_list_is_for_reviewers_only(client, world):
    for who in ("instructor", "director", "student"):
        assert (await _admin_list(client, world[who])).status_code == 403, who
    assert (await client.get(f"{COURSES}/admin")).status_code in (401, 403)
