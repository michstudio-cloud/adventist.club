"""Bloque C · I4 — El banco de preguntas del curso y el plan `EXAM`.

Dos cosas se prueban aquí por encima de todo: que las respuestas correctas **nunca** salen
hacia un miembro, y que un curso no se envía a revisión con un examen que nadie podría
rendir (banco vacío, banco más corto que el sorteo, o preguntas que exigen un calificador
humano que todavía no existe — eso llega en I6).

El endurecimiento de `GET /honors/{id}/instructor` (hallazgo 2) se prueba en test_honors.py.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, module_factory, requires_db

COURSES = "/api/v1/courses"
TEXT_BLOCK = {"type": "text", "markdown": "## Nudos\nPractica el nudo llano."}

MC = {
    "question_text": "¿Cuál es el nudo llano?",
    "question_type": "MULTIPLE_CHOICE",
    "options": ["El de la izquierda", "El de la derecha"],
    "correct_answer": "El de la izquierda",
    "points": 2,
    "explanation": "SECRETO-EXPLICACION",
}
TF = {
    "question_text": "El nudo llano une dos cabos del mismo grosor.",
    "question_type": "TRUE_FALSE",
    "correct_answer": "true",
}


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(text("SELECT to_regclass('public.course_questions') IS NOT NULL"))
                )
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _tables_exist(), reason="apply migrations/010_exams.sql to the test database"
    ),
]
factory = module_factory("course-exams")


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


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", association)
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", association["id"]),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "member": await factory.user("member", "STUDENT", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
    }
    await _authorize(people["instructor"]["id"], association["id"])
    return {**people, "association": association, "zone": zone}


async def _honor(factory, label, theoretical=(True, True), *, created_by=None) -> dict:
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (
            await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO honors (id, ministry_id, name, slug, active, status, created_by_id)"
                " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED', :by)"
            ),
            {
                "id": honor_id,
                "m": ministry,
                "name": name,
                "slug": f"{factory.prefix}-{label}-{honor_id.hex[:6]}",
                "by": uuid.UUID(created_by) if created_by else None,
            },
        )
        for position, is_theoretical in enumerate(theoretical, start=1):
            await db.execute(
                text(
                    "INSERT INTO honor_requirements (id, honor_id, position, description,"
                    " is_theoretical, locale) VALUES (:rid, :h, :p, :d, :t, 'es')"
                ),
                {
                    "rid": uuid.uuid4(),
                    "h": honor_id,
                    "p": position,
                    "d": f"Requisito {position}",
                    "t": is_theoretical,
                },
            )
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _honor_bank(honor_id: str, position: int, how_many: int = 3) -> None:
    async with SessionLocal() as db:
        requirement = (
            await db.execute(
                text(
                    "SELECT id FROM honor_requirements WHERE honor_id = :h AND position = :p"
                    " AND locale = 'es'"
                ),
                {"h": uuid.UUID(honor_id), "p": position},
            )
        ).scalar_one()
        for index in range(how_many):
            await db.execute(
                text(
                    "INSERT INTO honor_questions (id, requirement_id, position, question_text,"
                    " question_type, options, correct_answer, points)"
                    " VALUES (:id, :r, :pos, :q, 'MULTIPLE_CHOICE', :opts, 'Sí', 1)"
                ),
                {
                    "id": uuid.uuid4(),
                    "r": requirement,
                    "pos": index + 1,
                    "q": f"Pregunta oficial {index + 1}",
                    "opts": '["Sí", "No"]',
                },
            )
        await db.commit()


async def _draft(client, world, honor, **extra) -> dict:
    body = {"honor_id": honor["id"], "title": "Nudos con el Inst. Pérez", **extra}
    created = await client.post(COURSES, json=body, headers=world["instructor"]["headers"])
    assert created.status_code == 201, created.text
    course = created.json()
    lesson = await client.post(
        f"{COURSES}/{course['id']}/lessons",
        json={"title": "Lección 1", "blocks": [TEXT_BLOCK]},
        headers=world["instructor"]["headers"],
    )
    assert lesson.status_code == 201, lesson.text
    return course


async def _set_bank(client, world, course_id, position, bank, draw_count=1):
    return await client.put(
        f"{COURSES}/{course_id}/requirements/{position}/questions",
        json={"draw_count": draw_count, "question_bank": bank},
        headers=world["instructor"]["headers"],
    )


async def _submit(client, world, course_id):
    return await client.post(f"{COURSES}/{course_id}/submit", headers=world["instructor"]["headers"])


async def _staff(client, world, course_id, who="instructor"):
    return await client.get(f"{COURSES}/{course_id}/instructor", headers=world[who]["headers"])


def _plan_by_position(detail: dict) -> dict:
    return {item["position"]: item for item in detail["plan"]}


# ----------------------------------------------------------------------------
# The bank belongs to the course
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_setting_a_bank_marks_the_requirement_as_exam(client, world, factory):
    honor = await _honor(factory, "bank")
    course = await _draft(client, world, honor)
    saved = await _set_bank(client, world, course["id"], 1, [MC, TF], draw_count=2)
    assert saved.status_code == 200, saved.text
    plan = _plan_by_position(saved.json())
    assert plan[1]["assessment"] == "EXAM" and plan[1]["draw_count"] == 2
    assert plan[2]["assessment"] == "REVIEW" and plan[2]["draw_count"] == 0

    bank = {row["position"]: row for row in saved.json()["question_banks"]}
    assert len(bank[1]["questions"]) == 2
    assert bank[1]["questions"][0]["correct_answer"] == "El de la izquierda"
    # The bank lives in the course's own table, never in honor_questions.
    rows = await fetch_all(
        "SELECT requirement_position, position FROM course_questions WHERE course_id = :c"
        " ORDER BY position",
        c=uuid.UUID(course["id"]),
    )
    assert [(r["requirement_position"], r["position"]) for r in rows] == [(1, 1), (1, 2)]


@pytest.mark.asyncio
async def test_a_practical_requirement_never_becomes_an_exam(client, world, factory):
    honor = await _honor(factory, "practical", (True, False))
    course = await _draft(client, world, honor)
    refused = await _set_bank(client, world, course["id"], 2, [TF])
    assert refused.status_code == 409
    assert "práctico" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_the_bank_never_reaches_a_member(client, world, factory):
    honor = await _honor(factory, "secret")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC])).status_code == 200
    await _publish(client, world, course["id"])

    public = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert public.status_code == 200
    assert "question_banks" not in public.json()
    assert "SECRETO-EXPLICACION" not in public.text
    assert "correct_answer" not in public.text
    # ...and the shop window does not carry it either.
    listing = await client.get(f"{COURSES}?honor_id={honor['id']}")
    assert "SECRETO-EXPLICACION" not in listing.text

    # The author and the reviewers do see it.
    for who in ("instructor", "zone_coordinator", "assoc_admin", "master"):
        staff = await _staff(client, world, course["id"], who)
        assert staff.status_code == 200, who
        assert "SECRETO-EXPLICACION" in staff.text, who
    assert (
        await client.get(
            f"{COURSES}/{course['id']}/instructor", headers=world["member"]["headers"]
        )
    ).status_code == 404


async def _publish(client, world, course_id) -> dict:
    assert (await _submit(client, world, course_id)).status_code == 200
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


# ----------------------------------------------------------------------------
# What a question may be
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        {**MC, "options": ["Uno"]},  # fewer than two options
        {**MC, "options": ["Uno", "Uno"]},  # repeated options
        {**MC, "options": ["a", "b", "c", "d", "e", "f", "g"]},  # more than six
        {**MC, "correct_answer": "Ninguna de las opciones"},
        {**MC, "options": None},
        {**TF, "correct_answer": "quizás"},
        {**TF, "options": ["Sí", "No"]},  # TRUE_FALSE carries no options
        {**MC, "points": 0},
        {**MC, "points": 11},
        {"question_text": "", "question_type": "TRUE_FALSE", "correct_answer": "true"},
    ],
)
async def test_an_invalid_question_is_refused(client, world, factory, broken):
    honor = await _honor(factory, f"invalid-{abs(hash(str(broken))) % 10000}")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [broken])).status_code == 422


@pytest.mark.asyncio
async def test_a_short_answer_accepts_up_to_ten_alternatives(client, world, factory):
    honor = await _honor(factory, "short")
    course = await _draft(client, world, honor)
    short = {
        "question_text": "¿Cómo se llama el nudo?",
        "question_type": "SHORT_ANSWER",
        "correct_answer": "llano|cuadrado",
    }
    assert (await _set_bank(client, world, course["id"], 1, [short])).status_code == 200
    too_many = {**short, "correct_answer": "|".join(str(n) for n in range(11))}
    assert (await _set_bank(client, world, course["id"], 1, [too_many])).status_code == 422


# ----------------------------------------------------------------------------
# Exam parameters
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exam_parameters_are_saved_and_bounded(client, world, factory):
    honor = await _honor(factory, "params")
    course = await _draft(client, world, honor)
    url = f"{COURSES}/{course['id']}"
    saved = await client.put(
        url,
        json={"exam_passing_score": 90, "exam_time_limit_minutes": 30, "max_exam_attempts": 2},
        headers=world["instructor"]["headers"],
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["exam_passing_score"] == 90
    assert saved.json()["exam_time_limit_minutes"] == 30
    assert saved.json()["max_exam_attempts"] == 2

    for bad in (
        {"exam_passing_score": 79},  # 80 is the floor of the vision, never lower
        {"exam_passing_score": 101},
        {"exam_time_limit_minutes": 4},
        {"exam_time_limit_minutes": 181},
        {"max_exam_attempts": 0},
        {"max_exam_attempts": 11},
        {"exam_mode": "CAMERA"},
    ):
        assert (
            await client.put(url, json=bad, headers=world["instructor"]["headers"])
        ).status_code == 422, bad


@pytest.mark.asyncio
async def test_a_published_course_does_not_change_its_exam(client, world, factory):
    honor = await _honor(factory, "immutable")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC, TF], 1)).status_code == 200
    await _publish(client, world, course["id"])
    assert (
        await client.put(
            f"{COURSES}/{course['id']}",
            json={"exam_passing_score": 95},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 400
    assert (await _set_bank(client, world, course["id"], 1, [MC])).status_code == 400


# ----------------------------------------------------------------------------
# The plan and the bank move together
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_plan_keeps_the_bank_and_clears_it_when_the_exam_goes(client, world, factory):
    honor = await _honor(factory, "plan")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC, TF], 2)).status_code == 200

    kept = await client.put(
        f"{COURSES}/{course['id']}/plan",
        json=[
            {"position": 1, "assessment": "EXAM"},
            {"position": 2, "assessment": "EVIDENCE", "guidance": "Sube una foto"},
        ],
        headers=world["instructor"]["headers"],
    )
    assert kept.status_code == 200, kept.text
    assert _plan_by_position(kept.json())[1]["draw_count"] == 2
    assert len(kept.json()["question_banks"][0]["questions"]) == 2

    dropped = await client.put(
        f"{COURSES}/{course['id']}/plan",
        json=[{"position": 1, "assessment": "REVIEW"}, {"position": 2, "assessment": "EVIDENCE"}],
        headers=world["instructor"]["headers"],
    )
    assert dropped.status_code == 200, dropped.text
    assert _plan_by_position(dropped.json())[1]["draw_count"] == 0
    assert dropped.json()["question_banks"] == []
    assert (
        await fetch_all(
            "SELECT id FROM course_questions WHERE course_id = :c", c=uuid.UUID(course["id"])
        )
        == []
    )


@pytest.mark.asyncio
async def test_the_plan_cannot_declare_exam_without_a_bank(client, world, factory):
    honor = await _honor(factory, "no-bank")
    course = await _draft(client, world, honor)
    refused = await client.put(
        f"{COURSES}/{course['id']}/plan",
        json=[{"position": 1, "assessment": "EXAM"}, {"position": 2, "assessment": "REVIEW"}],
        headers=world["instructor"]["headers"],
    )
    assert refused.status_code == 422
    assert "banco" in refused.json()["detail"].lower()


# ----------------------------------------------------------------------------
# Sending to review: nobody publishes an exam that cannot be taken
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_refuses_a_bank_shorter_than_the_draw(client, world, factory):
    honor = await _honor(factory, "short-bank")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC, TF], 2)).status_code == 200
    # Shrinking the bank behind the draw is caught before anyone can sit the exam.
    async with SessionLocal() as db:
        await db.execute(
            text("DELETE FROM course_questions WHERE course_id = :c AND position = 2"),
            {"c": uuid.UUID(course["id"])},
        )
        await db.commit()
    refused = await _submit(client, world, course["id"])
    assert refused.status_code == 400
    assert "banco" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_submit_refuses_more_than_sixty_drawn_questions(client, world, factory):
    honor = await _honor(factory, "too-many")
    course = await _draft(client, world, honor)
    for position, drawn in ((1, 31), (2, 30)):
        bank = [{**TF, "question_text": f"Afirmación {position}.{n}"} for n in range(drawn)]
        saved = await _set_bank(client, world, course["id"], position, bank, drawn)
        assert saved.status_code == 200, saved.text
    refused = await _submit(client, world, course["id"])
    assert refused.status_code == 400 and "61" in refused.json()["detail"]
    # ...and no single requirement may draw more than the whole exam is allowed to.
    over = await _set_bank(client, world, course["id"], 1, [TF] * 61, 61)
    assert over.status_code == 422


@pytest.mark.asyncio
async def test_submit_refuses_questions_nobody_can_grade_yet(client, world, factory):
    """I6 gate: until manual grading exists, no attempt may end up waiting for a grader."""
    honor = await _honor(factory, "needs-grader")
    course = await _draft(client, world, honor)
    essay = {
        "question_text": "Explica cuándo usarías un nudo llano.",
        "question_type": "ESSAY",
        "correct_answer": "Rúbrica: menciona dos usos.",
    }
    assert (await _set_bank(client, world, course["id"], 1, [essay])).status_code == 200
    refused = await _submit(client, world, course["id"])
    assert refused.status_code == 400
    assert "calificación manual" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_submit_refuses_an_in_person_exam_for_now(client, world, factory):
    honor = await _honor(factory, "in-person")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC])).status_code == 200
    assert (
        await client.put(
            f"{COURSES}/{course['id']}",
            json={"exam_mode": "IN_PERSON"},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 200
    refused = await _submit(client, world, course["id"])
    assert refused.status_code == 400 and "presencial" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_a_short_bank_is_a_warning_and_not_a_wall(client, world, factory):
    honor = await _honor(factory, "warn")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC, TF], 2)).status_code == 200
    staff = await _staff(client, world, course["id"])
    assert any("variará" in warning for warning in staff.json()["warnings"])
    assert (await _submit(client, world, course["id"])).status_code == 200
    # The reviewer reads the same warning before approving.
    review_view = await _staff(client, world, course["id"], "zone_coordinator")
    assert any("variará" in warning for warning in review_view.json()["warnings"])


# ----------------------------------------------------------------------------
# Importing the instructor's own honor bank
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_importing_the_honor_bank_needs_to_be_its_creator(client, world, factory):
    someone_elses = await _honor(factory, "not-mine")
    await _honor_bank(someone_elses["id"], 1)
    course = await _draft(client, world, someone_elses)
    refused = await client.post(
        f"{COURSES}/{course['id']}/import-honor-bank", headers=world["instructor"]["headers"]
    )
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_importing_copies_the_bank_of_my_own_honor(client, world, factory):
    mine = await _honor(factory, "mine", (True, False), created_by=world["instructor"]["id"])
    await _honor_bank(mine["id"], 1, how_many=3)
    await _honor_bank(mine["id"], 2, how_many=2)  # practical: never imported
    course = await _draft(client, world, mine)
    imported = await client.post(
        f"{COURSES}/{course['id']}/import-honor-bank", headers=world["instructor"]["headers"]
    )
    assert imported.status_code == 200, imported.text
    plan = _plan_by_position(imported.json())
    assert plan[1]["assessment"] == "EXAM" and plan[1]["draw_count"] == 1
    assert plan[2]["assessment"] == "EVIDENCE" and plan[2]["draw_count"] == 0
    banks = {row["position"]: row for row in imported.json()["question_banks"]}
    assert len(banks[1]["questions"]) == 3 and 2 not in banks


# ----------------------------------------------------------------------------
# Versions carry the exam with them
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_new_version_copies_the_bank_and_the_parameters(client, world, factory):
    honor = await _honor(factory, "version")
    course = await _draft(client, world, honor)
    assert (await _set_bank(client, world, course["id"], 1, [MC, TF], 2)).status_code == 200
    assert (
        await client.put(
            f"{COURSES}/{course['id']}",
            json={"exam_passing_score": 85, "max_exam_attempts": 5},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 200
    await _publish(client, world, course["id"])

    version = await client.post(
        f"{COURSES}/{course['id']}/version",
        json={"changes_description": "Más preguntas"},
        headers=world["instructor"]["headers"],
    )
    assert version.status_code == 201, version.text
    copy = version.json()
    assert copy["exam_passing_score"] == 85 and copy["max_exam_attempts"] == 5
    assert _plan_by_position(copy)[1]["draw_count"] == 2
    assert len(copy["question_banks"][0]["questions"]) == 2
    # Two independent banks: editing the copy never touches the published course.
    rows = await fetch_all(
        "SELECT course_id FROM course_questions WHERE course_id IN (:a, :b)",
        a=uuid.UUID(course["id"]),
        b=uuid.UUID(copy["id"]),
    )
    assert len(rows) == 4
