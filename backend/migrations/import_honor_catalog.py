"""Import the Spanish Pathfinder honor catalogue into Postgres.

    DATABASE_URL=... python import_honor_catalog.py data/honors_pathfinders_es.json            # dry run
    DATABASE_URL=... python import_honor_catalog.py data/honors_pathfinders_es.json --commit
    ... --commit --media-base https://media.adventist.club/patches   # also set image_url

Idempotent: honors are keyed by (ministry, slug). Rows that already exist keep their id
(certificates reference it) and only catalogue fields are refreshed. `image_url` is only
written with --media-base, i.e. once the files really exist in the bucket, so the API never
serves broken image URLs. Requirement PDFs are stored as a resource pointing at the source.
"""
import argparse, json, os, sys

import psycopg

# site category -> (slug, Spanish name); the first 8 are the official GC/NAD taxonomy seeded by 001.
EXTRA_CATEGORIES = {
    "adra": "ADRA",
    "doctrinal": "Doctrinales",
    "community-services": "Servicios Comunitarios Adventistas",
    "florida-conference": "Especialidades de la Asociación de Florida",
    "masters": "Maestrías",
}
LOCAL_CATEGORIES = {"florida-conference"}
PDF_RESOURCE_NAME = "Requisitos (PDF)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("catalog")
    ap.add_argument("--ministry", default="pathfinders")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--media-base", help="public base URL where <image_file> already exists")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    items = json.load(open(args.catalog, encoding="utf8"))
    created = updated = resources = 0

    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM ministries WHERE slug=%s", (args.ministry,))
        row = cur.fetchone()
        if not row:
            sys.exit(f"ministry {args.ministry!r} not found")
        ministry = row[0]
        for slug, name in EXTRA_CATEGORIES.items():
            cur.execute("""INSERT INTO honor_categories (ministry_id,name,slug) VALUES (%s,%s,%s)
                           ON CONFLICT (ministry_id,slug) DO NOTHING""", (ministry, name, slug))
        cur.execute("SELECT slug,id FROM honor_categories WHERE ministry_id=%s", (ministry,))
        categories = dict(cur.fetchall())

        for it in items:
            image_url = f"{args.media_base.rstrip('/')}/{it['image_file']}" if args.media_base else None
            honor_type = "LOCAL" if it["category_slug"] in LOCAL_CATEGORIES else "OFFICIAL_GC"
            cur.execute(
                """INSERT INTO honors (ministry_id,category_id,name,slug,source_url,image_url,active,status,
                                       honor_type,published_at)
                   VALUES (%s,%s,%s,%s,%s,%s,true,'PUBLISHED',%s,now())
                   ON CONFLICT (ministry_id,slug) DO UPDATE SET
                     category_id=EXCLUDED.category_id, name=EXCLUDED.name, source_url=EXCLUDED.source_url,
                     image_url=COALESCE(EXCLUDED.image_url, honors.image_url),
                     honor_type=COALESCE(honors.honor_type, EXCLUDED.honor_type),
                     active=true, status='PUBLISHED',
                     published_at=COALESCE(honors.published_at, now()), updated_at=now()
                   RETURNING id, (xmax = 0)""",
                (ministry, categories[it["category_slug"]], it["name"], it["slug"], it["source_url"],
                 image_url, honor_type))
            honor_id, inserted = cur.fetchone()
            created += inserted
            updated += not inserted
            if it.get("requirements_pdf"):
                cur.execute("SELECT 1 FROM honor_resources WHERE honor_id=%s AND url=%s",
                            (honor_id, it["requirements_pdf"]))
                if not cur.fetchone():
                    cur.execute("""INSERT INTO honor_resources (honor_id,position,name,url,type)
                                   VALUES (%s,1,%s,%s,'pdf')""",
                                (honor_id, PDF_RESOURCE_NAME, it["requirements_pdf"]))
                    resources += 1
        if args.commit:
            conn.commit()
        else:
            conn.rollback()

    print(json.dumps({"mode": "COMMIT" if args.commit else "DRY RUN (rolled back)", "items": len(items),
                      "created": created, "updated": updated, "pdf_resources_added": resources,
                      "image_url_written": bool(args.media_base)}, indent=1))


if __name__ == "__main__":
    main()
