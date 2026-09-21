"""Bloque B · I3 — Inscripción a un curso, dictamen del instructor y emisión manual.

Un miembro se une a un curso publicado: su inscripción del bloque A pasa a `mode = 'COURSE'`
con `course_id`, el plan del curso decide qué requisitos piden evidencia y el instructor
verificado gana `can_review` y `can_issue` sobre **esas** inscripciones y sólo sobre ellas.

Lo que NO cambia: el portafolio del miembro sigue siendo suyo (`can_view_enrollment` es más
estrecha que `can_view_portfolio`), el progreso nunca se pierde al salir o al ser expulsado, y
el director del club del miembro sigue dictaminando.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

COURSES = "/api/v1/courses"
PORTFOLIO = "/api/v1/portfolio"
ENROLLMENTS = f"{PORTFOLIO}/enrollments"

TEXT_BLOCK = {"type": "text", "markdown": "## Nudos\nPractica el nudo llano."}


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(
                        text(
                            "SELECT to_regclass('public.courses') IS NOT NULL AND EXISTS ("
                            " SELECT 1 FROM information_schema.columns WHERE table_name ="
                            " 'honor_enrollments' AND column_name = 'course_id')"
                        )
                    )
                )
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _tables_exist(),
        reason="apply migrations/009b_courses.sql and 009c_course_enrollment.sql to the test database",
    ),
]
factory = module_factory("course-enroll")


# ----------------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------------
async def _authorize(user_id: str, organization_id: str) -> None:
    """The letter `instructor_is_verified` asks for (its own flow lives in test_church_letters)."""
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
                "UPDATE users SET verification_status = 'VERIFIED',"
                " child_protection_completed = true WHERE id = :id"
            ),
            {"id": uuid.UUID(user_id)},
        )
        await db.commit()


async def _revoke(user_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'REVOKED' WHERE user_id = :id"),
            {"id": uuid.UUID(user_id)},
        )
        await db.commit()


async def _restore(user_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'AUTHORIZED' WHERE user_id = :id"),
            {"id": uuid.UUID(user_id)},
        )
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", association)
    club = await factory.org("club", "club", association)
    other_club = await factory.org("other-club", "club", association)
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    # The virtual instructors hang from the association, not from the members' club: that is
    # the whole point of the figure, and it keeps the course clause of `can_review` honest
    # (an instructor attached to the club would already review through the clause of A).
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", association["id"]),
        "instructor2": await factory.user("instructor2", "INSTRUCTOR", other_club["id"]),
        "member": await factory.user("member", "STUDENT", club["id"]),
        "member2": await factory.user("member2", "STUDENT", club["id"]),
        "member3": await factory.user("member3", "STUDENT", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "loner": await factory.user("loner", "STUDENT"),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "director_other": await factory.user("director-other", "CLUB_DIRECTOR", other_club["id"]),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
    }
    await _authorize(people["instructor"]["id"], association["id"])
    await _authorize(people["instructor2"]["id"], other_club["id"])
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE organizations SET code = :code WHERE id = :id"),
            {"code": issuer_code, "id": uuid.UUID(issuer["id"])},
        )
        await db.commit()
    return {
        **people,
        "association": association,
        "zone": zone,
        "club": club,
        "other_club": other_club,
        "issuer_code": issuer_code,
    }


@pytest.fixture
def issuer(world, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


async def _grant_consent(world, status="APPROVED") -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("DELETE FROM guardianships WHERE child_id = :c"),
            {"c": uuid.UUID(world["minor"]["id"])},
        )
        await db.execute(
            text(
                "INSERT INTO guardianships (guardian_id, child_id, consent_status)"
                " VALUES (:g, :c, :s)"
            ),
            {
                "g": uuid.UUID(world["guardian"]["id"]),
                "c": uuid.UUID(world["minor"]["id"]),
                "s": status,
            },
        )
        await db.commit()


async def _honor(factory, label, theoretical=(True, True), *, status="PUBLISHED") -> dict:
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (
            await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO honors (id, ministry_id, name, slug, active, status)"
                " VALUES (:id, :m, :name, :slug, true, :status)"
            ),
            {
                "id": honor_id,
                "m": ministry,
                "name": name,
                "slug": f"{factory.prefix}-{label}-{honor_id.hex[:6]}",
                "status": status,
            },
        )
        for position, is_theoretical in enumerate(theoretical, start=1):
            await db.execute(
                text(
                    "INSERT INTO honor_requirements (honor_id, position, description,"
                    " is_theoretical, locale) VALUES (:h, :p, :d, :t, 'es')"
                ),
                {"h": honor_id, "p": position, "d": f"Requisito {position}", "t": is_theoretical},
            )
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _published_course(client, world, honor, **extra) -> dict:
    instructor = extra.pop("instructor", world["instructor"])
    body = {"honor_id": honor["id"], "title": "Nudos con el Inst. Pérez", **extra}
    created = await client.post(COURSES, json=body, headers=instructor["headers"])
    assert created.status_code == 201, created.text
    course_id = created.json()["id"]
    lesson = await client.post(
        f"{COURSES}/{course_id}/lessons",
        json={"title": "Lección 1", "blocks": [TEXT_BLOCK]},
        headers=instructor["headers"],
    )
    assert lesson.status_code == 201, lesson.text
    assert (
        await client.post(f"{COURSES}/{course_id}/submit", headers=instructor["headers"])
    ).status_code == 200
    assert (
        await client.post(
            f"{COURSES}/{course_id}/review",
            json={"action": "APPROVE"},
            headers=world["zone_coordinator"]["headers"],
        )
    ).status_code == 200
    done = await client.post(
        f"{COURSES}/{course_id}/review",
        json={"action": "APPROVE"},
        headers=world["assoc_admin"]["headers"],
    )
    assert done.status_code == 200, done.text
    return done.json()


async def _join(client, user, course_id):
    return await client.post(f"{COURSES}/{course_id}/join", headers=user["headers"])


async def _joined(client, user, course_id) -> dict:
    response = await _join(client, user, course_id)
    assert response.status_code == 200, response.text
    return response.json()


async def _submit_requirement(client, user, enrollment_id, position, note="Mi respuesta"):
    return await client.put(
        f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}",
        json={"status": "SUBMITTED", "member_note": note},
        headers=user["headers"],
    )


async def _review(client, user, enrollment_id, position, verdict="COMPLETE", note=None):
    body = {"verdict": verdict}
    if note or verdict == "INCOMPLETE":
        body["note"] = note or "Falta trabajo"
    return await client.post(
        f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}/review",
        json=body,
        headers=user["headers"],
    )


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id)
    )
    return [row["action"] for row in rows]


def _requirements(detail: dict) -> dict:
    return {row["position"]: row for row in detail["requirements"]}


# ----------------------------------------------------------------------------
# Joining a course
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_join_creates_a_course_enrollment_with_the_course_plan(client, world, factory):
    honor = await _honor(factory, "join", (True, False))
    course = await _published_course(client, world, honor)

    detail = await _joined(client, world["member"], course["id"])
    assert detail["mode"] == "COURSE"
    assert detail["course"] == {
        "id": course["id"],
        "title": course["title"],
        "instructor_name": factory.name("instructor"),
    }
    rows = _requirements(detail)
    # The plan, not the honor, decides what the member must upload: requirement 1 is REVIEW
    # (an answer is enough) and requirement 2 is practical, so it stays EVIDENCE.
    assert rows[1]["assessment"] == "REVIEW" and rows[1]["is_practical"] is False
    assert rows[2]["assessment"] == "EVIDENCE" and rows[2]["is_practical"] is True

    stored = await fetch_one(
        "SELECT mode, course_id, course_joined_at FROM honor_enrollments WHERE id = :id",
        id=uuid.UUID(detail["id"]),
    )
    assert stored["mode"] == "COURSE" and str(stored["course_id"]) == course["id"]
    assert stored["course_joined_at"] is not None
    assert "COURSE_JOIN" in await _audit_actions(detail["id"])


@pytest.mark.asyncio
async def test_join_is_idempotent(client, world, factory):
    honor = await _honor(factory, "idem")
    course = await _published_course(client, world, honor)
    first = await _joined(client, world["member"], course["id"])
    second = await _joined(client, world["member"], course["id"])
    assert first["id"] == second["id"]
    count = await fetch_one(
        "SELECT count(*) AS n FROM honor_enrollments WHERE user_id = :u AND honor_id = :h",
        u=uuid.UUID(world["member"]["id"]),
        h=uuid.UUID(honor["id"]),
    )
    assert count["n"] == 1


@pytest.mark.asyncio
async def test_join_converts_a_club_enrollment_and_keeps_what_is_complete(client, world, factory):
    honor = await _honor(factory, "convert", (True, True))
    course = await _published_course(client, world, honor)
    started = await client.post(
        ENROLLMENTS, json={"honor_id": honor["id"]}, headers=world["member2"]["headers"]
    )
    assert started.status_code == 201, started.text
    enrollment_id = started.json()["id"]
    await _submit_requirement(client, world["member2"], enrollment_id, 1)
    assert (await _review(client, world["director"], enrollment_id, 1)).status_code == 200
    # A second requirement waiting for a verdict stays waiting: only EXAM rows are reset.
    await _submit_requirement(client, world["member2"], enrollment_id, 2)

    detail = await _joined(client, world["member2"], course["id"])
    assert detail["id"] == enrollment_id and detail["mode"] == "COURSE"
    rows = _requirements(detail)
    assert rows[1]["status"] == "COMPLETE" and rows[1]["completed_via"] == "REVIEW"
    assert rows[2]["status"] == "SUBMITTED"


@pytest.mark.asyncio
async def test_join_refuses_a_course_that_is_not_open(client, world, factory):
    honor = await _honor(factory, "closed")
    course = await _published_course(client, world, honor)
    assert (
        await client.patch(
            f"{COURSES}/{course['id']}/operation",
            json={"enrollment_open": False},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 200
    refused = await _join(client, world["member3"], course["id"])
    assert refused.status_code == 409
    assert "inscripciones" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_join_refuses_a_draft_course(client, world, factory):
    honor = await _honor(factory, "draft")
    created = await client.post(
        COURSES,
        json={"honor_id": honor["id"], "title": "Borrador"},
        headers=world["instructor"]["headers"],
    )
    assert created.status_code == 201
    # An unpublished course does not exist for a member: 404, never 403.
    assert (await _join(client, world["member"], created.json()["id"])).status_code == 404


@pytest.mark.asyncio
async def test_join_refuses_while_the_instructor_is_not_verified(client, world, factory):
    honor = await _honor(factory, "unverified")
    course = await _published_course(client, world, honor)
    await _revoke(world["instructor"]["id"])
    try:
        refused = await _join(client, world["member3"], course["id"])
        assert refused.status_code == 409
        assert "instructor" in refused.json()["detail"].lower()
    finally:
        await _restore(world["instructor"]["id"])


@pytest.mark.asyncio
async def test_the_instructor_never_joins_their_own_course(client, world, factory):
    honor = await _honor(factory, "self")
    course = await _published_course(client, world, honor)
    assert (await _join(client, world["instructor"], course["id"])).status_code == 403


@pytest.mark.asyncio
async def test_a_minor_needs_the_consent_of_a_guardian(client, world, factory):
    honor = await _honor(factory, "minor")
    course = await _published_course(client, world, honor)
    await _grant_consent(world, "PENDING")
    refused = await _join(client, world["minor"], course["id"])
    assert refused.status_code == 403
    assert "tutor" in refused.json()["detail"].lower()

    await _grant_consent(world, "APPROVED")
    assert (await _join(client, world["minor"], course["id"])).status_code == 200


@pytest.mark.asyncio
async def test_a_member_without_a_club_may_join(client, world, factory):
    """D2a: the course is the way in for whoever has no club nearby."""
    honor = await _honor(factory, "loner")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["loner"], course["id"])
    assert detail["club"] is None and detail["mode"] == "COURSE"


@pytest.mark.asyncio
async def test_capacity_closes_the_course_and_the_card_counts_seats(client, world, factory):
    honor = await _honor(factory, "capacity")
    course = await _published_course(client, world, honor)
    assert (
        await client.patch(
            f"{COURSES}/{course['id']}/operation",
            json={"capacity": 1},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 200
    assert (await _join(client, world["member"], course["id"])).status_code == 200

    full = await _join(client, world["member2"], course["id"])
    assert full.status_code == 409 and "lleno" in full.json()["detail"].lower()
    card = (await client.get(f"{COURSES}/{course['id']}")).json()
    assert card["enrolled_count"] == 1 and card["seats_left"] == 0

    # ...and the instructor cannot shrink the course below the people already in it.
    shrunk = await client.patch(
        f"{COURSES}/{course['id']}/operation",
        json={"capacity": 1},
        headers=world["instructor"]["headers"],
    )
    assert shrunk.status_code == 200  # equal to the enrolled count is fine
    assert (
        await client.patch(
            f"{COURSES}/{course['id']}/operation",
            json={"capacity": 1, "enrollment_open": True},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_capacity_cannot_be_set_below_the_people_already_inside(client, world, factory):
    honor = await _honor(factory, "shrink")
    course = await _published_course(client, world, honor)
    await _joined(client, world["member"], course["id"])
    await _joined(client, world["member2"], course["id"])
    shrunk = await client.patch(
        f"{COURSES}/{course['id']}/operation",
        json={"capacity": 1},
        headers=world["instructor"]["headers"],
    )
    assert shrunk.status_code == 409 and "2" in shrunk.json()["detail"]


@pytest.mark.asyncio
async def test_a_course_the_instructor_archived_keeps_serving_its_members(client, world, factory):
    """§3.5: the instructor retires their course; nobody new comes in, and those inside
    keep their lessons, their instructor and their verdicts until they finish."""
    honor = await _honor(factory, "retired")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    assert (
        await client.delete(f"{COURSES}/{course['id']}", headers=world["instructor"]["headers"])
    ).status_code == 204

    page = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert page.status_code == 200 and page.json()["archived_by_authority"] is False
    assert page.json()["lessons"][0]["blocks"][0]["type"] == "text"
    await _submit_requirement(client, world["member"], detail["id"], 1)
    assert (await _review(client, world["instructor"], detail["id"], 1)).status_code == 200
    # ...but no new member joins an archived course.
    assert (await _join(client, world["member3"], course["id"])).status_code == 404


@pytest.mark.asyncio
async def test_a_new_version_of_the_course_leaves_its_members_where_they_are(
    client, world, factory
):
    honor = await _honor(factory, "versioned")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    version = await client.post(
        f"{COURSES}/{course['id']}/version",
        json={"changes_description": "Lecciones revisadas"},
        headers=world["instructor"]["headers"],
    )
    assert version.status_code == 201, version.text
    await _publish_version(client, world, version.json()["id"])

    after = await client.get(f"{ENROLLMENTS}/{detail['id']}", headers=world["member"]["headers"])
    assert after.json()["course"]["id"] == course["id"]  # still the version they started
    assert (await _review(client, world["instructor"], detail["id"], 1)).status_code in (200, 409)
    old = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert old.status_code == 200 and old.json()["status"] == "ARCHIVED"
    assert old.json()["lessons"][0]["blocks"][0]["type"] == "text"


async def _publish_version(client, world, course_id) -> None:
    assert (
        await client.post(f"{COURSES}/{course_id}/submit", headers=world["instructor"]["headers"])
    ).status_code == 200
    assert (
        await client.post(
            f"{COURSES}/{course_id}/review",
            json={"action": "APPROVE"},
            headers=world["zone_coordinator"]["headers"],
        )
    ).status_code == 200
    done = await client.post(
        f"{COURSES}/{course_id}/review",
        json={"action": "APPROVE"},
        headers=world["assoc_admin"]["headers"],
    )
    assert done.status_code == 200, done.text


@pytest.mark.asyncio
async def test_a_member_is_in_at_most_one_course_per_honor(client, world, factory):
    honor = await _honor(factory, "two-courses")
    first = await _published_course(client, world, honor)
    second = await _published_course(client, world, honor, instructor=world["instructor2"])
    await _joined(client, world["member"], first["id"])
    clash = await _join(client, world["member"], second["id"])
    assert clash.status_code == 409 and "sal primero" in clash.json()["detail"].lower()


# ----------------------------------------------------------------------------
# Leaving, being removed, and what the member keeps
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_leave_returns_to_club_mode_without_losing_anything(client, world, factory):
    honor = await _honor(factory, "leave", (True, False))
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    enrollment_id = detail["id"]
    await _submit_requirement(client, world["member"], enrollment_id, 1)
    assert (await _review(client, world["instructor"], enrollment_id, 1)).status_code == 200

    left = await client.post(f"{COURSES}/{course['id']}/leave", headers=world["member"]["headers"])
    assert left.status_code == 200, left.text
    assert left.json()["mode"] == "CLUB" and left.json()["course"] is None
    rows = _requirements(left.json())
    assert rows[1]["status"] == "COMPLETE"
    # `is_practical` is not relaxed on the way out: the course's stricter plan stays.
    assert rows[2]["is_practical"] is True
    assert "COURSE_LEAVE" in await _audit_actions(enrollment_id)


@pytest.mark.asyncio
async def test_leaving_a_course_you_are_not_in_is_a_conflict(client, world, factory):
    honor = await _honor(factory, "not-in")
    course = await _published_course(client, world, honor)
    refused = await client.post(
        f"{COURSES}/{course['id']}/leave", headers=world["member3"]["headers"]
    )
    assert refused.status_code == 409


@pytest.mark.asyncio
async def test_the_instructor_removes_a_member_with_a_reason(client, world, factory):
    honor = await _honor(factory, "expel")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member2"], course["id"])

    no_reason = await client.request(
        "DELETE",
        f"{COURSES}/{course['id']}/members/{detail['id']}",
        json={},
        headers=world["instructor"]["headers"],
    )
    assert no_reason.status_code == 422

    removed = await client.request(
        "DELETE",
        f"{COURSES}/{course['id']}/members/{detail['id']}",
        json={"reason": "No siguió las normas del aula"},
        headers=world["instructor"]["headers"],
    )
    assert removed.status_code == 204, removed.text
    after = await client.get(f"{ENROLLMENTS}/{detail['id']}", headers=world["member2"]["headers"])
    assert after.json()["mode"] == "CLUB"
    assert after.json()["course_removed_reason"] == "No siguió las normas del aula"
    assert "COURSE_MEMBER_REMOVE" in await _audit_actions(detail["id"])


@pytest.mark.asyncio
async def test_only_the_author_removes_members(client, world, factory):
    honor = await _honor(factory, "expel-who")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member2"], course["id"])
    for who in ("director", "instructor2", "member2"):
        refused = await client.request(
            "DELETE",
            f"{COURSES}/{course['id']}/members/{detail['id']}",
            json={"reason": "porque sí"},
            headers=world[who]["headers"],
        )
        assert refused.status_code in (403, 404), who


# ----------------------------------------------------------------------------
# What an enrolled member reads
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_only_the_enrolled_member_sees_the_lesson_blocks(client, world, factory):
    honor = await _honor(factory, "blocks")
    course = await _published_course(client, world, honor)

    outsider = await client.get(f"{COURSES}/{course['id']}", headers=world["member3"]["headers"])
    assert outsider.status_code == 200
    assert outsider.json()["lessons"][0]["block_count"] == 1
    assert outsider.json()["lessons"][0]["blocks"] == []

    await _joined(client, world["member"], course["id"])
    enrolled = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert enrolled.json()["lessons"][0]["blocks"][0]["type"] == "text"


@pytest.mark.asyncio
async def test_my_joined_lists_the_courses_i_am_in(client, world, factory):
    honor = await _honor(factory, "joined-list")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member3"], course["id"])
    listed = await client.get(f"{COURSES}/my/joined", headers=world["member3"]["headers"])
    assert listed.status_code == 200, listed.text
    mine = [row for row in listed.json() if row["id"] == course["id"]]
    assert len(mine) == 1
    assert mine[0]["enrollment_id"] == detail["id"]
    assert mine[0]["enrollment_status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_a_course_withdrawn_by_authority_closes_its_lessons_for_the_member(
    client, world, factory
):
    honor = await _honor(factory, "withdrawn")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    assert (
        await client.request(
            "DELETE",
            f"{COURSES}/{course['id']}",
            json={"reason": "Contenido inadecuado"},
            headers=world["assoc_admin"]["headers"],
        )
    ).status_code == 204

    # The member still reaches their course (they must see why), but the content is closed...
    page = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert page.status_code == 200
    assert page.json()["archived_by_authority"] is True
    assert page.json()["archive_reason"] == "Contenido inadecuado"
    assert page.json()["lessons"][0]["blocks"] == []
    # ...and the instructor loses every power over the enrollment while it is withdrawn.
    assert (await _submit_requirement(client, world["member"], detail["id"], 1)).status_code == 200
    assert (await _review(client, world["instructor"], detail["id"], 1)).status_code == 403
    # The progress is kept and "pasar a modalidad club" still works.
    left = await client.post(f"{COURSES}/{course['id']}/leave", headers=world["member"]["headers"])
    assert left.status_code == 200 and left.json()["mode"] == "CLUB"


# ----------------------------------------------------------------------------
# can_review: the course instructor, and nobody else's course
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_course_instructor_reviews_the_enrollments_of_their_course(
    client, world, factory
):
    honor = await _honor(factory, "review")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    enrollment_id = detail["id"]
    await _submit_requirement(client, world["member"], enrollment_id, 1)

    # Another course's instructor has nothing to do here, verified or not.
    assert (await _review(client, world["instructor2"], enrollment_id, 1)).status_code == 403
    # The member's own club director keeps judging (the responsable's "director o instructor").
    assert (await _review(client, world["director"], enrollment_id, 1)).status_code == 200
    assert (await _review(client, world["director_other"], enrollment_id, 2)).status_code == 403

    await _submit_requirement(client, world["member"], enrollment_id, 2)
    done = await _review(client, world["instructor"], enrollment_id, 2)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "READY"


@pytest.mark.asyncio
async def test_an_instructor_who_lost_the_letter_stops_reviewing_at_once(client, world, factory):
    honor = await _honor(factory, "revoked")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member2"], course["id"])
    await _submit_requirement(client, world["member2"], detail["id"], 1)
    await _revoke(world["instructor"]["id"])
    try:
        assert (await _review(client, world["instructor"], detail["id"], 1)).status_code == 403
    finally:
        await _restore(world["instructor"]["id"])
    assert (await _review(client, world["instructor"], detail["id"], 1)).status_code == 200


@pytest.mark.asyncio
async def test_a_complete_verdict_is_reopened_only_by_who_gave_it_or_the_course_instructor(
    client, world, factory
):
    """D4a: the instructor signs the certificate, so they have the last word."""
    honor = await _honor(factory, "reopen")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member3"], course["id"])
    enrollment_id = detail["id"]
    await _submit_requirement(client, world["member3"], enrollment_id, 1)
    assert (await _review(client, world["instructor"], enrollment_id, 1)).status_code == 200

    # The director disagrees with the instructor: they escalate, they do not undo it.
    blocked = await _review(client, world["director"], enrollment_id, 1, "INCOMPLETE")
    assert blocked.status_code == 409
    assert "instructor" in blocked.json()["detail"].lower()
    # Whoever gave the verdict may reopen it, and so may MASTER_GC.
    assert (
        await _review(client, world["instructor"], enrollment_id, 1, "INCOMPLETE")
    ).status_code == 200
    await _submit_requirement(client, world["member3"], enrollment_id, 1)
    assert (await _review(client, world["director"], enrollment_id, 1)).status_code == 200
    assert (
        await _review(client, world["director"], enrollment_id, 1, "INCOMPLETE")
    ).status_code == 200
    await _submit_requirement(client, world["member3"], enrollment_id, 1)
    assert (await _review(client, world["director"], enrollment_id, 1)).status_code == 200
    assert (
        await _review(client, world["master"], enrollment_id, 1, "INCOMPLETE")
    ).status_code == 200


@pytest.mark.asyncio
async def test_in_club_mode_the_reopening_rule_of_a_stays_untouched(client, world, factory):
    honor = await _honor(factory, "club-reopen")
    started = await client.post(
        ENROLLMENTS, json={"honor_id": honor["id"]}, headers=world["member"]["headers"]
    )
    enrollment_id = started.json()["id"]
    await _submit_requirement(client, world["member"], enrollment_id, 1)
    assert (await _review(client, world["instructor"], enrollment_id, 1)).status_code == 403
    assert (await _review(client, world["director"], enrollment_id, 1)).status_code == 200
    # Rule 4 of A: any reviewer with jurisdiction reopens a COMPLETE one.
    assert (
        await _review(client, world["master"], enrollment_id, 1, "INCOMPLETE")
    ).status_code == 200


# ----------------------------------------------------------------------------
# can_view_enrollment: narrower than can_view_portfolio
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_instructor_sees_the_enrollment_but_never_the_whole_portfolio(
    client, world, factory
):
    honor = await _honor(factory, "visibility")
    other = await _honor(factory, "visibility-other")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    elsewhere = await client.post(
        ENROLLMENTS, json={"honor_id": other["id"]}, headers=world["member"]["headers"]
    )
    assert elsewhere.status_code == 201

    seen = await client.get(f"{ENROLLMENTS}/{detail['id']}", headers=world["instructor"]["headers"])
    assert seen.status_code == 200
    assert seen.json()["permissions"]["can_review"] is True

    # Not the rest of the portfolio, and not the enrollment of another course.
    portfolio = await client.get(
        f"{PORTFOLIO}/users/{world['member']['id']}", headers=world["instructor"]["headers"]
    )
    assert portfolio.status_code == 403
    assert (
        await client.get(
            f"{ENROLLMENTS}/{elsewhere.json()['id']}", headers=world["instructor"]["headers"]
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_the_members_list_carries_no_contact_details(client, world, factory):
    honor = await _honor(factory, "members")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])

    listed = await client.get(
        f"{COURSES}/{course['id']}/members", headers=world["instructor"]["headers"]
    )
    assert listed.status_code == 200, listed.text
    row = next(row for row in listed.json() if row["enrollment_id"] == detail["id"])
    assert row["member"]["name"] == detail["user"]["name"]
    assert row["counters"]["total"] == 2
    assert world["member"]["email"] not in listed.text
    # Only the author (and MASTER_GC) read it.
    assert (
        await client.get(f"{COURSES}/{course['id']}/members", headers=world["director"]["headers"])
    ).status_code in (403, 404)
    assert (
        await client.get(f"{COURSES}/{course['id']}/members", headers=world["master"]["headers"])
    ).status_code == 200


# ----------------------------------------------------------------------------
# The instructor's queues
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_queue_of_the_instructor_holds_only_their_courses(client, world, factory):
    honor = await _honor(factory, "queue")
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member3"], course["id"])
    await _submit_requirement(client, world["member3"], detail["id"], 1)

    queue = await client.get(
        f"{PORTFOLIO}/review/queue?status=SUBMITTED", headers=world["instructor2"]["headers"]
    )
    assert queue.status_code in (200, 403)
    if queue.status_code == 200:
        assert all(row["enrollment_id"] != detail["id"] for row in queue.json())

    mine = await client.get(
        f"{PORTFOLIO}/review/queue?status=SUBMITTED", headers=world["instructor"]["headers"]
    )
    assert mine.status_code == 200, mine.text
    assert any(row["enrollment_id"] == detail["id"] for row in mine.json())


@pytest.mark.asyncio
async def test_ready_queue_and_manual_issuance_by_the_instructor(client, world, factory, issuer):
    honor = await _honor(factory, "issue", (True, True))
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member"], course["id"])
    enrollment_id = detail["id"]
    for position, reviewer in ((1, "director"), (2, "instructor")):
        await _submit_requirement(client, world["member"], enrollment_id, position)
        assert (await _review(client, world[reviewer], enrollment_id, position)).status_code == 200

    ready = await client.get(
        f"{PORTFOLIO}/review/queue?status=READY", headers=world["instructor"]["headers"]
    )
    assert ready.status_code == 200
    mine = next(row for row in ready.json() if row["enrollment_id"] == enrollment_id)
    assert mine["can_issue"] is True

    # In COURSE the director does NOT issue: the instructor signs the certificate.
    refused = await client.post(
        f"{ENROLLMENTS}/{enrollment_id}/certificate",
        json={"issued_date": "2026-09-21"},
        headers=world["director"]["headers"],
    )
    assert refused.status_code == 403

    issued = await client.post(
        f"{ENROLLMENTS}/{enrollment_id}/certificate",
        json={"issued_date": "2026-09-21", "instructor_name": "Nombre inventado"},
        headers=world["instructor"]["headers"],
    )
    assert issued.status_code == 201, issued.text
    certificate = issued.json()
    assert certificate["issued_role"] == "INSTRUCTOR"
    # The instructor's own name, never what the form sent.
    assert certificate["instructor_name"] == detail["course"]["instructor_name"]
    assert certificate["director_name"] is not None  # the director judged requirement 1


@pytest.mark.asyncio
async def test_a_certified_course_enrollment_is_frozen(client, world, factory, issuer):
    honor = await _honor(factory, "frozen", (True,))
    course = await _published_course(client, world, honor)
    detail = await _joined(client, world["member2"], course["id"])
    enrollment_id = detail["id"]
    await _submit_requirement(client, world["member2"], enrollment_id, 1)
    assert (await _review(client, world["instructor"], enrollment_id, 1)).status_code == 200
    issued = await client.post(
        f"{ENROLLMENTS}/{enrollment_id}/certificate",
        json={"issued_date": "2026-09-21"},
        headers=world["instructor"]["headers"],
    )
    assert issued.status_code == 201, issued.text
    left = await client.post(f"{COURSES}/{course['id']}/leave", headers=world["member2"]["headers"])
    assert left.status_code == 409
