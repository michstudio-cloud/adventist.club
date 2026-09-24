"""Horas de servicio desde la app: lo que las pantallas del miembro y del club necesitan.

  * la matriz de una clase dice, por miembro, cuántas horas aprobadas lleva cada requisito
    `HOURS` frente a su meta («3 / 5 h»), con la misma regla que la tarjeta del miembro:
    sólo lo APROBADO y sólo desde que empezó la clase;
  * el resumen del mes del club: horas de servicio aprobadas por miembro y por unidad, con el
    acumulado del año y lo que espera decisión; nunca la descripción ni el lugar;
  * la cola «Horas por aprobar» se puede acotar a un club (quien decide en varios, como la
    Asociación o MASTER, abre el panel de UN club).
"""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal, engine
from tests.conftest import DB_AVAILABLE, module_factory, requires_db
from tests.test_club_classes import _exec, _membership, _program, _unit

CLUBS = "/api/v1/clubs"
ACTIVITY = "/api/v1/activity"


def _tables_exist() -> bool:
    import asyncio

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.activity_logs') IS NOT NULL"
                    " AND to_regclass('public.club_units') IS NOT NULL"
                    " AND to_regclass('public.programs') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(not _tables_exist(), reason="apply migrations 008d, 012 and 013 to the test database"),
]
factory = module_factory("servicehours")


async def _log(user: dict, club: dict | None, *, on: date, quantity: float, status="APPROVED",
               category="SERVICE", description="Limpieza del parque", place="Parque central") -> str:
    log_id = uuid.uuid4()
    decided = status != "SUBMITTED"
    await _exec(
        "INSERT INTO activity_logs (id, user_id, club_id, category, performed_on, quantity,"
        " description, place, status, decided_at)"
        " VALUES (:id, :user, :club, :category, :on, :q, :d, :place, :status,"
        " CASE WHEN :decided THEN now() ELSE NULL END)",
        id=log_id, user=uuid.UUID(user["id"]), club=uuid.UUID(club["id"]) if club else None,
        category=category, on=on, q=quantity, d=description, place=place, status=status,
        decided=decided,
    )
    return str(log_id)


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("club-b", "club", association)
    p = {
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "secretary": await factory.user("secretary", "CLUB_SECRETARY", club["id"]),
        "counselor": await factory.user("counselor", "COUNSELOR", club["id"]),
        "s1": await factory.user("s1", "STUDENT", club["id"], is_minor=True),
        "s2": await factory.user("s2", "STUDENT", club["id"]),
        "s3": await factory.user("s3", "STUDENT", club["id"]),
        "s4": await factory.user("s4", "STUDENT", club["id"]),
        "outsider": await factory.user("outsider", "STUDENT", other_club["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", other_club["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    u1 = await _unit(club, factory.name("Águilas"), p["counselor"])
    u2 = await _unit(club, factory.name("Halcones"))
    await _membership(p["director"], club, role="CLUB_DIRECTOR")
    await _membership(p["secretary"], club, role="CLUB_SECRETARY")
    await _membership(p["counselor"], club, role="COUNSELOR")
    await _membership(p["s1"], club, unit_id=u1)
    await _membership(p["s2"], club, unit_id=u1)
    await _membership(p["s3"], club, unit_id=u2)
    await _membership(p["s4"], club)
    await _membership(p["outsider"], other_club)
    await _membership(p["director_b"], other_club, role="CLUB_DIRECTOR")
    return {**p, "club": club, "other_club": other_club, "u1": u1, "u2": u2}


# ----------------------------------------------------------------------------
# 1. La matriz dice «x / y h» en cada requisito HOURS
# ----------------------------------------------------------------------------
async def test_the_matrix_carries_the_approved_hours_of_each_hours_requirement(client, world, factory):
    program = await _program(factory, "horas", sections=(("a", 2),), hours_positions=(2,))
    hours_requirement = program["requirements"][1]
    url = f"{CLUBS}/{world['club']['id']}/classes/{program['id']}"
    body = {"membership_ids": [world["s2"]["membership_id"], world["s3"]["membership_id"]]}
    assert (await client.post(f"{url}/enroll", json=body,
                              headers=world["director"]["headers"])).status_code == 200

    today = date.today()
    await _log(world["s2"], world["club"], on=today, quantity=3)
    await _log(world["s2"], world["club"], on=today, quantity=1, status="SUBMITTED")
    await _log(world["s2"], world["club"], on=today, quantity=4, status="REJECTED")
    await _log(world["s2"], world["club"], on=today, quantity=1, category="ATTENDANCE")
    # Before the class started: it does not count, as on the member's own card.
    await _log(world["s2"], world["club"], on=today - timedelta(days=30), quantity=2)

    response = await client.get(f"{url}/matrix", headers=world["director"]["headers"])
    assert response.status_code == 200, response.text
    members = {row["membership_id"]: row for row in response.json()["members"]}
    assert members[world["s2"]["membership_id"]]["hours"] == {
        hours_requirement: {"approved": 3.0, "target": 5.0}}
    assert members[world["s3"]["membership_id"]]["hours"] == {
        hours_requirement: {"approved": 0.0, "target": 5.0}}


async def test_a_class_without_hours_requirements_has_no_hours(client, world, factory):
    program = await _program(factory, "sin-horas", sections=(("a", 1),))
    url = f"{CLUBS}/{world['club']['id']}/classes/{program['id']}"
    await client.post(f"{url}/enroll", json={"membership_ids": [world["s4"]["membership_id"]]},
                      headers=world["director"]["headers"])
    members = (await client.get(f"{url}/matrix", headers=world["director"]["headers"])).json()["members"]
    assert [row["hours"] for row in members] == [{}]


# ----------------------------------------------------------------------------
# 2. El resumen del mes del club
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def march(world):
    club, other = world["club"], world["other_club"]
    await _log(world["s2"], club, on=date(2025, 3, 5), quantity=3)
    await _log(world["s2"], club, on=date(2025, 3, 20), quantity=1.5)
    await _log(world["s2"], club, on=date(2025, 2, 10), quantity=2)          # the year, not the month
    await _log(world["s2"], club, on=date(2024, 12, 10), quantity=7)         # another year
    await _log(world["s2"], club, on=date(2025, 3, 7), quantity=1, status="SUBMITTED")
    await _log(world["s2"], club, on=date(2025, 3, 8), quantity=5, status="REJECTED")
    await _log(world["s2"], club, on=date(2025, 3, 1), quantity=1, category="ATTENDANCE")
    await _log(world["s2"], other, on=date(2025, 3, 9), quantity=6)          # done for another club
    await _log(world["s3"], club, on=date(2025, 3, 15), quantity=2)
    await _log(world["s1"], club, on=date(2025, 3, 31), quantity=0.5)
    await _log(world["s1"], club, on=date(2025, 4, 1), quantity=9)           # next month
    # Waiting for a decision, whatever its date (s4 has nothing else in this module).
    await _log(world["s4"], club, on=date(2024, 11, 2), quantity=2, status="SUBMITTED")


def _summary(client, world, actor="director", **params):
    return client.get(f"{CLUBS}/{world['club']['id']}/service-hours", params=params,
                      headers=world[actor]["headers"])


async def test_the_month_by_member_and_by_unit(client, world, march):
    response = await _summary(client, world, month="2025-03")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month"] == "2025-03"
    assert body["starts_on"] == "2025-03-01" and body["ends_on"] == "2025-03-31"
    members = {row["user_id"]: row for row in body["members"]}
    s2 = members[world["s2"]["id"]]
    assert s2["membership_id"] == world["s2"]["membership_id"]
    assert s2["unit_id"] == world["u1"]
    assert s2["service_month"] == 4.5
    assert s2["service_year"] == 6.5
    assert s2["attendance_month"] == 1.0
    assert s2["pending"] >= 1   # other tests of this module leave some of s2 waiting
    assert members[world["s3"]["id"]]["service_month"] == 2.0
    assert members[world["s1"]["id"]]["service_month"] == 0.5
    assert members[world["s4"]["id"]]["service_month"] == 0.0
    assert members[world["s4"]["id"]]["pending"] == 1
    # Totals only: never where a minor was.
    assert not {"description", "place"} & set(s2)
    units = {row["unit_id"]: row for row in body["units"]}
    assert units[world["u1"]]["service_month"] == 5.0
    assert units[world["u1"]]["members"] == 2
    assert units[world["u2"]]["service_month"] == 2.0
    assert body["service_month"] == 7.0


async def test_the_default_month_is_the_current_one(client, world):
    body = (await _summary(client, world)).json()
    assert body["month"] == date.today().strftime("%Y-%m")


async def test_who_reads_the_summary(client, world, march):
    secretary = await _summary(client, world, actor="secretary", month="2025-03")
    assert secretary.status_code == 200
    counselor = (await _summary(client, world, actor="counselor", month="2025-03")).json()
    assert {row["user_id"] for row in counselor["members"]} == {world["s1"]["id"], world["s2"]["id"]}
    assert (await _summary(client, world, actor="master", month="2025-03")).status_code == 200
    for actor in ("s2", "stranger", "director_b"):
        assert (await _summary(client, world, actor=actor, month="2025-03")).status_code == 403, actor
    assert (await _summary(client, world, month="2025-13")).status_code == 422
    assert (await _summary(client, world, month="marzo")).status_code == 422


# ----------------------------------------------------------------------------
# 3. La cola acotada a un club
# ----------------------------------------------------------------------------
async def test_the_queue_of_one_club(client, world):
    today = date.today().isoformat()
    body = {"category": "SERVICE", "performed_on": today, "quantity": 2,
            "description": "Visita al asilo"}
    mine = (await client.post(f"{ACTIVITY}/logs", json=body, headers=world["s3"]["headers"])).json()[0]
    theirs = (await client.post(f"{ACTIVITY}/logs", json=body,
                                headers=world["outsider"]["headers"])).json()[0]

    everything = (await client.get(f"{ACTIVITY}/queue", params={"limit": 500},
                                   headers=world["master"]["headers"])).json()
    assert {mine["id"], theirs["id"]} <= {row["id"] for row in everything}

    one = await client.get(f"{ACTIVITY}/queue", params={"club_id": world["club"]["id"]},
                           headers=world["master"]["headers"])
    assert one.status_code == 200
    ids = {row["id"] for row in one.json()}
    assert mine["id"] in ids and theirs["id"] not in ids

    # A director asking for another club's queue gets nothing of it.
    other = await client.get(f"{ACTIVITY}/queue", params={"club_id": world["other_club"]["id"]},
                             headers=world["director"]["headers"])
    assert other.status_code == 200 and other.json() == []
