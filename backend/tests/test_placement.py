"""Bloque E, incremento E6 — Zona e iglesia del club.

Decisión D3 del responsable: **las zonas las administra la asociación**. El
director declara su iglesia y su club y nunca la zona; la asociación acepta o
corrige lo declarado y asigna la zona.

Lo que se prueba: que un club no pasa a `active` sin iglesia y zona; que mover
un nodo conserva los ids y reescribe el `path` de todos sus descendientes; que
las lecturas obtienen la asociación como ANCESTRO (no como padre); y que el
coordinador de zona no dibuja el mapa ni decide sobre otra zona.
"""

import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("placement")

ORG = "/api/v1/org-nodes"
AUTH = "/api/v1/auth"


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :id"),
            {**columns, "id": uuid.UUID(user["id"])},
        )
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    association = await factory.org("assoc", "association", union)
    zone_a = await factory.org("zona-a", "zone", association)
    zone_b = await factory.org("zona-b", "zone", association)
    church_a = await factory.org("iglesia-a", "church", zone_a)
    other_association = await factory.org("otra-assoc", "association", union)

    return {
        "division": division,
        "union": union,
        "association": association,
        "zone_a": zone_a,
        "zone_b": zone_b,
        "church_a": church_a,
        "other_association": other_association,
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "coord_a": await factory.user("coord-a", "COORDINATOR_ZONE", zone_a["id"]),
        "coord_b": await factory.user("coord-b", "COORDINATOR_ZONE", zone_b["id"]),
        "other_admin": await factory.user(
            "other-admin", "ADMIN_ASSOCIATION", other_association["id"]
        ),
        "master": await factory.user("master", "MASTER_GC"),
    }


async def _director(factory, label: str) -> dict:
    person = await factory.user(label, "CLUB_DIRECTOR")
    await _set(person, verification_status="VERIFIED")
    return person


async def _request_club(client, factory, world, label: str, **club) -> tuple[dict, dict]:
    director = await _director(factory, f"dir-{label}")
    body = {"name": factory.name(f"club-{label}"), "association_id": world["association"]["id"], "ministry": "pathfinders"}
    body.update(club)
    response = await client.post(f"{ORG}/clubs", json=body, headers=director["headers"])
    assert response.status_code == 201, response.text
    return director, response.json()


async def _node(node_id: str) -> dict:
    return await fetch_one(
        "SELECT id::text AS id, parent_id::text AS parent, path::text AS path, type, status, name"
        " FROM organizations WHERE id = :id",
        id=uuid.UUID(node_id),
    )


# ----------------------------------------------------------------------------
# Sign-up: the director declares a church, never a zone
# ----------------------------------------------------------------------------
async def test_a_club_request_without_a_church_is_refused(client, factory, world):
    director = await _director(factory, "dir-no-church")
    response = await client.post(
        f"{ORG}/clubs",
        json={"name": factory.name("club-sin-iglesia"), "association_id": world["association"]["id"], "ministry": "pathfinders"},
        headers=director["headers"],
    )
    assert response.status_code == 422, response.text

    # ...and the director never names a zone: the field does not exist.
    both = await client.post(
        f"{ORG}/clubs",
        json={
            "name": factory.name("club-con-zona"),
            "association_id": world["association"]["id"], "ministry": "pathfinders",
            "church_name": factory.name("iglesia-central"),
            "zone_id": world["zone_a"]["id"],
        },
        headers=director["headers"],
    )
    assert both.status_code == 422, both.text


async def test_with_a_church_that_has_a_zone_the_club_is_born_in_its_place(
    client, factory, world
):
    _, club = await _request_club(
        client, factory, world, "placed", church_id=world["church_a"]["id"]
    )
    row = await _node(club["id"])
    assert row["parent"] == world["church_a"]["id"]
    assert row["path"] == f"{world['church_a']['path']}.{uuid.UUID(club['id']).hex}"
    assert row["status"] == "pending"

    # A church of another association is not an option.
    outsider_church = await factory.org("iglesia-ajena", "church", world["other_association"])
    director = await _director(factory, "dir-foreign-church")
    denied = await client.post(
        f"{ORG}/clubs",
        json={
            "name": factory.name("club-ajeno"),
            "association_id": world["association"]["id"], "ministry": "pathfinders",
            "church_id": outsider_church["id"],
        },
        headers=director["headers"],
    )
    assert denied.status_code == 400, denied.text


async def test_with_a_declared_name_the_club_waits_under_the_association(client, factory, world):
    _, club = await _request_club(
        client, factory, world, "declared", church_name=factory.name("iglesia-nueva")
    )
    row = await _node(club["id"])
    assert row["parent"] == world["association"]["id"]
    assert club["metadata"]["placement"]["church_name"] == factory.name("iglesia-nueva")
    assert club["metadata"]["placement"]["church_id"] is None
    assert club["metadata"]["church"] == factory.name("iglesia-nueva")


# ----------------------------------------------------------------------------
# Approving is placing
# ----------------------------------------------------------------------------
async def test_approving_an_unplaced_club_requires_a_zone(client, factory, world):
    _, club = await _request_club(client, factory, world, "needs-zone", church_name=factory.name("iglesia-sur"))
    bare = await client.post(
        f"{ORG}/{club['id']}/approve", headers=world["assoc_admin"]["headers"]
    )
    assert bare.status_code == 409, bare.text
    assert "ubicar" in bare.json()["detail"].lower()
    assert (await _node(club["id"]))["status"] == "pending"

    placed = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={"zone_id": world["zone_a"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert placed.status_code == 200, placed.text
    body = placed.json()
    assert body["status"] == "ACTIVE"
    assert body["zone"]["id"] == world["zone_a"]["id"]
    assert body["church"]["name"] == factory.name("iglesia-sur")
    assert body["association"]["id"] == world["association"]["id"]

    row = await _node(club["id"])
    assert row["parent"] == body["church"]["id"]
    church = await _node(body["church"]["id"])
    assert church["parent"] == world["zone_a"]["id"]


async def test_whoever_approves_may_correct_what_was_declared(client, factory, world):
    _, club = await _request_club(client, factory, world, "corrected", church_name=factory.name("igelsia-centrl"))
    corrected = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={
            "club_name": factory.name("club-corregido"),
            "city": "Reynosa",
            "church_id": world["church_a"]["id"],
            "zone_id": world["zone_a"]["id"],
        },
        headers=world["assoc_admin"]["headers"],
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["name"] == factory.name("club-corregido")
    assert corrected.json()["city"] == "Reynosa"
    assert corrected.json()["church"]["id"] == world["church_a"]["id"]

    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CLUB_APPROVE'",
        id=club["id"],
    )
    # What was declared and what was decided both stay in the record.
    assert audit["metadata_json"]["declared"]["church_name"] == factory.name("igelsia-centrl")
    assert audit["metadata_json"]["church_id"] == world["church_a"]["id"]
    assert audit["metadata_json"]["final_name"] == factory.name("club-corregido")


async def test_a_church_is_never_duplicated_by_typing_its_name(client, factory, world):
    """The name already exists under another zone: the answer carries its id so
    the association picks it instead of creating a second row."""
    _, club = await _request_club(
        client, factory, world, "dup", church_name=factory.name("iglesia-a")
    )
    clash = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={"zone_id": world["zone_b"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    # Declaring a name that already exists resolves to that very church, so the
    # answer is about its zone — and it still carries the id to act on.
    assert clash.status_code == 409, clash.text
    assert world["church_a"]["id"] in clash.json()["detail"]

    # The same name with different case and accents is the same church.
    _, other = await _request_club(
        client, factory, world, "dup2", church_name=factory.name("iglesia-a").upper()
    )
    again = await client.post(
        f"{ORG}/{other['id']}/approve",
        json={"zone_id": world["zone_b"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert again.status_code == 409, again.text
    assert world["church_a"]["id"] in again.json()["detail"]

    # And typing the name straight into the approval body says the same thing:
    # pick the row that exists instead of creating a second one.
    _, third = await _request_club(
        client, factory, world, "dup3", church_name=factory.name("iglesia-tercera")
    )
    typed = await client.post(
        f"{ORG}/{third['id']}/approve",
        json={"zone_id": world["zone_b"]["id"], "church_name": factory.name("iglesia-a").lower()},
        headers=world["assoc_admin"]["headers"],
    )
    assert typed.status_code == 409, typed.text
    assert "ya existe" in typed.json()["detail"].lower()
    assert world["church_a"]["id"] in typed.json()["detail"]


# ----------------------------------------------------------------------------
# Only the association draws the map (decision D3)
# ----------------------------------------------------------------------------
async def test_a_zone_coordinator_never_creates_structure(client, factory, world):
    for node_type, parent in (
        ("zone", world["association"]["id"]),
        ("church", world["zone_a"]["id"]),
    ):
        denied = await client.post(
            ORG,
            json={"name": factory.name(f"nueva-{node_type}"), "type": node_type, "parent_id": parent},
            headers=world["coord_a"]["headers"],
        )
        assert denied.status_code == 403, (node_type, denied.text)

    allowed = await client.post(
        ORG,
        json={
            "name": factory.name("zona-c"),
            "type": "zone",
            "parent_id": world["association"]["id"],
        },
        headers=world["assoc_admin"]["headers"],
    )
    assert allowed.status_code == 201, allowed.text

    # Editing one is the same act.
    renamed = await client.patch(
        f"{ORG}/{world['zone_a']['id']}",
        json={"name": factory.name("zona-a-renombrada")},
        headers=world["coord_a"]["headers"],
    )
    assert renamed.status_code == 403, renamed.text


async def test_a_zone_coordinator_places_only_inside_their_own_zone(client, factory, world):
    _, club = await _request_club(client, factory, world, "coord", church_name=factory.name("iglesia-norte"))
    # Their own zone exists, but creating the church is the association's act.
    needs_church = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={"zone_id": world["zone_a"]["id"]},
        headers=world["coord_a"]["headers"],
    )
    assert needs_church.status_code == 403, needs_church.text

    church = await factory.org("iglesia-coord", "church", world["zone_a"])
    elsewhere = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={"zone_id": world["zone_b"]["id"], "church_id": church["id"]},
        headers=world["coord_a"]["headers"],
    )
    assert elsewhere.status_code == 403, elsewhere.text

    ok = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={"zone_id": world["zone_a"]["id"], "church_id": church["id"]},
        headers=world["coord_a"]["headers"],
    )
    assert ok.status_code == 200, ok.text


async def test_a_zone_coordinator_does_not_decide_about_another_zone(client, factory, world):
    """Before E6 every club hung beside the zones, so a coordinator reached the
    whole association. Now the shortcut stops where another zone begins."""
    _, club = await _request_club(
        client, factory, world, "inside-b", church_id=world["church_a"]["id"]
    )
    # church_a lives in zone A, so this request is inside zone A.
    denied = await client.post(
        f"{ORG}/{club['id']}/reject", headers=world["coord_b"]["headers"]
    )
    assert denied.status_code == 403, denied.text
    listed = await client.get(f"{ORG}/pending-clubs", headers=world["coord_b"]["headers"])
    assert club["id"] not in [row["id"] for row in listed.json()]

    # ...and the coordinator of zone A does decide about it.
    seen = await client.get(f"{ORG}/pending-clubs", headers=world["coord_a"]["headers"])
    assert club["id"] in [row["id"] for row in seen.json()]


# ----------------------------------------------------------------------------
# Moving: ids never change, paths are rewritten
# ----------------------------------------------------------------------------
async def test_moving_a_church_takes_its_clubs_and_keeps_every_id(client, factory, world):
    _, club = await _request_club(
        client, factory, world, "moved", church_id=world["church_a"]["id"]
    )
    approved = await client.post(
        f"{ORG}/{club['id']}/approve", headers=world["assoc_admin"]["headers"]
    )
    assert approved.status_code == 200, approved.text

    moved = await client.post(
        f"{ORG}/churches/{world['church_a']['id']}/place",
        json={"zone_id": world["zone_b"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert moved.status_code == 200, moved.text

    church = await _node(world["church_a"]["id"])
    child = await _node(club["id"])
    assert church["parent"] == world["zone_b"]["id"]
    assert church["path"].startswith(f"{world['zone_b']['path']}.")
    # The subtree followed, and neither id changed.
    assert child["id"] == club["id"]
    assert child["path"] == f"{church['path']}.{uuid.UUID(club['id']).hex}"

    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CHURCH_PLACE'"
        " ORDER BY created_at DESC LIMIT 1",
        id=world["church_a"]["id"],
    )
    assert audit["metadata_json"]["previous_parent_id"] == world["zone_a"]["id"]
    assert audit["metadata_json"]["previous_path"].startswith(world["zone_a"]["path"])

    # Now the coordinator of zone B decides about that club and the one of A does not.
    assert (
        await client.get(f"{ORG}/{club['id']}", headers=world["coord_b"]["headers"])
    ).status_code == 200


async def test_only_the_association_moves_a_church(client, factory, world):
    church = await factory.org("iglesia-movible", "church", world["zone_a"])
    denied = await client.post(
        f"{ORG}/churches/{church['id']}/place",
        json={"zone_id": world["zone_b"]["id"]},
        headers=world["coord_a"]["headers"],
    )
    assert denied.status_code == 403, denied.text
    outsider = await client.post(
        f"{ORG}/churches/{church['id']}/place",
        json={"zone_id": world["zone_b"]["id"]},
        headers=world["other_admin"]["headers"],
    )
    assert outsider.status_code == 403, outsider.text


# ----------------------------------------------------------------------------
# Clubs that already exist without a zone keep working
# ----------------------------------------------------------------------------
async def test_unplaced_clubs_keep_working_and_can_be_placed_later(client, factory, world):
    legacy = await factory.org("legacy-club", "club", world["association"])
    director = await _director(factory, "dir-legacy")
    async with SessionLocal() as db:
        await db.execute(
            text(
                "UPDATE users SET organization_id = :club, club_approval = 'APPROVED'"
                " WHERE id = :id"
            ),
            {"club": uuid.UUID(legacy["id"]), "id": uuid.UUID(director["id"])},
        )
        await db.commit()

    listed = await client.get(f"{ORG}/unplaced-clubs", headers=world["assoc_admin"]["headers"])
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json() if item["id"] == legacy["id"])
    assert row["association"]["id"] == world["association"]["id"]
    assert row["declared"] is None

    # The director declares the church; it moves nothing.
    proposed = await client.put(
        f"{ORG}/clubs/{legacy['id']}/placement-proposal",
        json={"church_name": factory.name("iglesia-valle")},
        headers=director["headers"],
    )
    assert proposed.status_code == 200, proposed.text
    assert (await _node(legacy["id"]))["parent"] == world["association"]["id"]
    again = await client.get(f"{ORG}/unplaced-clubs", headers=world["assoc_admin"]["headers"])
    row = next(item for item in again.json() if item["id"] == legacy["id"])
    assert row["declared"]["church_name"] == factory.name("iglesia-valle")

    # Somebody else's director cannot declare it.
    stranger = await _director(factory, "dir-stranger")
    assert (
        await client.put(
            f"{ORG}/clubs/{legacy['id']}/placement-proposal",
            json={"church_name": factory.name("iglesia-otra")},
            headers=stranger["headers"],
        )
    ).status_code == 403

    # And the association places it.
    placed = await client.post(
        f"{ORG}/clubs/{legacy['id']}/place",
        json={"zone_id": world["zone_b"]["id"], "church_name": factory.name("iglesia-valle")},
        headers=world["assoc_admin"]["headers"],
    )
    assert placed.status_code == 200, placed.text
    assert placed.json()["zone"]["id"] == world["zone_b"]["id"]
    moved = await _node(legacy["id"])
    assert moved["parent"] == placed.json()["church"]["id"]
    gone = await client.get(f"{ORG}/unplaced-clubs", headers=world["assoc_admin"]["headers"])
    assert legacy["id"] not in [item["id"] for item in gone.json()]


# ----------------------------------------------------------------------------
# Reads by ancestor
# ----------------------------------------------------------------------------
async def test_nearby_returns_the_right_association_before_and_after_moving(
    client, factory, world
):
    _, club = await _request_club(
        client,
        factory,
        world,
        "nearby",
        church_name=factory.name("iglesia-puerto"),
        latitude=19.4326,
        longitude=-99.1332,
    )
    approved = await client.post(
        f"{ORG}/{club['id']}/approve",
        json={"zone_id": world["zone_a"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert approved.status_code == 200, approved.text
    church_id = approved.json()["church"]["id"]

    async def _row():
        response = await client.get(
            f"{ORG}/clubs/nearby", params={"lat": 19.4326, "lon": -99.1332, "radius_km": 5}
        )
        assert response.status_code == 200, response.text
        return next(item for item in response.json() if item["id"] == club["id"])

    before = await _row()
    assert before["association"]["id"] == world["association"]["id"]
    assert before["zone"]["id"] == world["zone_a"]["id"]
    assert before["church_ref"]["id"] == church_id
    assert before["church"] == factory.name("iglesia-puerto")

    moved = await client.post(
        f"{ORG}/churches/{church_id}/place",
        json={"zone_id": world["zone_b"]["id"]},
        headers=world["assoc_admin"]["headers"],
    )
    assert moved.status_code == 200, moved.text
    after = await _row()
    assert after["association"]["id"] == world["association"]["id"]
    assert after["zone"]["id"] == world["zone_b"]["id"]


async def test_search_can_be_narrowed_to_one_subtree(client, factory, world):
    church = await factory.org("iglesia-buscable", "church", world["zone_a"])
    churches = await client.get(
        f"{ORG}/search",
        params={"type": "church", "within": world["zone_a"]["id"], "q": factory.prefix},
    )
    assert churches.status_code == 200, churches.text
    assert church["id"] in [row["id"] for row in churches.json()]

    elsewhere = await client.get(
        f"{ORG}/search",
        params={"type": "church", "within": world["other_association"]["id"], "q": factory.prefix},
    )
    assert church["id"] not in [row["id"] for row in elsewhere.json()]

    zones = await client.get(
        f"{ORG}/search",
        params={"type": "zone", "within": world["association"]["id"], "q": factory.prefix},
    )
    assert {world["zone_a"]["id"], world["zone_b"]["id"]} <= {row["id"] for row in zones.json()}


async def test_moving_a_club_keeps_its_members_and_its_memberships(client, factory, world):
    _, club = await _request_club(
        client, factory, world, "members", church_id=world["church_a"]["id"]
    )
    approved = await client.post(
        f"{ORG}/{club['id']}/approve", headers=world["assoc_admin"]["headers"]
    )
    assert approved.status_code == 200, approved.text

    member = await factory.user("member", "STUDENT", club["id"])
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO club_memberships (id, user_id, club_id, role, status, source,"
                " started_at) VALUES (gen_random_uuid(), :user, :club, 'STUDENT', 'ACTIVE',"
                " 'BACKFILL', now())"
            ),
            {"user": uuid.UUID(member["id"]), "club": uuid.UUID(club["id"])},
        )
        await db.commit()

    moved = await client.post(
        f"{ORG}/clubs/{club['id']}/place",
        json={"zone_id": world["zone_b"]["id"], "church_name": factory.name("iglesia-destino")},
        headers=world["assoc_admin"]["headers"],
    )
    assert moved.status_code == 200, moved.text

    rows = await fetch_all(
        "SELECT club_id::text AS club FROM club_memberships WHERE user_id = :id",
        id=member["id"],
    )
    assert [row["club"] for row in rows] == [club["id"]]
    still = await fetch_one(
        "SELECT organization_id::text AS org FROM users WHERE id = :id", id=member["id"]
    )
    assert still["org"] == club["id"]
