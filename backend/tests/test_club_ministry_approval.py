"""Ningún club nace sin ministerio: la solicitud del director y su aprobación.

  * `POST /auth/register` con `club` (y `POST /org-nodes/clubs`) exigen el ministerio: 422
    `club_ministry_required` sin él, 422 `ministry_not_found` con uno desconocido, y en ninguno
    de los dos casos queda cuenta ni club a medias;
  * `POST /org-nodes/{id}/approve` acepta `ministry`: obligatorio si la solicitud no lo trae
    (solicitudes anteriores a que el registro lo pidiera), opcional si lo trae; corregirlo es de
    la administración de la asociación o superior (la zona sólo completa el que falta);
  * el cambio queda en `audit_log` (`CLUB_APPROVE.metadata_json.ministry = {from, to}`);
  * `GET /org-nodes/pending-clubs?ministry=` filtra las solicitudes (`none`: las que no lo tienen).
"""

import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import DEFAULT_PASSWORD, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("clubminapprove")

AUTH = "/api/v1/auth"
ORG = "/api/v1/org-nodes"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _column(node_id: str) -> str | None:
    row = await fetch_one(
        "SELECT m.slug FROM organizations o LEFT JOIN ministries m ON m.id = o.ministry_id"
        " WHERE o.id = :id",
        id=uuid.UUID(node_id),
    )
    return row["slug"]


async def _status(node_id: str) -> str:
    row = await fetch_one("SELECT status FROM organizations WHERE id = :id", id=uuid.UUID(node_id))
    return row["status"]


async def _audit(node_id: str, action: str) -> dict | None:
    row = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = :action"
        " ORDER BY created_at DESC LIMIT 1",
        id=node_id,
        action=action,
    )
    return row["metadata_json"] if row else None


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    division = await factory.org("div", "division")
    union = await factory.org("uni", "union", division)
    association = await factory.org("assoc", "association", union)
    zone = await factory.org("zona", "zone", association)
    church = await factory.org("iglesia", "church", zone)
    return {
        "association": association,
        "zone": zone,
        "church": church,
        "assoc_admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "coord": await factory.user("coord", "COORDINATOR_ZONE", zone["id"]),
    }


def _club(factory, world, label: str, **extra) -> dict:
    return {
        "name": factory.name(f"club-{label}"),
        "association_id": world["association"]["id"],
        "church_name": factory.name(f"iglesia-{label}"),
        **extra,
    }


def _register_body(factory, label: str, club: dict) -> dict:
    return {
        "email": factory.email(label),
        "password": DEFAULT_PASSWORD,
        "name": factory.name(label),
        "role": "CLUB_DIRECTOR",
        "club": club,
    }


async def _request(client, factory, world, label: str, ministry: str | None = "pathfinders") -> str:
    """A pending request from a fresh director; `ministry=None` makes it one of the requests
    from before the registration asked for it (column and metadata both empty)."""
    director = await factory.user(f"dir-{label}", "CLUB_DIRECTOR")
    response = await client.post(
        f"{ORG}/clubs",
        json=_club(factory, world, label, ministry=ministry or "pathfinders"),
        headers=director["headers"],
    )
    assert response.status_code == 201, response.text
    club_id = response.json()["id"]
    if ministry is None:
        await _exec("UPDATE organizations SET ministry_id = NULL WHERE id = :id", id=uuid.UUID(club_id))
    return club_id


def _placement(world) -> dict:
    return {"zone_id": world["zone"]["id"], "church_id": world["church"]["id"]}


# ----------------------------------------------------------------------------
# La solicitud
# ----------------------------------------------------------------------------
async def test_registering_a_club_without_a_ministry_is_422_and_leaves_nothing(client, factory, world):
    missing = _club(factory, world, "sin-ministerio")
    response = await client.post(f"{AUTH}/register", json=_register_body(factory, "sin-min", missing))
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "club_ministry_required"

    unknown = _club(factory, world, "ministerio-raro", ministry="scouts")
    response = await client.post(f"{AUTH}/register", json=_register_body(factory, "raro", unknown))
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "ministry_not_found"

    for label in ("sin-min", "raro"):
        assert await fetch_one("SELECT 1 AS x FROM users WHERE email = :e", e=factory.email(label)) is None
    for club in (missing, unknown):
        assert await fetch_one("SELECT 1 AS x FROM organizations WHERE name = :n", n=club["name"]) is None


async def test_the_request_keeps_the_ministry_the_director_chose(client, factory, world):
    club = _club(factory, world, "aventureros", ministry="adventurers")
    response = await client.post(f"{AUTH}/register", json=_register_body(factory, "aventureros", club))
    assert response.status_code == 201, response.text
    club_id = response.json()["organization_id"]
    assert await _status(club_id) == "pending"
    assert await _column(club_id) == "adventurers"
    assert (await _audit(club_id, "CLUB_REQUEST"))["ministry"] == "adventurers"

    pending = await client.get(f"{ORG}/pending-clubs", headers=world["assoc_admin"]["headers"])
    row = next(item for item in pending.json() if item["id"] == club_id)
    assert row["ministry"]["slug"] == "adventurers"


async def test_the_panel_request_requires_it_too(client, factory, world):
    director = await factory.user("dir-panel", "CLUB_DIRECTOR")
    response = await client.post(
        f"{ORG}/clubs", json=_club(factory, world, "panel"), headers=director["headers"]
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "club_ministry_required"
    me = await fetch_one("SELECT club_approval FROM users WHERE id = :id", id=uuid.UUID(director["id"]))
    assert me["club_approval"] is None


# ----------------------------------------------------------------------------
# La aprobación
# ----------------------------------------------------------------------------
async def test_an_old_request_without_a_ministry_is_approved_only_with_one(client, factory, world):
    club_id = await _request(client, factory, world, "vieja", ministry=None)
    url = f"{ORG}/{club_id}/approve"
    headers = world["assoc_admin"]["headers"]

    refused = await client.post(url, json=_placement(world), headers=headers)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "club_ministry_required"
    assert await _status(club_id) == "pending" and await _column(club_id) is None

    unknown = await client.post(url, json={**_placement(world), "ministry": "scouts"}, headers=headers)
    assert unknown.status_code == 422 and unknown.json()["detail"] == "ministry_not_found"

    approved = await client.post(url, json={**_placement(world), "ministry": "master-guides"}, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "ACTIVE"
    assert approved.json()["ministry"]["slug"] == "master-guides"
    assert await _status(club_id) == "active" and await _column(club_id) == "master-guides"
    audit = await _audit(club_id, "CLUB_APPROVE")
    assert audit["ministry"] == {"from": None, "to": "master-guides"}


async def test_a_request_with_a_ministry_is_approved_as_is(client, factory, world):
    club_id = await _request(client, factory, world, "tal-cual", ministry="adventurers")
    approved = await client.post(
        f"{ORG}/{club_id}/approve", json=_placement(world), headers=world["assoc_admin"]["headers"]
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["ministry"]["slug"] == "adventurers"
    assert (await _audit(club_id, "CLUB_APPROVE"))["ministry"] == {"from": "adventurers", "to": "adventurers"}


async def test_the_association_corrects_it_the_zone_only_fills_it_in(client, factory, world):
    corrected = await _request(client, factory, world, "corrige", ministry="pathfinders")
    denied = await client.post(
        f"{ORG}/{corrected}/approve",
        json={**_placement(world), "ministry": "adventurers"},
        headers=world["coord"]["headers"],
    )
    assert denied.status_code == 403, denied.text
    assert await _status(corrected) == "pending" and await _column(corrected) == "pathfinders"

    approved = await client.post(
        f"{ORG}/{corrected}/approve",
        json={**_placement(world), "ministry": "adventurers"},
        headers=world["assoc_admin"]["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert await _column(corrected) == "adventurers"
    assert (await _audit(corrected, "CLUB_APPROVE"))["ministry"] == {"from": "pathfinders", "to": "adventurers"}

    # The zone decides requests too: it may give a ministry to one that has none, and
    # naming the one the request already has is no correction.
    filled = await _request(client, factory, world, "completa", ministry=None)
    by_zone = await client.post(
        f"{ORG}/{filled}/approve",
        json={**_placement(world), "ministry": "pathfinders"},
        headers=world["coord"]["headers"],
    )
    assert by_zone.status_code == 200, by_zone.text
    assert await _column(filled) == "pathfinders"
    same = await _request(client, factory, world, "igual", ministry="pathfinders")
    by_zone = await client.post(
        f"{ORG}/{same}/approve",
        json={**_placement(world), "ministry": "pathfinders"},
        headers=world["coord"]["headers"],
    )
    assert by_zone.status_code == 200, by_zone.text


async def test_rejecting_needs_no_ministry(client, factory, world):
    club_id = await _request(client, factory, world, "rechazo", ministry=None)
    rejected = await client.post(
        f"{ORG}/{club_id}/reject", json={"reason": "duplicado"}, headers=world["assoc_admin"]["headers"]
    )
    assert rejected.status_code == 200, rejected.text
    assert await _status(club_id) == "rejected"


async def test_pending_requests_filter_by_ministry(client, factory, world):
    without = await _request(client, factory, world, "filtro-sin", ministry=None)
    adventurers = await _request(client, factory, world, "filtro-av", ministry="adventurers")
    headers = world["assoc_admin"]["headers"]

    async def ids(ministry: str) -> set[str]:
        response = await client.get(f"{ORG}/pending-clubs", params={"ministry": ministry}, headers=headers)
        assert response.status_code == 200, response.text
        return {row["id"] for row in response.json()}

    assert without in await ids("none") and adventurers not in await ids("none")
    assert adventurers in await ids("adventurers") and without not in await ids("adventurers")
    bad = await client.get(f"{ORG}/pending-clubs", params={"ministry": "NO!"}, headers=headers)
    assert bad.status_code == 422


async def test_a_request_that_only_declared_it_in_its_metadata_keeps_it(client, factory, world):
    """Before 019's backfill reaches a database, the slug lives in `metadata_json.ministry`:
    approving it without a body writes that one into the column, invents nothing."""
    club_id = await _request(client, factory, world, "metadatos", ministry=None)
    await _exec(
        "UPDATE organizations SET metadata_json = metadata_json || '{\"ministry\": \"adventurers\"}'::jsonb"
        " WHERE id = :id",
        id=uuid.UUID(club_id),
    )
    approved = await client.post(
        f"{ORG}/{club_id}/approve", json=_placement(world), headers=world["assoc_admin"]["headers"]
    )
    assert approved.status_code == 200, approved.text
    assert await _column(club_id) == "adventurers"
    assert (await _audit(club_id, "CLUB_APPROVE"))["ministry"] == {"from": "adventurers", "to": "adventurers"}
