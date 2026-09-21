"""Bloque B · I2 — Cursos del instructor virtual: autoría, revisión y publicación.

Un curso es la oferta que un instructor verificado hace de una versión publicada de una
especialidad en un idioma: lecciones con bloques de contenido y un plan de evaluación por
requisito que nunca es más laxo que la especialidad. Reutiliza el flujo y los revisores de
las especialidades (zona -> Asociación) y su historial (`honor_reviews`).

La inscripción a cursos, el dictamen del instructor y los exámenes son I3 en adelante.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

COURSES = "/api/v1/courses"
HONORS = "/api/v1/honors"

TEXT_BLOCK = {"type": "text", "markdown": "## Nudos\nPractica el nudo llano."}
IMAGE_BLOCK = {
    "type": "image",
    "url": "https://media.adventist.club/courses/nudo.jpg",
    "alt": "Un nudo llano terminado",
    "caption": "Nudo llano",
}
PDF_BLOCK = {"type": "pdf", "url": "https://media.adventist.club/courses/guia.pdf", "name": "Guía"}
VIDEO_BLOCK = {
    "type": "video",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "Cómo atar un nudo llano",
}


def _courses_table_exists() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(await db.scalar(text("SELECT to_regclass('public.courses') IS NOT NULL")))
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _courses_table_exists(),
        reason="apply migrations/009_church_letters.sql and 009b_courses.sql to the test database",
    ),
]
factory = module_factory("courses")


# ----------------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------------
async def _authorize(user_id: str, organization_id: str) -> None:
    """The letter that `instructor_is_verified` asks for, written straight to the table:
    its own workflow is covered by tests/test_church_letters.py."""
    letter_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO church_letters (id, user_id, organization_id, church_name, storage_key,"
                " content_type, size_bytes, status) VALUES (:id, :user, :org, 'Iglesia', :key,"
                " 'application/pdf', 1024, 'AUTHORIZED')"
            ),
            {
                "id": letter_id,
                "user": uuid.UUID(user_id),
                "org": uuid.UUID(organization_id),
                "key": f"letters/{user_id}/{letter_id}.pdf",
            },
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
    association = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", association)
    club = await factory.org("club", "club", association)
    other_association = await factory.org("other-assoc", "association")
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "unverified": await factory.user("unverified", "INSTRUCTOR", club["id"]),
        "orphan": await factory.user("orphan", "INSTRUCTOR"),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "other_admin": await factory.user("other-admin", "ADMIN_ASSOCIATION", other_association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "student": await factory.user("student", "STUDENT", club["id"]),
    }
    await _authorize(people["instructor"]["id"], club["id"])
    return {**people, "association": association, "zone": zone, "club": club}


async def _honor(factory, label, theoretical=(True, False), *, status="PUBLISHED", english=False) -> dict:
    """A published honor with one Spanish requirement per flag (False = practical)."""
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(
            text(
                "INSERT INTO honors (id, ministry_id, name, slug, active, status)"
                " VALUES (:id, :m, :name, :slug, true, :status)"
            ),
            {"id": honor_id, "m": ministry, "name": name,
             "slug": f"{factory.prefix}-{label}-{honor_id.hex[:6]}", "status": status},
        )
        for locale in ("es", "en") if english else ("es",):
            for position, is_theoretical in enumerate(theoretical, start=1):
                await db.execute(
                    text(
                        "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale)"
                        " VALUES (:h, :p, :d, :t, :l)"
                    ),
                    {"h": honor_id, "p": position, "d": f"{locale.upper()} requisito {position}",
                     "t": is_theoretical, "l": locale},
                )
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _create(client, user, honor, **extra):
    body = {"honor_id": honor["id"], "title": "Nudos con el Inst. Pérez", **extra}
    return await client.post(COURSES, json=body, headers=user["headers"])


async def _course(client, user, honor, **extra) -> dict:
    response = await _create(client, user, honor, **extra)
    assert response.status_code == 201, response.text
    return response.json()


async def _add_lesson(client, user, course_id, title="Lección 1", blocks=None, **extra):
    body = {"title": title, "blocks": blocks if blocks is not None else [TEXT_BLOCK], **extra}
    return await client.post(f"{COURSES}/{course_id}/lessons", json=body, headers=user["headers"])


async def _ready_draft(client, world, honor, **extra) -> dict:
    """A draft that passes every `submit` validation."""
    course = await _course(client, world["instructor"], honor, **extra)
    added = await _add_lesson(client, world["instructor"], course["id"], blocks=[TEXT_BLOCK, IMAGE_BLOCK])
    assert added.status_code == 201, added.text
    return course


async def _submit(client, user, course_id):
    return await client.post(f"{COURSES}/{course_id}/submit", headers=user["headers"])


async def _review(client, reviewer, course_id, action="APPROVE", **extra):
    return await client.post(
        f"{COURSES}/{course_id}/review", json={"action": action, **extra}, headers=reviewer["headers"]
    )


async def _publish(client, world, course_id) -> dict:
    assert (await _submit(client, world["instructor"], course_id)).status_code == 200
    assert (await _review(client, world["zone_coordinator"], course_id)).status_code == 200
    done = await _review(client, world["assoc_admin"], course_id)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "PUBLISHED"
    return done.json()


async def _published_course(client, world, honor, **extra) -> dict:
    course = await _ready_draft(client, world, honor, **extra)
    return await _publish(client, world, course["id"])


async def _get(client, user, course_id, suffix=""):
    return await client.get(
        f"{COURSES}/{course_id}{suffix}", headers=user["headers"] if user else None
    )


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id)
    )
    return [row["action"] for row in rows]


def _plan_by_position(course: dict) -> dict:
    return {item["position"]: item for item in course["plan"]}


# ----------------------------------------------------------------------------
# Creating a course: the plan is preloaded from the honor
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_preloads_the_plan_from_the_honor(client, world, factory):
    honor = await _honor(factory, "plan", (True, False, True))
    course = await _course(client, world["instructor"], honor, summary="Un curso corto")

    assert course["status"] == "DRAFT"
    assert course["version"] == 1
    assert course["locale"] == "es"
    assert course["instructor"]["id"] == world["instructor"]["id"]
    plan = _plan_by_position(course)
    # Practical in the honor => EVIDENCE; the rest start as REVIEW (EXAM arrives with I4).
    assert [plan[p]["assessment"] for p in (1, 2, 3)] == ["REVIEW", "EVIDENCE", "REVIEW"]
    assert plan[2]["is_practical"] is True
    assert plan[1]["description"] == "ES requisito 1"
    assert "CREATE" in await _audit_actions(course["id"])


@pytest.mark.asyncio
async def test_a_draft_does_not_need_the_verification_gate(client, world, factory):
    """The instructor prepares the course while the letter is still being validated."""
    honor = await _honor(factory, "draft-gate")
    assert (await _create(client, world["unverified"], honor)).status_code == 201


@pytest.mark.asyncio
async def test_create_refuses_what_it_cannot_offer(client, world, factory):
    honor = await _honor(factory, "refuse")
    draft_honor = await _honor(factory, "unpublished", status="DRAFT")

    assert (await _create(client, world["student"], honor)).status_code == 403
    assert (await _create(client, world["instructor"], draft_honor)).status_code == 404
    assert (await _create(client, world["orphan"], honor)).status_code == 409  # no organization
    assert (await _create(client, world["instructor"], honor, locale="en")).status_code == 409

    assert (await _create(client, world["instructor"], honor)).status_code == 201
    duplicate = await _create(client, world["instructor"], honor)
    assert duplicate.status_code == 409  # one live course per honor version and language


@pytest.mark.asyncio
async def test_two_instructors_may_offer_the_same_honor(client, world, factory):
    honor = await _honor(factory, "shared")
    assert (await _create(client, world["instructor"], honor)).status_code == 201
    assert (await _create(client, world["unverified"], honor)).status_code == 201


# ----------------------------------------------------------------------------
# Lessons and content blocks
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_blocks_are_validated_and_video_keeps_only_provider_and_id(client, world, factory):
    honor = await _honor(factory, "blocks")
    course = await _course(client, world["instructor"], honor)
    added = await _add_lesson(
        client, world["instructor"], course["id"],
        blocks=[TEXT_BLOCK, IMAGE_BLOCK, PDF_BLOCK, VIDEO_BLOCK], requirement_positions=[1, 2],
    )
    assert added.status_code == 201, added.text
    lesson = added.json()
    assert lesson["position"] == 1
    assert lesson["requirement_positions"] == [1, 2]
    assert [block["type"] for block in lesson["blocks"]] == ["text", "image", "pdf", "video"]
    assert all(block["id"] for block in lesson["blocks"])  # every block gets a stable short id

    video = lesson["blocks"][3]
    assert video["provider"] == "youtube"
    assert video["video_id"] == "dQw4w9WgXcQ"
    assert "url" not in video  # the URL is not stored, only provider + id


@pytest.mark.asyncio
async def test_unsafe_or_foreign_content_is_refused(client, world, factory):
    honor = await _honor(factory, "unsafe")
    course = await _course(client, world["instructor"], honor)

    async def refused(block):
        response = await _add_lesson(client, world["instructor"], course["id"], blocks=[block])
        assert response.status_code == 422, response.text

    await refused({"type": "text", "markdown": "Hola <script>alert(1)</script>"})
    await refused({"type": "text", "markdown": '<img src=x onerror="alert(1)">'})
    await refused({**IMAGE_BLOCK, "url": "https://evil.example.com/nudo.jpg"})
    await refused({**IMAGE_BLOCK, "alt": ""})  # alt text is mandatory
    await refused({**PDF_BLOCK, "url": "https://media.adventist.club.evil.com/g.pdf"})
    await refused({"type": "video", "url": "https://tiktok.com/v/123", "title": "Nudos"})
    await refused({"type": "video", "url": "https://www.youtube.com/watch", "title": "Nudos"})


@pytest.mark.asyncio
async def test_lessons_are_capped_in_number_size_and_blocks(client, world, factory):
    honor = await _honor(factory, "caps")
    course = await _course(client, world["instructor"], honor)

    too_many_blocks = await _add_lesson(
        client, world["instructor"], course["id"], blocks=[TEXT_BLOCK] * 41
    )
    assert too_many_blocks.status_code == 422

    heavy = {"type": "text", "markdown": "x" * 19_000}
    too_heavy = await _add_lesson(client, world["instructor"], course["id"], blocks=[heavy] * 12)
    assert too_heavy.status_code == 413

    # A course holds 30 lessons; the rows go in directly because 30 requests prove nothing.
    async with SessionLocal() as db:
        for position in range(1, 31):
            await db.execute(
                text(
                    "INSERT INTO course_lessons (course_id, position, title, blocks)"
                    " VALUES (:course, :position, :title, '[]'::jsonb)"
                ),
                {"course": uuid.UUID(course["id"]), "position": position, "title": f"L{position}"},
            )
        await db.commit()
    full = await _add_lesson(client, world["instructor"], course["id"], "La 31")
    assert full.status_code == 409


@pytest.mark.asyncio
async def test_lessons_can_be_edited_reordered_and_removed(client, world, factory):
    honor = await _honor(factory, "lessons")
    course = await _course(client, world["instructor"], honor)
    headers = world["instructor"]["headers"]
    first = (await _add_lesson(client, world["instructor"], course["id"], "Uno")).json()
    second = (await _add_lesson(client, world["instructor"], course["id"], "Dos")).json()
    assert [first["position"], second["position"]] == [1, 2]

    edited = await client.put(
        f"{COURSES}/{course['id']}/lessons/{first['id']}",
        json={"title": "Uno corregido", "blocks": [TEXT_BLOCK, PDF_BLOCK]},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Uno corregido"
    assert len(edited.json()["blocks"]) == 2

    reordered = await client.put(
        f"{COURSES}/{course['id']}/lessons/order",
        json={"lesson_ids": [second["id"], first["id"]]},
        headers=headers,
    )
    assert reordered.status_code == 200, reordered.text
    assert [lesson["title"] for lesson in reordered.json()["lessons"]] == ["Dos", "Uno corregido"]

    incomplete = await client.put(
        f"{COURSES}/{course['id']}/lessons/order", json={"lesson_ids": [first["id"]]}, headers=headers
    )
    assert incomplete.status_code == 422

    removed = await client.delete(f"{COURSES}/{course['id']}/lessons/{first['id']}", headers=headers)
    assert removed.status_code == 204
    detail = await _get(client, world["instructor"], course["id"], "/instructor")
    assert [lesson["title"] for lesson in detail.json()["lessons"]] == ["Dos"]


@pytest.mark.asyncio
async def test_only_the_author_edits_the_course(client, world, factory):
    honor = await _honor(factory, "author-only")
    course = await _course(client, world["instructor"], honor)
    for who in ("unverified", "assoc_admin", "student"):
        response = await _add_lesson(client, world[who], course["id"])
        assert response.status_code in (403, 404), f"{who}: {response.status_code}"


# ----------------------------------------------------------------------------
# The evaluation plan is never more lenient than the honor
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_plan_cannot_relax_a_practical_requirement(client, world, factory):
    honor = await _honor(factory, "strict", (True, False))
    course = await _course(client, world["instructor"], honor)
    url = f"{COURSES}/{course['id']}/plan"
    headers = world["instructor"]["headers"]

    relaxed = await client.put(
        url,
        json=[{"position": 1, "assessment": "REVIEW"}, {"position": 2, "assessment": "REVIEW"}],
        headers=headers,
    )
    assert relaxed.status_code == 422
    assert "2" in relaxed.json()["detail"]

    # Stricter than the honor is fine: a theoretical requirement may ask for evidence.
    stricter = await client.put(
        url,
        json=[
            {"position": 1, "assessment": "EVIDENCE", "guidance": "Sube una foto del nudo"},
            {"position": 2, "assessment": "EVIDENCE"},
        ],
        headers=headers,
    )
    assert stricter.status_code == 200, stricter.text
    plan = _plan_by_position(stricter.json())
    assert plan[1]["assessment"] == "EVIDENCE"
    assert plan[1]["guidance"] == "Sube una foto del nudo"


@pytest.mark.asyncio
async def test_the_plan_must_cover_every_requirement_exactly_once(client, world, factory):
    honor = await _honor(factory, "complete-plan", (True, True))
    course = await _course(client, world["instructor"], honor)
    url = f"{COURSES}/{course['id']}/plan"
    headers = world["instructor"]["headers"]

    missing = await client.put(url, json=[{"position": 1, "assessment": "REVIEW"}], headers=headers)
    assert missing.status_code == 422

    unknown = await client.put(
        url,
        json=[{"position": 1, "assessment": "REVIEW"}, {"position": 2, "assessment": "REVIEW"},
              {"position": 9, "assessment": "REVIEW"}],
        headers=headers,
    )
    assert unknown.status_code == 422

    repeated = await client.put(
        url,
        json=[{"position": 1, "assessment": "REVIEW"}, {"position": 1, "assessment": "REVIEW"}],
        headers=headers,
    )
    assert repeated.status_code == 422


@pytest.mark.asyncio
async def test_exam_is_not_offered_until_the_question_bank_exists(client, world, factory):
    """I4 opens `EXAM`; until then the API only accepts the two human verdicts."""
    honor = await _honor(factory, "no-exam", (True,))
    course = await _course(client, world["instructor"], honor)
    response = await client.put(
        f"{COURSES}/{course['id']}/plan",
        json=[{"position": 1, "assessment": "EXAM", "draw_count": 5}],
        headers=world["instructor"]["headers"],
    )
    assert response.status_code == 422


# ----------------------------------------------------------------------------
# Submitting for review
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_lists_what_is_missing(client, world, factory):
    honor = await _honor(factory, "submit-empty")
    course = await _course(client, world["instructor"], honor)

    empty = await _submit(client, world["instructor"], course["id"])
    assert empty.status_code == 400
    assert "lección" in empty.json()["detail"]

    without_blocks = await _add_lesson(client, world["instructor"], course["id"], blocks=[])
    assert without_blocks.status_code == 201
    still_empty = await _submit(client, world["instructor"], course["id"])
    assert still_empty.status_code == 400

    assert (await _add_lesson(client, world["instructor"], course["id"], "Dos")).status_code == 201
    sent = await _submit(client, world["instructor"], course["id"])
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "ZONE_REVIEW"
    assert "SUBMIT" in await _audit_actions(course["id"])


@pytest.mark.asyncio
async def test_submit_needs_the_verification_gate(client, world, factory):
    honor = await _honor(factory, "submit-gate")
    course = await _ready_draft(client, world, honor)
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'REVOKED' WHERE user_id = :id"),
            {"id": uuid.UUID(world["instructor"]["id"])},
        )
        await db.commit()
    blocked = await _submit(client, world["instructor"], course["id"])
    assert blocked.status_code == 403

    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'AUTHORIZED' WHERE user_id = :id"),
            {"id": uuid.UUID(world["instructor"]["id"])},
        )
        await db.commit()
    assert (await _submit(client, world["instructor"], course["id"])).status_code == 200


@pytest.mark.asyncio
async def test_submit_refuses_a_honor_that_is_no_longer_published(client, world, factory):
    honor = await _honor(factory, "archived-honor")
    course = await _ready_draft(client, world, honor)
    async with SessionLocal() as db:
        await db.execute(text("UPDATE honors SET status = 'ARCHIVED' WHERE id = :id"),
                         {"id": uuid.UUID(honor["id"])})
        await db.commit()
    assert (await _submit(client, world["instructor"], course["id"])).status_code == 400


# ----------------------------------------------------------------------------
# Review: the same two steps and the same reviewers as an honor
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zone_then_association_publish_the_course(client, world, factory):
    honor = await _honor(factory, "review-flow")
    course = await _ready_draft(client, world, honor)
    await _submit(client, world["instructor"], course["id"])

    assert (await _review(client, world["instructor"], course["id"])).status_code == 403
    assert (await _review(client, world["other_admin"], course["id"])).status_code == 403
    assert (await _review(client, world["student"], course["id"])).status_code == 403

    zone = await _review(client, world["zone_coordinator"], course["id"])
    assert zone.status_code == 200, zone.text
    assert zone.json()["status"] == "ASSOCIATION_REVIEW"

    assert (await _review(client, world["zone_coordinator"], course["id"])).status_code == 403

    published = await _review(client, world["assoc_admin"], course["id"])
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "PUBLISHED"
    assert published.json()["published_at"] is not None
    assert await _audit_actions(course["id"]) == ["CREATE", "UPDATE", "SUBMIT", "APPROVE", "APPROVE"]


@pytest.mark.asyncio
async def test_request_changes_sends_it_back_with_the_comment(client, world, factory):
    honor = await _honor(factory, "changes")
    course = await _ready_draft(client, world, honor)
    await _submit(client, world["instructor"], course["id"])

    back = await _review(
        client, world["zone_coordinator"], course["id"], "REQUEST_CHANGES",
        comments="Añade el aviso de seguridad",
    )
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "DRAFT"

    detail = await _get(client, world["instructor"], course["id"], "/instructor")
    history = detail.json()["review_history"]
    assert [row["action"] for row in history] == ["REQUEST_CHANGES"]
    assert history[0]["comments"] == "Añade el aviso de seguridad"


@pytest.mark.asyncio
async def test_a_course_review_never_shows_up_in_the_honor_history(client, world, factory):
    """Regression: `honor_reviews` is shared, so the honor detail filters course rows out."""
    honor = await _honor(factory, "shared-history")
    course = await _ready_draft(client, world, honor)
    await _submit(client, world["instructor"], course["id"])
    await _review(client, world["zone_coordinator"], course["id"], "REJECT", comments="No")

    rows = await fetch_all(
        "SELECT course_id FROM honor_reviews WHERE honor_id = :id", id=uuid.UUID(honor["id"])
    )
    assert [str(row["course_id"]) for row in rows] == [course["id"]]

    honor_detail = await client.get(f"{HONORS}/{honor['id']}", headers=world["master"]["headers"])
    assert honor_detail.status_code == 200
    assert honor_detail.json()["review_history"] == []


# ----------------------------------------------------------------------------
# Published content is immutable; only the operational fields move
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_published_content_is_frozen_but_operation_is_not(client, world, factory):
    honor = await _honor(factory, "frozen")
    course = await _published_course(client, world, honor)
    headers = world["instructor"]["headers"]

    assert (await client.put(f"{COURSES}/{course['id']}",
                             json={"title": "Otro"}, headers=headers)).status_code == 400
    assert (await _add_lesson(client, world["instructor"], course["id"])).status_code == 400
    assert (await client.put(f"{COURSES}/{course['id']}/plan",
                             json=[{"position": 1, "assessment": "REVIEW"},
                                   {"position": 2, "assessment": "EVIDENCE"}],
                             headers=headers)).status_code == 400

    operation = await client.patch(
        f"{COURSES}/{course['id']}/operation", json={"enrollment_open": False, "capacity": 20},
        headers=headers,
    )
    assert operation.status_code == 200, operation.text
    assert operation.json()["enrollment_open"] is False
    assert operation.json()["capacity"] == 20
    assert "COURSE_OPERATION_UPDATE" in await _audit_actions(course["id"])

    assert (await client.patch(f"{COURSES}/{course['id']}/operation", json={"capacity": 5},
                               headers=world["assoc_admin"]["headers"])).status_code in (403, 404)


# ----------------------------------------------------------------------------
# Versions
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_new_version_copies_the_course_and_the_old_one_lives_until_publication(
    client, world, factory
):
    honor = await _honor(factory, "versions")
    course = await _published_course(client, world, honor)
    headers = world["instructor"]["headers"]

    version = await client.post(
        f"{COURSES}/{course['id']}/version", json={"changes_description": "Más ejemplos"},
        headers=headers,
    )
    assert version.status_code == 201, version.text
    second = version.json()
    assert second["version"] == 2
    assert second["status"] == "DRAFT"
    assert second["previous_version_id"] == course["id"]
    assert [lesson["title"] for lesson in second["lessons"]] == ["Lección 1"]
    assert len(second["lessons"][0]["blocks"]) == 2
    assert _plan_by_position(second).keys() == _plan_by_position(course).keys()

    # The published one is still the live course until the new version is published.
    assert (await _get(client, None, course["id"])).json()["status"] == "PUBLISHED"
    again = await client.post(
        f"{COURSES}/{course['id']}/version", json={"changes_description": "Otra"}, headers=headers
    )
    assert again.status_code == 409

    await _publish(client, world, second["id"])
    assert (await _get(client, world["instructor"], course["id"], "/instructor")).json()["status"] == "ARCHIVED"
    assert "COURSE_VERSION" in await _audit_actions(second["id"])


@pytest.mark.asyncio
async def test_only_a_published_course_can_be_versioned(client, world, factory):
    honor = await _honor(factory, "version-draft")
    course = await _ready_draft(client, world, honor)
    response = await client.post(
        f"{COURSES}/{course['id']}/version", json={"changes_description": "Sin publicar"},
        headers=world["instructor"]["headers"],
    )
    assert response.status_code == 400


# ----------------------------------------------------------------------------
# Archiving: by the author, or by the authority with a reason
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_author_archives_without_a_reason(client, world, factory):
    honor = await _honor(factory, "archive-author")
    course = await _published_course(client, world, honor)
    removed = await client.request(
        "DELETE", f"{COURSES}/{course['id']}", json={}, headers=world["instructor"]["headers"]
    )
    assert removed.status_code == 204, removed.text
    detail = (await _get(client, world["instructor"], course["id"], "/instructor")).json()
    assert detail["status"] == "ARCHIVED"
    assert detail["archived_by_authority"] is False


@pytest.mark.asyncio
async def test_the_authority_withdraws_a_course_only_with_a_reason(client, world, factory):
    honor = await _honor(factory, "archive-authority")
    course = await _published_course(client, world, honor)

    assert (await client.request("DELETE", f"{COURSES}/{course['id']}", json={},
                                 headers=world["assoc_admin"]["headers"])).status_code == 422
    assert (await client.request("DELETE", f"{COURSES}/{course['id']}", json={"reason": "Contenido"},
                                 headers=world["other_admin"]["headers"])).status_code in (403, 404)

    withdrawn = await client.request(
        "DELETE", f"{COURSES}/{course['id']}", json={"reason": "Una foto reconocible de un menor"},
        headers=world["assoc_admin"]["headers"],
    )
    assert withdrawn.status_code == 204, withdrawn.text
    row = await fetch_one("SELECT * FROM courses WHERE id = :id", id=uuid.UUID(course["id"]))
    assert row["status"] == "ARCHIVED"
    assert row["archived_by_authority"] is True
    assert row["archive_reason"] == "Una foto reconocible de un menor"
    assert "ARCHIVE" in await _audit_actions(course["id"])


# ----------------------------------------------------------------------------
# Who sees what
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unpublished_course_is_invisible_to_everyone_else(client, world, factory):
    honor = await _honor(factory, "visibility")
    course = await _ready_draft(client, world, honor)

    assert (await _get(client, None, course["id"])).status_code == 404
    assert (await _get(client, world["student"], course["id"])).status_code == 404
    assert (await _get(client, world["unverified"], course["id"])).status_code == 404
    assert (await _get(client, world["other_admin"], course["id"])).status_code == 404

    for who in ("instructor", "zone_coordinator", "assoc_admin", "master"):
        response = await _get(client, world[who], course["id"])
        assert response.status_code == 200, f"{who}: {response.text}"

    # The full view, with the blocks and the review history, is staff only.
    assert (await _get(client, world["instructor"], course["id"], "/instructor")).status_code == 200
    assert (await _get(client, world["assoc_admin"], course["id"], "/instructor")).status_code == 200
    assert (await _get(client, world["student"], course["id"], "/instructor")).status_code == 404


@pytest.mark.asyncio
async def test_the_public_page_shows_the_lessons_but_not_their_content(client, world, factory):
    honor = await _honor(factory, "public-page")
    course = await _published_course(client, world, honor)

    public = await _get(client, None, course["id"])
    assert public.status_code == 200, public.text
    body = public.json()
    assert body["title"] == "Nudos con el Inst. Pérez"
    # The instructor's name is public (they sign certificates); nothing else of their account is.
    assert body["instructor"] == {"id": world["instructor"]["id"], "name": body["instructor"]["name"]}
    assert [lesson["title"] for lesson in body["lessons"]] == ["Lección 1"]
    assert body["lessons"][0]["block_count"] == 2
    assert body["lessons"][0]["blocks"] == []  # content is for the enrolled, the author and reviewers
    assert body["plan"][0]["assessment"] in ("REVIEW", "EVIDENCE")
    assert body["instructor_verified"] is True

    author_view = await _get(client, world["instructor"], course["id"])
    assert len(author_view.json()["lessons"][0]["blocks"]) == 2


# ----------------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discovery_lists_only_open_courses_of_verified_instructors(client, world, factory):
    honor = await _honor(factory, "discovery")
    published = await _published_course(client, world, honor)
    draft = await _course(client, world["unverified"], honor)

    async def listed(user=None) -> list[str]:
        response = await client.get(
            f"{COURSES}?honor_id={honor['id']}", headers=user["headers"] if user else None
        )
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()]

    ids = await listed()
    assert published["id"] in ids
    assert draft["id"] not in ids

    card = next(
        row for row in (await client.get(f"{COURSES}?honor_id={honor['id']}")).json()
        if row["id"] == published["id"]
    )
    assert card["lesson_count"] == 1
    assert card["assessment_counts"] == {"EXAM": 0, "REVIEW": 1, "EVIDENCE": 1}
    assert card["seats_left"] is None  # no cap

    # Closing enrolment or losing the letter takes the course out of the shop window.
    await client.patch(f"{COURSES}/{published['id']}/operation", json={"enrollment_open": False},
                       headers=world["instructor"]["headers"])
    assert published["id"] not in await listed()
    await client.patch(f"{COURSES}/{published['id']}/operation", json={"enrollment_open": True},
                       headers=world["instructor"]["headers"])
    assert published["id"] in await listed()

    async with SessionLocal() as db:
        await db.execute(text("UPDATE church_letters SET status = 'REVOKED' WHERE user_id = :id"),
                         {"id": uuid.UUID(world["instructor"]["id"])})
        await db.commit()
    assert published["id"] not in await listed()
    async with SessionLocal() as db:
        await db.execute(text("UPDATE church_letters SET status = 'AUTHORIZED' WHERE user_id = :id"),
                         {"id": uuid.UUID(world["instructor"]["id"])})
        await db.commit()


@pytest.mark.asyncio
async def test_my_created_and_the_review_queue(client, world, factory):
    honor = await _honor(factory, "queues")
    course = await _ready_draft(client, world, honor)
    await _submit(client, world["instructor"], course["id"])

    mine = await client.get(f"{COURSES}/my/created", headers=world["instructor"]["headers"])
    assert mine.status_code == 200, mine.text
    assert course["id"] in [row["id"] for row in mine.json()["items"]]
    assert (await client.get(f"{COURSES}/my/created",
                             headers=world["student"]["headers"])).status_code == 403

    zone_queue = await client.get(f"{COURSES}/pending/reviews", headers=world["zone_coordinator"]["headers"])
    assert zone_queue.status_code == 200, zone_queue.text
    assert course["id"] in [row["id"] for row in zone_queue.json()]
    assert course["id"] not in [
        row["id"] for row in (await client.get(f"{COURSES}/pending/reviews",
                                               headers=world["other_admin"]["headers"])).json()
    ]
    # The association only sees its own step.
    assert course["id"] not in [
        row["id"] for row in (await client.get(f"{COURSES}/pending/reviews",
                                               headers=world["assoc_admin"]["headers"])).json()
    ]
