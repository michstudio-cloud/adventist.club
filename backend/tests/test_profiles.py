"""Bloque G — Perfil público, handle, XP derivado, insignias y barra de buena conducta.

Spec: docs/superpowers/specs/2026-09-23-perfil-publico.md.

The rules that must never break:
  * a minor's profile is never open to the internet, whatever its `profile_visibility`;
  * whoever may not see a profile gets a 404, never a 403 (existence is not revealed);
  * a profile never carries an e-mail, a birth date or a phone;
  * a public viewer sees the level, never the XP number;
  * the director's points are capped at 100 positive points per member and week.
"""

import re
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, text

from app.config import settings
from app.db import SessionLocal, engine
from app.services import profiles as profile_service
from app.services import storage
from app.services import xp
from app.security import utcnow
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db


def _tables_exist() -> bool:
    import asyncio

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.xp_awards') IS NOT NULL"
                    " AND to_regclass('public.programs') IS NOT NULL"
                    " AND to_regclass('public.activity_logs') IS NOT NULL"
                    " AND to_regclass('public.club_units') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _tables_exist(), reason="apply migrations/014_profiles.sql to the test database"
    ),
]
factory = module_factory("profiles")

PROFILES = "/api/v1/profiles"
MY_PROFILE = "/api/v1/users/me/profile"
MEDIA = settings.R2_PUBLIC_URL.rstrip("/")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


# ----------------------------------------------------------------------------
# Helpers that write rows directly
# ----------------------------------------------------------------------------
async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    await _exec(f"UPDATE users SET {assignments} WHERE id = :id", id=uuid.UUID(user["id"]), **columns)


async def _membership(user: dict, club: dict, *, role="STUDENT", status="ACTIVE", unit_id=None) -> str:
    membership_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, unit_id,"
        " started_at) VALUES (:id, :user, :club, :role, :status, 'ADMIN', :unit, now())",
        id=membership_id, user=uuid.UUID(user["id"]), club=uuid.UUID(club["id"]), role=role,
        status=status, unit=unit_id,
    )
    return str(membership_id)


async def _guardianship(guardian: dict, child: dict, consent="APPROVED") -> None:
    await _exec(
        "INSERT INTO guardianships (id, guardian_id, child_id, relationship, consent_status)"
        " VALUES (gen_random_uuid(), :g, :c, 'PARENT', :consent)",
        g=uuid.UUID(guardian["id"]), c=uuid.UUID(child["id"]), consent=consent,
    )


async def _profile(client, target: dict | str, viewer: dict | None = None):
    key = target if isinstance(target, str) else target["id"]
    return await client.get(f"{PROFILES}/{key}", headers=viewer["headers"] if viewer else None)


async def _award(client, actor: dict, club: dict, membership_id: str, **body):
    body.setdefault("category", "conducta")
    body.setdefault("points", 10)
    if isinstance(body.get("occurred_on"), date):
        body["occurred_on"] = body["occurred_on"].isoformat()
    return await client.post(
        f"/api/v1/clubs/{club['id']}/members/{membership_id}/xp", json=body, headers=actor["headers"]
    )


# ----------------------------------------------------------------------------
# The world
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("club-b", "club", association)
    other_assoc = await factory.org("assoc-b", "association")
    await _exec("UPDATE organizations SET city = 'Ciudad Club' WHERE id = :id", id=uuid.UUID(club["id"]))
    p = {
        "public": await factory.user("public", "STUDENT", club["id"]),
        "clubvis": await factory.user("clubvis", "STUDENT", club["id"]),
        "private": await factory.user("private", "STUDENT", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "nobd": await factory.user("nobd", "STUDENT", club["id"]),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "secretary": await factory.user("secretary", "CLUB_SECRETARY", club["id"]),
        "counselor": await factory.user("counselor", "COUNSELOR", club["id"]),
        "mate": await factory.user("mate", "STUDENT", club["id"]),
        "outsider": await factory.user("outsider", "STUDENT", other_club["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", other_club["id"]),
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", association["id"]),
        "admin_b": await factory.user("admin-b", "ADMIN_ASSOCIATION", other_assoc["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    await _set(p["public"], profile_visibility="public", bio="Hola, soy público",
               birth_date=date(1990, 5, 17))
    await _set(p["clubvis"], profile_visibility="club")
    # A minor who (wrongly) says public, and an account without birth date but with a guardian.
    await _set(p["minor"], profile_visibility="public", avatar_url=f"{MEDIA}/avatars/kid.png")
    await _set(p["nobd"], profile_visibility="public")
    await _guardianship(p["guardian"], p["minor"])
    await _guardianship(p["guardian"], p["nobd"])
    unit_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_units (id, club_id, name, counselor_id) VALUES (:id, :club, :name, :c)",
        id=unit_id, club=uuid.UUID(club["id"]), name=factory.name("unidad"),
        c=uuid.UUID(p["counselor"]["id"]),
    )
    memberships = {
        "minor": await _membership(p["minor"], club, unit_id=unit_id),
        "mate": await _membership(p["mate"], club),
        "public": await _membership(p["public"], club),
        "director": await _membership(p["director"], club, role="CLUB_DIRECTOR"),
        "outsider": await _membership(p["outsider"], other_club),
    }
    return {**p, "club": club, "other_club": other_club, "association": association,
            "unit_id": str(unit_id), "memberships": memberships}


# ----------------------------------------------------------------------------
# Handle: the migration's trigger and the rules
# ----------------------------------------------------------------------------
async def test_every_new_account_gets_a_handle_from_its_email(factory):
    first = await factory.user("Handle.Me")
    second = await factory.user("handle.me-2")
    rows = await fetch_all(
        "SELECT handle FROM users WHERE id IN (:a, :b)", a=uuid.UUID(first["id"]), b=uuid.UUID(second["id"])
    )
    handles = {row["handle"] for row in rows}
    assert len(handles) == 2
    for handle in handles:
        assert re.fullmatch(r"[a-z0-9_.]{3,32}", handle), handle
    reserved = await factory.user("admin-x")
    row = await fetch_one("SELECT handle FROM users WHERE id = :id", id=uuid.UUID(reserved["id"]))
    assert profile_service.handle_problem(row["handle"]) in (None, "handle_reserved")


@pytest.mark.parametrize(
    "handle, problem",
    [
        ("ana.perez", None),
        ("ana_99", None),
        ("ab", "handle_invalid"),
        ("a" * 33, "handle_invalid"),
        ("Ana", "handle_invalid"),      # the API lower-cases BEFORE validating
        ("ana-perez", "handle_invalid"),
        ("ana perez", "handle_invalid"),
        ("admin", "handle_reserved"),
        ("ad.min", "handle_reserved"),
        ("master_gc", "handle_reserved"),
        ("adventist.club", "handle_reserved"),
        ("conquistadores", "handle_reserved"),
        ("director", "handle_reserved"),
        ("me", "handle_invalid"),
    ],
)
def test_handle_rules(handle, problem):
    assert profile_service.handle_problem(handle) == problem


async def test_change_handle_taken_locked_and_audited(client, factory, world):
    me = await factory.user("handle-owner", "STUDENT")
    wanted = f"h{uuid.uuid4().hex[:10]}"
    response = await client.patch(MY_PROFILE, json={"handle": f"@{wanted.upper()}"}, headers=me["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handle"] == wanted and body["handle_locked_until"] is not None

    # Same handle again: nothing changes, no lock error.
    same = await client.patch(MY_PROFILE, json={"handle": wanted}, headers=me["headers"])
    assert same.status_code == 200
    # A different one within 30 days: locked.
    locked = await client.patch(MY_PROFILE, json={"handle": wanted + "x"}, headers=me["headers"])
    assert locked.status_code == 409 and locked.json()["detail"] == "handle_locked"
    await _set(me, handle_changed_at=utcnow() - timedelta(days=31))
    freed = await client.patch(MY_PROFILE, json={"handle": wanted + "x"}, headers=me["headers"])
    assert freed.status_code == 200 and freed.json()["handle"] == wanted + "x"

    other = await factory.user("handle-thief", "STUDENT")
    taken = await client.patch(MY_PROFILE, json={"handle": wanted + "x"}, headers=other["headers"])
    assert taken.status_code == 409 and taken.json()["detail"] == "handle_taken"
    reserved = await client.patch(MY_PROFILE, json={"handle": "Admin"}, headers=other["headers"])
    assert reserved.status_code == 422 and reserved.json()["detail"] == "handle_reserved"
    invalid = await client.patch(MY_PROFILE, json={"handle": "a b"}, headers=other["headers"])
    assert invalid.status_code == 422 and invalid.json()["detail"] == "handle_invalid"

    audits = await fetch_all(
        "SELECT action, metadata_json FROM audit_log WHERE entity_type = 'USER_PROFILE'"
        " AND entity_id = :id ORDER BY created_at",
        id=me["id"],
    )
    # The no-op (same handle) writes nothing.
    assert [a["action"] for a in audits] == ["UPDATE", "UPDATE"]
    assert audits[0]["metadata_json"]["handle"]["to"] == wanted

    # The handle finds the profile, whatever its case.
    await _set(me, profile_visibility="public")
    assert (await _profile(client, (wanted + "X").upper())).status_code == 200


# ----------------------------------------------------------------------------
# PATCH /users/me/profile
# ----------------------------------------------------------------------------
async def test_profile_patch_validations(client, factory, world):
    adult = await factory.user("patcher", "STUDENT", world["club"]["id"])
    h = adult["headers"]

    ok = await client.patch(MY_PROFILE, json={
        "name": "  Nombre Nuevo ", "bio": "  Me gustan los nudos  ",
        "avatar_url": f"{MEDIA}/avatars/abc.webp", "cover_url": f"{MEDIA}/covers/abc.jpg",
        "profile_visibility": "club",
    }, headers=h)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["name"] == "Nombre Nuevo" and body["bio"] == "Me gustan los nudos"
    assert body["avatar_url"] == f"{MEDIA}/avatars/abc.webp"
    assert body["cover_url"] == f"{MEDIA}/covers/abc.jpg"
    assert body["visibility"] == "club" and body["is_me"] and body["can_edit"]

    cases = [
        ({"bio": "x" * 281}, None),
        ({"avatar_url": "https://evil.example/avatars/a.png"}, "invalid_avatar_url"),
        ({"avatar_url": f"{MEDIA}/covers/a.png"}, "invalid_avatar_url"),
        ({"avatar_url": f"{MEDIA}/avatars/"}, "invalid_avatar_url"),
        ({"cover_url": f"{MEDIA}/avatars/a.png"}, "invalid_cover_url"),
        ({"cover_url": f"{MEDIA}/covers/../general/a.png"}, "invalid_cover_url"),
        ({"profile_visibility": "everyone"}, None),
        ({"name": "   "}, "name_required"),
        ({"handle": None}, "handle_required"),
        ({"email": "x@example.com"}, None),
    ]
    for payload, detail in cases:
        response = await client.patch(MY_PROFILE, json=payload, headers=h)
        assert response.status_code == 422, (payload, response.text)
        if detail:
            assert response.json()["detail"] == detail, payload
    assert (await client.patch(MY_PROFILE, json={}, headers=h)).status_code == 400

    # Clearing works.
    cleared = await client.patch(MY_PROFILE, json={"bio": None, "cover_url": None}, headers=h)
    assert cleared.json()["bio"] is None and cleared.json()["cover_url"] is None
    assert (await client.patch(MY_PROFILE, json={"bio": "x"})).status_code == 401


async def test_minor_cannot_be_public_nor_have_cover_nor_photo_without_guardian(client, factory, world):
    minor = await factory.user("minor-patch", "STUDENT", world["club"]["id"], is_minor=True)
    h = minor["headers"]
    public = await client.patch(MY_PROFILE, json={"profile_visibility": "public"}, headers=h)
    assert public.status_code == 422 and public.json()["detail"] == "minor_cannot_be_public"
    cover = await client.patch(MY_PROFILE, json={"cover_url": f"{MEDIA}/covers/a.png"}, headers=h)
    assert cover.status_code == 422 and cover.json()["detail"] == "minor_cannot_have_cover"
    photo = await client.patch(MY_PROFILE, json={"avatar_url": f"{MEDIA}/avatars/a.png"}, headers=h)
    assert photo.status_code == 422 and photo.json()["detail"] == "minor_avatar_not_allowed"
    # The old door is closed too.
    legacy = await client.patch(
        f"/api/v1/users/{minor['id']}", json={"avatar_url": f"{MEDIA}/avatars/a.png"}, headers=h
    )
    assert legacy.status_code == 422 and legacy.json()["detail"] == "minor_avatar_not_allowed"

    club_ok = await client.patch(MY_PROFILE, json={"profile_visibility": "club", "bio": "hola"}, headers=h)
    assert club_ok.status_code == 200 and club_ok.json()["is_minor"] is True

    await _set(minor, guardian_allows_avatar=True)
    allowed = await client.patch(MY_PROFILE, json={"avatar_url": f"{MEDIA}/avatars/a.png"}, headers=h)
    assert allowed.status_code == 200 and allowed.json()["avatar_url"] == f"{MEDIA}/avatars/a.png"


# ----------------------------------------------------------------------------
# Visibility matrix
# ----------------------------------------------------------------------------
VISIBILITY = [
    # target, viewer (None = anonymous), expected status
    ("public", None, 200), ("public", "stranger", 200), ("public", "outsider", 200),
    ("clubvis", None, 404), ("clubvis", "stranger", 404), ("clubvis", "outsider", 404),
    ("clubvis", "mate", 200), ("clubvis", "director", 200), ("clubvis", "admin", 200),
    ("private", None, 404), ("private", "mate", 404), ("private", "director_b", 404),
    ("private", "admin_b", 404), ("private", "private", 200), ("private", "director", 200),
    ("private", "secretary", 200), ("private", "counselor", 200), ("private", "admin", 200),
    ("private", "master", 200),
    ("minor", None, 404), ("minor", "stranger", 404), ("minor", "mate", 404),
    ("minor", "outsider", 404), ("minor", "director_b", 404), ("minor", "admin_b", 404),
    ("minor", "minor", 200), ("minor", "guardian", 200), ("minor", "director", 200),
    ("minor", "admin", 200),
    ("nobd", None, 404), ("nobd", "mate", 404), ("nobd", "guardian", 200),
]


async def test_visibility_matrix(client, world):
    failures = []
    for target, viewer, expected in VISIBILITY:
        response = await _profile(client, world[target], world[viewer] if viewer else None)
        if response.status_code != expected:
            failures.append((target, viewer, expected, response.status_code))
        if expected == 404:
            assert response.json() == {"detail": "profile_not_found"}
    assert failures == []


async def test_unknown_profiles_are_404(client, world):
    for key in (str(uuid.uuid4()), "nadie.aqui", "x", "%20"):
        response = await _profile(client, key, world["stranger"])
        assert response.status_code == 404, key


async def test_suspended_account_is_404_for_everyone_but_itself(client, factory, world):
    gone = await factory.user("gone", "STUDENT", world["club"]["id"])
    await _set(gone, profile_visibility="public", status="SUSPENDED")
    assert (await _profile(client, gone)).status_code == 404
    assert (await _profile(client, gone, world["director"])).status_code == 404


async def test_public_profile_leaks_no_personal_data(client, world):
    response = await _profile(client, world["public"])
    assert response.status_code == 200
    body = response.json()
    raw = response.text
    for key in ("email", "birth_date", "phone", "latitude", "longitude", "organization_id", "role"):
        assert key not in body
    assert world["public"]["email"] not in raw and "1990-05-17" not in raw
    assert set(body) == {
        "id", "handle", "name", "avatar_url", "cover_url", "bio", "club", "association", "class",
        "master_guide", "stats", "xp", "badges", "honors_earned", "honors_in_progress",
        "visibility", "is_me", "can_edit",
    }
    assert body["club"] == {"id": world["club"]["id"], "name": body["club"]["name"], "city": "Ciudad Club"}
    assert body["association"]["id"] == world["association"]["id"]
    assert body["bio"] == "Hola, soy público" and body["is_me"] is False and body["can_edit"] is False
    # The public sees the level, never the number.
    assert body["xp"]["total"] is None and body["xp"]["level"] >= 1 and body["xp"]["level_name"]


async def test_xp_number_only_for_self_staff_and_hierarchy(client, world):
    for viewer in ("public", "director", "admin", "master"):
        body = (await _profile(client, world["public"], world[viewer])).json()
        assert isinstance(body["xp"]["total"], int), viewer
    for viewer in (None, "mate", "outsider"):
        body = (await _profile(client, world["public"], world[viewer] if viewer else None)).json()
        assert body["xp"]["total"] is None, viewer
    # The approved guardian of a minor sees the number too (and the conduct bar, below).
    guardian_view = (await _profile(client, world["minor"], world["guardian"])).json()
    assert isinstance(guardian_view["xp"]["total"], int)


async def test_conduct_bar_only_for_guardians_staff_and_hierarchy(client, factory, world):
    minor = world["minor"]
    own = (await client.get(f"{PROFILES}/me", headers=minor["headers"])).json()
    for viewer in ("guardian", "director", "secretary", "counselor", "admin", "master", "minor"):
        response = await _profile(client, minor, world[viewer])
        assert response.status_code == 200, viewer
        body = response.json()
        # Exactly the shape of /profiles/me.
        assert body["conduct"] == own["conduct"], viewer
        assert body["xp"]["total"] == own["xp"]["total"], viewer
    # The guardian of an account without birth date (a minor for the profile) too.
    nobd = (await _profile(client, world["nobd"], world["guardian"])).json()
    assert nobd["conduct"]["start"] == 70 and isinstance(nobd["xp"]["total"], int)

    # `club`-visibility peers and the public: never the bar, never the number.
    peer = (await _profile(client, world["clubvis"], world["mate"])).json()
    assert "conduct" not in peer and peer["xp"]["total"] is None
    for viewer in (None, "stranger", "mate", "outsider"):
        body = (await _profile(client, world["public"], world[viewer] if viewer else None)).json()
        assert "conduct" not in body and body["xp"]["total"] is None, viewer
    # Staff of the member's club see an adult's bar too.
    staff = (await _profile(client, world["public"], world["director"])).json()
    assert staff["conduct"]["window_weeks"] == 8

    # A guardianship that is not approved opens nothing.
    pending = await factory.user("pending-guardian", "PARENT_GUARDIAN")
    await _guardianship(pending, minor, consent="PENDING")
    assert (await _profile(client, minor, pending)).status_code == 404


async def test_minor_photo_only_when_the_guardian_allows_it(client, world):
    minor, guardian = world["minor"], world["guardian"]
    assert (await _profile(client, minor, world["director"])).json()["avatar_url"] is None
    assert (await client.get(f"{PROFILES}/me", headers=minor["headers"])).json()["avatar_url"] is None

    url = f"/api/v1/users/{minor['id']}"
    for who in ("minor", "director", "admin", "stranger"):
        denied = await client.patch(url, json={"guardian_allows_avatar": True}, headers=world[who]["headers"])
        assert denied.status_code == 403, who
    # A guardian changes that flag and nothing else.
    other = await client.patch(url, json={"guardian_allows_avatar": True, "name": "X"}, headers=guardian["headers"])
    assert other.status_code == 403
    allowed = await client.patch(url, json={"guardian_allows_avatar": True}, headers=guardian["headers"])
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["guardian_allows_avatar"] is True
    assert (await _profile(client, minor, world["director"])).json()["avatar_url"] == f"{MEDIA}/avatars/kid.png"
    audit = await fetch_one(
        "SELECT user_id FROM audit_log WHERE entity_type = 'USER' AND entity_id = :id"
        " AND action = 'UPDATE' ORDER BY created_at DESC LIMIT 1", id=minor["id"],
    )
    assert str(audit["user_id"]) == guardian["id"]

    revoked = await client.patch(url, json={"guardian_allows_avatar": False}, headers=world["master"]["headers"])
    assert revoked.status_code == 200
    assert (await _profile(client, minor, world["director"])).json()["avatar_url"] is None


async def test_my_profile(client, world):
    response = await client.get(f"{PROFILES}/me", headers=world["minor"]["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["is_me"] and body["is_minor"] is True and body["visibility"] == "public"
    assert body["conduct"] == {"score": body["conduct"]["score"], "start": 70, "window_weeks": 8}
    assert isinstance(body["xp"]["total"], int) and body["cover_url"] is None
    assert "email" not in body and "birth_date" not in body
    assert (await client.get(f"{PROFILES}/me")).status_code == 401


# ----------------------------------------------------------------------------
# XP: pure functions
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "total, level, name, next_at",
    [
        (0, 1, "Explorador", 250), (249, 1, "Explorador", 250), (250, 2, "Rastreador", 750),
        (749, 2, "Rastreador", 750), (750, 3, "Excursionista", 1500), (1499, 3, "Excursionista", 1500),
        (1500, 4, "Guía", 3000), (3000, 5, "Pionero", 6000), (5999, 5, "Pionero", 6000),
        (6000, 6, "Maestro", None), (99999, 6, "Maestro", None),
    ],
)
def test_level_thresholds(total, level, name, next_at):
    assert xp.level_for(total) == xp.Level(level=level, name=name, next_level_at=next_at)


def test_xp_formula_per_source():
    assert xp.honor_xp(None) == 100 and xp.honor_xp(1) == 100
    assert xp.honor_xp(2) == 150 and xp.honor_xp(3) == 200
    # 5 per hour, at most 200 in one calendar month.
    assert xp.service_xp({(2026, 1): 10}) == 50
    assert xp.service_xp({(2026, 1): 40, (2026, 2): 100, (2026, 3): 0.5}) == 200 + 200 + 2
    facts = xp.Facts(
        requirements_complete=7, honor_skill_levels=[1, 2, 3], investitures=2, courses=1,
        service_by_month={(2026, 1): 12}, attendance=3, awards_net=-40,
    )
    result = xp.breakdown(facts)
    assert result.as_dict() == {
        "requirements": 70, "honors": 450, "investitures": 1000, "service": 60,
        "attendance": 15, "courses": 50, "awards": 0,
    }
    assert result.total == 1645
    assert xp.breakdown(xp.Facts(awards_net=35)).total == 35


def test_facts_from_rows():
    rows_certs = [
        {"honor_id": uuid.uuid4(), "program_id": None, "skill_level": 2, "enrollment_mode": "COURSE"},
        {"honor_id": uuid.uuid4(), "program_id": None, "skill_level": None, "enrollment_mode": "CLUB"},
        {"honor_id": None, "program_id": uuid.uuid4(), "skill_level": None, "enrollment_mode": "CLUB"},
    ]
    activity = [
        {"category": "SERVICE", "performed_on": date(2026, 1, 3), "quantity": 30},
        {"category": "SERVICE", "performed_on": date(2026, 1, 20), "quantity": 30},
        {"category": "ATTENDANCE", "performed_on": date(2026, 1, 20), "quantity": 1},
    ]
    facts = xp.facts_from([{"complete": 3}, {"complete": 2}], rows_certs, activity, 15)
    assert facts.requirements_complete == 5 and facts.courses == 1 and facts.investitures == 1
    assert facts.service_by_month == {(2026, 1): 60.0} and facts.attendance == 1
    assert xp.breakdown(facts).total == 50 + 250 + 500 + 200 + 5 + 50 + 15


def test_conduct_bar_math():
    assert xp.conduct_score(0) == 70
    assert xp.conduct_score(25) == 95 and xp.conduct_score(30) == 100 and xp.conduct_score(90) == 100
    assert xp.conduct_score(-70) == 0 and xp.conduct_score(-200) == 0
    today = date(2026, 9, 23)
    start = xp.conduct_window_start(today)
    assert (today - start).days + 1 == 56


def test_iso_week_parsing():
    assert xp.parse_iso_week("2026-W39", date(2026, 1, 1)) == (date(2026, 9, 21), date(2026, 9, 27), "2026-W39")
    assert xp.parse_iso_week(None, date(2026, 9, 23))[2] == "2026-W39"
    for bad in ("2026-39", "2026-W60", "abc"):
        with pytest.raises(Exception):
            xp.parse_iso_week(bad, date(2026, 1, 1))


# ----------------------------------------------------------------------------
# XP and badges from real rows
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def portfolio(factory, world):
    """A member with two certified honors (one of skill level 3) that complete a category, an
    honor in progress, a revoked certificate, an invested class, hours and attendance."""
    member = await factory.user("achiever", "STUDENT", world["club"]["id"])
    await _set(member, profile_visibility="public")
    uid = uuid.UUID(member["id"])
    async with SessionLocal() as db:
        ministry = await db.scalar(text("SELECT id FROM ministries WHERE slug = 'pathfinders'"))
        template = await db.scalar(text("SELECT id FROM certificate_templates ORDER BY created_at LIMIT 1"))
        if template is None:
            template = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO certificate_templates (id, name, width, height) VALUES (:id, 'zz-profiles', 11, 8.5)"
            ), {"id": template})
        category = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO honor_categories (id, name, slug) VALUES (:id, :name, :slug)"
        ), {"id": category, "name": factory.name("Nudos"), "slug": f"{factory.prefix}-nudos"})
        honors = {}
        for key, skill, cat in (("h1", 1, category), ("h2", 3, category), ("h3", 1, None), ("h4", 2, None)):
            honors[key] = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO honors (id, ministry_id, category_id, name, slug, code, status, active,"
                " skill_level, image_url) VALUES (:id, :m, :cat, :name, :slug, :code, 'PUBLISHED', true,"
                " :skill, :img)"
            ), {"id": honors[key], "m": ministry, "cat": cat, "name": factory.name(key),
                "slug": f"{factory.prefix}-{key}", "code": f"{factory.prefix}-{key}", "skill": skill,
                "img": f"{MEDIA}/patches/{key}.png"})
        program = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO programs (id, ministry_id, kind, slug, name, status, image_url)"
            " VALUES (:id, :m, 'CLASS', :slug, :name, 'PUBLISHED', :img)"
        ), {"id": program, "m": ministry, "slug": f"{factory.prefix}-amigo-v2",
            "name": factory.name("Amigo"), "img": f"{MEDIA}/programs/amigo.png"})

        async def enrollment(status, *, honor=None, prog=None, complete=0, total=0):
            eid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO honor_enrollments (id, user_id, honor_id, program_id, status, mode)"
                " VALUES (:id, :u, :h, :p, :s, 'CLUB')"
            ), {"id": eid, "u": uid, "h": honor, "p": prog, "s": status})
            for position in range(1, total + 1):
                await db.execute(text(
                    "INSERT INTO requirement_progress (id, enrollment_id, requirement_position,"
                    " is_practical, status) VALUES (gen_random_uuid(), :e, :pos, false, :st)"
                ), {"e": eid, "pos": position, "st": "COMPLETE" if position <= complete else "PENDING"})
            return eid

        async def certificate(eid, issued, *, honor=None, prog=None, revoked=False):
            await db.execute(text(
                "INSERT INTO certificates (id, ministry_id, organization_id, honor_id, program_id,"
                " template_id, certificate_no, recipient_name, honor_name_snapshot, issued_date, status,"
                " user_id, enrollment_id, revoked_at)"
                " VALUES (gen_random_uuid(), :m, :org, :h, :p, :t, :no, :rn, 'snap', :d, :st, :u, :e, :rv)"
            ), {"m": ministry, "org": uuid.UUID(world["association"]["id"]), "h": honor, "p": prog,
                "t": template, "no": f"{factory.prefix}-{uuid.uuid4().hex[:8]}",
                "rn": factory.name("achiever"), "d": issued, "st": "revoked" if revoked else "issued",
                "u": uid, "e": eid, "rv": utcnow() if revoked else None})

        e1 = await enrollment("CERTIFIED", honor=honors["h1"], complete=3, total=3)
        await certificate(e1, date(2026, 3, 1), honor=honors["h1"])
        e2 = await enrollment("CERTIFIED", honor=honors["h2"], complete=2, total=2)
        await certificate(e2, date(2026, 4, 1), honor=honors["h2"])
        await enrollment("IN_PROGRESS", honor=honors["h3"], complete=1, total=4)
        e4 = await enrollment("WITHDRAWN", honor=honors["h4"], complete=5, total=5)
        await certificate(e4, date(2026, 2, 1), honor=honors["h4"], revoked=True)
        e5 = await enrollment("CERTIFIED", prog=program, complete=0, total=0)
        await certificate(e5, date(2026, 5, 1), prog=program)
        for performed_on, category_name, quantity, status in (
            (date(2026, 6, 2), "SERVICE", 20, "APPROVED"),
            (date(2026, 6, 9), "SERVICE", 10, "APPROVED"),      # June: 30 h -> 150
            (date(2026, 7, 1), "SERVICE", 24, "APPROVED"),
            (date(2026, 7, 2), "SERVICE", 24, "APPROVED"),
            (date(2026, 7, 3), "SERVICE", 2, "APPROVED"),       # July: 50 h -> capped 200
            (date(2026, 7, 4), "SERVICE", 10, "SUBMITTED"),     # not approved: ignored
            (date(2026, 7, 5), "ATTENDANCE", 1, "APPROVED"),
            (date(2026, 7, 12), "ATTENDANCE", 1, "APPROVED"),
            (date(2026, 7, 19), "ATTENDANCE", 1, "APPROVED"),
            (date(2026, 7, 26), "ATTENDANCE", 1, "APPROVED"),  # 4 -> 20
        ):
            await db.execute(text(
                "INSERT INTO activity_logs (id, user_id, club_id, category, performed_on, quantity,"
                " description, status, decided_at) VALUES (gen_random_uuid(), :u, :c, :cat, :d, :q,"
                " 'x', :st, :decided)"
            ), {"u": uid, "c": uuid.UUID(world["club"]["id"]), "cat": category_name, "d": performed_on,
                "q": quantity, "st": status, "decided": None if status == "SUBMITTED" else utcnow()})
        await db.commit()
    membership = await _membership(member, world["club"])
    xp.invalidate(uid)
    return {"member": member, "membership": membership, "honors": honors, "program": program,
            "category_slug": f"{factory.prefix}-nudos", "class_key": f"clase-{factory.prefix}-amigo"}


# requirements (3 + 2 + 1) * 10 = 60; honors 100 + 200 = 300; class 500; service 150 + 200 = 350;
# attendance 4 * 5 = 20. The revoked certificate and its withdrawn enrollment count for nothing.
PORTFOLIO_XP = {"requirements": 60, "honors": 300, "investitures": 500, "service": 350,
                "attendance": 20, "courses": 0, "awards": 0}


async def test_xp_by_source_and_profile_sections(client, world, portfolio):
    member = portfolio["member"]
    mine = await client.get(f"{PROFILES}/me/xp", headers=member["headers"])
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["by_source"] == PORTFOLIO_XP
    assert body["xp"] == {"total": 1230, "level": 3, "level_name": "Excursionista", "next_level_at": 1500}
    assert body["conduct"]["score"] == 70 and body["awards"]["items"] == []

    profile = (await _profile(client, member)).json()
    assert profile["stats"] == {"honors_earned": 2, "honors_in_progress": 1,
                                "service_hours": 80.0, "attendance": 4}
    assert [h["honor_id"] for h in profile["honors_earned"]] == [
        str(portfolio["honors"]["h2"]), str(portfolio["honors"]["h1"])]
    earned = profile["honors_earned"][0]
    assert earned["image_url"].endswith("/patches/h2.png")
    assert earned["verify_url"].endswith(f"/verify/{earned['certificate_no']}")
    assert profile["honors_in_progress"] == [{
        "honor_id": str(portfolio["honors"]["h3"]), "name": profile["honors_in_progress"][0]["name"],
        "image_url": f"{MEDIA}/patches/h3.png", "progress_pct": 25}]
    assert profile["class"]["program_id"] == str(portfolio["program"])
    assert profile["class"]["progress_pct"] == 100
    assert profile["master_guide"] == {"status": "none", "progress_pct": 0}
    assert profile["xp"] == {"total": None, "level": 3, "level_name": "Excursionista", "next_level_at": 1500}

    keys = {badge["key"]: badge for badge in profile["badges"]}
    assert "primera-especialidad" in keys and "cinco-especialidades" not in keys
    assert keys["primera-especialidad"]["earned_at"] == "2026-03-01"
    category_key = f"categoria-completa:{portfolio['category_slug']}"
    assert keys[category_key]["earned_at"] == "2026-04-01"
    class_key = portfolio["class_key"]
    assert class_key in keys and keys[class_key]["image_url"] == f"{MEDIA}/programs/amigo.png"
    assert "100-horas-servicio" not in keys and "guia-mayor" not in keys and "fundador" not in keys


async def test_badges_derivation_edges(world, portfolio, monkeypatch):
    user_row = type("U", (), {"created_at": utcnow() - timedelta(days=400)})()
    monkeypatch.setattr(settings, "PUBLIC_LAUNCH_DATE", date.today())
    certs = [
        {"honor_id": uuid.uuid4(), "program_id": None, "issued_date": date(2026, 1, i + 1),
         "category_id": None, "enrollment_mode": "COURSE" if i == 3 else "CLUB",
         "program_slug": None, "program_kind": None}
        for i in range(10)
    ] + [
        {"honor_id": None, "program_id": uuid.uuid4(), "issued_date": date(2026, 2, 1), "category_id": None,
         "enrollment_mode": "CLUB", "program_slug": "guia-mayor-v2", "program_kind": "CURRICULUM",
         "program_name": "Guía Mayor", "program_image": None},
    ]
    activity = [
        {"category": "SERVICE", "performed_on": date(2026, 1, 1), "quantity": 60},
        {"category": "ATTENDANCE", "performed_on": date(2026, 1, 2), "quantity": 90},
        {"category": "SERVICE", "performed_on": date(2026, 1, 5), "quantity": 40},
    ]
    badges = {b.key: b for b in profile_service._badges(user_row, certs, activity, [])}
    assert badges["cinco-especialidades"].earned_at == date(2026, 1, 5)
    assert badges["diez-especialidades"].earned_at == date(2026, 1, 10)
    assert badges["100-horas-servicio"].earned_at == date(2026, 1, 5)
    assert badges["curso-en-linea"].earned_at == date(2026, 1, 4)
    assert badges["guia-mayor"].earned_at == date(2026, 2, 1)
    assert "fundador" in badges
    assert badges["primera-especialidad"].image_url == f"{settings.R2_PUBLIC_URL}/badges/primera-especialidad.png"


async def test_xp_total_is_cached_and_invalidated_by_an_award(client, factory, world, portfolio):
    member = portfolio["member"]
    before = (await client.get(f"{PROFILES}/me/xp", headers=member["headers"])).json()["xp"]["total"]
    # A change the cache is not told about stays invisible for 10 minutes…
    await _exec(
        "INSERT INTO activity_logs (id, user_id, category, performed_on, quantity, description, status,"
        " decided_at) VALUES (gen_random_uuid(), :u, 'ATTENDANCE', :d, 1, 'x', 'APPROVED', now())",
        u=uuid.UUID(member["id"]), d=date(2026, 8, 1),
    )
    cached_total = (await client.get(f"{PROFILES}/me/xp", headers=member["headers"])).json()["xp"]["total"]
    assert cached_total == before
    # …an award invalidates it.
    response = await _award(client, world["director"], world["club"], portfolio["membership"],
                            category="participacion", points=15)
    assert response.status_code == 201, response.text
    after = (await client.get(f"{PROFILES}/me/xp", headers=member["headers"])).json()
    assert after["xp"]["total"] == before + 5 + 15
    assert after["by_source"]["awards"] == 15
    item = after["awards"]["items"][0]
    assert item["category"] == "participacion" and item["points"] == 15
    assert item["awarded_by_name"] == factory.name("director")
    assert after["awards"]["total"] == 1 and after["awards"]["limit"] == 50


async def test_profile_assembly_query_budget(client, world, portfolio):
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        response = await _profile(client, portfolio["member"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    assert response.status_code == 200
    # user, organizations, guardianships, enrollments, certificates, activity, awards, categories
    assert len(statements) <= 8, statements


# ----------------------------------------------------------------------------
# XP awards: permissions, validation, weekly cap, conduct bar
# ----------------------------------------------------------------------------
async def test_award_permissions(client, factory, world):
    club, minor_membership = world["club"], world["memberships"]["minor"]
    assert (await _award(client, world["director"], club, minor_membership)).status_code == 201
    assert (await _award(client, world["secretary"], club, minor_membership, points=-5)).status_code == 201
    # The counselor of the member's unit may; of any other member, not.
    assert (await _award(client, world["counselor"], club, minor_membership, points=1)).status_code == 201
    denied = await _award(client, world["counselor"], club, world["memberships"]["mate"])
    assert denied.status_code == 403 and denied.json()["detail"] == "xp_award_forbidden"
    for who in ("mate", "director_b", "admin", "master", "guardian", "minor"):
        response = await _award(client, world[who], club, minor_membership)
        assert response.status_code == 403, who
    # Nobody awards themselves.
    own = await _award(client, world["director"], club, world["memberships"]["director"])
    assert own.status_code == 403
    # A membership of another club, asked through this club: 404.
    assert (await _award(client, world["director"], club, world["memberships"]["outsider"])).status_code == 404
    # Only ACTIVE memberships.
    ended_user = await factory.user("ended", "STUDENT", club["id"])
    ended = await _membership(ended_user, club, status="ENDED")
    response = await _award(client, world["director"], club, ended)
    assert response.status_code == 409 and response.json()["detail"] == "membership_not_active"

    audits = await fetch_all(
        "SELECT action, metadata_json FROM audit_log WHERE entity_type = 'XP_AWARD'"
        " AND metadata_json->>'membership_id' = :m", m=minor_membership,
    )
    assert len(audits) == 3 and {a["action"] for a in audits} == {"CREATE"}


async def test_award_validation(client, world):
    club, membership = world["club"], world["memberships"]["mate"]
    director = world["director"]
    today = utcnow().date()
    cases = [
        ({"points": 0}, "xp_points_zero"),
        ({"points": 51}, None),
        ({"points": -51}, None),
        ({"category": "simpatia"}, None),
        ({"note": "x" * 201}, None),
        ({"occurred_on": today + timedelta(days=1)}, "occurred_on_in_future"),
        ({"occurred_on": today - timedelta(days=91)}, "occurred_on_too_old"),
    ]
    for body, detail in cases:
        response = await _award(client, director, club, membership, **body)
        assert response.status_code == 422, (body, response.text)
        if detail:
            assert response.json()["detail"] == detail
    ok = await _award(client, director, club, membership, points=-50, note="  retraso  ",
                      occurred_on=today - timedelta(days=90), category="puntualidad")
    assert ok.status_code == 201, ok.text
    assert ok.json()["note"] == "retraso" and ok.json()["points"] == -50


async def test_weekly_cap(client, factory, world):
    member = await factory.user("capped", "STUDENT", world["club"]["id"])
    membership = await _membership(member, world["club"])
    director, club = world["director"], world["club"]
    today = utcnow().date()
    first = await _award(client, director, club, membership, points=50, category="participacion")
    assert first.json()["week_positive_points"] == 50 and first.json()["week_cap_remaining"] == 50
    assert (await _award(client, director, club, membership, points=50, category="otro")).status_code == 201
    over = await _award(client, director, club, membership, points=1)
    assert over.status_code == 409 and over.json()["detail"] == "xp_weekly_cap"
    # A penalty is never capped, and does not open room for more positive points.
    assert (await _award(client, director, club, membership, points=-10)).status_code == 201
    assert (await _award(client, director, club, membership, points=5)).status_code == 409
    # Another ISO week is another budget.
    last_week = today - timedelta(days=7)
    assert (await _award(client, director, club, membership, points=50, occurred_on=last_week)).status_code == 201
    rows = await fetch_all("SELECT points FROM xp_awards WHERE user_id = :u", u=uuid.UUID(member["id"]))
    assert sorted(r["points"] for r in rows) == [-10, 50, 50, 50]


async def test_conduct_bar_window_and_bounds(client, factory, world):
    member = await factory.user("conduct", "STUDENT", world["club"]["id"])
    membership = await _membership(member, world["club"])
    director, club = world["director"], world["club"]
    today = utcnow().date()
    for body in (
        {"category": "conducta", "points": 20},
        {"category": "puntualidad", "points": -5},
        {"category": "participacion", "points": 10},                           # not in the bar
        {"category": "uniforme", "points": 3, "occurred_on": today - timedelta(days=55)},  # inside
    ):
        assert (await _award(client, director, club, membership, **body)).status_code == 201
    # Outside the window (day 57 back): inserted by hand, the API allows up to 90 days.
    await _exec(
        "INSERT INTO xp_awards (id, user_id, club_id, category, points, occurred_on)"
        " VALUES (gen_random_uuid(), :u, :c, 'conducta', -40, :d)",
        u=uuid.UUID(member["id"]), c=uuid.UUID(club["id"]), d=today - timedelta(days=56),
    )
    me = (await client.get(f"{PROFILES}/me", headers=member["headers"])).json()
    assert me["conduct"]["score"] == 70 + 20 - 5 + 3
    # The public profile never shows it.
    await _set(member, profile_visibility="public")
    assert "conduct" not in (await _profile(client, member)).json()

    # Bounds: it never goes past 100 nor below 0.
    for _ in range(2):
        await _award(client, director, club, membership, category="conducta", points=40,
                     occurred_on=today - timedelta(days=14))
    assert (await client.get(f"{PROFILES}/me", headers=member["headers"])).json()["conduct"]["score"] == 100
    for _ in range(6):
        await _award(client, director, club, membership, category="conducta", points=-50)
    assert (await client.get(f"{PROFILES}/me", headers=member["headers"])).json()["conduct"]["score"] == 0


async def test_club_weekly_summary(client, factory, world):
    club, director = world["club"], world["director"]
    member = await factory.user("summary", "STUDENT", club["id"])
    membership = await _membership(member, club, unit_id=uuid.UUID(world["unit_id"]))
    today = utcnow().date()
    await _award(client, director, club, membership, points=30, category="participacion")
    await _award(client, director, club, membership, points=-10, category="conducta")
    await _award(client, director, club, membership, points=20, occurred_on=today - timedelta(days=7))

    response = await client.get(f"/api/v1/clubs/{club['id']}/xp", headers=director["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["week"] == "{:04d}-W{:02d}".format(*today.isocalendar()[:2])
    row = next(m for m in body["members"] if m["membership_id"] == membership)
    assert row["points_week"] == 20 and row["positive_points_week"] == 30
    assert row["cap_remaining"] == 70 and row["conduct"] == 70 - 10 + 20
    assert "email" not in row
    unit = next(u for u in body["units"] if u["unit_id"] == world["unit_id"])
    assert unit["points_week"] >= 20

    year, week, _ = (today - timedelta(days=7)).isocalendar()
    last = await client.get(f"/api/v1/clubs/{club['id']}/xp", params={"week": f"{year:04d}-W{week:02d}"},
                            headers=director["headers"])
    assert next(m for m in last.json()["members"] if m["membership_id"] == membership)["points_week"] == 20

    bad = await client.get(f"/api/v1/clubs/{club['id']}/xp", params={"week": "2026-99"}, headers=director["headers"])
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_week"
    for who in ("mate", "director_b", "stranger"):
        denied = await client.get(f"/api/v1/clubs/{club['id']}/xp", headers=world[who]["headers"])
        assert denied.status_code == 403, who
    # The counselor sees the members of their unit only.
    mine = await client.get(f"/api/v1/clubs/{club['id']}/xp", headers=world["counselor"]["headers"])
    assert mine.status_code == 200
    assert {m["unit_id"] for m in mine.json()["members"]} == {world["unit_id"]}


async def test_unit_counselor_awards_whatever_their_role(client, factory, world):
    """Who leads the unit is `club_units.counselor_id`; an INSTRUCTOR may lead one."""
    club = world["club"]
    leader = await factory.user("unit-leader", "INSTRUCTOR", club["id"])
    await _membership(leader, club, role="INSTRUCTOR")
    unit_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_units (id, club_id, name, counselor_id) VALUES (:id, :club, :name, :c)",
        id=unit_id, club=uuid.UUID(club["id"]), name=factory.name("unidad-b"),
        c=uuid.UUID(leader["id"]),
    )
    member = await factory.user("led", "STUDENT", club["id"])
    in_unit = await _membership(member, club, unit_id=unit_id)
    ok = await _award(client, leader, club, in_unit, points=2)
    assert ok.status_code == 201, ok.text
    # Not the members of other units, nor of none.
    for membership in (world["memberships"]["minor"], world["memberships"]["mate"]):
        assert (await _award(client, leader, club, membership)).status_code == 403
    # Once the unit is closed, the post goes with it.
    await _exec("UPDATE club_units SET status = 'archived' WHERE id = :id", id=unit_id)
    assert (await _award(client, leader, club, in_unit)).status_code == 403


# ----------------------------------------------------------------------------
# The guardian's panel and the roster link to the profile
# ----------------------------------------------------------------------------
async def test_my_children_carry_handle_and_avatar_switch(client, world):
    guardian, minor = world["guardian"], world["minor"]
    await _set(minor, guardian_allows_avatar=False)
    url = "/api/v1/users/guardianships/my-children"
    rows = (await client.get(url, headers=guardian["headers"])).json()
    row = next(r for r in rows if r["child_id"] == minor["id"])
    handle = (await fetch_one("SELECT handle FROM users WHERE id = :id", id=uuid.UUID(minor["id"])))["handle"]
    assert row["handle"] == handle and row["guardian_allows_avatar"] is False
    assert all(isinstance(r["handle"], str) and r["handle"] for r in rows)

    allowed = await client.patch(
        f"/api/v1/users/{minor['id']}", json={"guardian_allows_avatar": True}, headers=guardian["headers"]
    )
    assert allowed.status_code == 200
    rows = (await client.get(url, headers=guardian["headers"])).json()
    assert next(r for r in rows if r["child_id"] == minor["id"])["guardian_allows_avatar"] is True
    await _set(minor, guardian_allows_avatar=False)


async def test_roster_rows_link_to_the_profile(client, world):
    club, director = world["club"], world["director"]
    await _set(world["mate"], avatar_url=f"{MEDIA}/avatars/mate.png")
    await _set(world["minor"], guardian_allows_avatar=False)
    url = f"/api/v1/clubs/{club['id']}/members"

    def by_user(rows):
        return {row["user_id"]: row for row in rows}

    rows = by_user((await client.get(url, headers=director["headers"])).json())
    handles = {
        str(r["id"]): r["handle"]
        for r in await fetch_all("SELECT id, handle FROM users WHERE id = ANY(:ids)",
                                 ids=[uuid.UUID(uid) for uid in rows])
    }
    assert all(row["handle"] == handles[uid] for uid, row in rows.items())
    assert rows[world["mate"]["id"]]["avatar_url"] == f"{MEDIA}/avatars/mate.png"
    # Rule 4: a minor's photo only once a guardian allowed it.
    assert rows[world["minor"]["id"]]["avatar_url"] is None
    await _set(world["minor"], guardian_allows_avatar=True)
    rows = by_user((await client.get(url, headers=director["headers"])).json())
    assert rows[world["minor"]["id"]]["avatar_url"] == f"{MEDIA}/avatars/kid.png"
    # The secretary's rows (no guardian_email) carry the same link.
    secretary_rows = by_user((await client.get(url, headers=world["secretary"]["headers"])).json())
    assert secretary_rows[world["mate"]["id"]]["handle"] == handles[world["mate"]["id"]]
    await _set(world["minor"], guardian_allows_avatar=False)


async def test_roster_query_count_does_not_grow_with_adults(client, factory, world):
    """The link fields come in the roster's own query: no statement per row."""
    club, director = world["club"], world["director"]
    url = f"/api/v1/clubs/{club['id']}/members"

    async def statements() -> int:
        seen = []

        def count(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            assert (await client.get(url, headers=director["headers"])).status_code == 200
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        return len(seen)

    before = await statements()
    for index in range(3):
        adult = await factory.user(f"roster-adult-{index}", "STUDENT", club["id"])
        await _set(adult, avatar_url=f"{MEDIA}/avatars/a{index}.png")
        await _membership(adult, club)
    assert await statements() == before


# ----------------------------------------------------------------------------
# Media: the `covers` folder and the profile photo
# ----------------------------------------------------------------------------
class FakeR2:
    def __init__(self):
        self.objects = []

    def put_object(self, **kwargs):
        self.objects.append(kwargs)


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key-id")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "R2_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr(storage, "get_client", lambda: fake)
    return fake


async def _upload(client, user, data, content_type, folder):
    return await client.post(
        "/api/v1/media/upload",
        files={"file": ("f.bin", data, content_type)},
        data={"folder": folder},
        headers=user["headers"],
    )


async def test_profile_media_folders(client, factory, world, r2):
    adult = await factory.user("uploader", "STUDENT", world["club"]["id"])
    cover = await _upload(client, adult, PNG, "image/png", "covers")
    assert cover.status_code == 201, cover.text
    assert re.fullmatch(r"covers/[0-9a-f]{32}\.png", cover.json()["key"])
    assert (await _upload(client, adult, PNG, "image/png", "avatars")).status_code == 201
    # Only raster images there, and a member still cannot use the other folders.
    pdf = await _upload(client, adult, PDF, "application/pdf", "covers")
    assert pdf.status_code == 415 and pdf.json()["detail"] == "profile_media_images_only"
    assert (await _upload(client, adult, PNG, "image/png", "general")).status_code == 403
    assert (await _upload(client, adult, PNG, "image/png", "patches")).status_code == 403

    minor = await factory.user("minor-uploader", "STUDENT", world["club"]["id"], is_minor=True)
    no_cover = await _upload(client, minor, PNG, "image/png", "covers")
    assert no_cover.status_code == 403 and no_cover.json()["detail"] == "minor_cannot_upload_cover"
    no_photo = await _upload(client, minor, PNG, "image/png", "avatars")
    assert no_photo.status_code == 403 and no_photo.json()["detail"] == "minor_avatar_not_allowed"
    await _set(minor, guardian_allows_avatar=True)
    assert (await _upload(client, minor, PNG, "image/png", "avatars")).status_code == 201
    assert (await _upload(client, minor, PNG, "image/png", "covers")).status_code == 403
    assert len(r2.objects) == 3


# ----------------------------------------------------------------------------
# Last: annulling a certificate invalidates the cached XP (mutates the portfolio above).
# ----------------------------------------------------------------------------
async def test_revoking_a_certificate_invalidates_the_cached_xp(client, world, portfolio):
    from app.models import Certificate, User
    from app.services.certificates import revoke_certificate

    member = portfolio["member"]
    uid = uuid.UUID(member["id"])
    before = (await client.get(f"{PROFILES}/me/xp", headers=member["headers"])).json()["xp"]["total"]
    assert xp.cached(uid) is not None
    async with SessionLocal() as db:
        certificate = (await db.execute(
            text("SELECT id FROM certificates WHERE user_id = :u AND honor_id = :h"),
            {"u": uid, "h": portfolio["honors"]["h2"]},
        )).scalar_one()
        master = await db.get(User, uuid.UUID(world["master"]["id"]))
        await revoke_certificate(db, await db.get(Certificate, certificate), master, "prueba")
        await db.commit()
    assert xp.cached(uid) is None
    after = (await client.get(f"{PROFILES}/me/xp", headers=member["headers"])).json()
    # The level-3 honor (200) and its two requirements (20) are gone.
    assert after["xp"]["total"] == before - 220
    profile = (await _profile(client, member)).json()
    assert [h["honor_id"] for h in profile["honors_earned"]] == [str(portfolio["honors"]["h1"])]
    assert not any(b["key"].startswith("categoria-completa:") for b in profile["badges"])
