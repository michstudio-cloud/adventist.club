"""Bloque H — Secretaría del club: cargos, «pasar lista», nómina, CSV, página del club y puntuación.

Spec: docs/superpowers/specs/2026-09-23-secretaria-club.md.

The rules that must never break:
  * a cargo is a TITLE, never a permission; it is closed, never deleted;
  * the secretary never names DIRECTOR or SUBDIRECTOR;
  * «pasar lista» is idempotent and the attendance XP always mirrors the list;
  * a counselor records their own unit only, and nobody records themselves;
  * the secretary's CSV carries no e-mail and no guardian;
  * the public page of a club never carries an e-mail and never names a minor.
"""

import csv
import io
import re
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, text

from app.config import settings
from app.db import SessionLocal, engine
from app.services import storage
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

CLUBS = "/api/v1/clubs"
TODAY = date.today()
MEDIA = settings.R2_PUBLIC_URL.rstrip("/")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _tables_exist() -> bool:
    import asyncio

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.club_officers') IS NOT NULL"
                    " AND to_regclass('public.club_meetings') IS NOT NULL"
                    " AND to_regclass('public.club_attendance') IS NOT NULL"
                    " AND to_regclass('public.xp_awards') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _tables_exist(), reason="apply migrations/015_secretaria.sql to the test database"
    ),
]
factory = module_factory("secretaria")


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


async def _membership(user: dict, club: dict, *, role="STUDENT", unit_id=None, status="ACTIVE",
                      consent_by: dict | None = None) -> str:
    membership_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, unit_id,"
        " started_at, consent_by_id, consent_at) VALUES (:id, :user, :club, :role, :status,"
        " 'ADMIN', :unit, now() - interval '1 year', :cby, :cat)",
        id=membership_id, user=uuid.UUID(user["id"]), club=uuid.UUID(club["id"]), role=role,
        status=status, unit=uuid.UUID(unit_id) if unit_id else None,
        cby=uuid.UUID(consent_by["id"]) if consent_by else None,
        cat=TODAY if consent_by else None,
    )
    user["membership_id"] = str(membership_id)
    return str(membership_id)


async def _unit(club: dict, name: str, counselor: dict | None = None) -> str:
    unit_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_units (id, club_id, name, counselor_id) VALUES (:id, :club, :name, :c)",
        id=unit_id, club=uuid.UUID(club["id"]), name=name,
        c=uuid.UUID(counselor["id"]) if counselor else None,
    )
    return str(unit_id)


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("club-b", "club", association)
    other_assoc = await factory.org("assoc-b", "association")
    await _exec(
        "UPDATE organizations SET city = 'Ciudad Club', metadata_json = :meta WHERE id = :id",
        id=uuid.UUID(club["id"]), meta='{"church": "Iglesia Central"}',
    )
    p = {
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "secretary": await factory.user("secretary", "CLUB_SECRETARY", club["id"]),
        "counselor": await factory.user("counselor", "COUNSELOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "student": await factory.user("student", "STUDENT", club["id"]),
        "mate": await factory.user("mate", "STUDENT", club["id"]),
        "minor": await factory.user("minor", "STUDENT", club["id"], is_minor=True),
        "guardian": await factory.user("guardian", "PARENT_GUARDIAN"),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", other_club["id"]),
        "outsider": await factory.user("outsider", "STUDENT", other_club["id"]),
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", association["id"]),
        "admin_b": await factory.user("admin-b", "ADMIN_ASSOCIATION", other_assoc["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    await _set(p["student"], birth_date=date(1990, 5, 17), verification_status="VERIFIED")
    await _set(p["minor"], birth_date=date(2014, 5, 4))
    await _set(p["secretary"], verification_status="VERIFIED")
    await _set(p["instructor"], verification_status="VERIFIED")
    await _exec(
        "INSERT INTO guardianships (id, guardian_id, child_id, relationship, consent_status)"
        " VALUES (gen_random_uuid(), :g, :c, 'PARENT', 'APPROVED')",
        g=uuid.UUID(p["guardian"]["id"]), c=uuid.UUID(p["minor"]["id"]),
    )
    u1 = await _unit(club, factory.name("Águilas"), p["counselor"])
    u2 = await _unit(club, factory.name("Halcones"))
    await _membership(p["director"], club, role="CLUB_DIRECTOR")
    await _membership(p["secretary"], club, role="CLUB_SECRETARY")
    await _membership(p["counselor"], club, role="COUNSELOR")
    await _membership(p["instructor"], club, role="INSTRUCTOR")
    await _membership(p["student"], club, unit_id=u1)
    await _membership(p["minor"], club, unit_id=u1, consent_by=p["guardian"])
    await _membership(p["mate"], club, unit_id=u2)
    await _membership(p["director_b"], other_club, role="CLUB_DIRECTOR")
    await _membership(p["outsider"], other_club)
    return {**p, "club": club, "other_club": other_club, "association": association,
            "u1": u1, "u2": u2}


def _url(world, path: str = "") -> str:
    return f"{CLUBS}/{world['club']['id']}{path}"


async def _officer(client, world, actor: str, member: str, title: str, **extra):
    body = {"membership_id": world[member]["membership_id"], "title": title, **extra}
    return await client.post(_url(world, "/officers"), json=body, headers=world[actor]["headers"])


_meeting_counter = iter(range(1, 10_000))


async def _meeting(client, world, actor="director", *, held_on=None, kind="REUNION", title=None):
    body = {"held_on": (held_on or TODAY - timedelta(days=7)).isoformat(), "kind": kind,
            "title": title or f"Reunión {next(_meeting_counter)}"}
    return await client.post(_url(world, "/meetings"), json=body, headers=world[actor]["headers"])


async def _attendance(client, world, meeting_id: str, actor: str, entries: dict[str, str]):
    body = {"entries": [
        {"membership_id": world[label]["membership_id"], "status": status}
        for label, status in entries.items()
    ]}
    return await client.put(
        _url(world, f"/meetings/{meeting_id}/attendance"), json=body, headers=world[actor]["headers"]
    )


async def _meeting_logs(meeting_id: str) -> list:
    return await fetch_all(
        "SELECT user_id, category, status, quantity, performed_on, club_id FROM activity_logs"
        " WHERE meeting_id = :m", m=uuid.UUID(meeting_id),
    )


# ----------------------------------------------------------------------------
# 1. Cargos
# ----------------------------------------------------------------------------
async def test_officer_creation_matrix(client, world):
    created = await _officer(client, world, "director", "mate", "SUBDIRECTOR")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["title"] == "SUBDIRECTOR" and body["custom_title"] is None
    assert body["since"] == TODAY.isoformat() and body["until"] is None and body["active"] is True
    assert body["user_id"] == world["mate"]["id"] and "email" not in body

    assert (await _officer(client, world, "secretary", "student", "TESORERO")).status_code == 201
    assert (await _officer(client, world, "admin", "student", "CAPELLAN")).status_code == 201
    assert (await _officer(client, world, "master", "counselor", "CONSEJERO")).status_code == 201
    for actor in ("counselor", "instructor", "student", "stranger", "director_b", "admin_b"):
        denied = await _officer(client, world, actor, "mate", "INSTRUCTOR")
        assert denied.status_code == 403, (actor, denied.text)
        assert denied.json()["detail"] == "No tienes permiso para gestionar los cargos de este club"

    audit = await fetch_one(
        "SELECT action, metadata_json FROM audit_log WHERE entity_type = 'CLUB_OFFICER'"
        " AND entity_id = :id", id=body["id"],
    )
    assert audit["action"] == "OFFICER_CREATE" and audit["metadata_json"]["title"] == "SUBDIRECTOR"

    # Reading: the staff and the hierarchy; not a student nor a stranger.
    for actor in ("director", "secretary", "instructor", "counselor", "admin", "master"):
        listed = await client.get(_url(world, "/officers"), headers=world[actor]["headers"])
        assert listed.status_code == 200, (actor, listed.text)
    for actor in ("student", "stranger", "director_b"):
        assert (await client.get(_url(world, "/officers"),
                                 headers=world[actor]["headers"])).status_code == 403


async def test_the_secretary_never_names_the_direction(client, world):
    for title in ("DIRECTOR", "SUBDIRECTOR"):
        denied = await _officer(client, world, "secretary", "instructor", title)
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"] == "La secretaría no nombra los cargos de dirección ni subdirección"
    # ...nor edits or closes one the director named.
    named = (await _officer(client, world, "director", "instructor", "DIRECTOR")).json()
    patch = await client.patch(_url(world, f"/officers/{named['id']}"), json={"until": TODAY.isoformat()},
                               headers=world["secretary"]["headers"])
    assert patch.status_code == 403
    close = await client.delete(_url(world, f"/officers/{named['id']}"),
                                headers=world["secretary"]["headers"])
    assert close.status_code == 403


async def test_officer_uniqueness_and_custom_title(client, world):
    first = await _officer(client, world, "director", "minor", "SECRETARIO")
    assert first.status_code == 201, first.text
    again = await _officer(client, world, "director", "minor", "SECRETARIO")
    assert again.status_code == 409 and again.json()["detail"] == "Esa persona ya tiene ese cargo en el club"

    assert (await _officer(client, world, "director", "minor", "OTRO")).status_code == 422
    assert (await _officer(client, world, "director", "minor", "TESORERO",
                           custom_title="Tesorero adjunto")).status_code == 422
    assert (await _officer(client, world, "director", "minor", "OTRO",
                           custom_title="x" * 61)).status_code == 422
    bugler = await _officer(client, world, "director", "minor", "OTRO", custom_title="Corneta")
    assert bugler.status_code == 201 and bugler.json()["custom_title"] == "Corneta"
    # Another free title for the same person is another cargo; the same one is not.
    assert (await _officer(client, world, "director", "minor", "OTRO",
                           custom_title="Abanderado")).status_code == 201
    assert (await _officer(client, world, "director", "minor", "OTRO",
                           custom_title="corneta")).status_code == 409


async def test_closing_an_officer_keeps_the_row(client, world):
    officer = (await _officer(client, world, "director", "mate", "INSTRUCTOR")).json()
    closed = await client.delete(_url(world, f"/officers/{officer['id']}"),
                                 headers=world["secretary"]["headers"])
    assert closed.status_code == 204, closed.text
    row = await fetch_one("SELECT until FROM club_officers WHERE id = :id", id=uuid.UUID(officer["id"]))
    assert row["until"] == TODAY
    # Idempotent.
    assert (await client.delete(_url(world, f"/officers/{officer['id']}"),
                                headers=world["director"]["headers"])).status_code == 204

    current = (await client.get(_url(world, "/officers"), headers=world["director"]["headers"])).json()
    assert officer["id"] not in {row["id"] for row in current}
    history = (await client.get(_url(world, "/officers?include_closed=true"),
                                headers=world["director"]["headers"])).json()
    closed_row = next(row for row in history if row["id"] == officer["id"])
    assert closed_row["active"] is False and closed_row["until"] == TODAY.isoformat()
    # The cargo is free again.
    assert (await _officer(client, world, "director", "mate", "INSTRUCTOR")).status_code == 201
    audit = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE entity_type = 'CLUB_OFFICER' AND entity_id = :id"
        " AND action = 'OFFICER_CLOSE'", id=officer["id"],
    )
    assert audit["n"] == 1


async def test_patching_an_officer(client, world):
    other = (await _officer(client, world, "director", "counselor", "OTRO", custom_title="Guía")).json()
    url = _url(world, f"/officers/{other['id']}")
    renamed = await client.patch(url, json={"custom_title": "Guía de campamento"},
                                 headers=world["secretary"]["headers"])
    assert renamed.status_code == 200 and renamed.json()["custom_title"] == "Guía de campamento"
    later = (TODAY + timedelta(days=30)).isoformat()
    until = await client.patch(url, json={"until": later}, headers=world["director"]["headers"])
    assert until.status_code == 200 and until.json()["until"] == later and until.json()["active"] is True
    too_early = await client.patch(url, json={"until": (TODAY - timedelta(days=400)).isoformat()},
                                   headers=world["director"]["headers"])
    assert too_early.status_code == 422
    assert (await client.patch(url, json={}, headers=world["director"]["headers"])).status_code == 400
    assert (await client.patch(url, json={"custom_title": None},
                               headers=world["director"]["headers"])).status_code == 422

    plain = (await _officer(client, world, "director", "counselor", "CAPELLAN")).json()
    bad = await client.patch(_url(world, f"/officers/{plain['id']}"), json={"custom_title": "Pastor"},
                             headers=world["director"]["headers"])
    assert bad.status_code == 422 and bad.json()["detail"] == "custom_title sólo se usa con el cargo OTRO"
    assert (await client.patch(url, json={"until": later},
                               headers=world["student"]["headers"])).status_code == 403
    audits = await fetch_one(
        "SELECT count(*) AS n FROM audit_log WHERE entity_id = :id AND action = 'OFFICER_UPDATE'",
        id=other["id"],
    )
    assert audits["n"] == 2


async def test_an_officer_needs_an_active_membership_of_this_club(client, world, factory):
    foreign = await client.post(
        _url(world, "/officers"),
        json={"membership_id": world["outsider"]["membership_id"], "title": "TESORERO"},
        headers=world["director"]["headers"],
    )
    assert foreign.status_code == 404
    gone = await factory.user("gone", "STUDENT")
    await _membership(gone, world["club"], status="ENDED")
    ended = await client.post(
        _url(world, "/officers"), json={"membership_id": gone["membership_id"], "title": "TESORERO"},
        headers=world["director"]["headers"],
    )
    assert ended.status_code == 400
    assert ended.json()["detail"] == "Sólo un miembro activo del club puede tener un cargo"


# ----------------------------------------------------------------------------
# 2. Reuniones y asistencia
# ----------------------------------------------------------------------------
async def test_meeting_creation_matrix_and_uniqueness(client, world):
    held_on = TODAY - timedelta(days=3)
    created = await _meeting(client, world, "director", held_on=held_on, title="Investidura")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["held_on"] == held_on.isoformat() and body["kind"] == "REUNION"
    assert body["counts"] == {"present": 0, "absent": 0, "justified": 0}

    duplicate = await _meeting(client, world, "secretary", held_on=held_on, title="  investidura ")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Ya existe una reunión con esa fecha, tipo y título"
    assert (await _meeting(client, world, "secretary", held_on=held_on, kind="CAMPAMENTO",
                           title="Investidura")).status_code == 201
    assert (await _meeting(client, world, "counselor", held_on=held_on)).status_code == 201
    for actor in ("student", "stranger", "instructor", "director_b", "admin"):
        denied = await _meeting(client, world, actor, held_on=held_on)
        assert denied.status_code == 403, (actor, denied.text)
        assert denied.json()["detail"] == "No tienes permiso para pasar lista en este club"
    audit = await fetch_one(
        "SELECT action FROM audit_log WHERE entity_type = 'CLUB_MEETING' AND entity_id = :id",
        id=body["id"],
    )
    assert audit["action"] == "MEETING_CREATE"


async def test_attendance_is_idempotent_and_mirrors_activity_logs(client, world):
    held_on = TODAY - timedelta(days=10)
    meeting = (await _meeting(client, world, held_on=held_on)).json()
    student_id = uuid.UUID(world["student"]["id"])

    async def attendance_xp() -> int:
        me = await client.get("/api/v1/profiles/me/xp", headers=world["student"]["headers"])
        return me.json()["by_source"]["attendance"]

    xp_before = await attendance_xp()
    first = await _attendance(client, world, meeting["id"], "director",
                              {"student": "PRESENT", "mate": "ABSENT", "minor": "JUSTIFIED"})
    assert first.status_code == 200, first.text
    assert first.json()["counts"] == {"present": 1, "absent": 1, "justified": 1}
    logs = await _meeting_logs(meeting["id"])
    assert len(logs) == 1
    log = logs[0]
    assert log["user_id"] == student_id and log["category"] == "ATTENDANCE"
    assert log["status"] == "APPROVED" and float(log["quantity"]) == 1.0
    assert log["performed_on"] == held_on and str(log["club_id"]) == world["club"]["id"]
    assert await attendance_xp() == xp_before + 5

    # The same list again changes nothing and leaves no new audit row.
    audits = await fetch_one("SELECT count(*) AS n FROM audit_log WHERE entity_id = :id", id=meeting["id"])
    again = await _attendance(client, world, meeting["id"], "director",
                              {"student": "PRESENT", "mate": "ABSENT", "minor": "JUSTIFIED"})
    assert again.status_code == 200
    assert len(await _meeting_logs(meeting["id"])) == 1
    assert (await fetch_one("SELECT count(*) AS n FROM audit_log WHERE entity_id = :id",
                            id=meeting["id"]))["n"] == audits["n"]

    # Present -> absent removes the log, and the XP follows at once.
    changed = await _attendance(client, world, meeting["id"], "secretary",
                                {"student": "ABSENT", "mate": "PRESENT"})
    assert changed.status_code == 200, changed.text
    logs = await _meeting_logs(meeting["id"])
    assert [row["user_id"] for row in logs] == [uuid.UUID(world["mate"]["id"])]
    assert await attendance_xp() == xp_before
    # The minor was left out of the list: their entry is gone (the list is REPLACED).
    rows = await fetch_all("SELECT membership_id FROM club_attendance WHERE meeting_id = :m",
                           m=uuid.UUID(meeting["id"]))
    assert {str(row["membership_id"]) for row in rows} == {
        world["student"]["membership_id"], world["mate"]["membership_id"]}
    changes = await fetch_all(
        "SELECT metadata_json FROM audit_log WHERE entity_id = :id AND action = 'ATTENDANCE_RECORD'",
        id=meeting["id"],
    )
    # Three entries the first time; then the student, the mate and the removed minor.
    assert len(changes) == 6


async def test_attendance_matrix(client, world):
    meeting = (await _meeting(client, world)).json()
    for actor in ("director", "secretary"):
        done = await _attendance(client, world, meeting["id"], actor, {"mate": "PRESENT"})
        assert done.status_code == 200, (actor, done.text)
    for actor in ("instructor", "student", "stranger", "director_b", "admin", "master"):
        denied = await _attendance(client, world, meeting["id"], actor, {"mate": "PRESENT"})
        assert denied.status_code == 403, (actor, denied.text)
        assert denied.json()["detail"] == "No tienes permiso para pasar lista en este club"
    # Another club's meeting is not found from here.
    other = await client.put(
        f"{CLUBS}/{world['other_club']['id']}/meetings/{meeting['id']}/attendance",
        json={"entries": []}, headers=world["director_b"]["headers"],
    )
    assert other.status_code == 404
    # A membership of another club, a repeated entry, a meeting in the future.
    foreign = await client.put(
        _url(world, f"/meetings/{meeting['id']}/attendance"),
        json={"entries": [{"membership_id": world["outsider"]["membership_id"], "status": "PRESENT"}]},
        headers=world["director"]["headers"],
    )
    assert foreign.status_code == 404
    twice = await client.put(
        _url(world, f"/meetings/{meeting['id']}/attendance"),
        json={"entries": [{"membership_id": world["mate"]["membership_id"], "status": "PRESENT"},
                          {"membership_id": world["mate"]["membership_id"], "status": "ABSENT"}]},
        headers=world["director"]["headers"],
    )
    assert twice.status_code == 422
    future = (await _meeting(client, world, held_on=TODAY + timedelta(days=5))).json()
    early = await _attendance(client, world, future["id"], "director", {"mate": "PRESENT"})
    assert early.status_code == 422 and early.json()["detail"] == "No se pasa lista de una reunión futura"


async def test_a_counselor_records_their_unit_only(client, world):
    meeting = (await _meeting(client, world)).json()
    await _attendance(client, world, meeting["id"], "director",
                      {"mate": "PRESENT", "student": "ABSENT"})
    outside = await _attendance(client, world, meeting["id"], "counselor", {"mate": "ABSENT"})
    assert outside.status_code == 403
    assert outside.json()["detail"] == "Sólo puedes pasar lista de los miembros de tu unidad"

    # The counselor's list replaces THEIR unit's entries and leaves the rest alone.
    mine = await _attendance(client, world, meeting["id"], "counselor", {"minor": "PRESENT"})
    assert mine.status_code == 200, mine.text
    rows = {str(row["membership_id"]): row["status"] for row in await fetch_all(
        "SELECT membership_id, status FROM club_attendance WHERE meeting_id = :m",
        m=uuid.UUID(meeting["id"]))}
    assert rows == {world["mate"]["membership_id"]: "PRESENT", world["minor"]["membership_id"]: "PRESENT"}
    users = {row["user_id"] for row in await _meeting_logs(meeting["id"])}
    assert users == {uuid.UUID(world["mate"]["id"]), uuid.UUID(world["minor"]["id"])}
    # The detail a counselor reads is their unit.
    detail = await client.get(_url(world, f"/meetings/{meeting['id']}"),
                              headers=world["counselor"]["headers"])
    assert detail.status_code == 200
    assert {row["user_id"] for row in detail.json()["attendees"]} == {
        world["student"]["id"], world["minor"]["id"]}


async def test_nobody_records_their_own_attendance(client, world):
    meeting = (await _meeting(client, world)).json()
    own = await _attendance(client, world, meeting["id"], "secretary", {"secretary": "PRESENT"})
    assert own.status_code == 403
    assert own.json()["detail"] == "Nadie pasa lista de sí mismo: otra persona del club marca tu asistencia"
    # The director marks the secretary; the secretary's own list then leaves that entry alone.
    await _attendance(client, world, meeting["id"], "director", {"secretary": "PRESENT"})
    await _attendance(client, world, meeting["id"], "secretary", {"mate": "PRESENT"})
    statuses = {str(row["membership_id"]) for row in await fetch_all(
        "SELECT membership_id FROM club_attendance WHERE meeting_id = :m", m=uuid.UUID(meeting["id"]))}
    assert world["secretary"]["membership_id"] in statuses


async def test_meeting_detail_and_list(client, world):
    held_on = TODAY - timedelta(days=20)
    meeting = (await _meeting(client, world, held_on=held_on, kind="SERVICIO", title="Comedor")).json()
    await _attendance(client, world, meeting["id"], "director",
                      {"student": "PRESENT", "mate": "JUSTIFIED"})
    detail = await client.get(_url(world, f"/meetings/{meeting['id']}"),
                              headers=world["secretary"]["headers"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["title"] == "Comedor" and body["kind"] == "SERVICIO"
    by_user = {row["user_id"]: row for row in body["attendees"]}
    # Every ACTIVE member of the club, with their status (or null when not on the list).
    assert by_user[world["student"]["id"]]["status"] == "PRESENT"
    assert by_user[world["mate"]["id"]]["status"] == "JUSTIFIED"
    assert by_user[world["minor"]["id"]]["status"] is None
    assert by_user[world["student"]["id"]]["unit"]["id"] == world["u1"]
    assert "email" not in detail.text
    assert world["outsider"]["id"] not in by_user

    listed = await client.get(
        _url(world, f"/meetings?from={held_on.isoformat()}&to={held_on.isoformat()}"),
        headers=world["instructor"]["headers"],
    )
    assert listed.status_code == 200
    rows = [row for row in listed.json() if row["id"] == meeting["id"]]
    assert rows and rows[0]["counts"] == {"present": 1, "absent": 0, "justified": 1}
    assert all(row["held_on"] == held_on.isoformat() for row in listed.json())
    for actor in ("student", "stranger", "director_b"):
        assert (await client.get(_url(world, "/meetings"),
                                 headers=world[actor]["headers"])).status_code == 403
        assert (await client.get(_url(world, f"/meetings/{meeting['id']}"),
                                 headers=world[actor]["headers"])).status_code == 403


async def test_attendance_summary_by_member_and_unit(client, world, factory):
    # A club of its own, so the numbers are exact.
    club = await factory.org("club-sum", "club", world["association"])
    director = await factory.user("sum-director", "CLUB_DIRECTOR", club["id"])
    a = await factory.user("sum-a", "STUDENT", club["id"])
    b = await factory.user("sum-b", "STUDENT", club["id"])
    unit = await _unit(club, factory.name("Leones"))
    await _membership(director, club, role="CLUB_DIRECTOR")
    await _membership(a, club, unit_id=unit)
    await _membership(b, club)
    local = {"director": director, "a": a, "b": b, "club": club}
    days = [TODAY - timedelta(days=d) for d in (30, 20, 10)]
    for index, held_on in enumerate(days):
        meeting = (await client.post(
            f"{CLUBS}/{club['id']}/meetings",
            json={"held_on": held_on.isoformat(), "kind": "REUNION", "title": f"S{index}"},
            headers=director["headers"],
        )).json()
        entries = {"a": "PRESENT", "b": "PRESENT" if index == 0 else "ABSENT"}
        if index == 2:
            entries["a"] = "JUSTIFIED"
        await _attendance(client, local, meeting["id"], "director", entries)

    summary = await client.get(
        f"{CLUBS}/{club['id']}/attendance/summary?from={days[0].isoformat()}&to={TODAY.isoformat()}",
        headers=director["headers"],
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["meetings"] == 3
    members = {row["user_id"]: row for row in body["members"]}
    assert (members[a["id"]]["meetings"], members[a["id"]]["present"]) == (3, 2)
    assert members[a["id"]]["pct"] == pytest.approx(66.7, abs=0.05)
    assert (members[b["id"]]["meetings"], members[b["id"]]["present"]) == (3, 1)
    assert members[director["id"]]["meetings"] == 0 and members[director["id"]]["pct"] is None
    units = {row["unit_id"]: row for row in body["units"]}
    assert units[unit]["present"] == 2 and units[unit]["meetings"] == 3
    # A window that only holds the last meeting.
    last = (await client.get(
        f"{CLUBS}/{club['id']}/attendance/summary?from={days[2].isoformat()}",
        headers=director["headers"],
    )).json()
    assert last["meetings"] == 1
    assert {row["user_id"]: row["present"] for row in last["members"]}[a["id"]] == 0
    assert (await client.get(f"{CLUBS}/{club['id']}/attendance/summary",
                             headers=world["stranger"]["headers"])).status_code == 403


# ----------------------------------------------------------------------------
# can_approve_activity: the secretary registers attendance, never service hours
# ----------------------------------------------------------------------------
async def test_the_secretary_records_attendance_but_not_service_hours(client, world):
    base = {"performed_on": (TODAY - timedelta(days=2)).isoformat(), "quantity": 1,
            "description": "Reunión del sábado", "user_ids": [world["mate"]["id"]]}
    attendance = await client.post("/api/v1/activity/logs", json={**base, "category": "ATTENDANCE"},
                                   headers=world["secretary"]["headers"])
    assert attendance.status_code == 201, attendance.text
    assert attendance.json()[0]["status"] == "APPROVED"
    service = await client.post("/api/v1/activity/logs", json={**base, "category": "SERVICE"},
                                headers=world["secretary"]["headers"])
    assert service.status_code == 403

    # Deciding: an ATTENDANCE the member submitted, yes; their SERVICE hours, no.
    own = {"performed_on": base["performed_on"], "quantity": 1, "description": "Lo mío"}
    submitted_attendance = (await client.post("/api/v1/activity/logs", json={**own, "category": "ATTENDANCE"},
                                              headers=world["mate"]["headers"])).json()[0]
    submitted_service = (await client.post("/api/v1/activity/logs", json={**own, "category": "SERVICE"},
                                           headers=world["mate"]["headers"])).json()[0]
    ok = await client.post(f"/api/v1/activity/logs/{submitted_attendance['id']}/decision",
                           json={"status": "APPROVED"}, headers=world["secretary"]["headers"])
    assert ok.status_code == 200, ok.text
    no = await client.post(f"/api/v1/activity/logs/{submitted_service['id']}/decision",
                           json={"status": "APPROVED"}, headers=world["secretary"]["headers"])
    assert no.status_code == 403
    assert (await client.post(f"/api/v1/activity/logs/{submitted_service['id']}/decision",
                              json={"status": "APPROVED"},
                              headers=world["director"]["headers"])).status_code == 200


# ----------------------------------------------------------------------------
# 3. Nómina: cargos, completitud y asistencia de 90 días; CSV
# ----------------------------------------------------------------------------
async def test_roster_carries_titles_flags_and_attendance(client, world, factory):
    club = await factory.org("club-roster", "club", world["association"])
    director = await factory.user("roster-director", "CLUB_DIRECTOR", club["id"])
    secretary = await factory.user("roster-secretary", "CLUB_SECRETARY", club["id"])
    adult = await factory.user("roster-adult", "STUDENT", club["id"])
    kid = await factory.user("roster-kid", "STUDENT", club["id"], is_minor=True)
    guardian = await factory.user("roster-guardian", "PARENT_GUARDIAN")
    await _set(adult, birth_date=date(1995, 1, 1), verification_status="VERIFIED")
    await _set(kid, birth_date=date(2013, 1, 1))
    await _exec(
        "INSERT INTO guardianships (id, guardian_id, child_id, relationship, consent_status)"
        " VALUES (gen_random_uuid(), :g, :c, 'PARENT', 'APPROVED')",
        g=uuid.UUID(guardian["id"]), c=uuid.UUID(kid["id"]),
    )
    await _membership(director, club, role="CLUB_DIRECTOR")
    await _membership(secretary, club, role="CLUB_SECRETARY")
    await _membership(adult, club)
    await _membership(kid, club)  # no consent recorded on this membership
    local = {"director": director, "adult": adult, "kid": kid, "club": club}
    base = f"{CLUBS}/{club['id']}"
    await client.post(f"{base}/officers", json={"membership_id": adult["membership_id"], "title": "TESORERO"},
                      headers=director["headers"])
    await client.post(f"{base}/officers", json={"membership_id": adult["membership_id"], "title": "OTRO",
                                                "custom_title": "Corneta"}, headers=director["headers"])
    for index, status in enumerate(("PRESENT", "ABSENT", "PRESENT", "PRESENT")):
        meeting = (await client.post(
            f"{base}/meetings",
            json={"held_on": (TODAY - timedelta(days=5 + index)).isoformat(), "title": f"R{index}"},
            headers=director["headers"],
        )).json()
        await _attendance(client, local, meeting["id"], "director", {"adult": status})
    # An old meeting (outside the 90 days) does not count.
    old = (await client.post(f"{base}/meetings", json={"held_on": (TODAY - timedelta(days=120)).isoformat()},
                             headers=director["headers"])).json()
    await _attendance(client, local, old["id"], "director", {"adult": "ABSENT"})

    rows = {row["user_id"]: row for row in (await client.get(f"{base}/members",
                                                             headers=secretary["headers"])).json()}
    adult_row, kid_row = rows[adult["id"]], rows[kid["id"]]
    assert sorted(adult_row["officer_titles"]) == ["Corneta", "TESORERO"]
    assert adult_row["completeness"] == {"birth_date": True, "consent": True, "guardian": True,
                                         "email_verified": True}
    assert adult_row["attendance_pct_90d"] == 75.0
    assert kid_row["officer_titles"] == [] and kid_row["attendance_pct_90d"] is None
    assert kid_row["completeness"] == {"birth_date": True, "consent": False, "guardian": True,
                                       "email_verified": False}
    # A role change answers with the same row shape.
    changed = await client.patch(f"{base}/members/{adult['membership_id']}", json={"role": "STUDENT"},
                                 headers=director["headers"])
    assert changed.status_code == 200 and changed.json()["attendance_pct_90d"] == 75.0


async def test_roster_extras_add_no_query_per_row(client, world, factory):
    url = _url(world, "/members")

    async def statements() -> int:
        seen = []

        def count(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            assert (await client.get(url, headers=world["director"]["headers"])).status_code == 200
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        return len(seen)

    before = await statements()
    meeting = (await _meeting(client, world)).json()
    local = dict(world)
    entries = {}
    for index in range(3):
        adult = await factory.user(f"np1-{index}", "STUDENT", world["club"]["id"])
        await _membership(adult, world["club"], unit_id=world["u2"])
        local[f"np1-{index}"] = adult
        entries[f"np1-{index}"] = "PRESENT"
        await client.post(_url(world, "/officers"),
                          json={"membership_id": adult["membership_id"], "title": "INSTRUCTOR"},
                          headers=world["director"]["headers"])
    await _attendance(client, local, meeting["id"], "director", entries)
    assert await statements() == before


def _csv(response) -> list[list[str]]:
    raw = response.content
    assert raw.startswith("﻿".encode()), raw[:10]
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


async def test_the_directors_csv_has_contact_columns(client, world):
    response = await client.get(_url(world, "/members/export.csv"), headers=world["director"]["headers"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith("attachment; filename=")
    rows = _csv(response)
    assert rows[0] == ["Nombre", "Cargo", "Rol", "Unidad", "Edad", "Estado", "Fecha de ingreso",
                       "Asistencia 90 d (%)", "Correo", "Tutor"]
    by_name = {row[0]: row for row in rows[1:]}
    student = by_name[f"{factory_prefix(world)} student"]
    assert student[8] == world["student"]["email"] and student[4] == str(TODAY.year - 1990 - (
        0 if (TODAY.month, TODAY.day) >= (5, 17) else 1))
    minor = by_name[f"{factory_prefix(world)} minor"]
    # A minor's e-mail never leaves the backend; their guardian's name does, for the director.
    assert minor[8] == "" and minor[9] == f"{factory_prefix(world)} guardian"
    audit = await fetch_one(
        "SELECT action, entity_type, metadata_json FROM audit_log WHERE entity_type = 'CLUB_ROSTER'"
        " AND entity_id = :id AND user_id = :u ORDER BY created_at DESC LIMIT 1",
        id=world["club"]["id"], u=uuid.UUID(world["director"]["id"]),
    )
    assert audit["action"] == "EXPORT" and audit["metadata_json"]["with_contact"] is True


def factory_prefix(world) -> str:
    return world["student"]["email"].split("-student@")[0]


async def test_the_secretarys_csv_is_redacted(client, world):
    response = await client.get(_url(world, "/members/export.csv"), headers=world["secretary"]["headers"])
    assert response.status_code == 200, response.text
    rows = _csv(response)
    assert rows[0] == ["Nombre", "Cargo", "Rol", "Unidad", "Edad", "Estado", "Fecha de ingreso",
                       "Asistencia 90 d (%)"]
    assert "@" not in response.content.decode("utf-8-sig")
    assert f"{factory_prefix(world)} guardian" not in response.content.decode("utf-8-sig")
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE entity_type = 'CLUB_ROSTER' AND action = 'EXPORT'"
        " AND user_id = :u", u=uuid.UUID(world["secretary"]["id"]),
    )
    assert audit["metadata_json"]["with_contact"] is False
    assert (await client.get(_url(world, "/members/export.csv"),
                             headers=world["admin"]["headers"])).status_code == 200
    for actor in ("counselor", "instructor", "student", "stranger", "director_b", "admin_b"):
        denied = await client.get(_url(world, "/members/export.csv"), headers=world[actor]["headers"])
        assert denied.status_code == 403, actor


async def test_csv_cells_never_start_a_formula(client, world, factory):
    evil = await factory.user("formula", "STUDENT", world["club"]["id"])
    await _set(evil, name="=HYPERLINK(\"http://x\")")
    await _membership(evil, world["club"])
    rows = _csv(await client.get(_url(world, "/members/export.csv"), headers=world["director"]["headers"]))
    names = [row[0] for row in rows[1:]]
    assert "'=HYPERLINK(\"http://x\")" in names
    await _set(evil, name=factory.name("formula"))


# ----------------------------------------------------------------------------
# 4. Renovar el enlace multiuso
# ----------------------------------------------------------------------------
async def test_the_secretary_renews_a_multi_use_link(client, world):
    created = await client.post(_url(world, "/invitations"),
                                json={"role": "STUDENT", "max_uses": 50, "unit_id": world["u1"]},
                                headers=world["secretary"]["headers"])
    assert created.status_code == 201, created.text
    old = created.json()["invitation"]
    renewed = await client.post(_url(world, f"/invitations/{old['id']}/renew"),
                                headers=world["secretary"]["headers"])
    assert renewed.status_code == 201, renewed.text
    body = renewed.json()
    new = body["invitation"]
    assert new["id"] != old["id"] and body["token"] != created.json()["token"]
    assert (new["role"], new["max_uses"], new["unit_id"], new["uses"]) == ("STUDENT", 50, world["u1"], 0)
    assert new["state"] == "ACTIVE" and new["requires_approval"] is True
    row = await fetch_one("SELECT revoked_at FROM club_invitations WHERE id = :id", id=uuid.UUID(old["id"]))
    assert row["revoked_at"] is not None
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'INVITATION_RENEW' AND entity_id = :id",
        id=new["id"],
    )
    assert audit["metadata_json"]["renewed_from"] == old["id"]

    single = (await client.post(_url(world, "/invitations"), json={"role": "STUDENT"},
                                headers=world["secretary"]["headers"])).json()["invitation"]
    refused = await client.post(_url(world, f"/invitations/{single['id']}/renew"),
                                headers=world["secretary"]["headers"])
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Sólo se renuevan los enlaces multiuso del club"
    for actor in ("student", "counselor", "director_b"):
        assert (await client.post(_url(world, f"/invitations/{new['id']}/renew"),
                                  headers=world[actor]["headers"])).status_code == 403
    assert (await client.post(_url(world, f"/invitations/{uuid.uuid4()}/renew"),
                              headers=world["director"]["headers"])).status_code == 404


# ----------------------------------------------------------------------------
# 5. Página pública del club
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


async def _upload(client, user, folder):
    return await client.post("/api/v1/media/upload", files={"file": ("logo.png", PNG, "image/png")},
                             data={"folder": folder}, headers=user["headers"])


async def test_direction_and_secretary_upload_the_logo(client, world, r2):
    for actor in ("director", "secretary"):
        uploaded = await _upload(client, world[actor], "logos")
        assert uploaded.status_code == 201, uploaded.text
        assert re.fullmatch(r"logos/[0-9a-f]{32}\.png", uploaded.json()["key"])
        # ...and only there.
        assert (await _upload(client, world[actor], "patches")).status_code == 403
    assert (await _upload(client, world["student"], "logos")).status_code == 403
    assert (await _upload(client, world["counselor"], "logos")).status_code == 403


async def test_profile_patch_description_and_logo(client, world):
    logo = f"{MEDIA}/logos/{'a' * 32}.png"
    # 022: the logo is the director's (or the association's), never the secretary's.
    refused = await client.patch(
        _url(world, "/profile"), json={"logo_url": logo}, headers=world["secretary"]["headers"]
    )
    assert refused.status_code == 403 and refused.json()["detail"] == "club_logo_forbidden"
    described = await client.patch(
        _url(world, "/profile"),
        json={"description": "  Un club   de exploradores. ", "meeting_day": "Sábado"},
        headers=world["secretary"]["headers"],
    )
    assert described.status_code == 200, described.text
    patched = await client.patch(
        _url(world, "/profile"), json={"logo_url": logo}, headers=world["director"]["headers"]
    )
    assert patched.status_code == 200, patched.text
    profile = patched.json()["profile"]
    assert profile["description"] == "Un club de exploradores." and profile["logo_url"] == logo
    assert patched.json()["logo_url"] == logo
    for bad in (f"{MEDIA}/avatars/x.png", "https://evil.example/logos/x.png", f"{MEDIA}/logos/"):
        refused = await client.patch(_url(world, "/profile"), json={"logo_url": bad},
                                     headers=world["director"]["headers"])
        assert refused.status_code == 422, bad
    too_long = await client.patch(_url(world, "/profile"), json={"description": "x" * 601},
                                  headers=world["director"]["headers"])
    assert too_long.status_code == 422
    assert (await client.patch(_url(world, "/profile"), json={"description": "hola"},
                               headers=world["counselor"]["headers"])).status_code == 403


async def test_the_public_profile_of_a_club(client, world, factory):
    await client.patch(_url(world, "/profile"),
                       json={"description": "Club de prueba", "meeting_time": "15:00",
                             "contact": "Salón de la iglesia", "accepts_requests": True},
                       headers=world["director"]["headers"])
    officer = await _officer(client, world, "director", "student", "SECRETARIO")
    assert officer.status_code == 201
    response = await client.get(_url(world, "/profile"))  # no session at all
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == world_name(world) and body["city"] == "Ciudad Club"
    assert body["church"] == "Iglesia Central"
    assert body["association"]["id"] == world["association"]["id"] and body["zone"] is None
    assert body["description"] == "Club de prueba" and body["meeting_time"] == "15:00"
    assert body["contact"] == "Salón de la iglesia" and body["accepts_requests"] is True
    active = await fetch_one(
        "SELECT count(*) AS n FROM club_memberships WHERE club_id = :c AND status = 'ACTIVE'",
        c=uuid.UUID(world["club"]["id"]),
    )
    assert body["active_members"] == active["n"]
    assert {"title": "SECRETARIO", "custom_title": None,
            "name": f"{factory_prefix(world)} student"} in body["officers"]
    for row in body["officers"]:
        assert set(row) == {"title", "custom_title", "name"}
    # Never an e-mail, and never a minor's name (the minor holds cargos above).
    assert "@" not in response.text
    assert f"{factory_prefix(world)} minor" not in response.text


def world_name(world) -> str:
    return f"{factory_prefix(world)} club"


async def test_the_public_profile_only_exists_for_active_clubs(client, world, factory):
    pending = await factory.org("club-pending", "club", world["association"])
    await _exec("UPDATE organizations SET status = 'pending' WHERE id = :id", id=uuid.UUID(pending["id"]))
    assert (await client.get(f"{CLUBS}/{pending['id']}/profile")).status_code == 404
    assert (await client.get(f"{CLUBS}/{world['association']['id']}/profile")).status_code == 404
    assert (await client.get(f"{CLUBS}/{uuid.uuid4()}/profile")).status_code == 404


# ----------------------------------------------------------------------------
# 6. Puntuación del club
# ----------------------------------------------------------------------------
async def test_the_score_adds_awards_attendance_and_badges(client, world, factory):
    club = await factory.org("club-score", "club", world["association"])
    director = await factory.user("score-director", "CLUB_DIRECTOR", club["id"])
    a = await factory.user("score-a", "STUDENT", club["id"])
    b = await factory.user("score-b", "STUDENT", club["id"])
    unit = await _unit(club, factory.name("Pumas"))
    await _membership(director, club, role="CLUB_DIRECTOR")
    await _membership(a, club, unit_id=unit)
    await _membership(b, club)
    local = {"director": director, "a": a, "b": b, "club": club}
    season = 2025

    async def award(user, points, occurred_on, club_id=club["id"]):
        await _exec(
            "INSERT INTO xp_awards (id, user_id, club_id, awarded_by_id, category, points, occurred_on)"
            " VALUES (gen_random_uuid(), :u, :c, :by, 'participacion', :p, :d)",
            u=uuid.UUID(user["id"]), c=uuid.UUID(club_id), by=uuid.UUID(director["id"]),
            p=points, d=occurred_on,
        )

    await award(a, 20, date(2025, 3, 5))
    await award(a, -5, date(2025, 3, 6))            # penalties do not count
    await award(b, 10, date(2025, 4, 1))
    await award(b, 30, date(2024, 12, 31))          # another season
    await award(b, 40, date(2025, 4, 2), world["club"]["id"])  # another club
    for held_on, entries in ((date(2025, 3, 8), {"a": "PRESENT", "b": "PRESENT"}),
                             (date(2025, 4, 12), {"a": "PRESENT", "b": "ABSENT"})):
        meeting = (await client.post(f"{CLUBS}/{club['id']}/meetings",
                                     json={"held_on": held_on.isoformat()},
                                     headers=director["headers"])).json()
        await _attendance(client, local, meeting["id"], "director", entries)
    # A badge: a certified honor of skill level 2 earned in this club (100 + 50).
    async with SessionLocal() as db:
        ministry = await db.scalar(text("SELECT id FROM ministries WHERE slug = 'pathfinders'"))
        template = await db.scalar(text("SELECT id FROM certificate_templates ORDER BY created_at LIMIT 1"))
        if template is None:
            template = uuid.uuid4()
            await db.execute(text("INSERT INTO certificate_templates (id, name, width, height)"
                                  " VALUES (:id, 'zz-secretaria', 11, 8.5)"), {"id": template})
        honor, enrollment = uuid.uuid4(), uuid.uuid4()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, name, slug, code, status, active, skill_level)"
            " VALUES (:id, :m, :name, :slug, :code, 'PUBLISHED', true, 2)"
        ), {"id": honor, "m": ministry, "name": factory.name("Nudos"), "slug": f"{factory.prefix}-nudos",
            "code": f"{factory.prefix}-nudos"})
        await db.execute(text(
            "INSERT INTO honor_enrollments (id, user_id, honor_id, status, mode, club_id)"
            " VALUES (:id, :u, :h, 'CERTIFIED', 'CLUB', :c)"
        ), {"id": enrollment, "u": uuid.UUID(a["id"]), "h": honor, "c": uuid.UUID(club["id"])})
        await db.execute(text(
            "INSERT INTO certificates (id, ministry_id, organization_id, honor_id, template_id,"
            " certificate_no, recipient_name, honor_name_snapshot, issued_date, status, user_id,"
            " enrollment_id) VALUES (gen_random_uuid(), :m, :org, :h, :t, :no, :rn, 'snap',"
            " :d, 'issued', :u, :e)"
        ), {"m": ministry, "org": uuid.UUID(club["id"]), "h": honor, "t": template,
            "no": f"{factory.prefix}-{uuid.uuid4().hex[:8]}", "rn": factory.name("score-a"),
            "d": date(2025, 5, 20), "u": uuid.UUID(a["id"]), "e": enrollment})
        await db.commit()

    response = await client.get(f"{CLUBS}/{club['id']}/score?season={season}", headers=director["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["season"] == season
    assert body["by_source"] == {"awards": 30, "attendance": 15, "badges": 150}
    assert body["total"] == 195
    units = {row["unit_id"]: row["total"] for row in body["units"]}
    assert units == {unit: 20 + 10 + 150, None: 10 + 5}
    months = {row["month"]: row["total"] for row in body["months"]}
    assert months == {"2025-03": 20 + 10, "2025-04": 10 + 5, "2025-05": 150}

    # Who reads it: the club (its members too) and its hierarchy.
    for actor in (a, b):
        assert (await client.get(f"{CLUBS}/{club['id']}/score?season={season}",
                                 headers=actor["headers"])).status_code == 200
    assert (await client.get(f"{CLUBS}/{club['id']}/score?season={season}",
                             headers=world["admin"]["headers"])).status_code == 200
    for actor in ("stranger", "director_b", "admin_b", "outsider"):
        denied = await client.get(f"{CLUBS}/{club['id']}/score?season={season}",
                                  headers=world[actor]["headers"])
        assert denied.status_code == 403, actor
        assert denied.json()["detail"] == "No tienes permiso para ver la puntuación de este club"
    assert (await client.get(f"{CLUBS}/{club['id']}/score?season=1900",
                             headers=director["headers"])).status_code == 422
    default = await client.get(f"{CLUBS}/{club['id']}/score", headers=director["headers"])
    assert default.status_code == 200 and default.json()["season"] == TODAY.year


async def test_reading_matrix_of_the_secretariat(client, world):
    """Meetings, their detail, the summary and the cargos are read like the roster: the staff
    and the hierarchy in scope; never a member, a stranger or another club."""
    meeting = (await _meeting(client, world)).json()
    paths = ["/meetings", f"/meetings/{meeting['id']}", "/attendance/summary", "/officers"]
    for actor in ("director", "secretary", "counselor", "instructor", "admin", "master"):
        for path in paths:
            response = await client.get(_url(world, path), headers=world[actor]["headers"])
            assert response.status_code == 200, (actor, path, response.text)
    for actor in ("student", "minor", "stranger", "director_b", "admin_b", "guardian"):
        for path in paths:
            response = await client.get(_url(world, path), headers=world[actor]["headers"])
            assert response.status_code == 403, (actor, path)
            assert response.json()["detail"] == "No tienes permiso para ver la nómina de este club"
    for path in paths:
        assert (await client.get(_url(world, path))).status_code == 401
