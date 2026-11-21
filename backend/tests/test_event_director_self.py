"""El director se registra solo con el pase del club (`POST /events/pass/director`, sin sesión).

Proved here:
  * unknown pass: neutral 404; before the club's arrival: 409 (nothing created);
  * after the arrival: a CLUB_DIRECTOR invitation tied to the event, its link returned at once
    (and e-mailed); accepting it makes that account the club's director, only that club;
  * the same address again gets a fresh link (the old one dies); another address while one is
    pending, or once the club has a director: 409;
  * an administrator's address is never taken as a director.
"""
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("selfdir")
API = "/api/v1/events"
SELF = f"{API}/pass/director"
ACCEPT = "/api/v1/org-invitations/accept"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


def _token(url: str) -> str:
    return url.split("t=", 1)[1]


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b")}
    for club in clubs.values():
        await _exec(
            "UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
            " WHERE id = :id", id=uuid.UUID(club["id"]))
        await _exec(
            "INSERT INTO organization_ministries (organization_id, ministry_id)"
            " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
            id=uuid.UUID(club["id"]))
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    h = admin["headers"]
    created = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Self director",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    registrations, passes = {}, {}
    for key, club in clubs.items():
        response = await client.post(f"{API}/{event_id}/registrations", json={"club_id": club["id"]}, headers=h)
        assert response.status_code == 201, response.text
        registrations[key] = response.json()["id"]
        passes[key] = response.json()["pass_token"]
        assert passes[key]
    assert (await client.post(f"{API}/{event_id}/status", json={"status": "OPEN"}, headers=h)).status_code == 200
    yield {"event": event_id, "clubs": clubs, "registrations": registrations, "passes": passes, "admin": admin}
    await _exec("DELETE FROM org_invitations WHERE on_accept->>'event_id' = :id", id=event_id)
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event_id))


async def test_director_registers_with_the_pass_after_arrival(client, factory, w):
    email = factory.email("dir-a")
    body = {"token": w["passes"]["a"], "name": "Directora A", "email": email}

    assert (await client.post(SELF, json={**body, "token": "evp_" + "x" * 40})).status_code == 404
    early = await client.post(SELF, json=body)
    assert early.status_code == 409 and "llegada" in early.json()["detail"]
    assert await fetch_one("SELECT id FROM org_invitations WHERE email = :e", e=email) is None

    arrived = await client.post(f"{API}/{w['event']}/check-in", json={"pass_token": w["passes"]["a"]},
                                headers=w["admin"]["headers"])
    assert arrived.status_code == 201, arrived.text

    first = await client.post(SELF, json=body)
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["club_name"]
    assert out["url"].startswith("https://events.adventist.club/invitacion?t=")
    row = await fetch_one("SELECT role, return_to, on_accept FROM org_invitations WHERE email = :e AND revoked_at IS NULL",
                          e=email)
    assert row["role"] == "CLUB_DIRECTOR" and row["on_accept"]["name"] == "Directora A"
    assert row["return_to"] == f"https://events.adventist.club/{w['event']}/club"

    # Same address again: a fresh link, the old one dies.
    again = await client.post(SELF, json=body)
    assert again.status_code == 200 and again.json()["url"] != out["url"]
    live = await fetch_one("SELECT count(*) AS n FROM org_invitations WHERE email = :e AND revoked_at IS NULL", e=email)
    assert live["n"] == 1
    # Another address while that one is pending: refused.
    other = await client.post(SELF, json={**body, "email": factory.email("other-a")})
    assert other.status_code == 409

    person = await factory.user("dir-a", "STUDENT")
    assert (await client.post(ACCEPT, json={"token": _token(out["url"])}, headers=person["headers"])).status_code != 200
    ok = await client.post(ACCEPT, json={"token": _token(again.json()["url"])}, headers=person["headers"])
    assert ok.status_code == 200, ok.text
    mine = await client.get(f"{API}/{w['event']}/my", headers=person["headers"])
    assert [item["club"]["id"] for item in mine.json()["director"]["registrations"]] == [w["clubs"]["a"]["id"]]

    # The club has its director now: nobody else registers with that pass.
    late = await client.post(SELF, json={**body, "email": factory.email("late-a")})
    assert late.status_code == 409 and "ya tiene director" in late.json()["detail"]


async def test_an_administrator_address_is_never_taken(client, w):
    arrived = await client.post(f"{API}/{w['event']}/check-in", json={"pass_token": w["passes"]["b"]},
                                headers=w["admin"]["headers"])
    assert arrived.status_code == 201, arrived.text
    refused = await client.post(SELF, json={"token": w["passes"]["b"], "name": "Admin", "email": w["admin"]["email"]})
    assert refused.status_code == 409
