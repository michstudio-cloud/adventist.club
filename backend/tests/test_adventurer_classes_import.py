"""Aventureros · las seis clases (currículo GC, español de mundoja.org) en el catálogo de programas.

Three things are proved here:
  1. `data/adventurer_classes.json` (catalog_tools/build_adventurer_classes.py) is complete: the six
     classes with mundoja's exact names, five sections each, every requirement in Spanish and
     English with a `source_url` for both, every award / category target present in the award
     library, and the generator reproduces the file;
  2. migrations/import_adventurer_classes.py loads them as DRAFT programs of `adventurers`
     (authority GC), is idempotent (a rerun writes nothing), refuses to publish while the target
     awards are drafts and publishes with --publish once they are published;
  3. the recommendations service reads the classes: the awards each requirement asks for.

The database tests tag every program, award and category with this run's prefix (the awards are
loaded with import_adventurer_awards.py, also prefixed), so the conftest cleanup removes them.
"""

import copy
import csv
import importlib.util
import json
import pathlib
import sys

import pytest

from app.db import SessionLocal
from app.models import Program
from app.services import program_recommendations
from tests.conftest import TEST_DATABASE_URL, module_factory, requires_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_DIR / "migrations"
DATA = BACKEND_DIR / "data"


def _load(name: str, path: pathlib.Path):
    """migrations/ is not a package: load the scripts by path."""
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load("import_adventurer_classes", MIGRATIONS / "import_adventurer_classes.py")
awards_importer = _load("import_adventurer_awards", MIGRATIONS / "import_adventurer_awards.py")
builder = _load("build_adventurer_classes", MIGRATIONS / "catalog_tools" / "build_adventurer_classes.py")

factory = module_factory("advclasses")

DATA_JSON = json.loads((DATA / "adventurer_classes.json").read_text(encoding="utf-8"))
with (DATA / "adventurer_awards_library.csv").open(encoding="utf-8") as fh:
    LIBRARY = {row["slug"]: row for row in csv.DictReader(fh)}

NAMES = {
    "corderitos": ("Corderitos", "Little Lamb"),
    "aves-madrugadoras": ("Aves Madrugadoras", "Early Bird"),
    "abejas-industriosas": ("Abejas Industriosas", "Busy Bee"),
    "rayos-de-sol": ("Rayos de Sol", "Sunbeam"),
    "constructor": ("Constructor", "Builder"),
    "manos-ayudadoras": ("Manos Ayudadoras", "Helping Hands"),
}


def _requirements(program: dict) -> list[dict]:
    return [r for s in program["sections"] for r in s["requirements"]]


# ----------------------------------------------------------------------------
# 1. The data
# ----------------------------------------------------------------------------
def test_six_classes_with_mundoja_names_sections_and_bilingual_requirements():
    programs = DATA_JSON["programs"]
    assert [p["slug"] for p in programs] == list(NAMES)
    assert DATA_JSON["author"] == "Mundo J.A (voluntarios)"
    for program in programs:
        assert (program["names"]["es"], program["names"]["en"]) == NAMES[program["slug"]]
        assert program["kind"] == "CLASS" and program["ministry"] == "adventurers" and program["authority"] == "GC"
        assert program["source_url"]["es"].startswith("https://mundoja.org/clubes/aventureros/")
        assert [s["slug"] for s in program["sections"]] == [
            "requisitos-basicos", "mi-dios", "yo-mismo", "mi-familia", "mi-mundo"]
        for section in program["sections"]:
            assert section["requirements"], (program["slug"], section["slug"])
            assert section["names"]["es"] and section["names"]["en"]
            assert all(section["source_url"][loc].startswith("https://") for loc in ("es", "en"))
        reqs = _requirements(program)
        assert [r["position"] for r in reqs] == list(range(1, len(reqs) + 1))
        assert len({r["label"] for r in reqs}) == len(reqs)
        for r in reqs:
            assert r["text"]["es"].strip() and r["text"]["en"].strip(), (program["slug"], r["label"])
            assert r["source_url"]["es"] == program["source_url"]["es"]
            assert r["source_url"]["en"].startswith("https://")
            assert r["translation"] == {"es": "mundoja", "en": "unofficial"}
            assert r["label"].split(".")[0] in ("I", "II", "III", "IV", "V") and len(r["label"]) <= 12
        assert program["emblem"]["file"] == f"adventurer_classes/{program['slug']}.webp"
        assert (DATA / program["emblem"]["file"]).stat().st_size < 64 * 1024
    assert sum(len(_requirements(p)) for p in programs) == 121
    # the four GC categories are the manual's own words (p. 10)
    assert [s["names"]["en"] for s in programs[0]["sections"][1:]] == ["My God", "My Self", "My Family", "My World"]
    assert all(s["source_url"]["en"].endswith("#page=10") for s in programs[0]["sections"][1:])


def test_every_target_is_an_award_of_the_library_and_the_book_classes_match():
    for program in DATA_JSON["programs"]:
        book_class = builder.BOOK_CLASS[program["slug"]]
        for r in _requirements(program):
            if r["kind"] == "HONOR":
                assert r["target_honor_slug"].removeprefix("av-") in LIBRARY, r["target_honor_slug"]
                assert r["text"]["en"].startswith("Complete")
            elif r["kind"] == "HONOR_FROM_CATEGORY":
                assert r["target_category_slug"] == "av-naturaleza"
            else:
                assert "target_honor_slug" not in r and "target_category_slug" not in r
            for option in r.get("options", []):
                assert option.removeprefix("av-") in LIBRARY
        awards = program["awards"]
        assert {s.removeprefix("av-") for s in awards["award_book_class"]} == {
            slug for slug, row in LIBRARY.items() if row["class_en"] == book_class}
        # every award the Award Book gives a class is on mundoja's page of that class
        assert awards["award_book_class_not_listed_by_mundoja"] == []
    kinds = [r["kind"] for p in DATA_JSON["programs"] for r in _requirements(p)]
    assert kinds.count("HONOR") == 63 and kinds.count("HONOR_FROM_CATEGORY") == 1 and kinds.count("HONOR_ANY") == 1


def test_the_library_csv_uses_mundojas_class_names():
    assert {row["class_es"] for row in LIBRARY.values() if row["class_en"] == "Builder"} == {"Constructor"}


def test_the_generator_reproduces_the_json():
    assert builder.build() == DATA_JSON


def test_the_resources_file_keeps_authorship_and_links():
    resources = json.loads((DATA / "adventurer_resources.json").read_text(encoding="utf-8"))
    assert [r["slug"] for r in resources["resources"]] == [
        "soy-padre-o-madre", "programa-anual", "ideas-para-reuniones", "ceremonias-y-eventos"]
    for resource in resources["resources"]:
        assert resource["author"] == "Mundo J.A (voluntarios)"
        assert resource["source_url"].startswith("https://mundoja.org/")
        assert 80 < len(resource["summary"]) < 450 and resource["sections"]
    assert set(resources["class_materials"]["classes"]) == set(NAMES)


def test_validation_rejects_broken_data():
    broken = copy.deepcopy(DATA_JSON)
    broken["programs"][0]["sections"][0]["requirements"][0]["text"]["en"] = ""
    with pytest.raises(importer.ImportError_, match="falta el texto en en"):
        importer.validate(broken)
    broken = copy.deepcopy(DATA_JSON)
    broken["programs"][1]["sections"][0]["requirements"][0]["position"] = 7
    with pytest.raises(importer.ImportError_, match="posiciones"):
        importer.validate(broken)
    broken = copy.deepcopy(DATA_JSON)
    broken["programs"][0]["sections"][0]["requirements"][1]["target_honor_slug"] = None
    with pytest.raises(importer.ImportError_, match="HONOR necesita"):
        importer.validate(broken)


def test_image_url_comes_from_the_json_or_the_base_url():
    program = copy.deepcopy(DATA_JSON["programs"][0])
    assert importer.image_url_for(program, None) is None
    assert importer.image_url_for(program, "https://media.example/av/") == "https://media.example/av/corderitos.webp"
    program["emblem"]["url"] = "https://cdn.example/x.webp"
    assert importer.image_url_for(program, "https://media.example/av") == "https://cdn.example/x.webp"


# ----------------------------------------------------------------------------
# 2. The importer against the test database
# ----------------------------------------------------------------------------
def _connect():
    return importer.connect(TEST_DATABASE_URL)


def _load_awards(factory, publish: bool = False) -> None:
    with _connect() as conn:
        awards_importer.run(conn, awards_importer.build(awards_importer.load()), publish=publish,
                            operator=factory.email("awards"), prefix=factory.prefix)
        conn.commit()


def _run(factory, data=DATA_JSON, **kwargs) -> tuple[dict, list]:
    with _connect() as conn:
        report = importer.run(conn, data, operator=factory.email("importer"), prefix=factory.prefix, **kwargs)
        after = importer.db_counts(conn, prefix=factory.prefix)
        conn.commit()
    return report, after


@requires_db
async def test_a_missing_award_fails_loudly_and_writes_nothing(factory):
    data = copy.deepcopy(DATA_JSON)
    data["programs"][0]["sections"][0]["requirements"][1]["target_honor_slug"] = "av-no-existe"
    with _connect() as conn:
        with pytest.raises(importer.ImportError_, match="av-no-existe"):
            importer.run(conn, data, prefix=factory.prefix)
        conn.rollback()
        assert importer.db_counts(conn, prefix=factory.prefix) == []


@requires_db
async def test_the_importer_loads_drafts_and_a_rerun_writes_nothing(factory):
    _load_awards(factory)
    first, after_first = _run(factory, image_base_url="https://media.example/programs/adventurers")
    assert first["created"] == 6 and first["requirements_written"] == 121 and first["texts_written"] == 242
    assert [row[0] for row in after_first] == [f"{factory.prefix}-{slug}" for slug in NAMES]
    assert {row[1:4] for row in after_first} == {("DRAFT", 1, True)}
    assert [row[5] for row in after_first] == [19, 19, 22, 19, 23, 19]
    assert [row[6] for row in after_first] == [13, 13, 9, 9, 11, 10]  # HONOR rows (with category / any)

    second, after_second = _run(factory, image_base_url="https://media.example/programs/adventurers")
    assert second["created"] == 0 and second["updated"] == 0 and second["unchanged"] == 6
    assert second["requirements_written"] == 0 and after_second == after_first

    with _connect() as conn:
        program = conn.execute(
            "SELECT p.id, p.name, p.code, p.authority, p.issuer_level, p.source, p.source_url, p.image_url, p.license"
            " FROM programs p WHERE p.slug = %s", (f"{factory.prefix}-constructor",)).fetchone()
        assert program[1] == f"{factory.prefix} Constructor" and program[2] == f"{factory.prefix}-CONSTRUCTOR"
        assert program[3:7] == ("GC", "CLUB", "mundoja.org", "https://mundoja.org/clubes/aventureros/constructor")
        assert program[7] == "https://media.example/programs/adventurers/constructor.webp"
        assert program[8] == "© GC Youth Ministries, permiso pendiente"
        names = conn.execute("SELECT locale, name FROM program_translations WHERE program_id = %s ORDER BY locale",
                             (program[0],)).fetchall()
        assert names == [("en", f"{factory.prefix} Builder"), ("es", f"{factory.prefix} Constructor")]
        sections = conn.execute(
            "SELECT s.position, s.slug, t.name FROM program_sections s JOIN program_section_translations t"
            " ON t.section_id = s.id AND t.locale = 'en' WHERE s.program_id = %s ORDER BY s.position",
            (program[0],)).fetchall()
        assert [name for *_, name in sections] == ["Basic Requirements", "My God", "My Self", "My Family", "My World"]
        texts = conn.execute(
            "SELECT r.label, r.kind, r.evidence_required, h.slug, c.slug, x.locale, x.description, x.source"
            " FROM program_requirements r JOIN program_requirement_texts x ON x.requirement_id = r.id"
            " LEFT JOIN honors h ON h.id = r.target_honor_id LEFT JOIN honor_categories c ON c.id = r.target_category_id"
            " WHERE r.program_id = %s AND r.label IN ('II.3b', 'III.3', 'V.3') ORDER BY r.position, x.locale",
            (program[0],)).fetchall()
        structure = {t[0]: t[1:5] for t in texts}
        assert structure["III.3"] == ("HONOR", True, f"{factory.prefix}-av-temperance", None)
        assert structure["V.3"] == ("HONOR", True, None, f"{factory.prefix}-av-naturaleza")
        assert structure["II.3b"] == ("FREE", False, None, None)
        rows = {(t[0], t[5]): (t[6], t[7]) for t in texts}
        assert rows[("II.3b", "es")][0].endswith("(además de Jesús) y por qué.")  # mundoja typo fixed
        assert rows[("II.3b", "es")][1] == "mundoja.org"
        assert rows[("II.3b", "en")][1] == "traduccion-no-oficial-mundoja"
        choose = conn.execute(
            "SELECT x.description FROM program_requirements r JOIN program_requirement_texts x"
            " ON x.requirement_id = r.id AND x.locale = 'en' JOIN programs p ON p.id = r.program_id"
            " WHERE p.slug = %s AND r.label = 'V.3'", (f"{factory.prefix}-corderitos",)).fetchone()[0]
        assert choose.split("\n") == ["Complete at least two of the following Little Lamb awards:", "Bodies of Water",
                                      "Insects", "Stars", "Weather I", "Zoo Animals"]


@requires_db
async def test_a_changed_draft_is_rewritten_and_publish_waits_for_the_awards(factory):
    _load_awards(factory)
    _run(factory)
    changed = copy.deepcopy(DATA_JSON)
    changed["programs"][3]["sections"][0]["requirements"][0]["text"]["en"] = "Say the Adventurer Law by heart."
    report, _ = _run(factory, changed)
    assert report["updated"] == 1 and report["unchanged"] == 5 and report["requirements_written"] == 19
    with _connect() as conn:
        text = conn.execute(
            "SELECT x.description FROM program_requirement_texts x JOIN program_requirements r ON r.id = x.requirement_id"
            " JOIN programs p ON p.id = r.program_id WHERE p.slug = %s AND r.position = 1 AND x.locale = 'en'",
            (f"{factory.prefix}-rayos-de-sol",)).fetchone()[0]
        assert text == "Say the Adventurer Law by heart."

    # the awards are drafts: publishing the classes fails and writes nothing
    with _connect() as conn:
        with pytest.raises(importer.ImportError_, match="awards de destino sin publicar"):
            importer.run(conn, DATA_JSON, publish=True, prefix=factory.prefix)
        conn.rollback()
    _, after = _run(factory)
    assert {row[1] for row in after} == {"DRAFT"}

    _load_awards(factory, publish=True)
    report, after = _run(factory, publish=True)
    assert report["published"] == 6 and {row[1] for row in after} == {"PUBLISHED"}
    with _connect() as conn:
        missing = conn.execute("SELECT count(*) FROM programs WHERE slug LIKE %s AND published_at IS NULL",
                               (f"{factory.prefix}-%",)).fetchone()[0]
        assert missing == 0
    # a published class is never rewritten
    again, _ = _run(factory, changed, publish=True)
    assert again["published"] == 0 and len(again["skipped_not_draft"]) == 6 and again["requirements_written"] == 0


# ----------------------------------------------------------------------------
# 3. Recommendations
# ----------------------------------------------------------------------------
@requires_db
async def test_the_recommendations_list_the_awards_each_requirement_asks_for(factory):
    _load_awards(factory)
    _run(factory)
    async with SessionLocal() as db:
        from sqlalchemy import select

        builder_program = (await db.execute(
            select(Program).where(Program.slug == f"{factory.prefix}-constructor"))).scalar_one()
        items, locale = await program_recommendations.build_recommendations(db, builder_program, "es")
        assert locale == "es"
        by_label = {item.label: item for item in items}
        assert by_label["I.3"].kind == "HONOR" and by_label["I.3"].choose == "all"
        assert [h.slug for h in by_label["I.3"].honors] == [f"{factory.prefix}-av-reading-iii"]
        assert by_label["V.3"].kind == "HONOR_FROM_CATEGORY" and by_label["V.3"].choose == "one"
        assert by_label["V.3"].category.slug == f"{factory.prefix}-av-naturaleza"
        # suggestions: published awards of the category only (none while the awards are drafts)
        assert len(by_label["V.3"].honors) <= program_recommendations.MAX_SUGGESTIONS
        assert len([i for i in items if i.kind == "HONOR"]) == 10

        hands = (await db.execute(
            select(Program).where(Program.slug == f"{factory.prefix}-manos-ayudadoras"))).scalar_one()
        items, _ = await program_recommendations.build_recommendations(db, hands, "en")
        kinds = {item.label: (item.kind, item.choose) for item in items}
        assert kinds["III.1b"] == ("HONOR_ANY", "any")
        assert kinds["IV.1b"] == ("HONOR", "all")
        assert sum(1 for kind, _ in kinds.values() if kind == "HONOR") == 9
