"""«QR para jueces» (30 minutos).

Proved here:
  * only the event's coordination makes the link; it lives on events.adventist.club `/unirse`;
  * the preview needs no session and names the event; a tampered or expired token is the same 404;
  * joining makes the signed-in adult a judge of every activity, once (idempotent); a minor never;
  * a closed event refuses it.
"""
import time
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.services import event_judge_link
from tests.conftest import RUN, fetch_all, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("judgelink")
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
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    coordinator = await factory.user("coordinator", "INSTRUCTOR", church["id"])
    newcomer = await factory.user("newcomer", "INSTRUCTOR")
    kid = await factory.user("kid", "STUDENT", is_minor=True)
    stranger = await factory.user("stranger", "INSTRUCTOR")
    h = admin["headers"]
    created = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Judge link",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    assert (await client.post(f"{API}/{event_id}/staff", json={"email": coordinator["email"], "role": "COORDINATOR"},
                              headers=h)).status_code == 201
    assert (await client.post(f"{API}/{event_id}/status", json={"status": "OPEN"}, headers=h)).status_code == 200
    yield {"event": event_id, "admin": admin, "coordinator": coordinator, "newcomer": newcomer, "kid": kid,
           "stranger": stranger}
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event_id))


def _token(url: str) -> str:
    return url.split("t=", 1)[1]


async def test_link_preview_and_join(client, w):
    url = f"{API}/{w['event']}/staff/join-link"
    assert (await client.post(url, headers=w["stranger"]["headers"])).status_code == 403
    made = await client.post(url, headers=w["coordinator"]["headers"])
    assert made.status_code == 200, made.text
    link = made.json()["url"]
    assert link.startswith("https://events.adventist.club/unirse?t=ejl_")
    token = _token(link)

    preview = await client.post(f"{API}/staff/join/preview", json={"token": token})
    assert preview.status_code == 200 and preview.json()["event_id"] == w["event"]
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert (await client.post(f"{API}/staff/join/preview", json={"token": tampered})).status_code == 404

    assert (await client.post(f"{API}/staff/join", json={"token": token})).status_code == 401
    joined = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["newcomer"]["headers"])
    assert joined.status_code == 200, joined.text
    assert joined.json() == {"event_id": w["event"], "event_name": joined.json()["event_name"], "added": 1}
    again = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["newcomer"]["headers"])
    assert again.json()["added"] == 0
    rows = await fetch_all("SELECT role, activity_id FROM event_staff WHERE event_id = :e AND user_id = :u",
                           e=uuid.UUID(w["event"]), u=uuid.UUID(w["newcomer"]["id"]))
    assert [(r["role"], r["activity_id"]) for r in rows] == [("JUDGE", None)]
    mine = await client.get(f"{API}/{w['event']}/my", headers=w["newcomer"]["headers"])
    assert mine.status_code == 200 and "JUDGE" in str(mine.json())

    assert (await client.post(f"{API}/staff/join", json={"token": token},
                              headers=w["kid"]["headers"])).status_code == 403


async def test_expired_link_is_refused(client, w):
    token, _ = event_judge_link.make(uuid.UUID(w["event"]), uuid.UUID(w["coordinator"]["id"]),
                                     now=time.time() - event_judge_link.TTL_SECONDS - 5)
    assert (await client.post(f"{API}/staff/join/preview", json={"token": token})).status_code == 404
    assert (await client.post(f"{API}/staff/join", json={"token": token},
                              headers=w["stranger"]["headers"])).status_code == 404
