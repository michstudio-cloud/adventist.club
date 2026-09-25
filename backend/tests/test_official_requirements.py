"""Official lists from outside the Pathfinder Wiki, the wiki importer's guard, and category names.

Proved here:
  1. the data files are well formed: data/official_requirements (importer's own check, path =
     <locale>/<slug>.json, never an unofficial or wiki source) and data/category_translations.json;
  2. import_official_requirements.py writes a list, a second run writes nothing, an edit rewrites only
     that row (ids survive), an unofficial translation of the same locale is replaced in place, and an
     instructor list or an official list of another source is never touched;
  3. import_wiki_requirements.py leaves alone a language that already has an official list from another
     source (guiasmayores.com…) instead of adding the page as a second list;
  4. import_category_translations.py upserts idempotently and moves legacy 'pt' rows to 'pt-BR', so
     `?locale=pt` translates the categories of every ministry.
"""
import importlib.util
import json
import os
import pathlib
import sys
import uuid

import pytest

from tests.conftest import TEST_DATABASE_URL, module_factory, requires_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_DIR / "migrations"
GM_URL = "https://www.guiasmayores.com/uploads/1/1/3/1/1131412/nudos_avanzado.pdf"


def _load(name: str, path: pathlib.Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


official = _load("import_official_requirements", MIGRATIONS / "import_official_requirements.py")
categories = _load("import_category_translations", MIGRATIONS / "import_category_translations.py")

factory = module_factory("offreq")


# ----------------------------------------------------------------------------
# 1. Files
# ----------------------------------------------------------------------------
def test_the_official_files_are_well_formed():
    docs = official.load_documents([str(official.DATA)])
    assert docs, "no official files"
    for doc in docs:
        assert doc["source_url"].startswith("https://"), doc["origin"]
        for req in doc["requirements"]:
            assert not req["description"].lstrip().startswith(f"{req['position']}."), doc["origin"]
    assert len({(d["locale"], d["honor_slug"]) for d in docs}) == len(docs)


def test_check_document_refuses_what_it_must_not_load():
    good = {"honor_slug": "x", "locale": "es", "source": "guiasmayores.com", "source_url": GM_URL, "license": None,
            "requirements": [{"position": 1, "description": "Tener la especialidad de Nudos."}]}
    assert official.check_document(good)["requirements"][0]["is_theoretical"] is None
    for change in ({"source": "pathfinder-wiki"}, {"source": "traduccion-no-oficial-guiasmayores.com"},
                   {"source": ""}, {"source_url": ""}, {"locale": "pt-br"}, {"requirements": []},
                   {"requirements": [{"position": 2, "description": "a"}]},
                   {"requirements": [{"position": 1, "description": "a", "is_theoretical": "no"}]},
                   {"requirements": [{"position": 1, "description": " "}]}, {"extra": 1}):
        with pytest.raises(official.DocumentError):
            official.check_document({**good, **change})


def test_the_category_file_is_well_formed():
    data = categories.load()
    slugs = {(e["ministry"], e["slug"]) for e in data["categories"]}
    for wanted in ("adra", "doctrinal", "community-services", "florida-conference", "masters"):
        assert ("pathfinders", wanted) in slugs
    assert {s for m, s in slugs if m == "adventurers"} == {"av-comunidad", "av-manualidades", "av-hogar",
                                                            "av-naturaleza", "av-recreacion", "av-espiritual"}
    assert all("pt-BR" in e["names"] and "pt" not in e["names"] for e in data["categories"])
    assert data["legacy_locales"] == {"pt": "pt-BR"}


# ----------------------------------------------------------------------------
# 2–4. Against the test database
# ----------------------------------------------------------------------------
def _connect():
    return official.connect(TEST_DATABASE_URL)


def _honor(factory, label: str, lists: dict[str, list[tuple[str, str | None]]]) -> dict:
    honor_id, slug = uuid.uuid4(), f"{factory.prefix}-{label}"
    with _connect() as conn:
        ministry = conn.execute("SELECT id FROM ministries WHERE slug = 'pathfinders'").fetchone()[0]
        conn.execute("INSERT INTO honors (id, ministry_id, name, slug, active, status) VALUES (%s, %s, %s, %s, true,"
                     " 'PUBLISHED')", (honor_id, ministry, factory.name(label), slug))
        for locale, rows in lists.items():
            for position, (text, source) in enumerate(rows, 1):
                conn.execute("INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale,"
                             " source) VALUES (%s, %s, %s, false, %s, %s)", (honor_id, position, text, locale, source))
        conn.commit()
    return {"id": str(honor_id), "slug": slug}


def _doc(slug: str, texts: list[str], source: str = "guiasmayores.com", locale: str = "es") -> dict:
    return official.check_document({
        "honor_slug": slug, "ministry": "pathfinders", "locale": locale, "source": source, "source_url": GM_URL,
        "license": None, "requirements": [{"position": i, "description": t} for i, t in enumerate(texts, 1)]})


def _run(factory, docs) -> dict:
    with _connect() as conn:
        report = official.run(conn, docs, operator=factory.email("importer"))
        conn.commit()
    return report


def _rows(honor: dict, locale: str) -> list[tuple]:
    with _connect() as conn:
        return conn.execute("SELECT position, description, is_theoretical, source, source_url, id"
                            " FROM honor_requirements WHERE honor_id = %s AND locale = %s ORDER BY position",
                            (honor["id"], locale)).fetchall()


def _audits(honor: dict) -> int:
    with _connect() as conn:
        return conn.execute("SELECT count(*) FROM audit_log WHERE entity_id = %s AND action = %s",
                            (honor["id"], official.AUDIT_ACTION)).fetchone()[0]


SPANISH = ["Tener la especialidad de Nudos.", "Citar 3 historias bíblicas con cuerdas.", "Hacer un cuadro de nudos."]


@requires_db
async def test_import_is_idempotent_and_replaces_an_unofficial_translation_in_place(factory):
    honor = _honor(factory, "nudos", {"es": [("Traducción uno", "traduccion-no-oficial-pathfinder-wiki"),
                                             ("Traducción dos", "traduccion-no-oficial-pathfinder-wiki")]})
    before = _rows(honor, "es")
    first = _run(factory, [_doc(honor["slug"], SPANISH)])
    assert first["unofficial_replaced"] == [f"es/{honor['slug']}"]
    assert first["rows_updated"] == 2 and first["rows_inserted"] == 1 and first["lists_written"] == 1
    rows = _rows(honor, "es")
    assert [r[1] for r in rows] == SPANISH and {r[3] for r in rows} == {"guiasmayores.com"}
    assert [r[5] for r in rows[:2]] == [r[5] for r in before]  # same ids: progress rows keep pointing at them
    assert [r[2] for r in rows] == [False, False, True]  # is_theoretical kept on update, default on insert
    assert _audits(honor) == 1

    second = _run(factory, [_doc(honor["slug"], SPANISH)])
    assert second["lists_unchanged"] == 1 and second["rows_inserted"] == second["rows_updated"] == 0
    assert _rows(honor, "es") == rows and _audits(honor) == 1

    shorter = _run(factory, [_doc(honor["slug"], SPANISH[:2])])
    assert shorter["rows_deleted"] == 1 and [r[0] for r in _rows(honor, "es")] == [1, 2]


@requires_db
async def test_instructor_and_other_official_lists_are_never_overwritten(factory):
    instructor = _honor(factory, "instructor", {"es": [("Lista del instructor", None)]})
    wiki = _honor(factory, "wiki", {"es": [("Lista oficial de la wiki", "pathfinder-wiki")]})
    before = (_rows(instructor, "es"), _rows(wiki, "es"))
    report = _run(factory, [_doc(instructor["slug"], SPANISH), _doc(wiki["slug"], SPANISH)])
    assert report["kept_instructor_list"] == [f"es/{instructor['slug']}"]
    assert report["kept_official_list"] == [f"es/{wiki['slug']}"]
    assert (_rows(instructor, "es"), _rows(wiki, "es")) == before


@requires_db
async def test_the_wiki_importer_does_not_add_a_second_list_next_to_another_official_one(factory, tmp_path):
    honor = _honor(factory, "guiasmayores", {"es": [(t, "guiasmayores.com") for t in SPANISH]})
    title = f"{factory.prefix} Knot Tying Advanced"
    with _connect() as conn:
        conn.execute("UPDATE honors SET wiki_title = %s WHERE id = %s", (title, honor["id"]))
        conn.commit()
    page = ('<div class="mw-content-ltr mw-parser-output"><p>translation is 100% complete</p>'
            "<p><b>1. Uno</b></p><p><b>2. Dos</b></p><p><b>3. Tres</b></p></div><div class=\"printfooter\"></div>")
    folder = tmp_path / "requirements" / "es"
    folder.mkdir(parents=True)
    wiki = _load("import_wiki_requirements", MIGRATIONS / "import_wiki_requirements.py")
    (folder / wiki.file_name(title)).write_text(page, encoding="utf-8")
    argv, url = sys.argv, os.environ.get("DATABASE_URL")
    try:
        sys.argv = ["import_wiki_requirements.py", str(tmp_path), "es", "--commit"]
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL
        wiki.main()
    finally:
        sys.argv = argv
        if url is not None:
            os.environ["DATABASE_URL"] = url
    rows = _rows(honor, "es")
    assert [r[1] for r in rows] == SPANISH and {r[3] for r in rows} == {"guiasmayores.com"}


@requires_db
async def test_category_names_are_upserted_and_legacy_pt_moves_to_pt_br(client, factory):
    made = {}
    with _connect() as conn:
        for ministry, label in (("pathfinders", "cat-conq"), ("adventurers", "cat-aven"), ("adventurers", "cat-solo")):
            ministry_id = conn.execute("SELECT id FROM ministries WHERE slug = %s", (ministry,)).fetchone()[0]
            made[label] = conn.execute("INSERT INTO honor_categories (ministry_id, name, slug) VALUES (%s, %s, %s)"
                                       " RETURNING id", (ministry_id, factory.name(label),
                                                         f"{factory.prefix}-{label}")).fetchone()[0]
        conn.execute("INSERT INTO honor_category_translations (category_id, locale, name) VALUES"
                     " (%s, 'pt', 'Recreação'), (%s, 'pt', 'Lar')", (made["cat-aven"], made["cat-solo"]))
        conn.commit()
    data = {"legacy_locales": {"pt": "pt-BR"}, "sources": {"x": "test"}, "categories": [
        {"ministry": "pathfinders", "slug": f"{factory.prefix}-cat-conq", "names": {"en": "Masters", "pt-BR": "Mestrados"},
         "source": "x"},
        {"ministry": "adventurers", "slug": f"{factory.prefix}-cat-aven",
         "names": {"en": "Recreation", "pt-BR": "Atividades Recreativas"}, "source": "x"},
        {"ministry": "adventurers", "slug": f"{factory.prefix}-missing", "names": {"pt-BR": "Nada"}, "source": "x"}]}

    def names(label):
        with _connect() as conn:
            return dict(conn.execute("SELECT locale, name FROM honor_category_translations WHERE category_id = %s",
                                     (made[label],)).fetchall())

    with _connect() as conn:
        first = categories.run(conn, data)
        conn.commit()
    assert first["written"] == 4 and first["missing_categories"] == [f"adventurers/{factory.prefix}-missing"]
    assert first["legacy_deleted"] >= 1 and first["legacy_renamed"] >= 1
    assert names("cat-aven") == {"en": "Recreation", "pt-BR": "Atividades Recreativas"}  # old 'pt' dropped
    assert names("cat-solo") == {"pt-BR": "Lar"}  # no pt-BR yet: the old row is renamed, not lost
    with _connect() as conn:
        second = categories.run(conn, data)
        conn.commit()
    assert second["written"] == 0 and second["unchanged"] == 4 and second["legacy_deleted"] == 0

    for ministry, label, wanted in (("pathfinders", "cat-conq", "Mestrados"),
                                    ("adventurers", "cat-aven", "Atividades Recreativas")):
        listed = (await client.get("/api/v1/honors/categories", params={"ministry": ministry, "locale": "pt"})).json()
        assert {c["slug"]: c["name"] for c in listed}[f"{factory.prefix}-{label}"] == wanted
