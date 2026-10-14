"""Check-in con QR, participación automática y juez rápido (028; spec SPEC_CHECKIN_JUEZ «Backend»).

What is proved here:
  * the pass is STABLE: GET …/pass shows the same token on every call and device (encrypted at
    rest, hash for lookups); only POST …/pass rotates it; a pre-028 pass is regenerated once;
    the QR encodes `<EVENT_PASS_URL_BASE>/<token>` and every lookup accepts that URL;
  * check-in: coordination and any judge, OPEN or IN_PROGRESS, idempotent («ya registrado» with
    when and who), by pass or by registration; undo by coordination only, with a reason, voiding
    what that check-in granted;
  * `config.auto_award`: «on_checkin» grants the maximum at check-in (or when the event starts /
    the mode is set), «on_scan» through POST …/activities/{aid}/scan; never twice, never over an
    existing evaluation; captured_as AUTO_CHECKIN / AUTO_SCAN;
  * the judge's progress (in /my and GET …/activities/{aid}/progress);
  * history: CHECK_IN / CHECK_IN_UNDONE and automatic captures; directors never see names;
  * `/events/pass/resolve` (the `/p/<token>` page) identifies the club only for that event's staff;
  * bugs B9 (loose CLUB_DIRECTOR after accepting) and B10 (events listed by any scoped role);
  * an invitation's `return_to` (own hosts only).
"""
import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import event_pass
from tests.conftest import RUN, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("checkin")
API = "/api/v1/events"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _adventurers(club: dict) -> None:
    await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                " WHERE id = :id", id=uuid.UUID(club["id"]))
    await _exec("INSERT INTO organization_ministries (organization_id, ministry_id)"
                " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
                id=uuid.UUID(club["id"]))


async def _ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


@pytest_asyncio.fixture(scope="module")
async def w(factory):
    assoc = await factory.org("assoc", "association")
    church = await factory.org("church", "church", assoc)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b", "c")}
    other = await factory.org("assoc-2", "association")
    for club in clubs.values():
        await _adventurers(club)
    world = {
        "assoc": assoc, "church": church, "other": other, "clubs": clubs, "s": {},
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR"),
        "judge": await factory.user("judge", "STUDENT"),           # judge of «Señal» (on_scan)
        "judge_marcha": await factory.user("judge-marcha", "STUDENT"),
        "director_a": await factory.user("director-a", "CLUB_DIRECTOR", clubs["a"]["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", clubs["b"]["id"]),
        "stranger": await factory.user("stranger", "STUDENT"),
        "second_admin": await factory.user("second-admin", "STUDENT"),
    }
    yield world
    ids = [uuid.UUID(assoc["id"]), uuid.UUID(other["id"])]
    await _exec("DELETE FROM audit_log WHERE entity_type = 'EVENT' AND entity_id IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM events WHERE organization_id = ANY(:ids)", ids=ids)


@pytest_asyncio.fixture(scope="module")
async def ev(client, w):
    """An event with: «Llegada» (on_checkin), «Señal» (on_scan), «Marcha» (rubric), «Final»
    (on_checkin, finalists only); 3 clubs; a coordinator and two judges. Left in DRAFT."""
    h = w["admin"]["headers"]
    event = await _ok(await client.post(API, json={
        "organization_id": w["assoc"]["id"], "ministry": "adventurers", "name": f"{RUN} Check-in",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h), 201)
    url = f"{API}/{event['id']}"

    async def activity(body):
        return (await _ok(await client.post(f"{url}/activities", json=body, headers=h), 201))["id"]

    ids = {
        "llegada": await activity({"name": "Llegada", "kind": "participation", "max_points": 100,
                                   "config": {"max": 100, "auto_award": "on_checkin"}}),
        "senal": await activity({"name": "Señal", "kind": "participation", "max_points": 150,
                                 "config": {"max": 150, "auto_award": "on_scan"}}),
        "marcha": await activity({"name": "Marcha", "kind": "rubric", "max_points": 50, "config": {
            "criteria": [{"key": "voz", "label": "Voz", "max": 50}]}}),
        "final": await activity({"name": "Final", "kind": "participation", "max_points": 20,
                                 "config": {"max": 20, "finalists_only": True, "auto_award": "on_checkin"}}),
    }
    for body in ({"user_id": w["coordinator"]["id"], "role": "COORDINATOR"},
                 {"user_id": w["judge"]["id"], "role": "JUDGE", "activity_id": ids["senal"]},
                 {"user_id": w["judge_marcha"]["id"], "role": "JUDGE", "activity_id": ids["marcha"]}):
        await _ok(await client.post(f"{url}/staff", json=body, headers=h), 201)
    regs, tokens = {}, {}
    for key, club in w["clubs"].items():
        reg = await _ok(await client.post(f"{url}/registrations", json={"club_id": club["id"]}, headers=h), 201)
        regs[key], tokens[key] = reg["id"], reg["pass_token"]
        assert reg["pass_url"] == event_pass.pass_url(reg["pass_token"])
        assert reg["checked_in_at"] is None
    return {"id": event["id"], "url": url, **ids, "regs": regs, "tokens": tokens}


async def _status(client, w, ev, step):
    await _ok(await client.post(f"{ev['url']}/status", json={"status": step}, headers=w["admin"]["headers"]))


def _pass_url(ev, reg):
    return f"{ev['url']}/registrations/{ev['regs'][reg]}/pass"


# ----------------------------------------------------------------------------
# Pase estable (B1)
# ----------------------------------------------------------------------------
async def test_pass_is_stable_across_reads_and_rotates_only_on_post(client, w, ev):
    first = ev["tokens"]["a"]
    seen = set()
    for who in ("director_a", "director_a", "coordinator", "admin", "director_a"):
        response = await client.get(_pass_url(ev, "a"), headers=w[who]["headers"])
        body = await _ok(response)
        assert "X-Pass-Regenerated" not in response.headers
        assert response.headers["Cache-Control"] == "no-store"
        seen.add(body["pass_token"])
        assert body["pass_url"] == f"https://events.adventist.club/p/{first}"
    assert seen == {first}
    stored = await fetch_one("SELECT pass_token_hash, pass_token_enc FROM event_registrations WHERE id = :id",
                             id=uuid.UUID(ev["regs"]["a"]))
    assert stored["pass_token_enc"].startswith("v1.") and first not in stored["pass_token_enc"]
    assert len(stored["pass_token_hash"]) == 64
    # Only that club's director or coordination; anonymous 401.
    for who in ("director_b", "judge", "stranger"):
        assert (await client.get(_pass_url(ev, "a"), headers=w[who]["headers"])).status_code == 403, who
    assert (await client.get(_pass_url(ev, "a"))).status_code == 401
    # The QR's URL resolves like the bare token.
    resolved = await client.post(f"{ev['url']}/resolve-pass", json={"token": f"https://events.adventist.club/p/{first}"},
                                 headers=w["judge"]["headers"])
    assert (await _ok(resolved))["id"] == ev["regs"]["a"]
    # POST = regenerate explicitly: new token, the old one dies, GET shows the new one.
    rotated = await _ok(await client.post(_pass_url(ev, "a"), headers=w["director_a"]["headers"]))
    assert rotated["pass_token"] != first
    assert (await _ok(await client.get(_pass_url(ev, "a"), headers=w["coordinator"]["headers"])))["pass_token"] \
        == rotated["pass_token"]
    assert (await client.post(f"{ev['url']}/resolve-pass", json={"token": first},
                              headers=w["judge"]["headers"])).status_code == 404
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE action = 'EVENT_PASS_REGENERATE'"
                            " AND entity_id = :id ORDER BY created_at DESC LIMIT 1", id=ev["regs"]["a"])
    assert audit["metadata_json"]["reason"] == "requested"
    ev["tokens"]["a"] = rotated["pass_token"]


async def test_legacy_pass_is_regenerated_once(client, w, ev):
    old = ev["tokens"]["b"]
    for legacy in (None, "v1.not-a-valid-ciphertext"):
        await _exec("UPDATE event_registrations SET pass_token_enc = :v WHERE id = :id",
                    v=legacy, id=uuid.UUID(ev["regs"]["b"]))
        response = await client.get(_pass_url(ev, "b"), headers=w["director_b"]["headers"])
        fresh = (await _ok(response))["pass_token"]
        assert response.headers.get("X-Pass-Regenerated") == "1" and fresh != old
        assert (await client.post(f"{ev['url']}/resolve-pass", json={"token": old},
                                  headers=w["judge"]["headers"])).status_code == 404
        again = await client.get(_pass_url(ev, "b"), headers=w["coordinator"]["headers"])
        assert (await _ok(again))["pass_token"] == fresh and "X-Pass-Regenerated" not in again.headers
        old = fresh
    # Two screens asking at once for a legacy pass get the same one.
    await _exec("UPDATE event_registrations SET pass_token_enc = NULL WHERE id = :id", id=uuid.UUID(ev["regs"]["b"]))
    twins = await asyncio.gather(*[client.get(_pass_url(ev, "b"), headers=w["director_b"]["headers"])
                                   for _ in range(3)])
    assert len({(await _ok(r))["pass_token"] for r in twins}) == 1
    ev["tokens"]["b"] = twins[0].json()["pass_token"]
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE action = 'EVENT_PASS_REGENERATE'"
                            " AND entity_id = :id ORDER BY created_at DESC LIMIT 1", id=ev["regs"]["b"])
    assert audit["metadata_json"]["reason"] == "legacy"


async def test_pass_url_base_is_configurable(client, w, ev, monkeypatch):
    monkeypatch.setattr(settings, "EVENT_PASS_URL_BASE", "https://eventos.example.org/p/")
    body = await _ok(await client.get(_pass_url(ev, "c"), headers=w["coordinator"]["headers"]))
    assert body["pass_url"] == f"https://eventos.example.org/p/{ev['tokens']['c']}"
    assert event_pass.token_from(f"https://x.example/p/{ev['tokens']['c']}?utm=1") == ev["tokens"]["c"]
    assert event_pass.token_from(f"/p/{ev['tokens']['c']}") == ev["tokens"]["c"]
    assert event_pass.token_from(f"  {ev['tokens']['c']} ") == ev["tokens"]["c"]
    assert event_pass.decrypt(event_pass.encrypt("evp_abc")) == "evp_abc"
    assert event_pass.decrypt("v2.whatever") is None and event_pass.decrypt(None) is None


# ----------------------------------------------------------------------------
# Check-in
# ----------------------------------------------------------------------------
async def test_check_in_needs_an_open_event_and_event_staff(client, w, ev):
    url = f"{ev['url']}/check-in"
    body = {"pass_token": ev["tokens"]["a"]}
    blocked = await client.post(url, json=body, headers=w["coordinator"]["headers"])
    assert blocked.status_code == 409 and "abierto" in blocked.json()["detail"]  # DRAFT
    await _status(client, w, ev, "OPEN")
    assert (await client.post(url, json=body)).status_code == 401
    for who in ("director_a", "stranger"):
        assert (await client.post(url, json=body, headers=w[who]["headers"])).status_code == 403, who
    for bad in ({}, {"pass_token": ev["tokens"]["a"], "registration_id": ev["regs"]["a"]}):
        assert (await client.post(url, json=bad, headers=w["coordinator"]["headers"])).status_code == 422
    assert (await client.post(url, json={"pass_token": "evp_" + "x" * 30},
                              headers=w["coordinator"]["headers"])).status_code == 404


async def test_check_in_is_idempotent_and_says_when_and_who(client, w, ev):
    url = f"{ev['url']}/check-in"
    # Any judge of the event checks in (not only the judge of a given activity), by the QR URL.
    first = await _ok(await client.post(url, json={"pass_token": event_pass.pass_url(ev["tokens"]["a"])},
                                        headers=w["judge_marcha"]["headers"]), 201)
    assert first["already_checked_in"] is False and first["awarded"] == []  # OPEN: nothing granted yet
    assert first["registration"]["id"] == ev["regs"]["a"] and first["registration"]["club"]["name"].endswith("club-a")
    assert first["check_in"]["checked_in_as"] == "JUDGE" and first["check_in"]["method"] == "PASS"
    assert first["check_in"]["checked_in_by_name"].endswith(" judge-marcha")
    assert first["registration"]["checked_in_at"] is not None
    again = await _ok(await client.post(url, json={"pass_token": ev["tokens"]["a"]},
                                        headers=w["coordinator"]["headers"]))
    assert again["already_checked_in"] is True and again["check_in"]["id"] == first["check_in"]["id"]
    assert again["check_in"]["checked_in_at"] == first["check_in"]["checked_in_at"]
    assert again["check_in"]["checked_in_by_name"].endswith(" judge-marcha")
    # Manual pick by coordination; concurrent twins record one arrival.
    manual = await asyncio.gather(*[client.post(url, json={"registration_id": ev["regs"]["b"]},
                                                headers=w["coordinator"]["headers"]) for _ in range(3)])
    assert sorted(r.status_code for r in manual) == [200, 200, 201], [r.text for r in manual]
    created = next(r.json() for r in manual if r.status_code == 201)
    assert created["check_in"]["checked_in_as"] == "COORDINATION" and created["check_in"]["method"] == "MANUAL"
    rows = await fetch_all("SELECT id FROM event_check_ins WHERE registration_id = ANY(:ids)",
                           ids=[uuid.UUID(ev["regs"]["a"]), uuid.UUID(ev["regs"]["b"])])
    assert len(rows) == 2
    count = await fetch_one("SELECT count(*) AS n FROM evaluations e JOIN event_registrations r"
                            " ON r.id = e.registration_id WHERE r.event_id = :id", id=uuid.UUID(ev["id"]))
    assert count["n"] == 0
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE action = 'EVENT_CHECK_IN'"
                            " AND entity_id = :id", id=first["check_in"]["id"])
    assert audit["metadata_json"]["as"] == "JUDGE" and audit["metadata_json"]["method"] == "PASS"
    # Registrations: staff see who; a director sees when and the role, never the name.
    staff = {r["id"]: r for r in await _ok(await client.get(f"{ev['url']}/registrations",
                                                            headers=w["judge"]["headers"]))}
    assert staff[ev["regs"]["a"]]["checked_in_by_name"].endswith(" judge-marcha")
    assert staff[ev["regs"]["c"]]["checked_in_at"] is None
    [own] = await _ok(await client.get(f"{ev['url']}/registrations", headers=w["director_a"]["headers"]))
    assert own["checked_in_at"] is not None and own["checked_in_as"] == "JUDGE" and "checked_in_by_name" not in own
    ev["check_in_a"] = first["check_in"]["id"]


async def test_on_scan_needs_the_event_in_progress(client, w, ev):
    scan = f"{ev['url']}/activities/{ev['senal']}/scan"
    response = await client.post(scan, json={"pass_token": ev["tokens"]["a"]}, headers=w["judge"]["headers"])
    assert response.status_code == 409 and "en curso" in response.json()["detail"]


async def test_on_checkin_is_granted_when_the_event_starts(client, w, ev):
    await _status(client, w, ev, "IN_PROGRESS")
    rows = await fetch_all(
        "SELECT r.id::text AS reg, a.name, e.points, e.captured_as, e.judge_id::text AS judge, e.check_in_id"
        " FROM evaluations e JOIN event_registrations r ON r.id = e.registration_id"
        " JOIN event_activities a ON a.id = e.activity_id WHERE r.event_id = :id ORDER BY a.name, r.id",
        id=uuid.UUID(ev["id"]))
    granted = {(row["reg"], row["name"]) for row in rows}
    assert granted == {(ev["regs"]["a"], "Llegada"), (ev["regs"]["b"], "Llegada")}  # not «Final»: no finalists
    assert all(row["points"] == 100 and row["captured_as"] == "AUTO_CHECKIN" and row["check_in_id"] for row in rows)
    by_reg = {row["reg"]: row for row in rows}
    assert by_reg[ev["regs"]["a"]]["judge"] == w["judge_marcha"]["id"]  # who checked the club in
    assert by_reg[ev["regs"]["b"]]["judge"] == w["coordinator"]["id"]


async def test_check_in_during_the_event_grants_and_never_twice(client, w, ev):
    url = f"{ev['url']}/check-in"
    first = await _ok(await client.post(url, json={"pass_token": ev["tokens"]["c"]},
                                        headers=w["judge"]["headers"]), 201)
    [award] = first["awarded"]
    assert award["activity_id"] == ev["llegada"] and award["points"] == 100 and award["created"] is True
    assert award["captured_as"] == "AUTO_CHECKIN"
    again = await _ok(await client.post(url, json={"pass_token": ev["tokens"]["c"]}, headers=w["judge"]["headers"]))
    assert again["already_checked_in"] is True
    assert [(a["evaluation_id"], a["created"]) for a in again["awarded"]] == [(award["evaluation_id"], False)]
    # Became a finalist after arriving: the next scan grants what is now due, only that.
    await _ok(await client.patch(f"{ev['url']}/registrations/{ev['regs']['c']}",
                                 json={"finalist_flags": {ev["final"]: True}}, headers=w["coordinator"]["headers"]))
    third = await _ok(await client.post(url, json={"registration_id": ev["regs"]["c"]},
                                        headers=w["coordinator"]["headers"]))
    assert {(a["activity_id"], a["created"]) for a in third["awarded"]} == {
        (ev["llegada"], False), (ev["final"], True)}
    rows = await fetch_all("SELECT activity_id FROM evaluations WHERE registration_id = :r",
                           r=uuid.UUID(ev["regs"]["c"]))
    assert len(rows) == 2
    listed = await _ok(await client.get(f"{ev['url']}/evaluations?registration_id={ev['regs']['c']}",
                                        headers=w["coordinator"]["headers"]))
    assert {e["captured_as"] for e in listed} == {"AUTO_CHECKIN"} and all(e["check_in_id"] for e in listed)


async def test_on_scan_grants_the_maximum_once(client, w, ev):
    scan = f"{ev['url']}/activities/{ev['senal']}/scan"
    first = await client.post(scan, json={"pass_token": event_pass.pass_url(ev["tokens"]["a"])},
                              headers=w["judge"]["headers"])
    body = await _ok(first, 201)
    assert body["created"] is True and body["evaluation"]["points"] == 150
    assert body["evaluation"]["captured_as"] == "AUTO_SCAN" and body["evaluation"]["judge_id"] == w["judge"]["id"]
    assert body["registration"]["id"] == ev["regs"]["a"] and body["activity"]["name"] == "Señal"
    again = await _ok(await client.post(scan, json={"pass_token": ev["tokens"]["a"]}, headers=w["judge"]["headers"]))
    assert again["created"] is False and again["evaluation"]["id"] == body["evaluation"]["id"]
    # Not this activity's judge, a director, a stranger: 403. Coordination may (manual pick).
    for who in ("judge_marcha", "director_a", "stranger"):
        assert (await client.post(scan, json={"pass_token": ev["tokens"]["b"]},
                                  headers=w[who]["headers"])).status_code == 403, who
    by_coord = await _ok(await client.post(scan, json={"registration_id": ev["regs"]["b"]},
                                           headers=w["coordinator"]["headers"]), 201)
    assert by_coord["evaluation"]["captured_as"] == "AUTO_SCAN"
    # An activity that is not «on_scan» refuses the scan.
    marcha = await client.post(f"{ev['url']}/activities/{ev['marcha']}/scan", json={"pass_token": ev["tokens"]["a"]},
                               headers=w["judge_marcha"]["headers"])
    assert marcha.status_code == 409 and "escanear" in marcha.json()["detail"]
    # Three scans at once: one evaluation.
    racing = await asyncio.gather(*[client.post(scan, json={"pass_token": ev["tokens"]["c"]},
                                                headers=w["judge"]["headers"]) for _ in range(3)])
    assert sorted(r.status_code for r in racing) == [200, 200, 201], [r.text for r in racing]
    assert len({r.json()["evaluation"]["id"] for r in racing}) == 1
    # Corrected through the normal flow.
    fixed = await client.patch(f"{ev['url']}/evaluations/{body['evaluation']['id']}", json={
        "inputs": {"points": 120}, "expected_revision": 1, "reason": "Llegó sin bandera"},
        headers=w["judge"]["headers"])
    assert (await _ok(fixed))["points"] == 120 and fixed.json()["captured_as"] == "AUTO_SCAN"
    after = await _ok(await client.post(scan, json={"pass_token": ev["tokens"]["a"]}, headers=w["judge"]["headers"]))
    assert after["created"] is False and after["evaluation"]["points"] == 120  # «devuelve la existente»
    ev["scan_a"] = body["evaluation"]["id"]


async def test_undo_check_in_voids_what_it_granted(client, w, ev):
    url = f"{ev['url']}/registrations/{ev['regs']['a']}/check-in"
    reason = {"reason": "Se escaneó el club equivocado"}
    for who in ("judge", "judge_marcha", "director_a"):
        assert (await client.request("DELETE", url, json=reason, headers=w[who]["headers"])).status_code == 403
    assert (await client.request("DELETE", url, headers=w["coordinator"]["headers"])).status_code == 422
    assert (await client.request("DELETE", url, json={"reason": "  "},
                                 headers=w["coordinator"]["headers"])).status_code == 422
    llegada = await fetch_one("SELECT id FROM evaluations WHERE registration_id = :r AND activity_id = :a",
                              r=uuid.UUID(ev["regs"]["a"]), a=uuid.UUID(ev["llegada"]))
    undone = await _ok(await client.request("DELETE", url, json=reason, headers=w["coordinator"]["headers"]))
    assert undone["undone"] is True and undone["registration"]["checked_in_at"] is None
    assert [(v["evaluation_id"], v["activity_id"]) for v in undone["voided"]] == [(str(llegada["id"]), ev["llegada"])]
    row = await fetch_one("SELECT status, revision FROM evaluations WHERE id = :id", id=llegada["id"])
    assert dict(row) == {"status": "VOID", "revision": 2}
    revision = await fetch_one("SELECT action, reason, points FROM evaluation_revisions WHERE evaluation_id = :id",
                               id=llegada["id"])
    assert dict(revision) == {"action": "VOID", "reason": reason["reason"], "points": 100}
    # What the judge scanned is not the check-in's: it stays.
    scanned = await fetch_one("SELECT status FROM evaluations WHERE id = :id", id=uuid.UUID(ev["scan_a"]))
    assert scanned["status"] == "CONFIRMED"
    stored = await fetch_one("SELECT undone_at, undone_by_id::text AS who, undo_reason FROM event_check_ins"
                             " WHERE id = :id", id=uuid.UUID(ev["check_in_a"]))
    assert stored["undone_at"] and stored["who"] == w["coordinator"]["id"] and stored["undo_reason"] == reason["reason"]
    # Idempotent (and the reason may come as a query parameter).
    twice = await _ok(await client.delete(f"{url}?reason=otra%20vez", headers=w["coordinator"]["headers"]))
    assert twice["undone"] is False and twice["voided"] == []
    # Checked in again: a new arrival, a new participation.
    back = await _ok(await client.post(f"{ev['url']}/check-in", json={"pass_token": ev["tokens"]["a"]},
                                       headers=w["coordinator"]["headers"]), 201)
    [award] = back["awarded"]
    assert award["created"] is True and award["evaluation_id"] != str(llegada["id"])
    standings = await _ok(await client.get(f"{ev['url']}/standings", headers=w["coordinator"]["headers"]))
    total_a = next(r for r in standings["rows"] if r["registration_id"] == ev["regs"]["a"])
    assert total_a["total"] == 100 + 120 and total_a["checked_in_at"] is not None


# ----------------------------------------------------------------------------
# Reglas: auto_award
# ----------------------------------------------------------------------------
async def test_auto_award_is_a_rule_of_participation(client, w, ev):
    h = w["admin"]["headers"]
    url = f"{ev['url']}/activities"
    for body in ({"name": "X", "kind": "participation", "config": {"max": 5, "auto_award": "always"}},
                 {"name": "X", "kind": "rubric", "config": {
                     "criteria": [{"key": "a", "label": "A", "max": 1}], "auto_award": "on_scan"}}):
        assert (await client.post(url, json=body, headers=h)).status_code == 422
    listed = {a["name"]: a for a in await _ok(await client.get(url, headers=w["judge"]["headers"]))}
    assert listed["Señal"]["auto_award"] == "on_scan" and listed["Marcha"]["auto_award"] is None
    assert listed["Llegada"]["config"] == {"max": 100, "auto_award": "on_checkin"}
    # Switching the mode over live evaluations is allowed (it scores nothing) and bumps the rules.
    before = (await _ok(await client.get(ev["url"], headers=h)))["rules_version"]
    switched = await _ok(await client.patch(f"{url}/{ev['senal']}", json={"config": {"max": 150}}, headers=h))
    assert switched["auto_award"] is None
    assert (await _ok(await client.get(ev["url"], headers=h)))["rules_version"] == before + 1
    assert (await client.post(f"{url}/{ev['senal']}/scan", json={"pass_token": ev["tokens"]["a"]},
                              headers=w["judge"]["headers"])).status_code == 409
    # The points still may not change under live evaluations.
    assert (await client.patch(f"{url}/{ev['senal']}", json={"config": {"max": 140, "auto_award": "on_scan"},
                                                             "max_points": 140}, headers=h)).status_code == 409
    await _ok(await client.patch(f"{url}/{ev['senal']}", json={"config": {"max": 150, "auto_award": "on_scan"}},
                                 headers=h))


async def test_setting_on_checkin_during_the_event_grants_the_clubs_already_in(client, w, ev):
    h = w["admin"]["headers"]
    created = await _ok(await client.post(f"{ev['url']}/activities", json={
        "name": "Bienvenida", "kind": "participation", "max_points": 10, "config": {"max": 10}}, headers=h), 201)
    assert await fetch_one("SELECT id FROM evaluations WHERE activity_id = :a", a=uuid.UUID(created["id"])) is None
    await _ok(await client.patch(f"{ev['url']}/activities/{created['id']}",
                                 json={"config": {"max": 10, "auto_award": "on_checkin"}}, headers=h))
    rows = await fetch_all("SELECT registration_id::text AS reg, captured_as FROM evaluations WHERE activity_id = :a",
                           a=uuid.UUID(created["id"]))
    assert {r["reg"] for r in rows} == set(ev["regs"].values()) and {r["captured_as"] for r in rows} == {"AUTO_CHECKIN"}
    ev["bienvenida"] = created["id"]


# ----------------------------------------------------------------------------
# Progreso del juez
# ----------------------------------------------------------------------------
async def test_judge_progress(client, w, ev):
    url = f"{ev['url']}/activities/{ev['senal']}/progress"
    for who in ("judge_marcha", "director_a", "stranger"):
        assert (await client.get(url, headers=w[who]["headers"])).status_code == 403, who
    progress = await _ok(await client.get(url, headers=w["judge"]["headers"]))
    assert progress["counts"] == {"registered": 3, "checked_in": 3, "eligible": 3, "scored": 3}
    assert progress["auto_award"] == "on_scan" and progress["max_points"] == 150
    clubs = {c["registration_id"]: c for c in progress["clubs"]}
    assert clubs[ev["regs"]["a"]]["evaluated"] is True and clubs[ev["regs"]["a"]]["evaluation"]["points"] == 120
    assert clubs[ev["regs"]["a"]]["checked_in"] is True
    marcha = await _ok(await client.get(f"{ev['url']}/activities/{ev['marcha']}/progress",
                                        headers=w["coordinator"]["headers"]))
    assert marcha["counts"]["scored"] == 0 and not any(c["evaluated"] for c in marcha["clubs"])
    final = await _ok(await client.get(f"{ev['url']}/activities/{ev['final']}/progress",
                                       headers=w["coordinator"]["headers"]))
    assert final["counts"] == {"registered": 3, "checked_in": 3, "eligible": 1, "scored": 1}
    my = await _ok(await client.get(f"{ev['url']}/my", headers=w["judge"]["headers"]))
    [senal] = my["judge"]["activities"]
    assert senal["id"] == ev["senal"] and senal["progress"] == progress["counts"]
    mine = await _ok(await client.get(f"{ev['url']}/my", headers=w["judge_marcha"]["headers"]))
    assert mine["judge"]["activities"][0]["progress"]["scored"] == 0
    # The director's own view carries the arrival.
    director = await _ok(await client.get(f"{ev['url']}/my", headers=w["director_a"]["headers"]))
    assert director["director"]["registrations"][0]["checked_in_at"] is not None


# ----------------------------------------------------------------------------
# Historial
# ----------------------------------------------------------------------------
async def test_history_shows_check_ins_and_hides_names_from_directors(client, w, ev):
    url = f"{ev['url']}/history"
    items = (await _ok(await client.get(url, params={"registration_id": ev["regs"]["a"], "limit": 200},
                                        headers=w["coordinator"]["headers"])))["items"]
    types = [i["type"] for i in items]
    assert types.count("CHECK_IN") == 2 and types.count("CHECK_IN_UNDONE") == 1
    undone = next(i for i in items if i["type"] == "CHECK_IN_UNDONE")
    assert undone["reason"] == "Se escaneó el club equivocado" and undone["points"] is None
    assert undone["activity"] is None and undone["actor"]["as"] == "COORDINATION"
    assert undone["actor"]["name"].endswith(" coordinator") and undone["id"] == f"cu:{ev['check_in_a']}"
    first = next(i for i in items if i["id"] == f"ci:{ev['check_in_a']}")
    assert first["actor"] == {"name": first["actor"]["name"], "as": "JUDGE"}
    assert first["actor"]["name"].endswith(" judge-marcha")
    autos = {(i["auto"], i["activity"]["name"]) for i in items if i["type"] == "EVALUATION_CREATED"}
    assert autos == {("CHECKIN", "Llegada"), ("SCAN", "Señal"), ("CHECKIN", "Bienvenida")}
    scan = next(i for i in items if i["auto"] == "SCAN")
    assert scan["actor"]["as"] == "JUDGE" and scan["points"] == 150
    voided = next(i for i in items if i["type"] == "EVALUATION_VOIDED")
    assert voided["reason"] == "Se escaneó el club equivocado" and voided["activity"]["name"] == "Llegada"
    # A director: the same entries of their club, the role and never the name.
    mine = (await _ok(await client.get(url, params={"limit": 200}, headers=w["director_a"]["headers"])))["items"]
    assert [i["id"] for i in mine] == [i["id"] for i in items]
    assert all(i["actor"]["name"] is None and i["actor"]["as"] in ("JUDGE", "COORDINATION") for i in mine)
    # Judges and coordination keep the names.
    judged = (await _ok(await client.get(url, headers=w["judge"]["headers"])))["items"]
    assert judged and all(i["actor"]["name"] for i in judged)


# ----------------------------------------------------------------------------
# /p/<token>
# ----------------------------------------------------------------------------
async def test_pass_page_identifies_the_club_only_for_that_events_staff(client, w, ev, factory):
    url = f"{API}/pass/resolve"
    body = {"token": event_pass.pass_url(ev["tokens"]["b"])}
    assert (await client.post(url, json=body)).status_code == 401
    for who in ("coordinator", "judge", "admin"):
        found = await _ok(await client.post(url, json=body, headers=w[who]["headers"]))
        assert found["event"]["id"] == ev["id"] and found["registration"]["id"] == ev["regs"]["b"]
        assert found["registration"]["club"]["name"].endswith("club-b")
        assert found["registration"].get("pass_token") is None
    # Everybody else — the club's own director included — gets the same neutral 404.
    other_admin = await factory.user("other-admin", "ADMIN_ASSOCIATION", w["other"]["id"])
    for headers in (w["director_b"]["headers"], w["stranger"]["headers"], other_admin["headers"]):
        response = await client.post(url, json=body, headers=headers)
        assert response.status_code == 404 and "club" not in response.text.lower()
    unknown = await client.post(url, json={"token": "evp_" + "z" * 30}, headers=w["coordinator"]["headers"])
    assert unknown.status_code == 404


# ----------------------------------------------------------------------------
# B10 y B9
# ----------------------------------------------------------------------------
async def test_b10_events_listed_by_a_second_admin_role(client, w, ev):
    person = w["second_admin"]
    assert ev["id"] not in [e["id"] for e in await _ok(await client.get(API, headers=person["headers"]))]
    await _exec("INSERT INTO role_assignments (user_id, role, organization_id, status, is_primary, source)"
                " VALUES (:u, 'ADMIN_ASSOCIATION', :o, 'ACTIVE', false, 'ADMIN')",
                u=uuid.UUID(person["id"]), o=uuid.UUID(w["assoc"]["id"]))
    principal = await fetch_one("SELECT role FROM users WHERE id = :id", id=uuid.UUID(person["id"]))
    assert principal["role"] == "STUDENT"
    listed = {e["id"]: e for e in await _ok(await client.get(API, headers=person["headers"]))}
    assert ev["id"] in listed and listed[ev["id"]]["my_roles"] == ["ADMIN"]


async def test_b9_signing_up_as_director_then_accepting_leaves_one_director_role(client, w, factory):
    club = await factory.org("club-b9", "club", w["church"])
    person = await factory.user("b9-director", "CLUB_DIRECTOR")  # signed up as director, no club yet
    invited = await _ok(await client.post(f"/api/v1/org-nodes/{club['id']}/invitations", json={
        "role": "CLUB_DIRECTOR", "email": person["email"]}, headers=w["admin"]["headers"]), 201)
    accepted = await _ok(await client.post("/api/v1/org-invitations/accept", json={"token": invited["token"]},
                                           headers=person["headers"]))
    assert [(r["role"], (r["organization"] or {}).get("id")) for r in accepted["roles"]] == [
        ("CLUB_DIRECTOR", club["id"])]
    active = await fetch_all("SELECT role, organization_id::text AS org FROM role_assignments"
                             " WHERE user_id = :u AND status = 'ACTIVE'", u=uuid.UUID(person["id"]))
    assert [dict(r) for r in active] == [{"role": "CLUB_DIRECTOR", "org": club["id"]}]
    me = await _ok(await client.get("/api/v1/auth/me", headers=person["headers"]))
    assert [r["role"] for r in me["roles"]] == ["CLUB_DIRECTOR"]


# ----------------------------------------------------------------------------
# Invitaciones con return_to
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "https://evil.example.com/eventos/1", "//evil.example.com/x", "/\\evil.example.com", "javascript:alert(1)",
    "https://user:pw@events.adventist.club/x", "ftp://events.adventist.club/x", "https://events.adventist.club:x/",
    "/eventos/\n1", "eventos/1",
])
async def test_return_to_only_own_hosts(client, w, factory, bad):
    club = await factory.org(f"club-rt-{uuid.uuid4().hex[:6]}", "club", w["church"])
    response = await client.post(f"/api/v1/org-nodes/{club['id']}/invitations", json={
        "role": "CLUB_DIRECTOR", "email": factory.email("rt-bad"), "return_to": bad}, headers=w["admin"]["headers"])
    assert response.status_code == 422, (bad, response.text)


async def test_return_to_travels_with_the_invitation(client, w, factory):
    club = await factory.org("club-rt", "club", w["church"])
    person = await factory.user("rt-director", "STUDENT")
    back = f"https://events.adventist.club/eventos/{uuid.uuid4()}"
    created = await _ok(await client.post(f"/api/v1/org-nodes/{club['id']}/invitations", json={
        "role": "CLUB_DIRECTOR", "email": person["email"], "return_to": back}, headers=w["admin"]["headers"]), 201)
    assert created["invitation"]["return_to"] == back
    assert created["url"].startswith("https://events.adventist.club/invitacion?t=")
    resent = await _ok(await client.post(
        f"/api/v1/org-nodes/{club['id']}/invitations/{created['invitation']['id']}/resend",
        headers=w["admin"]["headers"]), 201)
    assert resent["invitation"]["return_to"] == back
    preview = await _ok(await client.post("/api/v1/org-invitations/preview", json={"token": resent["token"]}))
    assert preview["return_to"] == back
    accepted = await _ok(await client.post("/api/v1/org-invitations/accept", json={"token": resent["token"]},
                                           headers=person["headers"]))
    assert accepted["return_to"] == back
    # A relative path is fine too, and without return_to nothing changes.
    other = await factory.org("club-rt-2", "club", w["church"])
    relative = await _ok(await client.post(f"/api/v1/org-nodes/{other['id']}/invitations", json={
        "role": "CLUB_DIRECTOR", "email": factory.email("rt-rel"), "return_to": "/eventos/1"},
        headers=w["admin"]["headers"]), 201)
    assert relative["invitation"]["return_to"] == "/eventos/1"
    assert relative["url"].startswith(f"{settings.frontend_url}/invitacion?t=")
    plain = await _ok(await client.post(f"/api/v1/org-nodes/{w['assoc']['id']}/invitations", json={
        "role": "INSTRUCTOR", "email": factory.email("rt-none")}, headers=w["admin"]["headers"]), 201)
    assert plain["invitation"]["return_to"] is None


async def test_closed_event_blocks_check_in_and_scan(client, w, ev):
    await _status(client, w, ev, "CLOSED")
    response = await client.post(f"{ev['url']}/check-in", json={"registration_id": ev["regs"]["a"]},
                                 headers=w["coordinator"]["headers"])
    assert response.status_code == 409
    scan = await client.post(f"{ev['url']}/activities/{ev['senal']}/scan", json={"pass_token": ev["tokens"]["a"]},
                             headers=w["judge"]["headers"])
    assert scan.status_code == 409
    undo = await client.request("DELETE", f"{ev['url']}/registrations/{ev['regs']['a']}/check-in",
                                json={"reason": "tarde"}, headers=w["coordinator"]["headers"])
    assert undo.status_code == 409
    # The pass is still shown (stable), never rotated.
    shown = await _ok(await client.get(_pass_url(ev, "a"), headers=w["director_a"]["headers"]))
    assert shown["pass_token"] == ev["tokens"]["a"]
