"""Bloque B · I1 — Verificación del instructor con la carta de la iglesia.

Subida al bucket PRIVADO (nunca al público), doble escalón zona -> Asociación, alcance
por el árbol de organizaciones y la puerta única `instructor_is_verified`.

El bucket privado es siempre un doble: ningún test sale a la red.
"""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import private_storage
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

LETTERS = "/api/v1/church-letters"
MB = 1024 * 1024


def _letters_table_exists() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(await db.scalar(text("SELECT to_regclass('public.church_letters') IS NOT NULL")))
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _letters_table_exists(),
        reason="apply migrations/009_church_letters.sql to the test database",
    ),
]
factory = module_factory("letters")


# ----------------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def world(factory):
    """One association with a zone and a club, plus a second association that sees nothing."""
    association = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", association)
    club = await factory.org("club", "club", association)
    other_association = await factory.org("other-assoc", "association")
    people = {
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "instructor2": await factory.user("instructor2", "INSTRUCTOR", club["id"]),
        "orphan": await factory.user("orphan", "INSTRUCTOR"),
        "minor": await factory.user("minor-instructor", "INSTRUCTOR", club["id"], is_minor=True),
        "zone_coordinator": await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"]),
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "other_admin": await factory.user("other-admin", "ADMIN_ASSOCIATION", other_association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "student": await factory.user("student", "STUDENT", club["id"]),
    }
    async with SessionLocal() as db:
        # Every instructor has a confirmed e-mail and the child protection course: the tests
        # that need one of those missing take it away explicitly.
        for label in ("instructor", "instructor2", "orphan", "minor"):
            await db.execute(
                text(
                    "UPDATE users SET verification_status = 'VERIFIED',"
                    " child_protection_completed = true WHERE id = :id"
                ),
                {"id": uuid.UUID(people[label]["id"])},
            )
        await db.commit()
    return {**people, "association": association, "zone": zone, "club": club}


class FakePrivateR2:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.presigned: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presigned.append({"operation": operation, "expires": ExpiresIn, **Params})
        return f"https://r2.test/{Params['Bucket']}/{Params['Key']}?op={operation}"

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
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
    monkeypatch.setattr(settings, "R2_PRIVATE_BUCKET_NAME", "letters-test")
    monkeypatch.setattr(private_storage, "get_client", lambda: fake)
    return fake


async def _create(client, user, **extra):
    body = {"church_name": "Iglesia Central", "content_type": "application/pdf", "size_bytes": 4096}
    return await client.post(LETTERS, json={**body, **extra}, headers=user["headers"])


async def _submitted(client, r2, user, **extra) -> dict:
    """Create, "upload" to the fake bucket and confirm: a letter in SUBMITTED."""
    created = await _create(client, user, **extra)
    assert created.status_code == 201, created.text
    letter = created.json()["letter"]
    r2.store_last_upload(extra.get("size_bytes", 4096), extra.get("content_type", "application/pdf"))
    done = await client.post(f"{LETTERS}/{letter['id']}/complete", headers=user["headers"])
    assert done.status_code == 200, done.text
    return done.json()


async def _review(client, reviewer, letter_id, action, **extra):
    return await client.post(
        f"{LETTERS}/{letter_id}/review", json={"action": action, **extra}, headers=reviewer["headers"]
    )


async def _authorized(client, r2, world, user, **extra) -> dict:
    letter = await _submitted(client, r2, user, **extra)
    assert (await _review(client, world["zone_coordinator"], letter["id"], "VALIDATE")).status_code == 200
    done = await _review(client, world["assoc_admin"], letter["id"], "AUTHORIZE")
    assert done.status_code == 200, done.text
    return done.json()


async def _me(client, user):
    return await client.get(f"{LETTERS}/me", headers=user["headers"])


async def _drop_letters(user_id: str) -> None:
    """Back to a clean slate between tests that each need their own live letter."""
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM church_letters WHERE user_id = :id"), {"id": uuid.UUID(user_id)})
        await db.commit()


@pytest_asyncio.fixture
async def clean(world):
    yield
    for label in ("instructor", "instructor2", "orphan", "minor", "zone_coordinator"):
        await _drop_letters(world[label]["id"])


async def _audit_actions(entity_id) -> list[str]:
    rows = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_id = :id ORDER BY created_at", id=str(entity_id)
    )
    return [row["action"] for row in rows]


# ----------------------------------------------------------------------------
# Upload handshake
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_returns_a_presigned_put_into_the_private_bucket(client, world, r2, clean):
    response = await _create(client, world["instructor"])
    assert response.status_code == 201, response.text
    body = response.json()
    letter = body["letter"]
    assert letter["status"] == "PENDING_UPLOAD"
    assert letter["church_name"] == "Iglesia Central"
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"]["Content-Type"] == "application/pdf"

    signed = r2.presigned[-1]
    assert signed["operation"] == "put_object"
    assert signed["Bucket"] == "letters-test"  # the PRIVATE bucket, never media.adventist.club
    assert signed["Key"] == f"letters/{world['instructor']['id']}/{letter['id']}.pdf"


@pytest.mark.asyncio
async def test_create_without_private_storage_answers_503(client, world, clean):
    response = await _create(client, world["instructor"])
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_only_an_adult_instructor_presents_a_letter(client, world, r2, clean):
    assert (await _create(client, world["student"])).status_code == 403
    assert (await _create(client, world["minor"])).status_code == 403


@pytest.mark.asyncio
async def test_an_instructor_without_organization_is_told_to_pick_one(client, world, r2, clean):
    response = await _create(client, world["orphan"])
    assert response.status_code == 409
    assert "Asociación" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oversized_or_unknown_types_are_refused(client, world, r2, clean):
    assert (await _create(client, world["instructor"], size_bytes=11 * MB)).status_code == 413
    assert (await _create(client, world["instructor"], content_type="text/html")).status_code == 422


@pytest.mark.asyncio
async def test_complete_checks_the_object_in_the_bucket(client, world, r2, clean):
    created = await _create(client, world["instructor"])
    letter_id = created.json()["letter"]["id"]
    headers = world["instructor"]["headers"]

    nothing_uploaded = await client.post(f"{LETTERS}/{letter_id}/complete", headers=headers)
    assert nothing_uploaded.status_code == 409

    r2.store_last_upload(999, "application/pdf")  # a different size than declared
    mismatch = await client.post(f"{LETTERS}/{letter_id}/complete", headers=headers)
    assert mismatch.status_code == 409

    r2.store_last_upload(4096, "application/pdf")
    done = await client.post(f"{LETTERS}/{letter_id}/complete", headers=headers)
    assert done.status_code == 200
    assert done.json()["status"] == "SUBMITTED"
    assert "LETTER_SUBMIT" in await _audit_actions(letter_id)


@pytest.mark.asyncio
async def test_only_the_owner_completes_and_only_one_live_letter_per_person(client, world, r2, clean):
    created = await _create(client, world["instructor"])
    letter_id = created.json()["letter"]["id"]
    r2.store_last_upload(4096, "application/pdf")
    assert (await client.post(f"{LETTERS}/{letter_id}/complete",
                              headers=world["instructor2"]["headers"])).status_code == 403
    assert (await client.post(f"{LETTERS}/{letter_id}/complete",
                              headers=world["instructor"]["headers"])).status_code == 200

    again = await _create(client, world["instructor"])
    assert again.status_code == 409


# ----------------------------------------------------------------------------
# Reading the letter: owner and reviewers in scope, nobody else
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_signed_read_url_is_for_the_owner_and_reviewers_in_scope(client, world, r2, clean):
    letter = await _submitted(client, r2, world["instructor"])
    url = f"{LETTERS}/{letter['id']}/url"

    for who in ("instructor", "zone_coordinator", "assoc_admin", "master"):
        allowed = await client.get(url, headers=world[who]["headers"])
        assert allowed.status_code == 200, f"{who}: {allowed.text}"
        assert allowed.json()["expires_in"] == 300
        assert allowed.headers["cache-control"] == "no-store"

    for who in ("instructor2", "student", "other_admin"):
        assert (await client.get(url, headers=world[who]["headers"])).status_code == 403
    assert (await client.get(url)).status_code == 401


# ----------------------------------------------------------------------------
# The double step: zone validates, the association authorizes
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zone_validates_then_association_authorizes(client, world, r2, clean):
    letter = await _submitted(client, r2, world["instructor"])

    too_early = await _review(client, world["assoc_admin"], letter["id"], "AUTHORIZE")
    assert too_early.status_code == 409  # it has not been validated by the zone yet

    validated = await _review(client, world["zone_coordinator"], letter["id"], "VALIDATE")
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "ZONE_VALIDATED"

    out_of_stage = await _review(client, world["zone_coordinator"], letter["id"], "AUTHORIZE")
    assert out_of_stage.status_code == 403  # the zone does not authorize

    valid_until = (date.today() + timedelta(days=365)).isoformat()
    authorized = await _review(client, world["assoc_admin"], letter["id"], "AUTHORIZE", valid_until=valid_until)
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["status"] == "AUTHORIZED"
    assert authorized.json()["valid_until"] == valid_until

    row = await fetch_one("SELECT * FROM church_letters WHERE id = :id", id=uuid.UUID(letter["id"]))
    assert str(row["zone_validated_by_id"]) == world["zone_coordinator"]["id"]
    assert str(row["decided_by_id"]) == world["assoc_admin"]["id"]
    assert await _audit_actions(letter["id"]) == ["LETTER_SUBMIT", "LETTER_VALIDATE", "LETTER_AUTHORIZE"]


@pytest.mark.asyncio
async def test_an_association_reviewer_may_also_give_the_zone_step(client, world, r2, clean):
    letter = await _submitted(client, r2, world["instructor"])
    validated = await _review(client, world["assoc_admin"], letter["id"], "VALIDATE")
    assert validated.status_code == 200, validated.text
    assert (await _review(client, world["assoc_admin"], letter["id"], "AUTHORIZE")).status_code == 200


@pytest.mark.asyncio
async def test_a_reviewer_outside_the_scope_cannot_decide(client, world, r2, clean):
    letter = await _submitted(client, r2, world["instructor"])
    assert (await _review(client, world["other_admin"], letter["id"], "VALIDATE")).status_code == 403
    assert (await _review(client, world["student"], letter["id"], "VALIDATE")).status_code == 403


@pytest.mark.asyncio
async def test_nobody_validates_their_own_letter(client, world, r2, clean):
    """Unreachable through POST today (only instructors present letters) but the rule is the
    gate for the roles bloque E will add, so it is enforced and tested at the service."""
    letter_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO church_letters (id, user_id, organization_id, church_name, storage_key,"
                " content_type, size_bytes, status) VALUES (:id, :user, :org, 'Iglesia', :key,"
                " 'application/pdf', 4096, 'SUBMITTED')"
            ),
            {
                "id": letter_id,
                "user": uuid.UUID(world["zone_coordinator"]["id"]),
                "org": uuid.UUID(world["zone"]["id"]),
                "key": f"letters/{world['zone_coordinator']['id']}/{letter_id}.pdf",
            },
        )
        await db.commit()
    assert (await _review(client, world["zone_coordinator"], str(letter_id), "VALIDATE")).status_code == 403
    assert (await _review(client, world["assoc_admin"], str(letter_id), "VALIDATE")).status_code == 200


@pytest.mark.asyncio
async def test_reject_needs_a_note_and_lets_the_instructor_try_again(client, world, r2, clean):
    letter = await _submitted(client, r2, world["instructor"])
    assert (await _review(client, world["zone_coordinator"], letter["id"], "REJECT")).status_code == 422

    rejected = await _review(
        client, world["zone_coordinator"], letter["id"], "REJECT", note="La firma no se lee"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["decision_note"] == "La firma no se lee"
    assert (await _review(client, world["zone_coordinator"], letter["id"], "VALIDATE")).status_code == 409

    # A rejected letter is not a live one: the instructor presents a new one.
    assert (await _create(client, world["instructor"])).status_code == 201
    assert "LETTER_REJECT" in await _audit_actions(letter["id"])


@pytest.mark.asyncio
async def test_revoke_needs_a_note_and_takes_the_authorization_away_at_once(client, world, r2, clean):
    letter = await _authorized(client, r2, world, world["instructor"])
    assert (await _me(client, world["instructor"])).json()["verified"] is True

    # The zone does not revoke what the association authorized, with or without a reason.
    assert (await _review(client, world["zone_coordinator"], letter["id"], "REVOKE",
                          note="Ya no")).status_code == 403
    assert (await _review(client, world["assoc_admin"], letter["id"], "REVOKE")).status_code == 422

    revoked = await _review(client, world["assoc_admin"], letter["id"], "REVOKE", note="Cambió de iglesia")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    assert (await _me(client, world["instructor"])).json()["verified"] is False
    assert "LETTER_REVOKE" in await _audit_actions(letter["id"])


# ----------------------------------------------------------------------------
# The validation queue
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_queue_shows_only_letters_in_scope_and_at_the_right_stage(client, world, r2, clean):
    letter = await _submitted(client, r2, world["instructor"])

    def ids(response):
        return [row["id"] for row in response.json()]

    zone_queue = await client.get(f"{LETTERS}/queue", headers=world["zone_coordinator"]["headers"])
    assert zone_queue.status_code == 200, zone_queue.text
    assert letter["id"] in ids(zone_queue)
    entry = next(row for row in zone_queue.json() if row["id"] == letter["id"])
    # The reviewer needs who and which church, and nothing else of the account.
    assert entry["user"]["id"] == world["instructor"]["id"]
    assert entry["church_name"] == "Iglesia Central"
    assert "email" not in entry["user"]

    assert letter["id"] not in ids(
        await client.get(f"{LETTERS}/queue", headers=world["other_admin"]["headers"])
    )
    assert letter["id"] in ids(await client.get(f"{LETTERS}/queue", headers=world["master"]["headers"]))
    assert (await client.get(f"{LETTERS}/queue", headers=world["instructor"]["headers"])).status_code == 403

    await _review(client, world["zone_coordinator"], letter["id"], "VALIDATE")
    assert letter["id"] not in ids(
        await client.get(f"{LETTERS}/queue?status=SUBMITTED", headers=world["zone_coordinator"]["headers"])
    )
    assert letter["id"] in ids(
        await client.get(f"{LETTERS}/queue?status=ZONE_VALIDATED", headers=world["assoc_admin"]["headers"])
    )


# ----------------------------------------------------------------------------
# The single gate: instructor_is_verified
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_checklist_reports_every_condition(client, world, r2, clean):
    empty = await _me(client, world["instructor"])
    assert empty.status_code == 200
    assert empty.json() == {
        "role": "INSTRUCTOR",
        "email_verified": True,
        "child_protection": True,
        "letter": None,
        "verified": False,
    }

    await _submitted(client, r2, world["instructor"])
    waiting = (await _me(client, world["instructor"])).json()
    assert waiting["letter"]["status"] == "SUBMITTED"
    assert waiting["verified"] is False  # a letter in progress verifies nobody


@pytest.mark.asyncio
async def test_each_condition_of_the_gate_on_its_own(client, world, r2, clean):
    from app.models import User
    from app.rbac import instructor_is_verified

    await _authorized(client, r2, world, world["instructor"])
    user_id = uuid.UUID(world["instructor"]["id"])

    async def verified(**changes) -> bool:
        async with SessionLocal() as db:
            user = await db.get(User, user_id)
            for column, value in changes.items():
                setattr(user, column, value)
            answer = await instructor_is_verified(db, user)
            await db.rollback()
            return answer

    assert await verified() is True
    assert await verified(role="STUDENT") is False
    assert await verified(status="SUSPENDED") is False
    assert await verified(is_minor=True) is False
    assert await verified(verification_status="PENDING") is False  # e-mail only, but still required
    assert await verified(child_protection_completed=False) is False

    # An expired authorization stops working the same day, without touching the letter.
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE church_letters SET valid_until = :day WHERE user_id = :id"),
            {"day": date.today() - timedelta(days=1), "id": user_id},
        )
        await db.commit()
    assert await verified() is False
    assert (await _me(client, world["instructor"])).json()["verified"] is False


@pytest.mark.asyncio
async def test_a_letter_of_someone_else_never_verifies_you(client, world, r2, clean):
    await _authorized(client, r2, world, world["instructor"])
    assert (await _me(client, world["instructor2"])).json()["verified"] is False
