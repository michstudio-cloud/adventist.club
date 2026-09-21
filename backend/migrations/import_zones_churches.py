"""Load the zones and churches of ONE association from a CSV (Bloque E, E6).

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_zones_churches.py zonas.csv --association NTAM [--commit]

CSV columns (header required, in any order): `zona, iglesia, ciudad`. `ciudad` is
optional. One row per church; the zone is created the first time it appears.

Dry run by default: it prints what it WOULD create and changes nothing. With
`--commit` it inserts what is missing and nothing else — it never renames, moves
or deletes an existing node, so re-running it is safe and idempotent.

Matching is by name, accents and case ignored, inside the association: that is
exactly what stops a second «Iglesia Central» being created. A church that
already exists under ANOTHER zone is reported and left alone; moving it is a
deliberate act of the association (`POST /org-nodes/churches/{id}/place`).
"""
import argparse
import csv
import json
import os
import sys
import unicodedata
import uuid

import psycopg

ZONE, CHURCH = "zone", "church"


def normalize(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", " ".join((name or "").split()))
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower()


def find_association(cur, reference: str):
    cur.execute(
        "SELECT id, name, path::text, country FROM organizations"
        " WHERE type = 'association' AND status = 'active'"
        "   AND (code = %(ref)s OR id::text = %(ref)s)",
        {"ref": reference},
    )
    row = cur.fetchone()
    if row is None:
        sys.exit(f"No active association with code or id {reference!r}")
    return {"id": row[0], "name": row[1], "path": row[2], "country": row[3]}


def children(cur, parent_id, node_type: str) -> dict[str, dict]:
    cur.execute(
        "SELECT id, name, path::text FROM organizations"
        " WHERE parent_id = %s AND type = %s AND status = 'active'",
        (parent_id, node_type),
    )
    return {
        normalize(name): {"id": node_id, "name": name, "path": path}
        for node_id, name, path in cur.fetchall()
    }


def churches_anywhere(cur, association_path: str) -> dict[str, dict]:
    cur.execute(
        "SELECT id, name, parent_id FROM organizations"
        " WHERE type = 'church' AND status = 'active' AND path <@ %s::ltree",
        (association_path,),
    )
    return {
        normalize(name): {"id": node_id, "name": name, "parent_id": parent_id}
        for node_id, name, parent_id in cur.fetchall()
    }


def insert(cur, *, parent, node_type: str, name: str, city, country, commit: bool) -> dict:
    node_id = uuid.uuid4()
    path = f"{parent['path']}.{node_id.hex}"
    if commit:
        cur.execute(
            "INSERT INTO organizations (id, parent_id, type, name, status, path, city, country,"
            " created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, 'active', text2ltree(%s), %s, %s, now(), now())",
            (node_id, parent["id"], node_type, name, path, city, country),
        )
    return {"id": node_id, "name": name, "path": path}


def run(conn, rows, association_ref: str, *, commit: bool) -> dict:
    report = {
        "association": None,
        "zones_created": [],
        "churches_created": [],
        "already_there": 0,
        "elsewhere": [],
        "skipped": [],
    }
    with conn.cursor() as cur:
        association = find_association(cur, association_ref)
        report["association"] = association["name"]
        zones = children(cur, association["id"], ZONE)
        existing_churches = churches_anywhere(cur, association["path"])

        for number, row in enumerate(rows, start=2):
            zone_name = " ".join((row.get("zona") or "").split())
            church_name = " ".join((row.get("iglesia") or "").split())
            city = " ".join((row.get("ciudad") or "").split()) or None
            if not zone_name or not church_name:
                report["skipped"].append({"line": number, "row": row})
                continue

            zone = zones.get(normalize(zone_name))
            if zone is None:
                zone = insert(
                    cur,
                    parent=association,
                    node_type=ZONE,
                    name=zone_name,
                    city=None,
                    country=association["country"],
                    commit=commit,
                )
                zones[normalize(zone_name)] = zone
                report["zones_created"].append(zone_name)

            church = existing_churches.get(normalize(church_name))
            if church is not None:
                if church["parent_id"] == zone["id"]:
                    report["already_there"] += 1
                else:
                    # Never moved from here: that is the association's own act.
                    report["elsewhere"].append(
                        {"church": church["name"], "id": str(church["id"]), "zone": zone_name}
                    )
                continue

            created = insert(
                cur,
                parent=zone,
                node_type=CHURCH,
                name=church_name,
                city=city,
                country=association["country"],
                commit=commit,
            )
            existing_churches[normalize(church_name)] = {
                "id": created["id"],
                "name": church_name,
                "parent_id": zone["id"],
            }
            report["churches_created"].append({"zone": zone_name, "church": church_name})
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--association", required=True, help="organizations.code or id")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")

    with open(args.csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = [
            {(key or "").strip().lower(): value for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]

    with psycopg.connect(url) as conn:
        report = run(conn, rows, args.association, commit=args.commit)
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    report["mode"] = "commit" if args.commit else "dry-run"
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
