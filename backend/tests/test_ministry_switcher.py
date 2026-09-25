"""024 · Selector de ministerio + catálogo de Guías Mayores.

What is proved here:
  1. `programs.kind` accepts MEDALLION / MASTERY / TRAINING (024) and the JSON of
     `data/master_guide_catalog.json` is what the generator builds (EMC of mundoja + two examples);
  2. migrations/import_master_guide_catalog.py loads it as DRAFT, is idempotent, and `--publish`
     publishes the EMC but never an «ejemplo, sustituir»;
  3. `GET /programs?ministry=master-guides&kind=…` lists them (public: published only; MASTER_GC
     with `status=ALL`: drafts too) and the detail shows sections and requirements;
  4. `GET /auth/me` / `GET /users/me` expose `ministries_available` from the person's clubs (MASTER
     and administration: every active ministry) and `active_ministry`;
  5. `PATCH /users/me/preferences` saves the ministry / club, refuses what is not offered, and a
     choice that stops being available falls back instead of failing;
  6. the honors catalogue keeps filtering by ministry, and nothing changes without a session.
"""

import copy
import importlib.util
import json
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import TEST_DATABASE_URL, fetch_all, fetch_one, module_factory, requires_db

pytestmark = requires_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_DIR / "migrations"
MIGRATION = MIGRATIONS / "024_master_guide_catalog.sql"
DATA = BACKEND_DIR / "data" / "master_guide_catalog.json"
PROGRAMS = "/api/v1/programs"
ME = "/api/v1/auth/me"
PREFS = "/api/v1/users/me/preferences"


def _load(name: str, path: pathlib.Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load("import_master_guide_catalog", MIGRATIONS / "import_master_guide_catalog.py")
builder = _load("build_master_guide_catalog", MIGRATIONS / "catalog_tools" / "build_master_guide_catalog.py")
DATA_JSON = json.loads(DATA.read_text(encoding="utf-8"))

factory = module_factory("ministrysw")


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def _exec(sql: str, **params) -> None:
    async with SessionLocal() as db:
        await db.execute(text(sql), params)
        await db.commit()


@pytest.fixture(scope="module", autouse=True)
async def _migrated():
    """024 twice: idempotent."""
    for _ in range(2):
        async with SessionLocal() as db:
            for statement in _statements(MIGRATION.read_text()):
                await db.execute(text(statement))
            await db.commit()


async def _ministry_ids() -> dict[str, uuid.UUID]:
    rows = await fetch_all("SELECT slug, id FROM ministries WHERE status = 'active'")
    return {row["slug"]: row["id"] for row in rows}


async def _club(factory, label: str, slugs: list[str]) -> dict:
    """A club of this run working with `slugs` (principal first)."""
    club = await factory.org(label, "club")
    ids = await _ministry_ids()
    await _exec("UPDATE organizations SET ministry_id = :m WHERE id = :c", m=ids[slugs[0]], c=uuid.UUID(club["id"]))
    for index, slug in enumerate(slugs):
        await _exec(
            "INSERT INTO organization_ministries (organization_id, ministry_id, created_at)"
            " VALUES (:c, :m, now() + make_interval(secs => :i))",
            c=uuid.UUID(club["id"]), m=ids[slug], i=index,
        )
    return club


async def _member(factory, label: str, club: dict | None, role: str = "STUDENT") -> dict:
    user = await factory.user(label, role, club["id"] if club else None)
    if club:
        await _exec(
            "INSERT INTO club_memberships (id, user_id, club_id, role, status, source, started_at)"
            " VALUES (gen_random_uuid(), :u, :c, :r, 'ACTIVE', 'ADMIN', now())",
            u=uuid.UUID(user["id"]), c=uuid.UUID(club["id"]), r=role,
        )
    return user


def _slugs(items: list[dict]) -> list[str]:
    return [item["slug"] for item in items]


# ----------------------------------------------------------------------------
# 1–3. Catálogo de Guías Mayores
# ----------------------------------------------------------------------------
def test_the_json_is_what_the_generator_builds_and_it_validates():
    assert builder.render() == DATA.read_text(encoding="utf-8")
    importer.validate(DATA_JSON)
    programs = DATA_JSON["programs"]
    emc = [p for p in programs if p["kind"] == "TRAINING"]
    assert [p["code"] for p in emc] == ["EMC-1", "EMC-2", "EMC-3", "EMC-4", "EMC-5"]
    assert all(p["source_url"]["es"] == "https://mundoja.org/clubes/guias-mayores/emc" for p in emc)
    assert all(p["issuer_level"] == "ASSOCIATION" and not p.get("example") for p in emc)
    assert DATA_JSON["author"] == "Mundo J.A (voluntarios)"
    # One example of each of the other two families, marked as such.
    examples = {p["kind"]: p for p in programs if p.get("example")}
    assert set(examples) == {"MASTERY", "MEDALLION"}
    assert all("(ejemplo, sustituir)" in p["names"]["es"] for p in examples.values())
    # The director's certification lists its eight workshops by code.
    director = next(p for p in emc if p["slug"] == "emc-director-conquistadores")
    talleres = next(s for s in director["sections"] if s["slug"] == "talleres")
    assert [r["label"] for r in talleres["requirements"]] == [
        "LEAD 001", "LEAD 002", "LEAD 150", "WILD 101", "EDUC 200", "FINA 100", "PYSO 120", "PYSO 207"]


def test_validation_rejects_broken_data():
    broken = copy.deepcopy(DATA_JSON)
    broken["programs"][0]["kind"] = "CLASS"
    with pytest.raises(importer.ImportError_, match="sólo"):
        importer.validate(broken)
    broken = copy.deepcopy(DATA_JSON)
    broken["programs"][5]["names"]["es"] = "Maestría sin marca"
    with pytest.raises(importer.ImportError_, match="ejemplo"):
        importer.validate(broken)
    broken = copy.deepcopy(DATA_JSON)
    broken["programs"][1]["source_url"]["es"] = "https://example.com"
    with pytest.raises(importer.ImportError_, match="mundoja"):
        importer.validate(broken)


async def test_programs_kind_accepts_the_three_new_families(factory):
    row = await fetch_one("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname = 'programs_kind_check'")
    for kind in ("CLASS", "CURRICULUM", "MEDALLION", "MASTERY", "TRAINING"):
        assert kind in row["d"]
    ids = await _ministry_ids()
    with pytest.raises(Exception, match="programs_kind_check"):
        await _exec(
            "INSERT INTO programs (ministry_id, kind, slug, name) VALUES (:m, 'BADGE', :s, :n)",
            m=ids["master-guides"], s=f"{factory.prefix}-badge", n=factory.name("badge"),
        )


def _run(factory, **kwargs) -> tuple[dict, list]:
    with importer.connect(TEST_DATABASE_URL) as conn:
        report = importer.run(conn, DATA_JSON, operator=factory.email("importer"), prefix=factory.prefix, **kwargs)
        after = importer.db_counts(conn, prefix=factory.prefix)
        conn.commit()
    return report, after


async def test_the_importer_loads_drafts_is_idempotent_and_never_publishes_an_example(client, factory):
    first, after = _run(factory)
    assert first["created"] == 7 and first["requirements_written"] == 63
    assert {row[2] for row in after} == {"DRAFT"}
    assert [row[1] for row in after].count("TRAINING") == 5

    second, again = _run(factory)
    assert second["created"] == 0 and second["updated"] == 0 and second["unchanged"] == 7
    assert again == after

    published, final = _run(factory, publish=True)
    assert published["published"] == 5 and published["examples_not_published"] == 2
    status = {row[0]: row[2] for row in final}
    assert status[f"{factory.prefix}-emc-director-conquistadores"] == "PUBLISHED"
    assert status[f"{factory.prefix}-ejemplo-medallon"] == "DRAFT"
    assert status[f"{factory.prefix}-ejemplo-maestria-naturaleza"] == "DRAFT"

    # A published program is never rewritten by a rerun.
    third, _ = _run(factory)
    assert len(third["skipped_not_draft"]) == 5 and third["unchanged"] == 2


async def test_programs_endpoint_lists_the_families_by_kind(client, factory):
    _run(factory, publish=True)
    master = await factory.user("master-cat", "MASTER_GC")
    mine = lambda items: [i for i in items if i["slug"].startswith(factory.prefix)]  # noqa: E731

    public = await client.get(PROGRAMS, params={"ministry": "master-guides", "kind": "TRAINING"})
    assert public.status_code == 200, public.text
    training = mine(public.json())
    assert len(training) == 5 and {i["kind"] for i in training} == {"TRAINING"}
    assert {i["status"] for i in training} == {"PUBLISHED"}

    # Examples are drafts: nobody but MASTER_GC sees them.
    hidden = await client.get(PROGRAMS, params={"ministry": "master-guides", "kind": "MEDALLION"})
    assert mine(hidden.json()) == []
    drafts = await client.get(PROGRAMS, params={"ministry": "master-guides", "kind": "MEDALLION", "status": "ALL"},
                              headers=master["headers"])
    assert [i["status"] for i in mine(drafts.json())] == ["DRAFT"]
    mastery = await client.get(PROGRAMS, params={"ministry": "master-guides", "kind": "MASTERY", "status": "ALL"},
                               headers=master["headers"])
    assert [i["name"] for i in mine(mastery.json())] == [f"{factory.prefix} Maestría en Naturaleza (ejemplo, sustituir)"]

    # The certifications never leak into another ministry's list.
    other = await client.get(PROGRAMS, params={"ministry": "pathfinders", "status": "ALL"}, headers=master["headers"])
    assert mine(other.json()) == []
    assert (await client.get(PROGRAMS, params={"ministry": "master-guides", "kind": "BADGE"})).status_code == 422

    detail = await client.get(f"{PROGRAMS}/{training[-1]['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["kind"] == "TRAINING" and body["issuer_level"] == "ASSOCIATION" and body["authority"] == "IAD"
    assert body["attribution"]["source"] == "mundoja.org"
    assert [s["slug"] for s in body["sections"]] == ["servicio", "talleres", "mentoria-y-campo", "portafolio"]
    example = mine(drafts.json())[0]
    assert (await client.get(f"{PROGRAMS}/{example['id']}")).status_code == 404  # a draft does not exist


# ----------------------------------------------------------------------------
# 4–5. Contexto de ministerio de la persona
# ----------------------------------------------------------------------------
async def test_without_a_club_only_pathfinders_is_offered(client, factory):
    solo = await _member(factory, "solo", None)
    me = await client.get(ME, headers=solo["headers"])
    assert me.status_code == 200, me.text
    body = me.json()
    assert _slugs(body["ministries_available"]) == ["pathfinders"]
    assert body["active_ministry"] == "pathfinders"
    assert body["clubs_available"] == [] and body["active_club_id"] is None
    # `/users/me` answers the same.
    assert (await client.get("/api/v1/users/me", headers=solo["headers"])).json()["ministries_available"] == body[
        "ministries_available"]


async def test_the_ministries_come_from_the_club_and_the_principal_is_the_default(client, factory):
    club = await _club(factory, "club-av-gm", ["adventurers", "master-guides"])
    member = await _member(factory, "miembro-av", club)
    body = (await client.get(ME, headers=member["headers"])).json()
    assert _slugs(body["ministries_available"]) == ["pathfinders", "adventurers", "master-guides"]
    assert body["active_ministry"] == "adventurers"
    assert body["active_club_id"] == club["id"]
    [context] = body["clubs_available"]
    assert context["name"] == factory.name("club-av-gm") and context["role"] == "STUDENT"
    assert _slugs(context["ministries"]) == ["adventurers", "master-guides"]


async def test_master_and_administration_get_every_active_ministry(client, factory):
    everyone = sorted(await _ministry_ids())
    for label, role in (("master-min", "MASTER_GC"), ("assoc-min", "ADMIN_ASSOCIATION")):
        user = await factory.user(label, role)
        body = (await client.get(ME, headers=user["headers"])).json()
        assert sorted(_slugs(body["ministries_available"])) == everyone
        assert body["active_ministry"] == "pathfinders"


async def test_the_preference_is_saved_and_only_offered_values_are_accepted(client, factory):
    club = await _club(factory, "club-pref", ["pathfinders", "master-guides"])
    member = await _member(factory, "miembro-pref", club)
    headers = member["headers"]
    assert (await client.get(ME, headers=headers)).json()["active_ministry"] == "pathfinders"

    saved = await client.patch(PREFS, json={"ministry": "master-guides"}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["active_ministry"] == "master-guides"
    assert (await client.get(ME, headers=headers)).json()["active_ministry"] == "master-guides"
    row = await fetch_one(
        "SELECT m.slug FROM users u JOIN ministries m ON m.id = u.active_ministry_id WHERE u.id = :id",
        id=uuid.UUID(member["id"]),
    )
    assert row["slug"] == "master-guides"

    refused = await client.patch(PREFS, json={"ministry": "adventurers"}, headers=headers)
    assert refused.status_code == 422 and refused.json()["detail"] == "ministry_not_available"
    assert (await client.patch(PREFS, json={"ministry": "no existe"}, headers=headers)).status_code == 422
    assert (await client.patch(PREFS, json={"role": "MASTER_GC"}, headers=headers)).status_code == 422
    other = await _club(factory, "club-ajeno", ["adventurers"])
    wrong_club = await client.patch(PREFS, json={"club_id": other["id"]}, headers=headers)
    assert wrong_club.status_code == 422 and wrong_club.json()["detail"] == "club_not_available"
    assert (await client.patch(PREFS, json={"club_id": club["id"]}, headers=headers)).json()["active_club_id"] == club["id"]

    # Still master-guides (the refused calls wrote nothing); null goes back to the default.
    assert (await client.get(ME, headers=headers)).json()["active_ministry"] == "master-guides"
    reset = await client.patch(PREFS, json={"ministry": None}, headers=headers)
    assert reset.json()["active_ministry"] == "pathfinders"

    # Leaving the club: the saved choice stops being available and falls back, never an error.
    await client.patch(PREFS, json={"ministry": "master-guides"}, headers=headers)
    await _exec("UPDATE club_memberships SET status = 'ENDED' WHERE user_id = :u", u=uuid.UUID(member["id"]))
    await _exec("UPDATE users SET organization_id = NULL WHERE id = :u", u=uuid.UUID(member["id"]))
    body = (await client.get(ME, headers=headers)).json()
    assert body["active_ministry"] == "pathfinders" and _slugs(body["ministries_available"]) == ["pathfinders"]


async def test_a_director_attached_to_a_club_sees_its_ministries(client, factory):
    club = await _club(factory, "club-dir", ["adventurers"])
    director = await factory.user("director-av", "CLUB_DIRECTOR", club["id"])  # no membership row
    body = (await client.get(ME, headers=director["headers"])).json()
    assert _slugs(body["ministries_available"]) == ["pathfinders", "adventurers"]
    assert body["clubs_available"][0]["role"] == "CLUB_DIRECTOR"
    assert body["active_ministry"] == "adventurers"


# ----------------------------------------------------------------------------
# 6. Filtros por ministerio y sin sesión
# ----------------------------------------------------------------------------
async def _honor(factory, label: str, ministry: str, status: str) -> str:
    honor_id = uuid.uuid4()
    ids = await _ministry_ids()
    await _exec(
        "INSERT INTO honors (id, ministry_id, name, slug, active, status) VALUES (:id, :m, :n, :s, true, :st)",
        id=honor_id, m=ids[ministry], n=factory.name(label), s=f"{factory.prefix}-{label}", st=status,
    )
    return str(honor_id)


async def test_the_honors_catalogue_filters_by_ministry_and_drafts_stay_hidden(client, factory):
    published = await _honor(factory, "av-publicado", "adventurers", "PUBLISHED")
    draft = await _honor(factory, "av-borrador", "adventurers", "DRAFT")
    pathfinder = await _honor(factory, "conq-publicado", "pathfinders", "PUBLISHED")
    master = await factory.user("master-honors", "MASTER_GC")
    ids = lambda response: {row["id"] for row in response.json()}  # noqa: E731

    adventurers = await client.get("/api/v1/honors", params={"ministry": "adventurers", "q": factory.prefix})
    assert ids(adventurers) == {published}
    default = await client.get("/api/v1/honors", params={"q": factory.prefix})  # no session, no ministry
    assert ids(default) == {pathfinder}
    staff = await client.get("/api/v1/honors", params={"ministry": "adventurers", "status": "ALL", "q": factory.prefix},
                             headers=master["headers"])
    assert ids(staff) == {published, draft}
    anonymous_all = await client.get("/api/v1/honors", params={"ministry": "adventurers", "status": "ALL"})
    assert anonymous_all.status_code == 403


async def test_without_a_session_there_is_no_context_and_no_preference(client, factory):
    assert (await client.get(ME)).status_code == 401
    assert (await client.patch(PREFS, json={"ministry": "adventurers"})).status_code == 401
    # The public programs list keeps asking for a ministry, as before.
    assert (await client.get(PROGRAMS)).status_code == 422


# ----------------------------------------------------------------------------
# 7. Certificados de Aventureros: los seis diseños por elementos, emblema y palabra de marca
# ----------------------------------------------------------------------------
# The designs whose ministry art the engine can swap (emblem slot + brand words) or that name no
# ministry. The other three carry the Pathfinder shield / seal baked into background.webp.
ADVENTURER_DESIGNS = ("especialidad-editorial-rojo", "especialidad-academico", "especialidad-reticula-verde")
PATHFINDER_ONLY = ("especialidad-color", "especialidad-dorada", "especialidad-modular-azul")


@pytest.mark.parametrize("slug", PATHFINDER_ONLY)
def test_designs_with_baked_pathfinder_art_stay_pathfinder_only(slug):
    from app.certificates.render import load_template

    template = load_template(slug)
    assert template.ministries == ["pathfinders"] and not template.serves("adventurers", "honor")


@pytest.mark.parametrize("slug", ADVENTURER_DESIGNS)
def test_the_element_designs_serve_adventurers_with_their_emblem(slug):
    from app.certificates.render import default_emblem, fill_svg, load_template, qr_data_url

    template = load_template(slug)
    assert template.serves("adventurers", "honor") and template.serves("pathfinders", "honor")
    data = {"recipient_name": "Ana López", "honor_name": "Estrellas", "issued_date": "2026-09-24",
            "director_name": "Juan Pérez", "instructor_name": "Ana Ruiz", "certificate_no": "CC-TEST000001",
            "church_name": "Iglesia Central", "association_name": "Asociación Norte"}
    images = {"qr": qr_data_url("https://adventist.club/verify/CC-TEST000001")}
    adventurers = fill_svg(template, data, images, "es", "adventurers")
    pathfinders = fill_svg(template, data, images, "es", "pathfinders")
    if "emblem" in template.fields:          # «dorada» prints the church logo as fixed art instead
        assert default_emblem("adventurers") in adventurers
        assert default_emblem("pathfinders") not in adventurers
        assert default_emblem("pathfinders") in pathfinders
    if "conquistadores" in template.strings.get("es", {}):
        assert "AVENTUREROS" in adventurers and "CONQUISTADORES" not in adventurers
        assert "CONQUISTADORES" in pathfinders
        english = fill_svg(template, data, images, "en", "adventurers")
        assert "ADVENTURERS" in english and "PATHFINDERS" not in english


def test_the_retired_maestria_template_stays_retired():
    from app.certificates.render import load_template

    assert load_template("ntam-maestria").listed is False


async def test_the_portfolio_names_the_ministry_of_each_honor(client, factory):
    honor_id = await _honor(factory, "av-portafolio", "adventurers", "PUBLISHED")
    await _exec(
        "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale)"
        " VALUES (:h, 1, 'Requisito 1', true, 'es')",
        h=uuid.UUID(honor_id),
    )
    member = await _member(factory, "miembro-portafolio", None)
    enrolled = await client.post("/api/v1/portfolio/enrollments", json={"honor_id": honor_id},
                                 headers=member["headers"])
    assert enrolled.status_code in (200, 201), enrolled.text
    assert enrolled.json()["honor"]["ministry"] == "adventurers"
    listed = await client.get("/api/v1/portfolio/enrollments", headers=member["headers"])
    [item] = [row for row in listed.json() if row["honor"] and row["honor"]["id"] == honor_id]
    assert item["honor"]["ministry"] == "adventurers"
