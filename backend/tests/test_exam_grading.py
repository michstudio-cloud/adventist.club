"""Bloque C · I6 — Calificación manual, anulación de intentos y sesión presencial.

Las tres piezas que I5 dejó fuera, y las tres son superficie de seguridad:

  * calificar es leer lo que escribió un menor y decidir su nota, así que sólo lo hace el
    instructor de *ese* curso mientras su carta sigue autorizada — nunca sobre su propio
    intento ni sobre el curso de otro;
  * anular es revertir un resultado ya ganado, así que revierte **exactamente** las
    posiciones que el intento completó y jamás toca una inscripción certificada;
  * el código de la sesión presencial es un secreto compartido: se guarda con hash, caduca,
    vale para un solo curso y se bloquea tras cinco fallos.
"""

import hashlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

COURSES = "/api/v1/courses"
EXAMS = "/api/v1/exams"
ENROLLMENTS = "/api/v1/portfolio/enrollments"

TEXT_BLOCK = {"type": "text", "markdown": "## Nudos\nPractica el nudo llano."}
RUBRIC = "RUBRICA-SECRETA"


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(
                        text(
                            "SELECT to_regclass('public.exam_attempts') IS NOT NULL"
                            " AND EXISTS (SELECT 1 FROM information_schema.columns"
                            "   WHERE table_name = 'courses'"
                            "     AND column_name = 'session_code_hash')"
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
        reason="apply migrations/010_exams.sql and 010b_exam_session_code.sql",
    ),
]
factory = module_factory("exam-grading")


# ----------------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------------
async def _authorize(user_id: str, organization_id: str) -> None:
    letter_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO church_letters (id, user_id, organization_id, church_name,"
                " storage_key, content_type, size_bytes, status) VALUES (:id, :user, :org,"
                " 'Iglesia', :key, 'application/pdf', 1024, 'AUTHORIZED')"
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
    club = await factory.org("club", "club", association)
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", association["id"]),
        "other_instructor": await factory.user("other-inst", "INSTRUCTOR", association["id"]),
        "member": await factory.user("member", "STUDENT", club["id"]),
        "member2": await factory.user("member2", "STUDENT", club["id"]),
        "member3": await factory.user("member3", "STUDENT", club["id"]),
        "member4": await factory.user("member4", "STUDENT", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
    }
    for who in ("instructor", "other_instructor"):
        await _authorize(people[who]["id"], association["id"])
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO guardianships (guardian_id, child_id, consent_status)"
                " VALUES (:g, :c, 'APPROVED')"
            ),
            {"g": uuid.UUID(people["guardian"]["id"]), "c": uuid.UUID(people["minor"]["id"])},
        )
        await db.commit()
    return {**people, "association": association, "zone": zone, "club": club}


async def _honor(factory, label, theoretical=(True,)) -> dict:
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (
            await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))
        ).scalar_one()
        await db.execute(
            text(
                "INSERT INTO honors (id, ministry_id, name, slug, active, status)"
                " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED')"
            ),
            {
                "id": honor_id,
                "m": ministry,
                "name": name,
                "slug": f"{factory.prefix}-{label}-{honor_id.hex[:6]}",
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


def _mc(number: int) -> dict:
    return {
        "question_text": f"Pregunta {number}",
        "question_type": "MULTIPLE_CHOICE",
        "options": [f"Correcta {number}", f"Falsa {number}"],
        "correct_answer": f"Correcta {number}",
        "points": 1,
    }


def _essay(number: int, points: int = 4) -> dict:
    return {
        "question_text": f"Explica el punto {number}",
        "question_type": "ESSAY",
        "correct_answer": RUBRIC,
        "points": points,
    }


def _short(number: int) -> dict:
    return {
        "question_text": f"¿Cómo se llama el nudo {number}?",
        "question_type": "SHORT_ANSWER",
        "correct_answer": "llano",
        "points": 1,
    }


async def _course(
    client, world, factory, label, *, bank, draw=None, theoretical=(True,), owner="instructor",
    **update,
) -> dict:
    """A published course whose requirement 1 is settled by the exam, with `bank` questions."""
    honor = await _honor(factory, label, theoretical)
    headers = world[owner]["headers"]
    created = await client.post(
        COURSES, json={"honor_id": honor["id"], "title": f"Curso {label}"}, headers=headers
    )
    assert created.status_code == 201, created.text
    course_id = created.json()["id"]
    assert (
        await client.post(
            f"{COURSES}/{course_id}/lessons",
            json={"title": "Lección 1", "blocks": [TEXT_BLOCK]},
            headers=headers,
        )
    ).status_code == 201
    saved = await client.put(
        f"{COURSES}/{course_id}/requirements/1/questions",
        json={"draw_count": draw or len(bank), "question_bank": bank},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    if update:
        changed = await client.put(f"{COURSES}/{course_id}", json=update, headers=headers)
        assert changed.status_code == 200, changed.text
    submitted = await client.post(f"{COURSES}/{course_id}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    for reviewer in ("zone_coordinator", "assoc_admin"):
        step = await client.post(
            f"{COURSES}/{course_id}/review",
            json={"action": "APPROVE"},
            headers=world[reviewer]["headers"],
        )
        assert step.status_code == 200, step.text
    return {"id": course_id, "honor": honor}


async def _join(client, user, course_id) -> dict:
    joined = await client.post(f"{COURSES}/{course_id}/join", headers=user["headers"])
    assert joined.status_code == 200, joined.text
    return joined.json()


async def _start(client, user, enrollment_id, **payload):
    return await client.post(
        f"{EXAMS}/enrollments/{enrollment_id}/attempts",
        json={"pledge": True, **payload},
        headers=user["headers"],
    )


async def _answer(client, user, attempt_id, position, response):
    return await client.put(
        f"{EXAMS}/attempts/{attempt_id}/answers/{position}",
        json={"response": response},
        headers=user["headers"],
    )


async def _answer_all(client, user, paper, *, correct=True, essay="Mi respuesta") -> None:
    for question in paper["questions"]:
        if question["question_type"] == "MULTIPLE_CHOICE":
            value = str(
                next(
                    i
                    for i, option in enumerate(question["options"])
                    if option.startswith("Correcta") is correct
                )
            )
        elif question["question_type"] == "SHORT_ANSWER":
            value = "llano" if correct else "algo que el instructor no previó"
        else:
            value = essay
        saved = await _answer(client, user, paper["id"], question["position"], value)
        assert saved.status_code == 200, saved.text


async def _submit(client, user, attempt_id):
    return await client.post(f"{EXAMS}/attempts/{attempt_id}/submit", headers=user["headers"])


async def _judge(client, world, user, enrollment_id, position) -> dict:
    """The member sends the requirement in and the instructor completes it (Bloque A)."""
    sent = await client.put(
        f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}",
        json={"status": "SUBMITTED", "member_note": "Hecho"},
        headers=user["headers"],
    )
    assert sent.status_code == 200, sent.text
    judged = await client.post(
        f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}/review",
        json={"verdict": "COMPLETE", "note": "Visto"},
        headers=world["instructor"]["headers"],
    )
    assert judged.status_code == 200, judged.text
    return judged.json()


async def _grade(client, who, attempt_id, position, points, note=None):
    body = {"points_awarded": points}
    if note is not None:
        body["note"] = note
    return await client.post(
        f"{EXAMS}/attempts/{attempt_id}/answers/{position}/grade",
        json=body,
        headers=who["headers"],
    )


async def _pending_positions(attempt_id: str) -> list[int]:
    rows = await fetch_all(
        "SELECT position FROM exam_answers WHERE attempt_id = :a AND points_awarded IS NULL"
        " ORDER BY position",
        a=uuid.UUID(attempt_id),
    )
    return [row["position"] for row in rows]


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id)
    )
    return [row["action"] for row in rows]


# ----------------------------------------------------------------------------
# The submit gate of I5 is gone
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_course_may_now_be_published_with_questions_a_person_grades(
    client, world, factory
):
    """Until I6 the review gate refused SHORT_ANSWER and ESSAY so no answer would wait for
    a grader that did not exist. The grader exists now."""
    course = await _course(client, world, factory, "gate-essay", bank=[_essay(1)])
    detail = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert detail.status_code == 200 and detail.json()["status"] == "PUBLISHED"


@pytest.mark.asyncio
async def test_an_in_person_course_may_now_be_published(client, world, factory):
    course = await _course(
        client, world, factory, "gate-presencial", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    detail = await client.get(f"{COURSES}/{course['id']}", headers=world["member"]["headers"])
    assert detail.json()["exam_mode"] == "IN_PERSON"


# ----------------------------------------------------------------------------
# Manual grading
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_essay_waits_for_the_instructor_and_shows_no_score_meanwhile(
    client, world, factory
):
    course = await _course(client, world, factory, "essay", bank=[_mc(1), _essay(1)])
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper)
    result = await _submit(client, world["member"], paper["id"])

    assert result.status_code == 200, result.text
    body = result.json()
    # D5: «Tu instructor está revisando tus respuestas»; ninguna nota, ningún desglose.
    assert body["status"] == "PENDING_GRADING"
    assert body["points_awarded"] is None and body["score_percent"] is None
    assert body["breakdown"] == [] and body["feedback"] == []
    assert RUBRIC not in result.text


@pytest.mark.asyncio
async def test_the_grading_queue_belongs_to_the_instructor_of_the_course(client, world, factory):
    course = await _course(client, world, factory, "queue", bank=[_essay(1)])
    enrollment = await _join(client, world["member2"], course["id"])
    paper = (await _start(client, world["member2"], enrollment["id"])).json()
    await _answer_all(client, world["member2"], paper)
    assert (await _submit(client, world["member2"], paper["id"])).json()["status"] == "PENDING_GRADING"

    url = f"{EXAMS}/grading/queue?course_id={course['id']}"
    mine = await client.get(url, headers=world["instructor"]["headers"])
    assert mine.status_code == 200, mine.text
    rows = mine.json()
    assert [row["attempt_id"] for row in rows] == [paper["id"]]
    assert rows[0]["member"]["id"] == world["member2"]["id"]
    assert set(rows[0]["member"]) == {"id", "name"}
    assert rows[0]["pending_answers"] == 1 and rows[0]["course_title"].startswith("Curso")
    # No contact detail of a member ever reaches the instructor (§6).
    assert "email" not in mine.text

    # Another instructor has their own queue, and this attempt is never in it...
    await _course(client, world, factory, "queue-other", bank=[_mc(1)], owner="other_instructor")
    other = await client.get(
        f"{EXAMS}/grading/queue", headers=world["other_instructor"]["headers"]
    )
    assert other.status_code == 200 and other.json() == []
    # ...and whoever teaches nothing has no queue at all.
    assert (await client.get(url, headers=world["member"]["headers"])).status_code == 403
    assert (await client.get(url, headers=world["director"]["headers"])).status_code == 403


@pytest.mark.asyncio
async def test_grading_the_last_pending_answer_closes_the_attempt_and_completes_the_requirement(
    client, world, factory
):
    course = await _course(client, world, factory, "grade-pass", bank=[_mc(1), _essay(1, points=4)])
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper)
    assert (await _submit(client, world["member"], paper["id"])).json()["status"] == "PENDING_GRADING"

    position = (await _pending_positions(paper["id"]))[0]
    graded = await _grade(client, world["instructor"], paper["id"], position, 4, "Bien explicado")
    assert graded.status_code == 200, graded.text
    body = graded.json()
    assert body["status"] == "PASSED" and body["points_awarded"] == 5
    assert body["finished_at"] is not None

    detail = await client.get(
        f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member"]["headers"]
    )
    rows = {row["position"]: row for row in detail.json()["requirements"]}
    assert rows[1]["status"] == "COMPLETE" and rows[1]["completed_via"] == "EXAM"
    stored = await fetch_one(
        "SELECT graded_by_id, grader_note FROM exam_answers WHERE attempt_id = :a"
        " AND position = :p",
        a=uuid.UUID(paper["id"]),
        p=position,
    )
    assert str(stored["graded_by_id"]) == world["instructor"]["id"]
    assert stored["grader_note"] == "Bien explicado"
    assert "EXAM_GRADE" in await _audit_actions(paper["id"])


@pytest.mark.asyncio
async def test_a_grade_that_cannot_reach_the_threshold_fails_the_attempt(client, world, factory):
    course = await _course(client, world, factory, "grade-fail", bank=[_mc(1), _essay(1, points=4)])
    enrollment = await _join(client, world["member3"], course["id"])
    paper = (await _start(client, world["member3"], enrollment["id"])).json()
    await _answer_all(client, world["member3"], paper)
    await _submit(client, world["member3"], paper["id"])

    position = (await _pending_positions(paper["id"]))[0]
    graded = await _grade(client, world["instructor"], paper["id"], position, 1)
    assert graded.status_code == 200 and graded.json()["status"] == "FAILED"
    detail = await client.get(
        f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member3"]["headers"]
    )
    assert {r["position"]: r for r in detail.json()["requirements"]}[1]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_only_the_instructor_of_that_course_grades_and_never_twice(client, world, factory):
    course = await _course(client, world, factory, "grade-who", bank=[_essay(1)])
    enrollment = await _join(client, world["minor"], course["id"])
    paper = (await _start(client, world["minor"], enrollment["id"])).json()
    await _answer_all(client, world["minor"], paper)
    await _submit(client, world["minor"], paper["id"])
    position = (await _pending_positions(paper["id"]))[0]

    for who in ("member", "minor", "guardian", "director", "other_instructor", "assoc_admin"):
        refused = await _grade(client, world[who], paper["id"], position, 4)
        assert refused.status_code == 403, f"{who}: {refused.text}"

    # ...and the instructor loses it the instant the letter is revoked.
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'REVOKED' WHERE user_id = :u"),
            {"u": uuid.UUID(world["instructor"]["id"])},
        )
        await db.commit()
    assert (await _grade(client, world["instructor"], paper["id"], position, 4)).status_code == 403
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'AUTHORIZED' WHERE user_id = :u"),
            {"u": uuid.UUID(world["instructor"]["id"])},
        )
        await db.commit()

    assert (await _grade(client, world["instructor"], paper["id"], position, 9)).status_code == 422
    assert (await _grade(client, world["instructor"], paper["id"], position, -1)).status_code == 422
    assert (await _grade(client, world["instructor"], paper["id"], 99, 1)).status_code == 404
    assert (await _grade(client, world["instructor"], paper["id"], position, 4)).status_code == 200
    again = await _grade(client, world["instructor"], paper["id"], position, 0)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_an_unforeseen_short_answer_waits_for_a_person_instead_of_being_wrong(
    client, world, factory
):
    course = await _course(client, world, factory, "short", bank=[_short(1)])
    enrollment = await _join(client, world["member4"], course["id"])
    paper = (await _start(client, world["member4"], enrollment["id"])).json()
    await _answer_all(client, world["member4"], paper, correct=False)
    assert (await _submit(client, world["member4"], paper["id"])).json()["status"] == "PENDING_GRADING"
    position = (await _pending_positions(paper["id"]))[0]
    graded = await _grade(client, world["instructor"], paper["id"], position, 1, "También vale")
    assert graded.status_code == 200 and graded.json()["status"] == "PASSED"


@pytest.mark.asyncio
async def test_a_member_waiting_for_a_grade_cannot_start_another_attempt(client, world, factory):
    course = await _course(
        client, world, factory, "wait", bank=[_essay(1)], max_exam_attempts=3
    )
    enrollment = await _join(client, world["member2"], course["id"])
    paper = (await _start(client, world["member2"], enrollment["id"])).json()
    await _answer_all(client, world["member2"], paper)
    await _submit(client, world["member2"], paper["id"])
    blocked = await _start(client, world["member2"], enrollment["id"])
    assert blocked.status_code == 409 and "revisando" in blocked.json()["detail"]


# ----------------------------------------------------------------------------
# Voiding an attempt
# ----------------------------------------------------------------------------
async def _void(client, who, attempt_id, reason="Fallo técnico de la sala"):
    return await client.post(
        f"{EXAMS}/attempts/{attempt_id}/void", json={"reason": reason}, headers=who["headers"]
    )


@pytest.mark.asyncio
async def test_voiding_a_passed_attempt_reverts_exactly_what_it_completed(client, world, factory):
    course = await _course(
        client, world, factory, "void-revert", bank=[_mc(1)], theoretical=(True, True)
    )
    # Requirement 2 is settled by a human verdict, and stays settled after the void.
    enrollment = await _join(client, world["member"], course["id"])
    await _judge(client, world, world["member"], enrollment["id"], 2)

    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper)
    assert (await _submit(client, world["member"], paper["id"])).json()["status"] == "PASSED"
    ready = await client.get(f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member"]["headers"])
    assert ready.json()["status"] == "READY"

    voided = await _void(client, world["instructor"], paper["id"])
    assert voided.status_code == 200, voided.text
    assert voided.json()["status"] == "VOIDED"
    assert voided.json()["void_reason"] == "Fallo técnico de la sala"

    after = await client.get(f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member"]["headers"])
    rows = {row["position"]: row for row in after.json()["requirements"]}
    assert rows[1]["status"] == "PENDING" and rows[1]["completed_via"] is None
    assert rows[2]["status"] == "COMPLETE"  # the human verdict is untouched
    assert after.json()["status"] == "IN_PROGRESS"  # READY recalculated
    assert "EXAM_VOID" in await _audit_actions(paper["id"])


@pytest.mark.asyncio
async def test_a_voided_attempt_does_not_count_and_the_member_sees_the_reason(
    client, world, factory
):
    course = await _course(
        client, world, factory, "void-count", bank=[_mc(1), _mc(2)], draw=1, max_exam_attempts=1
    )
    enrollment = await _join(client, world["member3"], course["id"])
    first = (await _start(client, world["member3"], enrollment["id"])).json()
    await _answer_all(client, world["member3"], first, correct=False)
    assert (await _submit(client, world["member3"], first["id"])).json()["status"] == "FAILED"
    assert (await _start(client, world["member3"], enrollment["id"])).status_code == 409

    assert (await _void(client, world["instructor"], first["id"], "Otra oportunidad")).status_code == 200
    seen = await client.get(
        f"{EXAMS}/attempts/{first['id']}", headers=world["member3"]["headers"]
    )
    assert seen.json()["status"] == "VOIDED" and seen.json()["void_reason"] == "Otra oportunidad"
    assert (await _start(client, world["member3"], enrollment["id"])).status_code == 200


@pytest.mark.asyncio
async def test_who_may_void_and_who_may_not(client, world, factory):
    course = await _course(client, world, factory, "void-who", bank=[_mc(1)])
    enrollment = await _join(client, world["minor"], course["id"])
    paper = (await _start(client, world["minor"], enrollment["id"])).json()

    for who in ("minor", "guardian", "member", "director", "other_instructor", "assoc_admin"):
        refused = await _void(client, world[who], paper["id"])
        assert refused.status_code == 403, f"{who}: {refused.text}"
    assert (await _void(client, world["instructor"], paper["id"], "")).status_code == 422
    assert (await _void(client, world["master"], paper["id"])).status_code == 200
    assert (await _void(client, world["instructor"], paper["id"])).status_code == 409


@pytest.mark.asyncio
async def test_a_certified_enrollment_is_never_touched_by_a_void(
    client, world, factory, monkeypatch
):
    from app.config import settings

    issuer = await factory.org("issuer-void", "association")
    code = f"{factory.prefix}-VOID"
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE organizations SET code = :c WHERE id = :id"),
            {"c": code, "id": uuid.UUID(issuer["id"])},
        )
        await db.commit()
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", code)

    course = await _course(
        client, world, factory, "void-certified", bank=[_mc(1)], theoretical=(True, True)
    )
    enrollment = await _join(client, world["member4"], course["id"])
    paper = (await _start(client, world["member4"], enrollment["id"])).json()
    await _answer_all(client, world["member4"], paper)
    assert (await _submit(client, world["member4"], paper["id"])).json()["status"] == "PASSED"
    # Requirement 2 is REVIEW: a human verdict is what turns the enrollment READY.
    judged = await _judge(client, world, world["member4"], enrollment["id"], 2)
    assert judged["status"] == "READY"
    issued = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/certificate",
        json={"issued_date": "2026-09-21"},
        headers=world["instructor"]["headers"],
    )
    assert issued.status_code == 201, issued.text
    refused = await _void(client, world["instructor"], paper["id"])
    assert refused.status_code == 409 and "certificad" in refused.json()["detail"].lower()


# ----------------------------------------------------------------------------
# The in-person session code
# ----------------------------------------------------------------------------
def _hash(course_id: str, code: str) -> str:
    return hashlib.sha256(f"{course_id}:{code}".encode()).hexdigest()


async def _open_session(client, who, course_id, **payload):
    return await client.post(
        f"{COURSES}/{course_id}/exam-session", json=payload, headers=who["headers"]
    )


@pytest.mark.asyncio
async def test_the_session_code_is_dictated_once_and_stored_hashed(client, world, factory):
    course = await _course(
        client, world, factory, "session", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    opened = await _open_session(client, world["instructor"], course["id"], minutes=30)
    assert opened.status_code == 200, opened.text
    code = opened.json()["code"]
    assert len(code) == 6 and opened.json()["expires_at"] is not None
    assert not (set(code) & set("01OI"))  # no ambiguous characters to dictate

    row = await fetch_one(
        "SELECT session_code, session_code_hash FROM courses WHERE id = :id",
        id=uuid.UUID(course["id"]),
    )
    assert row["session_code"] is None  # the plain column of I5 stays unused
    assert row["session_code_hash"] == _hash(course["id"], code)
    assert "EXAM_SESSION_OPEN" in await _audit_actions(course["id"])
    # The code itself is never written to the audit trail.
    details = await fetch_all(
        "SELECT details, metadata_json FROM audit_log WHERE entity_id = :id", id=course["id"]
    )
    assert all(code not in str(row) for row in details)


@pytest.mark.asyncio
async def test_an_in_person_exam_needs_a_live_code_and_marks_the_attempt_proctored(
    client, world, factory
):
    course = await _course(
        client, world, factory, "proctored", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    enrollment = await _join(client, world["member"], course["id"])
    assert (await _start(client, world["member"], enrollment["id"])).status_code == 403

    code = (await _open_session(client, world["instructor"], course["id"])).json()["code"]
    wrong = await _start(client, world["member"], enrollment["id"], session_code="ZZZZZZ")
    assert wrong.status_code == 403
    started = await _start(client, world["member"], enrollment["id"], session_code=code.lower())
    assert started.status_code == 200, started.text
    assert started.json()["proctored"] is True
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'EXAM_START'",
        id=started.json()["id"],
    )
    assert audit["metadata_json"]["proctored"] is True


@pytest.mark.asyncio
async def test_a_code_of_another_course_never_opens_this_exam(client, world, factory):
    mine = await _course(
        client, world, factory, "code-mine", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    theirs = await _course(
        client, world, factory, "code-theirs", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    code = (await _open_session(client, world["instructor"], theirs["id"])).json()["code"]
    enrollment = await _join(client, world["member2"], mine["id"])
    refused = await _start(client, world["member2"], enrollment["id"], session_code=code)
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_an_expired_or_closed_session_stops_working(client, world, factory):
    course = await _course(
        client, world, factory, "expiry", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    code = (await _open_session(client, world["instructor"], course["id"])).json()["code"]
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE courses SET session_code_expires_at = now() - interval '1 minute'"
                 " WHERE id = :id"),
            {"id": uuid.UUID(course["id"])},
        )
        await db.commit()
    enrollment = await _join(client, world["member3"], course["id"])
    assert (
        await _start(client, world["member3"], enrollment["id"], session_code=code)
    ).status_code == 403

    fresh = (await _open_session(client, world["instructor"], course["id"])).json()["code"]
    closed = await client.delete(
        f"{COURSES}/{course['id']}/exam-session", headers=world["instructor"]["headers"]
    )
    assert closed.status_code == 204
    assert (
        await _start(client, world["member3"], enrollment["id"], session_code=fresh)
    ).status_code == 403
    row = await fetch_one(
        "SELECT session_code_hash FROM courses WHERE id = :id", id=uuid.UUID(course["id"])
    )
    assert row["session_code_hash"] is None
    assert "EXAM_SESSION_CLOSE" in await _audit_actions(course["id"])


@pytest.mark.asyncio
async def test_five_wrong_codes_lock_the_member_out(client, world, factory):
    course = await _course(
        client, world, factory, "bruteforce", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    code = (await _open_session(client, world["instructor"], course["id"])).json()["code"]
    enrollment = await _join(client, world["member4"], course["id"])
    for _ in range(5):
        assert (
            await _start(client, world["member4"], enrollment["id"], session_code="AAAAAA")
        ).status_code == 403
    # ...and now even the right code waits: guessing six characters is not a way in.
    locked = await _start(client, world["member4"], enrollment["id"], session_code=code)
    assert locked.status_code == 429


@pytest.mark.asyncio
async def test_only_the_verified_author_opens_a_session_on_a_published_course(
    client, world, factory
):
    course = await _course(
        client, world, factory, "session-who", bank=[_mc(1)], exam_mode="IN_PERSON"
    )
    for who in ("member", "other_instructor", "director", "assoc_admin"):
        refused = await _open_session(client, world[who], course["id"])
        assert refused.status_code in (403, 404), f"{who}: {refused.text}"
    assert (
        await _open_session(client, world["instructor"], course["id"], minutes=1)
    ).status_code == 422
    assert (
        await _open_session(client, world["instructor"], course["id"], minutes=999)
    ).status_code == 422


@pytest.mark.asyncio
async def test_an_online_exam_accepts_a_code_but_does_not_require_one(client, world, factory):
    course = await _course(client, world, factory, "online-code", bank=[_mc(1)])
    enrollment = await _join(client, world["member"], course["id"])
    started = await _start(client, world["member"], enrollment["id"])
    assert started.status_code == 200 and started.json()["proctored"] is False
