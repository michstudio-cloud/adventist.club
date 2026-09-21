"""Bloque A — Portafolio: enrollment, progress per requirement, private evidence, review verdicts,
automatic READY, the certificate linked to the account, and who may see or decide what.

The private bucket is always a fake: no test reaches the network.
"""

import uuid

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import private_storage
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

PORTFOLIO = "/api/v1/portfolio"
ENROLLMENTS = f"{PORTFOLIO}/enrollments"
EVIDENCES = f"{PORTFOLIO}/evidences"
QUEUE = f"{PORTFOLIO}/review/queue"
MB = 1024 * 1024


def _portfolio_tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.honor_enrollments') IS NOT NULL"
                    " AND to_regclass('public.certificates') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _portfolio_tables_exist(),
        reason="apply tests/sql/base_certificates.sql and migrations/007_portfolio.sql to the test database",
    ),
]
factory = module_factory("portfolio")


# ----------------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def world(factory):
    """An association with two clubs and everyone who matters in the permission matrix."""
    association = await factory.org("assoc", "association")
    club_a = await factory.org("club-a", "club", association)
    club_b = await factory.org("club-b", "club", association)
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    people = {
        "member": await factory.user("member", "STUDENT", club_a["id"], is_minor=True),
        "director": await factory.user("director-a", "CLUB_DIRECTOR", club_a["id"]),
        "instructor": await factory.user("instructor-a", "INSTRUCTOR", club_a["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", club_b["id"]),
        "admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "guardian_pending": await factory.user("guardian-pending", "PARENT_GUARDIAN"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": issuer_code, "id": uuid.UUID(issuer["id"])})
        # an instructor reviews only once verified (the factory creates accounts as PENDING)
        await db.execute(text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
                         {"id": uuid.UUID(people["instructor"]["id"])})
        for label, consent in (("guardian", "APPROVED"), ("guardian_pending", "PENDING")):
            await db.execute(text(
                "INSERT INTO guardianships (guardian_id, child_id, consent_status) VALUES (:g, :c, :s)"),
                {"g": uuid.UUID(people[label]["id"]), "c": uuid.UUID(people["member"]["id"]), "s": consent})
        await db.commit()
    return {**people, "association": association, "club_a": club_a, "club_b": club_b, "issuer_code": issuer_code}


@pytest.fixture
def issuer(world, monkeypatch):
    """Certificates are issued by an organisation of this run, never by a placeholder that would persist."""
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


class FakePrivateR2:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.presigned: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presigned.append({"operation": operation, "expires": ExpiresIn, **Params})
        return f"https://r2.test/{Params['Bucket']}/{Params['Key']}?op={operation}&X-Amz-Expires={ExpiresIn}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
        return self.objects[Key]

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def store_last_upload(self, size: int, content_type: str) -> str:
        """What the browser does with the presigned PUT."""
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


async def _honor(factory, label, theoretical=(True, True), *, status="PUBLISHED", english=False) -> dict:
    """A published honor with one Spanish requirement per flag (and the same list in English)."""
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, name, slug, active, status) VALUES (:id, :m, :name, :slug, true, :status)"),
            {"id": honor_id, "m": ministry, "name": name, "slug": f"{factory.prefix}-{label}", "status": status})
        for locale in ("es", "en") if english else ("es",):
            for position, is_theoretical in enumerate(theoretical, start=1):
                await db.execute(text(
                    "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale)"
                    " VALUES (:h, :p, :d, :t, :l)"),
                    {"h": honor_id, "p": position, "d": f"{locale.upper()} requisito {position}",
                     "t": is_theoretical, "l": locale})
        if english:
            await db.execute(text("INSERT INTO honor_translations (honor_id, locale, name) VALUES (:h, 'en', :n)"),
                             {"h": honor_id, "n": f"{name} (EN)"})
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _enroll(client, user, honor, **extra) -> dict:
    response = await client.post(ENROLLMENTS, json={"honor_id": honor["id"], **extra}, headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _submit(client, user, enrollment_id, position, status="SUBMITTED", **extra):
    if status == "SUBMITTED":
        extra.setdefault("member_note", "Respuesta de prueba")     # an empty requirement cannot be sent
    return await client.put(f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}",
                            json={"status": status, **extra}, headers=user["headers"])


async def _review(client, reviewer, enrollment_id, position, verdict="COMPLETE", note=None):
    body = {"verdict": verdict} if note is None else {"verdict": verdict, "note": note}
    return await client.post(f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}/review",
                             json=body, headers=reviewer["headers"] if reviewer else None)


async def _complete_all(client, member, reviewer, enrollment) -> None:
    for requirement in enrollment["requirements"]:
        assert (await _submit(client, member, enrollment["id"], requirement["position"])).status_code == 200
        done = await _review(client, reviewer, enrollment["id"], requirement["position"])
        assert done.status_code == 200, done.text


async def _add_evidence(client, r2, user, enrollment_id, position, content_type="image/jpeg", size=2048, **meta):
    """Create, "upload" to the fake bucket and confirm. Returns the ACTIVE evidence."""
    created = await client.post(f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}/evidences",
                                json={"content_type": content_type, "size_bytes": size, **meta},
                                headers=user["headers"])
    assert created.status_code == 201, created.text
    r2.store_last_upload(size, content_type)
    done = await client.post(f"{EVIDENCES}/{created.json()['evidence']['id']}/complete", headers=user["headers"])
    assert done.status_code == 200, done.text
    return done.json()


async def _get(client, user, enrollment_id):
    return await client.get(f"{ENROLLMENTS}/{enrollment_id}", headers=user["headers"] if user else None)


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all("SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id))
    return [row["action"] for row in rows]


def _requirement(detail: dict, position: int) -> dict:
    return next(r for r in detail["requirements"] if r["position"] == position)


# ----------------------------------------------------------------------------
# Enrollment
# ----------------------------------------------------------------------------
async def test_everything_needs_a_session(client):
    anything = uuid.uuid4()
    for method, path in [
        ("POST", ENROLLMENTS), ("GET", ENROLLMENTS), ("GET", f"{ENROLLMENTS}/{anything}"),
        ("DELETE", f"{ENROLLMENTS}/{anything}"), ("PUT", f"{ENROLLMENTS}/{anything}/requirements/1"),
        ("POST", f"{ENROLLMENTS}/{anything}/requirements/1/evidences"),
        ("POST", f"{EVIDENCES}/{anything}/complete"), ("GET", f"{EVIDENCES}/{anything}/url"),
        ("DELETE", f"{EVIDENCES}/{anything}"), ("GET", QUEUE),
        ("POST", f"{ENROLLMENTS}/{anything}/requirements/1/review"),
        ("POST", f"{ENROLLMENTS}/{anything}/certificate"),
        ("GET", f"{PORTFOLIO}/users/{anything}"), ("GET", f"{PORTFOLIO}/me"),
    ]:
        response = await client.request(method, path, json={} if method in ("POST", "PUT") else None)
        assert response.status_code == 401, (method, path, response.status_code)


async def test_enroll_is_idempotent_and_pinned_to_version_and_locale(client, factory, world):
    member = world["member"]
    honor = await _honor(factory, "nudos", theoretical=(True, False, True), english=True)

    first = await client.post(ENROLLMENTS, json={"honor_id": honor["id"], "locale": "en-US"}, headers=member["headers"])
    assert first.status_code == 201, first.text
    detail = first.json()
    assert detail["status"] == "IN_PROGRESS" and detail["mode"] == "CLUB" and detail["locale"] == "en"
    assert detail["honor"] == {**detail["honor"], "id": honor["id"], "name": f"{honor['name']} (EN)"}
    assert detail["club"]["id"] == world["club_a"]["id"]
    assert detail["permissions"] == {"is_owner": True, "can_review": False, "can_issue": False}
    assert detail["counters"] == {"total": 3, "complete": 0, "submitted": 0, "incomplete": 0}
    assert [(r["position"], r["description"], r["is_practical"], r["status"]) for r in detail["requirements"]] == [
        (1, "EN requisito 1", False, "PENDING"), (2, "EN requisito 2", True, "PENDING"),
        (3, "EN requisito 3", False, "PENDING")]
    assert all(r["evidences"] == [] and r["requirement_id"] for r in detail["requirements"])

    again = await client.post(ENROLLMENTS, json={"honor_id": honor["id"], "locale": "es"}, headers=member["headers"])
    assert again.status_code == 200 and again.json()["id"] == detail["id"]
    assert again.json()["locale"] == "en"                       # the language chosen at enrollment stays
    assert await _audit_actions(detail["id"]) == ["ENROLL"]    # the second call changed nothing

    # is_practical was copied: flipping the flag on the catalogue does not touch this enrollment
    async with SessionLocal() as db:
        await db.execute(text("UPDATE honor_requirements SET is_theoretical = true WHERE honor_id = :h"),
                         {"h": uuid.UUID(honor["id"])})
        await db.commit()
    assert _requirement((await _get(client, member, detail["id"])).json(), 2)["is_practical"] is True

    # a language the honor does not have falls back to Spanish, like the catalogue
    stranger = await _enroll(client, world["stranger"], honor, locale="fr")
    assert stranger["locale"] == "es" and stranger["requirements"][0]["description"] == "ES requisito 1"
    assert stranger["club"] is None and stranger["honor"]["name"] == honor["name"]


async def test_enroll_rejects_unpublished_unknown_and_empty_honors(client, factory, world):
    member = world["member"]
    draft = await _honor(factory, "borrador", status="DRAFT")
    empty = await _honor(factory, "vacia", theoretical=())
    for honor_id, expected in ((draft["id"], 404), (str(uuid.uuid4()), 404), (empty["id"], 409)):
        response = await client.post(ENROLLMENTS, json={"honor_id": honor_id}, headers=member["headers"])
        assert response.status_code == expected, response.text
    assert (await client.post(ENROLLMENTS, json={"honor_id": empty["id"], "locale": "no locale"},
                              headers=member["headers"])).status_code == 422
    assert await fetch_one("SELECT 1 AS x FROM honor_enrollments WHERE honor_id = :h", h=uuid.UUID(empty["id"])) is None


async def test_withdraw_frees_the_honor_and_freezes_the_old_enrollment(client, factory, world, r2):
    member = world["member"]
    honor = await _honor(factory, "retiro")
    enrollment = await _enroll(client, member, honor)
    url = f"{ENROLLMENTS}/{enrollment['id']}"

    assert (await client.delete(url, headers=world["director"]["headers"])).status_code == 403   # owner only
    assert (await client.delete(url, headers=member["headers"])).status_code == 204
    row = await fetch_one("SELECT status, withdrawn_at FROM honor_enrollments WHERE id = :id", id=uuid.UUID(enrollment["id"]))
    assert row["status"] == "WITHDRAWN" and row["withdrawn_at"] is not None
    assert await _audit_actions(enrollment["id"]) == ["ENROLL", "ENROLLMENT_WITHDRAW"]

    assert (await _submit(client, member, enrollment["id"], 1)).status_code == 409
    assert (await _review(client, world["director"], enrollment["id"], 1)).status_code == 409
    blocked = await client.post(f"{url}/requirements/1/evidences", json={"content_type": "image/png", "size_bytes": 10},
                                headers=member["headers"])
    assert blocked.status_code == 409

    fresh = await _enroll(client, member, honor)                 # a withdrawn enrollment is history
    assert fresh["id"] != enrollment["id"] and fresh["status"] == "IN_PROGRESS"
    mine = await client.get(ENROLLMENTS, headers=member["headers"])
    assert enrollment["id"] not in [row["id"] for row in mine.json()]
    withdrawn = await client.get(ENROLLMENTS, params={"status": "WITHDRAWN"}, headers=member["headers"])
    assert enrollment["id"] in [row["id"] for row in withdrawn.json()]


# ----------------------------------------------------------------------------
# Requirement state machine
# ----------------------------------------------------------------------------
async def test_requirement_state_machine(client, factory, world):
    member, director = world["member"], world["director"]
    enrollment = await _enroll(client, member, await _honor(factory, "estados"))
    eid = enrollment["id"]

    sent = await _submit(client, member, eid, 1, member_note="Lo hice en el campamento")
    assert sent.status_code == 200, sent.text
    first = _requirement(sent.json(), 1)
    assert first["status"] == "SUBMITTED" and first["submitted_at"] and first["member_note"] == "Lo hice en el campamento"
    assert sent.json()["counters"]["submitted"] == 1

    back = await _submit(client, member, eid, 1, status="PENDING")           # not reviewed yet: may take it back
    assert _requirement(back.json(), 1)["status"] == "PENDING"
    assert _requirement(back.json(), 1)["member_note"] == "Lo hice en el campamento"   # untouched when omitted

    assert (await _submit(client, member, eid, 1, status="COMPLETE")).status_code == 422    # never the member's call
    assert (await _submit(client, member, eid, 1, member_note="x" * 2001)).status_code == 422
    assert (await _submit(client, member, eid, 99)).status_code == 404
    assert (await _submit(client, director, eid, 1)).status_code == 403                     # owner only
    assert (await _review(client, director, eid, 1)).status_code == 409                     # nothing was submitted

    await _submit(client, member, eid, 1)
    rejected = await _review(client, director, eid, 1, "INCOMPLETE", "Falta explicar el segundo punto")
    assert rejected.status_code == 200, rejected.text
    row = _requirement(rejected.json(), 1)
    assert (row["status"], row["review_note"], row["completed_via"]) == ("INCOMPLETE", "Falta explicar el segundo punto", None)
    assert row["reviewed_by"]["id"] == director["id"] and row["reviewed_at"]
    assert rejected.json()["counters"]["incomplete"] == 1

    assert (await _submit(client, member, eid, 1, status="PENDING")).status_code == 409     # INCOMPLETE -> SUBMITTED only
    assert (await _submit(client, member, eid, 1, member_note="Corregido")).status_code == 200
    approved = await _review(client, director, eid, 1)
    row = _requirement(approved.json(), 1)
    assert (row["status"], row["completed_via"]) == ("COMPLETE", "REVIEW")
    assert (await _submit(client, member, eid, 1)).status_code == 409                       # COMPLETE is closed to the member

    progress = await fetch_one(
        "SELECT id FROM requirement_progress WHERE enrollment_id = :e AND requirement_position = 1", e=uuid.UUID(eid))
    actions = await _audit_actions(progress["id"])
    assert actions.count("REQUIREMENT_SUBMIT") == 4 and actions.count("REQUIREMENT_REVIEW") == 2


# ----------------------------------------------------------------------------
# Permissions
# ----------------------------------------------------------------------------
async def test_permission_matrix(client, factory, world, issuer):
    member = world["member"]
    enrollment = await _enroll(client, member, await _honor(factory, "matriz", theoretical=(True,)))
    eid = enrollment["id"]
    assert (await _submit(client, member, eid, 1)).status_code == 200

    #                      view  review  (can_review, can_issue) shown to that viewer
    matrix = {
        "member":           (200, 403, (False, False)),
        "director":         (200, 200, (True, True)),
        "instructor":       (200, 200, (True, False)),
        "director_b":       (403, 403, None),
        "guardian":         (200, 403, (False, False)),
        "guardian_pending": (403, 403, None),
        "admin":            (200, 403, (False, False)),
        "master":           (200, 200, (True, True)),
        "stranger":         (403, 403, None),
    }
    for who, (view, review, flags) in matrix.items():
        actor = world[who]
        seen = await _get(client, actor, eid)
        assert seen.status_code == view, (who, seen.text)
        if flags:
            permissions = seen.json()["permissions"]
            assert (permissions["can_review"], permissions["can_issue"]) == flags, who
            assert permissions["is_owner"] is (who == "member")
        portfolio = await client.get(f"{PORTFOLIO}/users/{member['id']}", headers=actor["headers"])
        assert portfolio.status_code == view, (who, portfolio.text)

        # an INCOMPLETE verdict keeps the requirement reviewable for the next actor
        verdict = await _review(client, actor, eid, 1, "INCOMPLETE", f"revisado por {who}")
        assert verdict.status_code == review, (who, verdict.text)
        if review == 200:
            assert (await _submit(client, member, eid, 1)).status_code == 200

        issue = await client.post(f"{ENROLLMENTS}/{eid}/certificate", json={"issued_date": "2026-09-22"},
                                  headers=actor["headers"])
        assert issue.status_code == (409 if flags and flags[1] else 403), (who, issue.text)   # 409: allowed, not READY

    assert (await _get(client, None, eid)).status_code == 401
    assert (await _review(client, None, eid, 1)).status_code == 401
    assert (await _get(client, member, str(uuid.uuid4()))).status_code == 404


async def test_a_director_without_an_approved_club_reviews_nobody(client, factory, world):
    pending = await factory.user("director-pendiente", "CLUB_DIRECTOR", world["club_a"]["id"])
    async with SessionLocal() as db:
        await db.execute(text("UPDATE users SET club_approval = 'PENDING' WHERE id = :id"), {"id": uuid.UUID(pending["id"])})
        await db.commit()
    enrollment = await _enroll(client, world["member"], await _honor(factory, "pendiente"))
    await _submit(client, world["member"], enrollment["id"], 1)
    assert (await _get(client, pending, enrollment["id"])).status_code == 403
    assert (await _review(client, pending, enrollment["id"], 1)).status_code == 403
    assert (await client.get(QUEUE, headers=pending["headers"])).status_code == 403


async def test_an_unverified_instructor_reviews_nobody(client, factory, world):
    newcomer = await factory.user("instructor-nuevo", "INSTRUCTOR", world["club_a"]["id"])
    enrollment = await _enroll(client, world["member"], await _honor(factory, "sin-verificar"))
    eid = enrollment["id"]
    await _submit(client, world["member"], eid, 1)

    assert (await _review(client, newcomer, eid, 1)).status_code == 403
    assert (await client.get(QUEUE, headers=newcomer["headers"])).status_code == 403
    # ...and does not get to look either: a portfolio holds evidence of minors
    assert (await _get(client, newcomer, eid)).status_code == 403
    assert (await client.get(f"{PORTFOLIO}/users/{world['member']['id']}", headers=newcomer["headers"])).status_code == 403

    async with SessionLocal() as db:
        await db.execute(text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
                         {"id": uuid.UUID(newcomer["id"])})
        await db.commit()
    assert (await client.get(QUEUE, headers=newcomer["headers"])).status_code == 200
    assert (await _review(client, newcomer, eid, 1)).status_code == 200


async def test_nobody_reviews_or_certifies_their_own_enrollment(client, factory, world, issuer):
    director, instructor, master = world["director"], world["instructor"], world["master"]
    honor = await _honor(factory, "propia", theoretical=(True,))

    own = await _enroll(client, director, honor)                 # the director is a member of their own club
    assert own["permissions"] == {"is_owner": True, "can_review": False, "can_issue": False}
    await _submit(client, director, own["id"], 1)
    assert (await _review(client, director, own["id"], 1)).status_code == 403
    assert (await _review(client, instructor, own["id"], 1)).status_code == 200              # someone else does
    ready = await _get(client, director, own["id"])
    assert ready.json()["status"] == "READY"
    issue = await client.post(f"{ENROLLMENTS}/{own['id']}/certificate", json={"issued_date": "2026-09-22"},
                              headers=director["headers"])
    assert issue.status_code == 403

    masters = await _enroll(client, master, honor)               # not even MASTER_GC
    await _submit(client, master, masters["id"], 1)
    assert (await _review(client, master, masters["id"], 1)).status_code == 403


async def test_jurisdiction_follows_the_members_current_club(client, factory, world):
    mover = await factory.user("mover", "STUDENT", world["club_a"]["id"])
    enrollment = await _enroll(client, mover, await _honor(factory, "mudanza"))
    eid = enrollment["id"]
    await _submit(client, mover, eid, 1)
    assert (await _get(client, world["director"], eid)).json()["permissions"]["can_review"] is True

    async with SessionLocal() as db:
        await db.execute(text("UPDATE users SET organization_id = :club WHERE id = :id"),
                         {"club": uuid.UUID(world["club_b"]["id"]), "id": uuid.UUID(mover["id"])})
        await db.commit()
    assert (await _review(client, world["director"], eid, 1)).status_code == 403            # no longer their member
    assert (await _review(client, world["director_b"], eid, 1)).status_code == 200
    row = await fetch_one("SELECT club_id::text AS club_id FROM honor_enrollments WHERE id = :id", id=uuid.UUID(eid))
    assert row["club_id"] == world["club_b"]["id"]                                           # refreshed on write


# ----------------------------------------------------------------------------
# Integrity rules
# ----------------------------------------------------------------------------
async def test_practical_requirement_needs_active_evidence_to_be_completed(client, factory, world, r2):
    member, director = world["member"], world["director"]
    enrollment = await _enroll(client, member, await _honor(factory, "practica", theoretical=(False,)))
    eid = enrollment["id"]
    nothing = await _submit(client, member, eid, 1)                        # text alone does not send a practical one
    assert nothing.status_code == 422 and "pide evidencia" in nothing.json()["detail"]

    # an upload that was never confirmed is not evidence
    pending = await client.post(f"{ENROLLMENTS}/{eid}/requirements/1/evidences",
                                json={"content_type": "image/png", "size_bytes": 512}, headers=member["headers"])
    assert pending.status_code == 201
    assert (await _submit(client, member, eid, 1)).status_code == 422

    evidence = await _add_evidence(client, r2, member, eid, 1)
    assert (await _submit(client, member, eid, 1)).status_code == 200

    assert (await _review(client, director, eid, 1, "INCOMPLETE")).status_code == 422       # a note is mandatory
    assert (await _review(client, director, eid, 1, "INCOMPLETE", "   ")).status_code == 422

    # the member may still take the photo away while waiting: the verdict checks again (rule 1)
    removed = await client.delete(f"{PORTFOLIO}/evidences/{evidence['id']}", headers=member["headers"])
    assert removed.status_code == 204
    without = await _review(client, director, eid, 1)
    assert without.status_code == 409 and "evidencia" in without.json()["detail"].lower()

    await _add_evidence(client, r2, member, eid, 1)
    done = await _review(client, director, eid, 1)
    assert done.status_code == 200 and _requirement(done.json(), 1)["status"] == "COMPLETE"


async def test_ready_is_automatic_and_reverts(client, factory, world):
    member, director, instructor = world["member"], world["director"], world["instructor"]
    enrollment = await _enroll(client, member, await _honor(factory, "lista", theoretical=(True, True)))
    eid = enrollment["id"]
    for position in (1, 2):
        await _submit(client, member, eid, position)

    half = await _review(client, director, eid, 1)
    assert half.json()["status"] == "IN_PROGRESS" and half.json()["ready_at"] is None
    full = await _review(client, instructor, eid, 2)
    assert full.json()["status"] == "READY" and full.json()["ready_at"]
    assert full.json()["counters"] == {"total": 2, "complete": 2, "submitted": 0, "incomplete": 0}

    reopened = await _review(client, director, eid, 2, "INCOMPLETE", "Repetir la demostración")   # rule 4: reviewer reopens
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "IN_PROGRESS" and reopened.json()["ready_at"] is None
    assert (await _review(client, director, eid, 2, "INCOMPLETE", "otra vez")).status_code == 409  # waits for the member

    await _submit(client, member, eid, 2)
    assert (await _review(client, director, eid, 2)).json()["status"] == "READY"


# ----------------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------------
async def test_evidence_upload_handshake(client, factory, world, r2):
    member, director = world["member"], world["director"]
    enrollment = await _enroll(client, member, await _honor(factory, "evidencia", theoretical=(False, True)))
    eid = enrollment["id"]
    create_url = f"{ENROLLMENTS}/{eid}/requirements/1/evidences"

    created = await client.post(create_url, headers=member["headers"], json={
        "content_type": "image/jpeg", "size_bytes": 4096, "sha256": "a" * 64,
        "taken_on": "2026-09-20", "place": "Reynosa", "caption": "Nudo as de guía"})
    assert created.status_code == 201, created.text
    evidence, upload = created.json()["evidence"], created.json()["upload"]
    assert evidence["status"] == "PENDING_UPLOAD" and evidence["kind"] == "image"
    assert (evidence["taken_on"], evidence["place"], evidence["caption"]) == ("2026-09-20", "Reynosa", "Nudo as de guía")
    assert "url" not in evidence and "storage_key" not in evidence
    assert upload["method"] == "PUT" and upload["expires_in"] == 600 and upload["headers"] == {"Content-Type": "image/jpeg"}
    signed = r2.presigned[-1]
    assert signed["operation"] == "put_object" and signed["Bucket"] == "evidence-test" and signed["expires"] == 600
    assert signed["Key"] == f"evidence/{member['id']}/{eid}/{evidence['id']}.jpg"
    assert (signed["ContentType"], signed["ContentLength"]) == ("image/jpeg", 4096)   # the URL only accepts what was declared

    complete_url = f"{EVIDENCES}/{evidence['id']}/complete"
    assert (await client.post(complete_url, headers=member["headers"])).status_code == 409        # nothing uploaded yet
    assert (await client.post(complete_url, headers=director["headers"])).status_code == 403      # only who uploads
    assert (await client.get(f"{EVIDENCES}/{evidence['id']}/url", headers=member["headers"])).status_code == 404

    r2.objects[signed["Key"]] = {"ContentLength": 4097, "ContentType": "image/jpeg"}
    assert (await client.post(complete_url, headers=member["headers"])).status_code == 409        # size differs
    r2.objects[signed["Key"]] = {"ContentLength": 4096, "ContentType": "image/png"}
    assert (await client.post(complete_url, headers=member["headers"])).status_code == 409        # type differs
    r2.objects[signed["Key"]] = {"ContentLength": 4096, "ContentType": "image/jpeg"}
    done = await client.post(complete_url, headers=member["headers"])
    assert done.status_code == 200 and done.json()["status"] == "ACTIVE"
    assert (await client.post(complete_url, headers=member["headers"])).status_code == 200        # idempotent

    detail = (await _get(client, director, eid)).json()
    listed = _requirement(detail, 1)["evidences"]
    assert [e["id"] for e in listed] == [evidence["id"]] and "url" not in listed[0] and "storage_key" not in listed[0]

    for viewer, expected in (("member", 200), ("director", 200), ("guardian", 200), ("admin", 200),
                             ("director_b", 403), ("guardian_pending", 403), ("stranger", 403)):
        link = await client.get(f"{EVIDENCES}/{evidence['id']}/url", headers=world[viewer]["headers"])
        assert link.status_code == expected, viewer
    link = await client.get(f"{EVIDENCES}/{evidence['id']}/url", headers=director["headers"])
    assert link.json()["expires_in"] == 300 and link.json()["url"].startswith("https://r2.test/evidence-test/evidence/")
    assert r2.presigned[-1]["operation"] == "get_object" and r2.presigned[-1]["expires"] == 300

    for body, expected in (({"content_type": "image/gif", "size_bytes": 10}, 422),
                           ({"content_type": "image/png", "size_bytes": 0}, 422),
                           ({"content_type": "image/png", "size_bytes": 10 * MB + 1}, 413),
                           ({"content_type": "application/pdf", "size_bytes": 20 * MB + 1}, 413),
                           ({"content_type": "image/png", "size_bytes": 10, "sha256": "zz"}, 422)):
        assert (await client.post(create_url, json=body, headers=member["headers"])).status_code == expected, body
    pdf = await client.post(create_url, json={"content_type": "application/pdf", "size_bytes": 20 * MB},
                            headers=member["headers"])
    assert pdf.status_code == 201 and pdf.json()["evidence"]["kind"] == "pdf" and r2.presigned[-1]["Key"].endswith(".pdf")
    assert (await client.post(create_url, json={"content_type": "image/png", "size_bytes": 10},
                              headers=director["headers"])).status_code == 403              # owner only

    removed = await client.delete(f"{EVIDENCES}/{evidence['id']}", headers=member["headers"])
    assert removed.status_code == 204
    row = await fetch_one("SELECT status, removed_at FROM evidences WHERE id = :id", id=uuid.UUID(evidence["id"]))
    assert row["status"] == "REMOVED" and row["removed_at"] is not None
    assert signed["Key"] in r2.objects                                     # logical: the purge script deletes objects
    assert _requirement((await _get(client, member, eid)).json(), 1)["evidences"] == []
    assert (await client.get(f"{EVIDENCES}/{evidence['id']}/url", headers=member["headers"])).status_code == 404
    assert (await client.delete(f"{EVIDENCES}/{evidence['id']}", headers=director["headers"])).status_code == 403
    assert await _audit_actions(evidence["id"]) == ["EVIDENCE_ADD", "EVIDENCE_REMOVE"]


async def test_evidence_limit_and_closed_requirements(client, factory, world, r2):
    member, director = world["member"], world["director"]
    enrollment = await _enroll(client, member, await _honor(factory, "limite", theoretical=(False,)))
    eid = enrollment["id"]
    for _ in range(6):
        last = await _add_evidence(client, r2, member, eid, 1, content_type="image/webp", size=100)
    over = await client.post(f"{ENROLLMENTS}/{eid}/requirements/1/evidences",
                             json={"content_type": "image/webp", "size_bytes": 100}, headers=member["headers"])
    assert over.status_code == 409 and "6" in over.json()["detail"]

    await _submit(client, member, eid, 1)
    assert (await _review(client, director, eid, 1)).status_code == 200
    # rule 4: a COMPLETE requirement takes no more evidence and loses none
    assert (await client.delete(f"{EVIDENCES}/{last['id']}", headers=member["headers"])).status_code == 409
    assert (await client.post(f"{ENROLLMENTS}/{eid}/requirements/1/evidences",
                              json={"content_type": "image/webp", "size_bytes": 100},
                              headers=member["headers"])).status_code == 409


async def test_without_the_private_bucket_only_evidence_is_unavailable(client, factory, world):
    member, director = world["member"], world["director"]
    assert settings.private_storage_configured is False
    enrollment = await _enroll(client, member, await _honor(factory, "sin-bucket", theoretical=(True,)))
    response = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/requirements/1/evidences",
                                 json={"content_type": "image/png", "size_bytes": 10}, headers=member["headers"])
    assert response.status_code == 503
    assert response.json() == {"detail": "Almacenamiento privado no configurado"}
    assert (await _submit(client, member, enrollment["id"], 1)).status_code == 200            # the rest works
    assert (await _review(client, director, enrollment["id"], 1)).json()["status"] == "READY"


# ----------------------------------------------------------------------------
# Review queue and listings
# ----------------------------------------------------------------------------
async def test_review_queue_is_scoped_to_the_reviewers_club(client, factory, world):
    member, director, instructor = world["member"], world["director"], world["instructor"]
    honor = await _honor(factory, "cola", theoretical=(True, True))
    enrollment = await _enroll(client, member, honor)
    eid = enrollment["id"]
    await _submit(client, member, eid, 2, member_note="Listo")
    own = await _enroll(client, director, honor)                 # the director's own work never shows up in their queue
    await _submit(client, director, own["id"], 1)

    queue = await client.get(QUEUE, headers=director["headers"])
    assert queue.status_code == 200, queue.text
    mine = [row for row in queue.json() if row["honor"]["id"] == honor["id"]]
    assert [(row["enrollment_id"], row["position"]) for row in mine] == [(eid, 2)]
    assert mine[0]["member"] == {"id": member["id"], "name": factory.name("member")}
    assert mine[0]["member_note"] == "Listo" and mine[0]["evidence_count"] == 0 and mine[0]["submitted_at"]

    seen_by_instructor = [r["enrollment_id"] for r in (await client.get(QUEUE, headers=instructor["headers"])).json()
                          if r["honor"]["id"] == honor["id"]]
    assert sorted(seen_by_instructor) == sorted([eid, own["id"]])
    other = await client.get(QUEUE, headers=world["director_b"]["headers"])
    assert [r for r in other.json() if r["honor"]["id"] == honor["id"]] == []
    everything = await client.get(QUEUE, headers=world["master"]["headers"])
    assert {eid, own["id"]} <= {r["enrollment_id"] for r in everything.json()}
    for outsider in ("member", "guardian", "admin", "stranger"):
        assert (await client.get(QUEUE, headers=world[outsider]["headers"])).status_code == 403, outsider
    assert (await client.get(QUEUE, params={"status": "PENDING"}, headers=director["headers"])).status_code == 422

    await _submit(client, member, eid, 1)
    for position in (1, 2):
        await _review(client, director, eid, position)
    ready = await client.get(QUEUE, params={"status": "READY"}, headers=director["headers"])
    row = next(r for r in ready.json() if r["enrollment_id"] == eid)
    assert row["ready_at"] and row["can_issue"] is True and row["member"]["id"] == member["id"]
    as_instructor = await client.get(QUEUE, params={"status": "READY"}, headers=instructor["headers"])
    assert next(r for r in as_instructor.json() if r["enrollment_id"] == eid)["can_issue"] is False
    assert eid not in [r["enrollment_id"] for r in (await client.get(QUEUE, headers=director["headers"])).json()]


async def test_my_enrollments_with_counters_and_status_filter(client, factory, world):
    learner = await factory.user("learner", "STUDENT", world["club_a"]["id"])
    first = await _enroll(client, learner, await _honor(factory, "lista-uno", theoretical=(True, True, True)))
    second = await _enroll(client, learner, await _honor(factory, "lista-dos", theoretical=(True,)))
    await _submit(client, learner, first["id"], 1)
    await _submit(client, learner, first["id"], 2)
    await _review(client, world["director"], first["id"], 1)
    await _review(client, world["director"], first["id"], 2, "INCOMPLETE", "Falta")
    await _submit(client, learner, second["id"], 1)
    await _review(client, world["director"], second["id"], 1)

    rows = {row["id"]: row for row in (await client.get(ENROLLMENTS, headers=learner["headers"])).json()}
    assert set(rows) == {first["id"], second["id"]}
    assert rows[first["id"]]["counters"] == {"total": 3, "complete": 1, "submitted": 0, "incomplete": 1}
    assert rows[second["id"]]["status"] == "READY" and "requirements" not in rows[second["id"]]
    ready = await client.get(ENROLLMENTS, params={"status": "READY"}, headers=learner["headers"])
    assert [row["id"] for row in ready.json()] == [second["id"]]
    assert (await client.get(ENROLLMENTS, params={"status": "nope"}, headers=learner["headers"])).status_code == 422

    me = await client.get(f"{PORTFOLIO}/me", headers=learner["headers"])
    assert me.status_code == 200 and me.json()["user"]["id"] == learner["id"]
    assert {row["id"] for row in me.json()["enrollments"]} == {first["id"], second["id"]}
    assert me.json()["certificates"] == []


# ----------------------------------------------------------------------------
# Certificate
# ----------------------------------------------------------------------------
async def test_certificate_is_linked_and_freezes_the_enrollment(client, factory, world, issuer, r2):
    member, director, instructor = world["member"], world["director"], world["instructor"]
    honor = await _honor(factory, "certificada", theoretical=(True, True))
    enrollment = await _enroll(client, member, honor)
    eid = enrollment["id"]
    issue_url = f"{ENROLLMENTS}/{eid}/certificate"
    body = {"template": "especialidad-basica", "issued_date": "2026-09-22", "place": "Reynosa",
            "instructor_name": "Instructora Uno"}

    assert (await client.post(issue_url, json=body, headers=director["headers"])).status_code == 409     # not READY
    await _complete_all(client, member, director, enrollment)
    assert (await client.post(issue_url, json=body, headers=instructor["headers"])).status_code == 403   # reviews, never issues
    assert (await client.post(issue_url, json=body, headers=member["headers"])).status_code == 403
    assert (await client.post(issue_url, json={**body, "template": "no-existe"}, headers=director["headers"])).status_code == 404
    assert (await client.post(issue_url, json={"place": "x"}, headers=director["headers"])).status_code == 422

    issued = await client.post(issue_url, json=body, headers=director["headers"])
    assert issued.status_code == 201, issued.text
    certificate = issued.json()
    assert certificate["certificate_no"].startswith("CC-") and len(certificate["certificate_hash"]) == 64
    assert certificate["recipient_name"] == factory.name("member") and certificate["honor_name_snapshot"] == honor["name"]
    assert certificate["club_name_snapshot"] == factory.name("club-a") and certificate["template"] == "especialidad-basica"
    assert (certificate["user_id"], certificate["enrollment_id"], certificate["issued_by_id"], certificate["issued_role"]) == (
        member["id"], eid, director["id"], "CLUB_DIRECTOR")
    assert certificate["director_name"] == factory.name("director-a") and certificate["issued_date"] == "2026-09-22"

    row = await fetch_one(
        "SELECT c.user_id::text AS user_id, c.enrollment_id::text AS enrollment_id, c.issued_by_id::text AS issued_by_id,"
        " c.issued_role, c.status, o.code FROM certificates c JOIN organizations o ON o.id = c.organization_id"
        " WHERE c.certificate_no = :no", no=certificate["certificate_no"])
    assert dict(row) == {"user_id": member["id"], "enrollment_id": eid, "issued_by_id": director["id"],
                         "issued_role": "CLUB_DIRECTOR", "status": "issued", "code": world["issuer_code"]}
    assert await fetch_one("SELECT 1 AS x FROM certificate_events WHERE certificate_id = :id AND event_type = 'issued'",
                           id=uuid.UUID(certificate["id"]))
    verified = await client.get(f"/api/v1/certificates/verify/{certificate['certificate_no']}")
    assert verified.status_code == 200 and verified.json()["valid"] is True               # same hash, same verifier
    assert await _audit_actions(certificate["id"]) == ["CERTIFICATE_ISSUE"]

    frozen = (await _get(client, member, eid)).json()
    assert frozen["status"] == "CERTIFIED" and frozen["certified_at"]
    assert frozen["certificate"]["certificate_no"] == certificate["certificate_no"]
    assert frozen["club"]["id"] == world["club_a"]["id"]
    # rule 3: nothing moves after certification
    assert (await client.post(issue_url, json=body, headers=director["headers"])).status_code == 409
    assert (await _submit(client, member, eid, 1)).status_code == 409
    assert (await _review(client, director, eid, 1, "INCOMPLETE", "tarde")).status_code == 409
    assert (await client.post(f"{ENROLLMENTS}/{eid}/requirements/1/evidences",
                              json={"content_type": "image/png", "size_bytes": 10}, headers=member["headers"])).status_code == 409
    assert (await client.delete(f"{ENROLLMENTS}/{eid}", headers=member["headers"])).status_code == 409
    again = await client.post(ENROLLMENTS, json={"honor_id": honor["id"]}, headers=member["headers"])
    assert again.status_code == 200 and again.json()["id"] == eid                          # certified still counts as enrolled

    for viewer in ("member", "guardian", "director"):
        portfolio = await client.get(f"{PORTFOLIO}/users/{member['id']}", headers=world[viewer]["headers"])
        assert portfolio.status_code == 200, viewer
        numbers = [c["certificate_no"] for c in portfolio.json()["certificates"]]
        assert certificate["certificate_no"] in numbers
    mine = (await client.get(f"{PORTFOLIO}/me", headers=member["headers"])).json()
    listed = next(c for c in mine["certificates"] if c["certificate_no"] == certificate["certificate_no"])
    assert listed["enrollment_id"] == eid and listed["template"] == "especialidad-basica"


async def test_prototype_batch_contract_is_unchanged(client, factory, issuer, world):
    payload = {"recipient_names": [factory.name("Ana"), " ", factory.name("Luis")], "honor_name": factory.name("Prototipo"),
               "club_name": factory.name("club-prototipo"), "issued_date": "2026-09-22", "place": "Reynosa",
               "instructor_name": "I", "director_name": "D", "width_in": 11, "height_in": 8.5}
    response = await client.post("/api/v1/certificates/prototype-batch", json=payload)      # still no session required
    assert response.status_code == 201, response.text
    rows = response.json()
    assert [row["recipient_name"] for row in rows] == [factory.name("Ana"), factory.name("Luis")]   # blanks are skipped
    assert all(set(row) == {"id", "certificate_no", "recipient_name", "honor_name_snapshot", "club_name_snapshot",
                            "issued_date", "status", "certificate_hash"} for row in rows)
    assert rows[0]["honor_name_snapshot"] == factory.name("Prototipo") and rows[0]["status"] == "issued"
    assert rows[0]["club_name_snapshot"] == factory.name("club-prototipo") and rows[0]["issued_date"] == "2026-09-22"

    verified = await client.get(f"/api/v1/certificates/verify/{rows[0]['certificate_no']}")
    assert verified.json()["valid"] is True and verified.json()["hash_short"] == rows[0]["certificate_hash"][:12]
    stored = await fetch_one(
        "SELECT user_id, enrollment_id, issued_by_id, issued_role, director_name FROM certificates WHERE certificate_no = :no",
        no=rows[0]["certificate_no"])
    assert dict(stored) == {"user_id": None, "enrollment_id": None, "issued_by_id": None, "issued_role": None,
                            "director_name": "D"}                                          # old flow stays unlinked
    events = await fetch_all("SELECT event_type, actor_id, metadata_json FROM certificate_events WHERE certificate_id = :id",
                             id=uuid.UUID(rows[0]["id"]))
    assert [(e["event_type"], e["actor_id"], e["metadata_json"]) for e in events] == [
        ("issued", None, {"hash": rows[0]["certificate_hash"]})]

    second = await client.post("/api/v1/certificates/prototype-batch", json=payload)        # club, honor, template reused
    assert second.status_code == 201
    counts = await fetch_one(
        "SELECT (SELECT count(*) FROM clubs WHERE name = :club) AS clubs, (SELECT count(*) FROM honors WHERE name = :honor) AS honors",
        club=factory.name("club-prototipo"), honor=factory.name("Prototipo"))
    assert (counts["clubs"], counts["honors"]) == (1, 1)
    assert (await client.post("/api/v1/certificates/prototype-batch",
                              json={**payload, "ministry": "no-existe"})).status_code == 404


# ----------------------------------------------------------------------------
# migrations/purge_removed_evidence.py
# ----------------------------------------------------------------------------
async def test_purge_script_only_deletes_old_removed_and_unfinished_uploads(client, factory, world, r2):
    import asyncio
    import importlib.util
    import pathlib

    psycopg = pytest.importorskip("psycopg")
    from tests.conftest import TEST_DATABASE_URL

    path = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "purge_removed_evidence.py"
    spec = importlib.util.spec_from_file_location("purge_removed_evidence", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    member = world["member"]
    enrollment = await _enroll(client, member, await _honor(factory, "purga", theoretical=(False,)))
    eid = enrollment["id"]
    keys = {}
    for label in ("active", "removed_old", "removed_recent", "pending_old"):
        evidence = await _add_evidence(client, r2, member, eid, 1, size=64)
        keys[label] = (evidence["id"], r2.presigned[-1]["Key"])
    for label in ("removed_old", "removed_recent"):
        assert (await client.delete(f"{EVIDENCES}/{keys[label][0]}", headers=member["headers"])).status_code == 204
    async with SessionLocal() as db:
        await db.execute(text("UPDATE evidences SET removed_at = now() - interval '31 days' WHERE id = :id"),
                         {"id": uuid.UUID(keys["removed_old"][0])})
        await db.execute(text("UPDATE evidences SET status = 'PENDING_UPLOAD', created_at = now() - interval '40 days'"
                              " WHERE id = :id"), {"id": uuid.UUID(keys["pending_old"][0])})
        await db.commit()

    def run(commit: bool) -> dict:
        with psycopg.connect(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")) as conn:
            return script.purge(conn, r2, "evidence-test", days=30, commit=commit)

    mine = lambda report: sorted(k for k in report["keys"] if f"/{eid}/" in k)   # noqa: E731
    expected = sorted([keys["removed_old"][1], keys["pending_old"][1]])
    dry = await asyncio.to_thread(run, False)
    assert mine(dry) == expected and dry["purged"] == 0
    assert all(key in r2.objects for _, key in keys.values())                     # a dry run deletes nothing

    done = await asyncio.to_thread(run, True)
    assert done["purged"] >= 2 and done["failed"] == []
    assert {label: key in r2.objects for label, (_, key) in keys.items()} == {
        "active": True, "removed_old": False, "removed_recent": True, "pending_old": False}
    rows = await fetch_all("SELECT status, purged_at FROM evidences WHERE id = ANY(:ids)",
                           ids=[uuid.UUID(keys["removed_old"][0]), uuid.UUID(keys["pending_old"][0])])
    assert all(row["status"] == "REMOVED" and row["purged_at"] for row in rows)
    assert mine(await asyncio.to_thread(run, False)) == []                        # idempotent


async def test_drafts_save_without_sending_and_empty_requirements_cannot_be_sent(client, factory, world, r2):
    """The answer is typed in the card and saves itself; sending needs something a reviewer can judge."""
    honor = await _honor(factory, "borrador-envio", theoretical=(True, False))
    eid = (await _enroll(client, world["member"], honor))["id"]
    member = world["member"]["headers"]
    url = lambda position: f"{PORTFOLIO}/enrollments/{eid}/requirements/{position}"

    # status omitted = draft only: the note is kept, nothing is sent, nothing reaches the review queue
    draft = await client.put(url(1), headers=member, json={"member_note": "  Un amarre une dos palos.  "})
    assert draft.status_code == 200, draft.text
    first = _requirement(draft.json(), 1)
    assert first["status"] == "PENDING" and first["member_note"] == "Un amarre une dos palos."

    # an answer requirement needs text or evidence; a practical one needs evidence, text is not enough
    empty = await client.put(url(2), headers=member, json={"status": "SUBMITTED"})
    assert empty.status_code == 422 and "evidencia" in empty.json()["detail"]
    text_only = await client.put(url(2), headers=member, json={"status": "SUBMITTED", "member_note": "Lo hice"})
    assert text_only.status_code == 422 and "pide evidencia" in text_only.json()["detail"]
    assert _requirement((await _get(client, world["member"], eid)).json(), 2)["status"] == "PENDING"

    blank_answer = await client.put(url(1), headers=member, json={"status": "SUBMITTED", "member_note": "   "})
    assert blank_answer.status_code == 422 and "respuesta" in blank_answer.json()["detail"]
    # a refused request changes nothing: the saved draft is still there and is what gets sent
    sent = await client.put(url(1), headers=member, json={"status": "SUBMITTED"})
    assert sent.status_code == 200
    first = _requirement(sent.json(), 1)
    assert first["status"] == "SUBMITTED" and first["member_note"] == "Un amarre une dos palos."

    await _add_evidence(client, r2, world["member"], eid, 2)
    with_photo = await client.put(url(2), headers=member, json={"status": "SUBMITTED"})
    assert with_photo.status_code == 200 and _requirement(with_photo.json(), 2)["status"] == "SUBMITTED"

    # a sent requirement is not edited behind the reviewer's back; after INCOMPLETE the draft opens again
    locked = await client.put(url(1), headers=member, json={"member_note": "otra cosa"})
    assert locked.status_code == 409
    assert (await _review(client, world["director"], eid, 1, "INCOMPLETE", "Falta el propósito")).status_code == 200
    reopened = await client.put(url(1), headers=member, json={"member_note": "Une dos palos para construir."})
    assert reopened.status_code == 200
    again = _requirement(reopened.json(), 1)
    assert again["status"] == "INCOMPLETE" and again["member_note"] == "Une dos palos para construir."
