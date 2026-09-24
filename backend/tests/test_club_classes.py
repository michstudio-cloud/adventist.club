"""Bloque F · F3 — el club sigue una CLASE en grupo.

Spec: docs/superpowers/specs/2026-09-22-guias-mayores-aventureros-design.md §1.8.

  * inscribir al club (o a una unidad, o a una lista) reutiliza la inscripción del bloque A:
    las filas de progreso son exactamente las de una auto-inscripción, y es idempotente;
  * la matriz miembros × requisitos cuesta un número fijo de consultas;
  * la «firma en bloque» marca COMPLETE sólo lo que el líder alcanza, omite (no falla) el
    resto, deja `decided_via: block_sign` en la auditoría y lleva la inscripción a READY
    por la misma regla del bloque A;
  * la investidura es del director y sólo de inscripciones READY.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import event, text

from app.config import settings
from app.db import SessionLocal, engine
from tests.conftest import DB_AVAILABLE, fetch_all, fetch_one, module_factory, requires_db

CLUBS = "/api/v1/clubs"
ENROLLMENTS = "/api/v1/portfolio/enrollments"


def _tables_exist() -> bool:
    import asyncio

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.programs') IS NOT NULL"
                    " AND to_regclass('public.club_units') IS NOT NULL"
                    " AND to_regclass('public.honor_enrollments') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(not _tables_exist(), reason="apply migrations 008d and 012 to the test database"),
]
factory = module_factory("clubclasses")


# ----------------------------------------------------------------------------
# Rows written directly
# ----------------------------------------------------------------------------
async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _set(user: dict, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    await _exec(f"UPDATE users SET {assignments} WHERE id = :id", id=uuid.UUID(user["id"]), **columns)


async def _membership(user: dict, club: dict, *, role="STUDENT", unit_id=None, status="ACTIVE") -> str:
    membership_id = uuid.uuid4()
    await _exec(
        "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, unit_id,"
        " started_at) VALUES (:id, :user, :club, :role, :status, 'ADMIN', :unit,"
        " now() - interval '1 year')",
        id=membership_id, user=uuid.UUID(user["id"]), club=uuid.UUID(club["id"]), role=role,
        status=status, unit=uuid.UUID(unit_id) if unit_id else None,
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


async def _program(factory, label: str, *, sections=(("a", 2), ("b", 1)), status="PUBLISHED",
                   kind="CLASS", issuer_level="CLUB", evidence_positions=(), hours_positions=(),
                   ministry="pathfinders"):
    """A program of `ministry` with `sections` = ((slug, how many), ...), numbered globally."""
    program_id = uuid.uuid4()
    requirements = []
    position = 0
    async with SessionLocal() as db:
        ministry_id = (await db.execute(
            text("SELECT id FROM ministries WHERE slug = :m"), {"m": ministry})).scalar_one()
        await db.execute(text(
            "INSERT INTO programs (id, ministry_id, kind, slug, name, status, issuer_level, sort_order)"
            " VALUES (:id, :m, :k, :slug, :name, :status, :issuer, 1)"),
            {"id": program_id, "m": ministry_id, "k": kind, "slug": f"{factory.prefix}-{label}",
             "name": factory.name(label), "status": status, "issuer": issuer_level})
        for section_position, (slug, count) in enumerate(sections, start=1):
            section_id = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO program_sections (id, program_id, position, slug, name)"
                " VALUES (:id, :p, :pos, :slug, :name)"),
                {"id": section_id, "p": program_id, "pos": section_position, "slug": slug,
                 "name": f"Sección {slug}"})
            for index in range(1, count + 1):
                position += 1
                requirement_id = uuid.uuid4()
                hours = position in hours_positions
                await db.execute(text(
                    "INSERT INTO program_requirements (id, program_id, section_id, position, label,"
                    " kind, evidence_required, target_quantity, activity_category)"
                    " VALUES (:id, :p, :s, :pos, :label, :kind, :ev, :q, :cat)"),
                    {"id": requirement_id, "p": program_id, "s": section_id, "pos": position,
                     "label": f"{section_position}.{index}", "kind": "HOURS" if hours else "FREE",
                     "ev": position in evidence_positions, "q": 5 if hours else None,
                     "cat": "SERVICE" if hours else None})
                await db.execute(text(
                    "INSERT INTO program_requirement_texts (requirement_id, locale, description)"
                    " VALUES (:r, 'es', :d)"), {"r": requirement_id, "d": f"Requisito {position}"})
                requirements.append(str(requirement_id))
        await db.commit()
    return {"id": str(program_id), "slug": f"{factory.prefix}-{label}", "name": factory.name(label),
            "requirements": requirements}


@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    other_club = await factory.org("club-b", "club", association)
    other_assoc = await factory.org("assoc-b", "association")
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    await _exec("UPDATE organizations SET code = :code WHERE id = :id",
                code=issuer_code, id=uuid.UUID(issuer["id"]))
    p = {
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "secretary": await factory.user("secretary", "CLUB_SECRETARY", club["id"]),
        "counselor": await factory.user("counselor", "COUNSELOR", club["id"]),
        "counselor_b": await factory.user("counselor-b", "COUNSELOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "s1": await factory.user("s1", "STUDENT", club["id"], is_minor=True),
        "s2": await factory.user("s2", "STUDENT", club["id"]),
        "s3": await factory.user("s3", "STUDENT", club["id"]),
        "s4": await factory.user("s4", "STUDENT", club["id"]),
        "gone": await factory.user("gone", "STUDENT", club["id"]),
        "outsider": await factory.user("outsider", "STUDENT", other_club["id"]),
        "director_b": await factory.user("director-b", "CLUB_DIRECTOR", other_club["id"]),
        "admin": await factory.user("admin", "ADMIN_ASSOCIATION", association["id"]),
        "admin_b": await factory.user("admin-b", "ADMIN_ASSOCIATION", other_assoc["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    await _set(p["instructor"], verification_status="VERIFIED")
    await _set(p["s1"], avatar_url="https://media.example/avatars/s1.png")
    await _set(p["s2"], avatar_url="https://media.example/avatars/s2.png")
    u1 = await _unit(club, factory.name("Águilas"), p["counselor"])
    u2 = await _unit(club, factory.name("Halcones"), p["counselor_b"])
    await _membership(p["director"], club, role="CLUB_DIRECTOR")
    await _membership(p["secretary"], club, role="CLUB_SECRETARY")
    await _membership(p["counselor"], club, role="COUNSELOR")
    await _membership(p["counselor_b"], club, role="COUNSELOR")
    await _membership(p["instructor"], club, role="INSTRUCTOR")
    await _membership(p["s1"], club, unit_id=u1)
    await _membership(p["s2"], club, unit_id=u1)
    await _membership(p["s3"], club, unit_id=u2)
    await _membership(p["s4"], club)
    await _membership(p["gone"], club, unit_id=u1, status="ENDED")
    await _membership(p["outsider"], other_club)
    await _membership(p["director_b"], other_club, role="CLUB_DIRECTOR")
    return {**p, "club": club, "other_club": other_club, "u1": u1, "u2": u2,
            "issuer_code": issuer_code}


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


def _url(world, program=None, tail="") -> str:
    base = f"{CLUBS}/{world['club']['id']}/classes"
    return f"{base}/{program['id']}{tail}" if program else base


async def _enroll(client, world, program, actor="director", **body):
    return await client.post(_url(world, program, "/enroll"), json=body,
                             headers=world[actor]["headers"])


async def _matrix(client, world, program, actor="director", **params):
    return await client.get(_url(world, program, "/matrix"), params=params,
                            headers=world[actor]["headers"])


async def _sign(client, world, program, entries, actor="director", note=None):
    body = {"entries": entries}
    if note is not None:
        body["note"] = note
    return await client.post(_url(world, program, "/sign"), json=body,
                             headers=world[actor]["headers"])


def _by_membership(rows) -> dict:
    return {row["membership_id"]: row for row in rows}


# ----------------------------------------------------------------------------
# 2. Inscribir al club
# ----------------------------------------------------------------------------
async def test_enrolling_the_club_takes_every_active_student_and_is_idempotent(client, world, factory):
    program = await _program(factory, "todos")
    response = await _enroll(client, world, program)
    assert response.status_code == 200, response.text
    body = response.json()
    enrolled = {row["membership_id"] for row in body["enrolled"]}
    students = {world[k]["membership_id"] for k in ("s1", "s2", "s3", "s4")}
    assert enrolled == students
    assert body["skipped"] == []

    # Progress rows exactly as a self-enrolment would create them.
    for row in body["enrolled"]:
        progress = await fetch_all(
            "SELECT requirement_position, status, kind, program_requirement_id"
            " FROM requirement_progress WHERE enrollment_id = :e ORDER BY requirement_position",
            e=uuid.UUID(row["enrollment_id"]))
        assert [p["requirement_position"] for p in progress] == [1, 2, 3]
        assert {p["status"] for p in progress} == {"PENDING"}
        assert [str(p["program_requirement_id"]) for p in progress] == program["requirements"]
        enrollment = await fetch_one(
            "SELECT e.user_id, e.status, e.mode, e.club_id FROM honor_enrollments e WHERE e.id = :e",
            e=uuid.UUID(row["enrollment_id"]))
        assert enrollment["status"] == "IN_PROGRESS" and enrollment["mode"] == "CLUB"
        assert str(enrollment["club_id"]) == world["club"]["id"]

    audits = await fetch_all(
        "SELECT entity_id, metadata_json FROM audit_log WHERE action = 'ENROLL'"
        " AND user_id = :actor AND metadata_json->>'program_id' = :p",
        actor=uuid.UUID(world["director"]["id"]), p=program["id"])
    assert len(audits) == 4
    assert all(a["metadata_json"]["via"] == "club" for a in audits)
    assert all(a["metadata_json"]["on_behalf"] is True for a in audits)
    assert {a["entity_id"] for a in audits} == {row["enrollment_id"] for row in body["enrolled"]}

    again = (await _enroll(client, world, program)).json()
    assert again["enrolled"] == []
    assert {row["membership_id"]: row["reason"] for row in again["skipped"]} == {
        m: "already_enrolled" for m in students}
    count = await fetch_one(
        "SELECT count(*) AS n FROM honor_enrollments WHERE program_id = :p", p=uuid.UUID(program["id"]))
    assert count["n"] == 4


async def test_a_member_who_enrolled_alone_is_skipped_as_already_enrolled(client, world, factory):
    program = await _program(factory, "solo")
    own = await client.post(ENROLLMENTS, json={"program_id": program["id"]},
                            headers=world["s4"]["headers"])
    assert own.status_code == 201
    body = (await _enroll(client, world, program, membership_ids=[world["s4"]["membership_id"]])).json()
    assert body["enrolled"] == []
    assert body["skipped"] == [{"membership_id": world["s4"]["membership_id"],
                                "reason": "already_enrolled"}]


async def test_enrolling_a_unit_or_a_list(client, world, factory):
    program = await _program(factory, "unidad")
    unit = (await _enroll(client, world, program, unit_id=world["u1"])).json()
    assert {row["membership_id"] for row in unit["enrolled"]} == {
        world["s1"]["membership_id"], world["s2"]["membership_id"]}

    listed = (await _enroll(client, world, program, membership_ids=[
        world["s3"]["membership_id"], world["gone"]["membership_id"], world["s1"]["membership_id"],
    ])).json()
    assert [row["membership_id"] for row in listed["enrolled"]] == [world["s3"]["membership_id"]]
    assert {row["membership_id"]: row["reason"] for row in listed["skipped"]} == {
        world["gone"]["membership_id"]: "not_active",
        world["s1"]["membership_id"]: "already_enrolled",
    }

    both = (await _enroll(client, world, program, unit_id=world["u1"],
                          membership_ids=[world["s4"]["membership_id"]])).json()
    assert both["enrolled"] == []
    assert both["skipped"] == [{"membership_id": world["s4"]["membership_id"], "reason": "out_of_unit"}]


async def test_a_membership_of_another_club_or_an_unknown_unit_is_a_404(client, world, factory):
    program = await _program(factory, "ajena")
    foreign = await _enroll(client, world, program, membership_ids=[world["outsider"]["membership_id"]])
    assert foreign.status_code == 404
    unknown = await _enroll(client, world, program, unit_id=str(uuid.uuid4()))
    assert unknown.status_code == 404


async def test_only_a_published_program_is_enrolled(client, world, factory):
    for label, program_status in (("borrador", "DRAFT"), ("archivada", "ARCHIVED")):
        program = await _program(factory, label, status=program_status)
        response = await _enroll(client, world, program)
        assert response.status_code == 409
        assert response.json()["detail"] == "program_not_published"
    missing = await client.post(f"{_url(world)}/{uuid.uuid4()}/enroll", json={},
                                headers=world["director"]["headers"])
    assert missing.status_code == 404


async def test_who_may_enroll(client, world, factory):
    program = await _program(factory, "rbac-inscribir")
    for actor in ("s2", "stranger", "instructor",
                  "admin_b", "director_b", "outsider"):
        response = await _enroll(client, world, program, actor=actor)
        assert response.status_code == 403, (actor, response.text)
    # A counselor only for their own unit.
    assert (await _enroll(client, world, program, actor="counselor", unit_id=world["u2"])).status_code == 403

    own_unit = (await _enroll(client, world, program, actor="counselor")).json()
    assert {row["membership_id"] for row in own_unit["enrolled"]} == {
        world["s1"]["membership_id"], world["s2"]["membership_id"]}
    other = (await _enroll(client, world, program, actor="counselor",
                           membership_ids=[world["s3"]["membership_id"]])).json()
    assert other["skipped"] == [{"membership_id": world["s3"]["membership_id"], "reason": "out_of_unit"}]

    by_secretary = (await _enroll(client, world, program, actor="secretary",
                                  membership_ids=[world["s3"]["membership_id"]])).json()
    assert [row["membership_id"] for row in by_secretary["enrolled"]] == [world["s3"]["membership_id"]]
    by_admin = (await _enroll(client, world, program, actor="admin",
                              membership_ids=[world["s4"]["membership_id"]])).json()
    assert [row["membership_id"] for row in by_admin["enrolled"]] == [world["s4"]["membership_id"]]


# ----------------------------------------------------------------------------
# 1. Las clases del club
# ----------------------------------------------------------------------------
async def test_the_club_classes_with_their_counters_and_the_available_ones(client, world, factory, issuer):
    followed = await _program(factory, "seguida", sections=(("a", 1),))
    idle = await _program(factory, "libre")
    draft = await _program(factory, "oculta", status="DRAFT")
    enrolled = (await _enroll(client, world, followed)).json()["enrolled"]
    by_member = {row["membership_id"]: row["enrollment_id"] for row in enrolled}
    # s2 and s3 complete the only requirement; s3 is invested.
    for key in ("s2", "s3"):
        signed = await _sign(client, world, followed, [
            {"enrollment_id": by_member[world[key]["membership_id"]],
             "requirement_id": followed["requirements"][0]}])
        assert signed.json()["signed"] == 1
    invested = await client.post(_url(world, followed, "/invest"),
                                 json={"enrollment_ids": [by_member[world["s3"]["membership_id"]]]},
                                 headers=world["director"]["headers"])
    assert invested.status_code == 200, invested.text

    response = await client.get(_url(world), headers=world["director"]["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(item for item in body["classes"] if item["program"]["id"] == followed["id"])
    assert row["program"]["slug"] == followed["slug"] and row["program"]["kind"] == "CLASS"
    assert {k: row[k] for k in ("enrolled", "complete", "invested", "in_progress")} == {
        "enrolled": 4, "complete": 1, "invested": 1, "in_progress": 2}
    available = {item["id"] for item in body["available"]}
    assert idle["id"] in available and followed["id"] not in available and draft["id"] not in available
    assert all(item["program"]["id"] != idle["id"] for item in body["classes"])

    # The counselor counts their unit only (s1 in progress, s2 complete).
    mine = (await client.get(_url(world), headers=world["counselor"]["headers"])).json()
    row = next(item for item in mine["classes"] if item["program"]["id"] == followed["id"])
    assert (row["enrolled"], row["complete"], row["in_progress"]) == (2, 1, 1)


async def test_a_club_that_declares_its_ministry_is_offered_only_its_classes(client, world, factory):
    """Rule 3 of ESTADO.md: the ministry comes from the data (`metadata_json.ministry`)."""
    await _exec("UPDATE organizations SET metadata_json = :meta WHERE id = :id",
                meta='{"ministry": "adventurers"}', id=uuid.UUID(world["other_club"]["id"]))
    pathfinders = await _program(factory, "min-conquis")
    adventurers = await _program(factory, "min-aventureros", ministry="adventurers")
    url = f"{CLUBS}/{world['other_club']['id']}/classes"
    body = (await client.get(url, headers=world["director_b"]["headers"])).json()
    offered = {item["id"]: item["ministry"] for item in body["available"]}
    assert offered[adventurers["id"]] == "adventurers" and pathfinders["id"] not in offered
    assert set(offered.values()) == {"adventurers"}
    refused = await client.post(f"{url}/{pathfinders['id']}/enroll", json={},
                                headers=world["director_b"]["headers"])
    assert refused.status_code == 409 and refused.json()["detail"] == "program_not_in_club_ministry"
    accepted = await client.post(f"{url}/{adventurers['id']}/enroll", json={},
                                 headers=world["director_b"]["headers"])
    assert [row["membership_id"] for row in accepted.json()["enrolled"]] == [
        world["outsider"]["membership_id"]]


async def test_the_ministry_column_wins_over_the_metadata(client, world, factory):
    """019_club_ministry.sql: `organizations.ministry_id` is the truth; the metadata slug is
    only the fallback while the column is NULL. The answer names the club's ministry."""
    await _exec(
        "UPDATE organizations SET metadata_json = :meta,"
        " ministry_id = (SELECT id FROM ministries WHERE slug = 'pathfinders') WHERE id = :id",
        meta='{"ministry": "adventurers"}', id=uuid.UUID(world["other_club"]["id"]))
    try:
        pathfinders = await _program(factory, "col-conquis")
        adventurers = await _program(factory, "col-aventureros", ministry="adventurers")
        url = f"{CLUBS}/{world['other_club']['id']}/classes"
        body = (await client.get(url, headers=world["director_b"]["headers"])).json()
        assert body["ministry"]["slug"] == "pathfinders"
        assert set(body["ministry"]) == {"id", "slug", "name"}
        offered = {item["id"]: item["ministry"] for item in body["available"]}
        assert pathfinders["id"] in offered and adventurers["id"] not in offered
        assert set(offered.values()) == {"pathfinders"}
        refused = await client.post(f"{url}/{adventurers['id']}/enroll", json={},
                                    headers=world["director_b"]["headers"])
        assert refused.status_code == 409
        assert refused.json()["detail"] == "program_not_in_club_ministry"
    finally:
        await _exec("UPDATE organizations SET ministry_id = NULL WHERE id = :id",
                    id=uuid.UUID(world["other_club"]["id"]))


async def test_a_club_without_a_ministry_is_offered_every_ministry(client, world, factory):
    """Nothing guesses a ministry: without one the club sees every published class, and the
    answer says `ministry: null` so the screen can warn about it."""
    adventurers = await _program(factory, "sin-min-aventureros", ministry="adventurers")
    pathfinders = await _program(factory, "sin-min-conquis")
    body = (await client.get(_url(world), headers=world["director"]["headers"])).json()
    assert body["ministry"] is None
    offered = {item["id"] for item in body["available"]}
    assert {adventurers["id"], pathfinders["id"]} <= offered


async def test_who_may_read_the_club_classes(client, world):
    for actor in ("director", "secretary", "instructor", "counselor", "admin", "master"):
        assert (await client.get(_url(world), headers=world[actor]["headers"])).status_code == 200, actor
    for actor in ("s2", "stranger", "admin_b", "director_b"):
        assert (await client.get(_url(world), headers=world[actor]["headers"])).status_code == 403, actor


# ----------------------------------------------------------------------------
# 3. La matriz
# ----------------------------------------------------------------------------
async def test_the_matrix_shape(client, world, factory):
    program = await _program(factory, "matriz", sections=(("a", 2), ("b", 1)))
    enrolled = (await _enroll(client, world, program)).json()["enrolled"]
    by_member = {row["membership_id"]: row["enrollment_id"] for row in enrolled}
    first = program["requirements"][0]
    await _sign(client, world, program, [
        {"enrollment_id": by_member[world["s2"]["membership_id"]], "requirement_id": first}])

    response = await _matrix(client, world, program)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["program"] == {"id": program["id"], "slug": program["slug"], "name": program["name"]}
    assert [s["slug"] for s in body["sections"]] == ["a", "b"]
    assert body["sections"][0]["name"] == "Sección a"
    assert [r["id"] for s in body["sections"] for r in s["requirements"]] == program["requirements"]
    assert body["sections"][0]["requirements"][0] == {
        "id": first, "position": 1, "label": "1.1", "kind": "FREE"}

    members = _by_membership(body["members"])
    assert set(members) == {world[k]["membership_id"] for k in ("s1", "s2", "s3", "s4")}
    s2 = members[world["s2"]["membership_id"]]
    assert s2["enrollment_id"] == by_member[world["s2"]["membership_id"]]
    assert s2["user"]["id"] == world["s2"]["id"] and s2["user"]["name"] == factory.name("s2")
    assert "handle" in s2["user"]
    assert s2["unit"] == {"id": world["u1"], "name": factory.name("Águilas")}
    assert s2["status"] == "IN_PROGRESS" and s2["progress_pct"] == 33
    assert s2["cells"] == {first: "COMPLETE", program["requirements"][1]: "PENDING",
                           program["requirements"][2]: "PENDING"}
    assert members[world["s4"]["membership_id"]]["unit"] is None
    # Adult: their photo. Minor without the guardian's permission: none.
    assert s2["user"]["avatar_url"] == "https://media.example/avatars/s2.png"
    assert members[world["s1"]["membership_id"]]["user"]["avatar_url"] is None

    unit = (await _matrix(client, world, program, unit_id=world["u2"])).json()
    assert [m["membership_id"] for m in unit["members"]] == [world["s3"]["membership_id"]]


async def test_the_counselor_reads_their_unit_only(client, world, factory):
    program = await _program(factory, "matriz-consejero")
    await _enroll(client, world, program)
    mine = (await _matrix(client, world, program, actor="counselor")).json()
    assert {m["membership_id"] for m in mine["members"]} == {
        world["s1"]["membership_id"], world["s2"]["membership_id"]}
    assert (await _matrix(client, world, program, actor="counselor", unit_id=world["u2"])).status_code == 403
    for actor in ("s2", "stranger", "admin_b", "director_b"):
        assert (await _matrix(client, world, program, actor=actor)).status_code == 403, actor
    draft = await _program(factory, "matriz-borrador", status="DRAFT")
    assert (await _matrix(client, world, draft)).status_code == 404


async def test_the_matrix_costs_the_same_queries_whatever_the_members(client, world, factory):
    program = await _program(factory, "consultas")
    await _enroll(client, world, program, unit_id=world["u1"])

    async def statements() -> int:
        seen = []

        def count(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            assert (await _matrix(client, world, program)).status_code == 200
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        return len(seen)

    before = await statements()
    await _enroll(client, world, program)   # two more members, and their progress rows
    after = await statements()
    assert after == before, (before, after)
    assert after <= 6, after


# ----------------------------------------------------------------------------
# 4. Firma en bloque
# ----------------------------------------------------------------------------
async def test_block_sign_completes_cells_skips_the_rest_and_reaches_ready(client, world, factory):
    program = await _program(factory, "firma", sections=(("a", 2),), evidence_positions=(2,))
    other = await _program(factory, "firma-otra", sections=(("a", 1),))
    enrolled = (await _enroll(client, world, program)).json()["enrolled"]
    other_enrolled = (await _enroll(client, world, other)).json()["enrolled"]
    e = {row["membership_id"]: row["enrollment_id"] for row in enrolled}
    s2 = e[world["s2"]["membership_id"]]
    r1, r2 = program["requirements"]

    response = await _sign(client, world, program, [
        {"enrollment_id": s2, "requirement_id": r1},
        {"enrollment_id": s2, "requirement_id": r2},   # practical, no evidence: the leader vouches
        {"enrollment_id": other_enrolled[0]["enrollment_id"], "requirement_id": r1},
        {"enrollment_id": s2, "requirement_id": other["requirements"][0]},
    ], note="Lo vi en el campamento")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["signed"] == 2
    assert {(row["enrollment_id"], row["requirement_id"]): row["reason"] for row in body["skipped"]} == {
        (other_enrolled[0]["enrollment_id"], r1): "not_found",
        (s2, other["requirements"][0]): "not_found",
    }

    enrollment = await fetch_one("SELECT status, ready_at FROM honor_enrollments WHERE id = :e",
                                 e=uuid.UUID(s2))
    assert enrollment["status"] == "READY" and enrollment["ready_at"] is not None
    rows = await fetch_all(
        "SELECT id, status, completed_via, reviewed_by_id, review_note FROM requirement_progress"
        " WHERE enrollment_id = :e", e=uuid.UUID(s2))
    assert {r["status"] for r in rows} == {"COMPLETE"}
    assert {r["completed_via"] for r in rows} == {"REVIEW"}
    assert {str(r["reviewed_by_id"]) for r in rows} == {world["director"]["id"]}
    assert {r["review_note"] for r in rows} == {"Lo vi en el campamento"}

    audits = await fetch_all(
        "SELECT entity_id, metadata_json FROM audit_log WHERE action = 'REQUIREMENT_REVIEW'"
        " AND entity_id = ANY(:ids)", ids=[str(r["id"]) for r in rows])
    assert len(audits) == 2
    for audit in audits:
        meta = audit["metadata_json"]
        assert meta["decided_via"] == "block_sign" and meta["verdict"] == "COMPLETE"
        assert meta["program_id"] == program["id"] and meta["enrollment_id"] == s2
        assert meta["bulk_id"]
    assert len({a["metadata_json"]["bulk_id"] for a in audits}) == 1
    assert {a["metadata_json"]["enrollment_status"] for a in audits} == {"READY"}
    practical = next(a for a in audits if a["metadata_json"]["position"] == 2)
    assert practical["metadata_json"]["is_practical"] is True

    # Signing again: nothing changes, every cell is skipped as already complete.
    again = (await _sign(client, world, program, [
        {"enrollment_id": s2, "requirement_id": r1}])).json()
    assert again == {"signed": 0, "skipped": [
        {"enrollment_id": s2, "requirement_id": r1, "reason": "already_complete"}]}


async def test_block_sign_reach(client, world, factory):
    program = await _program(factory, "alcance", sections=(("a", 3),))
    enrolled = (await _enroll(client, world, program)).json()["enrolled"]
    e = {row["membership_id"]: row["enrollment_id"] for row in enrolled}
    r1, r2, r3 = program["requirements"]
    s1, s3 = e[world["s1"]["membership_id"]], e[world["s3"]["membership_id"]]

    # The counselor of u1 signs s1 (u1) but not s3 (u2).
    body = (await _sign(client, world, program, [
        {"enrollment_id": s1, "requirement_id": r1},
        {"enrollment_id": s3, "requirement_id": r1},
    ], actor="counselor")).json()
    assert body["signed"] == 1
    assert body["skipped"] == [{"enrollment_id": s3, "requirement_id": r1, "reason": "out_of_reach"}]

    # The instructor of the club reviews like the director.
    assert (await _sign(client, world, program, [{"enrollment_id": s3, "requirement_id": r2}],
                        actor="instructor")).json()["signed"] == 1

    # Nobody signs their own card.
    own = await client.post(ENROLLMENTS, json={"program_id": program["id"]},
                            headers=world["counselor"]["headers"])
    assert own.status_code == 201
    body = (await _sign(client, world, program, [
        {"enrollment_id": own.json()["id"], "requirement_id": r1}], actor="counselor")).json()
    assert body == {"signed": 0, "skipped": [{"enrollment_id": own.json()["id"], "requirement_id": r1,
                                              "reason": "own_enrollment"}]}

    # An HOURS requirement has one route, the activity log.
    hours = await _program(factory, "alcance-horas", sections=(("a", 1),), hours_positions=(1,))
    h = (await _enroll(client, world, hours, membership_ids=[world["s2"]["membership_id"]])).json()
    body = (await _sign(client, world, hours, [
        {"enrollment_id": h["enrolled"][0]["enrollment_id"], "requirement_id": hours["requirements"][0]}
    ])).json()
    assert body["skipped"][0]["reason"] == "hours_only"

    # Who has no signing power at all in this club: 403.
    for actor in ("secretary", "s2", "stranger", "admin", "director_b"):
        response = await _sign(client, world, program, [{"enrollment_id": s3, "requirement_id": r3}],
                               actor=actor)
        assert response.status_code == 403, (actor, response.text)

    # The note is capped.
    too_long = await _sign(client, world, program, [{"enrollment_id": s3, "requirement_id": r3}],
                           note="x" * 201)
    assert too_long.status_code == 422


# ----------------------------------------------------------------------------
# 5. Investidura
# ----------------------------------------------------------------------------
async def test_investiture_only_for_ready_and_only_by_the_director(client, world, factory, issuer):
    program = await _program(factory, "investidura", sections=(("a", 1),))
    enrolled = (await _enroll(client, world, program)).json()["enrolled"]
    e = {row["membership_id"]: row["enrollment_id"] for row in enrolled}
    ready, pending = e[world["s2"]["membership_id"]], e[world["s4"]["membership_id"]]
    await _sign(client, world, program, [{"enrollment_id": ready, "requirement_id": program["requirements"][0]}])

    for actor in ("secretary", "counselor", "instructor", "s2", "admin", "director_b"):
        response = await client.post(_url(world, program, "/invest"),
                                     json={"enrollment_ids": [ready]}, headers=world[actor]["headers"])
        assert response.status_code == 403, (actor, response.text)

    unknown = str(uuid.uuid4())
    response = await client.post(_url(world, program, "/invest"),
                                 json={"enrollment_ids": [ready, pending, unknown]},
                                 headers=world["director"]["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["enrollment_id"] for row in body["invested"]] == [ready]
    certificate_no = body["invested"][0]["certificate_no"]
    assert certificate_no
    assert {row["enrollment_id"]: row["reason"] for row in body["skipped"]} == {
        pending: "not_ready", unknown: "not_found"}

    row = await fetch_one(
        "SELECT e.status, c.certificate_no, c.program_id FROM honor_enrollments e"
        " JOIN certificates c ON c.id = e.certificate_id WHERE e.id = :e", e=uuid.UUID(ready))
    assert row["status"] == "CERTIFIED" and row["certificate_no"] == certificate_no
    assert str(row["program_id"]) == program["id"]
    audit = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'CERTIFICATE_ISSUE'"
        " AND metadata_json->>'enrollment_id' = :e", e=ready)
    assert audit["metadata_json"]["via"] == "club"

    again = (await client.post(_url(world, program, "/invest"), json={"enrollment_ids": [ready]},
                               headers=world["director"]["headers"])).json()
    assert again == {"invested": [], "skipped": [{"enrollment_id": ready, "reason": "already_invested"}]}


async def test_an_association_program_is_not_invested_by_the_club(client, world, factory, issuer):
    program = await _program(factory, "gm", kind="CURRICULUM", issuer_level="ASSOCIATION",
                             sections=(("a", 1),))
    enrolled = (await _enroll(client, world, program, membership_ids=[world["s2"]["membership_id"]])).json()
    response = await client.post(_url(world, program, "/invest"),
                                 json={"enrollment_ids": [enrolled["enrolled"][0]["enrollment_id"]]},
                                 headers=world["director"]["headers"])
    assert response.status_code == 409
    assert response.json()["detail"] == "program_issued_by_association"


async def test_the_default_issued_date_is_today(client, world, factory, issuer):
    program = await _program(factory, "fecha", sections=(("a", 1),))
    enrolled = (await _enroll(client, world, program, membership_ids=[world["s3"]["membership_id"]])).json()
    enrollment_id = enrolled["enrolled"][0]["enrollment_id"]
    await _sign(client, world, program, [{"enrollment_id": enrollment_id,
                                          "requirement_id": program["requirements"][0]}])
    body = (await client.post(_url(world, program, "/invest"), json={"enrollment_ids": [enrollment_id]},
                              headers=world["director"]["headers"])).json()
    certificate = await fetch_one("SELECT issued_date FROM certificates WHERE certificate_no = :n",
                                  n=body["invested"][0]["certificate_no"])
    assert certificate["issued_date"] == date.today()


# ----------------------------------------------------------------------------
# «Avisos»: what the members hear about a block signature and an investiture
# ----------------------------------------------------------------------------
async def test_block_sign_and_investiture_reach_the_members_inbox(
    client, world, factory, issuer, monkeypatch
):
    from app.services import email as email_service

    sent = []

    async def progress(to, name, kind, honor_name, note, link, **kwargs):
        sent.append({"to": to, "kind": kind, "award": honor_name, **kwargs})
        return True

    monkeypatch.setattr(email_service, "send_progress_email", progress)
    program = await _program(factory, "avisos", sections=(("a", 2),))
    enrolled = (await _enroll(client, world, program)).json()["enrolled"]
    e = {row["membership_id"]: row["enrollment_id"] for row in enrolled}
    card = e[world["s3"]["membership_id"]]
    r1, r2 = program["requirements"]

    async def inbox():
        return await fetch_all(
            "SELECT kind, count, data FROM notifications WHERE entity_id = :e ORDER BY created_at DESC",
            e=card,
        )

    # One of two signed: the inbox counts it; E9 sends no e-mail for a plain COMPLETE.
    assert (await _sign(client, world, program, [{"enrollment_id": card, "requirement_id": r1}])).status_code == 200
    rows = await inbox()
    assert [(row["kind"], row["count"]) for row in rows] == [("REQUIREMENT_APPROVED", 1)]
    assert rows[0]["data"]["type"] == "program" and rows[0]["data"]["award"] == program["name"]
    assert sent == []

    # The last one makes the card READY: «lista para la investidura», inbox and e-mail.
    assert (await _sign(client, world, program, [{"enrollment_id": card, "requirement_id": r2}])).status_code == 200
    assert [row["kind"] for row in await inbox()] == ["PROGRESS_READY", "REQUIREMENT_APPROVED"]
    assert [(m["kind"], m.get("award_type")) for m in sent] == [("PROGRESS_READY", "program")]

    # The investiture: the same E9 notice a certificate of the portfolio sends.
    invested = await client.post(_url(world, program, "/invest"), json={"enrollment_ids": [card]},
                                 headers=world["director"]["headers"])
    assert invested.status_code == 200, invested.text
    assert [row["enrollment_id"] for row in invested.json()["invested"]] == [card]
    assert (await inbox())[0]["kind"] == "PROGRESS_CERTIFIED"
    assert [(m["kind"], m["to"]) for m in sent][-1] == ("PROGRESS_CERTIFIED", world["s3"]["email"])
