"""Bloque F · contenido: clases de Conquistadores y Guía Mayor desde la Pathfinder Wiki.

Three things are proved here:
  1. the parser reads both templates of the wiki's class pages (label cell #8080e0 + text
     cell #c0c0ff, the advanced level after the ribbon <h1>) and the heading layout of the
     Master Guide pages, sub-items included;
  2. `migrations/data/ay_classes.json` is well formed: every program, section and requirement
     the importer needs, CC BY-SA attribution on every row, labels that fit the column;
  3. `migrations/import_ay_classes.py` loads programs as DRAFT through ONE SQL transaction, is
     idempotent (a rerun leaves the very same rows), never touches a published program, and
     fails loudly — writing nothing — when a target is missing.

The database tests work on a COPY of two programs whose slugs, codes and targets carry this
run's prefix, so the surgical cleanup of conftest removes every row they create.
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

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_DIR / "migrations"
DATA_FILE = MIGRATIONS / "data" / "ay_classes.json"


def _load(name: str, path: pathlib.Path):
    """migrations/ is not a package: load the scripts by path."""
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load("import_ay_classes", MIGRATIONS / "import_ay_classes.py")
parser = _load("parse_wiki_class", MIGRATIONS / "catalog_tools" / "parse_wiki_class.py")

factory = module_factory("ayclasses")


# ----------------------------------------------------------------------------
# 1. Parser
# ----------------------------------------------------------------------------
PAGE_HEAD = ('<html><head><title>Investiture Achievement/Friend/Serving Others - Pathfinder Wiki</title>'
             '</head><body><div class="mw-content-ltr mw-parser-output" lang="en">')
PAGE_FOOT = '<div class="printfooter">x</div></body></html>'

NEW_TEMPLATE = """
<div class="mw-parser-output"><table width="100%" class="ansreq-header"><tbody><tr>
<td bgcolor="#8080e0"><p><font size="3"><i><b>2b</b></i></font></p></td>
<td bgcolor="c0c0ff"><p><b><font size="3">Journal your thoughts by asking these questions:
</font></b></p><b><font size="3"><ul><li>What did I learn about God?</li>
<li>What did I learn about myself?</li></ul>
<dl><dd>You may journal through writing.</dd></dl></font></b></td>
<td class="nomobile"><p>[<span>Show/Hide Answer</span>]</p></td></tr></tbody></table></div>
<div class="toccolours mw-collapsible"><p>Instructor notes that are never imported.</p></div>
"""
OLD_TEMPLATE = """
<table width="100%" cellspacing="0"><tbody><tr><td bgcolor="#8080e0"><p><font size="2">
<b><i>Requirement {label}</i></b></font></p></td></tr>
<tr><td bgcolor="#c0c0ff"><p><font size="3"><b>{text}</b></font></p></td></tr></tbody></table>
<p>Help text for the instructor.</p>
"""


def test_the_parser_reads_both_templates_and_the_advanced_level():
    page = (PAGE_HEAD + NEW_TEMPLATE
            + OLD_TEMPLATE.format(label="3",
                                  text='Complete the <a href="/w/AY_Honors/Knot_Tying">Knot Tying</a> honor.')
            + "<h1>Trail Friend</h1>"
            + OLD_TEMPLATE.format(label="4", text="Complete Friend requirements.")
            + PAGE_FOOT)
    rows = parser.parse(page)
    assert [(r["label"], r["advanced"]) for r in rows] == [("2b", False), ("3", False), ("4", True)]
    assert rows[0]["text"] == "Journal your thoughts by asking these questions:"
    assert rows[0]["sub_items"] == ["  What did I learn about God?", "  What did I learn about myself?",
                                    "  You may journal through writing."]
    assert "Instructor notes" not in json.dumps(rows) and "Help text" not in json.dumps(rows)
    assert rows[1]["honor_links"] == ["Knot Tying"]
    assert parser.advanced_heading(page) == "Trail Friend"
    assert parser.title_parts(page) == ["Investiture Achievement", "Friend", "Serving Others"]


def test_the_parser_reads_the_master_guide_headings():
    page = (PAGE_HEAD
            + '<h2 id="mw-toc-heading">Contents</h2>'
            + '<h2><span class="mw-headline">7. Enhance knowledge of Church Heritage:</span></h2>'
            + "<p>Help text.</p>"
            + '<h3><span class="mw-headline">a. Earn the <a href="/w/AY_Honors/Adventist_Pioneer_Heritage">'
              "Adventist Pioneer Heritage</a> Honor.</span></h3>"
            + '<h4><span class="mw-headline">i. The Pathfinder Story</span></h4>'
            + '<h3><span class="mw-headline">Some explanation heading</span></h3>'
            + '<h2><span class="mw-headline">8. Read a book .</span></h2>'
            + '<h2><span class="mw-headline">References</span></h2>'
            + PAGE_FOOT)
    rows = parser.parse_headings(page)
    assert [r["label"] for r in rows] == ["7", "8"]
    assert rows[0]["sub_items"] == ["  a. Earn the Adventist Pioneer Heritage Honor.",
                                    "    i. The Pathfinder Story"]
    assert rows[0]["honor_links"] == ["Adventist Pioneer Heritage"]
    assert rows[1]["text"] == "Read a book."


# ----------------------------------------------------------------------------
# 2. The data file
# ----------------------------------------------------------------------------
def _data() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def test_the_data_file_is_complete_and_attributed():
    data = _data()
    importer.validate(data)  # raises on any structural problem
    assert data["license"] == "CC BY-SA 3.0" and data["source"] == "pathfinder-wiki"
    slugs = {p["slug"]: p for p in data["programs"]}
    for slug in ("amigo", "companero", "explorador", "orientador", "viajero", "guia", "guia-mayor"):
        assert slug in slugs, slug
    assert slugs["guia-mayor"]["kind"] == "MASTER_GUIDE"
    assert slugs["guia-mayor"]["ministry"] == "master-guides"
    assert slugs["guia-mayor"]["issuer_level"] == "ASSOCIATION"
    for program in data["programs"]:
        assert program["license"] == "CC BY-SA 3.0" and program["source_url"]["en"].startswith(
            "https://wiki.pathfindersonline.org/w/")
        for section in program["sections"]:
            for requirement in section["requirements"]:
                assert len(requirement["label"]) <= 12
                assert requirement["text"]["en"]
                for locale in requirement["text"]:
                    assert requirement["source_url"][locale].startswith("https://wiki.pathfindersonline.org/w/")
        if program.get("level") == "ADVANCED":
            prerequisites = [r for s in program["sections"] for r in s["requirements"]
                             if r["kind"] == "PROGRAM"]
            assert [r["target_program_slug"] for r in prerequisites] == [program["base_program_slug"]]


def test_the_generated_sql_is_one_transaction_of_drafts_with_final_counts():
    sql = importer.build_sql(_data(), "someone@example.com")
    assert sql.count("BEGIN;") == 1 and sql.rstrip().endswith("COMMIT;")
    assert "'PUBLISHED'" not in sql.replace("status <> 'DRAFT'", "")
    assert "-- Final counts" in sql
    assert importer.build_sql(_data(), "someone@example.com") == sql  # deterministic


# ----------------------------------------------------------------------------
# 3. The importer against the database
# ----------------------------------------------------------------------------
async def _category(factory, label: str) -> str:
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text("INSERT INTO honor_categories (id, ministry_id, name, slug)"
                              " VALUES (:id, :m, :name, :slug)"),
                         {"id": uuid.uuid4(), "m": ministry, "name": factory.name(label),
                          "slug": f"{factory.prefix}-{label}"})
        await db.commit()
    return f"{factory.prefix}-{label}"


async def _honor(factory, label: str) -> str:
    async with SessionLocal() as db:
        ministry = (await db.execute(text("SELECT id FROM ministries WHERE slug='pathfinders'"))).scalar_one()
        await db.execute(text("INSERT INTO honors (id, ministry_id, name, slug, active, status, version)"
                              " VALUES (:id, :m, :name, :slug, true, 'PUBLISHED', 1)"),
                         {"id": uuid.uuid4(), "m": ministry, "name": factory.name(label),
                          "slug": f"{factory.prefix}-{label}"})
        await db.commit()
    return f"{factory.prefix}-{label}"


async def _prefixed(factory, label: str) -> dict:
    """The basic Friend class, its advanced level and the Master Guide, renamed for this run;
    every honour / category target points at a row created for this run."""
    data = copy.deepcopy(_data())
    wanted = {"amigo", "guia-mayor"} | {p["slug"] for p in data["programs"]
                                        if p.get("base_program_slug") == "amigo"}
    data["programs"] = [p for p in data["programs"] if p["slug"] in wanted]
    category = await _category(factory, f"{label}-cat")
    honor = await _honor(factory, f"{label}-hon")
    for program in data["programs"]:
        program["slug"] = f"{factory.prefix}-{label}-{program['slug']}"
        program["code"] = f"{factory.prefix}-{label}-{program['code']}"[:40]
        for requirement in (r for s in program["sections"] for r in s["requirements"]):
            if requirement.get("target_program_slug"):
                requirement["target_program_slug"] = f"{factory.prefix}-{label}-{requirement['target_program_slug']}"
            if requirement.get("target_category_slug"):
                requirement["target_category_slug"] = category
            if requirement.get("target_honor_slug"):
                requirement["target_honor_slug"] = honor
    # Make sure both honour kinds are exercised even if the wiki text changes.
    first = data["programs"][0]["sections"][0]["requirements"]
    first[0].update(kind="HONOR_FROM_CATEGORY", target_category_slug=category)
    first[0].pop("target_honor_slug", None)
    if len(first) > 1:
        first[1].update(kind="HONOR", target_honor_slug=honor)
        first[1].pop("target_category_slug", None)
    importer.validate(data)
    return data


async def _snapshot(slugs: list[str]) -> list[tuple]:
    rows = await fetch_all(
        "SELECT p.id AS program, p.slug, p.status, s.id AS section, s.slug AS section_slug,"
        " r.id AS requirement, r.position, r.label, r.kind, r.evidence_required,"
        " r.target_honor_id, r.target_category_id, r.target_program_id,"
        " t.locale, t.description, t.license, t.source, t.source_url"
        " FROM programs p JOIN program_sections s ON s.program_id = p.id"
        " JOIN program_requirements r ON r.section_id = s.id"
        " JOIN program_requirement_texts t ON t.requirement_id = r.id"
        " WHERE p.slug = ANY(:slugs) ORDER BY p.slug, r.position, t.locale", slugs=slugs)
    return [tuple(row.values()) for row in rows]


@requires_db
async def test_the_importer_loads_drafts_and_a_rerun_leaves_the_same_rows(factory):
    data = await _prefixed(factory, "idem")
    slugs = [p["slug"] for p in data["programs"]]
    operator = factory.email("importer")
    notices, _ = importer.execute(importer.build_sql(data, operator), TEST_DATABASE_URL)
    assert sorted(n.split(": ")[1] for n in notices) == ["created"] * len(slugs)

    programs = await fetch_all(
        "SELECT p.slug, p.kind, p.status, p.issuer_level, p.authority, p.license, m.slug AS ministry"
        " FROM programs p JOIN ministries m ON m.id = p.ministry_id WHERE p.slug = ANY(:slugs)", slugs=slugs)
    by_slug = {row["slug"]: row for row in programs}
    assert all(row["status"] == "DRAFT" and row["license"] == "CC BY-SA 3.0" for row in programs)
    master = by_slug[f"{factory.prefix}-idem-guia-mayor"]
    assert (master["kind"], master["ministry"], master["issuer_level"]) == ("CURRICULUM", "master-guides", "ASSOCIATION")
    friend = by_slug[f"{factory.prefix}-idem-amigo"]
    assert (friend["kind"], friend["ministry"], friend["issuer_level"]) == ("CLASS", "pathfinders", "CLUB")

    first = await _snapshot(slugs)
    expected = sum(len(importer.complete_locales(p)) * sum(len(s["requirements"]) for s in p["sections"])
                   for p in data["programs"])
    assert len(first) == expected
    assert all(row[15] == "CC BY-SA 3.0" and row[16] == "pathfinder-wiki" and row[17] for row in first)
    kinds = {row[8] for row in first}
    assert {"FREE", "HONOR"} <= kinds
    honor_rows = [row for row in first if row[8] == "HONOR"]
    assert all(row[9] is True for row in honor_rows)  # HONOR always asks for evidence (§12.7)
    assert any(row[10] for row in honor_rows) and any(row[11] for row in honor_rows)
    advanced = [p for p in data["programs"] if p.get("level") == "ADVANCED"]
    if advanced:
        program_rows = [row for row in first if row[8] == "PROGRAM"]
        target = await fetch_one("SELECT id FROM programs WHERE slug = :s", s=f"{factory.prefix}-idem-amigo")
        assert program_rows and {row[12] for row in program_rows} == {target["id"]}
    translations = await fetch_all(
        "SELECT t.locale, t.name, t.license FROM program_translations t JOIN programs p ON p.id = t.program_id"
        " WHERE p.slug = :s ORDER BY t.locale", s=f"{factory.prefix}-idem-amigo")
    assert [row["locale"] for row in translations] == importer.complete_locales(data["programs"][0])
    audit = await fetch_all("SELECT details FROM audit_log WHERE user_email = :e AND action = 'PROGRAM_IMPORT'",
                            e=operator)
    assert len(audit) == len(slugs)

    # A rerun rewrites the drafts in place: same program ids, same section and requirement ids,
    # same texts — nothing more, nothing less.
    notices, _ = importer.execute(importer.build_sql(data, operator), TEST_DATABASE_URL)
    assert sorted(n.split(": ")[1] for n in notices) == ["updated_draft"] * len(slugs)
    assert await _snapshot(slugs) == first
    assert len(await fetch_all("SELECT id FROM programs WHERE slug = ANY(:slugs)", slugs=slugs)) == len(slugs)


@requires_db
async def test_the_importer_never_touches_a_published_program(factory):
    data = await _prefixed(factory, "pub")
    slug = f"{factory.prefix}-pub-amigo"
    importer.execute(importer.build_sql(data, factory.email("importer")), TEST_DATABASE_URL)
    async with SessionLocal() as db:
        await db.execute(text("UPDATE programs SET status = 'PUBLISHED', published_at = now() WHERE slug = :s"),
                         {"s": slug})
        await db.commit()
    before = await _snapshot([slug])
    changed = copy.deepcopy(data)
    changed["programs"][0]["sections"][0]["requirements"][-1]["text"]["en"] = "Something else entirely."
    notices, _ = importer.execute(importer.build_sql(changed, factory.email("importer")), TEST_DATABASE_URL)
    assert any(n.startswith(f"{slug}: la versión vigente está PUBLISHED") for n in notices)
    assert await _snapshot([slug]) == before


@requires_db
async def test_a_missing_target_fails_loudly_and_writes_nothing(factory):
    data = await _prefixed(factory, "roto")
    for requirement in (r for s in data["programs"][0]["sections"] for r in s["requirements"]):
        if requirement.get("target_category_slug"):
            requirement["target_category_slug"] = "no-existe-esta-categoria"
            break
    with pytest.raises(Exception) as excinfo:
        importer.execute(importer.build_sql(data, factory.email("importer")), TEST_DATABASE_URL)
    assert "no-existe-esta-categoria" in str(excinfo.value)
    assert await fetch_all("SELECT id FROM programs WHERE slug LIKE :like",
                           like=f"{factory.prefix}-roto-%") == []


def test_the_json_must_not_claim_a_target_it_does_not_have():
    data = {"programs": [{
        "kind": "CLASS", "ministry": "pathfinders", "slug": "x", "names": {"en": "X"},
        "sections": [{"slug": "s", "names": {"en": "S"}, "requirements": [
            {"position": 1, "label": "I.1", "kind": "HONOR_FROM_CATEGORY", "text": {"en": "Earn one honor."}}]}]}]}
    with pytest.raises(importer.ImportError_):
        importer.validate(data)
    data["programs"][0]["sections"][0]["requirements"][0]["target_category_slug"] = "nature"
    importer.validate(data)
    assert importer.counts(data)[0]["by_kind"] == {"HONOR_FROM_CATEGORY": 1}
