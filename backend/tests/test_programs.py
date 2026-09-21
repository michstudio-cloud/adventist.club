"""Bloque F · F1 — Programas y tarjeta digital.

Two things are being proved here at the same time:
  1. a program walks the WHOLE engine of block A (enroll, draft, submit, evidence, verdict,
     READY, certificate) without block A growing a single branch of its own, and
  2. nothing of block A changes: a program is never an honor, never appears in an honors
     listing or search, and its certificate can never be taken for an honor certificate.
"""

import importlib.util
import json
import pathlib
import shutil
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from tests.conftest import (
    DB_AVAILABLE,
    TEST_DATABASE_URL,
    fetch_all,
    fetch_one,
    module_factory,
    requires_db,
)

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
# Hand-written TEST DATA. Never loaded into a real database; see tests/data/README.md.
FIXTURE_DIR = BACKEND_DIR / "tests" / "data" / "programs"
FIXTURE_SLUG = "clase-de-prueba"


def _load_importer():
    """migrations/ is not a package: load the script by path, as its own tests would."""
    path = BACKEND_DIR / "migrations" / "import_programs.py"
    spec = importlib.util.spec_from_file_location("import_programs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_with_prefix(tmp_path, factory, label: str = FIXTURE_SLUG) -> pathlib.Path:
    """A copy of the fixture whose slug and name carry this run's prefix, so the surgical
    cleanup of conftest removes every row it creates."""
    folder = tmp_path / "programs"
    shutil.copytree(FIXTURE_DIR, folder)
    slug = f"{factory.prefix}-{label}"
    ministry = folder / "pathfinders"
    for path in sorted(ministry.glob(f"{FIXTURE_SLUG}*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        if path.name.count(".") == 1:
            body["slug"] = slug
            body["code"] = slug[:40]
        if body.get("name"):
            body["name"] = f"{factory.name(label)} {body['name']}"
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        path.rename(ministry / path.name.replace(FIXTURE_SLUG, slug, 1))
    return folder

PORTFOLIO = "/api/v1/portfolio"
ENROLLMENTS = f"{PORTFOLIO}/enrollments"
PROGRAMS = "/api/v1/programs"
HONORS = "/api/v1/honors"


def _program_tables_exist() -> bool:
    import asyncio

    from app.db import engine

    async def probe() -> bool:
        try:
            async with SessionLocal() as db:
                found = await db.scalar(text(
                    "SELECT to_regclass('public.programs') IS NOT NULL"
                    " AND to_regclass('public.honor_enrollments') IS NOT NULL"))
            return bool(found)
        finally:
            await engine.dispose()

    return DB_AVAILABLE and asyncio.run(probe())


pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not _program_tables_exist(),
        reason="apply migrations/012_programs.sql to the test database",
    ),
]
factory = module_factory("programs")


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="module")
async def world(factory):
    association = await factory.org("assoc", "association")
    club = await factory.org("club", "club", association)
    issuer = await factory.org("issuer", "association")
    issuer_code = f"{factory.prefix}-ISS"
    people = {
        "member": await factory.user("member", "STUDENT", club["id"], is_minor=True),
        "director": await factory.user("director", "CLUB_DIRECTOR", club["id"]),
        "instructor": await factory.user("instructor", "INSTRUCTOR", club["id"]),
        "admin": await factory.user("assoc-admin", "ADMIN_ASSOCIATION", association["id"]),
        "master": await factory.user("master", "MASTER_GC"),
        "stranger": await factory.user("stranger", "STUDENT"),
    }
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET code = :code WHERE id = :id"),
                         {"code": issuer_code, "id": uuid.UUID(issuer["id"])})
        await db.execute(text("UPDATE users SET verification_status = 'VERIFIED' WHERE id = :id"),
                         {"id": uuid.UUID(people["instructor"]["id"])})
        await db.commit()
    return {**people, "association": association, "club": club, "issuer_code": issuer_code}


@pytest.fixture
def issuer(world, monkeypatch):
    monkeypatch.setattr(settings, "ISSUER_ORGANIZATION_CODE", world["issuer_code"])


async def _program(
    factory,
    label: str,
    *,
    sections=(("liderazgo", 2), ("servicio", 1)),
    status="PUBLISHED",
    ministry="pathfinders",
    kind="CLASS",
    issuer_level="CLUB",
    english=False,
    evidence_positions=(),
) -> dict:
    """A program with `sections` = ((slug, how many requirements), ...), numbered globally."""
    program_id = uuid.uuid4()
    name = factory.name(label)
    position = 0
    requirement_ids = []
    async with SessionLocal() as db:
        ministry_id = (await db.execute(
            text("SELECT id FROM ministries WHERE slug = :slug"), {"slug": ministry})).scalar_one()
        await db.execute(text(
            "INSERT INTO programs (id, ministry_id, kind, slug, name, status, issuer_level,"
            " sort_order, authority, source, source_url, license)"
            " VALUES (:id, :m, :k, :slug, :name, :status, :issuer, 1, 'IAD',"
            " 'pathfinder-wiki', 'https://wiki.pathfindersonline.org/w/Test', 'CC BY-SA 3.0')"),
            {"id": program_id, "m": ministry_id, "k": kind, "slug": f"{factory.prefix}-{label}",
             "name": name, "status": status, "issuer": issuer_level})
        for section_position, (slug, count) in enumerate(sections, start=1):
            section_id = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO program_sections (id, program_id, position, slug, name)"
                " VALUES (:id, :p, :pos, :slug, :name)"),
                {"id": section_id, "p": program_id, "pos": section_position, "slug": slug,
                 "name": f"Sección {slug}"})
            if english:
                await db.execute(text(
                    "INSERT INTO program_section_translations (section_id, locale, name)"
                    " VALUES (:s, 'en', :n)"), {"s": section_id, "n": f"Section {slug}"})
            for index in range(1, count + 1):
                position += 1
                requirement_id = uuid.uuid4()
                requirement_ids.append((position, requirement_id))
                await db.execute(text(
                    "INSERT INTO program_requirements (id, program_id, section_id, position, label,"
                    " kind, evidence_required) VALUES (:id, :p, :s, :pos, :label, 'FREE', :ev)"),
                    {"id": requirement_id, "p": program_id, "s": section_id, "pos": position,
                     "label": f"{section_position}.{index}", "ev": position in evidence_positions})
                for locale in ("es", "en") if english else ("es",):
                    await db.execute(text(
                        "INSERT INTO program_requirement_texts (requirement_id, locale, description,"
                        " source, source_url, license) VALUES (:r, :l, :d, 'pathfinder-wiki',"
                        " 'https://wiki.pathfindersonline.org/w/Test', 'CC BY-SA 3.0')"),
                        {"r": requirement_id, "l": locale,
                         "d": f"{locale.upper()} requisito {position}"})
        if english:
            await db.execute(text(
                "INSERT INTO program_translations (program_id, locale, name) VALUES (:p, 'en', :n)"),
                {"p": program_id, "n": f"{name} (EN)"})
        await db.commit()
    return {"id": str(program_id), "name": name, "requirements": requirement_ids,
            "total": position, "slug": f"{factory.prefix}-{label}"}


async def _enroll(client, user, program, **extra) -> dict:
    response = await client.post(ENROLLMENTS, json={"program_id": program["id"], **extra},
                                 headers=user["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _submit(client, user, enrollment_id, position, **extra):
    extra.setdefault("member_note", "Respuesta de prueba")
    return await client.put(f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}",
                            json={"status": "SUBMITTED", **extra}, headers=user["headers"])


async def _review(client, reviewer, enrollment_id, position, verdict="COMPLETE", note=None):
    body = {"verdict": verdict} if note is None else {"verdict": verdict, "note": note}
    return await client.post(f"{ENROLLMENTS}/{enrollment_id}/requirements/{position}/review",
                             json=body, headers=reviewer["headers"])


async def _complete_all(client, member, reviewer, enrollment) -> None:
    for requirement in enrollment["requirements"]:
        assert (await _submit(client, member, enrollment["id"], requirement["position"])).status_code == 200
        done = await _review(client, reviewer, enrollment["id"], requirement["position"])
        assert done.status_code == 200, done.text


# ----------------------------------------------------------------------------
# The database keeps the invariant, whatever the code does
# ----------------------------------------------------------------------------
async def test_an_enrollment_is_of_an_honor_or_of_a_program_never_both_nor_neither(world, factory):
    program = await _program(factory, "check")
    member = uuid.UUID(world["member"]["id"])
    honor_id = (await fetch_one("SELECT id FROM honors LIMIT 1"))["id"]
    for honor, program_id in ((honor_id, program["id"]), (None, None)):
        with pytest.raises(IntegrityError) as excinfo:
            async with SessionLocal() as db:
                await db.execute(text(
                    "INSERT INTO honor_enrollments (user_id, honor_id, program_id)"
                    " VALUES (:u, :h, :p)"),
                    {"u": member, "h": honor, "p": program_id})
                await db.commit()
        assert "one_curriculum" in str(excinfo.value)


async def test_relaxing_not_null_did_not_touch_a_single_honor_enrollment():
    """Every enrollment that existed before 012 is still an honor enrollment."""
    rows = await fetch_all(
        "SELECT count(*) AS orphans FROM honor_enrollments"
        " WHERE num_nonnulls(honor_id, program_id) <> 1")
    assert rows[0]["orphans"] == 0


# ----------------------------------------------------------------------------
# Catalogue
# ----------------------------------------------------------------------------
async def test_public_catalogue_lists_published_programs_of_one_ministry(client, factory, world):
    published = await _program(factory, "amigo")
    draft = await _program(factory, "companero", status="DRAFT")

    response = await client.get(PROGRAMS, params={"ministry": "pathfinders"})
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert published["id"] in ids and draft["id"] not in ids
    item = next(row for row in response.json() if row["id"] == published["id"])
    assert item["kind"] == "CLASS" and item["authority"] == "IAD" and item["requirement_count"] == 3


async def test_the_ministry_is_never_defaulted(client):
    assert (await client.get(PROGRAMS)).status_code == 422
    assert (await client.get(PROGRAMS, params={"ministry": "no-existe"})).json() == []


async def test_detail_groups_requirements_by_section_in_each_language(client, factory, world):
    program = await _program(factory, "detalle", english=True)
    spanish = (await client.get(f"{PROGRAMS}/{program['id']}", params={"locale": "es"})).json()
    english = (await client.get(f"{PROGRAMS}/{program['id']}", params={"locale": "en"})).json()

    assert [s["slug"] for s in spanish["sections"]] == ["liderazgo", "servicio"]
    assert [len(s["requirements"]) for s in spanish["sections"]] == [2, 1]
    positions = [r["position"] for s in spanish["sections"] for r in s["requirements"]]
    assert positions == [1, 2, 3]
    assert positions == [r["position"] for s in english["sections"] for r in s["requirements"]]
    assert spanish["sections"][0]["requirements"][0]["label"] == "1.1"
    assert spanish["sections"][0]["requirements"][0]["description"].startswith("ES")
    assert english["sections"][0]["requirements"][0]["description"].startswith("EN")
    assert english["name"].endswith("(EN)") and english["locale"] == "en"
    # CC BY-SA: attribution travels with every single row, never only with the program.
    assert spanish["sections"][0]["requirements"][0]["attribution"]["license"] == "CC BY-SA 3.0"


async def test_a_draft_program_is_visible_only_to_the_publisher(client, factory, world):
    draft = await _program(factory, "borrador", status="DRAFT")
    assert (await client.get(f"{PROGRAMS}/{draft['id']}")).status_code == 404
    assert (await client.get(f"{PROGRAMS}/{draft['id']}",
                             headers=world["director"]["headers"])).status_code == 404
    seen = await client.get(f"{PROGRAMS}/{draft['id']}", headers=world["master"]["headers"])
    assert seen.status_code == 200 and seen.json()["status"] == "DRAFT"


async def test_only_master_publishes_and_archives_a_program(client, factory, world):
    program = await _program(factory, "publicar", status="DRAFT")
    for actor in ("director", "admin", "instructor"):
        denied = await client.post(f"{PROGRAMS}/{program['id']}/publish",
                                   headers=world[actor]["headers"])
        assert denied.status_code == 403, actor

    published = await client.post(f"{PROGRAMS}/{program['id']}/publish",
                                  headers=world["master"]["headers"])
    assert published.status_code == 200 and published.json()["status"] == "PUBLISHED"
    audit = await fetch_one(
        "SELECT action FROM audit_log WHERE entity_id = :id AND action = 'PROGRAM_PUBLISH'",
        id=program["id"])
    assert audit is not None

    archived = await client.post(f"{PROGRAMS}/{program['id']}/archive",
                                 headers=world["master"]["headers"])
    assert archived.status_code == 200 and archived.json()["status"] == "ARCHIVED"
    listed = (await client.get(PROGRAMS, params={"ministry": "pathfinders"})).json()
    assert program["id"] not in [row["id"] for row in listed]


async def test_a_program_without_requirements_cannot_be_published(client, factory, world):
    empty = await _program(factory, "vacio", sections=(), status="DRAFT")
    refused = await client.post(f"{PROGRAMS}/{empty['id']}/publish", headers=world["master"]["headers"])
    assert refused.status_code == 409 and "requisitos" in refused.json()["detail"]


# ----------------------------------------------------------------------------
# A program never leaks into the honors catalogue
# ----------------------------------------------------------------------------
async def test_a_program_never_appears_among_the_honors(client, factory, world):
    program = await _program(factory, "invisible")
    search = await client.get(HONORS, params={"q": program["name"], "ministry": "pathfinders"})
    assert search.status_code == 200 and search.json() == []
    assert (await client.get(f"{HONORS}/{program['id']}")).status_code == 404
    listed = await client.get(HONORS, params={"ministry": "pathfinders", "limit": 500})
    assert program["id"] not in [row["id"] for row in listed.json()]


async def test_the_honors_catalogue_answers_exactly_the_same_before_and_after_a_program(
    client, factory, world
):
    params = {"ministry": "pathfinders", "limit": 5}
    live = ("SELECT count(*) AS n FROM honors h JOIN ministries m ON m.id = h.ministry_id"
            " WHERE m.slug = 'pathfinders' AND h.active AND h.status = 'PUBLISHED'")

    before = await client.get(HONORS, params=params)
    before_categories = await client.get(f"{HONORS}/categories", params={"ministry": "pathfinders"})
    # The catalogue counts the honors table and nothing else…
    assert int(before.headers["X-Total-Count"]) == (await fetch_one(live))["n"]

    await _program(factory, "no-regresion")

    after = await client.get(HONORS, params=params)
    after_categories = await client.get(f"{HONORS}/categories", params={"ministry": "pathfinders"})
    # …and it still does after a program exists: a program adds nothing to it.
    assert int(after.headers["X-Total-Count"]) == (await fetch_one(live))["n"]
    assert before.json() == after.json()
    assert before_categories.json() == after_categories.json()


# ----------------------------------------------------------------------------
# Enrollment: block A's engine, fed by the adapter
# ----------------------------------------------------------------------------
async def test_enrolling_in_a_program_creates_the_progress_rows_of_block_a(client, factory, world):
    program = await _program(factory, "inscribir", evidence_positions=(2,))
    enrollment = await _enroll(client, world["member"], program)

    assert enrollment["type"] == "program"
    assert enrollment["honor"] is None
    assert enrollment["program"]["id"] == program["id"]
    assert enrollment["counters"]["total"] == 3
    rows = await fetch_all(
        "SELECT requirement_position, kind, is_practical, honor_id, program_id"
        " FROM requirement_progress p JOIN honor_enrollments e ON e.id = p.enrollment_id"
        " WHERE e.id = :id ORDER BY requirement_position", id=enrollment["id"])
    assert [row["requirement_position"] for row in rows] == [1, 2, 3]
    assert {row["kind"] for row in rows} == {"FREE"}
    assert [row["is_practical"] for row in rows] == [False, True, False]
    assert all(row["honor_id"] is None for row in rows)
    assert all(str(row["program_id"]) == program["id"] for row in rows)
    # The program requirement it came from is kept as an informative twin.
    linked = await fetch_all(
        "SELECT program_requirement_id FROM requirement_progress WHERE enrollment_id = :id",
        id=enrollment["id"])
    assert all(row["program_requirement_id"] is not None for row in linked)


async def test_enrolling_twice_in_the_same_program_returns_the_same_enrollment(client, factory, world):
    program = await _program(factory, "idempotente")
    first = await _enroll(client, world["member"], program)
    again = await client.post(ENROLLMENTS, json={"program_id": program["id"]},
                              headers=world["member"]["headers"])
    assert again.status_code == 200 and again.json()["id"] == first["id"]


async def test_the_enrollment_pins_the_language_it_was_started_in(client, factory, world):
    program = await _program(factory, "idioma", english=True)
    enrollment = await _enroll(client, world["member"], program, locale="en")
    assert enrollment["locale"] == "en"
    assert enrollment["requirements"][0]["description"].startswith("EN")
    assert enrollment["program"]["name"].endswith("(EN)")


async def test_enrolling_needs_exactly_one_of_honor_or_program(client, world, factory):
    program = await _program(factory, "exclusivo")
    honor_id = (await fetch_one("SELECT id FROM honors WHERE status = 'PUBLISHED' LIMIT 1"))["id"]
    both = await client.post(ENROLLMENTS,
                             json={"program_id": program["id"], "honor_id": str(honor_id)},
                             headers=world["member"]["headers"])
    neither = await client.post(ENROLLMENTS, json={}, headers=world["member"]["headers"])
    assert both.status_code == 422 and neither.status_code == 422


async def test_a_program_without_requirements_cannot_be_enrolled_in(client, factory, world):
    empty = await _program(factory, "sin-requisitos", sections=())
    refused = await client.post(ENROLLMENTS, json={"program_id": empty["id"]},
                                headers=world["member"]["headers"])
    assert refused.status_code == 409


async def test_an_unpublished_program_cannot_be_enrolled_in(client, factory, world):
    draft = await _program(factory, "no-publicado", status="DRAFT")
    refused = await client.post(ENROLLMENTS, json={"program_id": draft["id"]},
                                headers=world["member"]["headers"])
    assert refused.status_code == 404


# ----------------------------------------------------------------------------
# The card
# ----------------------------------------------------------------------------
async def test_the_card_shows_sections_with_their_counters(client, factory, world):
    program = await _program(factory, "tarjeta")
    enrollment = await _enroll(client, world["member"], program)
    assert (await _submit(client, world["member"], enrollment["id"], 1)).status_code == 200
    assert (await _review(client, world["director"], enrollment["id"], 1)).status_code == 200

    card = (await client.get(f"{ENROLLMENTS}/{enrollment['id']}",
                             headers=world["member"]["headers"])).json()
    assert [s["slug"] for s in card["sections"]] == ["liderazgo", "servicio"]
    assert card["sections"][0] == {"position": 1, "slug": "liderazgo",
                                   "name": "Sección liderazgo", "positions": [1, 2],
                                   "complete": 1, "total": 2}
    assert [r["label"] for r in card["requirements"]] == ["1.1", "1.2", "2.1"]
    assert {r["kind"] for r in card["requirements"]} == {"FREE"}


async def test_an_honor_enrollment_keeps_exactly_the_payload_of_block_a(client, world, factory):
    """The detail of an honor gains no section, no label and no target."""
    honor = await fetch_one(
        "SELECT h.id FROM honors h JOIN honor_requirements r ON r.honor_id = h.id"
        " WHERE h.status = 'PUBLISHED' AND h.active LIMIT 1")
    created = await client.post(ENROLLMENTS, json={"honor_id": str(honor["id"])},
                                headers=world["stranger"]["headers"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["type"] == "honor" and body["program"] is None and body["sections"] is None
    assert body["honor"]["id"] == str(honor["id"])
    assert all(r["label"] is None and r["target"] is None for r in body["requirements"])
    assert {r["kind"] for r in body["requirements"]} == {"FREE"}


# ----------------------------------------------------------------------------
# Review and investiture
# ----------------------------------------------------------------------------
async def test_the_program_reaches_ready_and_its_certificate_through_block_a(
    client, factory, world, issuer
):
    program = await _program(factory, "investidura")
    enrollment = await _enroll(client, world["member"], program)
    await _complete_all(client, world["member"], world["director"], enrollment)

    detail = (await client.get(f"{ENROLLMENTS}/{enrollment['id']}",
                               headers=world["member"]["headers"])).json()
    assert detail["status"] == "READY"

    issued = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/certificate",
                               json={"issued_date": "2026-10-04"},
                               headers=world["director"]["headers"])
    assert issued.status_code == 201, issued.text
    certificate = issued.json()
    assert certificate["honor_id"] is None
    assert certificate["honor_name_snapshot"] == program["name"]

    row = await fetch_one("SELECT honor_id, program_id FROM certificates WHERE id = :id",
                          id=certificate["id"])
    assert row["honor_id"] is None and str(row["program_id"]) == program["id"]

    verified = await client.get(f"/api/v1/certificates/verify/{certificate['certificate_no']}")
    assert verified.status_code == 200
    assert verified.json()["valid"] is True and verified.json()["kind"] == "program"


async def test_an_investiture_is_never_printed_on_an_honor_template(client, factory, world, issuer):
    program = await _program(factory, "plantilla")
    enrollment = await _enroll(client, world["member"], program)
    await _complete_all(client, world["member"], world["director"], enrollment)
    refused = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/certificate",
                                json={"issued_date": "2026-10-04", "template": "especialidad-basica"},
                                headers=world["director"]["headers"])
    assert refused.status_code == 409
    assert "plantilla de investidura" in refused.json()["detail"]


async def test_a_club_director_never_invests_an_association_program(client, factory, world, issuer):
    program = await _program(factory, "guia-mayor", kind="CURRICULUM", issuer_level="ASSOCIATION")
    enrollment = await _enroll(client, world["member"], program)
    await _complete_all(client, world["member"], world["director"], enrollment)

    detail = (await client.get(f"{ENROLLMENTS}/{enrollment['id']}",
                               headers=world["director"]["headers"])).json()
    assert detail["permissions"]["can_issue"] is False
    refused = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/certificate",
                                json={"issued_date": "2026-10-04"},
                                headers=world["director"]["headers"])
    assert refused.status_code == 403


async def test_the_honor_certificate_of_block_a_is_unchanged(client, world, issuer):
    """Same flow on an honor: `honor_id` set, `program_id` NULL, verification says honor."""
    honor = await fetch_one(
        "SELECT h.id FROM honors h JOIN honor_requirements r ON r.honor_id = h.id"
        " WHERE h.status = 'PUBLISHED' AND h.active GROUP BY h.id HAVING count(*) = 1 LIMIT 1")
    created = await client.post(ENROLLMENTS, json={"honor_id": str(honor["id"])},
                                headers=world["member"]["headers"])
    assert created.status_code == 201, created.text
    enrollment = created.json()
    await _complete_all(client, world["member"], world["director"], enrollment)
    issued = await client.post(f"{ENROLLMENTS}/{enrollment['id']}/certificate",
                               json={"issued_date": "2026-10-04"},
                               headers=world["director"]["headers"])
    assert issued.status_code == 201, issued.text
    row = await fetch_one("SELECT honor_id, program_id FROM certificates WHERE id = :id",
                          id=issued.json()["id"])
    assert row["honor_id"] is not None and row["program_id"] is None
    verified = await client.get(f"/api/v1/certificates/verify/{issued.json()['certificate_no']}")
    assert verified.json()["kind"] == "honor"


async def test_the_review_queue_of_the_club_carries_programs_and_honors(client, factory, world):
    program = await _program(factory, "cola")
    enrollment = await _enroll(client, world["member"], program)
    assert (await _submit(client, world["member"], enrollment["id"], 1)).status_code == 200

    queue = await client.get(f"{PORTFOLIO}/review/queue", headers=world["director"]["headers"])
    assert queue.status_code == 200
    mine = [row for row in queue.json() if row["enrollment_id"] == enrollment["id"]]
    assert len(mine) == 1
    assert mine[0]["type"] == "program" and mine[0]["honor"] is None
    assert mine[0]["program"]["id"] == program["id"]


async def test_the_portfolio_of_the_member_carries_the_program(client, factory, world):
    program = await _program(factory, "portafolio")
    enrollment = await _enroll(client, world["member"], program)
    body = (await client.get(f"{PORTFOLIO}/me", headers=world["member"]["headers"])).json()
    mine = next(row for row in body["enrollments"] if row["id"] == enrollment["id"])
    assert mine["type"] == "program" and mine["program"]["slug"] == program["slug"]


async def test_the_importer_loads_a_program_as_a_draft_and_is_idempotent(factory, tmp_path):
    """`migrations/import_programs.py` on the hand-written fixture in tests/data.

    It never reaches the network: it reads JSON that is already on disk (spec §7). The
    fixture is test data and is renamed with this run's prefix so cleanup stays surgical.
    """
    import_programs = _load_importer()
    folder = _fixture_with_prefix(tmp_path, factory)
    url = TEST_DATABASE_URL

    # 1. Dry run writes nothing.
    dry = import_programs.run(folder, url, commit=False, operator=None)
    assert [row["action"] for row in dry] == ["created"]
    assert await fetch_one("SELECT id FROM programs WHERE slug = :slug",
                           slug=f"{factory.prefix}-clase-de-prueba") is None

    # 2. --commit writes it, as a DRAFT and never as anything else.
    done = import_programs.run(folder, url, commit=True, operator="tester@example.com")
    assert done[0]["action"] == "created"
    # The language that does not cover every requirement is left out and reported.
    assert done[0]["locales"] == ["en", "es"] and done[0]["skipped_locales"] == [("pt-BR", 2)]
    program = await fetch_one(
        "SELECT id, status, authority, source, license, sort_order FROM programs WHERE slug = :slug",
        slug=f"{factory.prefix}-clase-de-prueba")
    assert program["status"] == "DRAFT" and program["authority"] == "IAD"
    assert program["license"] == "CC BY-SA 3.0"
    rows = await fetch_all(
        "SELECT r.position, r.label, r.kind, r.evidence_required, r.target_quantity,"
        " r.activity_category, s.slug AS section FROM program_requirements r"
        " JOIN program_sections s ON s.id = r.section_id WHERE r.program_id = :id"
        " ORDER BY r.position", id=program["id"])
    assert [row["position"] for row in rows] == [1, 2, 3]
    assert [row["section"] for row in rows] == ["general", "general", "servicio"]
    assert [row["kind"] for row in rows] == ["FREE", "FREE", "HOURS"]
    assert [row["evidence_required"] for row in rows] == [False, True, False]
    assert float(rows[2]["target_quantity"]) == 6.0 and rows[2]["activity_category"] == "SERVICE"
    texts = await fetch_all(
        "SELECT t.locale, t.description, t.license FROM program_requirement_texts t"
        " JOIN program_requirements r ON r.id = t.requirement_id"
        " WHERE r.program_id = :id ORDER BY t.locale, r.position", id=program["id"])
    assert {row["locale"] for row in texts} == {"en", "es"}
    assert all(row["license"] == "CC BY-SA 3.0" for row in texts)  # CC BY-SA row by row
    audit = await fetch_one(
        "SELECT action FROM audit_log WHERE entity_id = :id AND action = 'PROGRAM_IMPORT'",
        id=str(program["id"]))
    assert audit is not None

    # 3. Running it again changes nothing: the draft is rewritten in place, same row.
    again = import_programs.run(folder, url, commit=True, operator=None)
    assert again[0]["action"] == "updated_draft"
    still = await fetch_all("SELECT id FROM programs WHERE slug = :slug",
                            slug=f"{factory.prefix}-clase-de-prueba")
    assert len(still) == 1 and str(still[0]["id"]) == str(program["id"])
    assert len(await fetch_all(
        "SELECT id FROM program_requirements WHERE program_id = :id", id=program["id"])) == 3


async def test_the_importer_never_touches_a_published_program(client, factory, tmp_path, world):
    """A published curriculum is immutable: a change becomes the next version, in draft."""
    import_programs = _load_importer()
    folder = _fixture_with_prefix(tmp_path, factory, label="publicada")
    import_programs.run(folder, TEST_DATABASE_URL, commit=True, operator=None)
    slug = f"{factory.prefix}-publicada"
    program = await fetch_one("SELECT id FROM programs WHERE slug = :slug", slug=slug)
    published = await client.post(f"{PROGRAMS}/{program['id']}/publish",
                                  headers=world["master"]["headers"])
    assert published.status_code == 200

    # Unchanged content: nothing happens at all.
    assert import_programs.run(folder, TEST_DATABASE_URL, True, None)[0]["action"] == "unchanged"

    # Changed content: version 2, DRAFT, pointing at version 1, which stays PUBLISHED.
    structure = json.loads((folder / "pathfinders" / f"{slug}.json").read_text())
    structure["sections"][0]["requirements"].append({"label": "4", "kind": "FREE"})
    (folder / "pathfinders" / f"{slug}.json").write_text(json.dumps(structure))
    for locale in ("es", "en"):
        path = folder / "pathfinders" / f"{slug}.{locale}.json"
        body = json.loads(path.read_text())
        body["requirements"]["4"] = {"description": "Requisito nuevo"}
        path.write_text(json.dumps(body))

    result = import_programs.run(folder, TEST_DATABASE_URL, True, None)[0]
    assert result["action"] == "new_version"
    versions = await fetch_all(
        "SELECT slug, status, version, previous_version_id FROM programs"
        " WHERE slug = :slug OR slug LIKE :like ORDER BY version",
        slug=slug, like=f"{slug}-v%")
    assert [(row["slug"], row["status"], row["version"]) for row in versions] == [
        (slug, "PUBLISHED", 1), (f"{slug}-v2", "DRAFT", 2)]
    assert str(versions[1]["previous_version_id"]) == str(program["id"])


async def test_the_importer_fails_loudly_when_a_target_does_not_exist(factory, tmp_path):
    import_programs = _load_importer()
    folder = _fixture_with_prefix(tmp_path, factory, label="destino-roto")
    path = folder / "pathfinders" / f"{factory.prefix}-destino-roto.json"
    structure = json.loads(path.read_text())
    structure["sections"][0]["requirements"][0] = {
        "label": "1", "kind": "HONOR", "target_honor_slug": "no-existe-esta-especialidad"}
    path.write_text(json.dumps(structure))
    with pytest.raises(import_programs.ImportError_) as excinfo:
        import_programs.run(folder, TEST_DATABASE_URL, commit=True, operator=None)
    assert "no existe en el catálogo" in str(excinfo.value)
    assert await fetch_one("SELECT id FROM programs WHERE slug = :slug",
                           slug=f"{factory.prefix}-destino-roto") is None


async def test_enrolling_in_a_program_is_audited_with_the_program_id(client, factory, world):
    program = await _program(factory, "auditoria")
    enrollment = await _enroll(client, world["member"], program)
    row = await fetch_one(
        "SELECT metadata_json FROM audit_log WHERE action = 'ENROLL' AND entity_id = :id",
        id=enrollment["id"])
    assert row["metadata_json"]["program_id"] == program["id"]
    assert row["metadata_json"]["honor_id"] is None
