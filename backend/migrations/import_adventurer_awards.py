"""Load the 164 Adventurer awards (GC *Adventurer Award Book 2020*) into the honour catalogue of
the `adventurers` ministry: 6 categories, 164 honours, their requirements in Spanish (base) and
English, names/descriptions in English and the patch image.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_adventurer_awards.py              # simulacro: todo en una transacción y ROLLBACK
    DATABASE_URL=... python import_adventurer_awards.py --commit     # escribe
    DATABASE_URL=... python import_adventurer_awards.py --commit --publish   # y además los publica

Data (all in backend/data/, nothing is downloaded here):
  * adventurer_awards.json            English, extracted from the PDF by extract_adventurer_awards.py
  * adventurer_awards_mundoja_es.json Spanish of the 34 Spiritual awards from mundoja.org
                                      (catalog_tools/fetch_mundoja_awards.py)
  * adventurer_awards_es.json         Spanish names of the other awards and an UNOFFICIAL Spanish
                                      translation of their requirements (and of the book's intros)
  * adventurer_awards_library.csv     patch library: category/class in Spanish and `webp_url`

Rules:
  * the model keeps one requirement list per language in `honor_requirements` (locale); the
    Spanish list is the base one (the editor's SOURCE_LOCALE), English is loaded next to it:
      es  mundoja.org (source='mundoja.org', source_url=ficha, license NULL) for the Spiritual
          awards whose mundoja list is the book's list; otherwise the unofficial translation
          (source='traduccion-no-oficial-gc-award-book-2020', GC licence, book page as URL)
      en  the book (source='gc-award-book-2020', source_url=GC page #page=N,
          license='© GC Youth Ministries, permiso pendiente' — 40 characters, the column's width);
  * three mundoja fichas (Amigo de Jesús, Temperancia, Mayordomo sabio) carry an older list that
    is not the book's (different number of requirements): by default their Spanish list is the
    translation of the book and only the mundoja name is used; --mundoja-always loads mundoja's;
  * the instructor's «Supporting Answers» (and mundoja's «Ayuda») stay in the JSON and are NOT
    loaded by default: `honor_requirements.instructions` is printed under each requirement on the
    member's worksheet (honour sheet), and those notes carry the answers. --instructor-notes loads
    them there anyway (English notes on the English rows, mundoja's on its Spanish rows, the
    book's unnumbered general note on requirement 1);
  * idempotent by (ministry, `av-<slug>`): an honour keeps its id; its catalogue fields are
    rewritten; a new honour enters as DRAFT, an existing one keeps its status (--publish sets
    PUBLISHED on all 164); requirements are replaced ONLY when the content hash of what this
    importer loads changed since its last run (recorded in audit_log, action HONOR_IMPORT), so
    edits made in the app and the progress rows that point at requirement ids survive a rerun;
    a Spanish list written in the editor (rows without `source`) is never touched;
  * the book's suggested class (Little Lamb, Early Bird… or Multi-level) goes in the honour's
    description: there are no Adventurer class programs yet to hang a recommendation on;
  * `code` stays NULL; `honor_type` is 'OFFICIAL_GC' (the CHECK allows OFFICIAL_GC, DIVISIONAL,
    LOCAL; an award of the General Conference's book is the closest to «award»).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"
EN_JSON = DATA / "adventurer_awards.json"
MUNDOJA_JSON = DATA / "adventurer_awards_mundoja_es.json"
ES_JSON = DATA / "adventurer_awards_es.json"
LIBRARY_CSV = DATA / "adventurer_awards_library.csv"

MINISTRY = "adventurers"
SLUG_PREFIX = "av-"
BOOK_URL = "https://www.gcyouthministries.org/ministries/adventurers/"
EN_SOURCE = "gc-award-book-2020"
ES_SOURCE = "traduccion-no-oficial-gc-award-book-2020"
MUNDOJA_SOURCE = "mundoja.org"
LICENSE = "© GC Youth Ministries, permiso pendiente"
HONOR_TYPE = "OFFICIAL_GC"
AUDIT_ACTION = "HONOR_IMPORT"
IMPORTER = "import_adventurer_awards"

# category_en -> (slug, es, en, pt)
CATEGORIES = {
    "Community": ("av-comunidad", "Comunidad", "Community", "Comunidade"),
    "Crafts": ("av-manualidades", "Manualidades", "Crafts", "Artesanato"),
    "Home": ("av-hogar", "Hogar", "Home", "Lar"),
    "Nature": ("av-naturaleza", "Naturaleza", "Nature", "Natureza"),
    "Recreation": ("av-recreacion", "Recreación", "Recreation", "Recreação"),
    "Spiritual": ("av-espiritual", "Espiritual", "Spiritual", "Espiritual"),
}
# class_en -> (es, en) as the description says it
CLASSES = {
    "Little Lamb": ("Corderitos", "Little Lamb"),
    "Early Bird": ("Aves Madrugadoras", "Early Bird"),
    "Busy Bee": ("Abejas Industriosas", "Busy Bee"),
    "Sunbeam": ("Rayos de Sol", "Sunbeam"),
    "Builder": ("Constructores", "Builder"),
    "Helping Hand": ("Manos Ayudadoras", "Helping Hands"),
    "Helping Hands": ("Manos Ayudadoras", "Helping Hands"),
    "Multi-level": ("Multinivel", "Multi-level"),
}


class ImportError_(Exception):
    """Loud failure: nothing is written when the data does not make sense."""


# ----------------------------------------------------------------------------
# Data (no database)
# ----------------------------------------------------------------------------
def load(en_path=EN_JSON, mundoja_path=MUNDOJA_JSON, es_path=ES_JSON, library_path=LIBRARY_CSV) -> dict:
    en = json.loads(pathlib.Path(en_path).read_text(encoding="utf-8"))
    mundoja = json.loads(pathlib.Path(mundoja_path).read_text(encoding="utf-8"))["awards"]
    es = json.loads(pathlib.Path(es_path).read_text(encoding="utf-8"))["awards"]
    with pathlib.Path(library_path).open(encoding="utf-8") as fh:
        library = {row["slug"]: row for row in csv.DictReader(fh)}
    return {"en": en["awards"], "mundoja": mundoja, "es": es, "library": library}


def description(text: str, sub: list[str]) -> str:
    """The stored requirement: its text, then one sub-item per line (the form the honour sheet
    and the editor read)."""
    return "\n".join([text.strip(), *[line.rstrip() for line in sub if line.strip()]])


def mundoja_matches(award: dict, ficha: dict | None) -> bool:
    return bool(ficha) and len(ficha["requirements"]) == len(award["requirements"])


def build(data: dict, *, mundoja_always: bool = False, instructor_notes: bool = False) -> list[dict]:
    """One plan per award: honour fields, translations and the requirement rows per locale."""
    plans = []
    seen = set()
    for award in data["en"]:
        slug = award["slug"]
        if slug in seen:
            raise ImportError_(f"{slug}: repetido")
        seen.add(slug)
        lib = data["library"].get(slug)
        if lib is None:
            raise ImportError_(f"{slug}: no está en adventurer_awards_library.csv")
        if award["category_en"] not in CATEGORIES:
            raise ImportError_(f"{slug}: categoría desconocida {award['category_en']}")
        if not award["requirements"]:
            raise ImportError_(f"{slug}: sin requisitos en inglés")
        ficha = data["mundoja"].get(slug)
        tr = data["es"].get(slug) or {}
        use_mundoja = bool(ficha) and (mundoja_always or mundoja_matches(award, ficha))
        name_es = (ficha or {}).get("name_es") or tr.get("name_es")
        if not name_es:
            raise ImportError_(f"{slug}: falta el nombre en español")
        page_url = f"{BOOK_URL}#page={award['page']}"
        class_es, class_en = CLASSES[award["class_en"]]

        en_rows = []
        general = award.get("general_notes_en")
        for req in award["requirements"]:
            notes = req.get("instructor_notes_en") if instructor_notes else None
            if instructor_notes and general and req["position"] == 1:
                notes = "\n\n".join(p for p in (notes, "General notes:\n" + general) if p)
            en_rows.append({"position": req["position"], "description": description(req["text_en"], req["sub"]),
                            "instructions": notes or None, "source": EN_SOURCE, "source_url": page_url,
                            "license": LICENSE})

        if use_mundoja:
            es_rows = [{"position": r["position"], "description": description(r["text_es"], r["sub"]),
                        "instructions": (r.get("instructor_notes_es") if instructor_notes else None) or None,
                        "source": MUNDOJA_SOURCE, "source_url": ficha["source_url"], "license": None}
                       for r in ficha["requirements"]]
            es_source = MUNDOJA_SOURCE
        else:
            translated = tr.get("requirements") or []
            if [r["position"] for r in translated] != [r["position"] for r in award["requirements"]]:
                raise ImportError_(f"{slug}: la traducción no cubre los mismos requisitos que el libro")
            es_rows = [{"position": r["position"], "description": description(r["text_es"], r["sub"]),
                        "instructions": None, "source": ES_SOURCE, "source_url": page_url, "license": LICENSE}
                       for r in translated]
            es_source = ES_SOURCE
        for row in es_rows + en_rows:
            if not row["description"].strip() or any(not line.strip() for line in row["description"].split("\n")):
                raise ImportError_(f"{slug}: requisito {row['position']} con texto vacío")

        intro_es, intro_en = tr.get("intro_es"), award.get("intro_en")
        if intro_en and not intro_es:
            raise ImportError_(f"{slug}: falta la traducción de la introducción")
        desc_es = "\n\n".join(p for p in (intro_es, f"Clase sugerida: {class_es}." if class_en != "Multi-level"
                                          else "Multinivel: no está asignada a una clase.") if p)
        desc_en = "\n\n".join(p for p in (intro_en, f"Suggested class: {class_en}." if class_en != "Multi-level"
                                          else "Multi-level: not assigned to one class.") if p)
        plans.append({
            "slug": slug,
            "category": award["category_en"],
            "name_es": name_es,
            "name_en": award["title_en"],
            "description_es": desc_es,
            "description_en": desc_en,
            "image_url": (lib.get("webp_url") or "").strip() or None,
            "source_url": page_url,
            "class_en": award["class_en"],
            "es_source": es_source,
            "requirements": {"es": es_rows, "en": en_rows},
        })
    return plans


def content_hash(requirements: dict) -> str:
    payload = json.dumps(requirements, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summary(plans: list[dict]) -> dict:
    by_category: dict[str, dict] = {}
    for plan in plans:
        row = by_category.setdefault(CATEGORIES[plan["category"]][1], {"awards": 0, "requisitos": 0,
                                                                     "es_mundoja": 0, "es_traduccion": 0})
        row["awards"] += 1
        row["requisitos"] += len(plan["requirements"]["en"])
        if plan["es_source"] == MUNDOJA_SOURCE:
            row["es_mundoja"] += len(plan["requirements"]["es"])
        else:
            row["es_traduccion"] += len(plan["requirements"]["es"])
    return by_category


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
def connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))


def _tag(prefix: str, value: str, sep: str) -> str:
    return f"{prefix}{sep}{value}" if prefix else value


def run(conn, plans: list[dict], *, publish: bool = False, operator: str | None = None,
        prefix: str = "") -> dict:
    """Apply the plans inside the caller's transaction (it commits or rolls back).
    `prefix` exists for the tests: it tags category and honour slugs/names with the run's prefix
    so the test cleanup removes them."""
    report = {"categories_created": 0, "honors_created": 0, "honors_updated": 0, "honors_unchanged": 0,
              "requirements_replaced": 0, "requirements_unchanged": 0, "requirement_rows_written": 0,
              "es_kept_instructor_list": 0, "published": 0}
    operator = operator or IMPORTER
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM ministries WHERE slug = %s", (MINISTRY,))
        row = cur.fetchone()
        if not row:
            raise ImportError_(f"no existe el ministerio {MINISTRY}")
        ministry = row[0]
        categories = {}
        for key, (slug, es, en, pt) in CATEGORIES.items():
            cur.execute(
                "INSERT INTO honor_categories (ministry_id, name, slug) VALUES (%s, %s, %s)"
                " ON CONFLICT (ministry_id, slug) DO UPDATE SET name = EXCLUDED.name,"
                " updated_at = CASE WHEN honor_categories.name IS DISTINCT FROM EXCLUDED.name"
                " THEN now() ELSE honor_categories.updated_at END"
                " RETURNING id, (xmax = 0)",
                (ministry, _tag(prefix, es, " "), _tag(prefix, slug, "-")))
            categories[key], created = cur.fetchone()
            report["categories_created"] += int(created)
            for locale, name in (("en", en), ("pt", pt)):
                cur.execute(
                    "INSERT INTO honor_category_translations (category_id, locale, name) VALUES (%s, %s, %s)"
                    " ON CONFLICT (category_id, locale) DO UPDATE SET name = EXCLUDED.name,"
                    " updated_at = now() WHERE honor_category_translations.name IS DISTINCT FROM EXCLUDED.name",
                    (categories[key], locale, name))

        for plan in plans:
            slug = _tag(prefix, SLUG_PREFIX + plan["slug"], "-")
            fields = (categories[plan["category"]], _tag(prefix, plan["name_es"], " "), plan["description_es"],
                      plan["image_url"], plan["source_url"])
            cur.execute("SELECT id, status FROM honors WHERE ministry_id = %s AND slug = %s", (ministry, slug))
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    "INSERT INTO honors (ministry_id, category_id, name, slug, code, description, image_url,"
                    " source_url, active, status, honor_type, authority, version, published_at)"
                    " VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, true, %s, %s, 'GC', 1, %s) RETURNING id",
                    (ministry, fields[0], fields[1], slug, fields[2], fields[3], fields[4],
                     "PUBLISHED" if publish else "DRAFT", HONOR_TYPE, None))
                honor_id = cur.fetchone()[0]
                if publish:
                    cur.execute("UPDATE honors SET published_at = now() WHERE id = %s", (honor_id,))
                report["honors_created"] += 1
                action = "created"
            else:
                honor_id = existing[0]
                cur.execute(
                    "UPDATE honors SET category_id = %s, name = %s, description = %s, image_url = %s,"
                    " source_url = %s, honor_type = %s, authority = 'GC', active = true, updated_at = now()"
                    " WHERE id = %s AND (category_id, name, description, image_url, source_url, honor_type,"
                    " authority, active) IS DISTINCT FROM (%s, %s, %s, %s, %s, %s, 'GC', true)",
                    (*fields, HONOR_TYPE, honor_id, *fields, HONOR_TYPE))
                changed = cur.rowcount
                report["honors_updated" if changed else "honors_unchanged"] += 1
                action = "updated" if changed else None
            if publish:
                cur.execute("UPDATE honors SET status = 'PUBLISHED', published_at = COALESCE(published_at, now()),"
                            " updated_at = now() WHERE id = %s AND status <> 'PUBLISHED'", (honor_id,))
                report["published"] += cur.rowcount

            cur.execute(
                "INSERT INTO honor_translations (honor_id, locale, name, description, source, source_url, license)"
                " VALUES (%s, 'en', %s, %s, %s, %s, %s) ON CONFLICT (honor_id, locale) DO UPDATE SET"
                " name = EXCLUDED.name, description = EXCLUDED.description, source = EXCLUDED.source,"
                " source_url = EXCLUDED.source_url, license = EXCLUDED.license, updated_at = now()"
                " WHERE (honor_translations.name, honor_translations.description, honor_translations.source,"
                " honor_translations.source_url, honor_translations.license) IS DISTINCT FROM"
                " (EXCLUDED.name, EXCLUDED.description, EXCLUDED.source, EXCLUDED.source_url, EXCLUDED.license)",
                (honor_id, plan["name_en"], plan["description_en"], EN_SOURCE, plan["source_url"], LICENSE))

            replaced = _requirements(cur, honor_id, plan, report)
            if action or replaced:
                cur.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, user_email, details, metadata_json)"
                    " VALUES (%s, 'HONOR', %s, %s, %s, %s::jsonb)",
                    (AUDIT_ACTION, str(honor_id), operator,
                     f"{action or 'requirements'}: adventurers/{slug} ({IMPORTER})",
                     json.dumps({"importer": IMPORTER, "slug": slug,
                                 "content_hash": content_hash(plan["requirements"]),
                                 "requirements_replaced": replaced,
                                 "es_source": plan["es_source"]})))
    return report


def _requirements(cur, honor_id, plan: dict, report: dict) -> bool:
    """Replace this honour's imported lists only when their content changed since the last
    import (hash in audit_log) — or, on the first run, when what is stored is not the same."""
    wanted = content_hash(plan["requirements"])
    cur.execute(
        "SELECT metadata_json->>'content_hash' FROM audit_log WHERE action = %s AND entity_type = 'HONOR'"
        " AND entity_id = %s AND metadata_json->>'importer' = %s ORDER BY created_at DESC LIMIT 1",
        (AUDIT_ACTION, str(honor_id), IMPORTER))
    row = cur.fetchone()
    last = row[0] if row else None
    if last is None:
        stored = {}
        for locale in ("es", "en"):
            cur.execute("SELECT position, description, instructions, source, source_url, license"
                        " FROM honor_requirements WHERE honor_id = %s AND locale = %s ORDER BY position",
                        (honor_id, locale))
            stored[locale] = [dict(zip(("position", "description", "instructions", "source", "source_url",
                                        "license"), r)) for r in cur.fetchall()]
        last = content_hash(stored)
    if last == wanted:
        report["requirements_unchanged"] += 1
        return False
    for locale, rows in plan["requirements"].items():
        if locale == "es":
            cur.execute("SELECT count(*) FROM honor_requirements WHERE honor_id = %s AND locale = 'es'"
                        " AND source IS NULL", (honor_id,))
            if cur.fetchone()[0]:
                report["es_kept_instructor_list"] += 1
                continue
        cur.execute("DELETE FROM honor_requirements WHERE honor_id = %s AND locale = %s AND source IS NOT NULL",
                    (honor_id, locale))
        for r in rows:
            cur.execute(
                "INSERT INTO honor_requirements (honor_id, position, description, is_theoretical, instructions,"
                " locale, source, source_url, license) VALUES (%s, %s, %s, true, %s, %s, %s, %s, %s)",
                (honor_id, r["position"], r["description"], r["instructions"], locale, r["source"],
                 r["source_url"], r["license"]))
            report["requirement_rows_written"] += 1
    report["requirements_replaced"] += 1
    return True


def counts(conn, prefix: str = "") -> dict:
    like = _tag(prefix, SLUG_PREFIX, "-").replace("_", "\\_") + "%"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE h.status = 'DRAFT'), count(*) FILTER (WHERE h.status = 'PUBLISHED'),"
            " count(*) FILTER (WHERE h.image_url IS NOT NULL)"
            " FROM honors h JOIN ministries m ON m.id = h.ministry_id WHERE m.slug = %s AND h.slug LIKE %s",
            (MINISTRY, like))
        honors, draft, published, images = cur.fetchone()
        cur.execute(
            "SELECT r.locale, coalesce(r.source, '(editor)'), count(*) FROM honor_requirements r"
            " JOIN honors h ON h.id = r.honor_id JOIN ministries m ON m.id = h.ministry_id"
            " WHERE m.slug = %s AND h.slug LIKE %s GROUP BY 1, 2 ORDER BY 1, 2",
            (MINISTRY, like))
        requirements = {f"{locale}/{source}": n for locale, source, n in cur.fetchall()}
        cur.execute(
            "SELECT count(*) FROM honor_translations t JOIN honors h ON h.id = t.honor_id"
            " JOIN ministries m ON m.id = h.ministry_id WHERE m.slug = %s AND h.slug LIKE %s",
            (MINISTRY, like))
        translations = cur.fetchone()[0]
    return {"honors": honors, "draft": draft, "published": published, "with_image": images,
            "requirements": requirements, "honor_translations": translations}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="por defecto: todo en una transacción y ROLLBACK")
    mode.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL")
    parser.add_argument("--publish", action="store_true", help="pone los 164 en PUBLISHED (decisión del propietario)")
    parser.add_argument("--mundoja-always", action="store_true",
                        help="usa la lista de mundoja aunque no sea la del libro (3 fichas)")
    parser.add_argument("--instructor-notes", action="store_true",
                        help="carga las «Supporting Answers»/«Ayuda» en instructions (se imprimen en la hoja del miembro)")
    parser.add_argument("--operator", default=os.environ.get("USER"), help="quién importa (audit_log)")
    args = parser.parse_args()
    try:
        plans = build(load(), mundoja_always=args.mundoja_always, instructor_notes=args.instructor_notes)
    except ImportError_ as exc:
        sys.exit(f"ERROR: {exc}")
    for category, row in summary(plans).items():
        print(f"{category}: {row}")
    print(f"{len(plans)} awards, {sum(len(p['requirements']['en']) for p in plans)} requisitos (en),"
          f" {sum(len(p['requirements']['es']) for p in plans)} (es); con imagen: {sum(1 for p in plans if p['image_url'])}")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Sin DATABASE_URL: solo se validaron los datos.")
        return
    with connect(url) as conn:
        try:
            report = run(conn, plans, publish=args.publish, operator=args.operator)
            after = counts(conn)
        except ImportError_ as exc:
            conn.rollback()
            sys.exit(f"ERROR: {exc}")
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({**report, "after": after, "committed": bool(args.commit)}, ensure_ascii=False, indent=1))
    if not args.commit:
        print("SIMULACRO: nada se ha escrito (ROLLBACK). Repite con --commit.")
    elif not args.publish:
        print("Los awards nuevos entran como BORRADOR: publícalos con --publish cuando el propietario lo decida.")


if __name__ == "__main__":
    main()
