"""«Pedir ser director de este club»: the association first, then its existing club; the
administration approves; the association's administrators are e-mailed; «Actividad».

Proved here:
  * `GET /director-requests/clubs` (no session) lists only the ACTIVE clubs of the chosen
    association, accents and case ignored, with name, church and city and nothing personal;
    a club with a director or a pending request says so; a club in an open event carries it;
  * signing up with `club_director_request` creates an adult INSTRUCTOR and a pending request
    (no e-mail to the address, no new club); the association's administrators are e-mailed
    (the other association's are not); a club with a director, or another address pending,
    is refused and no account is left behind;
  * the list and the decision are the association's administration's: another association's
    administrator and a zone coordinator get nothing / 403; «Aprobar» appoints a VERIFIED
    account at once and otherwise e-mails the invitation; «Rechazar» frees the club;
  * a new club at sign-up and the event's «Mi club ya está en el evento» e-mail them too;
  * `GET /admin/activity` shows these events in the admin's scope only.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.services import email as email_service
from tests.conftest import RUN, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("dirclub")
BASE = "/api/v1/director-requests"
SEARCH = f"{BASE}/clubs"
ACTIVITY = "/api/v1/admin/activity"
REGISTER = "/api/v1/auth/register"
EVENTS = "/api/v1/events"
PASSWORD = "Camporee-2026x"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _ministry(club_id: str) -> None:
    await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                " WHERE id = :id", id=uuid.UUID(club_id))
    await _exec("INSERT INTO organization_ministries (organization_id, ministry_id)"
                " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
                id=uuid.UUID(club_id))


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    other_assoc = await factory.org("other-assoc", "association")
    clubs = {key: await factory.org(f"club-{key}", "club", church) for key in ("a", "b", "c", "d", "e", "f")}
    names = {}
    for key, club in clubs.items():
        names[key] = f"{factory.name(f'club-{key}')} Pléyades"
        await _exec("UPDATE organizations SET name = :n, city = 'Reynosa' WHERE id = :id",
                    n=names[key], id=uuid.UUID(club["id"]))
        await _ministry(club["id"])
    foreign = await factory.org("club-foreign", "club", other_assoc)
    await _exec("UPDATE organizations SET name = :n WHERE id = :id",
                n=f"{factory.name('club-foreign')} Pléyades", id=uuid.UUID(foreign["id"]))
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    other_admin = await factory.user("other-admin", "ADMIN_ASSOCIATION", other_assoc["id"])
    coordinator = await factory.user("zone-coord", "COORDINATOR_ZONE", zone["id"])
    instructor = await factory.user("instructor", "INSTRUCTOR", church["id"])
    await factory.user("director-b", "CLUB_DIRECTOR", clubs["b"]["id"])

    # Club d is registered in an OPEN event.
    created = await client.post(EVENTS, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Existing club request",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=admin["headers"])
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    registered = await client.post(f"{EVENTS}/{event_id}/registrations", json={"club_id": clubs["d"]["id"]},
                                   headers=admin["headers"])
    assert registered.status_code == 201, registered.text
    assert (await client.post(f"{EVENTS}/{event_id}/status", json={"status": "OPEN"},
                              headers=admin["headers"])).status_code == 200
    yield {"assoc": assoc, "other_assoc": other_assoc, "clubs": clubs, "names": names, "foreign": foreign,
           "admin": admin, "other_admin": other_admin, "coordinator": coordinator, "instructor": instructor,
           "event": event_id, "registration_d": registered.json()["id"]}
    await _exec("DELETE FROM org_invitations WHERE on_accept->>'event_id' = :id", id=event_id)
    await _exec("DELETE FROM events WHERE id = :id", id=uuid.UUID(event_id))


@pytest.fixture
def mails(monkeypatch):
    """Every admin notice that would have been sent: (to, kind, club, person, email, link, event)."""
    sent = []

    async def fake(to, admin_name, kind, club_name, person_name, person_email, link,
                   association_name=None, event_name=None):
        sent.append({"to": to, "kind": kind, "club": club_name, "person": person_name, "email": person_email,
                     "link": link, "event": event_name, "association": association_name})
        return True

    monkeypatch.setattr(email_service, "send_admin_request_email", fake)
    return sent


def _signup(email: str, club_id: str, name: str = "Directora Existente") -> dict:
    return {"email": email, "password": PASSWORD, "name": name, "role": "CLUB_DIRECTOR",
            "club_director_request": {"club_id": club_id}}


async def _search(client, w, q: str, association: str | None = None) -> list[dict]:
    response = await client.get(SEARCH, params={"association_id": association or w["assoc"]["id"], "q": q})
    assert response.status_code == 200, response.text
    return response.json()


async def _requests(client, user) -> list[dict]:
    response = await client.get(BASE, headers=user["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_search_lists_the_association_clubs_without_session(client, factory, w):
    assert await _search(client, w, "p") == []
    found = await _search(client, w, w["names"]["a"].replace("é", "e").upper())
    assert [row["name"] for row in found] == [w["names"]["a"]]
    assert set(found[0]) == {"id", "name", "church", "city", "has_director", "request_pending",
                             "event_registration_id", "event_name"}
    assert found[0]["church"] == factory.name("church") and found[0]["city"] == "Reynosa"
    assert found[0]["has_director"] is False and found[0]["request_pending"] is False
    everyone = {row["name"]: row for row in await _search(client, w, f"{factory.prefix} club")}
    # Only this association: the other one's club never shows up here.
    assert f"{factory.name('club-foreign')} Pléyades" not in everyone
    assert everyone[w["names"]["b"]]["has_director"] is True
    assert everyone[w["names"]["d"]]["event_registration_id"] == w["registration_d"]
    assert everyone[w["names"]["d"]]["event_name"] == f"{RUN} Existing club request"
    # A club id is not an association.
    assert await _search(client, w, w["names"]["a"], association=w["clubs"]["a"]["id"]) == []


async def test_request_notice_and_approval_by_email(client, factory, w, mails):
    email = factory.email("req-a")
    signed = await client.post(REGISTER, json=_signup(email, w["clubs"]["a"]["id"]))
    assert signed.status_code == 201, signed.text
    assert signed.json()["role"] == "INSTRUCTOR" and signed.json()["organization_id"] is None
    assert "association's approval" in signed.json()["message"]
    # No club was created, and the address got nothing.
    assert await fetch_one("SELECT id FROM organizations WHERE name = 'Directora Existente'") is None
    assert await fetch_one("SELECT id FROM notification_log WHERE email = :e", e=email) is None
    # The association's administrator is told; the other association's is not.
    assert [(m["to"], m["kind"], m["club"], m["email"]) for m in mails] == [
        (w["admin"]["email"], "ADMIN_DIRECTOR_REQUEST", w["names"]["a"], email)]
    assert mails[0]["link"].endswith("/admin/directores") and mails[0]["person"] == "Directora Existente"
    log = await fetch_one("SELECT kind, entity_id FROM notification_log WHERE email = :e", e=w["admin"]["email"])
    assert dict(log) == {"kind": "ADMIN_DIRECTOR_REQUEST", "entity_id": w["clubs"]["a"]["id"]}

    rows = [row for row in await _requests(client, w["admin"]) if row["club"]["id"] == w["clubs"]["a"]["id"]]
    assert len(rows) == 1
    row = rows[0]
    assert row["email"] == email and row["name"] == "Directora Existente" and row["source"] == "signup"
    assert row["has_account"] is True and row["verified"] is False and row["event"] is None
    assert (await _search(client, w, w["names"]["a"]))[0]["request_pending"] is True

    # Another association, a zone coordinator, an instructor: neither list nor decision.
    assert all(r["club"]["id"] != w["clubs"]["a"]["id"] for r in await _requests(client, w["other_admin"]))
    assert (await client.get(BASE, headers=w["coordinator"]["headers"])).status_code == 403
    assert (await client.get(BASE, headers=w["instructor"]["headers"])).status_code == 403
    for who in ("other_admin", "coordinator", "instructor"):
        assert (await client.post(f"{BASE}/{row['id']}/approve", headers=w[who]["headers"])).status_code == 403
        assert (await client.post(f"{BASE}/{row['id']}/reject", headers=w[who]["headers"])).status_code == 403

    # Not verified: «Aprobar» e-mails the invitation to that address instead of appointing.
    ok = await client.post(f"{BASE}/{row['id']}/approve", headers=w["admin"]["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"action": "invite", "club": {"id": w["clubs"]["a"]["id"], "name": w["names"]["a"]},
                         "email": email}
    sent = await fetch_one("SELECT kind FROM notification_log WHERE email = :e", e=email)
    assert sent["kind"] == "ORG_INVITATION"
    assert await fetch_one(
        "SELECT m.id FROM club_memberships m JOIN users u ON u.id = m.user_id WHERE u.email = :e", e=email) is None
    assert all(r["club"]["id"] != w["clubs"]["a"]["id"] for r in await _requests(client, w["admin"]))
    assert (await client.post(f"{BASE}/{row['id']}/approve", headers=w["admin"]["headers"])).status_code == 404


async def test_verified_account_is_appointed_at_once(client, factory, w, mails):
    email = factory.email("req-e")
    assert (await client.post(REGISTER, json=_signup(email, w["clubs"]["e"]["id"]))).status_code == 201
    await _exec("UPDATE users SET verification_status = 'VERIFIED' WHERE email = :e", e=email)
    row = next(r for r in await _requests(client, w["admin"]) if r["club"]["id"] == w["clubs"]["e"]["id"])
    assert row["verified"] is True
    ok = await client.post(f"{BASE}/{row['id']}/approve", headers=w["admin"]["headers"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["action"] == "approved"
    member = await fetch_one(
        "SELECT m.role, m.status FROM club_memberships m JOIN users u ON u.id = m.user_id"
        " WHERE u.email = :e AND m.club_id = :c", e=email, c=uuid.UUID(w["clubs"]["e"]["id"]))
    assert dict(member) == {"role": "CLUB_DIRECTOR", "status": "ACTIVE"}
    assert (await _search(client, w, w["names"]["e"]))[0]["has_director"] is True
    # The club has its director now: another request is refused and leaves no account.
    late = factory.email("late-e")
    assert (await client.post(REGISTER, json=_signup(late, w["clubs"]["e"]["id"]))).status_code == 409
    assert await fetch_one("SELECT id FROM users WHERE email = :e", e=late) is None


async def test_refusals_leave_no_account(client, factory, w, mails):
    taken = factory.email("req-b")
    assert (await client.post(REGISTER, json=_signup(taken, w["clubs"]["b"]["id"]))).status_code == 409
    assert await fetch_one("SELECT id FROM users WHERE email = :e", e=taken) is None
    foreign_assoc = factory.email("req-assoc")
    # An association (not a club) is not a club to direct.
    assert (await client.post(REGISTER, json=_signup(foreign_assoc, w["assoc"]["id"]))).status_code == 409
    assert await fetch_one("SELECT id FROM users WHERE email = :e", e=foreign_assoc) is None

    first = factory.email("first-c")
    assert (await client.post(REGISTER, json=_signup(first, w["clubs"]["c"]["id"]))).status_code == 201
    second = factory.email("second-c")
    refused = await client.post(REGISTER, json=_signup(second, w["clubs"]["c"]["id"]))
    assert refused.status_code == 409 and "pendiente" in refused.json()["detail"]
    assert await fetch_one("SELECT id FROM users WHERE email = :e", e=second) is None
    assert [m["email"] for m in mails] == [first]

    # «Rechazar»: the request goes, the account stays, the club is free again.
    row = next(r for r in await _requests(client, w["admin"]) if r["club"]["id"] == w["clubs"]["c"]["id"])
    no = await client.post(f"{BASE}/{row['id']}/reject", headers=w["admin"]["headers"])
    assert no.status_code == 200 and no.json()["action"] == "rejected"
    assert (await _search(client, w, w["names"]["c"]))[0]["request_pending"] is False
    assert await fetch_one("SELECT role FROM users WHERE email = :e", e=first) is not None
    again = factory.email("again-c")
    assert (await client.post(REGISTER, json=_signup(again, w["clubs"]["c"]["id"]))).status_code == 201


async def test_request_is_exclusive(client, factory, w):
    body = _signup(factory.email("both"), w["clubs"]["f"]["id"])
    body["event_director_request"] = {"registration_id": w["registration_d"]}
    assert (await client.post(REGISTER, json=body)).status_code == 400
    body = _signup(factory.email("both-club"), w["clubs"]["f"]["id"])
    body["club"] = {"name": "Otro", "association_id": w["assoc"]["id"], "ministries": ["adventurers"],
                    "church": "Iglesia Central"}
    assert (await client.post(REGISTER, json=body)).status_code == 400
    body = _signup(factory.email("student"), w["clubs"]["f"]["id"])
    body["role"] = "STUDENT"
    assert (await client.post(REGISTER, json=body)).status_code == 400


async def test_new_club_and_event_requests_notify_too(client, factory, w, mails):
    # A new club at sign-up (today's behaviour), now with the administrators' e-mail.
    director = factory.email("new-club")
    club_name = factory.name("club-nuevo")
    signed = await client.post(REGISTER, json={
        "email": director, "password": PASSWORD, "name": "Director Nuevo", "role": "CLUB_DIRECTOR",
        "club": {"name": club_name, "association_id": w["assoc"]["id"], "ministries": ["adventurers"],
                 "church": "Iglesia Central"}})
    assert signed.status_code == 201, signed.text
    assert [(m["to"], m["kind"], m["club"]) for m in mails] == [(w["admin"]["email"], "ADMIN_CLUB_REQUEST", club_name)]
    assert mails[0]["link"].endswith("/admin/clubes?vista=por-aprobar")

    # «Mi club ya está en el evento» (unchanged flow) also tells them, linking the event's Clubes.
    mails.clear()
    asked = factory.email("event-d")
    signed = await client.post(REGISTER, json={
        "email": asked, "password": PASSWORD, "name": "Directora Evento", "role": "CLUB_DIRECTOR",
        "event_director_request": {"registration_id": w["registration_d"]}})
    assert signed.status_code == 201, signed.text
    assert [(m["to"], m["kind"], m["club"], m["event"]) for m in mails] == [
        (w["admin"]["email"], "ADMIN_DIRECTOR_REQUEST", w["names"]["d"], f"{RUN} Existing club request")]
    assert mails[0]["link"].endswith(f"/{w['event']}/clubes")
    # ...and it is listed for the administration too, with its event.
    row = next(r for r in await _requests(client, w["admin"]) if r["club"]["id"] == w["clubs"]["d"]["id"])
    assert row["source"] == "event" and row["event"]["id"] == w["event"]


async def test_a_notice_never_fails_the_request(client, factory, w, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("mail provider down")

    monkeypatch.setattr(email_service, "send_admin_request_email", broken)
    email = factory.email("req-f")
    assert (await client.post(REGISTER, json=_signup(email, w["clubs"]["f"]["id"]))).status_code == 201
    log = await fetch_one("SELECT ok FROM notification_log WHERE kind = 'ADMIN_DIRECTOR_REQUEST'"
                          " AND entity_id = :c", c=w["clubs"]["f"]["id"])
    assert log["ok"] is False


async def test_activity_is_scoped(client, w):
    response = await client.get(ACTIVITY, headers=w["admin"]["headers"])
    assert response.status_code == 200, response.text
    page = response.json()
    mine = {w["clubs"][key]["id"] for key in w["clubs"]}
    actions = {item["action"] for item in page["items"] if (item["organization"] or {}).get("id") in mine}
    assert {"CLUB_DIRECTOR_REQUEST", "CLUB_DIRECTOR_REQUEST_APPROVE", "CLUB_DIRECTOR_REQUEST_REJECT",
            "CLUB_DIRECTOR_ASSIGN", "EVENT_DIRECTOR_REQUEST"} <= actions
    request_row = next(item for item in page["items"] if item["action"] == "CLUB_DIRECTOR_REQUEST")
    assert request_row["actor"]["email"].endswith("@example.com")
    # The other association's administrator sees none of it.
    other = (await client.get(ACTIVITY, headers=w["other_admin"]["headers"])).json()
    assert not [item for item in other["items"] if (item["organization"] or {}).get("id") in mine]
    # A filter by action, paging, and nobody below the association.
    only = (await client.get(ACTIVITY, params={"action": "CLUB_DIRECTOR_REQUEST", "limit": 1},
                             headers=w["admin"]["headers"])).json()
    assert len(only["items"]) == 1 and only["has_more"] is True
    assert only["items"][0]["action"] == "CLUB_DIRECTOR_REQUEST"
    assert (await client.get(ACTIVITY, params={"action": "DROP_TABLE"}, headers=w["admin"]["headers"])).status_code == 422
    assert (await client.get(ACTIVITY, headers=w["coordinator"]["headers"])).status_code == 403
    assert (await client.get(ACTIVITY, headers=w["instructor"]["headers"])).status_code == 403
    rows = await fetch_all("SELECT action FROM audit_log WHERE action = 'CLUB_DIRECTOR_REQUEST'"
                           " AND entity_id = :c", c=w["clubs"]["a"]["id"])
    assert rows
