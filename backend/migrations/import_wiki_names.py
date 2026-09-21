"""Load the official honour names (other languages) and wiki links into the catalogue.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_wiki_names.py data/wiki_honor_links.json [--commit]

Source: Pathfinder Wiki (wiki.pathfindersonline.org), text under CC BY-SA 3.0 — every row keeps
`source`, `source_url` and `license`, and the attribution must be shown wherever the text is.
`data/wiki_honor_links.json` is built by catalog_tools/match_wiki.py from the wiki's index pages.
Idempotent: translations are upserted by (honor, locale); honours are found by ministry + slug
and only their wiki_title / authority / skill_level / year_introduced are filled in. Spanish
names (the source text in honors.name) are never touched. Dry run unless --commit.
"""
import argparse, json, os, sys
from urllib.parse import quote

import psycopg

WIKI = "https://wiki.pathfindersonline.org/w/AY_Honors/"
SUBPAGE = {"en": "", "pt-BR": "/pt-br", "fr": "/fr", "de": "/de", "uk": "/uk", "es": "/es"}


def page_url(title, locale):
    return WIKI + quote(title.replace(" ", "_"), safe="()_-.,'!") + SUBPAGE.get(locale, "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links")
    ap.add_argument("--ministry", default="pathfinders")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    data = json.load(open(args.links, encoding="utf-8"))
    report = {"honors_linked": 0, "honor_names": 0, "category_names": 0, "missing_slugs": []}

    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM ministries WHERE slug = %s", (args.ministry,))
        ministry = cur.fetchone()
        if not ministry:
            sys.exit(f"Ministry {args.ministry} not found")
        for slug, names in data["categories"].items():
            cur.execute("SELECT id FROM honor_categories WHERE ministry_id = %s AND slug = %s", (ministry[0], slug))
            row = cur.fetchone()
            if not row:
                report["missing_slugs"].append(f"category:{slug}")
                continue
            for locale, name in names.items():
                cur.execute(
                    "INSERT INTO honor_category_translations (category_id, locale, name) VALUES (%s, %s, %s)"
                    " ON CONFLICT (category_id, locale) DO UPDATE SET name = EXCLUDED.name, updated_at = now()",
                    (row[0], locale, name))
                report["category_names"] += 1
        for honor in data["honors"]:
            # every version of the honour shares the catalogue slug or "<slug>-vN"
            cur.execute("SELECT id FROM honors WHERE ministry_id = %s AND (slug = %s OR slug ~ %s)",
                        (ministry[0], honor["slug"], "^" + honor["slug"] + "-v[0-9]+$"))
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                report["missing_slugs"].append(honor["slug"])
                continue
            for honor_id in ids:
                cur.execute(
                    "UPDATE honors SET wiki_title = %s, authority = %s, skill_level = %s, year_introduced = %s"
                    " WHERE id = %s", (honor["wiki_title"], honor["authority"], honor["skill_level"],
                                       honor["year_introduced"], honor_id))
                for locale, name in honor["names"].items():
                    cur.execute(
                        "INSERT INTO honor_translations (honor_id, locale, name, source, source_url, license)"
                        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (honor_id, locale) DO UPDATE SET"
                        " name = EXCLUDED.name, source = EXCLUDED.source, source_url = EXCLUDED.source_url,"
                        " license = EXCLUDED.license, updated_at = now()",
                        (honor_id, locale, name[:180], data["source"], page_url(honor["wiki_title"], locale),
                         data["license"]))
                    report["honor_names"] += 1
            report["honors_linked"] += 1
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({**report, "committed": args.commit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
