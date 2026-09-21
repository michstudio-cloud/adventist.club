"""Bloque E, incremento E4 — Solicitudes de ingreso desde `/clubs` y traslados.

La otra puerta de entrada al club: la persona la toca desde fuera. Dos cosas
que importan aquí más que el camino feliz: un menor que solicita NO es visible
para el club hasta que su tutor autoriza (§7), y los correos a terceros nunca
llevan nombres de menores (§5.10).
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.services import email as email_service
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("joinreq")

USERS = "/api/v1/users"
CLUBS = "/api/v1/clubs"
MEMBERSHIPS = "/api/v1/memberships"
REQUESTS = f"{MEMBERSHIPS}/requests"

MINOR_BIRTH_DATE = date(2014, 5, 4)


@pytest.fixture
def mails(monkeypatch):
    sent = []

    async def consent(to, child_name, club_name, link):
        sent.append({"kind": "consent", "to": to, "child": child_name, "link": link})
        return True

    async def queue(to, director_name, club_name, pending, link):
        sent.append({"kind": "queue", "to": to, "pending": pending, "club": club_name})
        return True

    async def decision(to, name, club_name, approved, reason):
        sent.append(
            {"kind": "decision", "to": to, "approved": approved, "reason": reason, "name": name}
        )
        return True

    monkeypatch.setattr(email_service, "send_consent_request_email", consent)
    monkeypatch.setattr(email_service, "send_pending_requests_email", queue)
    monkeypatch.setattr(email_service, "send_membership_decision_email", decision)
    return sent


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :id"),
            {**columns, "id": uuid.UUID(user["id"])},
        )
        await db.commit()


async def _join(user: dict, club: dict, role: str = "STUDENT") -> str:
    membership_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at) VALUES (:id, :user, :club, :role, 'ACTIVE', 'BACKFILL', now())"
            ),
            {
                "id": membership_id,
                "user": uuid.UUID(user["id"]),
                "club": uuid.UUID(club["id"]),
                "role": role,
            },
        )
        await db.execute(
            text("UPDATE users SET organization_id = :club, role = :role WHERE id = :user"),
            {"club": uuid.UUID(club["id"]), "role": role, "user": uuid.UUID(user["id"])},
        )
        await db.commit()
    return str(membership_id)


async def _verified(factory, label, role="STUDENT", **extra) -> dict:
    user = await factory.user(label, role, **extra)
    await _set(user, verification_status="VERIFIED")
    return user


async def _membership_of(user: dict) -> dict | None:
    return await fetch_one(
        "SELECT id::text AS id, club_id::text AS club, role, status, source, end_reason,"
        " decision_reason FROM club_memberships WHERE user_id = :id"
        " ORDER BY created_at DESC LIMIT 1",
        id=user["id"],
    )


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("other-club", "club", association)
    director = await factory.user("director", "CLUB_DIRECTOR", club["id"])
    secretary = await factory.user("secretary", "CLUB_SECRETARY", club["id"])
    await _join(director, club, "CLUB_DIRECTOR")
    await _join(secretary, club, "CLUB_SECRETARY")
    other_director = await factory.user("other-director", "CLUB_DIRECTOR", other_club["id"])
    await _join(other_director, other_club, "CLUB_DIRECTOR")
    return {
        "association": association,
        "club": club,
        "other_club": other_club,
        "director": director,
        "secretary": secretary,
        "other_director": other_director,
    }


async def _request(client, world, person, **payload):
    return await client.post(
        REQUESTS, json={"club_id": world["club"]["id"], **payload}, headers=person["headers"]
    )


# ----------------------------------------------------------------------------
# Pedir entrar
# ----------------------------------------------------------------------------
async def test_an_adult_request_waits_for_the_director(client, world, factory, mails):
    person = await _verified(factory, "asker")
    created = await _request(client, world, person, message="Me mudé a la ciudad este mes")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "PENDING_APPROVAL" and body["role"] == "STUDENT"

    row = await _membership_of(person)
    assert (row["status"], row["source"]) == ("PENDING_APPROVAL", "REQUEST")
    org = await fetch_one("SELECT organization_id FROM users WHERE id = :id", id=person["id"])
    assert org["organization_id"] is None

    # The director is told there is a queue, with a count and no names at all.
    queue_mails = [mail for mail in mails if mail["kind"] == "queue"]
    assert queue_mails and queue_mails[0]["to"] == world["director"]["email"]
    assert queue_mails[0]["pending"] == 1
    assert person["email"] not in str(mails)


async def test_an_unverified_account_cannot_ask(client, world, factory):
    person = await factory.user("unverified")
    refused = await _request(client, world, person)
    assert refused.status_code == 403
    assert "correo" in refused.json()["detail"].lower()


async def test_a_club_can_close_its_door(client, world, factory):
    closed = await client.patch(
        f"{CLUBS}/{world['other_club']['id']}/profile",
        json={"accepts_requests": False},
        headers=world["other_director"]["headers"],
    )
    assert closed.status_code == 200, closed.text

    person = await _verified(factory, "unwanted")
    refused = await client.post(
        REQUESTS, json={"club_id": world["other_club"]["id"]}, headers=person["headers"]
    )
    assert refused.status_code == 409
    assert "solicitudes" in refused.json()["detail"].lower()


async def test_only_three_open_requests_and_one_per_club(client, world, factory):
    person = await _verified(factory, "eager")
    clubs = [await factory.org(f"eager-club-{i}", "club", world["association"]) for i in range(4)]
    for club in clubs[:3]:
        response = await client.post(
            REQUESTS, json={"club_id": club["id"]}, headers=person["headers"]
        )
        assert response.status_code == 201, response.text

    duplicate = await client.post(
        REQUESTS, json={"club_id": clubs[0]["id"]}, headers=person["headers"]
    )
    assert duplicate.status_code == 409

    too_many = await client.post(
        REQUESTS, json={"club_id": clubs[3]["id"]}, headers=person["headers"]
    )
    assert too_many.status_code == 409
    assert "3" in too_many.json()["detail"]


async def test_a_rejection_has_a_cooling_off_period(client, world, factory, mails):
    person = await _verified(factory, "rejected")
    created = await _request(client, world, person)
    membership_id = created.json()["membership_id"]

    rejected = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/reject",
        json={"reason": "Este año el club está completo"},
        headers=world["director"]["headers"],
    )
    assert rejected.status_code == 200, rejected.text
    row = await _membership_of(person)
    assert row["status"] == "REJECTED" and row["decision_reason"]

    # The person is told, with the reason.
    decisions = [mail for mail in mails if mail["kind"] == "decision"]
    assert decisions and decisions[-1]["to"] == person["email"]
    assert decisions[-1]["approved"] is False and decisions[-1]["reason"]

    too_soon = await _request(client, world, person)
    assert too_soon.status_code == 409
    assert "30" in too_soon.json()["detail"]

    # Once the 30 days are up, they may ask again.
    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE club_memberships SET decided_at = now() - interval '31 days'"
                " WHERE id = :id"
            ),
            {"id": uuid.UUID(membership_id)},
        )
        await db.commit()
    again = await _request(client, world, person)
    assert again.status_code == 201, again.text


async def test_a_person_can_withdraw_their_own_request(client, world, factory):
    person = await _verified(factory, "withdrawer")
    created = await _request(client, world, person)
    membership_id = created.json()["membership_id"]
    other = await _verified(factory, "nosy")

    refused = await client.delete(f"{REQUESTS}/{membership_id}", headers=other["headers"])
    assert refused.status_code == 404

    cancelled = await client.delete(f"{REQUESTS}/{membership_id}", headers=person["headers"])
    assert cancelled.status_code == 200, cancelled.text
    row = await _membership_of(person)
    assert row["status"] == "CANCELLED"


async def test_an_adult_instructor_asks_as_an_instructor(client, world, factory):
    person = await _verified(factory, "instructor-asker", "INSTRUCTOR")
    created = await _request(client, world, person)
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "INSTRUCTOR"


async def test_administrative_accounts_cannot_ask(client, world, factory):
    admin = await _verified(
        factory, "admin-asker", "ADMIN_ASSOCIATION", organization_id=world["association"]["id"]
    )
    refused = await _request(client, world, admin)
    assert refused.status_code in (403, 409)


# ----------------------------------------------------------------------------
# La cola del director
# ----------------------------------------------------------------------------
async def test_the_queue_shows_age_and_never_a_birth_date(client, world, factory, mails):
    person = await _verified(factory, "queued")
    await _request(client, world, person, message="Quiero unirme")

    queue = await client.get(
        f"{CLUBS}/{world['club']['id']}/requests", headers=world["director"]["headers"]
    )
    assert queue.status_code == 200, queue.text
    rows = {row["user_id"]: row for row in queue.json()}
    assert person["id"] in rows
    mine = rows[person["id"]]
    assert mine["message"] == "Quiero unirme" and mine["source"] == "REQUEST"
    assert "birth_date" not in mine and "email" not in mine

    outsider = await client.get(
        f"{CLUBS}/{world['club']['id']}/requests", headers=world["other_director"]["headers"]
    )
    assert outsider.status_code == 403


async def test_a_minor_is_invisible_until_a_guardian_authorizes(client, world, factory, mails):
    minor = await _verified(factory, "minor-asker", is_minor=True)
    await _set(minor, birth_date=MINOR_BIRTH_DATE)
    guardian = await _verified(factory, "minor-guardian", "PARENT_GUARDIAN")

    created = await _request(client, world, minor, guardian_email=guardian["email"])
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "PENDING_CONSENT"

    queue = await client.get(
        f"{CLUBS}/{world['club']['id']}/requests", headers=world["director"]["headers"]
    )
    assert minor["id"] not in [row["user_id"] for row in queue.json()]
    # And the club was not told about a minor it may not know about yet.
    assert all(mail["kind"] != "queue" or minor["email"] not in str(mail) for mail in mails)

    consent_mails = [mail for mail in mails if mail["kind"] == "consent"]
    assert consent_mails and consent_mails[-1]["to"] == guardian["email"]

    approved = await client.post(
        f"{MEMBERSHIPS}/consents/decide",
        json={"token": consent_mails[-1]["link"].rsplit("=", 1)[1], "decision": "APPROVE"},
        headers=guardian["headers"],
    )
    assert approved.status_code == 200, approved.text
    # Authorized, but a request still waits for the club.
    assert approved.json()["status"] == "PENDING_APPROVAL"

    queue = await client.get(
        f"{CLUBS}/{world['club']['id']}/requests", headers=world["director"]["headers"]
    )
    row = {r["user_id"]: r for r in queue.json()}[minor["id"]]
    assert row["is_minor"] is True and row["age"] is not None
    assert row["consent"]["status"] == "APPROVED"


# ----------------------------------------------------------------------------
# Decidir
# ----------------------------------------------------------------------------
async def test_approving_puts_the_person_in_the_club(client, world, factory, mails):
    person = await _verified(factory, "approved")
    created = await _request(client, world, person)
    membership_id = created.json()["membership_id"]

    approved = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/approve",
        json={},
        headers=world["director"]["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ACTIVE"
    org = await fetch_one(
        "SELECT organization_id::text AS org FROM users WHERE id = :id", id=person["id"]
    )
    assert org["org"] == world["club"]["id"]

    decisions = [mail for mail in mails if mail["kind"] == "decision"]
    assert decisions[-1]["to"] == person["email"] and decisions[-1]["approved"] is True

    # Deciding twice is a conflict, not a second membership.
    again = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/approve",
        json={},
        headers=world["director"]["headers"],
    )
    assert again.status_code == 409


async def test_the_director_may_grant_another_role_on_approval(client, world, factory, mails):
    person = await _verified(factory, "promoted-on-approval")
    created = await _request(client, world, person)
    membership_id = created.json()["membership_id"]

    refused = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/approve",
        json={"role": "CLUB_DIRECTOR"},
        headers=world["director"]["headers"],
    )
    assert refused.status_code == 403

    approved = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/approve",
        json={"role": "COUNSELOR"},
        headers=world["director"]["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["role"] == "COUNSELOR"


async def test_the_secretary_approves_students_only(client, world, factory, mails):
    person = await _verified(factory, "sec-approved", "INSTRUCTOR")
    created = await _request(client, world, person)
    membership_id = created.json()["membership_id"]
    assert created.json()["role"] == "INSTRUCTOR"

    refused = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/approve",
        json={},
        headers=world["secretary"]["headers"],
    )
    assert refused.status_code == 403


async def test_approve_all_clears_the_student_queue(client, world, factory, mails):
    club = await factory.org("bulk-club", "club", world["association"])
    director = await factory.user("bulk-director", "CLUB_DIRECTOR", club["id"])
    await _join(director, club, "CLUB_DIRECTOR")
    people = [await _verified(factory, f"bulk-{i}") for i in range(3)]
    staff = await _verified(factory, "bulk-instructor", "INSTRUCTOR")
    for person in [*people, staff]:
        response = await client.post(
            REQUESTS, json={"club_id": club["id"]}, headers=person["headers"]
        )
        assert response.status_code == 201, response.text

    done = await client.post(f"{CLUBS}/{club['id']}/requests/approve-all", headers=director["headers"])
    assert done.status_code == 200, done.text
    assert done.json()["approved"] == 3  # the instructor is decided one by one

    for person in people:
        org = await fetch_one(
            "SELECT organization_id::text AS org FROM users WHERE id = :id", id=person["id"]
        )
        assert org["org"] == club["id"]
    still_waiting = await _membership_of(staff)
    assert still_waiting["status"] == "PENDING_APPROVAL"


async def test_being_approved_elsewhere_is_a_transfer(client, world, factory, mails):
    person = await _verified(factory, "transferred")
    await _join(person, world["other_club"])

    refused = await _request(client, world, person)
    assert refused.status_code == 409

    created = await _request(client, world, person, confirm_transfer=True)
    assert created.status_code == 201, created.text
    membership_id = created.json()["membership_id"]

    approved = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{membership_id}/approve",
        json={},
        headers=world["director"]["headers"],
    )
    assert approved.status_code == 200, approved.text
    rows = await fetch_all(
        "SELECT club_id::text AS club, status, end_reason FROM club_memberships"
        " WHERE user_id = :id ORDER BY created_at",
        id=person["id"],
    )
    assert [(r["club"], r["status"], r["end_reason"]) for r in rows] == [
        (world["other_club"]["id"], "ENDED", "TRANSFERRED"),
        (world["club"]["id"], "ACTIVE", None),
    ]


async def test_a_stale_request_expires_when_the_queue_is_read(client, world, factory, mails):
    person = await _verified(factory, "forgotten")
    created = await _request(client, world, person)
    membership_id = created.json()["membership_id"]
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE club_memberships SET created_at = now() - interval '31 days' WHERE id = :id"),
            {"id": uuid.UUID(membership_id)},
        )
        await db.commit()

    queue = await client.get(
        f"{CLUBS}/{world['club']['id']}/requests", headers=world["director"]["headers"]
    )
    assert person["id"] not in [row["user_id"] for row in queue.json()]
    row = await _membership_of(person)
    assert (row["status"], row["end_reason"]) == ("CANCELLED", "EXPIRED")


async def test_removing_a_member_tells_them_why(client, world, factory, mails):
    person = await _verified(factory, "removed")
    membership_id = await _join(person, world["club"])
    reason = "Dejó de asistir y se mudó de ciudad"

    removed = await client.post(
        f"{CLUBS}/{world['club']['id']}/members/{membership_id}/remove",
        json={"reason": reason},
        headers=world["director"]["headers"],
    )
    assert removed.status_code == 200, removed.text
    decisions = [mail for mail in mails if mail["kind"] == "decision"]
    assert decisions[-1]["to"] == person["email"]
    assert decisions[-1]["approved"] is False and decisions[-1]["reason"] == reason


# ----------------------------------------------------------------------------
# Lo que ve quien busca club
# ----------------------------------------------------------------------------
async def test_nearby_clubs_say_whether_they_take_requests(client, world, factory):
    club = await factory.org("nearby-club", "club", world["association"])
    director = await factory.user("nearby-director", "CLUB_DIRECTOR", club["id"])
    await _join(director, club, "CLUB_DIRECTOR")
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE organizations SET latitude = 25.9, longitude = -97.5 WHERE id = :id"),
            {"id": uuid.UUID(club["id"])},
        )
        await db.commit()

    nearby = await client.get(
        "/api/v1/org-nodes/clubs/nearby", params={"lat": 25.9, "lon": -97.5, "radius_km": 5}
    )
    assert nearby.status_code == 200, nearby.text
    row = {node["id"]: node for node in nearby.json()}[club["id"]]
    assert row["accepts_requests"] is True

    await client.patch(
        f"{CLUBS}/{club['id']}/profile",
        json={"accepts_requests": False},
        headers=director["headers"],
    )
    nearby = await client.get(
        "/api/v1/org-nodes/clubs/nearby", params={"lat": 25.9, "lon": -97.5, "radius_km": 5}
    )
    row = {node["id"]: node for node in nearby.json()}[club["id"]]
    assert row["accepts_requests"] is False
