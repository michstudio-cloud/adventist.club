"""«Mi club ya está en el evento»: solicitud de director desde el registro y «Aprobar» en Clubes.

Proved here:
  * `GET /events/directors/clubs` (no session) finds the clubs of open events by name, accents
    and case ignored; nothing for one letter;
  * signing up with `event_director_request` creates an adult account (never a director without a
    club) and a pending `request` invitation, with no e-mail; Clubes shows it; a COORDINATOR of the
    event may not approve, the association's administration does: the account is the director;
  * the address the administration already linked: director at once, no approval;
  * another address already pending, or a club with a director: refused, and no account is left.
"""
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("dirreq")
API = "/api/v1/events"
SEARCH = f"{API}/directors/clubs"
REGISTER = "/api/v1/auth/register"
PASSWORD = "Camporee-2026x"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b", "c", "d")}
    for club in clubs.values():
        await _exec(
            "UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
            " WHERE id = :id", id=uuid.UUID(club["id"]))
        await _exec(
            "INSERT INTO organization_ministries (organization_id, ministry_id)"
            " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
            id=uuid.UUID(club["id"]))
    names = {}
    for key, club in clubs.items():
        names[key] = f"{factory.name(f'club-{key}')} Águila"
        await _exec("UPDATE organizations SET name = :n WHERE id = :id", n=names[key], id=uuid.UUID(club["id"]))
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    coordinator = await factory.user("coordinator", "INSTRUCTOR", church["id"])
    h = admin["headers"]
    created = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Director request",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    registrations = {}
    for key, club in clubs.items():
        response = await client.post(f"{API}/{event_id}/registrations", json={"club_id": club["id"]}, headers=h)
        assert response.status_code == 201, response.text
        registrations[key] = response.json()["id"]
    assert (await client.post(f"{API}/{event_id}/staff", json={"email": coordinator["email"], "role": "COORDINATOR"},
                              headers=h)).status_code == 201
    assert (await client.post(f"{API}/{event_id}/status", json={"status": "OPEN"}, headers=h)).status_code == 200
    yield {"event": event_id, "clubs": clubs, "names": names, "registrations": registrations,
           "admin": admin, "coordinator": coordinator}
    await _exec("DELETE FROM org_invitations WHERE on_accept->>'event_id' = :id", id=event_id)
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event_id))


async def _directors(client, w) -> dict:
    response = await client.get(f"{API}/{w['event']}/directors", headers=w["admin"]["headers"])
    assert response.status_code == 200, response.text
    return {row["registration_id"]: row for row in response.json()["clubs"]}


def _signup(email: str, registration_id: str, name: str = "Directora Prueba") -> dict:
    return {"email": email, "password": PASSWORD, "name": name, "role": "CLUB_DIRECTOR",
            "event_director_request": {"registration_id": registration_id}}


async def test_search_finds_the_event_clubs_without_session(client, w):
    assert (await client.get(SEARCH, params={"q": "a"})).json() == []
    found = (await client.get(SEARCH, params={"q": w["names"]["a"].replace("Á", "a").upper()})).json()
    assert [row["club_name"] for row in found] == [w["names"]["a"]]
    assert found[0]["registration_id"] == w["registrations"]["a"] and found[0]["has_director"] is False


async def test_request_then_approval(client, factory, w):
    email = factory.email("req-a")
    signed = await client.post(REGISTER, json=_signup(email, w["registrations"]["a"]))
    assert signed.status_code == 201, signed.text
    assert signed.json()["role"] == "INSTRUCTOR"
    row = (await _directors(client, w))[w["registrations"]["a"]]
    assert row["state"] == "INVITED" and row["invitation"]["request"] is True
    assert row["invitation"]["name"] == "Directora Prueba"
    invitation_id = row["invitation"]["id"]
    assert await fetch_one("SELECT id FROM notification_log WHERE entity_id = :id", id=invitation_id) is None

    url = f"{API}/{w['event']}/invitations/{invitation_id}/approve"
    assert (await client.post(url, headers=w["coordinator"]["headers"])).status_code == 403
    ok = await client.post(url, headers=w["admin"]["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["action"] == "approved" and ok.json()["club"]["state"] == "ACTIVE"
    member = await fetch_one(
        "SELECT m.role, m.status FROM club_memberships m JOIN users u ON u.id = m.user_id"
        " WHERE u.email = :e AND m.club_id = :c", e=email, c=uuid.UUID(w["clubs"]["a"]["id"]))
    assert dict(member) == {"role": "CLUB_DIRECTOR", "status": "ACTIVE"}
    assert (await client.post(url, headers=w["admin"]["headers"])).status_code == 409

    # The club has its director now: another request is refused and leaves no account.
    late = factory.email("late-a")
    refused = await client.post(REGISTER, json=_signup(late, w["registrations"]["a"]))
    assert refused.status_code == 409
    assert await fetch_one("SELECT id FROM users WHERE email = :e", e=late) is None
    found = (await client.get(SEARCH, params={"q": w["names"]["a"]})).json()
    assert found[0]["has_director"] is True


async def test_the_address_already_linked_is_director_at_once(client, factory, w):
    email = factory.email("linked-b")
    link = await client.post(f"{API}/{w['event']}/registrations/{w['registrations']['b']}/director",
                             json={"name": "Directora B", "email": email}, headers=w["admin"]["headers"])
    assert link.status_code == 200 and link.json()["action"] == "invite"
    signed = await client.post(REGISTER, json=_signup(email, w["registrations"]["b"], "Directora B"))
    assert signed.status_code == 201, signed.text
    row = (await _directors(client, w))[w["registrations"]["b"]]
    assert row["state"] == "ACTIVE" and row["director"]["email"] == email


async def test_another_pending_address_is_refused(client, factory, w):
    first = factory.email("first-c")
    assert (await client.post(REGISTER, json=_signup(first, w["registrations"]["c"]))).status_code == 201
    second = factory.email("second-c")
    refused = await client.post(REGISTER, json=_signup(second, w["registrations"]["c"]))
    assert refused.status_code == 409
    assert await fetch_one("SELECT id FROM users WHERE email = :e", e=second) is None


async def test_request_and_new_club_are_exclusive(client, factory, w):
    body = _signup(factory.email("both-d"), w["registrations"]["d"])
    body["club"] = {"name": "Otro", "association_id": str(uuid.uuid4())}
    assert (await client.post(REGISTER, json=body)).status_code in (400, 422)
