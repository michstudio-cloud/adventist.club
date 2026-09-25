"""El ministerio del club como dato real (migrations/019_club_ministry.sql).

Hasta ahora sólo existía como `metadata_json.ministry` y casi ningún club lo declaraba, así que
la pestaña «Clases» ofrecía las clases de todos los ministerios. Lo que se prueba:

  * un club nuevo (alta de la administración o `POST /org-nodes` de tipo CLUB) sin ministerio
    es 422 — regla 3 de ESTADO.md: nada adivina un ministerio —; con él, la respuesta lo nombra;
  * el backfill de 019 rellena la columna desde `metadata_json.ministry` sin inventar nada;
  * lo cambia la administración de la asociación o superior, nunca el director ni la zona;
  * las lecturas de club (lista del admin, perfil público, cercanos) exponen
    `ministry: {id, slug, name}` y se pueden filtrar por él.
"""

import re
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("clubministry")

ORG = "/api/v1/org-nodes"
ADMIN_CLUBS = f"{ORG}/clubs/admin"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "019_club_ministry.sql"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ministry(slug: str) -> dict:
    row = await fetch_one("SELECT id::text AS id, slug, name FROM ministries WHERE slug = :s", s=slug)
    assert row is not None, f"ministry {slug} missing in the test database"
    return dict(row)


async def _column(node_id: str) -> str | None:
    row = await fetch_one(
        "SELECT m.slug FROM organizations o LEFT JOIN ministries m ON m.id = o.ministry_id"
        " WHERE o.id = :id",
        id=uuid.UUID(node_id),
    )
    return row["slug"]


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    association = await factory.org("assoc", "association", union)
    zone = await factory.org("zona", "zone", association)
    club = await factory.org("club-existente", "club", association)
    return {
        "association": association,
        "zone": zone,
        "club": club,
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "union_admin": await factory.user("union-admin", "ADMIN_UNION", union["id"]),
        "coord": await factory.user("coord", "COORDINATOR_ZONE", zone["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "director_free": await factory.user("director-libre", "CLUB_DIRECTOR"),
    }


async def _create(client, world, actor="assoc_admin", **body):
    payload = {"association_id": world["association"]["id"], **body}
    return await client.post(ADMIN_CLUBS, json=payload, headers=world[actor]["headers"])


# ----------------------------------------------------------------------------
# Crear
# ----------------------------------------------------------------------------
async def test_a_new_club_without_a_ministry_is_422(client, factory, world):
    missing = await _create(client, world, name=factory.name("sin-ministerio"))
    assert missing.status_code == 422, missing.text
    assert "club_ministry_required" in missing.text
    unknown = await _create(client, world, name=factory.name("desconocido"), ministry="scouts")
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["detail"] == "ministry_not_found"
    bad_id = await _create(client, world, name=factory.name("id-malo"), ministry_id=str(uuid.uuid4()))
    assert bad_id.status_code == 422 and bad_id.json()["detail"] == "ministry_not_found"
    adventurers = await _ministry("adventurers")
    mismatch = await _create(
        client, world, name=factory.name("mezcla"), ministry="pathfinders", ministry_id=adventurers["id"]
    )
    assert mismatch.status_code == 422 and mismatch.json()["detail"] == "ministry_mismatch"
    count = await fetch_one(
        "SELECT count(*) AS n FROM organizations WHERE name LIKE :like",
        like=f"{factory.prefix}%sin-ministerio%",
    )
    assert count["n"] == 0


async def test_a_new_club_names_its_ministry_by_slug_or_by_id(client, factory, world):
    adventurers = await _ministry("adventurers")
    by_slug = await _create(client, world, name=factory.name("por-slug"), ministry="adventurers")
    assert by_slug.status_code == 201, by_slug.text
    assert by_slug.json()["ministry"] == adventurers
    assert await _column(by_slug.json()["id"]) == "adventurers"

    master_guides = await _ministry("master-guides")
    by_id = await _create(client, world, name=factory.name("por-id"), ministry_id=master_guides["id"])
    assert by_id.status_code == 201, by_id.text
    assert by_id.json()["ministry"] == master_guides

    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CREATE'",
        id=by_slug.json()["id"],
    )
    assert audit["metadata_json"]["ministry"] == "adventurers"


async def test_the_generic_node_endpoint_also_requires_it_for_clubs(client, factory, world):
    church = await factory.org("iglesia-nodo", "church", world["zone"])
    body = {"name": factory.name("nodo-club"), "type": "CLUB", "parent_id": church["id"]}
    refused = await client.post(ORG, json=body, headers=world["master"]["headers"])
    assert refused.status_code == 422, refused.text
    created = await client.post(ORG, json={**body, "ministry": "pathfinders"}, headers=world["master"]["headers"])
    assert created.status_code == 201, created.text
    assert created.json()["ministry"]["slug"] == "pathfinders"
    # A zone has no ministry.
    zone = await client.post(
        ORG,
        json={"name": factory.name("zona-min"), "type": "ZONE", "parent_id": world["association"]["id"],
              "ministry": "pathfinders"},
        headers=world["master"]["headers"],
    )
    assert zone.status_code == 422, zone.text


async def test_a_director_request_may_name_its_ministry(client, factory, world):
    response = await client.post(
        f"{ORG}/clubs",
        json={
            "name": factory.name("solicitud"),
            "association_id": world["association"]["id"],
            "church_name": factory.name("iglesia-solicitud"),
            "ministry": "adventurers",
        },
        headers=world["director_free"]["headers"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PENDING"
    assert response.json()["ministry"]["slug"] == "adventurers"
    assert await _column(response.json()["id"]) == "adventurers"
    pending = await client.get(f"{ORG}/pending-clubs", headers=world["assoc_admin"]["headers"])
    row = next(item for item in pending.json() if item["id"] == response.json()["id"])
    assert row["ministry"]["slug"] == "adventurers"


# ----------------------------------------------------------------------------
# Cambiarlo
# ----------------------------------------------------------------------------
async def test_the_association_changes_it_the_director_and_the_zone_never(client, factory, world):
    created = await _create(client, world, name=factory.name("cambia"), ministry="pathfinders")
    club_id = created.json()["id"]
    url = f"{ORG}/{club_id}"

    director = await factory.user("director-cambia", "CLUB_DIRECTOR", club_id)
    for actor in (director, world["coord"]):
        denied = await client.patch(url, json={"ministry": "adventurers"}, headers=actor["headers"])
        assert denied.status_code == 403, denied.text
    assert await _column(club_id) == "pathfinders"

    changed = await client.patch(url, json={"ministry": "adventurers"}, headers=world["assoc_admin"]["headers"])
    assert changed.status_code == 200, changed.text
    assert changed.json()["ministry"]["slug"] == "adventurers"
    assert await _column(club_id) == "adventurers"
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'UPDATE'"
        " ORDER BY created_at DESC LIMIT 1",
        id=club_id,
    )
    assert audit["metadata_json"]["ministry"] == {"from": "pathfinders", "to": "adventurers"}

    master_guides = await _ministry("master-guides")
    by_union = await client.patch(
        url, json={"ministry_id": master_guides["id"]}, headers=world["union_admin"]["headers"]
    )
    assert by_union.status_code == 200 and by_union.json()["ministry"]["slug"] == "master-guides"

    # Never back to «none», never an unknown one, never on another node type.
    for body in ({"ministry": None}, {"ministry_id": None}, {"ministry": "scouts"}):
        refused = await client.patch(url, json=body, headers=world["master"]["headers"])
        assert refused.status_code == 422, (body, refused.text)
    zone = await client.patch(
        f"{ORG}/{world['zone']['id']}", json={"ministry": "pathfinders"}, headers=world["master"]["headers"]
    )
    assert zone.status_code == 400 and zone.json()["detail"] == "ministry_only_for_clubs"
    assert await _column(club_id) == "master-guides"

    # Renaming alone leaves it alone.
    renamed = await client.patch(url, json={"name": factory.name("cambia-2")}, headers=world["assoc_admin"]["headers"])
    assert renamed.status_code == 200 and renamed.json()["ministry"]["slug"] == "master-guides"


async def test_the_director_profile_answer_says_the_ministry_read_only(client, factory, world):
    created = await _create(client, world, name=factory.name("perfil"), ministry="adventurers")
    club_id = created.json()["id"]
    director = await factory.user("director-perfil", "CLUB_DIRECTOR", club_id)
    await _exec(
        "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, started_at)"
        " VALUES (:id, :u, :c, 'CLUB_DIRECTOR', 'ACTIVE', 'ADMIN', now())",
        id=uuid.uuid4(), u=uuid.UUID(director["id"]), c=uuid.UUID(club_id),
    )
    saved = await client.patch(
        f"/api/v1/clubs/{club_id}/profile", json={"meeting_day": "Sábado"}, headers=director["headers"]
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["ministry"]["slug"] == "adventurers"
    # A ministry is not a profile field: the whitelist refuses it.
    sneaky = await client.patch(
        f"/api/v1/clubs/{club_id}/profile", json={"ministry": "pathfinders"}, headers=director["headers"]
    )
    assert sneaky.status_code == 422, sneaky.text
    assert await _column(club_id) == "adventurers"


# ----------------------------------------------------------------------------
# Leer
# ----------------------------------------------------------------------------
async def test_club_reads_expose_the_ministry_and_filter_by_it(client, factory, world):
    near = {"latitude": 23.7361, "longitude": -99.1411}
    guides = await _create(client, world, name=factory.name("lista-gm"), ministry="master-guides", **near)
    scouts = await _create(client, world, name=factory.name("lista-av"), ministry="adventurers", **near)
    assert guides.status_code == 201 and scouts.status_code == 201

    params = {"q": factory.name("lista-")}
    rows = (await client.get(ADMIN_CLUBS, params=params, headers=world["assoc_admin"]["headers"])).json()
    assert {row["name"]: row["ministry"]["slug"] for row in rows} == {
        factory.name("lista-gm"): "master-guides",
        factory.name("lista-av"): "adventurers",
    }
    only = await client.get(
        ADMIN_CLUBS, params={**params, "ministry": "adventurers"}, headers=world["assoc_admin"]["headers"]
    )
    assert [row["name"] for row in only.json()] == [factory.name("lista-av")]
    assert only.headers["x-total-count"] == "1"

    # `none`: the clubs still without a ministry (the one the fixture wrote directly).
    none = await client.get(
        ADMIN_CLUBS, params={"q": factory.prefix, "ministry": "none"}, headers=world["assoc_admin"]["headers"]
    )
    assert world["club"]["id"] in [row["id"] for row in none.json()]
    assert all(row["ministry"] is None for row in none.json())

    public = await client.get(f"/api/v1/clubs/{guides.json()['id']}/profile", headers=world["assoc_admin"]["headers"])
    assert public.status_code == 200, public.text
    assert public.json()["ministry"]["slug"] == "master-guides"

    node = await client.get(f"{ORG}/{guides.json()['id']}")
    assert node.json()["ministry"]["slug"] == "master-guides"
    listing = await client.get(ORG, params={"q": factory.name("lista-"), "type": "club"})
    assert {row["ministry"]["slug"] for row in listing.json()} == {"master-guides", "adventurers"}

    nearby = await client.get(
        f"{ORG}/clubs/nearby", params={"lat": near["latitude"], "lon": near["longitude"], "radius_km": 1},
        headers=world["assoc_admin"]["headers"],
    )
    found = {row["id"]: row["ministry"] for row in nearby.json()}
    assert found[guides.json()["id"]]["slug"] == "master-guides"
    filtered = await client.get(
        f"{ORG}/clubs/nearby",
        params={"lat": near["latitude"], "lon": near["longitude"], "radius_km": 1, "ministry": "adventurers"},
        headers=world["assoc_admin"]["headers"],
    )
    ids = [row["id"] for row in filtered.json()]
    assert scouts.json()["id"] in ids and guides.json()["id"] not in ids


# ----------------------------------------------------------------------------
# Backfill de 019
# ----------------------------------------------------------------------------
def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def _apply_migration() -> None:
    async with SessionLocal() as db:
        for statement in _statements(MIGRATION.read_text()):
            await db.execute(text(statement))
        await db.commit()


async def test_the_backfill_copies_the_declared_slug_and_invents_nothing(factory, world):
    declared = await factory.org("backfill-declara", "club", world["association"])
    unknown = await factory.org("backfill-desconocido", "club", world["association"])
    silent = await factory.org("backfill-callado", "club", world["association"])
    kept = await factory.org("backfill-conserva", "club", world["association"])
    zone = await factory.org("backfill-zona", "zone", world["association"])
    for node, meta in ((declared, '{"ministry": "adventurers"}'), (unknown, '{"ministry": "scouts"}'),
                       (kept, '{"ministry": "adventurers"}'), (zone, '{"ministry": "adventurers"}')):
        await _exec("UPDATE organizations SET metadata_json = CAST(:m AS jsonb) WHERE id = :id",
                    m=meta, id=uuid.UUID(node["id"]))
    await _exec(
        "UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'pathfinders')"
        " WHERE id = :id",
        id=uuid.UUID(kept["id"]),
    )

    await _apply_migration()
    await _apply_migration()  # idempotent

    assert await _column(declared["id"]) == "adventurers"
    assert await _column(unknown["id"]) is None
    assert await _column(silent["id"]) is None
    assert await _column(kept["id"]) == "pathfinders"  # the column wins; never overwritten
    assert await _column(zone["id"]) is None  # only clubs
    index = await fetch_one(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'organizations_club_ministry_idx'"
    )
    assert index is not None and re.search(r"WHERE .*type.*'club'", index["indexdef"])


async def test_until_the_backfill_the_metadata_still_answers(client, factory, world):
    """A database where 019 added the column but a club only declared its slug: reads fall back
    to `metadata_json.ministry`, the same as the classes tab."""
    legacy = await factory.org("legado", "club", world["association"])
    await _exec("UPDATE organizations SET metadata_json = CAST(:m AS jsonb) WHERE id = :id",
                m='{"ministry": "adventurers"}', id=uuid.UUID(legacy["id"]))
    node = await client.get(f"{ORG}/{legacy['id']}")
    assert node.status_code == 200, node.text
    assert node.json()["ministry"]["slug"] == "adventurers"


@pytest.mark.parametrize("slug", ["pathfinders", "adventurers", "master-guides"])
async def test_the_three_ministries_are_offered(client, slug):
    rows = (await client.get("/api/v1/ministries")).json()
    assert slug in {row["slug"] for row in rows}
