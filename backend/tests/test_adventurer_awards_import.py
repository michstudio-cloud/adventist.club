"""Aventureros · carga de los 164 awards del *Adventurer Award Book 2020* con sus requisitos.

Three things are proved here:
  1. `data/adventurer_awards.json` (migrations/extract_adventurer_awards.py) holds the 164 awards
     of the patch library, every one with numbered requirements and no empty text; six awards
     were checked by hand against the PDF (pages 17, 177, 201, 203, 339, 405: plain list,
     requirement 1 printed without its «1.», multi-level award, sub-items a–d, a closing sentence
     after the sub-items and a roman list inside a letter) and are pinned below. When the PDF and
     pymupdf are on the machine, those six are extracted again and must match the JSON;
  2. the Spanish data: mundoja.org for the Spiritual awards (parsed from both markups the site
     uses), the unofficial translation for the rest with exactly the book's structure;
  3. migrations/import_adventurer_awards.py loads DRAFT honours with the `av-` slug prefix, is
     idempotent (a second run changes nothing), keeps edits made in the app when its own data did
     not change, replaces the lists when it did, and --publish publishes.

The database tests tag every category and honour with this run's prefix, so the conftest cleanup
removes what they create.
"""

import copy
import csv
import importlib.util
import json
import pathlib
import sys

import pytest

from tests.conftest import TEST_DATABASE_URL, module_factory, requires_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_DIR / "migrations"
DATA = BACKEND_DIR / "data"
PDF = pathlib.Path.home() / "Documents" / "DEEL" / "aventureros" / "Award Book 2020.pdf"


def _load(name: str, path: pathlib.Path):
    """migrations/ is not a package: load the scripts by path."""
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extractor = _load("extract_adventurer_awards", MIGRATIONS / "extract_adventurer_awards.py")
importer = _load("import_adventurer_awards", MIGRATIONS / "import_adventurer_awards.py")
mundoja = _load("fetch_mundoja_awards", MIGRATIONS / "catalog_tools" / "fetch_mundoja_awards.py")

factory = module_factory("advawards")

AWARDS = json.loads((DATA / "adventurer_awards.json").read_text(encoding="utf-8"))
BY_SLUG = {award["slug"]: award for award in AWARDS["awards"]}
with (DATA / "adventurer_awards_library.csv").open(encoding="utf-8") as fh:
    LIBRARY = list(csv.DictReader(fh))


def _texts(award: dict) -> list[tuple[str, list[str]]]:
    return [(r["text_en"], r["sub"]) for r in award["requirements"]]


# ----------------------------------------------------------------------------
# 1. The English extraction
# ----------------------------------------------------------------------------
def test_the_json_has_the_164_awards_of_the_library_all_with_requirements():
    assert len(AWARDS["awards"]) == 164
    assert [a["slug"] for a in AWARDS["awards"]] == [r["slug"] for r in sorted(LIBRARY, key=lambda r: int(r["page"]))]
    for award in AWARDS["awards"]:
        requirements = award["requirements"]
        assert requirements, award["slug"]
        assert [r["position"] for r in requirements] == list(range(1, len(requirements) + 1)), award["slug"]
        for requirement in requirements:
            assert requirement["text_en"].strip(), (award["slug"], requirement["position"])
            assert all(line.strip() for line in requirement["sub"]), (award["slug"], requirement["position"])
            # sub-items: marker lines indented two spaces per level, or an unindented closing sentence
            for line in requirement["sub"]:
                indent = len(line) - len(line.lstrip(" "))
                assert indent in (0, 2, 4), (award["slug"], line)
    assert sum(len(a["requirements"]) for a in AWARDS["awards"]) == 989
    assert AWARDS["source"] == "gc-award-book-2020"


def test_cooperation_p17_plain_list_and_numbered_instructor_notes():
    award = BY_SLUG["cooperation"]
    assert award["page"] == 17 and award["class_en"] == "Helping Hands"
    assert [t for t, _ in _texts(award)] == [
        "Read and discuss Acts 4:32-37 and Exodus 35:20-29; 36:2-7.",
        "What is cooperation?",
        "Why is cooperation important in your family, school, and church?",
        "Role play a Bible story about cooperation.",
        "Sing a cooperation song.",
        "Play a cooperative game.",
        "Make a cooperative craft with your group.",
    ]
    notes = {r["position"]: r["instructor_notes_en"] for r in award["requirements"]}
    assert notes[3] == "Discuss. This requirement may be combined with the Bible discussion in requirement 1."
    # the inner «1) … 6)» questions stay inside the note of requirement 1
    assert "1) what was accomplished when people worked together?" in notes[1]
    assert "6) Colossians 3:23-24" in notes[1]
    assert award["general_notes_en"].startswith("Note: This award requires cultural and group sensitivity.")


def test_technology_p177_rebuilds_requirement_1_printed_without_its_number():
    award = BY_SLUG["technology"]
    first = award["requirements"][0]
    assert first["text_en"] == "Explain the purpose of each item:"
    assert len(first["sub"]) == 12
    assert first["sub"][0] == "  a. Computer system"
    assert first["sub"][-1] == "  l. Diskette"          # printed «I.» after «k.»
    # the sub-items of requirement 3 continue on page 178 at the same level
    assert award["requirements"][2]["sub"] == ["  a. Type and print a thank-you note.",
                                              "  b. Play an educational game."]
    assert len(award["requirements"]) == 5


def test_dogs_p201_multi_level_award_with_wrapped_lines():
    award = BY_SLUG["dogs"]
    assert award["class_en"] == "Multi-level" and award["category_en"] == "Nature"
    assert len(award["requirements"]) == 8
    assert award["requirements"][5]["text_en"] == (
        "What kind of sounds do dogs make to communicate and what does each sound mean?"
        " Take turns making these dog sounds.")
    assert award["requirements"][7]["instructor_notes_en"] == "Google kids’ games on the internet."


def test_environmentalist_p203_keeps_the_sub_items():
    award = BY_SLUG["environmentalist"]
    assert award["requirements"][4]["text_en"] == "In your area:"
    assert award["requirements"][4]["sub"] == [
        "  a. What causes pollution? List ways you can prevent pollution.",
        "  b. Investigate how and why the pollution was caused.",
        "  c. Explain how you can keep from polluting water.",
        "  d. What dangers threaten the quality of air?",
    ]
    assert len(award["requirements"][5]["sub"]) == 3
    assert award["requirements"][6]["text_en"] == "Create a mural of the earth made new."


def test_bible_i_p339_closing_sentence_and_or_alternative():
    award = BY_SLUG["bible-i"]
    fifth = award["requirements"][4]
    assert fifth["sub"][-2:] == ["  e. Your choice", "Memorize and repeat two of them."]
    assert award["requirements"][5]["text_en"] == (
        "Make masks to illustrate a Bible story or parable. OR Create a Bible story in a sandbox or with felts.")
    # «1-2.» in the Supporting Answers covers both requirements
    notes = [r["instructor_notes_en"] for r in award["requirements"]]
    assert notes[0] == notes[1] and notes[0].startswith("If possible, see that each child has his/her own Bible.")


def test_temperance_p405_nests_the_roman_list_inside_the_letter():
    award = BY_SLUG["temperance"]
    assert award["requirements"][2]["sub"] == [
        "  a. Talk to a doctor/nurse or discuss with another adult the harm in using:",
        "    i. Tobacco",
        "    ii. Alcohol",
        "    iii. Other drugs",
        "  b. Watch and discuss a film or video on the dangers of using any of the above.",
    ]


def test_answers_printed_under_the_list_are_not_requirements():
    cyclist = BY_SLUG["cyclist-i"]
    assert cyclist["requirements"][-1] == {**cyclist["requirements"][-1], "text_en": "How are tires pumped up?",
                                           "sub": []}
    assert cyclist["general_notes_en"].startswith("Answer for #3:")
    assert BY_SLUG["manners-fun"]["requirements"][-1]["text_en"] == (
        "Play a game using the five magic words (or phrases).")


VERIFIED = ["cooperation", "technology", "dogs", "environmentalist", "bible-i", "temperance"]


def test_the_six_verified_awards_come_out_of_the_pdf_exactly_like_the_json():
    pytest.importorskip("pymupdf")
    if not PDF.exists():
        pytest.skip("el PDF del Award Book no está en esta máquina")
    fresh = {a["slug"]: a for a in extractor.extract(PDF, set(VERIFIED))["awards"]}
    for slug in VERIFIED:
        assert fresh[slug] == BY_SLUG[slug], slug


def test_the_line_splitter_on_a_synthetic_page():
    lines = [{"text": t, "x": x, "page": 1} for t, x in [
        ("1.\tDo one of the following:", 127), ("a.\tTake care of a pet.", 148),
        ("i. Feed it.", 162), ("b.\tVisit a zoo and", 148), ("write a report.", 162),
        ("Choose one.", 127), ("2.\tSing.", 127), ("1. not a requirement", 127),
    ]]
    preamble, items = extractor.split_requirements(lines)
    assert preamble == []
    assert [i["position"] for i in items] == [1, 2]
    assert [extractor.sub_line(*s) for s in items[0]["sub"]] == [
        "  a. Take care of a pet.", "    i. Feed it.", "  b. Visit a zoo and write a report.", "Choose one."]
    assert extractor._join(items[1]["head"]) == "Sing. 1. not a requirement"


# ----------------------------------------------------------------------------
# 2. Spanish
# ----------------------------------------------------------------------------
MUNDOJA = json.loads((DATA / "adventurer_awards_mundoja_es.json").read_text(encoding="utf-8"))["awards"]
TRANSLATION = json.loads((DATA / "adventurer_awards_es.json").read_text(encoding="utf-8"))


def test_mundoja_covers_the_34_spiritual_awards_with_their_names_and_lists():
    spiritual = [a["slug"] for a in AWARDS["awards"] if a["category_en"] == "Spiritual"]
    assert sorted(MUNDOJA) == sorted(spiritual) and len(MUNDOJA) == 34
    with (DATA / "adventurer_awards_mundoja_map.csv").open(encoding="utf-8") as fh:
        names = {row["slug"]: row["name_es"] for row in csv.DictReader(fh)}
    for slug, ficha in MUNDOJA.items():
        assert ficha["name_es"] == names[slug]
        assert ficha["source"] == "mundoja.org" and ficha["license"] is None
        assert ficha["source_url"].startswith("https://mundoja.org/")
        assert ficha["requirements"] and all(r["text_es"].strip() for r in ficha["requirements"])
    different = sorted(s for s, f in MUNDOJA.items() if len(f["requirements"]) != len(BY_SLUG[s]["requirements"]))
    assert different == ["friend-of-jesus", "temperance", "wise-steward"]


def test_the_mundoja_parser_reads_both_markups():
    as_list = ('<ul class="sppb-nav"><li><a>Requisitos</a></li><li><a>Ayuda</a></li></ul>'
               '<div class="sppb-tab-pane"><ol><li>Leer Juan 3:16.</li><li>Hacer 2 de las siguientes:'
               '<ol style="list-style-type: lower-alpha;"><li>Una corona</li><li>Otro</li></ol></li></ol></div>'
               '<div class="sppb-tab-pane"><p>1. Ayuda uno.</p><p>2. Ayuda dos.</p></div>')
    requirements, notes, _ = mundoja.parse(as_list)
    assert [(r["text_es"], r["sub"]) for r in requirements] == [
        ("Leer Juan 3:16.", []), ("Hacer 2 de las siguientes:", ["  a. Una corona", "  b. Otro"])]
    assert notes == {1: "Ayuda uno.", 2: "Ayuda dos."}
    as_paragraphs = ('<ul class="sppb-nav"><li><a>Requisitos</a></li></ul><div class="sppb-tab-pane">'
                     '<p>1.Debe tener una Biblia.<br/>2.Cuente:<br/>&nbsp;&nbsp;a.La creación<br/>'
                     '&nbsp;b. El cielo<br/>&nbsp;Memorice dos.</p><p>3. Ore.</p>'
                     '<ol style="list-style-type: lower-alpha;"><li>En casa</li></ol></div>')
    requirements, _, _ = mundoja.parse(as_paragraphs)
    assert [(r["text_es"], r["sub"]) for r in requirements] == [
        ("Debe tener una Biblia.", []),
        ("Cuente:", ["  a. La creación", "  b. El cielo", "Memorice dos."]),
        ("Ore.", ["  a. En casa"]),
    ]


def test_the_translation_mirrors_the_book_and_every_award_has_a_spanish_name():
    assert TRANSLATION["source"] == "traduccion-no-oficial-gc-award-book-2020"
    translated = TRANSLATION["awards"]
    for award in AWARDS["awards"]:
        slug = award["slug"]
        ficha = MUNDOJA.get(slug)
        needs_translation = not ficha or len(ficha["requirements"]) != len(award["requirements"])
        entry = translated.get(slug, {})
        assert (ficha or {}).get("name_es") or entry.get("name_es"), slug
        if award.get("intro_en"):
            assert entry.get("intro_es"), slug
        if not needs_translation:
            assert "requirements" not in entry, slug
            continue
        assert [r["position"] for r in entry["requirements"]] == [r["position"] for r in award["requirements"]], slug
        for es, en in zip(entry["requirements"], award["requirements"]):
            assert es["text_es"].strip()
            assert len(es["sub"]) == len(en["sub"]), (slug, en["position"])
            for line_es, line_en in zip(es["sub"], en["sub"]):
                assert len(line_es) - len(line_es.lstrip(" ")) == len(line_en) - len(line_en.lstrip(" "))
    assert translated["community-helpers"]["name_es"] == "Ayudantes de la comunidad"


def test_the_plans_are_complete_and_attributed():
    plans = importer.build(importer.load())
    assert len(plans) == 164
    assert all(p["image_url"] and p["image_url"].startswith("https://media.adventist.club/patches/") for p in plans)
    es = [r for p in plans for r in p["requirements"]["es"]]
    en = [r for p in plans for r in p["requirements"]["en"]]
    assert len(en) == 989 and len(es) == 989
    assert {r["source"] for r in en} == {"gc-award-book-2020"}
    assert {r["license"] for r in en} == {"© GC Youth Ministries, permiso pendiente"}
    assert len("© GC Youth Ministries, permiso pendiente") <= 40
    assert sum(1 for r in es if r["source"] == "mundoja.org") == 191
    assert {r["license"] for r in es if r["source"] == "mundoja.org"} == {None}
    assert sum(1 for r in es if r["source"] == "traduccion-no-oficial-gc-award-book-2020") == 798
    assert all(r["instructions"] is None for r in es + en)          # notes are opt-in
    by_slug = {p["slug"]: p for p in plans}
    assert by_slug["temperance"]["es_source"] == "traduccion-no-oficial-gc-award-book-2020"
    assert by_slug["temperance"]["name_es"] == "Temperancia"
    assert by_slug["prayer"]["es_source"] == "mundoja.org"
    assert by_slug["community-helpers"]["description_es"] == "Clase sugerida: Corderitos."
    assert by_slug["dogs"]["description_es"].startswith("Multinivel")
    assert by_slug["reading-i"]["description_en"].startswith("Awarded to Adventurers who read")
    with_notes = importer.build(importer.load(), instructor_notes=True)
    assert any(r["instructions"] for p in with_notes for r in p["requirements"]["en"])
    forced = {p["slug"]: p for p in importer.build(importer.load(), mundoja_always=True)}
    assert len(forced["temperance"]["requirements"]["es"]) == 8


# ----------------------------------------------------------------------------
# 3. The importer against the test database
# ----------------------------------------------------------------------------
def _connect():
    return importer.connect(TEST_DATABASE_URL)


def _run(factory, plans, **kwargs) -> tuple[dict, dict]:
    with _connect() as conn:
        report = importer.run(conn, plans, operator=factory.email("importer"), prefix=factory.prefix, **kwargs)
        after = importer.counts(conn, prefix=factory.prefix)
        conn.commit()
    return report, after


@requires_db
async def test_the_importer_loads_drafts_and_a_rerun_changes_nothing(factory):
    plans = importer.build(importer.load())
    first, after_first = _run(factory, plans)
    assert first["honors_created"] == 164 and first["categories_created"] == 6
    assert first["requirement_rows_written"] == 989 * 2
    assert after_first["honors"] == 164 and after_first["draft"] == 164 and after_first["published"] == 0
    assert after_first["with_image"] == 164 and after_first["honor_translations"] == 164
    assert after_first["requirements"] == {
        "en/gc-award-book-2020": 989,
        "es/mundoja.org": 191,
        "es/traduccion-no-oficial-gc-award-book-2020": 798,
    }
    second, after_second = _run(factory, plans)
    assert after_second == after_first
    assert second["honors_created"] == 0 and second["honors_unchanged"] == 164
    assert second["requirements_replaced"] == 0 and second["requirement_rows_written"] == 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT h.slug, h.code, h.honor_type, h.status, h.active, c.slug, m.slug"
            " FROM honors h JOIN honor_categories c ON c.id = h.category_id JOIN ministries m ON m.id = h.ministry_id"
            " WHERE h.slug LIKE %s", (f"{factory.prefix}-av-%",)).fetchall()
        assert len(rows) == 164
        assert all(slug.startswith(f"{factory.prefix}-av-") for slug, *_ in rows)
        assert {(code, kind, status, active, ministry) for _, code, kind, status, active, _, ministry in rows} == {
            (None, "OFFICIAL_GC", "DRAFT", True, "adventurers")}
        assert {category for *_, category, _ in rows} == {f"{factory.prefix}-{slug}" for slug, *_ in
                                                          importer.CATEGORIES.values()}
        names = conn.execute(
            "SELECT t.locale, t.name FROM honor_category_translations t JOIN honor_categories c ON c.id = t.category_id"
            " WHERE c.slug = %s ORDER BY t.locale", (f"{factory.prefix}-av-recreacion",)).fetchall()
        assert names == [("en", "Recreation"), ("pt-BR", "Atividades Recreativas")]
        description = conn.execute(
            "SELECT r.description FROM honor_requirements r JOIN honors h ON h.id = r.honor_id"
            " WHERE h.slug = %s AND r.locale = 'es' AND r.position = 3", (f"{factory.prefix}-av-temperance",)).fetchone()[0]
        assert description.split("\n")[2] == "    i. Tabaco"


@requires_db
async def test_edits_survive_a_rerun_and_changed_data_replaces_the_lists(factory):
    plans = importer.build(importer.load())
    _run(factory, plans)
    slug = f"{factory.prefix}-av-colors"
    with _connect() as conn:
        conn.execute("UPDATE honor_requirements SET description = 'Editado en la app' WHERE honor_id ="
                     " (SELECT id FROM honors WHERE slug = %s) AND locale = 'es' AND position = 1", (slug,))
        conn.commit()
    report, _ = _run(factory, plans)
    assert report["requirements_replaced"] == 0
    with _connect() as conn:
        kept = conn.execute("SELECT r.description FROM honor_requirements r JOIN honors h ON h.id = r.honor_id"
                            " WHERE h.slug = %s AND r.locale = 'es' AND r.position = 1", (slug,)).fetchone()[0]
        assert kept == "Editado en la app"
    changed = copy.deepcopy(plans)
    colors = next(p for p in changed if p["slug"] == "colors")
    colors["requirements"]["en"][0]["description"] = "Listen to a book about colors."
    report, after = _run(factory, changed)
    assert report["requirements_replaced"] == 1 and report["requirement_rows_written"] == 8
    with _connect() as conn:
        rows = conn.execute("SELECT r.locale, r.description FROM honor_requirements r JOIN honors h ON h.id = r.honor_id"
                            " WHERE h.slug = %s AND r.position = 1 ORDER BY r.locale", (slug,)).fetchall()
        assert rows == [("en", "Listen to a book about colors."), ("es", "Escuchar un libro sobre los colores")]
        audit = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'HONOR_IMPORT' AND user_email = %s",
                             (factory.email("importer"),)).fetchone()[0]
        assert audit >= 164


@requires_db
async def test_publish_flag_publishes_all_164(factory):
    plans = importer.build(importer.load())
    _run(factory, plans)
    report, after = _run(factory, plans, publish=True)
    assert after["published"] == 164 and after["draft"] == 0
    with _connect() as conn:
        missing = conn.execute("SELECT count(*) FROM honors WHERE slug LIKE %s AND published_at IS NULL",
                               (f"{factory.prefix}-av-%",)).fetchone()[0]
        assert missing == 0
    again, _ = _run(factory, plans, publish=True)
    assert again["published"] == 0
