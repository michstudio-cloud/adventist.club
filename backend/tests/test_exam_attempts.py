"""Bloque C · I5 — Intentos de examen: sorteo, autoguardado, reanudar, plazo y calificación.

Un examen es una superficie de seguridad, así que la mayoría de estas pruebas comprueban que
algo **no** pasa: que las respuestas correctas no salen antes de tiempo, que el cliente no
elige sus preguntas, que el reloj es el del servidor, que nadie lee el intento de otro y que
nadie rinde más intentos de los que le tocan.
"""

import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

COURSES = "/api/v1/courses"
EXAMS = "/api/v1/exams"
PORTFOLIO = "/api/v1/portfolio"
ENROLLMENTS = f"{PORTFOLIO}/enrollments"

TEXT_BLOCK = {"type": "text", "markdown": "## Nudos\nPractica el nudo llano."}
SECRET = "SECRETO-EXPLICACION"


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(text("SELECT to_regclass('public.exam_attempts') IS NOT NULL"))
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
factory = module_factory("exam-attempts")


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
    await _authorize(people["instructor"]["id"], association["id"])
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO guardianships (guardian_id, child_id, consent_status)"
                " VALUES (:g, :c, 'APPROVED')"
            ),
            {
                "g": uuid.UUID(people["guardian"]["id"]),
                "c": uuid.UUID(people["minor"]["id"]),
            },
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
    """A question whose right answer is always «Correcta N»."""
    return {
        "question_text": f"Pregunta {number}",
        "question_type": "MULTIPLE_CHOICE",
        "options": [f"Correcta {number}", f"Falsa {number}", f"Otra {number}"],
        "correct_answer": f"Correcta {number}",
        "points": 1,
        "explanation": SECRET,
    }


async def _course_with_exam(
    client, world, factory, label, *, bank=3, draw=2, theoretical=(True,), **update
) -> dict:
    honor = await _honor(factory, label, theoretical)
    created = await client.post(
        COURSES,
        json={"honor_id": honor["id"], "title": f"Curso {label}"},
        headers=world["instructor"]["headers"],
    )
    assert created.status_code == 201, created.text
    course_id = created.json()["id"]
    assert (
        await client.post(
            f"{COURSES}/{course_id}/lessons",
            json={"title": "Lección 1", "blocks": [TEXT_BLOCK]},
            headers=world["instructor"]["headers"],
        )
    ).status_code == 201
    saved = await client.put(
        f"{COURSES}/{course_id}/requirements/1/questions",
        json={"draw_count": draw, "question_bank": [_mc(n) for n in range(1, bank + 1)]},
        headers=world["instructor"]["headers"],
    )
    assert saved.status_code == 200, saved.text
    if update:
        changed = await client.put(
            f"{COURSES}/{course_id}", json=update, headers=world["instructor"]["headers"]
        )
        assert changed.status_code == 200, changed.text
    assert (
        await client.post(f"{COURSES}/{course_id}/submit", headers=world["instructor"]["headers"])
    ).status_code == 200
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


async def _start(client, user, enrollment_id, pledge=True):
    return await client.post(
        f"{EXAMS}/enrollments/{enrollment_id}/attempts",
        json={"pledge": pledge},
        headers=user["headers"],
    )


async def _answer(client, user, attempt_id, position, response):
    return await client.put(
        f"{EXAMS}/attempts/{attempt_id}/answers/{position}",
        json={"response": response},
        headers=user["headers"],
    )


async def _answer_all(client, user, paper, *, correct=True) -> None:
    for question in paper["questions"]:
        index = next(
            i for i, option in enumerate(question["options"])
            if option.startswith("Correcta") is correct
        )
        saved = await _answer(client, user, paper["id"], question["position"], str(index))
        assert saved.status_code == 200, saved.text


async def _submit(client, user, attempt_id):
    return await client.post(f"{EXAMS}/attempts/{attempt_id}/submit", headers=user["headers"])


async def _expire(attempt_id: str, seconds: int = 120) -> None:
    """Move the server's own deadline into the past. The clock is never the browser's, so
    this is the only way a test can travel in time."""
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE exam_attempts SET deadline_at = now() - make_interval(secs => :s)"
                 " WHERE id = :id"),
            {"id": uuid.UUID(attempt_id), "s": seconds},
        )
        await db.commit()


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id)
    )
    return [row["action"] for row in rows]


# ----------------------------------------------------------------------------
# Starting an attempt
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_paper_never_carries_the_answers(client, world, factory):
    course = await _course_with_exam(client, world, factory, "paper")
    enrollment = await _join(client, world["member"], course["id"])
    started = await _start(client, world["member"], enrollment["id"])
    assert started.status_code == 200, started.text
    paper = started.json()

    assert len(paper["questions"]) == 2  # draw_count, out of a bank of three
    assert "correct_answer" not in started.text and SECRET not in started.text
    for question in paper["questions"]:
        assert set(question) == {
            "position", "requirement_position", "question_text", "question_type",
            "options", "points", "response", "answered_at",
        }
        assert question["response"] is None
    assert paper["status"] == "IN_PROGRESS" and paper["passed"] is False
    assert paper["points_total"] == 2
    assert "EXAM_START" in await _audit_actions(paper["id"])


@pytest.mark.asyncio
async def test_the_draw_and_the_shuffle_live_on_the_server(client, world, factory):
    course = await _course_with_exam(client, world, factory, "draw", bank=6, draw=6)
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()

    stored = await fetch_all(
        "SELECT position, question_id, option_order FROM exam_answers WHERE attempt_id = :a"
        " ORDER BY position",
        a=uuid.UUID(paper["id"]),
    )
    assert len(stored) == 6
    assert all(row["option_order"] is not None for row in stored)
    # The options the member sees follow the permutation that was written down.
    async with SessionLocal() as db:
        for row, question in zip(stored, paper["questions"]):
            options = (
                await db.execute(
                    text("SELECT options FROM course_questions WHERE id = :id"),
                    {"id": row["question_id"]},
                )
            ).scalar_one()
            assert question["options"] == [options[i] for i in row["option_order"]]
    # Two attempts of two members do not have to share the order: the draw is random.
    other = await _join(client, world["member2"], course["id"])
    second = (await _start(client, world["member2"], other["id"])).json()
    assert len(second["questions"]) == 6


@pytest.mark.asyncio
async def test_the_draw_prefers_questions_the_member_has_not_seen(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "unseen", bank=4, draw=2, max_exam_attempts=3
    )
    enrollment = await _join(client, world["member"], course["id"])
    first = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], first, correct=False)
    assert (await _submit(client, world["member"], first["id"])).json()["status"] == "FAILED"

    second = (await _start(client, world["member"], enrollment["id"])).json()
    seen = {q["question_text"] for q in first["questions"]}
    fresh = {q["question_text"] for q in second["questions"]}
    assert not (seen & fresh)  # four in the bank, two drawn: the second pair is the other two


@pytest.mark.asyncio
async def test_starting_again_returns_the_open_attempt(client, world, factory):
    course = await _course_with_exam(client, world, factory, "idem")
    enrollment = await _join(client, world["member"], course["id"])
    first = (await _start(client, world["member"], enrollment["id"])).json()
    again = await _start(client, world["member"], enrollment["id"])
    assert again.status_code == 200 and again.json()["id"] == first["id"]
    rows = await fetch_all(
        "SELECT id FROM exam_attempts WHERE enrollment_id = :e", e=uuid.UUID(enrollment["id"])
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_the_pledge_is_required_and_the_owner_is_the_only_one_who_sits_it(
    client, world, factory
):
    course = await _course_with_exam(client, world, factory, "pledge")
    enrollment = await _join(client, world["member"], course["id"])
    assert (await _start(client, world["member"], enrollment["id"], pledge=False)).status_code == 422
    for who in ("member2", "instructor", "director", "master"):
        assert (await _start(client, world[who], enrollment["id"])).status_code == 403, who


@pytest.mark.asyncio
async def test_a_course_without_an_exam_has_no_attempts(client, world, factory):
    honor = await _honor(factory, "no-exam")
    created = await client.post(
        COURSES,
        json={"honor_id": honor["id"], "title": "Sin examen"},
        headers=world["instructor"]["headers"],
    )
    course_id = created.json()["id"]
    await client.post(
        f"{COURSES}/{course_id}/lessons",
        json={"title": "Lección 1", "blocks": [TEXT_BLOCK]},
        headers=world["instructor"]["headers"],
    )
    await client.post(f"{COURSES}/{course_id}/submit", headers=world["instructor"]["headers"])
    for reviewer in ("zone_coordinator", "assoc_admin"):
        await client.post(
            f"{COURSES}/{course_id}/review",
            json={"action": "APPROVE"},
            headers=world[reviewer]["headers"],
        )
    enrollment = await _join(client, world["member"], course_id)
    refused = await _start(client, world["member"], enrollment["id"])
    assert refused.status_code == 409 and "examen" in refused.json()["detail"].lower()


@pytest.mark.asyncio
async def test_the_attempt_limit_is_the_course_one(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "limit", bank=2, draw=2, max_exam_attempts=2
    )
    enrollment = await _join(client, world["member"], course["id"])
    for _ in range(2):
        attempt = (await _start(client, world["member"], enrollment["id"])).json()
        await _answer_all(client, world["member"], attempt, correct=False)
        assert (await _submit(client, world["member"], attempt["id"])).json()["status"] == "FAILED"
    refused = await _start(client, world["member"], enrollment["id"])
    assert refused.status_code == 409 and "2 intentos" in refused.json()["detail"]


# ----------------------------------------------------------------------------
# Answering, resuming and the deadline
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_answers_are_autosaved_and_the_attempt_resumes(client, world, factory):
    course = await _course_with_exam(client, world, factory, "resume", max_exam_attempts=1)
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    saved = await _answer(client, world["member"], paper["id"], 1, "0")
    assert saved.status_code == 200 and saved.json()["position"] == 1

    # Closing the tab changes nothing: the attempt lives on the server.
    resumed = await client.get(f"{EXAMS}/attempts/{paper['id']}", headers=world["member"]["headers"])
    assert resumed.status_code == 200
    assert resumed.json()["questions"][0]["response"] == "0"
    assert resumed.json()["questions"][1]["response"] is None
    assert resumed.json()["remaining_seconds"] is None or resumed.json()["remaining_seconds"] >= 0
    assert SECRET not in resumed.text


@pytest.mark.asyncio
async def test_the_client_cannot_invent_a_question_or_an_option(client, world, factory):
    course = await _course_with_exam(client, world, factory, "invent")
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    assert (await _answer(client, world["member"], paper["id"], 99, "0")).status_code == 404
    assert (await _answer(client, world["member"], paper["id"], 1, "9")).status_code == 422
    assert (await _answer(client, world["member"], paper["id"], 1, "no")).status_code == 422


@pytest.mark.asyncio
async def test_nobody_answers_after_the_deadline(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "deadline", exam_time_limit_minutes=5
    )
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    assert paper["remaining_seconds"] > 0 and paper["time_limit_minutes"] == 5
    await _answer(client, world["member"], paper["id"], 1, "0")
    await _expire(paper["id"])

    late = await _answer(client, world["member"], paper["id"], 2, "0")
    assert late.status_code == 409
    closed = await client.get(f"{EXAMS}/attempts/{paper['id']}", headers=world["member"]["headers"])
    assert closed.json()["status"] in ("PASSED", "FAILED")
    assert closed.json()["auto_submitted"] is True
    assert closed.json()["remaining_seconds"] is None


@pytest.mark.asyncio
async def test_without_a_time_limit_the_attempt_still_ends(client, world, factory):
    course = await _course_with_exam(client, world, factory, "nolimit")
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    assert paper["time_limit_minutes"] is None
    started = await fetch_one(
        "SELECT started_at, deadline_at FROM exam_attempts WHERE id = :id",
        id=uuid.UUID(paper["id"]),
    )
    assert started["deadline_at"] - started["started_at"] == timedelta(hours=72)


# ----------------------------------------------------------------------------
# Grading and what the member is shown
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_passing_completes_the_theoretical_requirements(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "pass", theoretical=(True, False)
    )
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper)
    result = await _submit(client, world["member"], paper["id"])
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PASSED" and result.json()["score_percent"] == 100

    detail = await client.get(
        f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member"]["headers"]
    )
    rows = {row["position"]: row for row in detail.json()["requirements"]}
    assert rows[1]["status"] == "COMPLETE" and rows[1]["completed_via"] == "EXAM"
    assert rows[1]["reviewed_by"] is None
    # ...and only the theoretical one: the practical requirement still needs a person.
    assert rows[2]["status"] == "PENDING"
    assert detail.json()["status"] == "IN_PROGRESS"
    stored = await fetch_one(
        "SELECT completed_positions FROM exam_attempts WHERE id = :id", id=uuid.UUID(paper["id"])
    )
    assert stored["completed_positions"] == [1]
    assert "REQUIREMENT_COMPLETE_EXAM" in await _audit_actions(enrollment["id"])


@pytest.mark.asyncio
async def test_passing_the_last_requirement_finishes_the_enrollment(client, world, factory):
    """Rule 2 of A fires: every requirement COMPLETE, so the enrollment leaves IN_PROGRESS.

    Until I7 it stopped at READY and waited for the instructor to press a button. With
    Bloque D · I7 a course with no practical requirement issues the certificate in the same
    request (spec §5.3), so what this test now sees is the step after READY. The rule that
    got it there did not change; tests/test_certification.py owns the issuance itself.
    """
    course = await _course_with_exam(client, world, factory, "ready")
    enrollment = await _join(client, world["member2"], course["id"])
    paper = (await _start(client, world["member2"], enrollment["id"])).json()
    await _answer_all(client, world["member2"], paper)
    assert (await _submit(client, world["member2"], paper["id"])).json()["status"] == "PASSED"
    detail = await client.get(
        f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member2"]["headers"]
    )
    assert detail.json()["status"] == "CERTIFIED"


@pytest.mark.asyncio
async def test_a_failed_attempt_with_attempts_left_shows_no_answers(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "fail", bank=4, draw=2, max_exam_attempts=3
    )
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper, correct=False)
    result = await _submit(client, world["member"], paper["id"])
    body = result.json()
    assert body["status"] == "FAILED" and body["score_percent"] == 0
    assert body["breakdown"][0]["requirement_position"] == 1
    assert [row["is_correct"] for row in body["feedback"]] == [False, False]
    # D5a: what was asked and what they answered, never the right answer.
    assert all("correct_answer" not in row for row in body["feedback"])
    assert SECRET not in result.text
    assert body["feedback"][0]["your_response"] is not None


@pytest.mark.asyncio
async def test_the_answers_appear_once_there_are_no_attempts_left(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "last", bank=2, draw=2, max_exam_attempts=1
    )
    enrollment = await _join(client, world["member3"], course["id"])
    paper = (await _start(client, world["member3"], enrollment["id"])).json()
    await _answer_all(client, world["member3"], paper, correct=False)
    result = await _submit(client, world["member3"], paper["id"])
    assert result.json()["status"] == "FAILED"
    assert SECRET in result.text
    assert result.json()["feedback"][0]["correct_answer"].startswith("Correcta")


@pytest.mark.asyncio
async def test_a_passed_attempt_shows_the_answers(client, world, factory):
    course = await _course_with_exam(client, world, factory, "passed-shows")
    enrollment = await _join(client, world["member4"], course["id"])
    paper = (await _start(client, world["member4"], enrollment["id"])).json()
    await _answer_all(client, world["member4"], paper)
    result = await _submit(client, world["member4"], paper["id"])
    assert result.json()["status"] == "PASSED" and SECRET in result.text


@pytest.mark.asyncio
async def test_submitting_twice_answers_the_same_thing(client, world, factory):
    course = await _course_with_exam(client, world, factory, "twice")
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper)
    first = await _submit(client, world["member"], paper["id"])
    second = await _submit(client, world["member"], paper["id"])
    assert first.json()["status"] == second.json()["status"] == "PASSED"
    assert first.json()["points_awarded"] == second.json()["points_awarded"]


@pytest.mark.asyncio
async def test_the_threshold_is_the_courses_one(client, world, factory):
    """Half right against a threshold of 80 % is a fail, and the arithmetic is integer."""
    course = await _course_with_exam(client, world, factory, "threshold", bank=2, draw=2)
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    question = paper["questions"][0]
    right = next(i for i, o in enumerate(question["options"]) if o.startswith("Correcta"))
    await _answer(client, world["member"], paper["id"], question["position"], str(right))
    result = await _submit(client, world["member"], paper["id"])
    assert result.json()["status"] == "FAILED"
    assert result.json()["score_percent"] == 50 and result.json()["passing_score"] == 80


# ----------------------------------------------------------------------------
# Who may read an attempt
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_nobody_reads_another_members_attempt(client, world, factory):
    course = await _course_with_exam(client, world, factory, "privacy")
    enrollment = await _join(client, world["minor"], course["id"])
    paper = (await _start(client, world["minor"], enrollment["id"])).json()
    await _answer_all(client, world["minor"], paper)
    assert (await _submit(client, world["minor"], paper["id"])).json()["status"] == "PASSED"
    url = f"{EXAMS}/attempts/{paper['id']}"

    assert (await client.get(url, headers=world["member2"]["headers"])).status_code == 403
    # The instructor of the course and the guardian read it whole...
    for who in ("instructor", "guardian", "master"):
        full = await client.get(url, headers=world[who]["headers"])
        assert full.status_code == 200, who
        assert "feedback" in full.json(), who
    # ...the club's director only the status and the score, never what the minor wrote.
    director = await client.get(url, headers=world["director"]["headers"])
    assert director.status_code == 200
    assert "feedback" not in director.json() and "questions" not in director.json()
    assert director.json()["status"] == "PASSED"


@pytest.mark.asyncio
async def test_an_attempt_in_progress_is_only_the_owners(client, world, factory):
    course = await _course_with_exam(client, world, factory, "in-progress")
    enrollment = await _join(client, world["minor"], course["id"])
    paper = (await _start(client, world["minor"], enrollment["id"])).json()
    watching = await client.get(
        f"{EXAMS}/attempts/{paper['id']}", headers=world["instructor"]["headers"]
    )
    assert watching.status_code == 200
    assert "questions" not in watching.json() and watching.json()["status"] == "IN_PROGRESS"


# ----------------------------------------------------------------------------
# An EXAM requirement is only completed by the exam
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_exam_requirement_is_not_judged_by_hand(client, world, factory):
    course = await _course_with_exam(client, world, factory, "handmade")
    enrollment = await _join(client, world["member"], course["id"])
    sent = await client.put(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
        json={"status": "SUBMITTED", "member_note": "Ya lo sé"},
        headers=world["member"]["headers"],
    )
    assert sent.status_code == 409 and "examen del curso" in sent.json()["detail"]

    verdict = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
        json={"verdict": "COMPLETE"},
        headers=world["instructor"]["headers"],
    )
    assert verdict.status_code == 409
    # A draft note is still the member's to keep.
    draft = await client.put(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
        json={"member_note": "Apuntes"},
        headers=world["member"]["headers"],
    )
    assert draft.status_code == 200


@pytest.mark.asyncio
async def test_joining_sends_a_submitted_exam_requirement_back_to_the_member(
    client, world, factory
):
    """§3.6: the answer is kept, but a requirement the course settles with an exam stops
    waiting for a person the moment the member joins."""
    course = await _course_with_exam(client, world, factory, "was-submitted")
    started = await client.post(
        ENROLLMENTS,
        json={"honor_id": course["honor"]["id"]},
        headers=world["member4"]["headers"],
    )
    assert started.status_code == 201, started.text
    enrollment_id = started.json()["id"]
    sent = await client.put(
        f"{ENROLLMENTS}/{enrollment_id}/requirements/1",
        json={"status": "SUBMITTED", "member_note": "Lo estudié"},
        headers=world["member4"]["headers"],
    )
    assert sent.status_code == 200, sent.text

    joined = await _join(client, world["member4"], course["id"])
    row = {r["position"]: r for r in joined["requirements"]}[1]
    assert row["status"] == "PENDING" and row["member_note"] == "Lo estudié"
    assert row["assessment"] == "EXAM"


@pytest.mark.asyncio
async def test_every_step_of_an_attempt_is_audited(client, world, factory):
    course = await _course_with_exam(client, world, factory, "audit")
    enrollment = await _join(client, world["member"], course["id"])
    paper = (await _start(client, world["member"], enrollment["id"])).json()
    await _answer_all(client, world["member"], paper)
    assert (await _submit(client, world["member"], paper["id"])).json()["status"] == "PASSED"
    assert await _audit_actions(paper["id"]) == ["EXAM_START", "EXAM_SUBMIT"]
    # ...and the autosave of each answer is NOT audited (§7).
    saved = await fetch_all(
        "SELECT id FROM audit_log WHERE entity_id = :id AND action = 'EXAM_ANSWER'",
        id=paper["id"],
    )
    assert saved == []


@pytest.mark.asyncio
async def test_the_instructor_reopens_a_requirement_completed_by_the_exam(client, world, factory):
    # A practical requirement keeps the automatic issuance of I7 away, which is what leaves
    # the enrollment open long enough to be reopened at all: §5.3 never issues when a row
    # of the plan is practical, and rule 3 of A freezes a certified enrollment.
    course = await _course_with_exam(client, world, factory, "reopen", theoretical=(True, False))
    enrollment = await _join(client, world["member2"], course["id"])
    paper = (await _start(client, world["member2"], enrollment["id"])).json()
    await _answer_all(client, world["member2"], paper)
    assert (await _submit(client, world["member2"], paper["id"])).json()["status"] == "PASSED"

    reopened = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
        json={"verdict": "INCOMPLETE", "note": "Quiero verlo en persona"},
        headers=world["instructor"]["headers"],
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "IN_PROGRESS"
    rows = {row["position"]: row for row in reopened.json()["requirements"]}
    assert rows[1]["status"] == "INCOMPLETE" and rows[1]["completed_via"] is None


@pytest.mark.asyncio
async def test_nothing_is_written_on_a_frozen_enrollment(client, world, factory, monkeypatch):
    from app.config import settings

    issuer = await factory.org("issuer", "association")
    code = f"{factory.prefix}-ISS"
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE organizations SET code = :c WHERE id = :id"),
            {"c": code, "id": uuid.UUID(issuer["id"])},
        )
        await db.commit()
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", code)

    course = await _course_with_exam(client, world, factory, "frozen")
    enrollment = await _join(client, world["member3"], course["id"])
    paper = (await _start(client, world["member3"], enrollment["id"])).json()
    await _answer_all(client, world["member3"], paper)
    assert (await _submit(client, world["member3"], paper["id"])).json()["status"] == "PASSED"
    # Since I7 the certificate is issued in that same request, so the enrollment is already
    # frozen here; a second issuance is refused for the same reason a second attempt is.
    detail = await client.get(
        f"{ENROLLMENTS}/{enrollment['id']}", headers=world["member3"]["headers"]
    )
    assert detail.json()["status"] == "CERTIFIED"
    again = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/certificate",
        json={"issued_date": "2026-09-21"},
        headers=world["instructor"]["headers"],
    )
    assert again.status_code == 409
    refused = await _start(client, world["member3"], enrollment["id"])
    assert refused.status_code == 409


# ----------------------------------------------------------------------------
# The exam card and the extra time
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_exam_card_tells_the_member_what_to_expect(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "card", bank=3, draw=2, max_exam_attempts=2,
        exam_passing_score=90,
    )
    enrollment = await _join(client, world["member"], course["id"])
    card = await client.get(
        f"{EXAMS}/enrollments/{enrollment['id']}", headers=world["member"]["headers"]
    )
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["passing_score"] == 90 and body["max_attempts"] == 2
    assert body["attempts_used"] == 0 and body["attempts_left"] == 2
    assert body["exam_positions"] == [1] and body["pending_positions"] == [1]
    assert body["can_start"] is True and body["blocked_reason"] is None
    assert body["open_attempt_id"] is None and body["last_result"] is None

    paper = (await _start(client, world["member"], enrollment["id"])).json()
    after = (
        await client.get(
            f"{EXAMS}/enrollments/{enrollment['id']}", headers=world["member"]["headers"]
        )
    ).json()
    assert after["open_attempt_id"] == paper["id"] and after["can_start"] is False


@pytest.mark.asyncio
async def test_extra_time_is_set_by_the_guardian_and_applies_to_the_next_attempt(
    client, world, factory
):
    course = await _course_with_exam(
        client, world, factory, "extra", exam_time_limit_minutes=10, max_exam_attempts=2
    )
    enrollment = await _join(client, world["minor"], course["id"])
    url = f"{EXAMS}/enrollments/{enrollment['id']}/extra-time"

    # A minor does not grant themselves an accommodation; their guardian does.
    assert (
        await client.put(url, json={"percent": 50}, headers=world["minor"]["headers"])
    ).status_code == 403
    assert (
        await client.put(url, json={"percent": 50}, headers=world["member2"]["headers"])
    ).status_code == 403
    granted = await client.put(url, json={"percent": 50}, headers=world["guardian"]["headers"])
    assert granted.status_code == 200, granted.text
    assert granted.json()["extra_time_percent"] == 50

    paper = (await _start(client, world["minor"], enrollment["id"])).json()
    assert paper["time_limit_minutes"] == 15  # 10 minutes + 50 %
    # ...and it does not stretch a clock that is already running.
    busy = await client.put(url, json={"percent": 100}, headers=world["guardian"]["headers"])
    assert busy.status_code == 409
    assert "EXAM_EXTRA_TIME" in await _audit_actions(enrollment["id"])


@pytest.mark.asyncio
async def test_an_adult_sets_their_own_extra_time(client, world, factory):
    course = await _course_with_exam(
        client, world, factory, "extra-adult", exam_time_limit_minutes=20
    )
    enrollment = await _join(client, world["member4"], course["id"])
    url = f"{EXAMS}/enrollments/{enrollment['id']}/extra-time"
    assert (
        await client.put(url, json={"percent": 25}, headers=world["member4"]["headers"])
    ).status_code == 200
    assert (
        await client.put(url, json={"percent": 25}, headers=world["instructor"]["headers"])
    ).status_code == 200
    assert (
        await client.put(url, json={"percent": 33}, headers=world["member4"]["headers"])
    ).status_code == 422


# ----------------------------------------------------------------------------
# Revisión de seguridad 2026-09
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sec04_withdrawing_and_joining_again_does_not_reset_the_attempts(
    client, world, factory
):
    """SEC-04: the attempts are the member's in the COURSE, not of one enrollment. Withdrawing
    and joining again used to hand out a fresh set — and the spent ones showed the answers."""
    member = await factory.user("sec04-member", "STUDENT", world["club"]["id"])
    course = await _course_with_exam(
        client, world, factory, "sec04", bank=4, draw=2, max_exam_attempts=2
    )
    first = await _join(client, member, course["id"])
    paper = (await _start(client, member, first["id"])).json()
    await _answer_all(client, member, paper, correct=False)
    assert (await _submit(client, member, paper["id"])).json()["status"] == "FAILED"

    withdrawn = await client.delete(
        f"/api/v1/portfolio/enrollments/{first['id']}", headers=member["headers"]
    )
    assert withdrawn.status_code == 204, withdrawn.text
    second = await _join(client, member, course["id"])
    assert second["id"] != first["id"]

    paper = (await _start(client, member, second["id"])).json()
    await _answer_all(client, member, paper, correct=False)
    last = await _submit(client, member, paper["id"])
    assert last.json()["status"] == "FAILED"
    refused = await _start(client, member, second["id"])
    assert refused.status_code == 409 and "2 intentos" in refused.json()["detail"]
    state = (
        await client.get(f"{EXAMS}/enrollments/{second['id']}", headers=member["headers"])
    ).json()
    assert state["attempts_used"] == 2 and state["attempts_left"] == 0


@pytest.mark.asyncio
async def test_sec05_the_guardian_does_not_see_the_answers_while_attempts_remain(
    client, world, factory
):
    course = await _course_with_exam(
        client, world, factory, "sec05", bank=4, draw=2, max_exam_attempts=3
    )
    enrollment = await _join(client, world["minor"], course["id"])
    paper = (await _start(client, world["minor"], enrollment["id"])).json()
    await _answer_all(client, world["minor"], paper, correct=False)
    assert (await _submit(client, world["minor"], paper["id"])).json()["status"] == "FAILED"

    read = await client.get(f"{EXAMS}/attempts/{paper['id']}", headers=world["guardian"]["headers"])
    assert read.status_code == 200, read.text
    assert SECRET not in read.text and "correct_answer" not in read.text
    # The instructor of the course still reads the whole attempt.
    staff = await client.get(
        f"{EXAMS}/attempts/{paper['id']}", headers=world["instructor"]["headers"]
    )
    assert staff.status_code == 200 and SECRET in staff.text
