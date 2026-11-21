"""«Puntaje completo a todos» (administración): el máximo de una actividad a cada club inscrito que
aún no tiene evaluación vigente en ella; los ya evaluados nunca se tocan; repetirlo no cuenta dos
veces; sólo la administración; sólo con el evento en curso."""
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, fetch_all, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("awardfull")
API = "/api/v1/events"
RUBRIC = {"criteria": [{"key": "orden", "label": "Orden", "max": 30, "deduction_step": 2},
                       {"key": "limpieza", "label": "Limpieza", "max": 20}]}


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    church = await factory.org("church", "church", assoc)
    clubs = [await factory.org(f"club-{i}", "club", church) for i in range(3)]
    for club in clubs:
        await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                    " WHERE id = :id", id=uuid.UUID(club["id"]))
        await _exec("INSERT INTO organization_ministries (organization_id, ministry_id)"
                    " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
                    id=uuid.UUID(club["id"]))
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    coordinator = await factory.user("coordinator", "INSTRUCTOR")
    judge = await factory.user("judge", "STUDENT")
    h = admin["headers"]
    event = await _ok(await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Completo",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h), 201)
    url = f"{API}/{event['id']}"
    rubric = await _ok(await client.post(f"{url}/activities", json={
        "name": "Inspección", "kind": "rubric", "max_points": 50, "config": RUBRIC}, headers=h), 201)
    for body in ({"user_id": coordinator["id"], "role": "COORDINATOR"},
                 {"user_id": judge["id"], "role": "JUDGE"}):
        await _ok(await client.post(f"{url}/staff", json=body, headers=h), 201)
    regs = [(await _ok(await client.post(f"{url}/registrations", json={"club_id": c["id"]}, headers=h), 201))["id"]
            for c in clubs]
    yield {"url": url, "event": event["id"], "rubric": rubric, "regs": regs, "admin": admin,
           "coordinator": coordinator, "judge": judge}
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event["id"]))


async def test_award_full_to_the_clubs_without_a_score(client, w):
    h = w["admin"]["headers"]
    award = f"{w['url']}/activities/{w['rubric']['id']}/award-full"
    # Not in progress yet: counting works, awarding does not.
    preview = await _ok(await client.post(award, params={"dry_run": "true"}, headers=h))
    assert preview["to_award"] == 3 and preview["points"] == 50 and preview["awarded"] == 0
    assert (await client.post(award, headers=h)).status_code == 409
    for step in ("OPEN", "IN_PROGRESS"):
        await _ok(await client.post(f"{w['url']}/status", json={"status": step}, headers=h))
    # One club already scored by a judge: never touched.
    await _ok(await client.post(f"{w['url']}/evaluations", json={
        "registration_id": w["regs"][0], "activity_id": w["rubric"]["id"],
        "inputs": {"criteria": {"orden": 10, "limpieza": 5}}, "idempotency_key": f"k-{uuid.uuid4().hex}"},
        headers=w["judge"]["headers"]), 201)
    # Coordination (not admin) and judges may not.
    assert (await client.post(award, headers=w["coordinator"]["headers"])).status_code == 403
    assert (await client.post(award, headers=w["judge"]["headers"])).status_code == 403

    done = await _ok(await client.post(award, headers=h))
    assert done["already_scored"] == 1 and done["awarded"] == 2 and done["failed"] == []
    rows = await fetch_all("SELECT registration_id, points, captured_as FROM evaluations"
                           " WHERE activity_id = :a AND status = 'CONFIRMED'", a=uuid.UUID(w["rubric"]["id"]))
    by_reg = {str(r["registration_id"]): r for r in rows}
    assert float(by_reg[w["regs"][0]]["points"]) == 15  # the judge's, untouched
    assert all(float(by_reg[reg]["points"]) == 50 and by_reg[reg]["captured_as"] == "COORDINATION"
               for reg in w["regs"][1:])
    # Again: nothing new.
    again = await _ok(await client.post(award, headers=h))
    assert again["awarded"] == 0 and again["to_award"] == 0
