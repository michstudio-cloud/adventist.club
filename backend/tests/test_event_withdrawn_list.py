"""A withdrawn club's director no longer sees that event in their list of events."""
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("wdlist")
API = "/api/v1/events"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    club = await factory.org("club", "club", church)
    await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                " WHERE id = :id", id=uuid.UUID(club["id"]))
    await _exec("INSERT INTO organization_ministries (organization_id, ministry_id)"
                " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
                id=uuid.UUID(club["id"]))
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    director = await factory.user("director", "CLUB_DIRECTOR", club["id"])
    h = admin["headers"]
    created = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Withdrawn list",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    reg = await client.post(f"{API}/{event_id}/registrations", json={"club_id": club["id"]}, headers=h)
    assert reg.status_code == 201, reg.text
    yield {"event": event_id, "registration": reg.json()["id"], "admin": admin, "director": director}
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event_id))


async def test_withdrawn_club_hides_the_event_from_its_director(client, w):
    def ids(response):
        body = response.json()
        rows = body if isinstance(body, list) else body.get("items", [])
        return {row["id"] for row in rows}

    listed = await client.get(API, headers=w["director"]["headers"])
    assert listed.status_code == 200 and w["event"] in ids(listed)
    out = await client.post(f"{API}/{w['event']}/registrations/{w['registration']}/withdraw",
                            headers=w["admin"]["headers"])
    assert out.status_code == 200, out.text
    assert w["event"] not in ids(await client.get(API, headers=w["director"]["headers"]))
    # The administration still lists it.
    assert w["event"] in ids(await client.get(API, headers=w["admin"]["headers"]))
