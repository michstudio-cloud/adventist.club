"""Import the SDA world administrative directory (Adventist Yearbook) into `organizations`.

    pip install openpyxl "psycopg[binary]"
    DATABASE_URL=... python import_world_directory.py data/directorio_mundial_iasd_2026.xlsx [--commit]

Idempotent: rows are keyed by the official Yearbook code (`organizations.code`), so a
re-run refreshes names/metadata without duplicating. Tree: General Conference (level 1)
> division (2) > union (3) > local field (4: conference / mission / region / field), all
stored as one `organizations` tree with an ltree `path` (label = uuid hex). Spanish names
win when the sheet has them; the official English name is kept in metadata_json.
"""
import argparse, json, os, sys, uuid
from datetime import date, datetime

import openpyxl, psycopg
from psycopg.types.json import Jsonb

NS = uuid.UUID("2c1a9d5e-7f43-4b1a-9c58-2f0d6c8b3e11")   # uuid5 namespace for yearbook entities
TYPE_BY_LEVEL = {1: "general_conference", 2: "division", 3: "union", 4: "association"}


def clean(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--sheet", default="Directorio mundial")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")

    ws = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)[args.sheet]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    records = [dict(zip(header, r)) for r in rows[1:] if r and r[0] is not None]
    by_id = {int(r["entity_id"]): r for r in records}

    def chain(r):
        out, seen = [], set()
        while r and int(r["entity_id"]) not in seen:
            seen.add(int(r["entity_id"]))
            out.append(r)
            pid = r.get("parent_id")
            r = by_id.get(int(pid)) if pid not in (None, "") else None
        return list(reversed(out))

    report = {"created": 0, "updated": 0, "orphans": 0}
    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn, conn.cursor() as cur:
        for r in sorted(records, key=lambda r: (len(chain(r)), int(r["entity_id"]))):
            eid = int(r["entity_id"])
            lineage = chain(r)
            parent = lineage[-2] if len(lineage) > 1 else None
            if r.get("parent_id") not in (None, "") and parent is None:
                report["orphans"] += 1
            oid = uuid.uuid5(NS, str(eid))
            path = ".".join(uuid.uuid5(NS, str(int(x["entity_id"]))).hex for x in lineage)
            level = int(r.get("org_level") or len(lineage))
            metadata = {
                "yearbook_entity_id": eid, "official_name": r.get("official_name"),
                "entity_type_code": r.get("entity_type_code"), "subtype_en": r.get("subtype_en"),
                "subtype_es": r.get("subtype_es"), "category": r.get("category"),
                "division_code": r.get("division_code"), "union_code": r.get("union_code"),
                "territory": r.get("territory"), "website": r.get("website"),
                "churches": r.get("churches"), "membership": r.get("membership"),
                "population": r.get("population"), "statistics_date": r.get("statistics_date"),
                "source_url": r.get("source_url"), "modified_at": clean(r.get("modified_at")),
                "is_umn": bool(r.get("is_umn")),
            }
            cur.execute(
                """INSERT INTO organizations (id,parent_id,type,name,code,status,path,city,state,country,metadata_json)
                   VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET parent_id=EXCLUDED.parent_id,type=EXCLUDED.type,
                     name=EXCLUDED.name,code=EXCLUDED.code,path=EXCLUDED.path,city=EXCLUDED.city,
                     state=EXCLUDED.state,country=EXCLUDED.country,metadata_json=EXCLUDED.metadata_json,
                     updated_at=now()
                   RETURNING (xmax = 0)""",
                (oid, uuid.uuid5(NS, str(int(parent["entity_id"]))) if parent else None,
                 TYPE_BY_LEVEL.get(level, "association"), r.get("spanish_name") or r["official_name"],
                 str(r["code"]).strip(), path, r.get("city"), r.get("state_province"), r.get("country_code"),
                 Jsonb({k: v for k, v in metadata.items() if v not in (None, "")})))
            report["created" if cur.fetchone()[0] else "updated"] += 1
        cur.execute("SELECT type, count(*) FROM organizations WHERE metadata_json ? 'yearbook_entity_id' GROUP BY type ORDER BY 1")
        report["by_type"] = dict(cur.fetchall())
        cur.execute("""SELECT o.name, nlevel(o.path) FROM organizations o WHERE o.code='ANT'""")
        report["ant"] = cur.fetchone()
        (conn.commit if args.commit else conn.rollback)()
    report["mode"] = "COMMIT" if args.commit else "DRY RUN (rolled back)"
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
