"""Clubs can be found by location: the director pins the physical club, anyone finds the nearby ones."""
import pytest

from tests.conftest import fetch_one, module_factory, requires_db
from tests.test_club_signup import ORG, _find_ntam, _ntam_node, _register_director

pytestmark = requires_db
factory = module_factory("clubloc")

REYNOSA = {"latitude": 26.0508, "longitude": -98.2979}
MATAMOROS = {"latitude": 25.8690, "longitude": -97.5027}     # ~82 km from Reynosa
MEXICO_CITY = {"latitude": 19.4326, "longitude": -99.1332}   # ~740 km


async def _club_of(client, director) -> str:
    me = await client.get("/api/v1/auth/me", headers=director["headers"])
    assert me.status_code == 200, me.text
    return me.json()["organization_id"]


async def _approved_director(client, factory, label, ntam, admin, **club):
    director = await _register_director(client, factory, label, ntam, **club)
    club_id = await _club_of(client, director)
    ok = await client.post(f"{ORG}/{club_id}/approve", headers=admin["headers"])
    assert ok.status_code == 200, ok.text
    return director, club_id


async def test_nearby_clubs_by_distance(client, factory):
    ntam = await _find_ntam(client)
    node = await _ntam_node(ntam)
    admin = await factory.user("assoc-admin", role="ADMIN_ASSOCIATION", organization_id=node["id"])

    _, near = await _approved_director(client, factory, "near", ntam, admin, city="Reynosa", **REYNOSA)
    _, mid = await _approved_director(client, factory, "mid", ntam, admin, city="Matamoros", **MATAMOROS)
    _, far = await _approved_director(client, factory, "far", ntam, admin, **MEXICO_CITY)
    pending = await _register_director(client, factory, "pending", ntam, **REYNOSA)
    pending_club = await _club_of(client, pending)
    _, unpinned = await _approved_director(client, factory, "nopin", ntam, admin)

    here = {"lat": 26.06, "lon": -98.30}                          # a user standing in Reynosa
    response = await client.get(f"{ORG}/clubs/nearby", params={**here, "radius_km": 150})
    assert response.status_code == 200, response.text
    rows = [r for r in response.json() if r["id"] in {near, mid, far, unpinned, pending_club}]
    assert [r["id"] for r in rows] == [near, mid]                  # ordered by distance; far/pending/unpinned absent
    assert rows[0]["distance_km"] < 3 and 70 < rows[1]["distance_km"] < 95
    assert rows[0]["association"]["code"] == "NTAM" and rows[0]["city"] == "Reynosa"

    tight = await client.get(f"{ORG}/clubs/nearby", params={**here, "radius_km": 10})
    assert [r["id"] for r in tight.json() if r["id"] in {near, mid}] == [near]

    for bad in ({"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {"lat": 0, "lon": 0, "radius_km": 0}):
        assert (await client.get(f"{ORG}/clubs/nearby", params=bad)).status_code == 422


async def test_director_updates_own_club_location_only(client, factory):
    ntam = await _find_ntam(client)
    node = await _ntam_node(ntam)
    admin = await factory.user("assoc-admin-2", role="ADMIN_ASSOCIATION", organization_id=node["id"])
    director, club_id = await _approved_director(client, factory, "mover", ntam, admin)
    other, other_club = await _approved_director(client, factory, "other", ntam, admin)

    moved = await client.put(f"{ORG}/clubs/{club_id}/location", json=MATAMOROS, headers=director["headers"])
    assert moved.status_code == 200, moved.text
    row = await fetch_one("SELECT latitude, longitude FROM organizations WHERE id = :id", id=club_id)
    assert (round(row["latitude"], 3), round(row["longitude"], 3)) == (25.869, -97.503)

    assert (await client.put(f"{ORG}/clubs/{other_club}/location", json=REYNOSA,
                             headers=director["headers"])).status_code == 403
    assert (await client.put(f"{ORG}/clubs/{club_id}/location", json=REYNOSA)).status_code == 401
    assert (await client.put(f"{ORG}/clubs/{club_id}/location", json={"latitude": 95, "longitude": 0},
                             headers=director["headers"])).status_code == 422
    audited = await fetch_one("SELECT count(*) AS n FROM audit_log WHERE entity_id = :id AND action = 'CLUB_LOCATION'",
                              id=str(club_id))
    assert audited["n"] == 1
