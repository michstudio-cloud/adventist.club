"""Eventos y puntajes (spec docs/superpowers/specs/2026-09-24-eventos.md §3).

The rules that must never break:
  * only an association admin in scope (or MASTER_GC) creates events; event staff is
    contextual and never touches users.role;
  * only official clubs of the owner's subtree with the event's ministry register;
  * captures need IN_PROGRESS + REGISTERED + READY + an assigned judge; TO_DEFINE blocks only
    its own activity; a CLOSED event blocks everything;
  * a retry with the same idempotency_key never counts twice; corrections are revisions;
  * total = evaluations + bonus − penalty, no implicit floor; directors see only their club
    and the honour only after CLOSED; standings are for coordination only;
  * duplicate copies rules, never registrations, staff nor scores.
"""
import asyncio
import importlib.util
import pathlib
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, TEST_DATABASE_URL, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("events")
API = "/api/v1/events"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ministry(club: dict, slug: str) -> None:
    await _exec(
        "UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = :slug)"
        " WHERE id = :id", slug=slug, id=uuid.UUID(club["id"]))
    await _exec(
        "INSERT INTO organization_ministries (organization_id, ministry_id)"
        " SELECT :id, id FROM ministries WHERE slug = :slug ON CONFLICT DO NOTHING",
        slug=slug, id=uuid.UUID(club["id"]))


async def _delete_events(org_ids: list[str]) -> None:
    ids = [uuid.UUID(i) for i in org_ids]
    await _exec("DELETE FROM audit_log WHERE entity_type = 'EVENT' AND entity_id IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM events WHERE organization_id = ANY(:ids)",
                ids=[uuid.UUID(i) for i in org_ids])


@pytest_asyncio.fixture(scope="module")
async def w(factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    club = await factory.org("club-a", "club", church)
    club2 = await factory.org("club-b", "club", church)
    pathfinders = await factory.org("club-p", "club", church)
    other_assoc = await factory.org("assoc-2", "association")
    outside = await factory.org("club-out", "club", other_assoc)
    for node in (club, club2, outside):
        await _ministry(node, "adventurers")
    await _ministry(pathfinders, "pathfinders")
    code = f"{RUN}-EV"
    await _exec("UPDATE organizations SET code = :code WHERE id = :id", code=code, id=uuid.UUID(assoc["id"]))
    people = {
        "master": await factory.user("master", "MASTER_GC"),
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "admin_other": await factory.user("admin-2", "ADMIN_ASSOCIATION", other_assoc["id"]),
        "zone": await factory.user("zonecoord", "COORDINATOR_ZONE", zone["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR"),
        "judge": await factory.user("judge", "STUDENT"),
        "judge_all": await factory.user("judge-all", "STUDENT"),
        "stranger": await factory.user("stranger", "STUDENT"),
        "minor": await factory.user("minor", "STUDENT", is_minor=True),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "director2": await factory.user("director-2", "CLUB_DIRECTOR", club2["id"]),
    }
    world = {"assoc": assoc, "other_assoc": other_assoc, "club": club, "club2": club2,
             "pathfinders": pathfinders, "outside": outside, "code": code, **people, "s": {}}
    yield world
    await _delete_events([assoc["id"], other_assoc["id"]])


def _event_body(w, **extra) -> dict:
    return {"organization_id": w["assoc"]["id"], "ministry": "adventurers",
            "name": f"{RUN} Camporee de prueba", "starts_on": "2026-11-20", "ends_on": "2026-11-22",
            "honor_bands": [{"key": "primeros", "label": "Primeros", "min": 950},
                            {"key": "segundos", "label": "Segundos", "min": 800},
                            {"key": "terceros", "label": "Terceros", "min": None}], **extra}


# ----------------------------------------------------------------------------
# Crear: sólo la administración en su ámbito
# ----------------------------------------------------------------------------
async def test_who_creates_events(client, w):
    body = _event_body(w)
    assert (await client.post(API, json=body)).status_code == 401
    for who in ("admin_other", "zone", "director", "coordinator", "judge"):
        response = await client.post(API, json=body, headers=w[who]["headers"])
        assert response.status_code == 403, (who, response.text)
    bad_owner = await client.post(API, json={**body, "organization_id": w["club"]["id"]},
                                  headers=w["admin"]["headers"])
    assert bad_owner.status_code == 422
    backwards = await client.post(API, json={**body, "ends_on": "2026-11-01"}, headers=w["admin"]["headers"])
    assert backwards.status_code == 422
    master = await client.post(API, json={**body, "name": f"{RUN} Evento del máster"},
                               headers=w["master"]["headers"])
    assert master.status_code == 201, master.text
    created = await client.post(API, json=body, headers=w["admin"]["headers"])
    assert created.status_code == 201, created.text
    event = created.json()
    assert event["status"] == "DRAFT" and event["ministry"]["slug"] == "adventurers"
    assert event["my_roles"] == ["ADMIN"] and event["slug"].startswith(RUN.lower())
    w["s"]["event"] = event["id"]
    # Anyone without a role in it: 403. The other association's admin too.
    for who in ("stranger", "admin_other", "director"):
        assert (await client.get(f"{API}/{event['id']}", headers=w[who]["headers"])).status_code == 403


async def test_build_activities_and_rules(client, w):
    event_id, h = w["s"]["event"], w["admin"]["headers"]

    async def add(body, expect=201):
        response = await client.post(f"{API}/{event_id}/activities", json=body, headers=h)
        assert response.status_code == expect, response.text
        return response.json()

    w["s"]["participation"] = (await add({"name": "Señal", "kind": "participation", "max_points": 150,
                                          "config": {"max": 150}}))["id"]
    group = await add({"name": "Conexión", "kind": "group", "max_points": 100})
    w["s"]["group"] = group["id"]
    w["s"]["bands"] = (await add({"name": "Ronda", "kind": "bands", "parent_id": group["id"], "max_points": 80,
                                  "config": {"bands": [{"min": 0, "max": 9, "points": 60},
                                                       {"min": 10, "max": 18, "points": 80}]}}))["id"]
    w["s"]["final"] = (await add({"name": "Final", "kind": "per_correct", "parent_id": group["id"],
                                  "max_points": 20, "config": {"points_each": 2, "max_items": 10,
                                                               "finalists_only": True}}))["id"]
    w["s"]["rubric"] = (await add({"name": "Marcha", "kind": "rubric", "max_points": 50, "config": {
        "criteria": [{"key": "voz", "label": "Voz", "max": 30, "deduction_step": 2},
                     {"key": "tiempo", "label": "Tiempo", "max": 20}]}}))["id"]
    tbd = await add({"name": "Inspección", "kind": "rubric", "max_points": 50, "status": "TO_DEFINE",
                     "config": {"criteria": [{"key": "carpas", "label": "Carpas", "max": None}]}})
    assert tbd["config_complete"] is False
    w["s"]["to_define"] = tbd["id"]
    # Strict validation: incomplete READY, max mismatch, formulas, nested groups.
    await add({"name": "X", "kind": "rubric", "config": {"criteria": [{"key": "a", "label": "A", "max": None}]}}, 422)
    await add({"name": "X", "kind": "participation", "max_points": 10, "config": {"max": 20}}, 422)
    await add({"name": "X", "kind": "participation", "config": {"max": 5, "formula": "x*2"}}, 422)
    await add({"name": "X", "kind": "group", "parent_id": group["id"]}, 422)
    await add({"name": "X", "kind": "participation", "parent_id": w["s"]["rubric"], "config": {"max": 1}}, 422)
    listed = (await client.get(f"{API}/{event_id}/activities", headers=h)).json()
    assert [a["name"] for a in listed] == ["Señal", "Conexión", "Ronda", "Final", "Marcha", "Inspección"]
    # Reorder the top level.
    top = [a["id"] for a in listed if a["parent_id"] is None]
    reordered = await client.post(f"{API}/{event_id}/activities/reorder", json={"ids": top[::-1]}, headers=h)
    assert reordered.status_code == 200, reordered.text
    assert [a["name"] for a in reordered.json() if a["parent_id"] is None][0] == "Inspección"
    bad = await client.post(f"{API}/{event_id}/activities/reorder", json={"ids": top[:2]}, headers=h)
    assert bad.status_code == 422
    # Adjustment types, one «por definir».
    for body in ({"kind": "BONUS", "label": "Ganador final", "points": 50, "max_per_event": 1},
                 {"kind": "PENALTY", "label": "Área sucia", "points": 50, "max_per_club": 1},
                 {"kind": "PENALTY", "label": "Disciplina: toque de queda"},
                 {"kind": "PENALTY", "label": "Otros criterios", "amount_mode": "FREE", "max_points": 20}):
        response = await client.post(f"{API}/{event_id}/adjustment-types", json=body, headers=h)
        assert response.status_code == 201, response.text
        w["s"][body["label"]] = response.json()["id"]
    free = response.json()
    assert free["amount_mode"] == "FREE" and free["max_points"] == 20 and free["to_define"] is False
    types = {t["label"]: t for t in (await client.get(f"{API}/{event_id}/adjustment-types", headers=h)).json()}
    assert types["Disciplina: toque de queda"]["to_define"] is True
    assert types["Área sucia"]["amount_mode"] == "FIXED" and types["Área sucia"]["to_define"] is False
    # A FIXED type carries no bound and a FREE type no fixed amount.
    for bad in ({"kind": "PENALTY", "label": "X", "amount_mode": "FREE", "points": 5},
                {"kind": "PENALTY", "label": "X", "amount_mode": "FIXED", "max_points": 5}):
        assert (await client.post(f"{API}/{event_id}/adjustment-types", json=bad, headers=h)).status_code == 422
    # Mode is editable while unused: FIXED -> FREE -> FIXED with the amount defined.
    scratch = (await client.post(f"{API}/{event_id}/adjustment-types",
                                 json={"kind": "BONUS", "label": "Temporal", "points": 5}, headers=h)).json()
    url = f"{API}/{event_id}/adjustment-types/{scratch['id']}"
    to_free = await client.patch(url, json={"amount_mode": "FREE", "max_points": 10}, headers=h)
    assert to_free.status_code == 200 and to_free.json()["points"] is None and to_free.json()["max_points"] == 10
    assert (await client.patch(url, json={"points": 3}, headers=h)).status_code == 422
    back = await client.patch(url, json={"amount_mode": "FIXED", "points": 7}, headers=h)
    assert back.status_code == 200 and back.json()["points"] == 7 and back.json()["max_points"] is None
    assert (await client.patch(url, json={"active": False}, headers=h)).status_code == 200


# ----------------------------------------------------------------------------
# Personal contextual
# ----------------------------------------------------------------------------
async def test_staff_is_contextual(client, w):
    event_id, h = w["s"]["event"], w["admin"]["headers"]
    url = f"{API}/{event_id}/staff"
    before = await fetch_one("SELECT role FROM users WHERE id = :id", id=uuid.UUID(w["judge"]["id"]))
    for body in ({"user_id": w["coordinator"]["id"], "role": "COORDINATOR"},
                 {"email": w["judge"]["email"], "role": "JUDGE", "activity_id": w["s"]["rubric"]},
                 {"email": w["judge"]["email"], "role": "JUDGE", "activity_id": w["s"]["group"]},
                 {"user_id": w["judge_all"]["id"], "role": "JUDGE"}):
        response = await client.post(url, json=body, headers=h)
        assert response.status_code == 201, response.text
    assert (await client.post(url, json={"user_id": w["judge_all"]["id"], "role": "JUDGE"},
                              headers=h)).status_code == 409
    assert (await client.post(url, json={"user_id": w["minor"]["id"], "role": "JUDGE"},
                              headers=h)).status_code == 403
    assert (await client.post(url, json={"user_id": w["judge"]["id"], "role": "COORDINATOR",
                                         "activity_id": w["s"]["rubric"]}, headers=h)).status_code == 422
    assert (await client.post(url, json={"email": "nobody@example.com", "role": "JUDGE"},
                              headers=h)).status_code == 404
    # A judge does not manage staff; a coordinator does.
    assert (await client.get(url, headers=w["judge"]["headers"])).status_code == 403
    listed = await client.get(url, headers=w["coordinator"]["headers"])
    assert listed.status_code == 200 and len(listed.json()) == 4
    after = await fetch_one("SELECT role FROM users WHERE id = :id", id=uuid.UUID(w["judge"]["id"]))
    assert before["role"] == after["role"] == "STUDENT"
    mine = (await client.get(API, headers=w["judge"]["headers"])).json()
    assert [e["id"] for e in mine] == [event_id] and mine[0]["my_roles"] == ["JUDGE"]


# ----------------------------------------------------------------------------
# Inscripciones
# ----------------------------------------------------------------------------
async def test_registrations_only_official_clubs_in_scope(client, w):
    event_id = w["s"]["event"]
    url = f"{API}/{event_id}/registrations"
    assert (await client.post(url, json={"club_id": w["club"]["id"]},
                              headers=w["judge"]["headers"])).status_code == 403
    first = await client.post(url, json={"club_id": w["club"]["id"]}, headers=w["admin"]["headers"])
    assert first.status_code == 201, first.text
    assert first.json()["pass_token"].startswith("evp_") and first.json()["has_pass"]
    w["s"]["reg"], w["s"]["token"] = first.json()["id"], first.json()["pass_token"]
    stored = await fetch_one("SELECT pass_token_hash FROM event_registrations WHERE id = :id",
                             id=uuid.UUID(w["s"]["reg"]))
    assert stored["pass_token_hash"] != w["s"]["token"] and len(stored["pass_token_hash"]) == 64
    again = await client.post(url, json={"club_id": w["club"]["id"]}, headers=w["admin"]["headers"])
    assert again.status_code == 409
    for club in ("pathfinders", "outside"):
        response = await client.post(url, json={"club_id": w[club]["id"]}, headers=w["admin"]["headers"])
        assert response.status_code == 422, (club, response.text)
    assert (await client.post(url, json={"club_id": w["assoc"]["id"]},
                              headers=w["admin"]["headers"])).status_code == 404
    second = await client.post(url, json={"club_id": w["club2"]["id"]}, headers=w["coordinator"]["headers"])
    assert second.status_code == 201
    w["s"]["reg2"] = second.json()["id"]
    # Directors list only their own club; judges see every club (manual search).
    own = (await client.get(url, headers=w["director"]["headers"])).json()
    assert [r["id"] for r in own] == [w["s"]["reg"]]
    assert len((await client.get(url, headers=w["judge"]["headers"])).json()) == 2


async def test_resolve_pass_identifies_but_authorizes_nothing(client, w):
    url = f"{API}/{w['s']['event']}/resolve-pass"
    found = await client.post(url, json={"token": w["s"]["token"]}, headers=w["judge"]["headers"])
    assert found.status_code == 200 and found.json()["id"] == w["s"]["reg"]
    assert "pass_token" not in found.json() or found.json()["pass_token"] is None
    assert (await client.post(url, json={"token": "evp_" + "x" * 30},
                              headers=w["judge"]["headers"])).status_code == 404
    assert (await client.post(url, json={"token": w["s"]["token"]},
                              headers=w["director"]["headers"])).status_code == 403
    assert (await client.post(url, json={"token": w["s"]["token"]},
                              headers=w["stranger"]["headers"])).status_code == 403


async def test_director_regenerates_only_their_own_pass(client, w):
    event_id = w["s"]["event"]
    own = await client.post(f"{API}/{event_id}/registrations/{w['s']['reg']}/pass",
                            headers=w["director"]["headers"])
    assert own.status_code == 200 and own.json()["pass_token"] != w["s"]["token"]
    old = await client.post(f"{API}/{event_id}/resolve-pass", json={"token": w["s"]["token"]},
                            headers=w["judge"]["headers"])
    assert old.status_code == 404  # the old QR stops working
    w["s"]["token"] = own.json()["pass_token"]
    other = await client.post(f"{API}/{event_id}/registrations/{w['s']['reg2']}/pass",
                              headers=w["director"]["headers"])
    assert other.status_code == 403


# ----------------------------------------------------------------------------
# Capturas
# ----------------------------------------------------------------------------
def _capture(w, activity: str, inputs: dict, key: str | None = None, reg: str = "reg", **extra) -> dict:
    return {"registration_id": w["s"][reg], "activity_id": w["s"][activity], "inputs": inputs,
            "idempotency_key": key or f"k-{uuid.uuid4().hex}", **extra}


async def test_captures_are_blocked_until_in_progress(client, w):
    url = f"{API}/{w['s']['event']}/evaluations"
    body = _capture(w, "rubric", {"criteria": {"voz": 30, "tiempo": 20}})
    blocked = await client.post(url, json=body, headers=w["judge"]["headers"])
    assert blocked.status_code == 409 and "en curso" in blocked.json()["detail"]
    # Status transitions by coordination only; illegal jumps refused.
    status_url = f"{API}/{w['s']['event']}/status"
    assert (await client.post(status_url, json={"status": "OPEN"},
                              headers=w["judge"]["headers"])).status_code == 403
    assert (await client.post(status_url, json={"status": "CLOSED"},
                              headers=w["admin"]["headers"])).status_code == 409
    for step in ("OPEN", "IN_PROGRESS"):
        response = await client.post(status_url, json={"status": step}, headers=w["coordinator"]["headers"])
        assert response.status_code == 200 and response.json()["status"] == step


async def test_capture_gates(client, w):
    url = f"{API}/{w['s']['event']}/evaluations"
    # Not assigned to this activity.
    response = await client.post(url, json=_capture(w, "participation", {"points": 10}),
                                 headers=w["judge"]["headers"])
    assert response.status_code == 403
    # TO_DEFINE blocks its own activity only.
    response = await client.post(url, json=_capture(w, "to_define", {"criteria": {"carpas": 1}}),
                                 headers=w["judge_all"]["headers"])
    assert response.status_code == 409 and "por definir" in response.json()["detail"]
    # A group is never captured.
    response = await client.post(url, json=_capture(w, "group", {}), headers=w["judge_all"]["headers"])
    assert response.status_code == 422
    # Judge of the group judges its rounds; out-of-range facts are 422.
    response = await client.post(url, json=_capture(w, "bands", {"correct": 19}), headers=w["judge"]["headers"])
    assert response.status_code == 422
    response = await client.post(url, json=_capture(w, "bands", {"correct": 0}), headers=w["judge"]["headers"])
    assert response.status_code == 201 and response.json()["points"] == 60
    # Finalists only.
    final = _capture(w, "final", {"correct": 10})
    response = await client.post(url, json=final, headers=w["judge"]["headers"])
    assert response.status_code == 409 and "finalista" in response.json()["detail"]
    flagged = await client.patch(f"{API}/{w['s']['event']}/registrations/{w['s']['reg']}",
                                 json={"finalist_flags": {w["s"]["final"]: True}},
                                 headers=w["coordinator"]["headers"])
    assert flagged.status_code == 200, flagged.text
    response = await client.post(url, json=final, headers=w["judge"]["headers"])
    assert response.status_code == 201 and response.json()["points"] == 20
    # Stale rules on the judge's screen.
    stale = _capture(w, "participation", {"points": 1}, rules_version=1)
    assert (await client.post(url, json=stale, headers=w["judge_all"]["headers"])).status_code == 409


async def test_idempotency_never_counts_twice(client, w):
    url = f"{API}/{w['s']['event']}/evaluations"
    body = _capture(w, "rubric", {"criteria": {"voz": {"deductions": 2}, "tiempo": 20}}, key=f"{RUN}-idem-1")
    first = await client.post(url, json=body, headers=w["judge"]["headers"])
    assert first.status_code == 201, first.text
    assert first.json()["points"] == 46 and first.json()["created"] is True
    retry = await client.post(url, json=body, headers=w["judge"]["headers"])
    assert retry.status_code == 200 and retry.json()["id"] == first.json()["id"]
    assert retry.json()["created"] is False
    rows = await fetch_all("SELECT id FROM evaluations WHERE registration_id = :r AND activity_id = :a",
                           r=uuid.UUID(w["s"]["reg"]), a=uuid.UUID(w["s"]["rubric"]))
    assert len(rows) == 1
    other_inputs = {**body, "inputs": {"criteria": {"voz": 1, "tiempo": 1}}}
    assert (await client.post(url, json=other_inputs, headers=w["judge"]["headers"])).status_code == 409
    new_key = {**body, "idempotency_key": f"{RUN}-idem-2"}
    duplicate = await client.post(url, json=new_key, headers=w["judge"]["headers"])
    assert duplicate.status_code == 409 and "vigente" in duplicate.json()["detail"]
    w["s"]["eval"] = first.json()["id"]


async def test_corrections_are_revisions(client, w):
    event_id = w["s"]["event"]
    url = f"{API}/{event_id}/evaluations/{w['s']['eval']}"
    body = {"inputs": {"criteria": {"voz": 30, "tiempo": 10}}, "expected_revision": 1,
            "reason": "Se contó mal el tiempo", "idempotency_key": f"{RUN}-fix-1"}
    assert (await client.patch(url, json={**body, "reason": "   "},
                               headers=w["judge"]["headers"])).status_code == 422
    assert (await client.patch(url, json={**body, "expected_revision": 7},
                               headers=w["judge"]["headers"])).status_code == 409
    assert (await client.patch(url, json=body, headers=w["judge_all"]["headers"])).status_code == 200
    # judge_all corrected to revision 2; a retry of the same key by the same person is a replay.
    replay = await client.patch(url, json=body, headers=w["judge_all"]["headers"])
    assert replay.status_code == 200 and replay.json()["revision"] == 2 and replay.json()["points"] == 40
    assert (await client.patch(url, json=body, headers=w["judge"]["headers"])).status_code == 409
    history = await client.get(f"{url}/revisions", headers=w["coordinator"]["headers"])
    assert history.status_code == 200
    [old] = history.json()
    assert old["revision"] == 1 and old["points"] == 46 and old["reason"] == "Se contó mal el tiempo"
    assert old["action"] == "CORRECTION"
    with pytest.raises(Exception, match="inmutable"):
        await _exec("UPDATE evaluation_revisions SET reason = 'x' WHERE evaluation_id = :id",
                    id=uuid.UUID(w["s"]["eval"]))
    # Rules of an activity with live evaluations do not change under them.
    changed = await client.patch(f"{API}/{event_id}/activities/{w['s']['rubric']}", json={"config": {
        "criteria": [{"key": "voz", "label": "Voz", "max": 40}, {"key": "tiempo", "label": "Tiempo", "max": 10}]}},
        headers=w["admin"]["headers"])
    assert changed.status_code == 409


async def test_void_and_recapture(client, w):
    event_id = w["s"]["event"]
    url = f"{API}/{event_id}/evaluations"
    first = await client.post(url, json=_capture(w, "participation", {"points": 100}, reg="reg2"),
                              headers=w["judge_all"]["headers"])
    assert first.status_code == 201
    void_url = f"{url}/{first.json()['id']}/void"
    assert (await client.post(void_url, json={"reason": "Club equivocado"},
                              headers=w["judge_all"]["headers"])).status_code == 403
    voided = await client.post(void_url, json={"reason": "Club equivocado"}, headers=w["coordinator"]["headers"])
    assert voided.status_code == 200 and voided.json()["status"] == "VOID"
    again = await client.post(url, json=_capture(w, "participation", {"points": 90}, reg="reg2"),
                              headers=w["judge_all"]["headers"])
    assert again.status_code == 201 and again.json()["points"] == 90


# ----------------------------------------------------------------------------
# Ajustes y total
# ----------------------------------------------------------------------------
async def test_adjustments_and_total(client, w):
    event_id = w["s"]["event"]
    url = f"{API}/{event_id}/adjustments"
    bonus = await client.post(url, json={"registration_id": w["s"]["reg"], "adjustment_type_id": w["s"]["Ganador final"],
                                         "reason": "Ganó la final"}, headers=w["coordinator"]["headers"])
    assert bonus.status_code == 201 and bonus.json()["status"] == "APPROVED" and bonus.json()["points"] == 50
    second = await client.post(url, json={"registration_id": w["s"]["reg2"],
                                          "adjustment_type_id": w["s"]["Ganador final"], "reason": "Otra"},
                               headers=w["coordinator"]["headers"])
    assert second.status_code == 409  # uno por evento
    undefined = await client.post(url, json={"registration_id": w["s"]["reg"],
                                             "adjustment_type_id": w["s"]["Disciplina: toque de queda"],
                                             "reason": "Ruido a las 2 a.m."}, headers=w["coordinator"]["headers"])
    assert undefined.status_code == 409 and "por definir" in undefined.json()["detail"]
    proposed = await client.post(url, json={"registration_id": w["s"]["reg"], "adjustment_type_id": w["s"]["Área sucia"],
                                            "reason": "Basura en el área"}, headers=w["judge"]["headers"])
    assert proposed.status_code == 201 and proposed.json()["status"] == "PENDING"
    assert (await client.post(url, json={"registration_id": w["s"]["reg"], "kind": "PENALTY", "points": 5,
                                         "reason": "libre"}, headers=w["judge"]["headers"])).status_code == 403
    breakdown_url = f"{API}/{event_id}/registrations/{w['s']['reg']}/breakdown"
    b = (await client.get(breakdown_url, headers=w["admin"]["headers"])).json()
    # bands 60 + final 20 + rubric 40 = 120; + bonus 50; the pending penalty does not count yet.
    assert b["evaluated_points"] == 120 and b["bonus"] == 50 and b["penalty"] == 0 and b["total"] == 170
    assert len(b["pending_adjustments"]) == 1
    group = next(a for a in b["activities"] if a["kind"] == "group")
    assert group["points"] == 80 and group["state"] == "scored"
    states = {a["name"]: a["state"] for a in b["activities"] if a["kind"] != "group"}
    assert states["Señal"] == "pending" and states["Inspección"] == "to_define"
    assert b["progress"] == {"done": 3, "expected": 5, "complete": False}
    approved = await client.post(f"{url}/{proposed.json()['id']}/approve", headers=w["coordinator"]["headers"])
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    # No implicit floor: a big free penalty takes the total below zero.
    free = await client.post(url, json={"registration_id": w["s"]["reg"], "kind": "PENALTY", "points": 500,
                                        "reason": "Prueba sin piso"}, headers=w["admin"]["headers"])
    assert free.status_code == 201
    b = (await client.get(breakdown_url, headers=w["admin"]["headers"])).json()
    assert b["total"] == 120 + 50 - 50 - 500
    assert (await client.post(f"{url}/{free.json()['id']}/void", json={"reason": "Era prueba"},
                              headers=w["admin"]["headers"])).status_code == 200
    b = (await client.get(breakdown_url, headers=w["admin"]["headers"])).json()
    assert b["total"] == 120 and b["honor"]["key"] == "terceros"
    # FREE type: the amount comes with each adjustment, within the type's bound.
    other = w["s"]["Otros criterios"]
    base = {"registration_id": w["s"]["reg"], "adjustment_type_id": other, "reason": "Pleito en fila"}
    assert (await client.post(url, json=base, headers=w["coordinator"]["headers"])).status_code == 422
    over = await client.post(url, json={**base, "points": 25}, headers=w["coordinator"]["headers"])
    assert over.status_code == 422 and "máximo" in over.json()["detail"]
    applied = await client.post(url, json={**base, "points": 7.5}, headers=w["coordinator"]["headers"])
    assert applied.status_code == 201 and applied.json()["points"] == 7.5
    assert applied.json()["status"] == "APPROVED" and applied.json()["label"] == "Otros criterios"
    by_judge = await client.post(url, json={**base, "points": 3}, headers=w["judge"]["headers"])
    assert by_judge.status_code == 201 and by_judge.json()["status"] == "PENDING"
    b = (await client.get(breakdown_url, headers=w["admin"]["headers"])).json()
    assert b["total"] == 112.5 and len(b["pending_adjustments"]) == 1
    # A used type no longer changes its amount mode.
    used = await client.patch(f"{API}/{event_id}/adjustment-types/{other}", json={"amount_mode": "FIXED"},
                              headers=w["admin"]["headers"])
    assert used.status_code == 409
    for done in (applied, by_judge):
        assert (await client.post(f"{url}/{done.json()['id']}/void", json={"reason": "Prueba"},
                                  headers=w["admin"]["headers"])).status_code == 200


async def test_director_sees_only_their_club(client, w):
    event_id = w["s"]["event"]
    my = await client.get(f"{API}/{event_id}/my", headers=w["director"]["headers"])
    assert my.status_code == 200, my.text
    view = my.json()
    assert view["roles"] == ["DIRECTOR"] and "coordination" not in view and "judge" not in view
    [mine] = view["director"]["registrations"]
    assert mine["registration_id"] == w["s"]["reg"] and mine["honor"] is None
    assert "pending_adjustments" not in mine
    assert (await client.get(f"{API}/{event_id}/registrations/{w['s']['reg2']}/breakdown",
                             headers=w["director"]["headers"])).status_code == 403
    own = await client.get(f"{API}/{event_id}/registrations/{w['s']['reg']}/breakdown",
                           headers=w["director"]["headers"])
    assert own.status_code == 200 and own.json()["honor"] is None
    for who in ("director", "judge", "stranger"):
        assert (await client.get(f"{API}/{event_id}/standings", headers=w[who]["headers"])).status_code == 403
    assert (await client.get(f"{API}/{event_id}/my", headers=w["stranger"]["headers"])).status_code == 403
    assert (await client.get(f"{API}/{event_id}/my")).status_code == 401
    judge = (await client.get(f"{API}/{event_id}/my", headers=w["judge"]["headers"])).json()
    assert {a["name"] for a in judge["judge"]["activities"]} == {"Conexión", "Ronda", "Final", "Marcha"}
    coord = (await client.get(f"{API}/{event_id}/my", headers=w["coordinator"]["headers"])).json()
    assert [a["name"] for a in coord["coordination"]["to_define"]["activities"]] == ["Inspección"]
    assert [t["label"] for t in coord["coordination"]["to_define"]["adjustment_types"]] == ["Disciplina: toque de queda"]
    standings = (await client.get(f"{API}/{event_id}/standings", headers=w["coordinator"]["headers"])).json()
    assert [row["registration_id"] for row in standings["rows"]] == [w["s"]["reg"], w["s"]["reg2"]]
    assert standings["rows"][0]["total"] == 120 and standings["rows"][1]["total"] == 90


async def test_manual_tiebreak_is_coordinations_and_hidden_from_others(client, w):
    event_id = w["s"]["event"]
    reg_url = f"{API}/{event_id}/registrations/{w['s']['reg2']}"
    standings_url = f"{API}/{event_id}/standings"
    # Tie the two clubs at 120 (raw totals equal too): without a rank, the name decides.
    bonus = await client.post(f"{API}/{event_id}/adjustments", json={
        "registration_id": w["s"]["reg2"], "kind": "BONUS", "points": 30, "reason": "Empate de prueba"},
        headers=w["coordinator"]["headers"])
    assert bonus.status_code == 201
    rows = (await client.get(standings_url, headers=w["coordinator"]["headers"])).json()["rows"]
    assert [r["registration_id"] for r in rows] == [w["s"]["reg"], w["s"]["reg2"]]
    assert rows[0]["total"] == rows[1]["total"] == 120 and rows[1]["tiebreak_rank"] is None
    # A reason is mandatory; judges and directors never set it.
    assert (await client.patch(reg_url, json={"tiebreak_rank": 1}, headers=w["coordinator"]["headers"])).status_code == 422
    assert (await client.patch(reg_url, json={"tiebreak_rank": 0, "reason": "x"},
                               headers=w["coordinator"]["headers"])).status_code == 422
    for who in ("judge", "director2"):
        assert (await client.patch(reg_url, json={"tiebreak_rank": 1, "reason": "yo"},
                                   headers=w[who]["headers"])).status_code == 403
    ranked = await client.patch(reg_url, json={"tiebreak_rank": 1, "reason": "Mejor en la final"},
                                headers=w["coordinator"]["headers"])
    assert ranked.status_code == 200 and ranked.json()["tiebreak_rank"] == 1
    assert ranked.json()["finalist_flags"] == {}  # untouched
    rows = (await client.get(standings_url, headers=w["admin"]["headers"])).json()["rows"]
    assert [(r["registration_id"], r["tiebreak_rank"], r["position"]) for r in rows] == [
        (w["s"]["reg2"], 1, 1), (w["s"]["reg"], None, 2)]
    audit = await fetch_one("SELECT details, metadata_json FROM audit_log WHERE action = 'EVENT_TIEBREAK'"
                            " AND entity_id = :id", id=w["s"]["reg2"])
    assert audit["details"] == "Mejor en la final" and audit["metadata_json"]["to"] == 1
    # Coordination reads it; judges and directors do not even get the key.
    listing = f"{API}/{event_id}/registrations"
    coord = {r["id"]: r for r in (await client.get(listing, headers=w["coordinator"]["headers"])).json()}
    assert coord[w["s"]["reg2"]]["tiebreak_rank"] == 1 and coord[w["s"]["reg"]]["tiebreak_rank"] is None
    for who in ("judge", "director2"):
        for row in (await client.get(listing, headers=w[who]["headers"])).json():
            assert "tiebreak_rank" not in row
    passed = await client.post(f"{reg_url}/pass", headers=w["director2"]["headers"])
    assert passed.status_code == 200 and "tiebreak_rank" not in passed.json()
    resolved = await client.post(f"{API}/{event_id}/resolve-pass", json={"token": passed.json()["pass_token"]},
                                 headers=w["judge"]["headers"])
    assert "tiebreak_rank" not in resolved.json()
    director = (await client.get(f"{reg_url}/breakdown", headers=w["director2"]["headers"])).json()
    assert "tiebreak_rank" not in director
    # Cleared again, and the tie undone so the rest of the module keeps its numbers.
    cleared = await client.patch(reg_url, json={"tiebreak_rank": None, "reason": "Se deshace"},
                                 headers=w["coordinator"]["headers"])
    assert cleared.status_code == 200 and cleared.json()["tiebreak_rank"] is None
    assert (await client.post(f"{API}/{event_id}/adjustments/{bonus.json()['id']}/void",
                              json={"reason": "Fin de prueba"}, headers=w["admin"]["headers"])).status_code == 200


async def test_coordination_may_enter_the_first_evaluation(client, w):
    """Owner decision: when a judge is missing, coordination (COORDINATOR staff or an event admin)
    enters the first evaluation, with the same gates, validation and idempotency, recorded."""
    url = f"{API}/{w['s']['event']}/evaluations"
    body = _capture(w, "participation", {"points": 10}, key=f"{RUN}-coord-1")
    assert (await client.post(url, json={**body, "inputs": {"points": 151}},
                              headers=w["coordinator"]["headers"])).status_code == 422
    first = await client.post(url, json=body, headers=w["coordinator"]["headers"])
    assert first.status_code == 201, first.text
    assert first.json()["captured_as"] == "COORDINATION" and first.json()["judge_id"] == w["coordinator"]["id"]
    retry = await client.post(url, json=body, headers=w["coordinator"]["headers"])
    assert retry.status_code == 200 and retry.json()["id"] == first.json()["id"]
    audit = await fetch_one("SELECT user_id, metadata_json FROM audit_log WHERE action = 'EVALUATION_CREATE'"
                            " AND entity_id = :id", id=first.json()["id"])
    assert str(audit["user_id"]) == w["coordinator"]["id"]
    assert audit["metadata_json"]["captured_as"] == "COORDINATION"
    # The platform admin of the event too; TO_DEFINE still blocks coordination.
    blocked = await client.post(url, json=_capture(w, "to_define", {"criteria": {"carpas": 1}}),
                                headers=w["admin"]["headers"])
    assert blocked.status_code == 409 and "por definir" in blocked.json()["detail"]
    judged = (await client.get(f"{url}?activity_id={w['s']['rubric']}", headers=w["admin"]["headers"])).json()
    assert {e["captured_as"] for e in judged} == {"JUDGE"}
    # Neither a director nor a stranger captures.
    for who in ("director", "stranger"):
        assert (await client.post(url, json=_capture(w, "participation", {"points": 1}, reg="reg2"),
                                  headers=w[who]["headers"])).status_code == 403
    b = (await client.get(f"{API}/{w['s']['event']}/registrations/{w['s']['reg']}/breakdown",
                          headers=w["admin"]["headers"])).json()
    assert b["total"] == 130


async def test_closed_event_blocks_everything(client, w):
    event_id = w["s"]["event"]
    closed = await client.post(f"{API}/{event_id}/status", json={"status": "CLOSED"},
                               headers=w["coordinator"]["headers"])
    assert closed.status_code == 200
    capture = await client.post(f"{API}/{event_id}/evaluations",
                                json=_capture(w, "participation", {"points": 1}), headers=w["judge_all"]["headers"])
    assert capture.status_code == 409
    fix = await client.patch(f"{API}/{event_id}/evaluations/{w['s']['eval']}",
                             json={"inputs": {"criteria": {"voz": 1, "tiempo": 1}}, "expected_revision": 2,
                                   "reason": "tarde"}, headers=w["coordinator"]["headers"])
    assert fix.status_code == 409
    for method, path, body in (
        ("patch", f"/activities/{w['s']['participation']}", {"name": "Otro"}),
        ("post", "/registrations", {"club_id": w["club2"]["id"]}),
        ("post", "/staff", {"user_id": w["stranger"]["id"], "role": "JUDGE"}),
        ("post", "/adjustments", {"registration_id": w["s"]["reg"], "kind": "BONUS", "points": 1, "reason": "x"}),
        ("patch", "", {"name": "Cambio tardío"}),
        ("patch", f"/registrations/{w['s']['reg']}", {"tiebreak_rank": 1, "reason": "tarde"}),
    ):
        response = await getattr(client, method)(f"{API}/{event_id}{path}", json=body, headers=w["admin"]["headers"])
        assert response.status_code == 409, (path, response.text)
    # Now the director sees the honour.
    own = (await client.get(f"{API}/{event_id}/registrations/{w['s']['reg']}/breakdown",
                            headers=w["director"]["headers"])).json()
    assert own["honor"]["key"] == "terceros" and own["honor_final"] is True
    # Reopening needs a reason.
    status_url = f"{API}/{event_id}/status"
    assert (await client.post(status_url, json={"status": "IN_PROGRESS"},
                              headers=w["coordinator"]["headers"])).status_code == 422
    reopened = await client.post(status_url, json={"status": "IN_PROGRESS", "reason": "Faltó una inspección"},
                                 headers=w["coordinator"]["headers"])
    assert reopened.status_code == 200
    audit = await fetch_one("SELECT details FROM audit_log WHERE entity_id = :id AND action = 'EVENT_STATUS'"
                            " ORDER BY created_at DESC LIMIT 1", id=event_id)
    assert audit["details"] == "Faltó una inspección"


async def test_concurrent_retries_count_once(client, w):
    """Three identical requests at once (a flaky network): one 201, the others replay it."""
    url = f"{API}/{w['s']['event']}/evaluations"
    body = _capture(w, "rubric", {"criteria": {"voz": 10, "tiempo": 10}}, key=f"{RUN}-race", reg="reg2")
    responses = await asyncio.gather(*[client.post(url, json=body, headers=w["judge"]["headers"])
                                       for _ in range(3)])
    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 200, 201], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    rows = await fetch_all("SELECT id FROM evaluations WHERE registration_id = :r AND activity_id = :a",
                           r=uuid.UUID(w["s"]["reg2"]), a=uuid.UUID(w["s"]["rubric"]))
    assert len(rows) == 1
    # Two different keys for the same club and activity at once: one wins, the other is 409.
    twins = [_capture(w, "final", {"correct": 3}, reg="reg2") for _ in range(2)]
    await client.patch(f"{API}/{w['s']['event']}/registrations/{w['s']['reg2']}",
                       json={"finalist_flags": {w["s"]["final"]: True}}, headers=w["coordinator"]["headers"])
    responses = await asyncio.gather(*[client.post(url, json=t, headers=w["judge"]["headers"]) for t in twins])
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    assert (await client.post(url, json=_capture(w, "rubric", {"criteria": {"voz": 1, "tiempo": 1}}),
                              headers=w["stranger"]["headers"])).status_code == 403


async def test_coordinator_limits_and_delete(client, w):
    event_id = w["s"]["event"]
    assert (await client.delete(f"{API}/{event_id}", headers=w["coordinator"]["headers"])).status_code == 403
    moved = await client.patch(f"{API}/{event_id}", json={"organization_id": w["other_assoc"]["id"]},
                               headers=w["coordinator"]["headers"])
    assert moved.status_code == 403
    assert (await client.delete(f"{API}/{event_id}", headers=w["admin"]["headers"])).status_code == 409


async def test_duplicate_copies_rules_only(client, w):
    event_id = w["s"]["event"]
    body = {"name": f"{RUN} Camporee 2027", "starts_on": "2027-11-19", "ends_on": "2027-11-21"}
    assert (await client.post(f"{API}/{event_id}/duplicate", json=body,
                              headers=w["coordinator"]["headers"])).status_code == 403
    copy = await client.post(f"{API}/{event_id}/duplicate", json=body, headers=w["admin"]["headers"])
    assert copy.status_code == 201, copy.text
    new = copy.json()
    assert new["status"] == "DRAFT" and new["template_of_id"] == event_id and new["rules_version"] == 1
    assert new["honor_bands"] == (await client.get(f"{API}/{event_id}", headers=w["admin"]["headers"])).json()["honor_bands"]
    source = (await client.get(f"{API}/{event_id}/activities", headers=w["admin"]["headers"])).json()
    copied = (await client.get(f"{API}/{new['id']}/activities", headers=w["admin"]["headers"])).json()
    assert [(a["name"], a["kind"], a["status"], a["config"]) for a in source] == \
        [(a["name"], a["kind"], a["status"], a["config"]) for a in copied]
    names = {a["id"]: a["name"] for a in copied}
    assert {names[a["parent_id"]] for a in copied if a["parent_id"]} == {"Conexión"}
    assert not {a["id"] for a in copied} & {a["id"] for a in source}
    types = (await client.get(f"{API}/{new['id']}/adjustment-types", headers=w["admin"]["headers"])).json()
    assert [(t["label"], t["amount_mode"], t["points"], t["max_points"], t["active"]) for t in types] == [
        ("Ganador final", "FIXED", 50, None, True), ("Área sucia", "FIXED", 50, None, True),
        ("Disciplina: toque de queda", "FIXED", None, None, True),
        ("Otros criterios", "FREE", None, 20, True), ("Temporal", "FIXED", 7, None, False)]
    counts = await fetch_one(
        "SELECT (SELECT count(*) FROM event_registrations WHERE event_id = :id) AS regs,"
        " (SELECT count(*) FROM event_staff WHERE event_id = :id) AS staff,"
        " (SELECT count(*) FROM evaluations e JOIN event_registrations r ON r.id = e.registration_id"
        "   WHERE r.event_id = :id) AS evals", id=uuid.UUID(new["id"]))
    assert dict(counts) == {"regs": 0, "staff": 0, "evals": 0}
    # The coordinator of the source has no role in the copy.
    assert (await client.get(f"{API}/{new['id']}", headers=w["coordinator"]["headers"])).status_code == 403
    deleted = await client.delete(f"{API}/{new['id']}", headers=w["admin"]["headers"])
    assert deleted.status_code == 204


# ----------------------------------------------------------------------------
# Seed de la plantilla
# ----------------------------------------------------------------------------
def _load_seed():
    path = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "seed_event_template_universo.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("seed_event_template_universo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_seed_template_universo(w):
    seed = _load_seed()
    plan = seed.build()
    with seed.connect(TEST_DATABASE_URL) as conn:
        dry = seed.run(conn, plan, association=w["code"], operator="pytest")
        conn.rollback()
    assert dry["created"] is True
    assert await fetch_one("SELECT id FROM events WHERE id = :id", id=uuid.UUID(dry["event_id"])) is None
    with seed.connect(TEST_DATABASE_URL) as conn:
        report = seed.run(conn, plan, association=w["code"], operator="pytest")
        conn.commit()
    with seed.connect(TEST_DATABASE_URL) as conn:
        again = seed.run(conn, plan, association=w["code"])
        conn.commit()
    assert again["created"] is False and again["event_id"] == report["event_id"]
    event_id = uuid.UUID(report["event_id"])
    event = await fetch_one("SELECT * FROM events WHERE id = :id", id=event_id)
    assert event["name"] == "Camporee Familiar de Aventureros 2026 — Universo de Dios"
    assert (str(event["starts_on"]), str(event["ends_on"]), event["status"]) == ("2026-11-20", "2026-11-22", "DRAFT")
    assert event["city"] == "La Morita, N.L." and event["total_floor"] is None
    assert [b["min"] for b in event["honor_bands"]] == [950, 800, None]
    rows = await fetch_all("SELECT a.name, a.kind, a.max_points, a.status, a.config, p.name AS parent"
                           " FROM event_activities a LEFT JOIN event_activities p ON p.id = a.parent_id"
                           " WHERE a.event_id = :id ORDER BY coalesce(p.position, a.position), a.parent_id NULLS FIRST,"
                           " a.position", id=event_id)
    top = [r for r in rows if r["parent"] is None]
    assert [r["name"] for r in top] == ["Señal eterna", "Conexión con Dios", "Conociendo mi esencia",
                                        "Misión Galáctica", "Marcha a las estrellas", "Inspección sábado",
                                        "Inspección domingo", "Evento previo"]
    assert sum(r["max_points"] for r in top) == 1000
    assert {r["name"] for r in rows if r["status"] == "TO_DEFINE"} == {
        "Inspección sábado", "Inspección domingo", "Evento previo"}
    assert [r["name"] for r in rows if r["parent"] == "Conexión con Dios"] == [
        "Ronda general aventureros", "Ronda general padres y directiva", "Ronda final"]
    esencia = next(r for r in rows if r["name"] == "Conociendo mi esencia")["config"]["criteria"]
    assert [c.get("deduction_step") for c in esencia] == [2, 2, 2, 2, None]
    mision = next(r for r in rows if r["name"] == "Misión Galáctica")["config"]["stations"]
    assert mision[-1] == {"key": "centro_mando", "label": "Centro de Mando (pin)", "points": 0}
    types = await fetch_all("SELECT kind, label, amount_mode, points, max_points, max_per_event, max_per_club"
                            " FROM event_adjustment_types WHERE event_id = :id ORDER BY position", id=event_id)
    assert (types[0]["kind"], types[0]["points"], types[0]["max_per_event"]) == ("BONUS", 50, 1)
    assert (types[1]["kind"], types[1]["points"], types[1]["max_per_club"]) == ("PENALTY", 50, 1)
    assert len(types) == 10 and all(t["points"] is None for t in types[2:])
    assert [t["amount_mode"] for t in types[:2]] == ["FIXED", "FIXED"]
    modes = {t["label"]: t["amount_mode"] for t in types[2:]}
    assert modes.pop("Disciplina: Otros criterios que determinen los jueces") == "FREE"
    assert set(modes.values()) == {"FIXED"} and len(modes) == 7
    # The seeded template is a normal event: the association admin sees and edits it.
    async with SessionLocal() as db:
        from app.models import Event, User
        from app.services.event_access import can_manage_events_of

        admin = await db.get(User, uuid.UUID(w["admin"]["id"]))
        loaded = await db.get(Event, event_id)
        assert await can_manage_events_of(db, admin, loaded.organization_id)


# ----------------------------------------------------------------------------
# Lo que la asociación completa en el editor (spec §3.4) — todo por el API, sobre la plantilla
# ----------------------------------------------------------------------------
async def test_the_association_completes_the_seeded_template(client, w):
    seeded = await fetch_one("SELECT e.id FROM events e JOIN organizations o ON o.id = e.organization_id"
                             " WHERE o.code = :code AND e.slug LIKE 'camporee-familiar%'", code=w["code"])
    event_id, h = str(seeded["id"]), w["admin"]["headers"]
    activities = {a["name"]: a for a in (await client.get(f"{API}/{event_id}/activities", headers=h)).json()}
    types = {t["label"]: t for t in (await client.get(f"{API}/{event_id}/adjustment-types", headers=h)).json()}

    # 1. Inspection: split of the 50 points, then READY. Incomplete READY is refused.
    sabado = activities["Inspección sábado"]
    assert (await client.patch(f"{API}/{event_id}/activities/{sabado['id']}", json={"status": "READY"},
                               headers=h)).status_code == 422
    criteria = [{**c, "max": 10} for c in sabado["config"]["criteria"]]
    done = await client.patch(f"{API}/{event_id}/activities/{sabado['id']}",
                              json={"status": "READY", "config": {"criteria": criteria}}, headers=h)
    assert done.status_code == 200, done.text
    assert done.json()["config_complete"] is True and done.json()["config_max"] == 50
    wrong = [{**c, "max": 5} for c in activities["Inspección domingo"]["config"]["criteria"]]
    assert (await client.patch(f"{API}/{event_id}/activities/{activities['Inspección domingo']['id']}",
                               json={"status": "READY", "config": {"criteria": wrong}},
                               headers=h)).status_code == 422  # 15 ≠ max_points 50
    # 2. Previous event: from participation to a rubric of its own.
    previo = await client.patch(f"{API}/{event_id}/activities/{activities['Evento previo']['id']}", json={
        "kind": "rubric", "status": "READY", "config": {"criteria": [
            {"key": "evidencia", "label": "Evidencia", "max": 30},
            {"key": "participacion", "label": "Participación", "max": 20, "deduction_step": 2}]}}, headers=h)
    assert previo.status_code == 200 and previo.json()["kind"] == "rubric"
    # 3. Discipline amounts (FIXED) and the FREE one.
    queda = types["Disciplina: No respetar el toque de queda"]
    assert queda["to_define"] is True
    defined = await client.patch(f"{API}/{event_id}/adjustment-types/{queda['id']}", json={"points": 10}, headers=h)
    assert defined.status_code == 200 and defined.json()["to_define"] is False
    otros = types["Disciplina: Otros criterios que determinen los jueces"]
    bounded = await client.patch(f"{API}/{event_id}/adjustment-types/{otros['id']}", json={"max_points": 30},
                                 headers=h)
    assert bounded.status_code == 200 and bounded.json()["amount_mode"] == "FREE"
    # 4. Honour bands and the total floor.
    bands = [{"key": "oro", "label": "Oro", "min": 900}, {"key": "resto", "label": "Participación", "min": None}]
    patched = await client.patch(f"{API}/{event_id}", json={"honor_bands": bands, "total_floor": 0}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["total_floor"] == 0 and [b["key"] for b in patched.json()["honor_bands"]] == ["oro", "resto"]
    assert (await client.patch(f"{API}/{event_id}", json={"total_floor": 1.005}, headers=h)).status_code == 422
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE action = 'EVENT_UPDATE' AND entity_id = :id"
                            " ORDER BY created_at DESC LIMIT 1", id=event_id)
    assert audit["metadata_json"]["total_floor"] == {"from": None, "to": 0.0}
    my = (await client.get(f"{API}/{event_id}/my", headers=h)).json()
    assert my["coordination"]["to_define"]["activities"] == [{"id": activities["Inspección domingo"]["id"],
                                                              "name": "Inspección domingo"}]
    # 5. Finalists, a club, penalties below zero: the displayed total stops at the floor.
    registration = await client.post(f"{API}/{event_id}/registrations", json={"club_id": w["club"]["id"]}, headers=h)
    assert registration.status_code == 201
    reg = registration.json()["id"]
    final = activities["Ronda final"]["id"]
    flagged = await client.patch(f"{API}/{event_id}/registrations/{reg}", json={"finalist_flags": {final: True}},
                                 headers=h)
    assert flagged.status_code == 200 and flagged.json()["finalist_flags"] == {final: True}
    for step in ("OPEN", "IN_PROGRESS"):
        assert (await client.post(f"{API}/{event_id}/status", json={"status": step}, headers=h)).status_code == 200
    for label in ("Disciplina: No respetar el toque de queda", "Área de acampar sucia al retirarse"):
        applied = await client.post(f"{API}/{event_id}/adjustments", json={
            "registration_id": reg, "adjustment_type_id": types[label]["id"], "reason": "Prueba de piso"}, headers=h)
        assert applied.status_code == 201, applied.text
    breakdown_url = f"{API}/{event_id}/registrations/{reg}/breakdown"
    b = (await client.get(breakdown_url, headers=h)).json()
    assert (b["raw_total"], b["total"], b["floored"], b["total_floor"]) == (-60, 0, True, 0)
    assert b["honor"]["key"] == "resto"
    standings = (await client.get(f"{API}/{event_id}/standings", headers=h)).json()["rows"]
    assert (standings[0]["raw_total"], standings[0]["total"], standings[0]["floored"]) == (-60, 0, True)
    director = (await client.get(breakdown_url, headers=w["director"]["headers"])).json()
    assert director["total"] == 0 and director["floored"] is True
    assert "raw_total" not in director and "total_floor" not in director
    [mine] = (await client.get(f"{API}/{event_id}/my", headers=w["director"]["headers"])).json()["director"]["registrations"]
    assert mine["total"] == 0 and mine["floored"] is True and "raw_total" not in mine
    # Any number: a negative floor, then none at all.
    await client.patch(f"{API}/{event_id}", json={"total_floor": -20}, headers=h)
    b = (await client.get(breakdown_url, headers=h)).json()
    assert (b["raw_total"], b["total"], b["floored"]) == (-60, -20, True)
    await client.patch(f"{API}/{event_id}", json={"total_floor": None}, headers=h)
    b = (await client.get(breakdown_url, headers=h)).json()
    assert (b["raw_total"], b["total"], b["floored"], b["total_floor"]) == (-60, -60, False, None)
    # The completed inspection now takes captures (coordination, no judge).
    captured = await client.post(f"{API}/{event_id}/evaluations", json={
        "registration_id": reg, "activity_id": sabado["id"], "idempotency_key": f"{RUN}-insp-1",
        "inputs": {"criteria": {c["key"]: 10 for c in criteria}}}, headers=h)
    assert captured.status_code == 201 and captured.json()["points"] == 50
    # Duplicate copies the floor; a closed event no longer changes it.
    await client.patch(f"{API}/{event_id}", json={"total_floor": 0}, headers=h)
    copy = await client.post(f"{API}/{event_id}/duplicate", json={
        "name": f"{RUN} Copia con piso", "starts_on": "2027-11-19", "ends_on": "2027-11-21"}, headers=h)
    assert copy.status_code == 201 and copy.json()["total_floor"] == 0
    assert (await client.post(f"{API}/{event_id}/status", json={"status": "CLOSED"}, headers=h)).status_code == 200
    assert (await client.patch(f"{API}/{event_id}", json={"total_floor": 5}, headers=h)).status_code == 409
