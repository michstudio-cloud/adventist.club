"""Álbum de evidencias: one folder per honor the member is working on or earned, with the
patch, the counters and the four newest photos; and the whole evidence of one enrollment.

Read-only views over block A: no mutation, no audit row. The private bucket is the same
in-memory fake as in test_portfolio.py.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event, text

from app.config import settings
from app.db import SessionLocal, engine
from app.models import User
from app.services import evidence_albums, private_storage
from tests.conftest import fetch_one, module_factory, requires_db
from tests.test_portfolio import (
    ENROLLMENTS,
    EVIDENCES,
    PORTFOLIO,
    FakePrivateR2,
    _add_evidence,
    _enroll,
    _honor,
    _portfolio_tables_exist,
    _review,
    _submit,
)

ALBUMS = f"{PORTFOLIO}/albums"

pytestmark = [
    requires_db,
    pytest.mark.skipif(not _portfolio_tables_exist(), reason="portfolio tables are not in the test database"),
]
factory = module_factory("albums")


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club_a = await factory.org("club-a", "club", association)
    club_b = await factory.org("club-b", "club", association)
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    people = {
        "member": await factory.user("member", "STUDENT", club_a["id"], is_minor=True),
        "director": await factory.user("director-a", "CLUB_DIRECTOR", club_a["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", club_b["id"]),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": issuer_code, "id": uuid.UUID(issuer["id"])})
        await db.execute(text(
            "INSERT INTO guardianships (guardian_id, child_id, consent_status) VALUES (:g, :c, 'APPROVED')"),
            {"g": uuid.UUID(people["guardian"]["id"]), "c": uuid.UUID(people["member"]["id"])})
        await db.commit()
    return {**people, "club_a": club_a, "issuer_code": issuer_code}


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


@pytest.fixture
def r2(monkeypatch):
    fake = FakePrivateR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", "evidence-test")
    monkeypatch.setattr(private_storage, "get_client", lambda: fake)
    return fake


async def _categorised(factory, honor: dict, label: str) -> dict:
    category_id = uuid.uuid4()
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honor_categories (id, ministry_id, name, slug) VALUES (:id, :m, :name, :slug)"),
            {"id": category_id, "m": ministry, "name": factory.name(label), "slug": f"{factory.prefix}-{label}"})
        await db.execute(text("UPDATE honors SET category_id = :c, image_url = :img WHERE id = :h"),
                         {"c": category_id, "h": uuid.UUID(honor["id"]), "img": f"https://cdn.test/{label}.png"})
        await db.commit()
    return {"id": str(category_id), "name": factory.name(label), "slug": f"{factory.prefix}-{label}"}


async def _albums(client, viewer, user_id=None):
    params = {"user_id": user_id} if user_id else None
    return await client.get(ALBUMS, params=params, headers=viewer["headers"] if viewer else None)


def _signed_ok(preview: dict) -> None:
    assert preview["url"].startswith("https://r2.test/evidence-test/evidence/"), preview["url"]
    assert "op=get_object" in preview["url"] and "X-Amz-Expires=900" in preview["url"]
    assert preview["thumbnail_url"] == preview["url"]


# ----------------------------------------------------------------------------
# GET /portfolio/albums
# ----------------------------------------------------------------------------
async def test_albums_need_a_session(client):
    assert (await client.get(ALBUMS)).status_code == 401
    assert (await client.get(f"{ENROLLMENTS}/{uuid.uuid4()}/evidence")).status_code == 401


async def test_my_albums_counts_latest_four_and_order(client, factory, world, r2):
    member, director = world["member"], world["director"]
    knots = await _honor(factory, "nudos", theoretical=(False, False, True))
    category = await _categorised(factory, knots, "cat-nudos")
    stars = await _honor(factory, "estrellas", theoretical=(False,))
    withdrawn = await _honor(factory, "retirada", theoretical=(False,))

    knots_enrollment = await _enroll(client, member, knots)
    kid = knots_enrollment["id"]
    uploaded = []
    for position, content_type in ((1, "image/jpeg"), (1, "image/png"), (2, "application/pdf"),
                                   (1, "image/webp"), (2, "image/jpeg"), (2, "image/png")):
        uploaded.append((position, await _add_evidence(client, r2, member, kid, position, content_type=content_type,
                                                       caption=f"foto {len(uploaded) + 1}")))
    # removed evidence and an upload never confirmed are not in the album
    assert (await client.delete(f"{EVIDENCES}/{uploaded[-1][1]['id']}", headers=member["headers"])).status_code == 204
    pending = await client.post(f"{ENROLLMENTS}/{kid}/requirements/1/evidences",
                                json={"content_type": "image/png", "size_bytes": 10}, headers=member["headers"])
    assert pending.status_code == 201
    # one requirement done
    assert (await _submit(client, member, kid, 3)).status_code == 200
    assert (await _review(client, director, kid, 3)).status_code == 200

    stars_enrollment = await _enroll(client, member, stars)
    star = await _add_evidence(client, r2, member, stars_enrollment["id"], 1)
    gone = await _enroll(client, member, withdrawn)
    assert (await client.delete(f"{ENROLLMENTS}/{gone['id']}", headers=member["headers"])).status_code == 204

    response = await _albums(client, member)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    albums = response.json()
    assert [a["enrollment_id"] for a in albums] == [stars_enrollment["id"], kid]   # updated_at desc, no withdrawn

    first = albums[1]
    assert first["honor"] == {"id": knots["id"], "name": knots["name"], "slug": f"{factory.prefix}-nudos",
                              "image_url": "https://cdn.test/cat-nudos.png", "category": category}
    assert first["status"] == "IN_PROGRESS" and first["certificate"] is None and first["updated_at"]
    assert (first["requirements_total"], first["requirements_done"], first["evidence_count"]) == (3, 1, 5)
    active = [(position, e) for position, e in uploaded[:-1]]
    expected = list(reversed(active))[:4]                                  # the four newest, newest first
    assert [p["id"] for p in first["latest"]] == [e["id"] for _, e in expected]
    assert [p["requirement_position"] for p in first["latest"]] == [position for position, _ in expected]
    assert [p["content_type"] for p in first["latest"]] == [e["content_type"] for _, e in expected]
    assert all(p["requirement_id"] and p["created_at"] for p in first["latest"])
    for preview in first["latest"]:
        _signed_ok(preview)
    assert r2.presigned[-1]["operation"] == "get_object" and r2.presigned[-1]["expires"] == 900
    assert r2.presigned[-1]["Bucket"] == "evidence-test"
    assert "storage_key" not in first["latest"][0]

    second = albums[0]
    assert second["honor"]["category"] is None and second["honor"]["image_url"] is None
    assert (second["requirements_total"], second["requirements_done"], second["evidence_count"]) == (1, 0, 1)
    assert [p["id"] for p in second["latest"]] == [star["id"]]

    # a new photo in the knots album moves it back to the top
    await _add_evidence(client, r2, member, kid, 1)
    assert (await _albums(client, member)).json()[0]["enrollment_id"] == kid


async def test_certified_album_carries_its_certificate(client, factory, world, issuer, r2, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_WEB_URL", "https://club.test/")
    learner = await factory.user("learner", "STUDENT", world["club_a"]["id"])
    honor = await _honor(factory, "certificada", theoretical=(False,))
    enrollment = await _enroll(client, learner, honor)
    photo = await _add_evidence(client, r2, learner, enrollment["id"], 1)
    assert (await _submit(client, learner, enrollment["id"], 1)).status_code == 200
    assert (await _review(client, world["director"], enrollment["id"], 1)).status_code == 200
    issued = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/certificate",
                               json={"issued_date": "2026-09-22"}, headers=world["director"]["headers"])
    assert issued.status_code == 201, issued.text
    number = issued.json()["certificate_no"]

    [album] = (await _albums(client, learner)).json()
    assert album["status"] == "CERTIFIED"
    assert (album["requirements_total"], album["requirements_done"], album["evidence_count"]) == (1, 1, 1)
    assert [p["id"] for p in album["latest"]] == [photo["id"]]
    assert album["certificate"] == {"certificate_no": number, "issued_date": "2026-09-22",
                                    "verify_url": f"https://club.test/verify/{number}", "status": "issued"}


async def test_who_sees_someone_elses_albums(client, factory, world, r2):
    member = world["member"]
    honor = await _honor(factory, "visible", theoretical=(False,))
    enrollment = await _enroll(client, member, honor)
    await _add_evidence(client, r2, member, enrollment["id"], 1)

    for viewer in ("director", "guardian", "member"):
        seen = await _albums(client, world[viewer], member["id"])
        assert seen.status_code == 200, (viewer, seen.text)
        album = next(a for a in seen.json() if a["enrollment_id"] == enrollment["id"])
        _signed_ok(album["latest"][0])
    # never a 403: whether the person exists is none of a stranger's business
    for viewer in ("director_b", "stranger"):
        assert (await _albums(client, world[viewer], member["id"])).status_code == 404, viewer
    assert (await _albums(client, member, str(uuid.uuid4()))).status_code == 404
    assert (await _albums(client, member, "not-a-uuid")).status_code == 422
    # the stranger's own (empty) album list
    assert (await _albums(client, world["stranger"])).json() == []


async def test_albums_without_the_private_bucket(client, factory, world):
    assert settings.private_storage_configured is False
    learner = await factory.user("sin-bucket", "STUDENT", world["club_a"]["id"])
    enrollment = await _enroll(client, learner, await _honor(factory, "sin-bucket", theoretical=(True,)))
    [album] = (await _albums(client, learner)).json()                    # no evidence: nothing to sign
    assert album["enrollment_id"] == enrollment["id"] and album["latest"] == [] and album["evidence_count"] == 0


async def test_albums_query_budget(client, factory, world, r2):
    learner = await factory.user("presupuesto", "STUDENT", world["club_a"]["id"])
    for label in ("q-uno", "q-dos", "q-tres"):
        enrollment = await _enroll(client, learner, await _honor(factory, label, theoretical=(False, False),
                                                                  english=True), locale="en")
        for position in (1, 2, 1):
            await _add_evidence(client, r2, learner, enrollment["id"], position)

    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    async with SessionLocal() as db:
        user = await db.get(User, uuid.UUID(learner["id"]))
        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            albums = await evidence_albums.albums_of(db, user, None)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert len(albums) == 3 and all(len(a.latest) == 3 for a in albums)
    assert all(a.honor.name.endswith("(EN)") for a in albums)        # named in the enrollment's language
    assert len(statements) <= 4, statements


# ----------------------------------------------------------------------------
# GET /portfolio/enrollments/{id}/evidence
# ----------------------------------------------------------------------------
async def test_all_evidence_of_one_enrollment(client, factory, world, r2):
    member, director = world["member"], world["director"]
    honor = await _honor(factory, "todas", theoretical=(False, False))
    enrollment = await _enroll(client, member, honor)
    eid = enrollment["id"]
    second = await _add_evidence(client, r2, member, eid, 2, caption="Fogata")
    first_a = await _add_evidence(client, r2, member, eid, 1, content_type="application/pdf", caption=" ")
    first_b = await _add_evidence(client, r2, member, eid, 1, caption="Segunda")
    removed = await _add_evidence(client, r2, member, eid, 1)
    assert (await client.delete(f"{EVIDENCES}/{removed['id']}", headers=member["headers"])).status_code == 204
    assert (await _submit(client, member, eid, 2)).status_code == 200
    assert (await _review(client, director, eid, 2, "INCOMPLETE", "Otra foto")).status_code == 200

    response = await client.get(f"{ENROLLMENTS}/{eid}/evidence", headers=member["headers"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    items = response.json()
    assert [i["id"] for i in items] == [first_a["id"], first_b["id"], second["id"]]   # position, then created_at
    assert [(i["requirement_position"], i["requirement_description"], i["status"], i["note"]) for i in items] == [
        (1, "ES requisito 1", "PENDING", None), (1, "ES requisito 1", "PENDING", "Segunda"),
        (2, "ES requisito 2", "INCOMPLETE", "Fogata")]
    assert items[0]["content_type"] == "application/pdf" and all(i["requirement_id"] and i["created_at"] for i in items)
    for item in items:
        _signed_ok(item)

    # same rule as the enrollment detail
    for viewer, expected in (("director", 200), ("guardian", 200), ("director_b", 403), ("stranger", 403)):
        seen = await client.get(f"{ENROLLMENTS}/{eid}/evidence", headers=world[viewer]["headers"])
        detail = await client.get(f"{ENROLLMENTS}/{eid}", headers=world[viewer]["headers"])
        assert seen.status_code == detail.status_code == expected, viewer
    assert (await client.get(f"{ENROLLMENTS}/{uuid.uuid4()}/evidence", headers=member["headers"])).status_code == 404


async def test_reading_albums_writes_nothing(client, factory, world, r2):
    learner = await factory.user("solo-lectura", "STUDENT", world["club_a"]["id"])
    enrollment = await _enroll(client, learner, await _honor(factory, "lectura", theoretical=(False,)))
    await _add_evidence(client, r2, learner, enrollment["id"], 1)
    before = await fetch_one("SELECT updated_at FROM honor_enrollments WHERE id = :id", id=uuid.UUID(enrollment["id"]))
    audit = await fetch_one("SELECT count(*) AS n FROM audit_log WHERE user_id = :u", u=uuid.UUID(learner["id"]))
    await _albums(client, learner)
    await client.get(f"{ENROLLMENTS}/{enrollment['id']}/evidence", headers=learner["headers"])
    after = await fetch_one("SELECT updated_at FROM honor_enrollments WHERE id = :id", id=uuid.UUID(enrollment["id"]))
    assert before["updated_at"] == after["updated_at"]
    assert (await fetch_one("SELECT count(*) AS n FROM audit_log WHERE user_id = :u",
                            u=uuid.UUID(learner["id"])))["n"] == audit["n"]
