"""Bloque E, incremento E5 — Unidades del club.

La unidad es una tabla ligera, NO un nodo del árbol: el miembro sigue colgando
del club (`users.organization_id`), que es lo que lee el RBAC del bloque A.

Lo que se prueba aquí: el cupo es duro y el tramo de edad sólo avisa; una unidad
y un miembro son siempre del mismo club; el consejero es un adulto ACTIVE del
club; y un consejero ve SÓLO a los miembros de sus unidades.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

CLUBS = "/api/v1/clubs"
MEMBERSHIPS = "/api/v1/memberships"

MINOR_BIRTH_DATE = date(2014, 5, 4)


def _units_table_exists() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(await db.scalar(text("SELECT to_regclass('public.club_units') IS NOT NULL")))
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _units_table_exists(),
        reason="apply migrations/008d_club_units.sql to the test database",
    ),
]
factory = module_factory("units")


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
    user["membership_id"] = str(membership_id)
    return str(membership_id)


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("other-club", "club", association)

    director = await factory.user("director", "CLUB_DIRECTOR", club["id"])
    secretary = await factory.user("secretary", "CLUB_SECRETARY", club["id"])
    instructor = await factory.user("instructor", "INSTRUCTOR", club["id"])
    counselor = await factory.user("counselor", "COUNSELOR", club["id"])
    outsider = await factory.user("outsider", "STUDENT", other_club["id"])
    other_director = await factory.user("other-director", "CLUB_DIRECTOR", other_club["id"])
    admin = await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"])
    for person, role in (
        (director, "CLUB_DIRECTOR"),
        (secretary, "CLUB_SECRETARY"),
        (instructor, "INSTRUCTOR"),
        (counselor, "COUNSELOR"),
    ):
        await _join(person, club, role)
    await _join(outsider, other_club, "STUDENT")
    await _join(other_director, other_club, "CLUB_DIRECTOR")
    return {
        "association": association,
        "club": club,
        "other_club": other_club,
        "director": director,
        "secretary": secretary,
        "instructor": instructor,
        "counselor": counselor,
        "outsider": outsider,
        "other_director": other_director,
        "admin": admin,
    }


async def _new_unit(client, world, actor=None, **payload) -> dict:
    body = {"name": f"Unidad {payload.pop('label', uuid.uuid4().hex[:6])}", **payload}
    response = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json=body,
        headers=(actor or world["director"])["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _member(factory, world, label: str, birth_date=None, **extra) -> dict:
    person = await factory.user(label, "STUDENT", world["club"]["id"], **extra)
    if birth_date is not None:
        await _set(person, birth_date=birth_date)
    await _join(person, world["club"], "STUDENT")
    return person


async def _assign(client, world, member: dict, unit_id: str | None, actor=None):
    return await client.put(
        f"{CLUBS}/{world['club']['id']}/members/{member['membership_id']}/unit",
        json={"unit_id": unit_id},
        headers=(actor or world["director"])["headers"],
    )


# ----------------------------------------------------------------------------
# Creating, listing and editing
# ----------------------------------------------------------------------------
async def test_director_creates_lists_and_edits_units(client, world, factory):
    created = await _new_unit(
        client, world, label="Halcones", min_age=10, max_age=11, capacity=2
    )
    assert created["name"] == "Unidad Halcones"
    assert created["min_age"] == 10 and created["max_age"] == 11
    assert created["capacity"] == 2 and created["members"] == 0
    assert created["counselor"] is None and created["status"] == "active"

    listed = await client.get(
        f"{CLUBS}/{world['club']['id']}/units", headers=world["director"]["headers"]
    )
    assert listed.status_code == 200, listed.text
    assert created["id"] in [row["id"] for row in listed.json()]

    renamed = await client.patch(
        f"{CLUBS}/{world['club']['id']}/units/{created['id']}",
        json={"name": "Unidad Águilas", "max_age": None},
        headers=world["director"]["headers"],
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Unidad Águilas"
    assert renamed.json()["max_age"] is None

    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'UNIT' AND entity_id = :id"
        " ORDER BY created_at",
        id=created["id"],
    )
    assert [row["action"] for row in audit] == ["UNIT_CREATE", "UNIT_UPDATE"]


async def test_two_active_units_cannot_share_a_name(client, world):
    unit = await _new_unit(client, world, label="Gemela")
    again = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": unit["name"].lower()},
        headers=world["director"]["headers"],
    )
    assert again.status_code == 409, again.text
    assert "ya existe" in again.json()["detail"].lower()


async def test_only_the_club_staff_manages_units(client, world):
    forbidden = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": "Unidad Ajena"},
        headers=world["other_director"]["headers"],
    )
    assert forbidden.status_code == 403, forbidden.text

    # An instructor reads the roster, so they read the units; they do not create them.
    unit = await _new_unit(client, world, label="Lectura")
    readable = await client.get(
        f"{CLUBS}/{world['club']['id']}/units", headers=world["instructor"]["headers"]
    )
    assert readable.status_code == 200
    assert unit["id"] in [row["id"] for row in readable.json()]
    denied = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": "Unidad Instructor"},
        headers=world["instructor"]["headers"],
    )
    assert denied.status_code == 403
    assert (
        await client.get(f"{CLUBS}/{world['club']['id']}/units", headers=world["outsider"]["headers"])
    ).status_code == 403


# ----------------------------------------------------------------------------
# Assigning members: hard capacity, age is only a warning
# ----------------------------------------------------------------------------
async def test_capacity_is_hard_and_age_only_warns(client, world, factory):
    unit = await _new_unit(client, world, label="Cupo", min_age=10, max_age=11, capacity=1)
    inside = await _member(factory, world, "cupo-a", birth_date=MINOR_BIRTH_DATE)
    outside = await _member(factory, world, "cupo-b")

    first = await _assign(client, world, inside, unit["id"])
    assert first.status_code == 200, first.text
    assert first.json()["unit_id"] == unit["id"]
    # 2014 is more than 11 years ago by the time this suite runs: a warning, not a refusal.
    assert first.json()["age_warning"] is True

    full = await _assign(client, world, outside, unit["id"])
    assert full.status_code == 409, full.text
    assert "llena" in full.json()["detail"].lower()

    # Raising the cap is one touch, and then the same call works.
    raised = await client.patch(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}",
        json={"capacity": 2},
        headers=world["director"]["headers"],
    )
    assert raised.status_code == 200, raised.text
    assert (await _assign(client, world, outside, unit["id"])).status_code == 200

    # And the cap can never be set below the people already in it.
    shrink = await client.patch(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}",
        json={"capacity": 1},
        headers=world["director"]["headers"],
    )
    assert shrink.status_code == 409, shrink.text

    listed = await client.get(
        f"{CLUBS}/{world['club']['id']}/units", headers=world["director"]["headers"]
    )
    row = next(item for item in listed.json() if item["id"] == unit["id"])
    assert row["members"] == 2

    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'MEMBERSHIP' AND entity_id = :id",
        id=inside["membership_id"],
    )
    assert "UNIT_ASSIGN" in [item["action"] for item in audit]


async def test_a_unit_and_a_member_are_always_of_the_same_club(client, world, factory):
    unit = await _new_unit(client, world, label="Propia")
    other = await client.post(
        f"{CLUBS}/{world['other_club']['id']}/units",
        json={"name": "Unidad Vecina"},
        headers=world["other_director"]["headers"],
    )
    assert other.status_code == 201, other.text

    member = await _member(factory, world, "cross")
    wrong_unit = await _assign(client, world, member, other.json()["id"])
    assert wrong_unit.status_code == 400, wrong_unit.text

    # ...and a membership of another club is not found in this one.
    foreign = await client.put(
        f"{CLUBS}/{world['club']['id']}/members/{world['outsider']['membership_id']}/unit",
        json={"unit_id": unit["id"]},
        headers=world["director"]["headers"],
    )
    assert foreign.status_code == 404, foreign.text


async def test_a_membership_that_is_not_active_takes_no_unit(client, world, factory):
    unit = await _new_unit(client, world, label="Inactiva")
    member = await _member(factory, world, "ended")
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE club_memberships SET status = 'ENDED' WHERE id = :id"),
            {"id": uuid.UUID(member["membership_id"])},
        )
        await db.commit()
    response = await _assign(client, world, member, unit["id"])
    assert response.status_code == 400, response.text


async def test_a_member_leaves_their_unit_with_null(client, world, factory):
    unit = await _new_unit(client, world, label="Salida")
    member = await _member(factory, world, "leaves")
    assert (await _assign(client, world, member, unit["id"])).status_code == 200
    cleared = await _assign(client, world, member, None)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["unit_id"] is None
    assert cleared.json()["age_warning"] is False


# ----------------------------------------------------------------------------
# The counselor
# ----------------------------------------------------------------------------
async def test_counselor_must_be_an_adult_active_member_of_the_club(client, world, factory):
    unit = await _new_unit(client, world, label="Consejo")
    minor = await _member(factory, world, "minor-counselor", is_minor=True)

    as_minor = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": minor["membership_id"]},
        headers=world["director"]["headers"],
    )
    assert as_minor.status_code == 400, as_minor.text

    # A STUDENT does not lead a unit either: the director grants COUNSELOR first.
    plain = await _member(factory, world, "plain-counselor")
    as_student = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": plain["membership_id"]},
        headers=world["director"]["headers"],
    )
    assert as_student.status_code == 400, as_student.text

    ok = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=world["director"]["headers"],
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["counselor"]["id"] == world["counselor"]["id"]

    cleared = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": None},
        headers=world["director"]["headers"],
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["counselor"] is None

    audit = await fetch_all(
        "SELECT action FROM audit_log WHERE entity_type = 'UNIT' AND entity_id = :id",
        id=unit["id"],
    )
    assert [row["action"] for row in audit].count("UNIT_COUNSELOR") == 2


async def test_the_secretary_never_appoints_a_counselor(client, world):
    unit = await _new_unit(client, world, label="Secretaria")
    # The secretary does manage units...
    created = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": "Unidad De Secretaría"},
        headers=world["secretary"]["headers"],
    )
    assert created.status_code == 201, created.text
    # ...but appointing somebody over a group of minors is the director's act.
    denied = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=world["secretary"]["headers"],
    )
    assert denied.status_code == 403, denied.text


async def test_a_counselor_only_sees_the_members_of_their_units(client, world, factory):
    mine = await _new_unit(client, world, label="Mía")
    theirs = await _new_unit(client, world, label="Otra")
    await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{mine['id']}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=world["director"]["headers"],
    )
    seen = await _member(factory, world, "seen")
    hidden = await _member(factory, world, "hidden")
    assert (await _assign(client, world, seen, mine["id"])).status_code == 200
    assert (await _assign(client, world, hidden, theirs["id"])).status_code == 200

    roster = await client.get(
        f"{CLUBS}/{world['club']['id']}/members", headers=world["counselor"]["headers"]
    )
    assert roster.status_code == 200, roster.text
    ids = [row["user_id"] for row in roster.json()]
    assert seen["id"] in ids
    assert hidden["id"] not in ids
    # Never the address a guardian was written to, and never a birth date.
    assert all("guardian_email" not in row for row in roster.json())
    assert all("birth_date" not in row for row in roster.json())

    # The director sees both, with the unit on each line.
    full = await client.get(
        f"{CLUBS}/{world['club']['id']}/members", headers=world["director"]["headers"]
    )
    rows = {row["user_id"]: row for row in full.json()}
    assert rows[seen["id"]]["unit"]["id"] == mine["id"]
    assert rows[hidden["id"]]["unit"]["name"] == theirs["name"]


# ----------------------------------------------------------------------------
# Archiving
# ----------------------------------------------------------------------------
async def test_archiving_needs_an_empty_unit_and_frees_the_name(client, world, factory):
    unit = await _new_unit(client, world, label="Archivo")
    member = await _member(factory, world, "archived-member")
    assert (await _assign(client, world, member, unit["id"])).status_code == 200

    busy = await client.delete(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}", headers=world["director"]["headers"]
    )
    assert busy.status_code == 409, busy.text

    assert (await _assign(client, world, member, None)).status_code == 200
    archived = await client.delete(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}", headers=world["director"]["headers"]
    )
    assert archived.status_code == 204, archived.text

    row = await fetch_one("SELECT status FROM club_units WHERE id = :id", id=unit["id"])
    assert row["status"] == "archived"  # nothing is deleted: the year's history stays
    listed = await client.get(
        f"{CLUBS}/{world['club']['id']}/units", headers=world["director"]["headers"]
    )
    assert unit["id"] not in [item["id"] for item in listed.json()]

    # The name is free again.
    reused = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": unit["name"]},
        headers=world["director"]["headers"],
    )
    assert reused.status_code == 201, reused.text


# ----------------------------------------------------------------------------
# An invitation may carry the unit
# ----------------------------------------------------------------------------
async def test_an_invitation_can_carry_the_unit(client, world, factory):
    unit = await _new_unit(client, world, label="Invitada", capacity=1)
    invitation = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "STUDENT", "unit_id": unit["id"]},
        headers=world["director"]["headers"],
    )
    assert invitation.status_code == 201, invitation.text
    token = invitation.json()["token"]

    guest = await factory.user("invited", "STUDENT")
    await _set(guest, verification_status="VERIFIED")
    accepted = await client.post(
        f"{MEMBERSHIPS}/invitations/accept", json={"token": token}, headers=guest["headers"]
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["unit"]["id"] == unit["id"]

    row = await fetch_one(
        "SELECT unit_id::text AS unit FROM club_memberships WHERE user_id = :id"
        " AND status = 'ACTIVE'",
        id=guest["id"],
    )
    assert row["unit"] == unit["id"]

    # A full unit does not stop anybody joining the club: they land without one.
    second = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "STUDENT", "unit_id": unit["id"]},
        headers=world["director"]["headers"],
    )
    latecomer = await factory.user("invited-late", "STUDENT")
    await _set(latecomer, verification_status="VERIFIED")
    joined = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": second.json()["token"]},
        headers=latecomer["headers"],
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["unit"] is None

    # An invitation cannot point at a unit of another club.
    foreign = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "STUDENT", "unit_id": str(uuid.uuid4())},
        headers=world["director"]["headers"],
    )
    assert foreign.status_code == 400, foreign.text


async def test_my_membership_shows_the_unit_and_its_counselor(client, world, factory):
    unit = await _new_unit(client, world, label="Mi Unidad")
    await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=world["director"]["headers"],
    )
    member = await _member(factory, world, "mine")
    assert (await _assign(client, world, member, unit["id"])).status_code == 200

    mine = await client.get(f"{MEMBERSHIPS}/me", headers=member["headers"])
    assert mine.status_code == 200, mine.text
    active = mine.json()["active"]
    assert active["unit"]["id"] == unit["id"]
    assert active["unit"]["name"] == unit["name"]
    assert active["counselor"]["id"] == world["counselor"]["id"]


async def test_ending_a_membership_clears_its_unit_and_its_counselor_post(client, world, factory):
    unit = await _new_unit(client, world, label="Baja")
    counselor = await factory.user("leaving-counselor", "COUNSELOR", world["club"]["id"])
    await _join(counselor, world["club"], "COUNSELOR")
    await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit['id']}/counselor",
        json={"membership_id": counselor["membership_id"]},
        headers=world["director"]["headers"],
    )
    member = await _member(factory, world, "removed")
    assert (await _assign(client, world, member, unit["id"])).status_code == 200

    removed = await client.post(
        f"{CLUBS}/{world['club']['id']}/members/{member['membership_id']}/remove",
        json={"reason": "Se mudó de ciudad"},
        headers=world["director"]["headers"],
    )
    assert removed.status_code == 200, removed.text
    row = await fetch_one(
        "SELECT unit_id FROM club_memberships WHERE id = :id", id=member["membership_id"]
    )
    assert row["unit_id"] is None

    left = await client.delete(f"{MEMBERSHIPS}/me", headers=counselor["headers"])
    assert left.status_code == 200, left.text
    unit_row = await fetch_one("SELECT counselor_id FROM club_units WHERE id = :id", id=unit["id"])
    assert unit_row["counselor_id"] is None
