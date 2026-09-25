"""Load the official requirements (downloaded by catalog_tools/crawl_wiki_requirements.py) into
`honor_requirements`, one list per language.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_wiki_requirements.py ~/adventist-wiki es en [--commit]

Source: Pathfinder Wiki, CC BY-SA 3.0 — every row keeps source / source_url / license.
Rules:
- honours are found by `honors.wiki_title` (set by import_wiki_names.py);
- a list written by an instructor (rows of that locale without `source`) is never touched, nor an
  official list of that language from another source (`guiasmayores.com`, `mundoja.org`, `spd-pathfinders`…,
  import_official_requirements.py): the page would be added next to it as a second list;
- a translated page is used only when the wiki says it is 100 % translated;
- an unofficial translation of that language (`traduccion-no-oficial-*`, import_requirement_translations.py)
  is replaced in place by the official page;
- idempotent: wiki rows are updated in place by position; surplus rows are removed only when no
  exam question hangs from them. Dry run unless --commit.
`is_theoretical` is left at its default: whether a requirement needs practical review is a
reviewer's decision, not something the wiki states.
"""
import argparse, json, os, pathlib, re, sys, uuid
from urllib.parse import quote

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).parent / "catalog_tools"))
from parse_wiki_requirements import parse, translation_progress  # noqa: E402

WIKI = "https://wiki.pathfindersonline.org/w/AY_Honors/"
LOCALES = {"en": "en", "es": "es", "pt-br": "pt-BR", "fr": "fr", "de": "de", "uk": "uk"}
SOURCE, LICENSE = "pathfinder-wiki", "CC BY-SA 3.0"


def file_name(title):
    return re.sub(r"[^A-Za-z0-9()._-]+", "_", title) + ".html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("langs", nargs="+")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    base = pathlib.Path(args.folder).expanduser() / "requirements"
    report = {lang: {"honors": 0, "requirements": 0, "not_downloaded": 0, "partial_translation": 0,
                     "kept_instructor_list": 0, "kept_official_list": [], "nothing_parsed": []} for lang in args.langs}

    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, wiki_title FROM honors WHERE wiki_title IS NOT NULL")
        honors = cur.fetchall()
        for lang in args.langs:
            locale, stats = LOCALES[lang], report[lang]
            for honor_id, title in honors:
                path = base / lang / file_name(title)
                if not path.exists():
                    stats["not_downloaded"] += 1
                    continue
                page = path.read_text(encoding="utf-8", errors="ignore")
                if lang != "en" and translation_progress(page) != 100:
                    stats["partial_translation"] += 1
                    continue
                rows = parse(page)
                if not rows:
                    stats["nothing_parsed"].append(title)
                    continue
                cur.execute("SELECT count(*) FROM honor_requirements WHERE honor_id = %s AND locale = %s AND source IS NULL",
                            (honor_id, locale))
                if cur.fetchone()[0]:
                    stats["kept_instructor_list"] += 1
                    continue
                cur.execute("SELECT count(*) FROM honor_requirements WHERE honor_id = %s AND locale = %s"
                            " AND source <> %s AND source NOT LIKE 'traduccion-no-oficial-%%'",
                            (honor_id, locale, SOURCE))
                if cur.fetchone()[0]:
                    stats["kept_official_list"].append(title)
                    continue
                source_url = WIKI + quote(title.replace(" ", "_"), safe="()_-.,'!") + "/Requirements" + ("" if lang == "en" else "/" + lang)
                # our own rows and an unofficial translation (import_requirement_translations.py) of this
                # language: the official page replaces the translation in place (ids and progress survive)
                cur.execute("SELECT id, position FROM honor_requirements WHERE honor_id = %s AND locale = %s"
                            " AND (source = %s OR source LIKE 'traduccion-no-oficial-%%') ORDER BY source = %s DESC",
                            (honor_id, locale, SOURCE, SOURCE))
                existing = {}
                for row_id, position in cur.fetchall():
                    existing.setdefault(position, row_id)
                for row in rows:
                    section = f"Sección: {row['section']}" if row["section"] else None
                    if row["position"] in existing:
                        cur.execute("UPDATE honor_requirements SET description = %s, instructions = %s, source = %s,"
                                    " source_url = %s, license = %s WHERE id = %s",
                                    (row["description"], section, SOURCE, source_url, LICENSE,
                                     existing.pop(row["position"])))
                    else:
                        cur.execute("INSERT INTO honor_requirements (id, honor_id, position, description, instructions,"
                                    " locale, source, source_url, license) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                    (uuid.uuid4(), honor_id, row["position"], row["description"], section, locale,
                                     SOURCE, source_url, LICENSE))
                for leftover in existing.values():
                    cur.execute("DELETE FROM honor_requirements r WHERE r.id = %s AND NOT EXISTS"
                                " (SELECT 1 FROM honor_questions q WHERE q.requirement_id = r.id)", (leftover,))
                stats["honors"] += 1
                stats["requirements"] += len(rows)
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({**report, "committed": args.commit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
