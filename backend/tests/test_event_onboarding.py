"""031 — alta desde el evento (FIDELIDAD_PROTOTIPO.md, Parte 2) y «Ver como…» (Parte 3).

Proved here:
  * directors of the event's clubs: NONE / INVITED / ACTIVE; only the association's
    administration links them (an event COORDINATOR reads, never links);
  * «Vincular director»: an account is appointed (idempotent: `already_director`), another
    address on a club that has one is refused, an address without an account gets ONE
    CLUB_DIRECTOR invitation that returns to the event; accepting shows only that club;
  * «Importar lista»: the dry run writes nothing; commit invites; a second commit duplicates
    nothing; bad rows are reported and never touched; admins only;
  * «Invitar juez»: an account is added at once; an address without one gets an EVENT_STAFF
    invitation whose `on_accept` creates the event_staff rows ONCE (a second accept is the
    single 404), never for a minor, never after the event closed (and the token survives);
    resend kills the old link; revoke kills it; the association's team list ignores them;
  * search-as-you-type stays inside the owner's association and leaves minors out;
  * «Ver como…»: coordination only; the judge / director views; any write that carries
    `X-Event-Preview` is refused (403) whatever the route.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import RUN, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("onboard")
API = "/api/v1/events"
ACCEPT = "/api/v1/org-invitations/accept"
PREVIEW = "/api/v1/org-invitations/preview"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _adventurers(club: dict) -> None:
    await _exec(
        "UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
        " WHERE id = :id", id=uuid.UUID(club["id"]))
    await _exec(
        "INSERT INTO organization_ministries (organization_id, ministry_id)"
        " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
        id=uuid.UUID(club["id"]))


def _token(url: str) -> str:
    return url.split("t=", 1)[1]


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b", "c", "d")}
    for key, club in clubs.items():
        await _adventurers(club)
        club["name"] = factory.name(f"club-{key}")
    other = await factory.org("assoc-2", "association")
    people = {
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "admin_other": await factory.user("admin-2", "ADMIN_ASSOCIATION", other["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR", church["id"]),
        "judge": await factory.user("judge", "INSTRUCTOR", church["id"]),
        "stranger": await factory.user("stranger", "STUDENT"),
        "outsider": await factory.user("outsider", "INSTRUCTOR", other["id"]),
        "kid": await factory.user("kid", "STUDENT", church["id"], is_minor=True),
        "director_a": await factory.user("director-a", "CLUB_DIRECTOR", clubs["a"]["id"]),
        "future_director_b": await factory.user("future-b", "INSTRUCTOR", church["id"]),
    }
    h = people["admin"]["headers"]
    created = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Onboarding",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    activities = []
    for name in ("Señal", "Marcha", "Esencia"):
        response = await client.post(f"{API}/{event_id}/activities", json={
            "name": name, "kind": "participation", "max_points": 50, "config": {"max": 50}}, headers=h)
        assert response.status_code == 201, response.text
        activities.append(response.json()["id"])
    registrations = {}
    for key, club in clubs.items():
        response = await client.post(f"{API}/{event_id}/registrations", json={"club_id": club["id"]}, headers=h)
        assert response.status_code == 201, response.text
        registrations[key] = response.json()["id"]
    for who, role in (("coordinator", "COORDINATOR"), ("judge", "JUDGE")):
        body = {"email": people[who]["email"], "role": role}
        if role == "JUDGE":
            body["activity_id"] = activities[0]
        assert (await client.post(f"{API}/{event_id}/staff", json=body, headers=h)).status_code == 201
    assert (await client.post(f"{API}/{event_id}/status", json={"status": "OPEN"}, headers=h)).status_code == 200
    world = {"assoc": assoc, "other": other, "clubs": clubs, "event": event_id, "activities": activities,
             "registrations": registrations, **people}
    yield world
    await _exec("DELETE FROM org_invitations WHERE on_accept->>'event_id' = :id", id=event_id)
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event_id))


async def _directors(client, w, who="admin") -> dict:
    response = await client.get(f"{API}/{w['event']}/directors", headers=w[who]["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _state(listing: dict, w, key: str) -> dict:
    return next(row for row in listing["clubs"] if row["registration_id"] == w["registrations"][key])


# ----------------------------------------------------------------------------
# Directors
# ----------------------------------------------------------------------------
async def test_directors_list_and_who_may_link(client, w):
    listing = await _directors(client, w)
    assert listing["can_manage"] is True
    assert _state(listing, w, "a")["state"] == "ACTIVE"
    assert _state(listing, w, "a")["director"]["email"] == w["director_a"]["email"]
    assert _state(listing, w, "b")["state"] == "NONE"
    # The event's COORDINATOR reads the list but may not link (no platform rank over clubs).
    as_coordinator = await _directors(client, w, "coordinator")
    assert as_coordinator["can_manage"] is False
    url = f"{API}/{w['event']}/registrations/{w['registrations']['b']}/director"
    body = {"name": "Futura", "email": w["future_director_b"]["email"]}
    for who, code in (("coordinator", 403), ("judge", 403), ("stranger", 403), ("admin_other", 403)):
        assert (await client.post(url, json=body, headers=w[who]["headers"])).status_code == code, who
    assert (await client.get(f"{API}/{w['event']}/directors", headers=w["judge"]["headers"])).status_code == 403


async def test_link_an_existing_account_appoints_once(client, w):
    url = f"{API}/{w['event']}/registrations/{w['registrations']['b']}/director"
    body = {"name": "Futura", "email": w["future_director_b"]["email"].upper()}
    first = await client.post(url, json=body, headers=w["admin"]["headers"])
    assert first.status_code == 200, first.text
    assert first.json()["action"] == "appoint"
    assert first.json()["club"]["state"] == "ACTIVE"
    again = await client.post(url, json=body, headers=w["admin"]["headers"])
    assert again.json()["action"] == "already_director"
    other = await client.post(url, json={"email": w["judge"]["email"]}, headers=w["admin"]["headers"])
    assert other.status_code == 409
    membership = await fetch_one(
        "SELECT role, status FROM club_memberships WHERE user_id = :u AND club_id = :c",
        u=uuid.UUID(w["future_director_b"]["id"]), c=uuid.UUID(w["clubs"]["b"]["id"]))
    assert dict(membership) == {"role": "CLUB_DIRECTOR", "status": "ACTIVE"}


async def test_link_without_account_invites_and_accepting_shows_only_that_club(client, factory, w):
    url = f"{API}/{w['event']}/registrations/{w['registrations']['c']}/director"
    email = factory.email("dir-c")
    created = await client.post(url, json={"name": "Directora C", "email": email}, headers=w["admin"]["headers"])
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action"] == "invite" and body["club"]["state"] == "INVITED"
    invitation = body["club"]["invitation"]
    assert invitation["name"] == "Directora C" and invitation["kind"] == "director"
    assert invitation["url"].startswith("https://events.adventist.club/invitacion?t=")
    assert invitation["whatsapp_url"].startswith("https://wa.me/?text=")
    row = await fetch_one("SELECT role, return_to, on_accept FROM org_invitations WHERE id = :id",
                          id=uuid.UUID(invitation["id"]))
    assert row["role"] == "CLUB_DIRECTOR"
    assert row["return_to"] == f"https://events.adventist.club/{w['event']}/club"
    assert row["on_accept"]["type"] == "event_director"
    assert await fetch_one("SELECT id FROM notification_log WHERE entity_id = :id", id=invitation["id"])
    # Idempotent: the same address again does not create a second invitation.
    again = await client.post(url, json={"email": email}, headers=w["admin"]["headers"])
    assert again.json()["action"] == "invitation_pending"
    assert again.json()["club"]["invitation"]["url"] is None  # no token the second time
    count = await fetch_one("SELECT count(*) AS n FROM org_invitations WHERE email = :e", e=email)
    assert count["n"] == 1
    # Another address while one is pending: refused.
    assert (await client.post(url, json={"email": factory.email("other-c")},
                              headers=w["admin"]["headers"])).status_code == 409
    # It is listed in the event (and not in the association's team).
    listed = await client.get(f"{API}/{w['event']}/invitations", headers=w["admin"]["headers"])
    assert any(item["id"] == invitation["id"] for item in listed.json())

    person = await factory.user("dir-c", "STUDENT")
    preview = await client.post(PREVIEW, json={"token": _token(invitation["url"])})
    assert preview.json()["event"]["id"] == w["event"]
    ok = await client.post(ACCEPT, json={"token": _token(invitation["url"])}, headers=person["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["return_to"].endswith(f"/{w['event']}/club")
    mine = await client.get(f"{API}/{w['event']}/my", headers=person["headers"])
    clubs = [item["club"]["id"] for item in mine.json()["director"]["registrations"]]
    assert clubs == [w["clubs"]["c"]["id"]]
    regs = await client.get(f"{API}/{w['event']}/registrations", headers=person["headers"])
    assert [item["club"]["id"] for item in regs.json()] == [w["clubs"]["c"]["id"]]
    assert _state(await _directors(client, w), w, "c")["state"] == "ACTIVE"


async def test_import_list_dry_run_then_commit_is_idempotent(client, factory, w):
    url = f"{API}/{w['event']}/directors/import"
    club_d = w["clubs"]["d"]
    email = factory.email("dir-d")
    csv = (
        "Club;Nombre del director;Correo\n"
        f"{club_d['name'].upper()};Director D;{email}\n"
        f"{w['clubs']['a']['name']};Director A;{w['director_a']['email']}\n"
        "Club que no existe;Nadie;nadie@example.com\n"
        f"{w['clubs']['b']['name']};Sin correo;\n"
    )
    for who in ("coordinator", "judge"):
        assert (await client.post(url, json={"csv": csv}, headers=w[who]["headers"])).status_code == 403
    dry = await client.post(url, json={"csv": csv}, headers=w["admin"]["headers"])
    assert dry.status_code == 200, dry.text
    actions = [row["action"] for row in dry.json()["rows"]]
    assert actions == ["invite", "already_director", "error", "error"]
    assert dry.json()["invitations"] == []
    assert await fetch_one("SELECT id FROM org_invitations WHERE email = :e", e=email) is None

    done = await client.post(url, json={"csv": csv, "commit": True}, headers=w["admin"]["headers"])
    assert done.status_code == 200, done.text
    assert done.json()["summary"]["invite"] == 1
    assert done.json()["invitations"][0]["url"]
    again = await client.post(url, json={"csv": csv, "commit": True}, headers=w["admin"]["headers"])
    assert [row["action"] for row in again.json()["rows"]][:2] == ["invitation_pending", "already_director"]
    count = await fetch_one("SELECT count(*) AS n FROM org_invitations WHERE email = :e", e=email)
    assert count["n"] == 1
    missing = await client.post(url, json={"csv": "nombre;telefono\nx;1\n"}, headers=w["admin"]["headers"])
    assert missing.status_code == 422


# ----------------------------------------------------------------------------
# Judges and coordination
# ----------------------------------------------------------------------------
async def test_invite_existing_account_adds_now(client, factory, w):
    person = await factory.user("judge-now", "INSTRUCTOR")
    url = f"{API}/{w['event']}/staff/invitations"
    response = await client.post(url, json={"email": person["email"], "role": "JUDGE",
                                             "activity_ids": w["activities"][:2]}, headers=w["coordinator"]["headers"])
    assert response.status_code == 201, response.text
    assert response.json()["action"] == "added"
    assert sorted(row["activity_id"] for row in response.json()["staff"]) == sorted(w["activities"][:2])
    # Same again: nothing duplicated.
    await client.post(url, json={"email": person["email"], "role": "JUDGE", "activity_ids": w["activities"][:2]},
                      headers=w["coordinator"]["headers"])
    rows = await fetch_all("SELECT id FROM event_staff WHERE user_id = :u AND active", u=uuid.UUID(person["id"]))
    assert len(rows) == 2
    minor = await client.post(url, json={"email": w["kid"]["email"], "role": "JUDGE"}, headers=w["admin"]["headers"])
    assert minor.status_code == 403
    for who in ("judge", "stranger", "director_a"):
        assert (await client.post(url, json={"email": person["email"], "role": "JUDGE"},
                                  headers=w[who]["headers"])).status_code == 403


async def test_invite_judge_without_account_applies_on_accept_once(client, factory, w):
    url = f"{API}/{w['event']}/staff/invitations"
    email = factory.email("judge-new")
    chosen = w["activities"][1:]
    created = await client.post(url, json={"email": email, "name": "Jueza Nueva", "role": "JUDGE",
                                           "activity_ids": chosen}, headers=w["coordinator"]["headers"])
    assert created.status_code == 201, created.text
    invitation = created.json()["invitation"]
    assert created.json()["action"] == "invite" and invitation["kind"] == "judge"
    assert invitation["activity_ids"] == chosen
    duplicate = await client.post(url, json={"email": email, "role": "JUDGE"}, headers=w["coordinator"]["headers"])
    assert duplicate.status_code == 409
    row = await fetch_one("SELECT role, organization_id::text AS org, return_to FROM org_invitations WHERE id = :id",
                          id=uuid.UUID(invitation["id"]))
    assert row["role"] == "EVENT_STAFF" and row["org"] == w["assoc"]["id"]
    assert row["return_to"].endswith(f"/{w['event']}/juez")
    # Not on the association's team screen.
    team = await client.get(f"/api/v1/org-nodes/{w['assoc']['id']}/invitations", headers=w["admin"]["headers"])
    assert all(item["id"] != invitation["id"] for item in team.json())
    preview = await client.post(PREVIEW, json={"token": _token(invitation["url"])})
    assert preview.status_code == 200
    assert preview.json()["role"] == "EVENT_STAFF" and preview.json()["event"]["staff_role"] == "JUDGE"

    person = await factory.user("judge-new", "STUDENT")
    wrong = await client.post(ACCEPT, json={"token": _token(invitation["url"])}, headers=w["stranger"]["headers"])
    assert wrong.status_code == 403
    ok = await client.post(ACCEPT, json={"token": _token(invitation["url"])}, headers=person["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["return_to"].endswith(f"/{w['event']}/juez")
    rows = await fetch_all("SELECT activity_id::text AS a, role FROM event_staff WHERE user_id = :u AND active",
                           u=uuid.UUID(person["id"]))
    assert sorted(r["a"] for r in rows) == sorted(chosen) and {r["role"] for r in rows} == {"JUDGE"}
    twice = await client.post(ACCEPT, json={"token": _token(invitation["url"])}, headers=person["headers"])
    assert twice.status_code == 404
    rows_after = await fetch_all("SELECT id FROM event_staff WHERE user_id = :u", u=uuid.UUID(person["id"]))
    assert len(rows_after) == len(chosen)
    mine = await client.get(f"{API}/{w['event']}/my", headers=person["headers"])
    assert sorted(a["id"] for a in mine.json()["judge"]["activities"]) == sorted(chosen)
    # Users.role untouched: the judge is a permission of the event.
    assert (await fetch_one("SELECT role FROM users WHERE id = :u", u=uuid.UUID(person["id"])))["role"] == "STUDENT"


async def test_coordinator_invitation_resend_revoke_minor_and_closed(client, factory, w):
    url = f"{API}/{w['event']}/staff/invitations"
    email = factory.email("coord-new")
    created = (await client.post(url, json={"email": email, "role": "COORDINATOR"},
                                 headers=w["admin"]["headers"])).json()["invitation"]
    assert created["kind"] == "coordinator" and created["activity_ids"] is None
    # Resend: new link, the old one dies.
    renewed = await client.post(f"{API}/{w['event']}/invitations/{created['id']}/resend", headers=w["admin"]["headers"])
    assert renewed.status_code == 201, renewed.text
    renewed = renewed.json()
    assert renewed["id"] != created["id"] and renewed["url"] != created["url"]
    assert (await client.post(PREVIEW, json={"token": _token(created["url"])})).status_code == 404
    # A judge of the event may not manage invitations.
    assert (await client.delete(f"{API}/{w['event']}/invitations/{renewed['id']}",
                                headers=w["judge"]["headers"])).status_code == 403
    # Another event's id: 404.
    assert (await client.delete(f"{API}/{uuid.uuid4()}/invitations/{renewed['id']}",
                                headers=w["admin"]["headers"])).status_code == 404
    assert (await client.delete(f"{API}/{w['event']}/invitations/{renewed['id']}",
                                headers=w["admin"]["headers"])).status_code == 204
    assert (await client.delete(f"{API}/{w['event']}/invitations/{renewed['id']}",
                                headers=w["admin"]["headers"])).status_code == 204
    person = await factory.user("coord-new", "STUDENT")
    assert (await client.post(ACCEPT, json={"token": _token(renewed["url"])}, headers=person["headers"])).status_code == 404

    # A minor never accepts (and the invitation is not spent by trying).
    kid_email = factory.email("kid-judge")
    kid_inv = (await client.post(url, json={"email": kid_email, "role": "JUDGE"},
                                 headers=w["admin"]["headers"])).json()["invitation"]
    kid = await factory.user("kid-judge", "STUDENT", is_minor=True)
    assert (await client.post(ACCEPT, json={"token": _token(kid_inv["url"])}, headers=kid["headers"])).status_code == 403
    assert (await client.post(PREVIEW, json={"token": _token(kid_inv["url"])})).status_code == 200

    # A closed event: the acceptance is refused and the token survives for when it reopens.
    late_email = factory.email("late-judge")
    late_inv = (await client.post(url, json={"email": late_email, "role": "JUDGE"},
                                  headers=w["admin"]["headers"])).json()["invitation"]
    await _exec("UPDATE events SET status = 'CLOSED' WHERE id = :id", id=uuid.UUID(w["event"]))
    try:
        late = await factory.user("late-judge", "STUDENT")
        refused = await client.post(ACCEPT, json={"token": _token(late_inv["url"])}, headers=late["headers"])
        assert refused.status_code == 409
        assert (await client.post(PREVIEW, json={"token": _token(late_inv["url"])})).status_code == 200
        # And no new invitation while closed.
        assert (await client.post(url, json={"email": factory.email("x"), "role": "JUDGE"},
                                  headers=w["admin"]["headers"])).status_code == 409
    finally:
        await _exec("UPDATE events SET status = 'OPEN' WHERE id = :id", id=uuid.UUID(w["event"]))
    assert (await client.post(ACCEPT, json={"token": _token(late_inv["url"])}, headers=late["headers"])).status_code == 200


async def test_activities_of_another_event_are_refused(client, factory, w):
    url = f"{API}/{w['event']}/staff/invitations"
    response = await client.post(url, json={"email": factory.email("bad-act"), "role": "JUDGE",
                                            "activity_ids": [str(uuid.uuid4())]}, headers=w["admin"]["headers"])
    assert response.status_code == 404
    response = await client.post(url, json={"email": factory.email("bad-coord"), "role": "COORDINATOR",
                                            "activity_ids": w["activities"][:1]}, headers=w["admin"]["headers"])
    assert response.status_code == 422


async def test_search_stays_inside_the_association(client, w):
    url = f"{API}/{w['event']}/people"
    found = await client.get(url, params={"q": f"{RUN}-onboard"}, headers=w["coordinator"]["headers"])
    assert found.status_code == 200, found.text
    emails = {item["email"] for item in found.json()}
    assert w["judge"]["email"] in emails or len(found.json()) == 8
    named = await client.get(url, params={"q": "outsider"}, headers=w["admin"]["headers"])
    assert all(item["email"] != w["outsider"]["email"] for item in named.json())
    kid = await client.get(url, params={"q": w["kid"]["email"]}, headers=w["admin"]["headers"])
    assert kid.json() == []
    judge = await client.get(url, params={"q": w["judge"]["email"]}, headers=w["admin"]["headers"])
    assert judge.json()[0]["event_roles"] == ["JUDGE"]
    assert (await client.get(url, params={"q": "a"}, headers=w["admin"]["headers"])).status_code == 422
    assert (await client.get(url, params={"q": "judge"}, headers=w["judge"]["headers"])).status_code == 403


# ----------------------------------------------------------------------------
# «Ver como…»
# ----------------------------------------------------------------------------
async def test_preview_as_judge_and_director(client, w):
    base = f"{API}/{w['event']}/preview"
    as_judge = await client.get(base, params={"role": "judge", "user_id": w["judge"]["id"]},
                                headers=w["coordinator"]["headers"])
    assert as_judge.status_code == 200, as_judge.text
    view = as_judge.json()
    assert [a["id"] for a in view["judge"]["activities"]] == [w["activities"][0]]
    assert "coordination" not in view and view["preview"]["role"] == "judge"
    anyone = (await client.get(base, params={"role": "judge"}, headers=w["admin"]["headers"])).json()
    assert len(anyone["judge"]["activities"]) == len(w["activities"])
    director = await client.get(base, params={"role": "director", "registration_id": w["registrations"]["a"]},
                                headers=w["admin"]["headers"])
    assert [r["club"]["id"] for r in director.json()["director"]["registrations"]] == [w["clubs"]["a"]["id"]]
    assert director.json()["preview"]["label"] == w["clubs"]["a"]["name"]
    assert (await client.get(base, params={"role": "judge", "user_id": w["stranger"]["id"]},
                             headers=w["admin"]["headers"])).status_code == 404
    for who in ("judge", "director_a", "stranger"):
        assert (await client.get(base, params={"role": "judge"}, headers=w[who]["headers"])).status_code == 403


async def test_writes_carrying_the_preview_header_are_refused(client, w):
    header = {**w["admin"]["headers"], "X-Event-Preview": "judge"}
    refused = await client.post(f"{API}/{w['event']}/status", json={"status": "IN_PROGRESS"}, headers=header)
    assert refused.status_code == 403 and refused.json()["code"] == "event_preview"
    refused = await client.patch(f"{API}/{w['event']}", json={"name": "x"}, headers=header)
    assert refused.status_code == 403
    refused = await client.post("/api/v1/org-invitations/accept", json={"token": "x" * 20}, headers=header)
    assert refused.status_code == 403
    assert (await client.get(f"{API}/{w['event']}", headers=header)).status_code == 200
    event = await fetch_one("SELECT status, name FROM events WHERE id = :id", id=uuid.UUID(w["event"]))
    assert event["status"] == "OPEN" and event["name"] == f"{RUN} Onboarding"


@pytest.mark.parametrize("value", ["", "   "])
async def test_an_empty_preview_header_is_not_a_preview(client, w, value):
    response = await client.get(f"{API}/{w['event']}/directors", headers={**w["admin"]["headers"], "X-Event-Preview": value})
    assert response.status_code == 200


async def test_registrations_name_code_church_and_director(client, w):
    """031: «ciudad · iglesia» and «código · director» of each club for the lists."""
    rows = (await client.get(f"{API}/{w['event']}/registrations", headers=w["admin"]["headers"])).json()
    row_a = next(row for row in rows if row["id"] == w["registrations"]["a"])
    assert row_a["club"]["church"].endswith("church")
    assert row_a["director_name"] and "code" in row_a["club"]
