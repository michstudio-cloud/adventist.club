"""Bloque E, incremento E8 — Secretaría de club (`CLUB_SECRETARY`).

La matriz entera de §5.7, en los dos sentidos: lo que la Secretaría SÍ hace
(nómina recortada, invitaciones y solicitudes de `STUDENT`, unidades, bajas con
motivo, datos de control) y lo que NO (dictaminar, certificar, ver portafolios,
conceder roles de personal, nombrar consejeros, ver `guardian_email` o fechas de
nacimiento, cambiar el nombre o la ubicación del club, usar `GET /users`).

La Secretaría de Asociación de la visión (reasignar cargos entre clubes,
puntajes) es otro bloque (decisión D1).
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DB_AVAILABLE, fetch_one, module_factory, requires_db

CLUBS = "/api/v1/clubs"
MEMBERSHIPS = "/api/v1/memberships"
USERS = "/api/v1/users"
ORG = "/api/v1/org-nodes"
PORTFOLIO = "/api/v1/portfolio"

MINOR_BIRTH_DATE = date(2014, 5, 4)


def _table_exists(name: str) -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(await db.scalar(text(f"SELECT to_regclass('public.{name}') IS NOT NULL")))
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _table_exists("club_units"), reason="apply migrations/008d_club_units.sql"
    ),
]
factory = module_factory("secretary")


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :id"),
            {**columns, "id": uuid.UUID(user["id"])},
        )
        await db.commit()


async def _join(user: dict, club: dict, role: str) -> str:
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
    people = {
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "secretary": await factory.user("secretary", "CLUB_SECRETARY", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "counselor": await factory.user("counselor", "COUNSELOR", club["id"]),
        "student": await factory.user("student", "STUDENT", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
    }
    for label, role in (
        ("director", "CLUB_DIRECTOR"),
        ("secretary", "CLUB_SECRETARY"),
        ("instructor", "INSTRUCTOR"),
        ("counselor", "COUNSELOR"),
        ("student", "STUDENT"),
        ("minor", "STUDENT"),
    ):
        await _join(people[label], club, role)
    await _set(people["minor"], birth_date=MINOR_BIRTH_DATE)
    await _set(people["secretary"], verification_status="VERIFIED")
    return {**people, "association": association, "club": club}


def _headers(world, label):
    return world[label]["headers"]


# ----------------------------------------------------------------------------
# Appointing one
# ----------------------------------------------------------------------------
async def test_the_director_appoints_the_secretary_with_a_nominal_link(client, world, factory):
    guest = await factory.user("appointed", "STUDENT")
    await _set(guest, verification_status="VERIFIED")

    # A staff role is always single-use AND nominal: a link in a group chat
    # must never hand out authority over minors.
    anonymous = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "CLUB_SECRETARY"},
        headers=_headers(world, "director"),
    )
    assert anonymous.status_code == 422, anonymous.text
    multi = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "CLUB_SECRETARY", "email": guest["email"], "max_uses": 5},
        headers=_headers(world, "director"),
    )
    assert multi.status_code == 422, multi.text

    created = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "CLUB_SECRETARY", "email": guest["email"]},
        headers=_headers(world, "director"),
    )
    assert created.status_code == 201, created.text
    accepted = await client.post(
        f"{MEMBERSHIPS}/invitations/accept",
        json={"token": created.json()["token"]},
        headers=guest["headers"],
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "CLUB_SECRETARY"
    row = await fetch_one("SELECT role FROM users WHERE id = :id", id=guest["id"])
    assert row["role"] == "CLUB_SECRETARY"


async def test_the_secretary_never_hands_out_a_staff_role(client, world, factory):
    other = await factory.user("no-staff", "STUDENT")
    for role in ("CLUB_SECRETARY", "INSTRUCTOR", "COUNSELOR"):
        denied = await client.post(
            f"{CLUBS}/{world['club']['id']}/invitations",
            json={"role": role, "email": other["email"]},
            headers=_headers(world, "secretary"),
        )
        assert denied.status_code == 403, (role, denied.text)

    students = await client.post(
        f"{CLUBS}/{world['club']['id']}/invitations",
        json={"role": "STUDENT", "max_uses": 20},
        headers=_headers(world, "secretary"),
    )
    assert students.status_code == 201, students.text

    # ...and it does not promote anybody from the roster either.
    for role in ("INSTRUCTOR", "CLUB_SECRETARY", "CLUB_DIRECTOR"):
        promoted = await client.patch(
            f"{CLUBS}/{world['club']['id']}/members/{world['student']['membership_id']}",
            json={"role": role},
            headers=_headers(world, "secretary"),
        )
        assert promoted.status_code == 403, (role, promoted.text)


# ----------------------------------------------------------------------------
# What the secretary does
# ----------------------------------------------------------------------------
async def test_the_secretary_runs_the_roster_without_the_private_data(client, world):
    roster = await client.get(
        f"{CLUBS}/{world['club']['id']}/members", headers=_headers(world, "secretary")
    )
    assert roster.status_code == 200, roster.text
    rows = {row["user_id"]: row for row in roster.json()}
    assert world["minor"]["id"] in rows
    minor_row = rows[world["minor"]["id"]]
    # Years, never a birth date; never an e-mail; never the guardian's address.
    assert minor_row["age"] == date.today().year - MINOR_BIRTH_DATE.year - (
        (date.today().month, date.today().day) < (MINOR_BIRTH_DATE.month, MINOR_BIRTH_DATE.day)
    )
    assert "guardian_email" not in minor_row
    assert "birth_date" not in minor_row and "email" not in minor_row

    # The director does see the address the consent was asked at.
    managed = await client.get(
        f"{CLUBS}/{world['club']['id']}/members", headers=_headers(world, "director")
    )
    assert all("guardian_email" in row for row in managed.json())


async def test_the_secretary_manages_units_but_not_their_counselors(client, world):
    unit = await client.post(
        f"{CLUBS}/{world['club']['id']}/units",
        json={"name": "Unidad De Secretaría", "capacity": 4},
        headers=_headers(world, "secretary"),
    )
    assert unit.status_code == 201, unit.text
    unit_id = unit.json()["id"]

    edited = await client.patch(
        f"{CLUBS}/{world['club']['id']}/units/{unit_id}",
        json={"capacity": 6},
        headers=_headers(world, "secretary"),
    )
    assert edited.status_code == 200, edited.text

    assigned = await client.put(
        f"{CLUBS}/{world['club']['id']}/members/{world['minor']['membership_id']}/unit",
        json={"unit_id": unit_id},
        headers=_headers(world, "secretary"),
    )
    assert assigned.status_code == 200, assigned.text

    denied = await client.put(
        f"{CLUBS}/{world['club']['id']}/units/{unit_id}/counselor",
        json={"membership_id": world["counselor"]["membership_id"]},
        headers=_headers(world, "secretary"),
    )
    assert denied.status_code == 403, denied.text

    # Emptying it first, the secretary may archive it.
    await client.put(
        f"{CLUBS}/{world['club']['id']}/members/{world['minor']['membership_id']}/unit",
        json={"unit_id": None},
        headers=_headers(world, "secretary"),
    )
    archived = await client.delete(
        f"{CLUBS}/{world['club']['id']}/units/{unit_id}", headers=_headers(world, "secretary")
    )
    assert archived.status_code == 204, archived.text


async def test_the_secretary_decides_only_on_student_requests(client, world, factory):
    asking_student = await factory.user("asking-student", "STUDENT")
    await _set(asking_student, verification_status="VERIFIED")
    asking_instructor = await factory.user("asking-instructor", "INSTRUCTOR")
    await _set(asking_instructor, verification_status="VERIFIED")
    for person in (asking_student, asking_instructor):
        asked = await client.post(
            f"{MEMBERSHIPS}/requests",
            json={"club_id": world["club"]["id"]},
            headers=person["headers"],
        )
        assert asked.status_code == 201, asked.text
        person["membership_id"] = asked.json()["membership_id"]

    denied = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{asking_instructor['membership_id']}/approve",
        json={},
        headers=_headers(world, "secretary"),
    )
    assert denied.status_code == 403, denied.text

    approved = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{asking_student['membership_id']}/approve",
        json={},
        headers=_headers(world, "secretary"),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["role"] == "STUDENT"

    # And the role it grants is never a bigger one.
    upgraded = await client.post(
        f"{CLUBS}/{world['club']['id']}/requests/{asking_instructor['membership_id']}/reject",
        json={"reason": "Faltan datos"},
        headers=_headers(world, "secretary"),
    )
    assert upgraded.status_code == 403, upgraded.text


async def test_the_secretary_removes_a_student_but_never_staff_nor_itself(client, world, factory):
    leaving = await factory.user("to-remove", "STUDENT", world["club"]["id"])
    await _join(leaving, world["club"], "STUDENT")

    removed = await client.post(
        f"{CLUBS}/{world['club']['id']}/members/{leaving['membership_id']}/remove",
        json={"reason": "Se mudó de ciudad"},
        headers=_headers(world, "secretary"),
    )
    assert removed.status_code == 200, removed.text

    for label in ("instructor", "counselor", "director", "secretary"):
        denied = await client.post(
            f"{CLUBS}/{world['club']['id']}/members/{world[label]['membership_id']}/remove",
            json={"reason": "Sin motivo real"},
            headers=_headers(world, "secretary"),
        )
        assert denied.status_code == 403, (label, denied.text)


async def test_the_secretary_edits_the_control_data_and_nothing_else_of_the_club(client, world):
    profile = await client.patch(
        f"{CLUBS}/{world['club']['id']}/profile",
        json={"meeting_day": "sábado", "accepts_requests": False},
        headers=_headers(world, "secretary"),
    )
    assert profile.status_code == 200, profile.text
    assert profile.json()["profile"]["accepts_requests"] is False
    # Put it back: other tests in this module ask to join.
    await client.patch(
        f"{CLUBS}/{world['club']['id']}/profile",
        json={"accepts_requests": True},
        headers=_headers(world, "secretary"),
    )

    for payload in ({"name": "Club Renombrado"}, {"city": "Otra"}):
        denied = await client.patch(
            f"{ORG}/{world['club']['id']}", json=payload, headers=_headers(world, "secretary")
        )
        assert denied.status_code == 403, (payload, denied.text)
    placement = await client.post(
        f"{ORG}/clubs/{world['club']['id']}/place",
        json={"zone_name": "Zona Inventada", "church_name": "Iglesia Inventada"},
        headers=_headers(world, "secretary"),
    )
    assert placement.status_code == 403, placement.text


# ----------------------------------------------------------------------------
# What the secretary never does
# ----------------------------------------------------------------------------
async def test_the_secretary_is_outside_every_portfolio_permission(world):
    from app.models import HonorEnrollment, User
    from app.rbac import can_issue, can_review, can_view_portfolio, can_view_user

    async with SessionLocal() as db:
        secretary = await db.get(User, uuid.UUID(world["secretary"]["id"]))
        minor = await db.get(User, uuid.UUID(world["minor"]["id"]))
        enrollment = HonorEnrollment(
            id=uuid.uuid4(),
            user_id=minor.id,
            honor_id=uuid.uuid4(),
            mode="CLUB",
            club_id=uuid.UUID(world["club"]["id"]),
            status="IN_PROGRESS",
        )
        assert await can_review(db, secretary, enrollment) is False
        assert await can_issue(db, secretary, enrollment) is False
        assert await can_view_user(db, secretary, minor) is False
        assert await can_view_portfolio(db, secretary, minor) is False


async def test_the_secretary_cannot_read_the_user_directory(client, world):
    listed = await client.get(USERS, headers=_headers(world, "secretary"))
    assert listed.status_code == 200, listed.text
    # `GET /users` is closed to anybody outside MEMBER_VIEW_ROLES: they see
    # themselves and nobody else.
    assert [row["id"] for row in listed.json()] == [world["secretary"]["id"]]

    detail = await client.get(
        f"{USERS}/{world['minor']['id']}", headers=_headers(world, "secretary")
    )
    assert detail.status_code == 403, detail.text


async def test_the_secretary_never_reads_a_letter(client, world, factory):
    """The director and the secretary see the STATE of the staff's verification
    in the roster, never the signed document (spec §5.6)."""
    async with SessionLocal() as db:
        letter_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO church_letters (id, user_id, role_requested, organization_id,"
                " church_name, storage_key, content_type, size_bytes, status)"
                " VALUES (:id, :user, 'INSTRUCTOR', :org, 'Iglesia Central', :key,"
                " 'application/pdf', 1024, 'SUBMITTED')"
            ),
            {
                "id": letter_id,
                "user": uuid.UUID(world["instructor"]["id"]),
                "org": uuid.UUID(world["club"]["id"]),
                "key": f"letters/{world['instructor']['id']}/{letter_id}.pdf",
            },
        )
        await db.commit()

    for label in ("secretary", "director"):
        denied = await client.get(
            f"/api/v1/church-letters/{letter_id}/url", headers=_headers(world, label)
        )
        assert denied.status_code in (403, 503), (label, denied.text)
    queue = await client.get(
        "/api/v1/church-letters/queue", headers=_headers(world, "secretary")
    )
    assert queue.status_code == 403, queue.text
