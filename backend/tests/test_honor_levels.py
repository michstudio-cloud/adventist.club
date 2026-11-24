"""Level (class) of an honor: `honors.level_slug` (migration 034).

The Adventurer awards belong to a class (corderitos … manos-ayudadoras) or are `multinivel`. The level comes
from the 2020 Award Book through the importer, `data/adventurer_awards_descriptions.csv` lists it per award,
and `GET /honors?level=` filters by it. Production migrations are applied by hand, so the API must also work
while the column does not exist yet.
"""
import importlib.util
import json
import pathlib
import sys
import time
import uuid

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.routers import honors as honors_router
from tests.conftest import module_factory, requires_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_DIR / "migrations"
DATA = BACKEND_DIR / "data"
HONORS = "/api/v1/honors"
LEVELS = {"corderitos", "aves-madrugadoras", "abejas-industriosas", "rayos-de-sol", "constructor", "manos-ayudadoras",
          "multinivel"}


def _load(name: str, path: pathlib.Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load("import_adventurer_awards", MIGRATIONS / "import_adventurer_awards.py")
descriptions = _load("build_adventurer_descriptions", MIGRATIONS / "catalog_tools" / "build_adventurer_descriptions.py")
BOOK = {a["slug"]: a for a in json.loads((DATA / "adventurer_awards.json").read_text(encoding="utf-8"))["awards"]}
CLASS_PROGRAMS = {p["slug"] for p in json.loads((DATA / "adventurer_classes.json").read_text(encoding="utf-8"))["programs"]}


# ---------- Data (no database) ----------

def test_descriptions_csv_is_up_to_date():
    expected = descriptions.render(descriptions.rows())
    assert (DATA / "adventurer_awards_descriptions.csv").read_text(encoding="utf-8") == expected


def test_every_award_has_one_level_and_it_is_a_class_program():
    rows = descriptions.rows()
    assert {r["slug"] for r in rows} == set(BOOK)
    assert {r["level_slug"] for r in rows} == LEVELS
    assert LEVELS - {"multinivel"} == CLASS_PROGRAMS
    by_slug = {r["slug"]: r for r in rows}
    assert by_slug["acts-of-kindness"]["level_slug"] == "rayos-de-sol" and by_slug["acts-of-kindness"]["level_order"] == 4
    assert by_slug["dogs"]["level_slug"] == "multinivel" and by_slug["dogs"]["level_order"] == ""
    assert by_slug["community-helpers"]["level_slug"] == "corderitos"


def test_the_importer_writes_the_same_level_as_the_csv():
    plans = {p["slug"]: p["level_slug"] for p in importer.build(importer.load())}
    assert plans == {r["slug"]: r["level_slug"] for r in descriptions.rows()}


def test_the_migration_backfill_covers_every_class_sentence():
    sql = (MIGRATIONS / "034_honor_level.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS level_slug" in sql
    for row in descriptions.rows():
        sentence = row["description_es"].split("\n\n")[-1]
        assert f"'%{sentence}%'" in sql and f"THEN '{row['level_slug']}'" in sql, row["slug"]


def test_level_query_is_parsed_strictly():
    parse = honors_router.parse_levels
    assert parse(None) == [] and parse("") == []
    assert parse("Corderitos, multinivel,corderitos") == ["corderitos", "multinivel"]
    assert parse("x' OR 1=1--,rayos-de-sol,,-bad") == ["rayos-de-sol"]


# ---------- API (database) ----------

factory = module_factory("hlevel")


async def _award(factory, label: str, level: str | None) -> dict:
    honor_id, name = uuid.uuid4(), factory.name(label)
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='adventurers'"))).scalar_one()
        await db.execute(text(
            "INSERT INTO honors (id, ministry_id, name, slug, active, status, level_slug)"
            " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED', :level)"),
            {"id": honor_id, "m": ministry, "name": name, "slug": f"{factory.prefix}-{label}", "level": level})
        await db.commit()
    return {"id": str(honor_id), "name": name}


async def _ids(client, prefix: str, **params) -> set[str]:
    response = await client.get(HONORS, params={"ministry": "adventurers", "q": prefix, **params})
    assert response.status_code == 200, response.text
    return {row["id"] for row in response.json()}


@requires_db
async def test_list_and_detail_carry_the_level_and_filter_by_it(client, factory):
    lamb = await _award(factory, "corderito", "corderitos")
    any_class = await _award(factory, "multi", "multinivel")
    sunbeam = await _award(factory, "rayito", "rayos-de-sol")
    unset = await _award(factory, "sin-nivel", None)

    rows = {r["id"]: r for r in (await client.get(HONORS, params={"ministry": "adventurers", "q": factory.prefix})).json()}
    assert rows[lamb["id"]]["level_slug"] == "corderitos"
    assert rows[unset["id"]]["level_slug"] is None

    assert await _ids(client, factory.prefix, level="corderitos") == {lamb["id"]}
    # a class plus the awards that fit any class, as the web asks for them
    assert await _ids(client, factory.prefix, level="corderitos,multinivel") == {lamb["id"], any_class["id"]}
    assert await _ids(client, factory.prefix) == {lamb["id"], any_class["id"], sunbeam["id"], unset["id"]}
    assert await _ids(client, factory.prefix, level="no-such-class") == set()
    assert await _ids(client, factory.prefix, level="'; DROP TABLE honors;--") == {lamb["id"], any_class["id"], sunbeam["id"], unset["id"]}

    detail = await client.get(f"{HONORS}/{sunbeam['id']}")
    assert detail.status_code == 200 and detail.json()["level_slug"] == "rayos-de-sol"


@requires_db
async def test_without_the_column_the_level_is_null_and_the_filter_ignored(client, factory, monkeypatch):
    lamb = await _award(factory, "corderito-b", "corderitos")
    sunbeam = await _award(factory, "rayito-b", "rayos-de-sol")
    # as in production before 034 is applied: the lookup says «missing» and is not repeated for a minute
    monkeypatch.setitem(honors_router._level_column, "ready", False)
    monkeypatch.setitem(honors_router._level_column, "checked_at", time.monotonic())

    assert {lamb["id"], sunbeam["id"]} <= await _ids(client, factory.prefix, level="corderitos")
    rows = (await client.get(HONORS, params={"ministry": "adventurers", "q": factory.prefix})).json()
    assert all(row["level_slug"] is None for row in rows)
    assert (await client.get(f"{HONORS}/{lamb['id']}")).json()["level_slug"] is None


@pytest.mark.parametrize("found", [True, False])
async def test_the_column_lookup_is_cached(monkeypatch, found):
    calls = []

    class _Result:
        def first(self):
            return (1,) if found else None

    class _Db:
        async def execute(self, _statement):
            calls.append(1)
            return _Result()

    monkeypatch.setitem(honors_router._level_column, "ready", False)
    monkeypatch.setitem(honors_router._level_column, "checked_at", float("-inf"))
    assert await honors_router.level_column_ready(_Db()) is found
    assert await honors_router.level_column_ready(_Db()) is found
    assert len(calls) == 1   # found: cached for good; missing: not asked again within the minute
