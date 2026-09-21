"""Bloque E, incremento E2 — Núcleo de membresía de club.

`club_memberships` es el libro; `users.organization_id` sigue siendo la verdad
del RBAC y lo escribe UN SOLO servicio (`app/services/memberships.py`). Aquí se
prueba justo eso: que las dos cosas no se separen nunca, entre entrar, salir,
cambiar de rol, ser dado de baja, trasladarse y `PATCH /users/{id}`.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import (
    DB_AVAILABLE,
    DEFAULT_PASSWORD,
    fetch_all,
    fetch_one,
    module_factory,
    requires_db,
)

pytestmark = requires_db
factory = module_factory("membership")

AUTH = "/api/v1/auth"
USERS = "/api/v1/users"
CLUBS = "/api/v1/clubs"
MEMBERSHIPS = "/api/v1/memberships"

# A minor who is comfortably under 18 whatever year this suite runs in.
MINOR_BIRTH_DATE = date(2014, 5, 4)


def _years_since(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


BACKFILL = """
INSERT INTO club_memberships (id, user_id, club_id, role, status, source, started_at, created_at, updated_at)
SELECT gen_random_uuid(), u.id, u.organization_id, u.role, 'ACTIVE', 'BACKFILL',
       COALESCE(u.created_at, now()), now(), now()
FROM users u
JOIN organizations o ON o.id = u.organization_id
WHERE o.type = 'club'
  AND u.role IN ('STUDENT','COUNSELOR','INSTRUCTOR','CLUB_SECRETARY','CLUB_DIRECTOR')
  AND NOT EXISTS (
    SELECT 1 FROM club_memberships m WHERE m.user_id = u.id AND m.status = 'ACTIVE'
  )
"""


def _portfolio_tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                return bool(
                    await db.scalar(text("SELECT to_regclass('public.honor_enrollments') IS NOT NULL"))
                )
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


needs_portfolio = pytest.mark.skipif(
    not _portfolio_tables_exist(), reason="apply migrations/007_portfolio.sql to the test database"
)


async def _join(user: dict, club: dict, role: str = "STUDENT", source: str = "BACKFILL") -> str:
    """Attach an account the way the backfill does, without going through the API."""
    membership_id = uuid.uuid4()
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at) VALUES (:id, :user, :club, :role, 'ACTIVE', :source, now())"
            ),
            {
                "id": membership_id,
                "user": uuid.UUID(user["id"]),
                "club": uuid.UUID(club["id"]),
                "role": role,
                "source": source,
            },
        )
        await db.execute(
            text("UPDATE users SET organization_id = :club, role = :role WHERE id = :user"),
            {"club": uuid.UUID(club["id"]), "role": role, "user": uuid.UUID(user["id"])},
        )
        await db.commit()
    user["membership_id"] = str(membership_id)
    return str(membership_id)


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :id"),
            {**columns, "id": uuid.UUID(user["id"])},
        )
        await db.commit()


async def _memberships_of(user: dict) -> list:
    return await fetch_all(
        "SELECT club_id::text AS club, role, status, source, end_reason"
        " FROM club_memberships WHERE user_id = :id ORDER BY created_at, status",
        id=user["id"],
    )


async def _org_of(user: dict) -> dict:
    return await fetch_one(
        "SELECT organization_id::text AS org, role FROM users WHERE id = :id", id=user["id"]
    )


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    """An association with two clubs, their staff and an administrator above them."""
    association = await factory.org("assoc", "association")
    club_a = await factory.org("club-a", "club", association)
    club_b = await factory.org("club-b", "club", association)
    people = {
        "director": await factory.user("director-a", "CLUB_DIRECTOR", club_a["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", club_b["id"]),
        "instructor": await factory.user("instructor-a", "INSTRUCTOR", club_a["id"]),
        "secretary": await factory.user("secretary-a", "CLUB_SECRETARY", club_a["id"]),
        "member": await factory.user("member-a", "STUDENT", club_a["id"]),
        "minor": await factory.user("minor-a", "STUDENT", club_a["id"], is_minor=True),
        "admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "outsider": await factory.user("outsider", "STUDENT"),
    }
    for label in ("director", "director_b", "instructor", "secretary", "member", "minor"):
        role = {
            "director": "CLUB_DIRECTOR",
            "director_b": "CLUB_DIRECTOR",
            "instructor": "INSTRUCTOR",
            "secretary": "CLUB_SECRETARY",
        }.get(label, "STUDENT")
        club = club_b if label == "director_b" else club_a
        await _join(people[label], club, role)
    await _set(people["minor"], birth_date=MINOR_BIRTH_DATE)
    return {"association": association, "club_a": club_a, "club_b": club_b, **people}


# ----------------------------------------------------------------------------
# Relleno y lectura propia
# ----------------------------------------------------------------------------
async def test_backfill_is_idempotent(client, factory):
    club = await factory.org("backfill-club", "club")
    person = await factory.user("backfilled", "STUDENT", club["id"])
    async with SessionLocal() as db:
        for _ in range(2):
            await db.execute(text(BACKFILL))
            await db.commit()

    rows = await _memberships_of(person)
    assert [(row["status"], row["source"]) for row in rows] == [("ACTIVE", "BACKFILL")]
    assert rows[0]["club"] == club["id"]


async def test_membership_me_shows_the_active_club(client, world):
    response = await client.get(f"{MEMBERSHIPS}/me", headers=world["member"]["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"]["club"]["id"] == world["club_a"]["id"]
    assert body["active"]["role"] == "STUDENT"
    assert body["active"]["status"] == "ACTIVE"
    assert body["pending"] == []

    none_yet = await client.get(f"{MEMBERSHIPS}/me", headers=world["outsider"]["headers"])
    assert none_yet.status_code == 200
    assert none_yet.json()["active"] is None


# ----------------------------------------------------------------------------
# La invariante: organization_id ↔ una única membresía ACTIVE
# ----------------------------------------------------------------------------
async def test_only_one_active_membership_per_person(client, world, factory):
    """The partial unique index is the last line of defence behind the service."""
    person = await factory.user("double", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])
    with pytest.raises(Exception):
        await _join(person, world["club_b"])


async def test_leaving_clears_the_organization(client, world, factory):
    person = await factory.user("leaver", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])

    left = await client.delete(f"{MEMBERSHIPS}/me", headers=person["headers"])
    assert left.status_code == 200, left.text
    assert left.json()["end_reason"] == "LEFT"

    assert (await _org_of(person))["org"] is None
    rows = await _memberships_of(person)
    assert [(row["status"], row["end_reason"]) for row in rows] == [("ENDED", "LEFT")]
    audited = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE action = 'MEMBERSHIP_LEAVE' AND entity_id = :id",
        id=person["membership_id"],
    )
    assert audited["n"] == 1

    # Nothing left to leave.
    again = await client.delete(f"{MEMBERSHIPS}/me", headers=person["headers"])
    assert again.status_code == 404


async def test_the_only_director_cannot_leave(client, world):
    refused = await client.delete(f"{MEMBERSHIPS}/me", headers=world["director"]["headers"])
    assert refused.status_code == 409
    assert "director" in refused.json()["detail"].lower()
    assert (await _org_of(world["director"]))["org"] == world["club_a"]["id"]


async def test_club_only_roles_fall_back_to_student_on_the_way_out(client, world, factory):
    """COUNSELOR and CLUB_SECRETARY only exist inside a club; INSTRUCTOR is the person's own."""
    for role, expected in (("CLUB_SECRETARY", "STUDENT"), ("INSTRUCTOR", "INSTRUCTOR")):
        person = await factory.user(f"exit-{role.lower()}", role, world["club_a"]["id"])
        await _join(person, world["club_a"], role)
        left = await client.delete(f"{MEMBERSHIPS}/me", headers=person["headers"])
        assert left.status_code == 200, left.text
        row = await _org_of(person)
        assert row["org"] is None and row["role"] == expected


# ----------------------------------------------------------------------------
# PATCH /users/{id} delega en el servicio
# ----------------------------------------------------------------------------
async def test_admin_move_between_clubs_is_a_transfer(client, world, factory):
    person = await factory.user("moved", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])

    moved = await client.patch(
        f"{USERS}/{person['id']}",
        json={"organization_id": world["club_b"]["id"]},
        headers=world["admin"]["headers"],
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["organization_id"] == world["club_b"]["id"]

    rows = await _memberships_of(person)
    assert [(row["club"], row["status"], row["end_reason"]) for row in rows] == [
        (world["club_a"]["id"], "ENDED", "TRANSFERRED"),
        (world["club_b"]["id"], "ACTIVE", None),
    ]
    assert rows[1]["source"] == "ADMIN"
    audited = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE action = 'MEMBERSHIP_TRANSFER'"
        " AND entity_id = :id",
        id=person["id"],
    )
    assert audited["n"] == 1


async def test_admin_detaching_ends_the_membership(client, world, factory):
    person = await factory.user("detached", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])
    master = await factory.user("master-detach", "MASTER_GC")

    detached = await client.patch(
        f"{USERS}/{person['id']}", json={"organization_id": None}, headers=master["headers"]
    )
    assert detached.status_code == 200, detached.text
    assert (await _org_of(person))["org"] is None
    rows = await _memberships_of(person)
    assert [(row["status"], row["end_reason"]) for row in rows] == [("ENDED", "REMOVED")]


async def test_admin_role_change_follows_the_membership(client, world, factory):
    person = await factory.user("promoted", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])
    await _set(person, child_protection_completed=True)

    promoted = await client.patch(
        f"{USERS}/{person['id']}", json={"role": "INSTRUCTOR"}, headers=world["admin"]["headers"]
    )
    assert promoted.status_code == 200, promoted.text
    rows = await _memberships_of(person)
    assert [(row["status"], row["role"]) for row in rows] == [("ACTIVE", "INSTRUCTOR")]


async def test_becoming_an_administrator_ends_the_club_membership(client, world, factory):
    """Administrative accounts have no membership: their organization is their jurisdiction."""
    person = await factory.user("into-admin", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])
    master = await factory.user("master-admin", "MASTER_GC")

    promoted = await client.patch(
        f"{USERS}/{person['id']}",
        json={"role": "COORDINATOR_ZONE", "organization_id": world["association"]["id"]},
        headers=master["headers"],
    )
    assert promoted.status_code == 200, promoted.text
    row = await _org_of(person)
    assert row["org"] == world["association"]["id"] and row["role"] == "COORDINATOR_ZONE"
    rows = await _memberships_of(person)
    assert [(r["status"], r["end_reason"]) for r in rows] == [("ENDED", "REMOVED")]


# ----------------------------------------------------------------------------
# Nómina
# ----------------------------------------------------------------------------
async def test_roster_shows_age_never_the_birth_date(client, world):
    response = await client.get(
        f"{CLUBS}/{world['club_a']['id']}/members", headers=world["director"]["headers"]
    )
    assert response.status_code == 200, response.text
    rows = {row["user_id"]: row for row in response.json()}
    minor = rows[world["minor"]["id"]]
    assert minor["is_minor"] is True
    assert minor["age"] == _years_since(MINOR_BIRTH_DATE)
    assert "birth_date" not in minor and "email" not in minor
    # The director is the only one who sees who was asked for consent.
    assert "guardian_email" in minor
    assert rows[world["member"]["id"]]["role"] == "STUDENT"


async def test_roster_hides_guardian_email_from_the_rest_of_the_staff(client, world):
    instructor = await client.get(
        f"{CLUBS}/{world['club_a']['id']}/members", headers=world["instructor"]["headers"]
    )
    assert instructor.status_code == 200, instructor.text
    assert all("guardian_email" not in row for row in instructor.json())

    secretary = await client.get(
        f"{CLUBS}/{world['club_a']['id']}/members", headers=world["secretary"]["headers"]
    )
    assert secretary.status_code == 200
    assert all("guardian_email" not in row for row in secretary.json())


async def test_roster_is_closed_to_members_and_to_other_clubs(client, world):
    for who in ("member", "minor", "outsider", "director_b"):
        response = await client.get(
            f"{CLUBS}/{world['club_a']['id']}/members", headers=world[who]["headers"]
        )
        assert response.status_code == 403, f"{who}: {response.text}"


async def test_roster_filters_by_role_and_status(client, world):
    response = await client.get(
        f"{CLUBS}/{world['club_a']['id']}/members",
        params={"role": "INSTRUCTOR"},
        headers=world["director"]["headers"],
    )
    assert response.status_code == 200
    rows = response.json()
    assert world["instructor"]["id"] in [row["user_id"] for row in rows]
    assert {row["role"] for row in rows} == {"INSTRUCTOR"}
    assert {row["status"] for row in rows} == {"ACTIVE"}  # the default filter


# ----------------------------------------------------------------------------
# Cambio de rol y baja
# ----------------------------------------------------------------------------
async def test_director_changes_a_role_inside_the_club(client, world, factory):
    person = await factory.user("role-change", "STUDENT", world["club_a"]["id"])
    membership_id = await _join(person, world["club_a"])
    url = f"{CLUBS}/{world['club_a']['id']}/members/{membership_id}"

    changed = await client.patch(
        url, json={"role": "COUNSELOR"}, headers=world["director"]["headers"]
    )
    assert changed.status_code == 200, changed.text
    assert (await _org_of(person))["role"] == "COUNSELOR"

    # The directorship is not handed over this way (an administrator does it).
    refused = await client.patch(
        url, json={"role": "CLUB_DIRECTOR"}, headers=world["director"]["headers"]
    )
    assert refused.status_code == 403
    audited = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE action = 'MEMBERSHIP_ROLE_CHANGE'"
        " AND entity_id = :id",
        id=membership_id,
    )
    assert audited["n"] == 1


async def test_a_minor_only_ever_holds_student(client, world):
    url = f"{CLUBS}/{world['club_a']['id']}/members/{world['minor']['membership_id']}"
    refused = await client.patch(
        url, json={"role": "INSTRUCTOR"}, headers=world["director"]["headers"]
    )
    assert refused.status_code == 400
    assert "menor" in refused.json()["detail"].lower()


async def test_the_secretary_only_handles_students(client, world, factory):
    student = await factory.user("sec-student", "STUDENT", world["club_a"]["id"])
    student_membership = await _join(student, world["club_a"])
    helper = await factory.user("sec-instructor", "INSTRUCTOR", world["club_a"]["id"])
    helper_membership = await _join(helper, world["club_a"], "INSTRUCTOR")
    headers = world["secretary"]["headers"]
    base = f"{CLUBS}/{world['club_a']['id']}/members"

    # Staff is out of reach, both to grant and to remove.
    assert (
        await client.patch(
            f"{base}/{student_membership}", json={"role": "INSTRUCTOR"}, headers=headers
        )
    ).status_code == 403
    assert (
        await client.post(
            f"{base}/{helper_membership}/remove", json={"reason": "No corresponde"}, headers=headers
        )
    ).status_code == 403

    removed = await client.post(
        f"{base}/{student_membership}/remove",
        json={"reason": "Dejó de asistir este semestre"},
        headers=headers,
    )
    assert removed.status_code == 200, removed.text
    assert (await _org_of(student))["org"] is None


async def test_removal_needs_a_reason_and_never_targets_oneself(client, world):
    base = f"{CLUBS}/{world['club_a']['id']}/members"
    no_reason = await client.post(
        f"{base}/{world['member']['membership_id']}/remove",
        json={},
        headers=world["director"]["headers"],
    )
    assert no_reason.status_code == 422

    on_self = await client.post(
        f"{base}/{world['director']['membership_id']}/remove",
        json={"reason": "me voy"},
        headers=world["director"]["headers"],
    )
    assert on_self.status_code == 403


async def test_membership_of_another_club_is_not_found_here(client, world):
    response = await client.patch(
        f"{CLUBS}/{world['club_b']['id']}/members/{world['member']['membership_id']}",
        json={"role": "COUNSELOR"},
        headers=world["director_b"]["headers"],
    )
    assert response.status_code == 404


# ----------------------------------------------------------------------------
# Datos de control del club
# ----------------------------------------------------------------------------
async def test_club_profile_is_a_whitelist(client, world):
    url = f"{CLUBS}/{world['club_a']['id']}/profile"
    updated = await client.patch(
        url,
        json={"meeting_day": "sábado", "meeting_time": "16:00", "accepts_requests": False},
        headers=world["director"]["headers"],
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["profile"]["accepts_requests"] is False

    row = await fetch_one(
        "SELECT name, metadata_json FROM organizations WHERE id = :id", id=world["club_a"]["id"]
    )
    assert row["metadata_json"]["profile"]["meeting_day"] == "sábado"
    assert row["name"] == world["club_a"].get("name", row["name"])  # untouched

    refused = await client.patch(
        url, json={"meeting_day": "lunes"}, headers=world["member"]["headers"]
    )
    assert refused.status_code == 403


# ----------------------------------------------------------------------------
# El fundador y el portafolio
# ----------------------------------------------------------------------------
async def test_the_founder_gets_a_membership_with_the_club(client, factory):
    association = await factory.org("founder-assoc", "association")
    response = await client.post(
        f"{AUTH}/register",
        json={
            "email": factory.email("founder"),
            "password": DEFAULT_PASSWORD,
            "name": factory.name("founder"),
            "role": "CLUB_DIRECTOR",
            "club": {"name": factory.name("founder-club"), "association_id": association["id"]},
        },
    )
    assert response.status_code == 201, response.text
    rows = await _memberships_of({"id": response.json()["id"]})
    assert [(row["status"], row["source"], row["role"]) for row in rows] == [
        ("ACTIVE", "FOUNDER", "CLUB_DIRECTOR")
    ]
    assert rows[0]["club"] == response.json()["organization_id"]


@needs_portfolio
async def test_open_enrollments_follow_the_member_to_the_new_club(client, world, factory):
    person = await factory.user("portfolio-mover", "STUDENT", world["club_a"]["id"])
    await _join(person, world["club_a"])
    async with SessionLocal() as db:
        # One honor per enrollment: a person has a single live enrollment per honor.
        for status_name in ("IN_PROGRESS", "READY", "CERTIFIED"):
            honor_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO honors (id, name, slug, active, status) VALUES"
                    " (:id, :name, :slug, true, 'PUBLISHED')"
                ),
                {
                    "id": honor_id,
                    "name": factory.name(f"honor-{status_name}"),
                    "slug": f"{factory.prefix}-honor-{status_name.lower()}",
                },
            )
            await db.execute(
                text(
                    "INSERT INTO honor_enrollments (id, user_id, honor_id, club_id, status)"
                    " VALUES (gen_random_uuid(), :user, :honor, :club, :status)"
                ),
                {
                    "user": uuid.UUID(person["id"]),
                    "honor": honor_id,
                    "club": uuid.UUID(world["club_a"]["id"]),
                    "status": status_name,
                },
            )
        await db.commit()

    moved = await client.patch(
        f"{USERS}/{person['id']}",
        json={"organization_id": world["club_b"]["id"]},
        headers=world["admin"]["headers"],
    )
    assert moved.status_code == 200, moved.text

    rows = await fetch_all(
        "SELECT status, club_id::text AS club FROM honor_enrollments WHERE user_id = :id",
        id=person["id"],
    )
    moved_clubs = {row["status"]: row["club"] for row in rows}
    assert moved_clubs["IN_PROGRESS"] == world["club_b"]["id"]
    assert moved_clubs["READY"] == world["club_b"]["id"]
    # Certified work is frozen with the club that issued it.
    assert moved_clubs["CERTIFIED"] == world["club_a"]["id"]

    # Leaving altogether leaves the queue without a club, as flow 5 of block A says.
    left = await client.delete(f"{MEMBERSHIPS}/me", headers=person["headers"])
    assert left.status_code == 200, left.text
    after = await fetch_all(
        "SELECT status, club_id::text AS club FROM honor_enrollments WHERE user_id = :id",
        id=person["id"],
    )
    assert {row["club"] for row in after if row["status"] != "CERTIFIED"} == {None}
