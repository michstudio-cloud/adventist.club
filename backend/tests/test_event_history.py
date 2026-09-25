"""Historia de puntos y marca por evento (spec docs/superpowers/specs/2026-09-24-eventos.md).

  * `GET /events/{id}/history`: «cada punto tiene historia», built from evaluations,
    evaluation_revisions and event_adjustments; newest first, cursor pagination.
    Coordination sees all (pending too); a judge only their activities (all when assigned to
    all); a director only their own club and never pending items; anonymous 401, others 403.
  * 027 branding: brand_logo_url / brand_color / brand_accent on the event, edited by the
    PATCH (audited, #RRGGBB validated), returned by EventOut and `/my`, copied by duplicate;
    the logo uploaded by coordination with the club-logo limits.
"""
import io
import re
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.services import storage
from tests.conftest import RUN, fetch_one, module_factory, requires_db

pytestmark = requires_db
factory = module_factory("event_history")
API = "/api/v1/events"
MEDIA = "https://media.test"


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


@pytest_asyncio.fixture(scope="module")
async def w(factory):
    assoc = await factory.org("assoc", "association")
    church = await factory.org("church", "church", assoc)
    club_a = await factory.org("club-a", "club", church)
    club_b = await factory.org("club-b", "club", church)
    other = await factory.org("assoc-2", "association")
    for club in (club_a, club_b):
        await _adventurers(club)
    world = {
        "assoc": assoc, "club_a": club_a, "club_b": club_b, "s": {},
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", assoc["id"]),
        "admin_other": await factory.user("admin-2", "ADMIN_ASSOCIATION", other["id"]),
        "coordinator": await factory.user("coordinator", "INSTRUCTOR"),
        "judge": await factory.user("judge", "STUDENT"),
        "judge_all": await factory.user("judge-all", "STUDENT"),
        "director_a": await factory.user("director-a", "CLUB_DIRECTOR", club_a["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", club_b["id"]),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    yield world
    ids = [uuid.UUID(assoc["id"]), uuid.UUID(other["id"])]
    await _exec("DELETE FROM audit_log WHERE entity_type = 'EVENT' AND entity_id IN"
                " (SELECT id::text FROM events WHERE organization_id = ANY(:ids))", ids=ids)
    await _exec("DELETE FROM events WHERE template_of_id IN (SELECT id FROM events WHERE organization_id = ANY(:ids))",
                ids=ids)
    await _exec("DELETE FROM events WHERE organization_id = ANY(:ids)", ids=ids)


def _body(w, **extra) -> dict:
    return {"organization_id": w["assoc"]["id"], "ministry": "adventurers",
            "name": f"{RUN} Historia", "starts_on": "2026-11-20", "ends_on": "2026-11-22", **extra}


async def _ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


# ----------------------------------------------------------------------------
# Historia
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def scored(client, w):
    """An event IN_PROGRESS with captures, corrections, a void and adjustments."""
    h = w["admin"]["headers"]
    event = await _ok(await client.post(API, json=_body(w), headers=h), 201)
    eid = event["id"]
    url = f"{API}/{eid}"
    a1 = await _ok(await client.post(f"{url}/activities", json={
        "name": "Señal", "kind": "participation", "max_points": 100, "config": {"max": 100}}, headers=h), 201)
    a2 = await _ok(await client.post(f"{url}/activities", json={
        "name": "Marcha", "kind": "participation", "max_points": 50, "config": {"max": 50}}, headers=h), 201)
    for body in ({"user_id": w["coordinator"]["id"], "role": "COORDINATOR"},
                 {"user_id": w["judge"]["id"], "role": "JUDGE", "activity_id": a1["id"]},
                 {"user_id": w["judge_all"]["id"], "role": "JUDGE"}):
        await _ok(await client.post(f"{url}/staff", json=body, headers=h), 201)
    reg_a = (await _ok(await client.post(f"{url}/registrations", json={"club_id": w["club_a"]["id"]},
                                         headers=h), 201))["id"]
    reg_b = (await _ok(await client.post(f"{url}/registrations", json={"club_id": w["club_b"]["id"]},
                                         headers=h), 201))["id"]
    for step in ("OPEN", "IN_PROGRESS"):
        await _ok(await client.post(f"{url}/status", json={"status": step}, headers=h))

    async def capture(who, reg, activity, points):
        return await _ok(await client.post(f"{url}/evaluations", json={
            "registration_id": reg, "activity_id": activity["id"], "inputs": {"points": points},
            "idempotency_key": f"k-{uuid.uuid4().hex}"}, headers=w[who]["headers"]), 201)

    e1 = await capture("judge", reg_a, a1, 80)
    await capture("judge_all", reg_a, a2, 40)
    e3 = await capture("judge_all", reg_b, a1, 70)
    await _ok(await client.patch(f"{url}/evaluations/{e1['id']}", json={
        "inputs": {"points": 90}, "reason": "Se contó mal", "expected_revision": 1},
        headers=w["judge"]["headers"]))
    await _ok(await client.patch(f"{url}/evaluations/{e1['id']}", json={
        "inputs": {"points": 95}, "reason": "Revisión de coordinación", "expected_revision": 2},
        headers=w["coordinator"]["headers"]))
    await _ok(await client.post(f"{url}/evaluations/{e3['id']}/void", json={"reason": "Club equivocado"},
                                headers=w["coordinator"]["headers"]))
    await _ok(await client.post(f"{url}/adjustments", json={
        "registration_id": reg_a, "kind": "BONUS", "points": 10, "activity_id": a2["id"],
        "reason": "Uniforme impecable"}, headers=w["coordinator"]["headers"]), 201)
    penalty = await _ok(await client.post(f"{url}/adjustments", json={
        "registration_id": reg_b, "kind": "PENALTY", "points": 5, "reason": "Área sucia"},
        headers=w["coordinator"]["headers"]), 201)
    await _ok(await client.post(f"{url}/adjustments/{penalty['id']}/void", json={"reason": "Se limpió a tiempo"},
                                headers=w["coordinator"]["headers"]))
    # A legacy adjustment still awaiting approval (the API no longer creates them).
    await _exec("INSERT INTO event_adjustments (registration_id, kind, points, reason, created_by_id)"
                " VALUES (:r, 'BONUS', 3, 'Propuesta', :u)",
                r=uuid.UUID(reg_a), u=uuid.UUID(w["judge"]["id"]))
    return {"id": eid, "url": f"{url}/history", "a1": a1["id"], "a2": a2["id"], "reg_a": reg_a,
            "reg_b": reg_b, "e1": e1["id"], "e3": e3["id"]}


async def _history(client, scored, who, w, **params):
    return await client.get(scored["url"], params=params, headers=w[who]["headers"])


async def test_history_permission_matrix(client, w, scored):
    assert (await client.get(scored["url"])).status_code == 401
    for who in ("stranger", "admin_other"):
        assert (await _history(client, scored, who, w)).status_code == 403, who
    for who in ("admin", "coordinator", "judge", "judge_all", "director_a", "director_b"):
        assert (await _history(client, scored, who, w)).status_code == 200, who
    missing = await client.get(f"{API}/{uuid.uuid4()}/history", headers=w["admin"]["headers"])
    assert missing.status_code == 404


async def test_coordination_sees_every_point_newest_first(client, w, scored):
    page = await _ok(await _history(client, scored, "coordinator", w))
    items = page["items"]
    assert page["next_cursor"] is None
    assert [i["type"] for i in items] == [
        "ADJUSTMENT_APPLIED",      # the pending legacy one, last inserted
        "ADJUSTMENT_VOIDED", "ADJUSTMENT_APPLIED", "ADJUSTMENT_APPLIED",
        "EVALUATION_VOIDED", "EVALUATION_CORRECTED", "EVALUATION_CORRECTED",
        "EVALUATION_CREATED", "EVALUATION_CREATED", "EVALUATION_CREATED",
    ]
    stamps = [i["at"] for i in items]
    assert stamps == sorted(stamps, reverse=True)
    assert len({i["id"] for i in items}) == len(items)
    pending = items[0]
    assert pending["pending"] is True and pending["points"] == 3 and pending["kind"] == "BONUS"
    assert all(i["pending"] is False for i in items[1:])

    by_type = {}
    for item in items:
        by_type.setdefault(item["type"], []).append(item)
    coord_fix, judge_fix = by_type["EVALUATION_CORRECTED"]
    assert judge_fix["previous_points"] == 80 and judge_fix["points"] == 90
    assert judge_fix["actor"]["as"] == "JUDGE" and set(judge_fix["actor"]) == {"name", "as"}
    assert judge_fix["actor"]["name"].endswith(" judge") and judge_fix["reason"] == "Se contó mal"
    assert coord_fix["previous_points"] == 90 and coord_fix["points"] == 95
    assert coord_fix["actor"]["as"] == "COORDINATION" and coord_fix["actor"]["name"].endswith(" coordinator")
    assert coord_fix["activity"] == {"id": scored["a1"], "name": "Señal"}
    assert coord_fix["registration"]["id"] == scored["reg_a"]
    assert coord_fix["registration"]["club_name"].endswith("club-a")
    # The capture keeps the points it was captured with, not the corrected ones.
    first = [i for i in by_type["EVALUATION_CREATED"] if i["id"] == f"ev:{scored['e1']}"][0]
    assert first["points"] == 80 and first["previous_points"] is None and first["kind"] is None
    assert first["actor"]["as"] == "JUDGE" and first["actor"]["name"].endswith(" judge") and first["reason"] is None
    [voided] = by_type["EVALUATION_VOIDED"]
    assert voided["points"] == 70 and voided["actor"]["as"] == "COORDINATION"
    assert voided["reason"] == "Club equivocado" and voided["registration"]["id"] == scored["reg_b"]
    [void_adj] = by_type["ADJUSTMENT_VOIDED"]
    assert void_adj["kind"] == "PENALTY" and void_adj["points"] == 5 and void_adj["reason"] == "Se limpió a tiempo"
    assert void_adj["activity"] is None
    bonus = [i for i in by_type["ADJUSTMENT_APPLIED"] if i["reason"] == "Uniforme impecable"][0]
    assert bonus["activity"]["id"] == scored["a2"] and bonus["actor"]["as"] == "COORDINATION"
    assert set(bonus) == {"id", "at", "type", "registration", "activity", "points", "previous_points",
                          "kind", "actor", "reason", "pending"}

    only_b = await _ok(await _history(client, scored, "admin", w, registration_id=scored["reg_b"]))
    assert {i["registration"]["id"] for i in only_b["items"]} == {scored["reg_b"]}
    assert len(only_b["items"]) == 4


async def test_history_pagination(client, w, scored):
    full = (await _ok(await _history(client, scored, "coordinator", w)))["items"]
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 3, **({"before": cursor} if cursor else {})}
        page = await _ok(await _history(client, scored, "coordinator", w, **params))
        assert len(page["items"]) <= 3
        seen += [i["id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [i["id"] for i in full] and pages == 4
    exact = await _ok(await _history(client, scored, "coordinator", w, limit=len(full)))
    assert exact["next_cursor"] is None
    for bad in ({"limit": 0}, {"limit": 201}, {"before": "not-a-cursor"}):
        assert (await _history(client, scored, "coordinator", w, **bad)).status_code == 422, bad


async def test_judges_see_their_activities(client, w, scored):
    judge = (await _ok(await _history(client, scored, "judge", w)))["items"]
    assert judge and {i["activity"]["id"] for i in judge} == {scored["a1"]}
    assert sorted(i["type"] for i in judge) == sorted([
        "EVALUATION_CREATED", "EVALUATION_CREATED", "EVALUATION_CORRECTED", "EVALUATION_CORRECTED",
        "EVALUATION_VOIDED"])
    # Assigned to all: everything but what is still pending.
    everyone = (await _ok(await _history(client, scored, "judge_all", w)))["items"]
    assert len(everyone) == 9 and not any(i["pending"] for i in everyone)
    # A judge may narrow to any club (they judge every club of their activities).
    narrowed = await _ok(await _history(client, scored, "judge", w, registration_id=scored["reg_b"]))
    assert {i["registration"]["id"] for i in narrowed["items"]} == {scored["reg_b"]}


async def test_directors_see_only_their_club(client, w, scored):
    mine = (await _ok(await _history(client, scored, "director_a", w)))["items"]
    assert {i["registration"]["id"] for i in mine} == {scored["reg_a"]}
    assert not any(i["pending"] for i in mine)
    assert sorted(i["type"] for i in mine) == sorted([
        "EVALUATION_CREATED", "EVALUATION_CREATED", "EVALUATION_CORRECTED", "EVALUATION_CORRECTED",
        "ADJUSTMENT_APPLIED"])
    explicit = (await _ok(await _history(client, scored, "director_a", w, registration_id=scored["reg_a"])))["items"]
    assert [i["id"] for i in explicit] == [i["id"] for i in mine]
    other = await _history(client, scored, "director_a", w, registration_id=scored["reg_b"])
    assert other.status_code == 403
    theirs = (await _ok(await _history(client, scored, "director_b", w)))["items"]
    assert {i["registration"]["id"] for i in theirs} == {scored["reg_b"]}
    assert sorted(i["type"] for i in theirs) == sorted([
        "EVALUATION_CREATED", "EVALUATION_VOIDED", "ADJUSTMENT_APPLIED", "ADJUSTMENT_VOIDED"])
    # Paging a director never leaks another club.
    page = await _ok(await _history(client, scored, "director_a", w, limit=2))
    rest = await _ok(await _history(client, scored, "director_a", w, limit=50, before=page["next_cursor"]))
    assert [i["id"] for i in page["items"] + rest["items"]] == [i["id"] for i in mine]


# ----------------------------------------------------------------------------
# Marca por evento (027)
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


def _image(size: int, fmt: str = "WEBP", colour=(47, 125, 225)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buffer, format=fmt)
    return buffer.getvalue()


async def _upload(client, event_id, user, data, content_type="image/webp"):
    return await client.post(f"{API}/{event_id}/brand-logo",
                             files={"file": ("logo.webp", data, content_type)}, headers=user["headers"])


@pytest_asyncio.fixture(scope="module")
async def branded(client, w):
    event = await _ok(await client.post(API, json=_body(w, name=f"{RUN} Marca"), headers=w["admin"]["headers"]), 201)
    assert event["brand_logo_url"] is None and event["brand_color"] is None and event["brand_accent"] is None
    await _ok(await client.post(f"{API}/{event['id']}/staff", json={"user_id": w["coordinator"]["id"],
                                                                    "role": "COORDINATOR"},
                                headers=w["admin"]["headers"]), 201)
    await _ok(await client.post(f"{API}/{event['id']}/staff", json={"user_id": w["judge"]["id"], "role": "JUDGE"},
                                headers=w["admin"]["headers"]), 201)
    return event["id"]


async def test_brand_colours_are_validated_and_audited(client, w, branded):
    url = f"{API}/{branded}"
    h = w["coordinator"]["headers"]
    for bad in ("red", "#fff", "#12345G", "1a2b3c", "#1a2b3c0"):
        assert (await client.patch(url, json={"brand_color": bad}, headers=h)).status_code == 422, bad
    assert (await client.patch(url, json={"brand_accent": "rgb(1,2,3)"}, headers=h)).status_code == 422
    assert (await client.patch(url, json={"brand_color": "#1a2b3c"},
                               headers=w["judge"]["headers"])).status_code == 403
    updated = await _ok(await client.patch(url, json={"brand_color": "#1a2b3c", "brand_accent": "#FFCC00"}, headers=h))
    assert updated["brand_color"] == "#1A2B3C" and updated["brand_accent"] == "#FFCC00"
    audit = await fetch_one("SELECT metadata_json FROM audit_log WHERE action = 'EVENT_UPDATE' AND entity_id = :id"
                            " ORDER BY created_at DESC LIMIT 1", id=branded)
    assert audit["metadata_json"]["brand_color"] == {"from": None, "to": "#1A2B3C"}
    assert audit["metadata_json"]["brand_accent"] == {"from": None, "to": "#FFCC00"}
    assert set(audit["metadata_json"]["fields"]) == {"brand_color", "brand_accent"}
    # /my and the list carry the brand to every role.
    my = await _ok(await client.get(f"{url}/my", headers=w["judge"]["headers"]))
    assert my["event"]["brand_color"] == "#1A2B3C" and my["event"]["brand_accent"] == "#FFCC00"
    listed = await _ok(await client.get(API, headers=w["judge"]["headers"]))
    assert [e["brand_color"] for e in listed if e["id"] == branded] == ["#1A2B3C"]
    # A foreign logo URL is refused; null clears a colour.
    foreign = await client.patch(url, json={"brand_logo_url": "https://evil.example/logo.png"}, headers=h)
    assert foreign.status_code == 422 and foreign.json()["detail"] == "event_logo_not_platform"
    cleared = await _ok(await client.patch(url, json={"brand_accent": None}, headers=h))
    assert cleared["brand_accent"] is None and cleared["brand_color"] == "#1A2B3C"
    await _ok(await client.patch(url, json={"brand_accent": "#FFCC00"}, headers=h))


async def test_brand_logo_upload_by_coordination_only(client, w, branded, r2):
    webp = _image(512)
    for who in ("judge", "director_a", "stranger", "admin_other"):
        refused = await _upload(client, branded, w[who], webp)
        assert refused.status_code == 403, (who, refused.text)
    assert r2.objects == []
    assert (await _upload(client, branded, w["coordinator"], _image(600))).json()["detail"] == "event_logo_too_big"
    svg = await _upload(client, branded, w["coordinator"], b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")
    assert svg.status_code == 415 and svg.json()["detail"] == "event_logo_images_only"
    lying = await _upload(client, branded, w["coordinator"], b"RIFF0000WEBPnot-an-image")
    assert lying.status_code == 415
    assert r2.objects == []

    uploaded = await _upload(client, branded, w["coordinator"], webp)
    assert uploaded.status_code == 201, uploaded.text
    logo = uploaded.json()["brand_logo_url"]
    assert re.fullmatch(rf"{MEDIA}/events/{branded}/brand-[0-9a-f]{{16}}\.webp", logo)
    assert r2.objects[-1]["Key"] == logo[len(MEDIA) + 1:] and r2.objects[-1]["ContentType"] == "image/webp"
    audit = await fetch_one("SELECT user_id, metadata_json FROM audit_log WHERE action = 'EVENT_BRAND_LOGO_UPDATE'"
                            " AND entity_id = :id ORDER BY created_at DESC LIMIT 1", id=branded)
    assert str(audit["user_id"]) == w["coordinator"]["id"] and audit["metadata_json"]["previous"] is None
    my = await _ok(await client.get(f"{API}/{branded}/my", headers=w["judge"]["headers"]))
    assert my["event"]["brand_logo_url"] == logo
    # The PATCH accepts our own bucket (the logo library) and keeps the uploaded file of any
    # other event; the admin replaces it with a PNG and the old file goes.
    library = f"{MEDIA}/logos/camporee.png"
    assert (await _ok(await client.patch(f"{API}/{branded}", json={"brand_logo_url": library},
                                         headers=w["admin"]["headers"])))["brand_logo_url"] == library
    assert logo[len(MEDIA) + 1:] in r2.deleted
    png = await _upload(client, branded, w["admin"], _image(256, "PNG"), "image/png")
    assert png.status_code == 201 and png.json()["brand_logo_url"].endswith(".png")
    assert "logos/camporee.png" not in r2.deleted  # never deletes outside events/


async def test_duplicate_copies_the_brand_and_shares_the_file(client, w, branded, r2):
    source = await _ok(await client.get(f"{API}/{branded}", headers=w["admin"]["headers"]))
    copy = await _ok(await client.post(f"{API}/{branded}/duplicate", json={
        "name": f"{RUN} Marca copia", "starts_on": "2027-11-20", "ends_on": "2027-11-22"},
        headers=w["admin"]["headers"]), 201)
    for key in ("brand_logo_url", "brand_color", "brand_accent"):
        assert copy[key] == source[key] and source[key] is not None, key
    shared = source["brand_logo_url"][len(MEDIA) + 1:]
    # The source replaces its logo: the copy still points at the old file, so it stays.
    replaced = await _upload(client, branded, w["coordinator"], _image(128, colour=(1, 2, 3)))
    assert replaced.status_code == 201 and shared not in r2.deleted
    # The copy drops it: nobody points at it any more, now it goes.
    removed = await _ok(await client.delete(f"{API}/{copy['id']}/brand-logo", headers=w["admin"]["headers"]))
    assert removed["brand_logo_url"] is None and shared in r2.deleted
    assert (await client.delete(f"{API}/{copy['id']}/brand-logo",
                                headers=w["judge"]["headers"])).status_code == 403


async def test_brand_logo_needs_storage(client, w, branded):
    assert (await _upload(client, branded, w["coordinator"], _image(64))).status_code == 503


async def test_migration_027_is_idempotent():
    import pathlib

    sql = (pathlib.Path(__file__).resolve().parents[1] / "migrations" / "027_event_branding.sql").read_text()
    async with SessionLocal() as db:
        raw = await (await db.connection()).get_raw_connection()
        for _ in range(2):  # simple-query protocol: the whole file, DO block included
            await raw.driver_connection.execute(sql)
        await db.commit()
        columns = {row[0]: row[1:] for row in (await db.execute(text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable"
            " FROM information_schema.columns WHERE table_name = 'events' AND column_name LIKE 'brand_%'"))).all()}
    assert columns == {"brand_logo_url": ("text", None, "YES"),
                       "brand_color": ("character varying", 7, "YES"),
                       "brand_accent": ("character varying", 7, "YES")}
    with pytest.raises(Exception, match="events_brand_color_ck"):
        await _exec("UPDATE events SET brand_color = 'red' WHERE id = (SELECT id FROM events LIMIT 1)")
