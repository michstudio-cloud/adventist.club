"""Alta directa de clubes por la administración.

Problema del responsable: «No puedo agregar clubes manuales, conectándolos a
asociaciones». Hasta ahora sólo un CLUB_DIRECTOR podía pedir un club (queda
`pending`) y la asociación lo aprobaba y ubicaba. Aquí la administración
(asociación, unión, división o MASTER_GC) crea el club ya ACTIVO, conectado a
su asociación y, si quiere, ubicado en zona e iglesia y con director.

Lo que se prueba: el alcance (nadie crea fuera de su subárbol, el coordinador
de zona sólo lee), los mismos efectos que la aprobación (club activo, director
con su membresía y `club_approval`, auditoría), y el listado con filtros,
búsqueda sin acentos y `X-Total-Count`.
"""

import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("adminclubs")

ORG = "/api/v1/org-nodes"
ADMIN_CLUBS = f"{ORG}/clubs/admin"


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    association = await factory.org("assoc", "association", union)
    zone_a = await factory.org("zona-a", "zone", association)
    church_a = await factory.org("iglesia-a", "church", zone_a)
    other_association = await factory.org("otra-assoc", "association", union)
    other_union = await factory.org("otra-uni", "union", division)

    director_club = await factory.org("club-existente", "club", church_a)
    return {
        "division": division,
        "union": union,
        "association": association,
        "zone_a": zone_a,
        "church_a": church_a,
        "other_association": other_association,
        "director_club": director_club,
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "union_admin": await factory.user("union-admin", "ADMIN_UNION", union["id"]),
        "other_union_admin": await factory.user("otra-union-admin", "ADMIN_UNION", other_union["id"]),
        "other_admin": await factory.user(
            "other-admin", "ADMIN_ASSOCIATION", other_association["id"]
        ),
        "coord_a": await factory.user("coord-a", "COORDINATOR_ZONE", zone_a["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "director": await factory.user("director", "CLUB_DIRECTOR", director_club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", director_club["id"]),
    }


async def _node(node_id: str) -> dict:
    return await fetch_one(
        "SELECT id::text AS id, parent_id::text AS parent, path::text AS path, type, status,"
        " name, code, city, country FROM organizations WHERE id = :id",
        id=uuid.UUID(node_id),
    )


async def _create(client, world, actor: str = "assoc_admin", **body):
    # Every club the administration opens names its ministry (019_club_ministry.sql).
    payload = {"association_id": world["association"]["id"], "ministry": "pathfinders", **body}
    return await client.post(ADMIN_CLUBS, json=payload, headers=world[actor]["headers"])


def _code(label: str) -> str:
    # organizations.code is globally unique: every run needs its own.
    return f"{label}-{uuid.uuid4().hex[:8]}".upper()


# ----------------------------------------------------------------------------
# Creating
# ----------------------------------------------------------------------------
async def test_a_club_with_only_its_association_is_active_and_waits_to_be_placed(
    client, factory, world
):
    response = await _create(
        client,
        world,
        name=factory.name("club-solo-asociacion"),
        code=_code("solo"),
        city="Reynosa",
        state="Tamaulipas",
        latitude=26.08,
        longitude=-98.28,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["type"] == "CLUB"
    assert body["association"]["id"] == world["association"]["id"]
    assert body["zone"] is None and body["church"] is None
    assert body["director"] is None
    assert body["requested_by"] is None
    assert body["city"] == "Reynosa" and body["state"] == "Tamaulipas"
    assert body["latitude"] == 26.08

    row = await _node(body["id"])
    assert row["parent"] == world["association"]["id"]
    assert row["path"] == f"{world['association']['path']}.{uuid.UUID(body['id']).hex}"
    assert row["status"] == "active"

    unplaced = await client.get(f"{ORG}/unplaced-clubs", headers=world["assoc_admin"]["headers"])
    assert body["id"] in [item["id"] for item in unplaced.json()]

    audit = await fetch_one(
        "SELECT action, entity_type, metadata_json FROM audit_log"
        " WHERE entity_id = :id AND action = 'CREATE'",
        id=body["id"],
    )
    assert audit["entity_type"] == "ORGANIZATION"
    assert audit["metadata_json"]["via"] == "admin"
    assert audit["metadata_json"]["association_id"] == world["association"]["id"]

    # It is public like any other active club.
    public = await client.get(f"{ORG}/{body['id']}")
    assert public.status_code == 200, public.text


async def test_zone_and_church_by_name_are_created_and_the_club_is_placed(client, factory, world):
    response = await _create(
        client,
        world,
        name=factory.name("club-ubicado"),
        zone_name=factory.name("zona-nueva"),
        church_name=factory.name("iglesia-nueva"),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    zone = await _node(body["zone"]["id"])
    church = await _node(body["church"]["id"])
    club = await _node(body["id"])
    assert zone["type"] == "zone" and zone["parent"] == world["association"]["id"]
    assert zone["name"] == factory.name("zona-nueva")
    assert church["type"] == "church" and church["parent"] == zone["id"]
    assert club["parent"] == church["id"]
    assert club["path"] == f"{church['path']}.{uuid.UUID(body['id']).hex}"
    assert body["association"]["id"] == world["association"]["id"]

    # Placed: it is not waiting in the unplaced list.
    unplaced = await client.get(f"{ORG}/unplaced-clubs", headers=world["assoc_admin"]["headers"])
    assert body["id"] not in [item["id"] for item in unplaced.json()]

    # The structure it created is audited too.
    for node_id in (zone["id"], church["id"]):
        created = await fetch_one(
            "SELECT id FROM audit_log WHERE entity_id = :id AND action = 'CREATE'", id=node_id
        )
        assert created is not None


async def test_an_existing_church_places_the_club_under_it(client, factory, world):
    both = await _create(
        client,
        world,
        name=factory.name("club-iglesia-id"),
        zone_id=world["zone_a"]["id"],
        church_id=world["church_a"]["id"],
    )
    assert both.status_code == 201, both.text
    assert (await _node(both.json()["id"]))["parent"] == world["church_a"]["id"]
    assert both.json()["zone"]["id"] == world["zone_a"]["id"]
    assert both.json()["church"]["id"] == world["church_a"]["id"]

    # The church alone is enough when it already has a zone.
    alone = await _create(
        client, world, name=factory.name("club-solo-iglesia"), church_id=world["church_a"]["id"]
    )
    assert alone.status_code == 201, alone.text
    assert (await _node(alone.json()["id"]))["parent"] == world["church_a"]["id"]
    assert alone.json()["zone"]["id"] == world["zone_a"]["id"]


async def test_a_placement_conflict_leaves_nothing_behind(client, factory, world):
    """The church already exists in zone A: typing it under a new zone is the
    409 of E6 (pick the row that exists), and neither the club nor the zone
    it would have created survive."""
    clash = await _create(
        client,
        world,
        name=factory.name("club-choque"),
        zone_name=factory.name("zona-choque"),
        church_name=factory.name("iglesia-a").upper(),
    )
    assert clash.status_code == 409, clash.text
    assert world["church_a"]["id"] in clash.json()["detail"]
    for label in ("club-choque", "zona-choque"):
        assert await fetch_one(
            "SELECT id FROM organizations WHERE name = :name", name=factory.name(label)
        ) is None


async def test_a_church_name_without_a_zone_is_only_declared(client, factory, world):
    response = await _create(
        client, world, name=factory.name("club-declarado"), church_name=factory.name("iglesia-x")
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (await _node(body["id"]))["parent"] == world["association"]["id"]
    assert body["church"] is None
    assert body["declared"]["church_name"] == factory.name("iglesia-x")
    # Nothing was created in the map without a zone to hang it from.
    assert await fetch_one(
        "SELECT id FROM organizations WHERE name = :name", name=factory.name("iglesia-x")
    ) is None


async def test_a_zone_alone_hangs_the_club_from_the_zone(client, factory, world):
    response = await _create(
        client, world, name=factory.name("club-solo-zona"), zone_id=world["zone_a"]["id"]
    )
    assert response.status_code == 201, response.text
    assert (await _node(response.json()["id"]))["parent"] == world["zone_a"]["id"]
    assert response.json()["zone"]["id"] == world["zone_a"]["id"]
    assert response.json()["church"] is None


# ----------------------------------------------------------------------------
# Who may
# ----------------------------------------------------------------------------
async def test_only_the_administration_in_scope_creates_clubs(client, factory, world):
    outsider = await _create(client, world, "other_admin", name=factory.name("club-ajeno"))
    assert outsider.status_code == 403, outsider.text
    far = await _create(client, world, "other_union_admin", name=factory.name("club-lejano"))
    assert far.status_code == 403, far.text
    for actor in ("coord_a", "director", "instructor"):
        denied = await _create(client, world, actor, name=factory.name(f"club-{actor}"))
        assert denied.status_code == 403, (actor, denied.text)

    from_union = await _create(client, world, "union_admin", name=factory.name("club-union"))
    assert from_union.status_code == 201, from_union.text
    from_master = await _create(client, world, "master", name=factory.name("club-master"))
    assert from_master.status_code == 201, from_master.text

    # Nothing was created by the refused calls.
    leftovers = await fetch_all(
        "SELECT id FROM organizations WHERE name IN (:a, :b, :c)",
        a=factory.name("club-ajeno"),
        b=factory.name("club-coord_a"),
        c=factory.name("club-director"),
    )
    assert leftovers == []


async def test_club_staff_cannot_list_either(client, world):
    for actor in ("director", "instructor"):
        denied = await client.get(ADMIN_CLUBS, headers=world[actor]["headers"])
        assert denied.status_code == 403, (actor, denied.text)
    assert (await client.get(ADMIN_CLUBS)).status_code == 401


# ----------------------------------------------------------------------------
# What is refused
# ----------------------------------------------------------------------------
async def test_bad_input_is_refused(client, factory, world):
    not_association = await client.post(
        ADMIN_CLUBS,
        json={
            "name": factory.name("club-x"),
            "association_id": world["zone_a"]["id"],
            "ministry": "pathfinders",
        },
        headers=world["master"]["headers"],
    )
    assert not_association.status_code == 400, not_association.text
    assert not_association.json()["detail"] == "association_not_found"
    missing = await client.post(
        ADMIN_CLUBS,
        json={
            "name": factory.name("club-x"),
            "association_id": str(uuid.uuid4()),
            "ministry": "pathfinders",
        },
        headers=world["master"]["headers"],
    )
    assert missing.status_code == 400, missing.text
    assert missing.json()["detail"] == "association_not_found"

    for body in (
        {"name": "x"},
        {"name": factory.name("club-x"), "zone_id": world["zone_a"]["id"], "zone_name": "Z"},
        {"name": factory.name("club-x"), "church_id": world["church_a"]["id"], "church_name": "I"},
        {"name": factory.name("club-x"), "latitude": 10.0},
        {"name": factory.name("club-x"), "longitude": 200.0, "latitude": 10.0},
        {"name": factory.name("club-x"), "director_email": "not-an-email"},
        {"name": factory.name("club-x"), "code": "C" * 61},
        {"name": factory.name("club-x"), "city": "C" * 121},
        {"name": factory.name("club-x"), "surprise": True},
    ):
        invalid = await _create(client, world, **body)
        assert invalid.status_code == 422, (body, invalid.text)


async def test_a_code_already_in_use_is_a_conflict(client, factory, world):
    code = _code("dup")
    first = await _create(client, world, name=factory.name("club-codigo-1"), code=code)
    assert first.status_code == 201, first.text
    again = await _create(client, world, name=factory.name("club-codigo-2"), code=code.lower())
    assert again.status_code == 409, again.text
    assert again.json()["detail"] == "club_code_taken"


async def test_a_name_already_in_use_in_the_association_is_a_conflict(client, factory, world):
    first = await _create(client, world, name=factory.name("club-nombre"))
    assert first.status_code == 201, first.text
    again = await _create(client, world, name=factory.name("CLUB-NOMBRE"))
    assert again.status_code == 409, again.text
    assert again.json()["detail"] == "club_name_taken"


# ----------------------------------------------------------------------------
# The director
# ----------------------------------------------------------------------------
async def test_the_director_is_appointed_exactly_like_an_approval(client, factory, world):
    person = await factory.user("nuevo-director", "STUDENT")
    response = await _create(
        client,
        world,
        name=factory.name("club-con-director"),
        director_email=person["email"].upper(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["director"] == {
        "id": person["id"],
        "name": factory.name("nuevo-director"),
        "email": person["email"],
    }

    user = await fetch_one(
        "SELECT role, organization_id::text AS org, club_approval, club_approval_at"
        " FROM users WHERE id = :id",
        id=uuid.UUID(person["id"]),
    )
    assert user["role"] == "CLUB_DIRECTOR"
    assert user["org"] == body["id"]
    assert user["club_approval"] == "APPROVED"
    assert user["club_approval_at"] is not None

    memberships = await fetch_all(
        "SELECT club_id::text AS club, role, status, source FROM club_memberships"
        " WHERE user_id = :id",
        id=uuid.UUID(person["id"]),
    )
    assert [dict(row) for row in memberships] == [
        {"club": body["id"], "role": "CLUB_DIRECTOR", "status": "ACTIVE", "source": "ADMIN"}
    ]

    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CLUB_DIRECTOR_ASSIGN'",
        id=body["id"],
    )
    assert audit["metadata_json"]["director_id"] == person["id"]
    assert audit["metadata_json"]["via"] == "admin"

    # The director now manages their club.
    roster = await client.get(f"/api/v1/clubs/{body['id']}/members", headers=person["headers"])
    assert roster.status_code == 200, roster.text

    # ...and cannot be handed a second one.
    second = await _create(
        client, world, name=factory.name("club-segundo"), director_email=person["email"]
    )
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "director_has_club"


async def test_director_problems_are_refused(client, factory, world):
    # Bloque I §1.2: an address without an account is no longer refused (it is invited:
    # see tests/test_role_assignments.py); everything else still is.
    busy = await _create(
        client,
        world,
        name=factory.name("club-ocupado"),
        director_email=world["director"]["email"],
    )
    assert busy.status_code == 409, busy.text
    assert busy.json()["detail"] == "director_has_club"

    # An administrator is never demoted into a director by this path.
    admin = await _create(
        client,
        world,
        name=factory.name("club-admin-director"),
        director_email=world["other_admin"]["email"],
    )
    assert admin.status_code == 400, admin.text
    assert admin.json()["detail"] == "director_not_eligible"

    # A member of a club outside the caller's scope is not theirs to move.
    foreign_club = await factory.org("club-ajeno-2", "club", world["other_association"])
    foreigner = await factory.user("ajeno", "STUDENT", foreign_club["id"])
    moved = await _create(
        client, world, name=factory.name("club-robado"), director_email=foreigner["email"]
    )
    assert moved.status_code == 403, moved.text
    assert moved.json()["detail"] == "director_out_of_scope"

    for label in ("club-ocupado", "club-admin-director", "club-robado"):
        assert await fetch_one(
            "SELECT id FROM organizations WHERE name = :name", name=factory.name(label)
        ) is None


# ----------------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------------
async def test_the_list_is_scoped_filtered_and_counted(client, factory, world):
    mine = []
    for label in ("lista-1", "lista-2", "lista-3"):
        created = await _create(client, world, name=factory.name(label))
        assert created.status_code == 201, created.text
        mine.append(created.json()["id"])
    theirs = await _create(client, world, "other_admin", name=factory.name("lista-ajena"),
                           association_id=world["other_association"]["id"])
    assert theirs.status_code == 201, theirs.text
    pending = await factory.org("lista-pendiente", "club", world["association"])
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE organizations SET status = 'pending' WHERE id = :id"),
            {"id": uuid.UUID(pending["id"])},
        )
        await db.commit()

    params = {"q": factory.name("lista"), "limit": 200}
    listed = await client.get(ADMIN_CLUBS, params=params, headers=world["assoc_admin"]["headers"])
    assert listed.status_code == 200, listed.text
    ids = [row["id"] for row in listed.json()]
    assert set(mine) | {pending["id"]} == set(ids)
    assert listed.headers["X-Total-Count"] == "4"
    names = [row["name"] for row in listed.json()]
    assert names == sorted(names)
    row = next(item for item in listed.json() if item["id"] == mine[0])
    assert row["status"] == "ACTIVE"
    assert row["association"]["id"] == world["association"]["id"]
    assert row["zone"] is None and row["church"] is None and row["director"] is None
    assert row["members_count"] == 0
    assert row["created_at"] and row["updated_at"]

    active = await client.get(
        ADMIN_CLUBS, params={**params, "status": "active"}, headers=world["assoc_admin"]["headers"]
    )
    assert pending["id"] not in [item["id"] for item in active.json()]
    assert active.headers["X-Total-Count"] == "3"
    only_pending = await client.get(
        ADMIN_CLUBS, params={**params, "status": "pending"}, headers=world["assoc_admin"]["headers"]
    )
    assert [item["id"] for item in only_pending.json()] == [pending["id"]]

    page = await client.get(
        ADMIN_CLUBS, params={**params, "limit": 1, "offset": 1},
        headers=world["assoc_admin"]["headers"],
    )
    assert len(page.json()) == 1
    assert page.json()[0]["id"] == ids[1]
    assert page.headers["X-Total-Count"] == "4"

    # The union sees both associations and can narrow to one.
    union = await client.get(ADMIN_CLUBS, params=params, headers=world["union_admin"]["headers"])
    assert theirs.json()["id"] in [item["id"] for item in union.json()]
    narrowed = await client.get(
        ADMIN_CLUBS,
        params={**params, "association_id": world["other_association"]["id"]},
        headers=world["master"]["headers"],
    )
    assert [item["id"] for item in narrowed.json()] == [theirs.json()["id"]]
    # Another association's filter never widens an administrator's scope.
    sneaky = await client.get(
        ADMIN_CLUBS,
        params={**params, "association_id": world["other_association"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert sneaky.json() == [] and sneaky.headers["X-Total-Count"] == "0"

    assert (
        await client.get(ADMIN_CLUBS, params={"status": "weird"}, headers=world["master"]["headers"])
    ).status_code == 422
    assert (
        await client.get(ADMIN_CLUBS, params={"limit": 201}, headers=world["master"]["headers"])
    ).status_code == 422


async def test_the_list_carries_placement_director_and_members(client, factory, world):
    person = await factory.user("director-lista", "STUDENT")
    created = await _create(
        client,
        world,
        name=factory.name("fila-completa"),
        zone_id=world["zone_a"]["id"],
        church_id=world["church_a"]["id"],
        director_email=person["email"],
    )
    assert created.status_code == 201, created.text
    member = await factory.user("miembro-fila", "STUDENT", created.json()["id"])
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at) VALUES (gen_random_uuid(), :user, :club, 'STUDENT', 'ACTIVE',"
                " 'BACKFILL', now())"
            ),
            {"user": uuid.UUID(member["id"]), "club": uuid.UUID(created.json()["id"])},
        )
        await db.commit()

    listed = await client.get(
        ADMIN_CLUBS, params={"q": factory.name("fila-completa")},
        headers=world["assoc_admin"]["headers"],
    )
    [row] = listed.json()
    assert row["zone"]["id"] == world["zone_a"]["id"]
    assert row["church"]["id"] == world["church_a"]["id"]
    assert row["association"]["id"] == world["association"]["id"]
    assert row["director"] == {
        "id": person["id"], "name": factory.name("director-lista"), "email": person["email"]
    }
    assert row["members_count"] == 2


async def test_the_zone_coordinator_reads_their_zone_only(client, factory, world):
    inside = await _create(
        client, world, name=factory.name("coord-dentro"), church_id=world["church_a"]["id"]
    )
    outside = await _create(client, world, name=factory.name("coord-fuera"))
    assert inside.status_code == 201 and outside.status_code == 201

    listed = await client.get(
        ADMIN_CLUBS, params={"q": factory.name("coord")}, headers=world["coord_a"]["headers"]
    )
    assert listed.status_code == 200, listed.text
    ids = [row["id"] for row in listed.json()]
    assert inside.json()["id"] in ids
    assert outside.json()["id"] not in ids


async def test_the_search_ignores_accents_and_case_on_name_and_code(client, factory, world):
    code = _code("ÁGUILA")
    created = await _create(client, world, name=factory.name("Águilas del Ñandú"), code=code)
    assert created.status_code == 201, created.text
    club_id = created.json()["id"]

    for q in ("aguilas del nandu", "ÁGUILAS", "del ÑANDÚ"):
        found = await client.get(
            ADMIN_CLUBS, params={"q": q, "association_id": world["association"]["id"]},
            headers=world["assoc_admin"]["headers"],
        )
        assert club_id in [row["id"] for row in found.json()], q
    by_code = await client.get(
        ADMIN_CLUBS, params={"q": code.replace("Á", "a").lower()},
        headers=world["assoc_admin"]["headers"],
    )
    assert [row["id"] for row in by_code.json()] == [club_id]


# ----------------------------------------------------------------------------
# After creation it is an ordinary club
# ----------------------------------------------------------------------------
async def test_an_admin_club_is_renamed_and_placed_later(client, factory, world):
    created = await _create(client, world, name=factory.name("club-a-editar"))
    assert created.status_code == 201, created.text
    club_id = created.json()["id"]

    renamed = await client.patch(
        f"{ORG}/{club_id}",
        json={"name": factory.name("club-editado"), "city": "Matamoros", "code": _code("ed")},
        headers=world["assoc_admin"]["headers"],
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == factory.name("club-editado")

    placed = await client.post(
        f"{ORG}/clubs/{club_id}/place",
        json={"zone_id": world["zone_a"]["id"], "church_name": factory.name("iglesia-despues")},
        headers=world["assoc_admin"]["headers"],
    )
    assert placed.status_code == 200, placed.text
    assert (await _node(club_id))["parent"] == placed.json()["church"]["id"]
    unplaced = await client.get(f"{ORG}/unplaced-clubs", headers=world["assoc_admin"]["headers"])
    assert club_id not in [item["id"] for item in unplaced.json()]
