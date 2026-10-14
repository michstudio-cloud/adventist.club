"""«En nodo local» (029; spec _dev-eventos/SPEC_RED_LOCAL.md §3).

What is proved here:
  * only coordination (event COORDINATOR or an admin in scope) locks / releases; judges,
    directors and strangers get 403; a reason is mandatory; both are audited;
  * while locked, EVERY write to the event answers 423 with the node's name and changes
    nothing, whatever the route (event, rules, staff, registrations, passes, check-in, scan,
    evaluations, adjustments, delete);
  * reads keep working, and so do the POSTs that only read (resolve-pass, /pass/resolve) and
    duplicate (it writes a NEW event);
  * a legacy pass is not regenerated in the cloud while locked (that GET would write);
  * `EventOut.offline_lock` tells every reader; releasing is idempotent and writes work again.
"""
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("offline")
API = "/api/v1/events"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


@pytest_asyncio.fixture(scope="module")
async def w(factory):
    assoc = await factory.org("assoc", "association")
    church = await factory.org("church", "church", assoc)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b")}
    for club in clubs.values():
        await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                    " WHERE id = :id", id=uuid.UUID(club["id"]))
    world = {
        "assoc": assoc, "clubs": clubs,
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR"),
        "judge": await factory.user("judge", "STUDENT"),
        "director_a": await factory.user("director-a", "CLUB_DIRECTOR", clubs["a"]["id"]),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    yield world
    ids = [uuid.UUID(assoc["id"])]
    await _exec("DELETE FROM audit_log WHERE entity_type LIKE 'EVENT%' AND entity_id IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM events WHERE organization_id = ANY(:ids)", ids=ids)


@pytest_asyncio.fixture(scope="module")
async def ev(client, w):
    """IN_PROGRESS event: a participation activity (on_scan), a rubric, an adjustment type,
    two clubs (b without check-in), a coordinator and a judge of everything."""
    h = w["admin"]["headers"]
    event = await _ok(await client.post(API, json={
        "organization_id": w["assoc"]["id"], "ministry": "adventurers", "name": f"{RUN} Nodo local",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h), 201)
    url = f"{API}/{event['id']}"
    senal = (await _ok(await client.post(f"{url}/activities", json={
        "name": "Señal", "kind": "participation", "max_points": 150,
        "config": {"max": 150, "auto_award": "on_scan"}}, headers=h), 201))["id"]
    marcha = (await _ok(await client.post(f"{url}/activities", json={
        "name": "Marcha", "kind": "participation", "max_points": 50, "config": {"max": 50}}, headers=h), 201))["id"]
    adj = (await _ok(await client.post(f"{url}/adjustment-types", json={
        "kind": "BONUS", "label": "Puntualidad", "amount_mode": "FIXED", "points": 10}, headers=h), 201))["id"]
    for body in ({"user_id": w["coordinator"]["id"], "role": "COORDINATOR"},
                 {"user_id": w["judge"]["id"], "role": "JUDGE"}):
        await _ok(await client.post(f"{url}/staff", json=body, headers=h), 201)
    regs, tokens = {}, {}
    for key, club in w["clubs"].items():
        reg = await _ok(await client.post(f"{url}/registrations", json={"club_id": club["id"]}, headers=h), 201)
        regs[key], tokens[key] = reg["id"], reg["pass_token"]
    for step in ("OPEN", "IN_PROGRESS"):
        await _ok(await client.post(f"{url}/status", json={"status": step}, headers=h))
    return {"id": event["id"], "url": url, "senal": senal, "marcha": marcha, "adj": adj,
            "regs": regs, "tokens": tokens}


async def _counts(event_id: str) -> dict:
    row = await fetch_one(
        "SELECT (SELECT count(*) FROM event_activities WHERE event_id = :id) AS activities,"
        " (SELECT count(*) FROM event_staff WHERE event_id = :id) AS staff,"
        " (SELECT count(*) FROM event_registrations WHERE event_id = :id) AS registrations,"
        " (SELECT count(*) FROM evaluations e JOIN event_registrations r ON r.id = e.registration_id"
        "   WHERE r.event_id = :id) AS evaluations,"
        " (SELECT count(*) FROM event_check_ins c JOIN event_registrations r ON r.id = c.registration_id"
        "   WHERE r.event_id = :id) AS check_ins,"
        " (SELECT count(*) FROM event_adjustments a JOIN event_registrations r ON r.id = a.registration_id"
        "   WHERE r.event_id = :id) AS adjustments,"
        " (SELECT rules_version || status || name FROM events WHERE id = :id) AS event,"
        " (SELECT string_agg(pass_token_hash, ',' ORDER BY id) FROM event_registrations WHERE event_id = :id) AS passes",
        id=uuid.UUID(event_id))
    return dict(row)


async def test_only_coordination_locks_and_a_reason_is_required(client, w, ev):
    url = f"{ev['url']}/offline-lock"
    body = {"reason": "Camporee sin internet", "node_label": "Mini PC La Morita"}
    for who in ("judge", "director_a", "stranger"):
        assert (await client.post(url, json=body, headers=w[who]["headers"])).status_code == 403, who
    assert (await client.post(url, json=body)).status_code == 401
    assert (await client.post(url, json={"reason": "   "}, headers=w["coordinator"]["headers"])).status_code == 422
    assert (await client.post(url, json={**body, "extra": 1}, headers=w["coordinator"]["headers"])).status_code == 422

    out = await _ok(await client.post(url, json=body, headers=w["coordinator"]["headers"]))
    lock = out["offline_lock"]
    assert lock["reason"] == "Camporee sin internet" and lock["node_label"] == "Mini PC La Morita"
    assert lock["locked_by_name"].endswith("coordinator") and lock["locked_at"]
    # Twice: 409, the first lock stays.
    again = await client.post(url, json={"reason": "otra vez"}, headers=w["admin"]["headers"])
    assert again.status_code == 409
    audit = await fetch_all("SELECT action, details, metadata_json FROM audit_log WHERE entity_type = 'EVENT'"
                            " AND entity_id = :id AND action LIKE 'EVENT_OFFLINE%'", id=ev["id"])
    assert [(a["action"], a["details"]) for a in audit] == [("EVENT_OFFLINE_LOCK", "Camporee sin internet")]
    assert audit[0]["metadata_json"]["node_label"] == "Mini PC La Morita"
    # Every reader sees it (a judge and the director too).
    for who in ("judge", "director_a", "admin"):
        got = await _ok(await client.get(ev["url"], headers=w[who]["headers"]))
        assert got["offline_lock"]["node_label"] == "Mini PC La Morita", who


async def test_every_write_answers_423_and_changes_nothing(client, w, ev):
    before = await _counts(ev["id"])
    url, regs, tokens = ev["url"], ev["regs"], ev["tokens"]
    coord, judge = w["coordinator"]["headers"], w["judge"]["headers"]
    attempts = [
        ("PATCH", url, {"name": "Otro nombre"}, coord),
        ("POST", f"{url}/status", {"status": "CLOSED"}, coord),
        ("POST", f"{url}/activities", {"name": "Nueva", "kind": "participation", "config": {"max": 5}}, coord),
        ("PATCH", f"{url}/activities/{ev['marcha']}", {"name": "Marcha 2"}, coord),
        ("DELETE", f"{url}/activities/{ev['marcha']}", None, coord),
        ("POST", f"{url}/adjustment-types", {"kind": "PENALTY", "label": "Ruido", "amount_mode": "FIXED",
                                              "points": 5}, coord),
        ("POST", f"{url}/staff", {"user_id": w["stranger"]["id"], "role": "JUDGE"}, coord),
        ("POST", f"{url}/registrations", {"club_id": w["clubs"]["a"]["id"]}, coord),
        ("PATCH", f"{url}/registrations/{regs['a']}", {"tiebreak_rank": 1, "reason": "x"}, coord),
        ("POST", f"{url}/registrations/{regs['b']}/withdraw", {"reason": "x"}, coord),
        ("POST", f"{url}/registrations/{regs['a']}/pass", None, w["director_a"]["headers"]),
        ("POST", f"{url}/check-in", {"pass_token": tokens["a"]}, judge),
        ("DELETE", f"{url}/registrations/{regs['a']}/check-in?reason=x", None, coord),
        ("POST", f"{url}/activities/{ev['senal']}/scan", {"pass_token": tokens["a"]}, judge),
        ("POST", f"{url}/evaluations", {"registration_id": regs["a"], "activity_id": ev["marcha"],
                                        "inputs": {"points": 40}, "idempotency_key": f"{RUN}-offline-1"}, judge),
        ("POST", f"{url}/adjustments", {"registration_id": regs["a"], "adjustment_type_id": ev["adj"],
                                        "reason": "Llegó temprano"}, coord),
        ("DELETE", url, None, w["admin"]["headers"]),
    ]
    for method, target, body, headers in attempts:
        response = await client.request(method, target, json=body, headers=headers)
        assert response.status_code == 423, (method, target, response.status_code, response.text)
        detail = response.json()["detail"]
        assert "nodo local «Mini PC La Morita»" in detail and "sólo se puede consultar" in detail
    assert await _counts(ev["id"]) == before


async def test_reads_and_read_only_posts_still_work(client, w, ev):
    url, coord, judge = ev["url"], w["coordinator"]["headers"], w["judge"]["headers"]
    for target in ("", "/standings", "/registrations", "/activities", "/history", "/my", "/adjustments",
                   "/staff", "/adjustment-types", f"/activities/{ev['senal']}/progress"):
        assert (await client.get(f"{url}{target}", headers=coord)).status_code == 200, target
    # The pass in force is shown (no rotation = no write).
    shown = await _ok(await client.get(f"{url}/registrations/{ev['regs']['a']}/pass",
                                       headers=w["director_a"]["headers"]))
    assert shown["pass_token"] == ev["tokens"]["a"]
    resolved = await _ok(await client.post(f"{url}/resolve-pass", json={"token": ev["tokens"]["a"]}, headers=judge))
    assert resolved["id"] == ev["regs"]["a"]
    page = await _ok(await client.post(f"{API}/pass/resolve", json={"token": ev["tokens"]["a"]}, headers=judge))
    assert page["registration"]["id"] == ev["regs"]["a"]
    # Duplicate writes a NEW event (unlocked) from the locked one.
    copy = await _ok(await client.post(f"{url}/duplicate", json={"name": f"{RUN} Copia nodo", "starts_on": "2027-11-20", "ends_on": "2027-11-22"},
                                       headers=w["admin"]["headers"]), 201)
    assert copy["offline_lock"] is None and copy["id"] != ev["id"]


async def test_a_legacy_pass_is_not_regenerated_in_the_cloud_while_locked(client, w, ev):
    rid = uuid.UUID(ev["regs"]["b"])
    await _exec("UPDATE event_registrations SET pass_token_enc = NULL WHERE id = :id", id=rid)
    before = await fetch_one("SELECT pass_token_hash FROM event_registrations WHERE id = :id", id=rid)
    response = await client.get(f"{ev['url']}/registrations/{ev['regs']['b']}/pass",
                                headers=w["coordinator"]["headers"])
    assert response.status_code == 423, response.text
    after = await fetch_one("SELECT pass_token_hash FROM event_registrations WHERE id = :id", id=rid)
    assert after["pass_token_hash"] == before["pass_token_hash"]


async def test_release_needs_coordination_and_a_reason_then_writes_work(client, w, ev):
    url = f"{ev['url']}/offline-lock"
    assert (await client.request("DELETE", url, json={"reason": "fin"}, headers=w["judge"]["headers"])).status_code == 403
    assert (await client.request("DELETE", url, headers=w["coordinator"]["headers"])).status_code == 422
    out = await _ok(await client.request("DELETE", f"{url}?reason=Datos importados", headers=w["admin"]["headers"]))
    assert out["offline_lock"] is None
    audit = await fetch_all("SELECT action, details, metadata_json FROM audit_log WHERE entity_type = 'EVENT'"
                            " AND entity_id = :id AND action = 'EVENT_OFFLINE_UNLOCK'", id=ev["id"])
    assert len(audit) == 1 and audit[0]["details"] == "Datos importados"
    assert audit[0]["metadata_json"]["node_label"] == "Mini PC La Morita"
    assert audit[0]["metadata_json"]["lock_reason"] == "Camporee sin internet"
    # Idempotent: nothing to release, nothing audited.
    await _ok(await client.request("DELETE", url, json={"reason": "otra"}, headers=w["coordinator"]["headers"]))
    assert len(await fetch_all("SELECT 1 FROM audit_log WHERE entity_id = :id AND action = 'EVENT_OFFLINE_UNLOCK'",
                               id=ev["id"])) == 1
    # Writes work again.
    created = await client.post(f"{ev['url']}/check-in", json={"pass_token": ev["tokens"]["a"]},
                                headers=w["judge"]["headers"])
    assert created.status_code == 201, created.text
    assert (await _ok(await client.patch(ev["url"], json={"venue": "La Morita"},
                                         headers=w["coordinator"]["headers"])))["venue"] == "La Morita"
