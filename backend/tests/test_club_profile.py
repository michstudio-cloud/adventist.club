"""Perfil del club (migrations/022_club_ministries.sql).

Pedido del propietario (2026-09-24, tras crear «Jadhai»): «Un club puede tener todos los
ministerios», la dirección con Google Places (dirección, place_id, enlace y coordenadas) y el
logo del club. Lo que se prueba:

  * 022 rellena la tabla puente desde `ministry_id` y el logo desde `metadata_json.profile`,
    y es idempotente;
  * alta, edición, solicitud y aprobación aceptan `ministries: [slug…]` (mínimo uno; `ministry`
    a secas sigue valiendo) y responden `ministries` además de `ministry` (el principal);
  * los mismos permisos de siempre para cambiarlos; el filtro `?ministry=` incluye al club si
    tiene ese ministerio; la pestaña «Clases» ofrece las clases de todos;
  * dirección, `place_id` y `maps_url` se guardan y se exponen; un enlace pegado da coordenadas;
  * el logo: sólo director y asociación o superior, WebP ≤ 512 px y ≤ 300 KB, en
    `clubs/<id>/logo-<hash>.webp`.
"""

import io
import os
import re
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import places, storage
from tests.conftest import fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("clubprofile")

ORG = "/api/v1/org-nodes"
ADMIN_CLUBS = f"{ORG}/clubs/admin"
MEDIA = "https://media.test"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "022_club_ministries.sql"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def _apply_migration() -> None:
    async with SessionLocal() as db:
        for statement in _statements(MIGRATION.read_text()):
            await db.execute(text(statement))
        await db.commit()


async def _bridge(club_id: str) -> list[str]:
    rows = await fetch_all(
        "SELECT m.slug FROM organization_ministries om JOIN ministries m ON m.id = om.ministry_id"
        " WHERE om.organization_id = :id ORDER BY om.created_at, m.slug",
        id=uuid.UUID(club_id),
    )
    return [row["slug"] for row in rows]


async def _principal(club_id: str) -> str | None:
    row = await fetch_one(
        "SELECT m.slug FROM organizations o LEFT JOIN ministries m ON m.id = o.ministry_id"
        " WHERE o.id = :id",
        id=uuid.UUID(club_id),
    )
    return row["slug"]


def _slugs(body: dict) -> list[str]:
    return [row["slug"] for row in body["ministries"]]


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    association = await factory.org("assoc", "association", union)
    other_association = await factory.org("assoc-b", "association", union)
    zone = await factory.org("zona", "zone", association)
    church = await factory.org("iglesia", "church", zone)
    return {
        "association": association,
        "other_association": other_association,
        "zone": zone,
        "church": church,
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "other_admin": await factory.user("assoc-b-admin", "ADMIN_ASSOCIATION", other_association["id"]),
        "union_admin": await factory.user("union-admin", "ADMIN_UNION", union["id"]),
        "coord": await factory.user("coord", "COORDINATOR_ZONE", zone["id"]),
        "master": await factory.user("master", "MASTER_GC"),
    }


async def _create(client, world, actor="assoc_admin", **body):
    payload = {"association_id": world["association"]["id"], **body}
    return await client.post(ADMIN_CLUBS, json=payload, headers=world[actor]["headers"])


# ----------------------------------------------------------------------------
# Migración
# ----------------------------------------------------------------------------
async def test_the_migration_fills_the_bridge_and_the_logo_and_is_idempotent(factory, world):
    with_ministry = await factory.org("mig-con", "club", world["association"])
    without = await factory.org("mig-sin", "club", world["association"])
    with_logo = await factory.org("mig-logo", "club", world["association"])
    kept_logo = await factory.org("mig-logo-nuevo", "club", world["association"])
    await _exec(
        "UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
        " WHERE id = :id",
        id=uuid.UUID(with_ministry["id"]),
    )
    for node, logo in ((with_logo, "https://media.test/logos/viejo.png"),
                       (kept_logo, "https://media.test/logos/otro.png")):
        await _exec(
            "UPDATE organizations SET metadata_json = jsonb_build_object('profile',"
            " jsonb_build_object('logo_url', CAST(:logo AS text))) WHERE id = :id",
            logo=logo, id=uuid.UUID(node["id"]),
        )
    await _exec("UPDATE organizations SET logo_url = 'https://media.test/clubs/x/logo-1.webp'"
                " WHERE id = :id", id=uuid.UUID(kept_logo["id"]))

    await _apply_migration()
    await _apply_migration()  # idempotent

    assert await _bridge(with_ministry["id"]) == ["adventurers"]
    assert await _bridge(without["id"]) == []
    logos = {
        row["id"]: row["logo_url"]
        for row in await fetch_all(
            "SELECT id::text AS id, logo_url FROM organizations WHERE id = ANY(:ids)",
            ids=[uuid.UUID(with_logo["id"]), uuid.UUID(kept_logo["id"])],
        )
    }
    assert logos[with_logo["id"]] == "https://media.test/logos/viejo.png"
    assert logos[kept_logo["id"]] == "https://media.test/clubs/x/logo-1.webp"  # never overwritten
    columns = {
        row["column_name"]
        for row in await fetch_all(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'organizations'"
        )
    }
    assert {"address", "place_id", "maps_url", "logo_url"} <= columns


# ----------------------------------------------------------------------------
# Varios ministerios
# ----------------------------------------------------------------------------
async def test_a_club_is_created_with_several_ministries(client, factory, world):
    created = await _create(client, world, name=factory.name("todos"),
                            ministries=["adventurers", "pathfinders", "adventurers"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert _slugs(body) == ["adventurers", "pathfinders"]  # order kept, duplicates dropped
    assert body["ministry"]["slug"] == "adventurers"  # the principal: the first chosen
    assert await _bridge(body["id"]) == ["adventurers", "pathfinders"]
    assert await _principal(body["id"]) == "adventurers"
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CREATE'", id=body["id"]
    )
    assert audit["metadata_json"]["ministries"] == ["adventurers", "pathfinders"]

    # `ministry` alone is still a list of one.
    single = await _create(client, world, name=factory.name("uno"), ministry="master-guides")
    assert single.status_code == 201 and _slugs(single.json()) == ["master-guides"]

    # With both, the single one must be in the list and becomes the principal.
    both = await _create(client, world, name=factory.name("ambos"), ministry="pathfinders",
                         ministries=["adventurers", "pathfinders"])
    assert both.status_code == 201 and _slugs(both.json()) == ["pathfinders", "adventurers"]
    mismatch = await _create(client, world, name=factory.name("mezcla"), ministry="master-guides",
                             ministries=["adventurers"])
    assert mismatch.status_code == 422 and mismatch.json()["detail"] == "ministry_mismatch"


async def test_at_least_one_ministry_and_only_known_ones(client, factory, world):
    empty = await _create(client, world, name=factory.name("vacio"), ministries=[])
    assert empty.status_code == 422 and "club_ministry_required" in empty.text
    unknown = await _create(client, world, name=factory.name("scouts"), ministries=["pathfinders", "scouts"])
    assert unknown.status_code == 422 and unknown.json()["detail"] == "ministry_not_found"
    nothing = await _create(client, world, name=factory.name("nada"))
    assert nothing.status_code == 422


async def test_only_the_association_or_above_changes_the_ministries(client, factory, world):
    created = await _create(client, world, name=factory.name("editable"), ministries=["pathfinders"])
    club_id = created.json()["id"]
    url = f"{ORG}/{club_id}"
    director = await factory.user("dir-editable", "CLUB_DIRECTOR", club_id)
    for actor in ("coord",):
        refused = await client.patch(url, json={"ministries": ["pathfinders", "adventurers"]},
                                     headers=world[actor]["headers"])
        assert refused.status_code == 403, actor
    refused = await client.patch(url, json={"ministries": ["adventurers"]}, headers=director["headers"])
    assert refused.status_code == 403
    assert (await client.patch(url, json={"ministries": []},
                               headers=world["assoc_admin"]["headers"])).status_code == 422

    changed = await client.patch(url, json={"ministries": ["pathfinders", "adventurers", "master-guides"]},
                                 headers=world["assoc_admin"]["headers"])
    assert changed.status_code == 200, changed.text
    assert _slugs(changed.json()) == ["pathfinders", "adventurers", "master-guides"]
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'UPDATE'"
        " ORDER BY created_at DESC LIMIT 1", id=club_id,
    )
    assert audit["metadata_json"]["ministries"] == {
        "from": ["pathfinders"], "to": ["pathfinders", "adventurers", "master-guides"]}

    # Dropping one and changing the principal: the bridge follows exactly.
    changed = await client.patch(url, json={"ministries": ["adventurers", "master-guides"]},
                                 headers=world["union_admin"]["headers"])
    assert changed.status_code == 200 and changed.json()["ministry"]["slug"] == "adventurers"
    assert sorted(await _bridge(club_id)) == ["adventurers", "master-guides"]
    assert await _principal(club_id) == "adventurers"

    # The legacy single field still replaces the whole list with one.
    single = await client.patch(url, json={"ministry": "pathfinders"}, headers=world["assoc_admin"]["headers"])
    assert single.status_code == 200 and _slugs(single.json()) == ["pathfinders"]
    assert await _bridge(club_id) == ["pathfinders"]


async def test_the_filter_includes_a_club_with_that_ministry_among_others(client, factory, world):
    created = await _create(client, world, name=factory.name("filtro"), ministries=["pathfinders", "adventurers"],
                            latitude=19.43, longitude=-99.13)
    club_id = created.json()["id"]
    headers = world["assoc_admin"]["headers"]
    for slug, expected in (("adventurers", True), ("pathfinders", True), ("master-guides", False)):
        rows = (await client.get(ADMIN_CLUBS, params={"ministry": slug, "q": factory.name("filtro"), "limit": 200},
                                 headers=headers)).json()
        assert (club_id in {row["id"] for row in rows}) is expected, slug
        nearby = (await client.get(f"{ORG}/clubs/nearby", params={
            "lat": 19.43, "lon": -99.13, "radius_km": 5, "ministry": slug})).json()
        assert (club_id in {row["id"] for row in nearby}) is expected, slug
    none = (await client.get(ADMIN_CLUBS, params={"ministry": "none", "limit": 200}, headers=headers)).json()
    assert club_id not in {row["id"] for row in none}
    row = next(r for r in (await client.get(ADMIN_CLUBS, params={"q": factory.name("filtro")}, headers=headers)).json()
               if r["id"] == club_id)
    assert _slugs(row) == ["pathfinders", "adventurers"] and row["ministry"]["slug"] == "pathfinders"
    public = (await client.get(f"/api/v1/clubs/{club_id}/profile")).json()
    assert _slugs(public) == ["pathfinders", "adventurers"]


async def test_a_club_with_two_ministries_is_offered_the_classes_of_both(client, factory, world):
    director = await factory.user("dir-clases", "CLUB_DIRECTOR")
    created = await _create(client, world, name=factory.name("clases"), ministries=["adventurers", "pathfinders"],
                            director_email=director["email"])
    assert created.status_code == 201, created.text
    club_id = created.json()["id"]
    programs = {}
    async with SessionLocal() as db:
        for slug in ("pathfinders", "adventurers", "master-guides"):
            program_id = uuid.uuid4()
            ministry_id = (await db.execute(text("SELECT id FROM ministries WHERE slug = :m"), {"m": slug})).scalar_one()
            await db.execute(text(
                "INSERT INTO programs (id, ministry_id, kind, slug, name, status, issuer_level, sort_order)"
                " VALUES (:id, :m, 'CLASS', :slug, :name, 'PUBLISHED', 'CLUB', 1)"),
                {"id": program_id, "m": ministry_id, "slug": f"{factory.prefix}-{slug}",
                 "name": factory.name(f"clase-{slug}")})
            programs[slug] = str(program_id)
        await db.commit()

    url = f"/api/v1/clubs/{club_id}/classes"
    body = (await client.get(url, headers=director["headers"])).json()
    offered = {item["id"] for item in body["available"]}
    assert programs["pathfinders"] in offered and programs["adventurers"] in offered
    assert programs["master-guides"] not in offered
    assert body["ministry"]["slug"] == "adventurers"
    assert _slugs(body) == ["adventurers", "pathfinders"]
    refused = await client.post(f"{url}/{programs['master-guides']}/enroll", json={}, headers=director["headers"])
    assert refused.status_code == 409 and refused.json()["detail"] == "program_not_in_club_ministry"
    for slug in ("pathfinders", "adventurers"):
        # Past the ministry gate (these bare programs have no requirements: that is the next 409).
        accepted = await client.post(f"{url}/{programs[slug]}/enroll", json={}, headers=director["headers"])
        assert accepted.json().get("detail") != "program_not_in_club_ministry", (slug, accepted.text)


async def test_a_request_with_several_ministries_and_its_approval(client, factory, world):
    director = await factory.user("dir-solicitud", "CLUB_DIRECTOR")
    requested = await client.post(f"{ORG}/clubs", json={
        "name": factory.name("solicitud"), "association_id": world["association"]["id"],
        "church_id": world["church"]["id"], "ministries": ["pathfinders", "adventurers"],
        "address": "Av. Juárez 10, Centro, CDMX", "place_id": "ChIJ-solicitud-123",
        "latitude": 19.4326, "longitude": -99.1332, "state": "CDMX", "country": "México",
    }, headers=director["headers"])
    assert requested.status_code == 201, requested.text
    club_id = requested.json()["id"]
    assert _slugs(requested.json()) == ["pathfinders", "adventurers"]
    assert requested.json()["maps_url"] == places.place_url("ChIJ-solicitud-123")
    assert requested.json()["state"] == "CDMX" and requested.json()["country"] == "México"
    pending = [row for row in (await client.get(f"{ORG}/pending-clubs", params={"ministry": "adventurers"},
                                               headers=world["assoc_admin"]["headers"])).json()
               if row["id"] == club_id]
    assert pending and _slugs(pending[0]) == ["pathfinders", "adventurers"]

    # The zone decides the request but never changes the set the director chose...
    added = await client.post(f"{ORG}/{club_id}/approve",
                              json={"ministries": ["pathfinders", "adventurers", "master-guides"]},
                              headers=world["coord"]["headers"])
    assert added.status_code == 403
    # ...the same set in another order is no change.
    approved = await client.post(f"{ORG}/{club_id}/approve", json={"ministries": ["adventurers", "pathfinders"]},
                                 headers=world["coord"]["headers"])
    assert approved.status_code == 200, approved.text
    assert _slugs(approved.json()) == ["pathfinders", "adventurers"]
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'CLUB_APPROVE'", id=club_id
    )
    assert audit["metadata_json"]["ministries"]["to"] == ["pathfinders", "adventurers"]


async def test_the_association_corrects_the_ministries_when_approving(client, factory, world):
    director = await factory.user("dir-corrige", "CLUB_DIRECTOR")
    requested = await client.post(f"{ORG}/clubs", json={
        "name": factory.name("corrige"), "association_id": world["association"]["id"],
        "church_id": world["church"]["id"], "ministry": "pathfinders",
    }, headers=director["headers"])
    club_id = requested.json()["id"]
    approved = await client.post(f"{ORG}/{club_id}/approve", json={"ministries": ["pathfinders", "adventurers"]},
                                 headers=world["assoc_admin"]["headers"])
    assert approved.status_code == 200, approved.text
    assert _slugs(approved.json()) == ["pathfinders", "adventurers"]
    assert await _bridge(club_id) == ["pathfinders", "adventurers"]


# ----------------------------------------------------------------------------
# Dirección (Google Places)
# ----------------------------------------------------------------------------
def test_coordinates_come_out_of_a_pasted_google_maps_link():
    place = "https://www.google.com/maps/place/Iglesia/@19.40,-99.10,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d19.4326077!4d-99.133208"
    assert places.coords_from_maps_url(place) == (19.4326077, -99.133208)  # the pin, not the centre
    assert places.coords_from_maps_url("https://www.google.com/maps/@26.0508,-98.2979,15z") == (26.0508, -98.2979)
    assert places.coords_from_maps_url("https://maps.google.com/?q=26.05,-98.29") == (26.05, -98.29)
    assert places.coords_from_maps_url("https://maps.app.goo.gl/AbCdEf123") is None
    for good in ("https://www.google.com/maps/place/?q=place_id:ChIJ123",
                 "https://www.google.com.mx/maps/@19.4,-99.1,15z", "https://maps.app.goo.gl/AbCdEf123",
                 "https://goo.gl/maps/xyz"):
        assert places.is_google_maps_url(good), good
    for bad in ("http://www.google.com/maps/@1,2", "https://evil.example/maps/@1,2",
                "https://www.google.com/search?q=club", "javascript:alert(1)",
                "https://www.google.com.evil.example/maps", "https://user@www.google.com/maps"):
        assert not places.is_google_maps_url(bad), bad


async def test_the_address_the_place_and_the_link_are_saved_and_exposed(client, factory, world):
    created = await _create(client, world, name=factory.name("direccion"), ministries=["pathfinders"],
                            address="  Calle 5   de Mayo 12, Reynosa  ", place_id="ChIJ_direccion-42",
                            latitude=26.0508, longitude=-98.2979, city="Reynosa", state="Tamaulipas",
                            country="México")
    assert created.status_code == 201, created.text
    body = created.json()
    club_id = body["id"]
    assert body["address"] == "Calle 5 de Mayo 12, Reynosa"
    assert body["place_id"] == "ChIJ_direccion-42"
    assert body["maps_url"] == "https://www.google.com/maps/place/?q=place_id:ChIJ_direccion-42"
    assert (body["latitude"], body["longitude"]) == (26.0508, -98.2979)
    node = (await client.get(f"{ORG}/{club_id}")).json()
    assert node["address"] == body["address"] and node["maps_url"] == body["maps_url"]
    row = next(r for r in (await client.get(ADMIN_CLUBS, params={"q": factory.name("direccion")},
                                            headers=world["assoc_admin"]["headers"])).json() if r["id"] == club_id)
    assert row["place_id"] == "ChIJ_direccion-42" and row["address"] == body["address"]
    public = (await client.get(f"/api/v1/clubs/{club_id}/profile")).json()
    assert public["address"] == body["address"] and public["maps_url"] == body["maps_url"]
    assert (public["latitude"], public["longitude"]) == (26.0508, -98.2979)
    nearby = (await client.get(f"{ORG}/clubs/nearby", params={"lat": 26.05, "lon": -98.30, "radius_km": 5})).json()
    found = next(r for r in nearby if r["id"] == club_id)
    assert found["address"] == body["address"] and found["maps_url"] == body["maps_url"]

    # A typed address (no Places key) replaces the place: the old id would lie.
    typed = await client.patch(f"{ORG}/{club_id}", json={
        "address": "Calle Hidalgo 3", "maps_url": "https://www.google.com/maps/@26.1,-98.3,17z"},
        headers=world["assoc_admin"]["headers"])
    assert typed.status_code == 200, typed.text
    assert typed.json()["place_id"] is None and typed.json()["address"] == "Calle Hidalgo 3"
    assert (typed.json()["latitude"], typed.json()["longitude"]) == (26.1, -98.3)  # from the link

    bad = await client.patch(f"{ORG}/{club_id}", json={"maps_url": "https://evil.example/maps/@1,2"},
                             headers=world["assoc_admin"]["headers"])
    assert bad.status_code == 422
    bad_id = await client.patch(f"{ORG}/{club_id}", json={"place_id": "no spaces allowed"},
                                headers=world["assoc_admin"]["headers"])
    assert bad_id.status_code == 422


async def test_the_director_sets_the_address_of_their_club(client, factory, world):
    created = await _create(client, world, name=factory.name("dir-direccion"), ministries=["adventurers"])
    club_id = created.json()["id"]
    director = await factory.user("dir-direccion", "CLUB_DIRECTOR", club_id)
    url = f"{ORG}/clubs/{club_id}/location"
    pasted = await client.put(url, json={
        "address": "Parque Central", "maps_url": "https://www.google.com/maps/@25.6866,-100.3161,16z"},
        headers=director["headers"])
    assert pasted.status_code == 200, pasted.text
    body = pasted.json()
    assert body["address"] == "Parque Central" and (body["latitude"], body["longitude"]) == (25.6866, -100.3161)
    picked = await client.put(url, json={
        "address": "Iglesia Central, Monterrey, N.L., México", "place_id": "ChIJplace_mty",
        "latitude": 25.67, "longitude": -100.31, "city": "Monterrey", "state": "Nuevo León", "country": "México"},
        headers=director["headers"])
    assert picked.status_code == 200, picked.text
    assert picked.json()["city"] == "Monterrey" and picked.json()["maps_url"].endswith("place_id:ChIJplace_mty")
    # The old contract (a bare point) still works; an empty body does not.
    assert (await client.put(url, json={"latitude": 25.6, "longitude": -100.3},
                             headers=director["headers"])).status_code == 200
    assert (await client.put(url, json={}, headers=director["headers"])).status_code == 422
    assert (await client.put(url, json={"latitude": 25.6}, headers=director["headers"])).status_code == 422
    stranger = await factory.user("dir-ajeno", "CLUB_DIRECTOR")
    assert (await client.put(url, json={"address": "x"}, headers=stranger["headers"])).status_code == 403


# ----------------------------------------------------------------------------
# Logo
# ----------------------------------------------------------------------------
class FakeR2:
    def __init__(self):
        self.objects = []
        self.deleted = []

    def put_object(self, **kwargs):
        self.objects.append(kwargs)

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs["Key"])


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(settings, "R2_PUBLIC_URL", f"{MEDIA}/")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    return fake


def _image(size: int, fmt: str = "WEBP", noise: bool = False) -> bytes:
    if noise:
        image = Image.frombytes("RGB", (size, size), os.urandom(size * size * 3))
    else:
        image = Image.new("RGB", (size, size), (47, 125, 225))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


async def _upload(client, club_id, user, data, content_type="image/webp"):
    return await client.post(f"/api/v1/media/clubs/{club_id}/logo",
                             files={"file": ("logo.webp", data, content_type)}, headers=user["headers"])


@pytest_asyncio.fixture(scope="module")
async def logo_club(client, factory, world):
    created = await _create(client, world, name=factory.name("logo"), ministries=["pathfinders"])
    club_id = created.json()["id"]
    return {
        "id": club_id,
        "director": await factory.user("logo-dir", "CLUB_DIRECTOR", club_id),
        "secretary": await factory.user("logo-sec", "CLUB_SECRETARY", club_id),
        "member": await factory.user("logo-miembro", "STUDENT", club_id),
    }


async def test_only_the_director_and_the_association_or_above_change_the_logo(client, world, logo_club, r2):
    webp = _image(512)
    for user in (logo_club["member"], logo_club["secretary"], world["coord"], world["other_admin"]):
        refused = await _upload(client, logo_club["id"], user, webp)
        assert refused.status_code == 403 and refused.json()["detail"] == "club_logo_forbidden"
    assert r2.objects == []

    uploaded = await _upload(client, logo_club["id"], logo_club["director"], webp)
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert re.fullmatch(rf"clubs/{logo_club['id']}/logo-[0-9a-f]{{16}}\.webp", body["key"])
    assert body["logo_url"] == f"{MEDIA}/{body['key']}"
    assert r2.objects[-1]["ContentType"] == "image/webp" and r2.objects[-1]["Key"] == body["key"]
    public = (await client.get(f"/api/v1/clubs/{logo_club['id']}/profile")).json()
    assert public["logo_url"] == body["logo_url"]

    # The association replaces it (PNG from a browser without WebP): the old object goes.
    replaced = await _upload(client, logo_club["id"], world["assoc_admin"], _image(256, "PNG"), "image/png")
    assert replaced.status_code == 201, replaced.text
    assert replaced.json()["key"].endswith(".png") and body["key"] in r2.deleted
    node = (await client.get(f"{ORG}/{logo_club['id']}")).json()
    assert node["logo_url"] == replaced.json()["logo_url"]
    master = await _upload(client, logo_club["id"], world["master"], webp)
    assert master.status_code == 201

    # Removing it: the member cannot, the director can.
    url = f"/api/v1/media/clubs/{logo_club['id']}/logo"
    assert (await client.delete(url, headers=logo_club["member"]["headers"])).status_code == 403
    removed = await client.delete(url, headers=logo_club["director"]["headers"])
    assert removed.status_code == 200 and removed.json()["logo_url"] is None
    assert (await client.get(f"{ORG}/{logo_club['id']}")).json()["logo_url"] is None


async def test_the_logo_is_small_square_and_an_image(client, logo_club, r2):
    director = logo_club["director"]
    heavy = _image(512, "PNG", noise=True)
    assert len(heavy) > 300 * 1024
    too_heavy = await _upload(client, logo_club["id"], director, heavy, "image/png")
    assert too_heavy.status_code == 413 and too_heavy.json()["detail"] == "club_logo_too_large"
    too_big = await _upload(client, logo_club["id"], director, _image(600))
    assert too_big.status_code == 422 and too_big.json()["detail"] == "club_logo_too_big"
    lying = await _upload(client, logo_club["id"], director, b"RIFF0000WEBPnot-an-image")
    assert lying.status_code == 415
    svg = await _upload(client, logo_club["id"], director, b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                        "image/svg+xml")
    assert svg.status_code == 415
    assert r2.objects == []


async def test_the_logo_needs_storage(client, logo_club):
    missing = await _upload(client, logo_club["id"], logo_club["director"], _image(64))
    assert missing.status_code == 503
