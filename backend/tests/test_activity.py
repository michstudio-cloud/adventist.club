"""Bloque F · F2 — Requisitos enlazados y horas.

Three ways a requirement of a program completes ITSELF, and one that the member chooses:

  * `HONOR` with a concrete target -> the member holds a CERTIFIED enrollment of any version
    of that honor;
  * `HONOR` open (a category, or free choice) -> the member picks which of their certified
    honors fills the slot;
  * `PROGRAM` -> a CERTIFIED enrollment of the target program;
  * `HOURS` -> the service or attendance the director approved adds up to the target.

And the two things that must never happen: a verdict a reviewer gave is never undone by the
automation, and a requirement of hours is never signed by hand.
"""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import private_storage
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

PORTFOLIO = "/api/v1/portfolio"
ENROLLMENTS = f"{PORTFOLIO}/enrollments"
EVIDENCES = f"{PORTFOLIO}/evidences"
ACTIVITY = "/api/v1/activity"
LOGS = f"{ACTIVITY}/logs"


def _tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.activity_logs') IS NOT NULL"
                    " AND to_regclass('public.programs') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _tables_exist(),
        reason="apply migrations/012_programs.sql and 013_activity_logs.sql to the test database",
    ),
]
factory = module_factory("activity")


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("club-b", "club", association)
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    people = {
        "member": await factory.user("member", "STUDENT", club["id"], is_minor=True),
        "mate": await factory.user("mate", "STUDENT", club["id"]),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", other_club["id"]),
        "admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": issuer_code, "id": uuid.UUID(issuer["id"])})
        await db.execute(text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
                         {"id": uuid.UUID(people["instructor"]["id"])})
        await db.commit()
    return {**people, "association": association, "club": club, "other_club": other_club,
            "issuer_code": issuer_code}


@pytest_asyncio.fixture
async def fresh(factory, world):
    """A member of the club with NO history. Hours are counted per person and per day, so a
    test about sums must not see what the tests before it had approved."""
    return await factory.user(f"fresh-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"])


@pytest_asyncio.fixture
async def fresh_minor(factory, world):
    return await factory.user(f"minor-{uuid.uuid4().hex[:6]}", "STUDENT", world["club"]["id"],
                              is_minor=True)


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


class FakePrivateR2:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.presigned: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presigned.append({"operation": operation, "expires": ExpiresIn, **Params})
        return f"https://r2.test/{Params['Bucket']}/{Params['Key']}"

    def head_object(self, Bucket, Key):
        return self.objects[Key]

    def store_last_upload(self, size: int, content_type: str) -> str:
        key = self.presigned[-1]["Key"]
        self.objects[key] = {"ContentLength": size, "ContentType": content_type}
        return key


@pytest.fixture
def r2(monkeypatch):
    fake = FakePrivateR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", "evidence-test")
    monkeypatch.setattr(private_storage, "get_client", lambda: fake)
    return fake


# ----------------------------------------------------------------------------
# Catalogue helpers
# ----------------------------------------------------------------------------
async def _honor(factory, label: str, *, category_id=None, previous_id=None) -> dict:
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(
            text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, category_id, name, slug, active, status,"
            " version, previous_version_id) VALUES (:id, :m, :c, :name, :slug, true, 'PUBLISHED',"
            " :v, :prev)"),
            {"id": honor_id, "m": ministry, "c": category_id, "name": name,
             "slug": f"{factory.prefix}-{label}", "v": 2 if previous_id else 1,
             "prev": uuid.UUID(previous_id) if previous_id else None})
        await db.execute(text(
            "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical,"
            " locale) VALUES (:h, 1, 'Requisito único', true, 'es')"), {"h": honor_id})
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _category(factory, label: str) -> dict:
    category_id = uuid.uuid4()
    async with SessionLocal() as db:
        ministry = (await db.execute(
            text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honor_categories (id, ministry_id, name, slug)"
            " VALUES (:id, :m, :name, :slug)"),
            {"id": category_id, "m": ministry, "name": factory.name(label),
             "slug": f"{factory.prefix}-{label}"})
        await db.commit()
    return {"id": str(category_id)}


async def _program(factory, label: str, requirements: list[dict], *, status="PUBLISHED") -> dict:
    """A one-section program whose requirements are given as dicts of the catalogue's shape."""
    program_id = uuid.uuid4()
    section_id = uuid.uuid4()
    name = factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(
            text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO programs (id, ministry_id, kind, slug, name, status)"
            " VALUES (:id, :m, 'CLASS', :slug, :name, :status)"),
            {"id": program_id, "m": ministry, "slug": f"{factory.prefix}-{label}", "name": name,
             "status": status})
        await db.execute(text(
            "INSERT INTO program_sections (id, program_id, position, slug, name)"
            " VALUES (:id, :p, 1, 'general', 'General')"),
            {"id": section_id, "p": program_id})
        for position, requirement in enumerate(requirements, start=1):
            requirement_id = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO program_requirements (id, program_id, section_id, position, label,"
                " kind, evidence_required, target_honor_id, target_category_id, target_program_id,"
                " target_quantity, activity_category)"
                " VALUES (:id, :p, :s, :pos, :label, :kind, :ev, :honor, :cat, :prog, :qty, :act)"),
                {"id": requirement_id, "p": program_id, "s": section_id, "pos": position,
                 "label": str(position), "kind": requirement.get("kind", "FREE"),
                 "ev": requirement.get("evidence_required", False),
                 "honor": uuid.UUID(requirement["honor"]) if requirement.get("honor") else None,
                 "cat": uuid.UUID(requirement["category"]) if requirement.get("category") else None,
                 "prog": uuid.UUID(requirement["program"]) if requirement.get("program") else None,
                 "qty": requirement.get("quantity"), "act": requirement.get("activity")})
            await db.execute(text(
                "INSERT INTO program_requirement_texts (requirement_id, locale, description)"
                " VALUES (:r, 'es', :d)"), {"r": requirement_id, "d": f"Requisito {position}"})
        await db.commit()
    return {"id": str(program_id), "name": name}


async def _certified_honor(client, user, honor, director, issuer_fixture=None) -> dict:
    """The member earns an honor for real, through block A."""
    created = await client.post(ENROLLMENTS, json={"honor_id": honor["id"]},
                                headers=user["headers"])
    assert created.status_code == 201, created.text
    enrollment = created.json()
    submitted = await client.put(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
        json={"status": "SUBMITTED", "member_note": "Hecho"}, headers=user["headers"])
    assert submitted.status_code == 200, submitted.text
    reviewed = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
        json={"verdict": "COMPLETE"}, headers=director["headers"])
    assert reviewed.status_code == 200, reviewed.text
    issued = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/certificate",
                               json={"issued_date": "2026-10-04"}, headers=director["headers"])
    assert issued.status_code == 201, issued.text
    return {"enrollment_id": enrollment["id"], "certificate": issued.json()}


async def _enroll_program(client, user, program) -> dict:
    response = await client.post(ENROLLMENTS, json={"program_id": program["id"]},
                                 headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def _requirement(detail: dict, position: int) -> dict:
    return next(r for r in detail["requirements"] if r["position"] == position)


async def _detail(client, user, enrollment_id) -> dict:
    response = await client.get(f"{ENROLLMENTS}/{enrollment_id}", headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


# ----------------------------------------------------------------------------
# HONOR with a concrete target
# ----------------------------------------------------------------------------
async def test_an_honor_already_earned_completes_the_requirement_when_enrolling(
    client, factory, world, issuer
):
    honor = await _honor(factory, "nudos")
    await _certified_honor(client, world["member"], honor, world["director"])
    program = await _program(factory, "clase-nudos", [{"kind": "HONOR", "honor": honor["id"]}])

    enrollment = await _enroll_program(client, world["member"], program)
    requirement = _requirement(enrollment, 1)
    assert requirement["status"] == "COMPLETE"
    assert requirement["completed_via"] == "HONOR"
    assert requirement["reviewed_by"] is None
    assert requirement["satisfied_by"]["type"] == "honor"
    assert requirement["satisfied_by"]["name"] == honor["name"]
    assert requirement["satisfied_by"]["certificate_no"]
    assert enrollment["status"] == "READY"  # the only requirement is done
    audit = await fetch_one(
        "SELECT action, metadata_json FROM audit_log"
        " WHERE action = 'REQUIREMENT_AUTOCOMPLETE' AND metadata_json->>'enrollment_id' = :id",
        id=enrollment["id"])
    assert audit is not None and audit["metadata_json"]["via"] == "HONOR"


async def test_earning_the_honor_later_completes_the_requirement_at_that_moment(
    client, factory, world, issuer
):
    honor = await _honor(factory, "fuego")
    program = await _program(factory, "clase-fuego", [{"kind": "HONOR", "honor": honor["id"]},
                                                      {"kind": "FREE"}])
    enrollment = await _enroll_program(client, world["member"], program)
    assert _requirement(enrollment, 1)["status"] == "PENDING"

    await _certified_honor(client, world["member"], honor, world["director"])

    after = await _detail(client, world["member"], enrollment["id"])
    assert _requirement(after, 1)["status"] == "COMPLETE"
    assert _requirement(after, 1)["completed_via"] == "HONOR"
    assert _requirement(after, 2)["status"] == "PENDING"
    assert after["status"] == "IN_PROGRESS"


async def test_any_version_of_the_honor_counts(client, factory, world, issuer):
    v1 = await _honor(factory, "primeros-auxilios")
    v2 = await _honor(factory, "primeros-auxilios-v2", previous_id=v1["id"])
    # The requirement points at v1; the member earned v2.
    program = await _program(factory, "clase-linaje", [{"kind": "HONOR", "honor": v1["id"]}])
    await _certified_honor(client, world["member"], v2, world["director"])

    enrollment = await _enroll_program(client, world["member"], program)
    assert _requirement(enrollment, 1)["status"] == "COMPLETE"


async def test_an_honor_of_somebody_else_never_counts(client, factory, world, issuer):
    honor = await _honor(factory, "ajena")
    await _certified_honor(client, world["mate"], honor, world["director"])
    program = await _program(factory, "clase-ajena", [{"kind": "HONOR", "honor": honor["id"]}])

    enrollment = await _enroll_program(client, world["member"], program)
    assert _requirement(enrollment, 1)["status"] == "PENDING"


async def test_an_uncertified_enrollment_does_not_count(client, factory, world):
    honor = await _honor(factory, "a-medias")
    created = await client.post(ENROLLMENTS, json={"honor_id": honor["id"]},
                                headers=world["member"]["headers"])
    assert created.status_code == 201
    program = await _program(factory, "clase-a-medias", [{"kind": "HONOR", "honor": honor["id"]}])
    enrollment = await _enroll_program(client, world["member"], program)
    assert _requirement(enrollment, 1)["status"] == "PENDING"


# ----------------------------------------------------------------------------
# The manual route: an achievement from outside the platform
# ----------------------------------------------------------------------------
async def test_a_linked_requirement_is_never_signed_by_hand_without_evidence(
    client, factory, world, r2
):
    honor = await _honor(factory, "en-papel")
    program = await _program(factory, "clase-papel", [{"kind": "HONOR", "honor": honor["id"]}])
    enrollment = await _enroll_program(client, world["member"], program)
    # Born practical (§1.3): the manual route demands the photo of the old certificate.
    assert _requirement(enrollment, 1)["is_practical"] is True

    sent = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                            json={"status": "SUBMITTED", "member_note": "La gané en papel"},
                            headers=world["member"]["headers"])
    assert sent.status_code == 422 and "evidencia" in sent.json()["detail"]

    created = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/evidences",
        json={"content_type": "image/jpeg", "size_bytes": 2048},
        headers=world["member"]["headers"])
    assert created.status_code == 201
    r2.store_last_upload(2048, "image/jpeg")
    assert (await client.post(f"{EVIDENCES}/{created.json()['evidence']['id']}/complete",
                              headers=world["member"]["headers"])).status_code == 200
    assert (await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                             json={"status": "SUBMITTED", "member_note": "La gané en papel"},
                             headers=world["member"]["headers"])).status_code == 200
    reviewed = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
                                 json={"verdict": "COMPLETE"},
                                 headers=world["director"]["headers"])
    assert reviewed.status_code == 200
    assert _requirement(reviewed.json(), 1)["completed_via"] == "REVIEW"


async def test_a_verdict_of_a_reviewer_is_never_undone_by_the_automation(
    client, factory, world, r2, issuer
):
    """A requirement a reviewer judged keeps its verdict even if the automation could act."""
    honor = await _honor(factory, "dictaminada")
    program = await _program(factory, "clase-dictaminada",
                             [{"kind": "HONOR", "honor": honor["id"]}])
    enrollment = await _enroll_program(client, world["member"], program)
    created = await client.post(
        f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/evidences",
        json={"content_type": "image/jpeg", "size_bytes": 1024},
        headers=world["member"]["headers"])
    r2.store_last_upload(1024, "image/jpeg")
    await client.post(f"{EVIDENCES}/{created.json()['evidence']['id']}/complete",
                      headers=world["member"]["headers"])
    await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                     json={"status": "SUBMITTED", "member_note": "En papel"},
                     headers=world["member"]["headers"])
    await client.post(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
                      json={"verdict": "COMPLETE"}, headers=world["director"]["headers"])

    # Now they also earn it on the platform: the row keeps REVIEW and its reviewer.
    await _certified_honor(client, world["member"], honor, world["director"])
    after = _requirement(await _detail(client, world["member"], enrollment["id"]), 1)
    assert after["completed_via"] == "REVIEW" and after["reviewed_by"] is not None


async def test_an_incomplete_verdict_breaks_the_automatic_link(client, factory, world, issuer):
    honor = await _honor(factory, "reabierta")
    program = await _program(factory, "clase-reabierta", [{"kind": "HONOR", "honor": honor["id"]}])
    await _certified_honor(client, world["member"], honor, world["director"])
    enrollment = await _enroll_program(client, world["member"], program)
    assert _requirement(enrollment, 1)["status"] == "COMPLETE"

    reopened = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
                                 json={"verdict": "INCOMPLETE", "note": "Falta la banda"},
                                 headers=world["director"]["headers"])
    assert reopened.status_code == 200
    requirement = _requirement(reopened.json(), 1)
    assert requirement["status"] == "INCOMPLETE" and requirement["satisfied_by"] is None
    row = await fetch_one(
        "SELECT satisfied_by_enrollment_id FROM requirement_progress"
        " WHERE enrollment_id = :id AND requirement_position = 1", id=enrollment["id"])
    assert row["satisfied_by_enrollment_id"] is None
    # …and it does NOT silently complete itself again on the next read.
    after = await _detail(client, world["member"], enrollment["id"])
    assert _requirement(after, 1)["status"] == "INCOMPLETE"


# ----------------------------------------------------------------------------
# HONOR open: the member chooses
# ----------------------------------------------------------------------------
async def test_the_member_chooses_which_honor_fills_an_open_slot(client, factory, world, issuer):
    category = await _category(factory, "naturaleza")
    nature = await _honor(factory, "aves", category_id=uuid.UUID(category["id"]))
    other = await _honor(factory, "nudos-2")
    earned_nature = await _certified_honor(client, world["member"], nature, world["director"])
    earned_other = await _certified_honor(client, world["member"], other, world["director"])
    program = await _program(factory, "clase-abierta",
                             [{"kind": "HONOR", "category": category["id"]}])
    enrollment = await _enroll_program(client, world["member"], program)
    # Nothing happens on its own: an open slot is a decision of the member.
    assert _requirement(enrollment, 1)["status"] == "PENDING"
    assert _requirement(enrollment, 1)["target"]["category"]["id"] == category["id"]

    wrong = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                             json={"honor_enrollment_id": earned_other["enrollment_id"]},
                             headers=world["member"]["headers"])
    assert wrong.status_code == 422 and "categoría" in wrong.json()["detail"]

    chosen = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                              json={"honor_enrollment_id": earned_nature["enrollment_id"]},
                              headers=world["member"]["headers"])
    assert chosen.status_code == 200
    requirement = _requirement(chosen.json(), 1)
    assert requirement["status"] == "COMPLETE" and requirement["completed_via"] == "HONOR"
    assert requirement["satisfied_by"]["name"] == nature["name"]
    audit = await fetch_one(
        "SELECT action FROM audit_log WHERE action = 'REQUIREMENT_LINK'"
        " AND metadata_json->>'enrollment_id' = :id", id=enrollment["id"])
    assert audit is not None


async def test_one_achievement_fills_at_most_one_slot_of_the_same_enrollment(
    client, factory, world, issuer
):
    honor = await _honor(factory, "unica")
    earned = await _certified_honor(client, world["member"], honor, world["director"])
    program = await _program(factory, "clase-dos-huecos",
                             [{"kind": "HONOR"}, {"kind": "HONOR"}])
    enrollment = await _enroll_program(client, world["member"], program)
    first = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                             json={"honor_enrollment_id": earned["enrollment_id"]},
                             headers=world["member"]["headers"])
    assert first.status_code == 200
    second = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/2",
                              json={"honor_enrollment_id": earned["enrollment_id"]},
                              headers=world["member"]["headers"])
    assert second.status_code == 409 and "ya completa" in second.json()["detail"]


async def test_an_open_slot_only_takes_a_certified_honor_of_the_owner(
    client, factory, world, issuer
):
    honor = await _honor(factory, "de-otro")
    earned = await _certified_honor(client, world["mate"], honor, world["director"])
    program = await _program(factory, "clase-de-otro", [{"kind": "HONOR"}])
    enrollment = await _enroll_program(client, world["member"], program)
    refused = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                               json={"honor_enrollment_id": earned["enrollment_id"]},
                               headers=world["member"]["headers"])
    assert refused.status_code == 404

    mine = await client.post(ENROLLMENTS, json={"honor_id": honor["id"]},
                             headers=world["member"]["headers"])
    not_certified = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                                     json={"honor_enrollment_id": mine.json()["id"]},
                                     headers=world["member"]["headers"])
    assert not_certified.status_code == 409


# ----------------------------------------------------------------------------
# PROGRAM
# ----------------------------------------------------------------------------
async def test_a_program_requirement_is_satisfied_by_the_investiture_of_that_program(
    client, factory, world, issuer
):
    amigo = await _program(factory, "amigo", [{"kind": "FREE"}])
    companero = await _program(factory, "companero",
                               [{"kind": "PROGRAM", "program": amigo["id"]}])
    enrolled_amigo = await _enroll_program(client, world["member"], amigo)
    enrollment = await _enroll_program(client, world["member"], companero)
    assert _requirement(enrollment, 1)["status"] == "PENDING"
    assert _requirement(enrollment, 1)["target"]["program"]["id"] == amigo["id"]

    await client.put(f"{ENROLLMENTS}/{enrolled_amigo['id']}/requirements/1",
                     json={"status": "SUBMITTED", "member_note": "Hecho"},
                     headers=world["member"]["headers"])
    await client.post(f"{ENROLLMENTS}/{enrolled_amigo['id']}/requirements/1/review",
                      json={"verdict": "COMPLETE"}, headers=world["director"]["headers"])
    issued = await client.post(f"{ENROLLMENTS}/{enrolled_amigo['id']}/certificate",
                               json={"issued_date": "2026-10-04"},
                               headers=world["director"]["headers"])
    assert issued.status_code == 201, issued.text

    after = _requirement(await _detail(client, world["member"], enrollment["id"]), 1)
    assert after["status"] == "COMPLETE" and after["completed_via"] == "PROGRAM"
    assert after["satisfied_by"]["type"] == "program"


# ----------------------------------------------------------------------------
# HOURS
# ----------------------------------------------------------------------------
async def test_hours_add_up_until_the_requirement_completes_and_come_back_down(
    client, factory, world, fresh
):
    program = await _program(factory, "clase-horas",
                             [{"kind": "HOURS", "quantity": 6, "activity": "SERVICE"}])
    enrollment = await _enroll_program(client, fresh, program)
    requirement = _requirement(enrollment, 1)
    assert requirement["status"] == "PENDING" and requirement["is_practical"] is False
    assert requirement["quantity"] == {"approved": 0.0, "target": 6.0}

    today = date.today().isoformat()
    first = await client.post(LOGS, json={"category": "SERVICE", "performed_on": today,
                                          "quantity": 4, "description": "Limpieza del parque",
                                          "place": "Parque central"},
                              headers=fresh["headers"])
    assert first.status_code == 201, first.text
    assert first.json()[0]["status"] == "SUBMITTED"
    # Nothing counts until the director approves.
    assert _requirement(await _detail(client, fresh, enrollment["id"]), 1)["status"] == "PENDING"

    second = await client.post(LOGS, json={"category": "SERVICE", "performed_on": today,
                                           "quantity": 2, "description": "Visita al asilo"},
                               headers=fresh["headers"])
    for log in (first.json()[0], second.json()[0]):
        decided = await client.post(f"{LOGS}/{log['id']}/decision", json={"status": "APPROVED"},
                                    headers=world["director"]["headers"])
        assert decided.status_code == 200, decided.text

    done = _requirement(await _detail(client, fresh, enrollment["id"]), 1)
    assert done["status"] == "COMPLETE" and done["completed_via"] == "HOURS"
    assert done["quantity"] == {"approved": 6.0, "target": 6.0}

    # An approved log turned down again takes the requirement back to PENDING.
    undone = await client.post(f"{LOGS}/{second.json()[0]['id']}/decision",
                               json={"status": "REJECTED", "note": "No fue una actividad del club"},
                               headers=world["director"]["headers"])
    assert undone.status_code == 200
    back = _requirement(await _detail(client, fresh, enrollment["id"]), 1)
    assert back["status"] == "PENDING" and back["completed_via"] is None
    assert back["quantity"] == {"approved": 4.0, "target": 6.0}


async def test_hours_earned_before_the_enrollment_do_not_count(client, factory, world, fresh):
    program = await _program(factory, "clase-antes",
                             [{"kind": "HOURS", "quantity": 2, "activity": "SERVICE"}])
    enrollment = await _enroll_program(client, fresh, program)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    created = await client.post(LOGS, json={"category": "SERVICE", "performed_on": yesterday,
                                            "quantity": 5, "description": "Antes de empezar",
                                            "user_ids": [fresh["id"]]},
                                headers=world["director"]["headers"])
    assert created.status_code == 201 and created.json()[0]["status"] == "APPROVED"
    assert _requirement(await _detail(client, fresh, enrollment["id"]),
                        1)["status"] == "PENDING"


async def test_attendance_and_service_are_counted_apart(client, factory, world, fresh):
    program = await _program(factory, "clase-asistencia",
                             [{"kind": "HOURS", "quantity": 2, "activity": "ATTENDANCE"}])
    enrollment = await _enroll_program(client, fresh, program)
    today = date.today().isoformat()
    service = await client.post(LOGS, json={"category": "SERVICE", "performed_on": today,
                                            "quantity": 8, "description": "Servicio",
                                            "user_ids": [fresh["id"]]},
                                headers=world["director"]["headers"])
    assert service.status_code == 201
    assert _requirement(await _detail(client, fresh, enrollment["id"]),
                        1)["status"] == "PENDING"
    attendance = await client.post(LOGS, json={"category": "ATTENDANCE", "performed_on": today,
                                               "quantity": 2, "description": "Dos reuniones",
                                               "user_ids": [fresh["id"]]},
                                   headers=world["director"]["headers"])
    assert attendance.status_code == 201
    assert _requirement(await _detail(client, fresh, enrollment["id"]),
                        1)["status"] == "COMPLETE"


async def test_a_requirement_of_hours_is_never_signed_by_hand(client, factory, world, fresh):
    program = await _program(factory, "clase-sin-firma",
                             [{"kind": "HOURS", "quantity": 3, "activity": "SERVICE"}])
    enrollment = await _enroll_program(client, fresh, program)
    sent = await client.put(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1",
                            json={"status": "SUBMITTED", "member_note": "Las hice"},
                            headers=fresh["headers"])
    assert sent.status_code == 409 and "horas" in sent.json()["detail"].lower()
    signed = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/review",
                               json={"verdict": "COMPLETE"},
                               headers=world["director"]["headers"])
    assert signed.status_code == 409 and "horas" in signed.json()["detail"].lower()


# ----------------------------------------------------------------------------
# Who registers and who approves
# ----------------------------------------------------------------------------
async def test_the_director_registers_hours_for_several_members_already_approved(
    client, factory, world
):
    today = date.today().isoformat()
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": today, "quantity": 3,
        "description": "El club hizo 3 h el sábado",
        "user_ids": [world["member"]["id"], world["mate"]["id"]]},
        headers=world["director"]["headers"])
    assert created.status_code == 201, created.text
    rows = created.json()
    assert len(rows) == 2 and {row["status"] for row in rows} == {"APPROVED"}
    assert {row["user"]["id"] for row in rows} == {world["member"]["id"], world["mate"]["id"]}
    audits = await fetch_all(
        "SELECT id FROM audit_log WHERE action = 'ACTIVITY_LOG_CREATE' AND entity_id = ANY(:ids)",
        ids=[row["id"] for row in rows])
    assert len(audits) == 2  # one audit row per member, so each minor's trail is complete


async def test_a_member_never_registers_hours_for_somebody_else(client, world):
    refused = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Ajenas", "user_ids": [world["mate"]["id"]]},
        headers=world["member"]["headers"])
    assert refused.status_code == 403


async def test_a_director_never_approves_their_own_hours(client, world):
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 2,
        "description": "Las mías"}, headers=world["director"]["headers"])
    assert created.status_code == 201 and created.json()[0]["status"] == "SUBMITTED"
    refused = await client.post(f"{LOGS}/{created.json()[0]['id']}/decision",
                                json={"status": "APPROVED"}, headers=world["director"]["headers"])
    assert refused.status_code == 403
    # Their Association decides instead.
    decided = await client.post(f"{LOGS}/{created.json()[0]['id']}/decision",
                                json={"status": "APPROVED"}, headers=world["admin"]["headers"])
    assert decided.status_code == 200


async def test_an_instructor_does_not_approve_hours(client, world):
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Para el instructor"}, headers=world["member"]["headers"])
    refused = await client.post(f"{LOGS}/{created.json()[0]['id']}/decision",
                                json={"status": "APPROVED"},
                                headers=world["instructor"]["headers"])
    assert refused.status_code == 403


async def test_a_director_of_another_club_decides_nothing(client, world):
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "De otro club"}, headers=world["member"]["headers"])
    refused = await client.post(f"{LOGS}/{created.json()[0]['id']}/decision",
                                json={"status": "APPROVED"},
                                headers=world["director_b"]["headers"])
    assert refused.status_code == 403


async def test_rejecting_needs_a_note(client, world):
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Sin nota"}, headers=world["member"]["headers"])
    refused = await client.post(f"{LOGS}/{created.json()[0]['id']}/decision",
                                json={"status": "REJECTED"}, headers=world["director"]["headers"])
    assert refused.status_code == 422


async def test_a_future_date_is_refused(client, world):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    refused = await client.post(LOGS, json={"category": "SERVICE", "performed_on": tomorrow,
                                            "quantity": 1, "description": "Mañana"},
                                headers=world["member"]["headers"])
    assert refused.status_code == 422


async def test_the_queue_and_the_list_of_the_club(client, world, fresh):
    created = await client.post(LOGS, json={
        "category": "ATTENDANCE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Reunión del sábado"}, headers=fresh["headers"])
    log_id = created.json()[0]["id"]

    queue = await client.get(f"{ACTIVITY}/queue", headers=world["director"]["headers"])
    assert queue.status_code == 200
    assert log_id in [row["id"] for row in queue.json()]
    # A club whose members are not these ones sees nothing of them.
    other = await client.get(f"{ACTIVITY}/queue", headers=world["director_b"]["headers"])
    assert log_id not in [row["id"] for row in other.json()]

    mine = await client.get(LOGS, headers=fresh["headers"])
    assert mine.status_code == 200
    assert log_id in [row["id"] for row in mine.json()["logs"]]
    assert mine.json()["totals"]["ATTENDANCE"] == 0.0  # still not approved


async def test_the_log_of_a_minor_is_not_public(client, world, fresh_minor):
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Dónde estuvo el sábado", "place": "Calle 5"},
        headers=fresh_minor["headers"])
    assert created.status_code == 201
    stranger = await client.get(LOGS, params={"user_id": fresh_minor["id"]},
                                headers=world["stranger"]["headers"])
    assert stranger.status_code == 403
    assert (await client.get(LOGS, params={"user_id": fresh_minor["id"]})).status_code in (401, 403)
    director = await client.get(LOGS, params={"user_id": fresh_minor["id"]},
                                headers=world["director"]["headers"])
    assert director.status_code == 200
    assert director.json()["logs"][0]["place"] == "Calle 5"


async def test_the_member_deletes_only_their_own_pending_log(client, world):
    created = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Me equivoqué"}, headers=world["member"]["headers"])
    log_id = created.json()[0]["id"]
    assert (await client.delete(f"{LOGS}/{log_id}",
                                headers=world["mate"]["headers"])).status_code == 403
    assert (await client.delete(f"{LOGS}/{log_id}",
                                headers=world["member"]["headers"])).status_code == 204
    assert await fetch_one("SELECT id FROM activity_logs WHERE id = :id", id=log_id) is None

    approved = await client.post(LOGS, json={
        "category": "SERVICE", "performed_on": date.today().isoformat(), "quantity": 1,
        "description": "Ya aprobada", "user_ids": [world["member"]["id"]]},
        headers=world["director"]["headers"])
    refused = await client.delete(f"{LOGS}/{approved.json()[0]['id']}",
                                  headers=world["member"]["headers"])
    assert refused.status_code == 409


# ----------------------------------------------------------------------------
# Nothing of block A moves
# ----------------------------------------------------------------------------
async def test_an_honor_enrollment_is_untouched_by_the_links(client, factory, world, issuer):
    """The automation only ever looks at PROGRAM enrollments."""
    honor = await _honor(factory, "intacta")
    created = await client.post(ENROLLMENTS, json={"honor_id": honor["id"]},
                                headers=world["member"]["headers"])
    enrollment = created.json()
    assert _requirement(enrollment, 1)["kind"] == "FREE"
    other = await _honor(factory, "otra-mas")
    await _certified_honor(client, world["member"], other, world["director"])
    after = await _detail(client, world["member"], enrollment["id"])
    assert _requirement(after, 1)["status"] == "PENDING"
    assert _requirement(after, 1)["satisfied_by"] is None
