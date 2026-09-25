"""Traducciones no oficiales de requisitos y nombres (migrations/import_requirement_translations.py).

Proved here:
  1. the translation files in data/requirement_translations are well formed (the importer's own
     check, folder = target locale, nothing left to fill in);
  2. the importer writes a translated list with the origin's positions and `is_theoretical`, a
     second run writes nothing (no rows, no audit entry), an edit rewrites only that row, and a
     shrunk origin removes the surplus row;
  3. it never overwrites a list written by an instructor (no `source`) or an official one
     (pathfinder-wiki), nor a name from another origin, and ignores names in Spanish (the source text);
  4. the API serves the translation: `?locale=es` answers with it, `?locale=pt` finds pt-BR
     (best_locale: exact → language → region).
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
DATA = BACKEND_DIR / "data" / "requirement_translations"
HONORS = "/api/v1/honors"
WIKI_URL = "https://wiki.pathfindersonline.org/w/AY_Honors/Raptors/Requirements"


def _load(name: str, path: pathlib.Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load("import_requirement_translations", MIGRATIONS / "import_requirement_translations.py")
common = _load("translation_common", MIGRATIONS / "catalog_tools" / "translation_common.py")

factory = module_factory("reqtr")


# ----------------------------------------------------------------------------
# 1. Files
# ----------------------------------------------------------------------------
def test_the_translation_files_are_well_formed():
    paths = importer.files([str(DATA)])
    assert paths, "no translation files"
    seen = set()
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        doc = importer.check_document(raw, str(path))
        assert path.parent.name == doc["target_locale"], path
        assert path.stem == doc["honor_slug"], path
        assert (doc["target_locale"], doc["honor_slug"]) not in seen, path
        seen.add((doc["target_locale"], doc["honor_slug"]))
        assert raw["name"] != "" and raw["description"] != "", f"{path}: nombre/descripción sin traducir"
        assert doc["requirements"] or doc["name"], f"{path}: no carga nada"
        if doc["source"] == "traduccion-no-oficial-pathfinder-wiki":
            assert doc["license"] == "CC BY-SA 3.0", path  # share-alike: a derivative keeps the licence
        for req in doc["requirements"]:
            assert not req["description"].lstrip().startswith(f"{req['position']}."), path


def test_check_document_refuses_what_it_must_not_load():
    good = {"honor_slug": "x", "source_locale": "en", "target_locale": "es",
            "source": "traduccion-no-oficial-pathfinder-wiki", "source_url": WIKI_URL, "license": "CC BY-SA 3.0",
            "name": None, "description": None,
            "requirements": [{"position": 1, "description": "Hacer algo", "instructions": None}]}
    assert importer.check_document(good)["requirements"][0]["description"] == "Hacer algo"
    for change in ({"source": "pathfinder-wiki"}, {"source": None}, {"target_locale": "en"},
                   {"target_locale": "pt-br"}, {"name_source": "guiasmayores.com"},
                   {"requirements": [{"position": 1, "description": "a"}, {"position": 1, "description": "b"}]},
                   {"requirements": [{"position": 1, "description": "  "}]}, {"extra": 1}):
        with pytest.raises(importer.DocumentError):
            importer.check_document({**good, **change})


def test_structure_compares_lines_and_markers():
    source = "Do two of the following:\n  a. One\n  b. Two"
    assert common.structure("Hacer dos de los siguientes:\n  a. Uno\n  b. Dos") == common.structure(source)
    assert common.structure("Hacer dos de los siguientes:\n  a) Uno\n  b) Dos") != common.structure(source)
    assert common.structure("Hacer dos de los siguientes: uno y dos") != common.structure(source)
    assert common.wiki_display_name("04 - God the Son (Jesus Christ)") == "God the Son (Jesus Christ)"
    assert common.wiki_display_name("Welding (GC)") == "Welding"


# ----------------------------------------------------------------------------
# 2–4. Against the test database
# ----------------------------------------------------------------------------
def _connect():
    return importer.connect(TEST_DATABASE_URL)


def _honor(factory, label: str, lists: dict[str, list[tuple[str, bool, str | None]]], names: dict | None = None) -> dict:
    """A published pathfinders honour named with this run's prefix; `lists`: locale -> [(text,
    is_theoretical, source)]; `names`: locale -> (name, source)."""
    honor_id, slug = uuid.uuid4(), f"{factory.prefix}-{label}"
    with _connect() as conn:
        ministry = conn.execute("SELECT id FROM ministries WHERE slug = 'pathfinders'").fetchone()[0]
        conn.execute("INSERT INTO honors (id, ministry_id, name, slug, active, status) VALUES (%s, %s, %s, %s, true,"
                     " 'PUBLISHED')", (honor_id, ministry, factory.name(label), slug))
        for locale, rows in lists.items():
            for position, (text, theoretical, source) in enumerate(rows, 1):
                conn.execute("INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, locale,"
                             " source, source_url, license) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                             (honor_id, position, text, theoretical, locale, source,
                              WIKI_URL if source else None, "CC BY-SA 3.0" if source else None))
        for locale, (name, source) in (names or {}).items():
            conn.execute("INSERT INTO honor_translations (honor_id, locale, name, source) VALUES (%s, %s, %s, %s)",
                         (honor_id, locale, name, source))
        conn.commit()
    return {"id": str(honor_id), "slug": slug}


def _doc(slug: str, target: str, texts: list[str], **extra) -> dict:
    raw = {"honor_slug": slug, "ministry": "pathfinders", "source_locale": "en", "target_locale": target,
           "source": "traduccion-no-oficial-pathfinder-wiki", "source_url": WIKI_URL, "license": "CC BY-SA 3.0",
           "name": None, "description": None,
           "requirements": [{"position": i, "description": t, "instructions": None} for i, t in enumerate(texts, 1)]}
    raw.update(extra)
    return importer.check_document(raw)


def _run(factory, docs) -> dict:
    with _connect() as conn:
        report = importer.run(conn, docs, operator=factory.email("importer"))
        conn.commit()
    return report


def _rows(honor: dict, locale: str) -> list[tuple]:
    with _connect() as conn:
        return conn.execute("SELECT position, description, is_theoretical, source, source_url, license, id"
                            " FROM honor_requirements WHERE honor_id = %s AND locale = %s ORDER BY position",
                            (honor["id"], locale)).fetchall()


def _audits(factory, honor: dict) -> int:
    with _connect() as conn:
        return conn.execute("SELECT count(*) FROM audit_log WHERE entity_id = %s AND action = %s",
                            (honor["id"], importer.AUDIT_ACTION)).fetchone()[0]


ENGLISH = [("What does the word raptor mean?", True, "pathfinder-wiki"),
           ("Watch a live raptor demonstration.", False, "pathfinder-wiki"),
           ("Find two Bible passages about raptors.", True, "pathfinder-wiki")]
SPANISH = ["¿Qué significa la palabra «rapiña»?", "Ver una demostración en vivo de aves de rapiña.",
           "Encontrar dos pasajes bíblicos acerca de las aves de rapiña."]


@requires_db
async def test_import_is_idempotent_and_keeps_the_origin_structure(factory):
    honor = _honor(factory, "rapinas", {"en": ENGLISH})
    doc = _doc(honor["slug"], "es", SPANISH)

    first = _run(factory, [doc])
    assert first["requirement_rows_inserted"] == 3 and first["requirement_lists_written"] == 1
    rows = _rows(honor, "es")
    assert [(r[0], r[1], r[2]) for r in rows] == [(1, SPANISH[0], True), (2, SPANISH[1], False), (3, SPANISH[2], True)]
    assert {(r[3], r[4], r[5]) for r in rows} == {("traduccion-no-oficial-pathfinder-wiki", WIKI_URL, "CC BY-SA 3.0")}
    assert _audits(factory, honor) == 1

    second = _run(factory, [doc])  # same content: nothing is written, not even the audit entry
    assert second["requirement_lists_unchanged"] == 1
    assert second["requirement_rows_inserted"] == second["requirement_rows_updated"] == 0
    assert _rows(honor, "es") == rows and _audits(factory, honor) == 1

    edited = _doc(honor["slug"], "es", [SPANISH[0], "Ver en vivo una demostración de aves de rapiña.", SPANISH[2]])
    third = _run(factory, [edited])
    assert third["requirement_rows_updated"] == 1 and third["requirement_rows_inserted"] == 0
    after = _rows(honor, "es")
    assert [r[6] for r in after] == [r[6] for r in rows]  # ids survive (progress rows point at them)
    assert after[1][1] == "Ver en vivo una demostración de aves de rapiña." and _audits(factory, honor) == 2

    # the origin lost its last requirement: the translation follows and drops the surplus row
    with _connect() as conn:
        conn.execute("DELETE FROM honor_requirements WHERE honor_id = %s AND locale = 'en' AND position = 3",
                     (honor["id"],))
        conn.commit()
    mismatch = _run(factory, [edited])
    assert mismatch["structure_mismatch"] and len(_rows(honor, "es")) == 3
    shrunk = _run(factory, [_doc(honor["slug"], "es", SPANISH[:1] + ["Ver en vivo una demostración de aves de rapiña."])])
    assert shrunk["requirement_rows_deleted"] == 1 and [r[0] for r in _rows(honor, "es")] == [1, 2]


@requires_db
async def test_instructor_and_official_lists_and_names_are_never_overwritten(factory):
    instructor = _honor(factory, "instructor", {"en": ENGLISH, "es": [("Lista del instructor", True, None)] * 3})
    wiki = _honor(factory, "wiki", {"en": ENGLISH, "es": [("Lista oficial", True, "pathfinder-wiki")] * 3},
                  names={"pt-BR": ("Aves de rapina", "pathfinder-wiki")})
    before = (_rows(instructor, "es"), _rows(wiki, "es"))

    report = _run(factory, [_doc(instructor["slug"], "es", SPANISH), _doc(wiki["slug"], "es", SPANISH),
                            _doc(wiki["slug"], "pt-BR", ["O que significa a palavra rapina?", "Assistir.", "Encontrar."],
                                 name="Rapinantes")])
    assert report["kept_instructor_list"] == [f"es/{instructor['slug']}"]
    assert report["kept_official_list"] == [f"es/{wiki['slug']}"]
    assert report["names_kept"] == [f"pt-BR/{wiki['slug']}"]
    assert (_rows(instructor, "es"), _rows(wiki, "es")) == before
    assert len(_rows(wiki, "pt-BR")) == 3  # the pt-BR list did not exist: it is written
    with _connect() as conn:
        name = conn.execute("SELECT name, source FROM honor_translations WHERE honor_id = %s AND locale = 'pt-BR'",
                            (wiki["id"],)).fetchone()
    assert name == ("Aves de rapina", "pathfinder-wiki")

    # Spanish is the source language: a name in an es/ file is ignored, honors.name stays
    ignored = _run(factory, [_doc(wiki["slug"], "es", SPANISH, name="Otro nombre")])
    assert ignored["names_ignored_source_language"] == 1


@requires_db
async def test_names_are_written_once_and_the_wiki_title_keeps_its_origin(factory):
    honor = _honor(factory, "nombres", {"en": ENGLISH})
    docs = [_doc(honor["slug"], "en", [], source_locale="es", name="Raptors", name_source="pathfinder-wiki",
                 name_source_url="https://wiki.pathfindersonline.org/w/AY_Honors/Raptors", name_license="CC BY-SA 3.0"),
            _doc(honor["slug"], "pt-BR", [], name="Aves de rapina")]
    first = _run(factory, docs)
    assert first["names_written"] == 2
    with _connect() as conn:
        names = dict(conn.execute("SELECT locale, source FROM honor_translations WHERE honor_id = %s",
                                  (honor["id"],)).fetchall())
    assert names == {"en": "pathfinder-wiki", "pt-BR": "traduccion-no-oficial-pathfinder-wiki"}
    second = _run(factory, docs)
    assert second["names_written"] == 0 and second["names_unchanged"] == 2
    renamed = _run(factory, [_doc(honor["slug"], "pt-BR", [], name="Aves de rapina (rapinantes)")])
    assert renamed["names_written"] == 1  # our own translation can be corrected


@requires_db
async def test_the_api_serves_the_translation_and_pt_finds_pt_br(client, factory):
    honor = _honor(factory, "api", {"en": ENGLISH})
    portuguese = ["O que significa a palavra «rapinante»?", "Assistir a uma demonstração ao vivo de aves de rapina.",
                  "Encontrar duas passagens bíblicas sobre aves de rapina."]
    _run(factory, [_doc(honor["slug"], "es", SPANISH),
                   _doc(honor["slug"], "pt-BR", portuguese, name="Aves de rapina")])

    spanish = (await client.get(f"{HONORS}/{honor['id']}", params={"locale": "es"})).json()
    assert spanish["requirements_locale"] == "es"
    assert [r["description"] for r in spanish["requirements"]] == SPANISH
    assert spanish["requirements"][0]["source"] == "traduccion-no-oficial-pathfinder-wiki"
    assert [r["is_theoretical"] for r in spanish["requirements"]] == [True, False, True]

    pt = (await client.get(f"{HONORS}/{honor['id']}", params={"locale": "pt"})).json()
    assert pt["requirements_locale"] == "pt-BR" and pt["name_locale"] == "pt-BR" and pt["name"] == "Aves de rapina"
    assert [r["description"] for r in pt["requirements"]] == portuguese

    english = (await client.get(f"{HONORS}/{honor['id']}", params={"locale": "en"})).json()
    assert english["requirements_locale"] == "en" and english["requirements"][0]["source"] == "pathfinder-wiki"


@requires_db
async def test_an_official_wiki_page_replaces_the_unofficial_translation_in_place(factory, tmp_path):
    """import_wiki_requirements.py (a 100 % translated page) takes over the translation's rows."""
    honor = _honor(factory, "oficial", {"en": ENGLISH})
    _run(factory, [_doc(honor["slug"], "es", SPANISH)])
    ids = [r[6] for r in _rows(honor, "es")]
    title = f"{factory.prefix} Raptors"
    with _connect() as conn:
        conn.execute("UPDATE honors SET wiki_title = %s WHERE id = %s", (title, honor["id"]))
        conn.commit()
    page = ('<div class="mw-content-ltr mw-parser-output"><p>This page is 100% translated. translation is 100% complete'
            "</p><p><b>1. Oficial uno</b></p><p><b>2. Oficial dos</b></p><p><b>3. Oficial tres</b></p>"
            '</div><div class="printfooter"></div>')
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
    assert [r[1] for r in rows] == ["Oficial uno", "Oficial dos", "Oficial tres"]
    assert {r[3] for r in rows} == {"pathfinder-wiki"} and [r[6] for r in rows] == ids
    # and the translation importer now leaves the official list alone
    assert _run(factory, [_doc(honor["slug"], "es", SPANISH)])["kept_official_list"] == [f"es/{honor['slug']}"]
