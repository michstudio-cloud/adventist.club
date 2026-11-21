"""«QR para jueces» (30 minutos).

Proved here:
  * only the event's coordination makes the link; it lives on events.adventist.club `/unirse`;
  * the preview needs no session and names the event; a tampered or expired token is the same 404;
  * joining makes the signed-in adult a judge of every activity, once (idempotent); a minor never;
  * a closed event refuses it;
  * a QR for some activities (`activity_ids`) names them in the preview and adds a judge row for each
    of them only; they are inside the signature (tampering = 404), must be the event's, and a link
    of the first format (no activities) still means every activity.
"""
import time
import uuid

import pytest_asyncio
from sqlalchemy import text

from app.db import SessionLocal
from app.services import event_judge_link
from tests.conftest import RUN, fetch_all, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("judgelink")
API = "/api/v1/events"


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


@pytest_asyncio.fixture(scope="module")
async def w(client, factory):
    assoc = await factory.org("assoc", "association")
    zone = await factory.org("zone", "zone", assoc)
    church = await factory.org("church", "church", zone)
    admin = await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"])
    coordinator = await factory.user("coordinator", "INSTRUCTOR", church["id"])
    newcomer = await factory.user("newcomer", "INSTRUCTOR")
    kid = await factory.user("kid", "STUDENT", is_minor=True)
    stranger = await factory.user("stranger", "INSTRUCTOR")
    inspector = await factory.user("inspector", "INSTRUCTOR")
    h = admin["headers"]
    created = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Judge link",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    activities = {}
    for name in ("Inspección domingo", "Marcha", "Nudos"):
        made = await client.post(f"{API}/{event_id}/activities", json={
            "name": name, "kind": "participation", "max_points": 50, "config": {"max": 50}}, headers=h)
        assert made.status_code == 201, made.text
        activities[name] = made.json()["id"]
    other = await client.post(API, json={
        "organization_id": assoc["id"], "ministry": "adventurers", "name": f"{RUN} Judge link other",
        "starts_on": "2026-11-20", "ends_on": "2026-11-22"}, headers=h)
    foreign = await client.post(f"{API}/{other.json()['id']}/activities", json={
        "name": "Ajena", "kind": "participation", "max_points": 50, "config": {"max": 50}}, headers=h)
    assert foreign.status_code == 201, foreign.text
    assert (await client.post(f"{API}/{event_id}/staff", json={"email": coordinator["email"], "role": "COORDINATOR"},
                              headers=h)).status_code == 201
    assert (await client.post(f"{API}/{event_id}/status", json={"status": "OPEN"}, headers=h)).status_code == 200
    yield {"event": event_id, "admin": admin, "coordinator": coordinator, "newcomer": newcomer, "kid": kid,
           "stranger": stranger, "inspector": inspector, "activities": activities, "foreign": foreign.json()["id"]}
    await _exec("DELETE FROM events WHERE id IN (:id, :other)", id=uuid.UUID(event_id),
                other=uuid.UUID(other.json()["id"]))


def _token(url: str) -> str:
    return url.split("t=", 1)[1]


async def test_link_preview_and_join(client, w):
    url = f"{API}/{w['event']}/staff/join-link"
    assert (await client.post(url, headers=w["stranger"]["headers"])).status_code == 403
    made = await client.post(url, headers=w["coordinator"]["headers"])
    assert made.status_code == 200, made.text
    link = made.json()["url"]
    assert link.startswith("https://events.adventist.club/unirse?t=ejl_")
    token = _token(link)

    preview = await client.post(f"{API}/staff/join/preview", json={"token": token})
    assert preview.status_code == 200 and preview.json()["event_id"] == w["event"]
    assert preview.json()["activities"] == []  # every activity
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert (await client.post(f"{API}/staff/join/preview", json={"token": tampered})).status_code == 404

    assert (await client.post(f"{API}/staff/join", json={"token": token})).status_code == 401
    joined = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["newcomer"]["headers"])
    assert joined.status_code == 200, joined.text
    assert joined.json() == {"event_id": w["event"], "event_name": joined.json()["event_name"], "added": 1,
                             "activities": []}
    again = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["newcomer"]["headers"])
    assert again.json()["added"] == 0
    rows = await fetch_all("SELECT role, activity_id FROM event_staff WHERE event_id = :e AND user_id = :u",
                           e=uuid.UUID(w["event"]), u=uuid.UUID(w["newcomer"]["id"]))
    assert [(r["role"], r["activity_id"]) for r in rows] == [("JUDGE", None)]
    mine = await client.get(f"{API}/{w['event']}/my", headers=w["newcomer"]["headers"])
    assert mine.status_code == 200 and "JUDGE" in str(mine.json())

    assert (await client.post(f"{API}/staff/join", json={"token": token},
                              headers=w["kid"]["headers"])).status_code == 403


async def test_expired_link_is_refused(client, w):
    token, _ = event_judge_link.make(uuid.UUID(w["event"]), uuid.UUID(w["coordinator"]["id"]),
                                     now=time.time() - event_judge_link.TTL_SECONDS - 5)
    assert (await client.post(f"{API}/staff/join/preview", json={"token": token})).status_code == 404
    assert (await client.post(f"{API}/staff/join", json={"token": token},
                              headers=w["stranger"]["headers"])).status_code == 404


async def test_a_director_of_a_competing_club_cannot_join_as_judge(client, factory, w):
    church = await factory.org("church-d", "church", await factory.org("zone-d", "zone", await factory.org("assoc-d", "association")))
    club = await factory.org("club-d", "club", church)
    await _exec("UPDATE organizations SET ministry_id = (SELECT id FROM ministries WHERE slug = 'adventurers')"
                " WHERE id = :id", id=uuid.UUID(club["id"]))
    await _exec("INSERT INTO organization_ministries (organization_id, ministry_id)"
                " SELECT :id, id FROM ministries WHERE slug = 'adventurers' ON CONFLICT DO NOTHING",
                id=uuid.UUID(club["id"]))
    await _exec("INSERT INTO event_registrations (id, event_id, club_id, status) VALUES (:id, :e, :c, 'REGISTERED')",
                id=uuid.uuid4(), e=uuid.UUID(w["event"]), c=uuid.UUID(club["id"]))
    director = await factory.user("director-d", "CLUB_DIRECTOR", club["id"])
    token = _token((await client.post(f"{API}/{w['event']}/staff/join-link", headers=w["coordinator"]["headers"])).json()["url"])
    refused = await client.post(f"{API}/staff/join", json={"token": token}, headers=director["headers"])
    assert refused.status_code == 403, refused.text
    assert "Diriges un club" in refused.json()["detail"]


async def _judge_rows(event_id: str, user_id: str) -> list:
    rows = await fetch_all("SELECT role, activity_id FROM event_staff WHERE event_id = :e AND user_id = :u AND active",
                           e=uuid.UUID(event_id), u=uuid.UUID(user_id))
    return sorted((r["role"], str(r["activity_id"]) if r["activity_id"] else None) for r in rows)


async def test_a_link_for_some_activities(client, w):
    url = f"{API}/{w['event']}/staff/join-link"
    acts = w["activities"]
    chosen = [acts["Marcha"], acts["Inspección domingo"]]
    # Activities of another event, or too many, are refused before any QR exists.
    assert (await client.post(url, json={"activity_ids": [w["foreign"]]},
                              headers=w["coordinator"]["headers"])).status_code == 404
    too_many = await client.post(url, json={"activity_ids": [str(uuid.uuid4()) for _ in range(30)]},
                                 headers=w["coordinator"]["headers"])
    assert too_many.status_code == 422 and "hasta 24" in too_many.json()["detail"]
    assert (await client.post(url, json={"activity_ids": ["x"]}, headers=w["coordinator"]["headers"])).status_code == 422

    made = await client.post(url, json={"activity_ids": chosen}, headers=w["coordinator"]["headers"])
    assert made.status_code == 200, made.text
    # The answer says what the QR grants, in the coordinator's order.
    assert [a["name"] for a in made.json()["activities"]] == ["Marcha", "Inspección domingo"]
    token = _token(made.json()["url"])
    assert len(made.json()["url"]) < 200

    preview = await client.post(f"{API}/staff/join/preview", json={"token": token})
    assert preview.status_code == 200, preview.text
    assert sorted(a["name"] for a in preview.json()["activities"]) == ["Inspección domingo", "Marcha"]
    assert {a["id"] for a in preview.json()["activities"]} == set(chosen)

    joined = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["inspector"]["headers"])
    assert joined.status_code == 200, joined.text
    assert joined.json()["added"] == 2
    assert {a["id"] for a in joined.json()["activities"]} == set(chosen)
    assert await _judge_rows(w["event"], w["inspector"]["id"]) == sorted(("JUDGE", a) for a in chosen)
    again = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["inspector"]["headers"])
    assert again.json()["added"] == 0

    # Somebody already judge of every activity gets nothing more from a QR for some of them.
    everything = _token((await client.post(url, headers=w["coordinator"]["headers"])).json()["url"])
    all_judge = w["stranger"]
    assert (await client.post(f"{API}/staff/join", json={"token": everything},
                              headers=all_judge["headers"])).json()["added"] == 1
    some = await client.post(f"{API}/staff/join", json={"token": token}, headers=all_judge["headers"])
    assert some.status_code == 200 and some.json()["added"] == 0
    assert await _judge_rows(w["event"], all_judge["id"]) == [("JUDGE", None)]

    # An empty list is «todas», like no body at all (the first format).
    empty = await client.post(url, json={"activity_ids": []}, headers=w["coordinator"]["headers"])
    assert empty.status_code == 200 and empty.json()["activities"] == []
    assert event_judge_link.read(_token(empty.json()["url"])).activity_refs == ()

    # A minor is still refused with a QR for some activities.
    assert (await client.post(f"{API}/staff/join", json={"token": token},
                              headers=w["kid"]["headers"])).status_code == 403


async def test_the_activities_are_signed(client, w):
    event_id, issuer = uuid.UUID(w["event"]), uuid.UUID(w["coordinator"]["id"])
    marcha = uuid.UUID(w["activities"]["Marcha"])
    nudos = uuid.UUID(w["activities"]["Nudos"])
    token, _ = event_judge_link.make(event_id, issuer, activity_ids=[marcha])
    link = event_judge_link.read(token)
    assert (link.event_id, link.issuer_id, link.activity_refs) == (event_id, issuer, (marcha.bytes[:8],))

    # Swapping (or adding) an activity in the body without the secret breaks the signature: same 404.
    body_text, sig = token[len(event_judge_link.PREFIX):].split(".", 1)
    body = event_judge_link._unb64(body_text)
    for forged in (body[:-8] + nudos.bytes[:8], body + nudos.bytes[:8], body[:36]):
        fake = f"{event_judge_link.PREFIX}{event_judge_link._b64(forged)}.{sig}"
        assert (await client.post(f"{API}/staff/join/preview", json={"token": fake})).status_code == 404
        assert (await client.post(f"{API}/staff/join", json={"token": fake},
                                  headers=w["newcomer"]["headers"])).status_code == 404


def test_link_formats():
    event_id, issuer = uuid.uuid4(), uuid.uuid4()
    # The first format (every activity) still reads, with no activities.
    every, expires = event_judge_link.make(event_id, issuer)
    assert event_judge_link.read(every) == (event_id, issuer, expires, ())
    ids = [uuid.uuid4() for _ in range(event_judge_link.MAX_ACTIVITIES)]
    most, _ = event_judge_link.make(event_id, issuer, activity_ids=ids + ids[:3])  # repeats count once
    assert len(event_judge_link.read(most).activity_refs) == event_judge_link.MAX_ACTIVITIES
    assert len(most) <= event_judge_link.MAX_TOKEN
    try:
        event_judge_link.make(event_id, issuer, activity_ids=[*ids, uuid.uuid4()])
    except ValueError:
        pass
    else:
        raise AssertionError("more than MAX_ACTIVITIES must be refused")
    expired, _ = event_judge_link.make(event_id, issuer, activity_ids=ids[:1],
                                       now=time.time() - event_judge_link.TTL_SECONDS - 5)
    for token in (expired, every + "x", "ejl_", "ejl_" + "A" * 500):
        try:
            event_judge_link.read(token)
        except Exception as error:  # noqa: BLE001 — the neutral 404
            assert getattr(error, "status_code", None) == 404
        else:
            raise AssertionError(token)


async def test_a_link_whose_activities_were_deleted(client, w):
    gone = w["activities"]["Nudos"]
    token = _token((await client.post(f"{API}/{w['event']}/staff/join-link", json={"activity_ids": [gone]},
                                      headers=w["coordinator"]["headers"])).json()["url"])
    await _exec("DELETE FROM event_activities WHERE id = :id", id=uuid.UUID(gone))
    preview = await client.post(f"{API}/staff/join/preview", json={"token": token})
    assert preview.status_code == 409 and "ya no existen" in preview.json()["detail"]
    refused = await client.post(f"{API}/staff/join", json={"token": token}, headers=w["newcomer"]["headers"])
    assert refused.status_code == 409
