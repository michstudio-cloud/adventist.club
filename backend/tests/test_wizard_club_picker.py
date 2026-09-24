"""The assistant's «Club» field (owner, 2026-09-24): registered clubs as you type, the batch tied
to the one picked, and the «Asociación o misión» line that a folio'd render prints back."""
import re
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.conftest import SessionLocal, module_factory, requires_db

pytestmark = requires_db

factory = module_factory("clubpick")
LOOKUP = "/api/v1/org-nodes/clubs/lookup"
BATCH = "/api/v1/certificates/prototype-batch"


async def _set(org: dict, **values) -> None:
    sets = ", ".join(f"{key} = :{key}" for key in values)
    async with SessionLocal() as db:
        await db.execute(text(f"UPDATE organizations SET {sets} WHERE id = :id"), {"id": uuid.UUID(org["id"]), **values})
        await db.commit()


async def _pathfinders_id() -> uuid.UUID:
    async with SessionLocal() as db:
        return (await db.execute(text("SELECT id FROM ministries WHERE slug = 'pathfinders'"))).scalar_one()


@pytest_asyncio.fixture(scope="module")
async def tree(factory):
    union = await factory.org("union", "union")
    association = await factory.org("asociacion norte", "association", union)
    code = f"CP{uuid.uuid4().hex[:8].upper()}"   # organizations.code is unique
    await _set(association, code=code)
    church = await factory.org("iglesia central", "church", association)
    club = await factory.org("Club Orión", "club", church)
    await _set(club, city="Reynosa", state="Tamaulipas", ministry_id=await _pathfinders_id(),
               latitude=26.1, longitude=-98.3)
    pending = await factory.org("Club Orión pendiente", "club", church)
    await _set(pending, status="pending")
    loose = await factory.org("Club Orión suelto", "club", union)
    return {"code": code, "association": association, "club": club, "pending": pending, "loose": loose}


def _body(factory, label: str, **extra) -> dict:
    return {"recipient_names": [factory.name(label)], "honor_name": factory.name("honor"),
            "club_name": "Texto libre", "issued_date": "2026-09-24", "width_in": 11, "height_in": 8.5, **extra}


async def test_the_public_lookup_lists_active_clubs_with_the_minimum_fields(client, factory, tree):
    # accent-insensitive, no session, pending clubs never appear
    response = await client.get(LOOKUP, params={"q": factory.name("club orion")})
    assert response.status_code == 200, response.text
    rows = response.json()
    ids = [row["id"] for row in rows]
    assert tree["club"]["id"] in ids and tree["loose"]["id"] in ids and tree["pending"]["id"] not in ids
    orion = next(row for row in rows if row["id"] == tree["club"]["id"])
    assert set(orion) == {"id", "name", "ministry", "city", "state", "association"}
    assert orion["name"] == factory.name("Club Orión") and (orion["city"], orion["state"]) == ("Reynosa", "Tamaulipas")
    assert orion["ministry"]["slug"] == "pathfinders"
    assert orion["association"] == {"id": tree["association"]["id"], "name": factory.name("asociacion norte"), "code": tree["code"]}
    loose = next(row for row in rows if row["id"] == tree["loose"]["id"])
    assert loose["association"] is None and loose["ministry"] is None


async def test_the_lookup_needs_two_letters_and_returns_at_most_eight(client, factory, tree):
    assert (await client.get(LOOKUP, params={"q": "o"})).json() == []
    for index in range(9):
        await factory.org(f"Club Lote {index}", "club")
    rows = (await client.get(LOOKUP, params={"q": factory.name("club lote")})).json()
    assert len(rows) == 8


async def test_my_club_is_the_active_membership_or_the_directors_own(client, factory, tree):
    assert (await client.get(f"{LOOKUP}/mine")).status_code == 401
    director = await factory.user("director", "CLUB_DIRECTOR", tree["club"]["id"])
    mine = await client.get(f"{LOOKUP}/mine", headers=director["headers"])
    assert mine.status_code == 200 and mine.json()["id"] == tree["club"]["id"]
    assert mine.json()["association"]["code"] == tree["code"]
    stranger = await factory.user("sin-club")
    assert (await client.get(f"{LOOKUP}/mine", headers=stranger["headers"])).json() is None
    waiting = await factory.user("pendiente", "CLUB_DIRECTOR", tree["pending"]["id"])
    assert (await client.get(f"{LOOKUP}/mine", headers=waiting["headers"])).json() is None


async def test_a_batch_with_a_registered_club_prints_its_name_and_keeps_the_link(client, factory, tree):
    response = await client.post(BATCH, json=_body(factory, "Ana", club_id=tree["club"]["id"], association_name=" Asociación Norte "))
    assert response.status_code == 201, response.text
    issued = response.json()[0]
    assert issued["club_name_snapshot"] == factory.name("Club Orión")      # not the free text
    async with SessionLocal() as db:
        row = (await db.execute(text(
            "SELECT cl.name AS club, e.metadata_json AS meta FROM certificates c JOIN clubs cl ON cl.id = c.club_id "
            "JOIN certificate_events e ON e.certificate_id = c.id AND e.event_type = 'issued' WHERE c.certificate_no = :n"),
            {"n": issued["certificate_no"]})).mappings().one()
    assert row["club"] == factory.name("Club Orión")
    assert row["meta"]["club_organization_id"] == tree["club"]["id"]
    assert row["meta"]["association_name"] == "Asociación Norte"
    verify = (await client.get(f"/api/v1/certificates/verify/{issued['certificate_no']}")).json()
    assert verify["valid"] is True and verify["club_name"] == factory.name("Club Orión")


async def test_a_batch_with_an_unknown_or_inactive_club_is_404(client, factory, tree):
    for club_id in (str(uuid.uuid4()), tree["pending"]["id"], tree["association"]["id"]):
        response = await client.post(BATCH, json=_body(factory, "Bea", club_id=club_id))
        assert response.status_code == 404, club_id
        assert response.json()["detail"]["code"] == "club_not_found"


async def test_free_text_still_works_and_anonymous_is_still_one_name(client, factory, tree):
    free = await client.post(BATCH, json=_body(factory, "Caro"))
    assert free.status_code == 201 and free.json()[0]["club_name_snapshot"] == "Texto libre"
    two = _body(factory, "Dani", club_id=tree["club"]["id"])
    two["recipient_names"].append(factory.name("Eli"))
    response = await client.post(BATCH, json=two)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "batch_requires_account"


async def test_a_folio_render_prints_the_association_the_batch_recorded(client, factory, tree, monkeypatch):
    monkeypatch.setattr("app.routers.render.fonts_installed", lambda: True)
    issued = (await client.post(BATCH, json=_body(factory, "Fer", association_name="Misión del Golfo"))).json()[0]
    response = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-dorada", "locale": "es", "format": "svg", "certificate_no": issued["certificate_no"],
        "data": {"association_name": "Otra cosa"}})
    assert response.status_code == 200, response.text
    printed = re.findall(r"<tspan[^>]*>([^<]*)</tspan>", response.text)
    assert "Misión del Golfo" in printed and "Otra cosa" not in printed
    # none recorded: still no line, whatever the caller sends (the record wins)
    bare = (await client.post(BATCH, json=_body(factory, "Gabi"))).json()[0]
    response = await client.post("/api/v1/certificates/render", json={
        "template": "especialidad-dorada", "locale": "es", "format": "svg", "certificate_no": bare["certificate_no"],
        "data": {"association_name": "Otra cosa"}})
    assert response.status_code == 200 and 'id="association_name"' not in response.text
