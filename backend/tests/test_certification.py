"""Bloque D · I7 — Emisión automática, verificación pública y anulación de certificados.

La regla de la visión: sin parte física, aprobar el examen certifica solo. Eso pone una
firma de una persona en un documento sin que esa persona toque un botón, así que casi todo
lo que sigue comprueba que **no** ocurre cuando no debe: con un requisito práctico, con una
fila pendiente, con el instructor sin carta, dos veces, o después de anularlo.

Y lo que nunca puede romperse: `canonical()` no cambia, de modo que un certificado emitido
ayer sigue verificando hoy.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

COURSES = "/api/v1/courses"
EXAMS = "/api/v1/exams"
PORTFOLIO = "/api/v1/portfolio"
ENROLLMENTS = f"{PORTFOLIO}/enrollments"
VERIFY = "/api/v1/certificates/verify"

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
                            "SELECT to_regclass('public.exam_attempts') IS NOT NULL"
                            " AND EXISTS (SELECT 1 FROM information_schema.columns"
                            "   WHERE table_name = 'certificates'"
                            "     AND column_name = 'revoked_at')"
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
        reason="apply migrations/010_exams.sql and 011_certificate_revocation.sql",
    ),
]
factory = module_factory("certification")


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


@pytest_asyncio.fixture(scope="module", autouse=True)
async def issuer(factory):
    """The issuing organisation of A (`ISSUER_ORGANIZATION_CODE`), for the whole module."""
    from app.config import settings

    org = await factory.org("issuer", "association")
    code = f"{factory.prefix}-ISS"
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE organizations SET code = :c WHERE id = :id"),
            {"c": code, "id": uuid.UUID(org["id"])},
        )
        await db.commit()
    previous = settings.ISSUER_ORGANIZATION_CODE
    settings.ISSUER_ORGANIZATION_CODE = code
    yield org
    settings.ISSUER_ORGANIZATION_CODE = previous


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", association)
    club = await factory.org("club", "club", association)
    far_association = await factory.org("far-assoc", "association")
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", association["id"]),
        "member": await factory.user("member", "STUDENT", club["id"]),
        "member2": await factory.user("member2", "STUDENT", club["id"]),
        "member3": await factory.user("member3", "STUDENT", club["id"]),
        "member4": await factory.user("member4", "STUDENT", club["id"]),
        "member5": await factory.user("member5", "STUDENT", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "far_admin": await factory.user("far-admin", "ADMIN_ASSOCIATION", far_association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
    }
    await _authorize(people["instructor"]["id"], association["id"])
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO guardianships (guardian_id, child_id, consent_status)"
                " VALUES (:g, :c, 'APPROVED')"
            ),
            {"g": uuid.UUID(people["guardian"]["id"]), "c": uuid.UUID(people["minor"]["id"])},
        )
        await db.commit()
    return {
        **people,
        "association": association,
        "zone": zone,
        "club": club,
        "far_association": far_association,
    }


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
        "correct_answer": "Rúbrica",
        "points": points,
    }


async def _course(client, world, factory, label, *, bank=None, theoretical=(True,), **update):
    honor = await _honor(factory, label, theoretical)
    headers = world["instructor"]["headers"]
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
        json={"draw_count": len(bank or [_mc(1)]), "question_bank": bank or [_mc(1)]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    if update:
        assert (
            await client.put(f"{COURSES}/{course_id}", json=update, headers=headers)
        ).status_code == 200
    assert (await client.post(f"{COURSES}/{course_id}/submit", headers=headers)).status_code == 200
    for reviewer in ("zone_coordinator", "assoc_admin"):
        step = await client.post(
            f"{COURSES}/{course_id}/review",
            json={"action": "APPROVE"},
            headers=world[reviewer]["headers"],
        )
        assert step.status_code == 200, step.text
    return {"id": course_id, "honor": honor, "title": f"Curso {label}"}


async def _join(client, user, course_id) -> dict:
    joined = await client.post(f"{COURSES}/{course_id}/join", headers=user["headers"])
    assert joined.status_code == 200, joined.text
    return joined.json()


async def _sit_and_pass(client, user, enrollment_id, *, correct=True) -> dict:
    started = await client.post(
        f"{EXAMS}/enrollments/{enrollment_id}/attempts",
        json={"pledge": True},
        headers=user["headers"],
    )
    assert started.status_code == 200, started.text
    paper = started.json()
    for question in paper["questions"]:
        if question["question_type"] == "MULTIPLE_CHOICE":
            value = str(
                next(
                    i
                    for i, option in enumerate(question["options"])
                    if option.startswith("Correcta") is correct
                )
            )
        else:
            value = "Mi respuesta"
        assert (
            await client.put(
                f"{EXAMS}/attempts/{paper['id']}/answers/{question['position']}",
                json={"response": value},
                headers=user["headers"],
            )
        ).status_code == 200
    handed = await client.post(
        f"{EXAMS}/attempts/{paper['id']}/submit", headers=user["headers"]
    )
    assert handed.status_code == 200, handed.text
    return handed.json()


async def _detail(client, user, enrollment_id) -> dict:
    got = await client.get(f"{ENROLLMENTS}/{enrollment_id}", headers=user["headers"])
    assert got.status_code == 200, got.text
    return got.json()


async def _certificate_of(enrollment_id: str) -> dict | None:
    return await fetch_one(
        "SELECT * FROM certificates WHERE enrollment_id = :e", e=uuid.UUID(enrollment_id)
    )


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id)
    )
    return [row["action"] for row in rows]


# ----------------------------------------------------------------------------
# Automatic issuance
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_passing_a_course_without_practical_work_issues_the_certificate(
    client, world, factory
):
    course = await _course(client, world, factory, "auto")
    enrollment = await _join(client, world["member"], course["id"])
    result = await _sit_and_pass(client, world["member"], enrollment["id"])
    assert result["status"] == "PASSED"

    detail = await _detail(client, world["member"], enrollment["id"])
    assert detail["status"] == "CERTIFIED"
    certificate = await _certificate_of(enrollment["id"])
    assert certificate is not None
    assert certificate["issued_role"] == "INSTRUCTOR"
    assert str(certificate["issued_by_id"]) == world["instructor"]["id"]
    assert certificate["instructor_name"] == factory.name("instructor")
    assert certificate["director_name"] is None and certificate["place"] is None
    assert certificate["status"] == "issued"

    events = await fetch_all(
        "SELECT event_type, metadata_json FROM certificate_events WHERE certificate_id = :c",
        c=certificate["id"],
    )
    assert [event["event_type"] for event in events] == ["issued"]
    assert events[0]["metadata_json"]["auto"] is True
    assert events[0]["metadata_json"]["attempt_id"] == result["id"]
    assert "CERTIFICATE_AUTO_ISSUE" in await _audit_actions(certificate["id"])


@pytest.mark.asyncio
async def test_a_practical_requirement_stops_the_automatic_issuance(client, world, factory):
    """§5.3: «sin parte física» means not one row with `is_practical`, according to the
    course's reviewed plan — never the default of the imported catalogue."""
    course = await _course(client, world, factory, "practical", theoretical=(True, False))
    enrollment = await _join(client, world["member2"], course["id"])
    assert (await _sit_and_pass(client, world["member2"], enrollment["id"]))["status"] == "PASSED"
    detail = await _detail(client, world["member2"], enrollment["id"])
    # The exam settled requirement 1; requirement 2 still waits for a person and evidence.
    assert detail["status"] == "IN_PROGRESS"
    assert await _certificate_of(enrollment["id"]) is None


@pytest.mark.asyncio
async def test_a_pending_human_verdict_stops_the_automatic_issuance(client, world, factory):
    course = await _course(client, world, factory, "pending-review", theoretical=(True, True))
    enrollment = await _join(client, world["member3"], course["id"])
    assert (await _sit_and_pass(client, world["member3"], enrollment["id"]))["status"] == "PASSED"
    assert (await _detail(client, world["member3"], enrollment["id"]))["status"] == "IN_PROGRESS"
    assert await _certificate_of(enrollment["id"]) is None

    # ...and the last human verdict does NOT issue either: that path is manual (§5.3).
    sent = await client.put(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/2",
        json={"status": "SUBMITTED", "member_note": "Hecho"},
        headers=world["member3"]["headers"],
    )
    assert sent.status_code == 200
    judged = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/2/review",
        json={"verdict": "COMPLETE"},
        headers=world["instructor"]["headers"],
    )
    assert judged.status_code == 200 and judged.json()["status"] == "READY"
    assert await _certificate_of(enrollment["id"]) is None


@pytest.mark.asyncio
async def test_a_failed_attempt_certifies_nothing(client, world, factory):
    course = await _course(client, world, factory, "failed")
    enrollment = await _join(client, world["member4"], course["id"])
    result = await _sit_and_pass(client, world["member4"], enrollment["id"], correct=False)
    assert result["status"] == "FAILED"
    assert await _certificate_of(enrollment["id"]) is None


@pytest.mark.asyncio
async def test_the_last_manual_grade_issues_the_certificate_too(client, world, factory):
    """The other door into PASSED: an essay the instructor grades (I6)."""
    course = await _course(client, world, factory, "auto-graded", bank=[_essay(1)])
    enrollment = await _join(client, world["member5"], course["id"])
    result = await _sit_and_pass(client, world["member5"], enrollment["id"])
    assert result["status"] == "PENDING_GRADING"
    position = (
        await fetch_one(
            "SELECT position FROM exam_answers WHERE attempt_id = :a AND points_awarded IS NULL",
            a=uuid.UUID(result["id"]),
        )
    )["position"]
    graded = await client.post(
        f"{EXAMS}/attempts/{result['id']}/answers/{position}/grade",
        json={"points_awarded": 4},
        headers=world["instructor"]["headers"],
    )
    assert graded.status_code == 200 and graded.json()["status"] == "PASSED"
    assert (await _detail(client, world["member5"], enrollment["id"]))["status"] == "CERTIFIED"


@pytest.mark.asyncio
async def test_an_instructor_without_a_letter_certifies_nothing_because_nobody_sits_the_exam(
    client, world, factory
):
    """The first guarantee of §5.3, «the instructor is verified at that moment», seen from
    the door: while the letter is down the exam itself is closed, so no attempt can pass
    and no certificate can be signed in that instructor's name."""
    course = await _course(client, world, factory, "unverified")
    enrollment = await _join(client, world["minor"], course["id"])
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET status = 'REVOKED' WHERE user_id = :u"),
            {"u": uuid.UUID(world["instructor"]["id"])},
        )
        await db.commit()
    try:
        # The course itself stops serving an exam while the letter is down (§4.2)...
        refused = await client.post(
            f"{EXAMS}/enrollments/{enrollment['id']}/attempts",
            json={"pledge": True},
            headers=world["minor"]["headers"],
        )
        assert refused.status_code == 409
    finally:
        async with SessionLocal() as db:
            await db.execute(
                text("UPDATE church_letters SET status = 'AUTHORIZED' WHERE user_id = :u"),
                {"u": uuid.UUID(world["instructor"]["id"])},
            )
            await db.commit()

    result = await _sit_and_pass(client, world["minor"], enrollment["id"])
    assert result["status"] == "PASSED"
    assert (await _detail(client, world["minor"], enrollment["id"]))["status"] == "CERTIFIED"


@pytest.mark.asyncio
async def test_a_failure_to_issue_leaves_the_exam_result_intact(
    client, world, factory, monkeypatch
):
    """The SAVEPOINT of §5.3, seen from the only angle that matters: the certificate blew
    up and the child keeps their pass."""
    from app.services import auto_certificate

    course = await _course(client, world, factory, "savepoint")
    enrollment = await _join(client, world["member"], course["id"])

    def explode(*args, **kwargs):
        raise RuntimeError("la plantilla no existe")

    monkeypatch.setattr(auto_certificate, "_build", explode)
    result = await _sit_and_pass(client, world["member"], enrollment["id"])
    assert result["status"] == "PASSED"
    detail = await _detail(client, world["member"], enrollment["id"])
    assert detail["status"] == "READY"  # waiting in the instructor's «listos para certificar»
    assert {row["position"]: row for row in detail["requirements"]}[1]["completed_via"] == "EXAM"
    assert await _certificate_of(enrollment["id"]) is None


# ----------------------------------------------------------------------------
# Public verification
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_public_verification_shows_the_course_and_the_instructor(client, world, factory):
    course = await _course(client, world, factory, "verify")
    enrollment = await _join(client, world["member2"], course["id"])
    await _sit_and_pass(client, world["member2"], enrollment["id"])
    certificate = await _certificate_of(enrollment["id"])

    seen = await client.get(f"{VERIFY}/{certificate['certificate_no']}")
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["valid"] is True and body["status"] == "válido"
    assert body["mode"] == "COURSE"
    assert body["course_title"] == course["title"]
    assert body["instructor_name"] == certificate["instructor_name"]
    # Nothing new about the person ever appears here.
    assert "user_id" not in body and "email" not in seen.text
    assert body.get("revoked_at") is None


@pytest.mark.asyncio
async def test_the_hash_of_the_existing_certificates_never_moved(client, world, factory):
    """Hallazgo 7: `canonical()` is frozen. A certificate stored before I7 — here, one
    issued by the prototype batch — still verifies."""
    ministry = await fetch_one("SELECT slug FROM ministries WHERE slug = 'pathfinders'")
    assert ministry is not None
    made = await client.post(
        "/api/v1/certificates/prototype-batch",
        json={
            "ministry": "pathfinders",
            "application": "conquistadores",
            "club_name": factory.name("batch"),
            "honor_name": factory.name("Nudos batch"),
            "recipient_names": [factory.name("Quien Sea")],
            "issued_date": "2026-01-15",
            "width_in": 11,
            "height_in": 8.5,
        },
    )
    assert made.status_code == 201, made.text
    number = made.json()[0]["certificate_no"]
    seen = await client.get(f"{VERIFY}/{number}")
    assert seen.status_code == 200
    assert seen.json()["valid"] is True and seen.json()["status"] == "válido"
    # A CLUB-mode certificate with no enrollment carries no course.
    assert seen.json()["course_title"] is None


# ----------------------------------------------------------------------------
# Revocation
# ----------------------------------------------------------------------------
async def _revoke(client, who, certificate_id, reason="Se comprobó que no hizo el examen"):
    return await client.post(
        f"{PORTFOLIO}/certificates/{certificate_id}/revoke",
        json={"reason": reason},
        headers=who["headers"],
    )


@pytest.mark.asyncio
async def test_the_association_revokes_and_the_certificate_stops_verifying(
    client, world, factory
):
    course = await _course(client, world, factory, "revoke")
    enrollment = await _join(client, world["member3"], course["id"])
    await _sit_and_pass(client, world["member3"], enrollment["id"])
    certificate = await _certificate_of(enrollment["id"])

    done = await _revoke(client, world["assoc_admin"], certificate["id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "revoked"
    assert done.json()["revoked_at"] is not None

    stored = await _certificate_of(enrollment["id"])
    assert stored["status"] == "revoked" and stored["revoked_at"] is not None
    assert str(stored["revoked_by_id"]) == world["assoc_admin"]["id"]
    assert stored["revocation_reason"] == "Se comprobó que no hizo el examen"
    # Revoking is never a delete: folio, hash and history stay.
    assert stored["certificate_hash"] == certificate["certificate_hash"]
    assert stored["certificate_no"] == certificate["certificate_no"]

    seen = await client.get(f"{VERIFY}/{certificate['certificate_no']}")
    assert seen.json()["valid"] is False and seen.json()["status"] == "revocado"
    assert seen.json()["revoked_at"] is not None
    # The reason is for the audit trail and the holder, not for whoever scans the QR.
    assert "Se comprobó" not in seen.text

    events = await fetch_all(
        "SELECT event_type FROM certificate_events WHERE certificate_id = :c ORDER BY created_at",
        c=certificate["id"],
    )
    assert [event["event_type"] for event in events] == ["issued", "revoked"]
    assert "CERTIFICATE_REVOKE" in await _audit_actions(certificate["id"])


@pytest.mark.asyncio
async def test_revoking_withdraws_the_enrollment_and_lets_the_member_start_again(
    client, world, factory
):
    course = await _course(client, world, factory, "revoke-withdraw")
    enrollment = await _join(client, world["member4"], course["id"])
    await _sit_and_pass(client, world["member4"], enrollment["id"])
    certificate = await _certificate_of(enrollment["id"])
    assert (await _revoke(client, world["master"], certificate["id"])).status_code == 200

    row = await fetch_one(
        "SELECT status, certificate_id, withdrawn_at FROM honor_enrollments WHERE id = :id",
        id=uuid.UUID(enrollment["id"]),
    )
    assert row["status"] == "WITHDRAWN" and row["withdrawn_at"] is not None
    assert row["certificate_id"] == certificate["id"]  # kept as history

    # ...and the honor can be started again from zero.
    again = await client.post(
        ENROLLMENTS, json={"honor_id": course["honor"]["id"]}, headers=world["member4"]["headers"]
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] != enrollment["id"]


@pytest.mark.asyncio
async def test_a_revoked_certificate_is_never_re_issued_silently(client, world, factory):
    course = await _course(client, world, factory, "no-reissue")
    enrollment = await _join(client, world["member5"], course["id"])
    await _sit_and_pass(client, world["member5"], enrollment["id"])
    certificate = await _certificate_of(enrollment["id"])
    assert (await _revoke(client, world["assoc_admin"], certificate["id"])).status_code == 200

    refused = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/certificate",
        json={"issued_date": "2026-09-21"},
        headers=world["instructor"]["headers"],
    )
    assert refused.status_code == 409
    assert (
        len(
            await fetch_all(
                "SELECT id FROM certificates WHERE enrollment_id = :e",
                e=uuid.UUID(enrollment["id"]),
            )
        )
        == 1
    )
    # ...and a second revocation is a 409, not a second event.
    assert (await _revoke(client, world["assoc_admin"], certificate["id"])).status_code == 409


@pytest.mark.asyncio
async def test_who_may_revoke_and_who_may_not(client, world, factory):
    course = await _course(client, world, factory, "revoke-who")
    enrollment = await _join(client, world["minor"], course["id"])
    await _sit_and_pass(client, world["minor"], enrollment["id"])
    certificate = await _certificate_of(enrollment["id"])

    # The holder, their guardian, the instructor who signed it, the club's director and an
    # association outside the course's scope: none of them.
    for who in ("minor", "guardian", "instructor", "director", "far_admin", "zone_coordinator"):
        refused = await _revoke(client, world[who], certificate["id"])
        assert refused.status_code == 403, f"{who}: {refused.text}"
    assert (await _revoke(client, world["assoc_admin"], certificate["id"], "")).status_code == 422
    assert (await _revoke(client, world["assoc_admin"], certificate["id"])).status_code == 200


@pytest.mark.asyncio
async def test_the_holder_and_the_guardian_of_a_minor_are_told(client, world, factory):
    course = await _course(client, world, factory, "revoke-mail")
    enrollment = await _join(client, world["minor"], course["id"])
    await _sit_and_pass(client, world["minor"], enrollment["id"])
    certificate = await _certificate_of(enrollment["id"])
    assert (await _revoke(client, world["assoc_admin"], certificate["id"])).status_code == 200

    logged = await fetch_all(
        "SELECT email, kind FROM notification_log WHERE entity_id = :c", c=str(certificate["id"])
    )
    addresses = {row["email"] for row in logged}
    assert addresses == {world["minor"]["email"], world["guardian"]["email"]}
    assert {row["kind"] for row in logged} == {"CERTIFICATE_REVOKED"}
