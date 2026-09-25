"""Load the certifications of Guías Mayores (024) from `data/master_guide_catalog.json` into the
program catalogue of the `master-guides` ministry: the five EMC certifications (TRAINING) and one
example each of MASTERY and MEDALLION.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_master_guide_catalog.py                 # simulacro: ROLLBACK
    DATABASE_URL=... python import_master_guide_catalog.py --commit        # escribe (BORRADOR)
    DATABASE_URL=... python import_master_guide_catalog.py --commit --publish   # publica el EMC

Without DATABASE_URL it only validates the JSON and prints the counts. The JSON is built by
`catalog_tools/build_master_guide_catalog.py` (nothing is downloaded here).

Rules (same spirit as import_adventurer_classes.py):
  * needs 024_master_guide_catalog.sql (the kinds MEDALLION / MASTERY / TRAINING);
  * everything enters as DRAFT, issuer_level ASSOCIATION, authority from the JSON (IAD: mundoja.org
    follows the Inter-American Division); texts in Spanish, source 'mundoja.org' + the page URL,
    author «Mundo J.A (voluntarios)» kept in the audit row and the licence text;
  * idempotent by (ministry, slug): the latest version of the slug is rewritten in place while it
    is a DRAFT, and ONLY when the content changed since this importer last wrote it (content hash
    in audit_log, action PROGRAM_IMPORT); a rerun with the same JSON writes nothing;
  * a PUBLISHED or ARCHIVED program is never rewritten;
  * `--publish` publishes the EMC certifications only: an `example: true` program («ejemplo,
    sustituir») is never published by this script;
  * every requirement is FREE (the EMC asks for attendance, mentoring and a portfolio: evidence).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"
DEFAULT_DATA = DATA / "master_guide_catalog.json"

MINISTRY = "master-guides"
IMPORTER = "import_master_guide_catalog"
AUDIT_ACTION = "PROGRAM_IMPORT"
SOURCE = "mundoja.org"
LOCALE = "es"
PROGRAM_KINDS = ("MEDALLION", "MASTERY", "TRAINING")
EXAMPLE_MARK = "(ejemplo, sustituir)"


class ImportError_(Exception):
    """Loud failure: nothing is written when the data does not make sense."""


# ----------------------------------------------------------------------------
# The JSON (no database)
# ----------------------------------------------------------------------------
def load(path: pathlib.Path = DEFAULT_DATA) -> dict:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    validate(data)
    return data


def requirements_of(program: dict):
    for section in program["sections"]:
        yield from section["requirements"]


def validate(data: dict) -> None:
    programs = data.get("programs") or []
    if not programs:
        raise ImportError_("el JSON no tiene programas")
    if not data.get("author") or not data.get("license"):
        raise ImportError_("el JSON necesita autoría (author) y licencia (license)")
    slugs, codes = set(), set()
    for program in programs:
        where = program.get("slug") or "?"
        for field in ("kind", "ministry", "slug", "code", "names", "sections", "source_url", "authority"):
            if not program.get(field):
                raise ImportError_(f"{where}: falta el campo obligatorio '{field}'")
        if program["kind"] not in PROGRAM_KINDS or program["ministry"] != MINISTRY:
            raise ImportError_(f"{where}: sólo {', '.join(PROGRAM_KINDS)} del ministerio {MINISTRY}")
        if program["slug"] in slugs or program["code"] in codes:
            raise ImportError_(f"{where}: slug o código repetido")
        slugs.add(program["slug"])
        codes.add(program["code"])
        if not program["names"].get(LOCALE) or not program["source_url"].get(LOCALE):
            raise ImportError_(f"{where}: falta el nombre o la fuente en {LOCALE}")
        if not str(program["source_url"][LOCALE]).startswith("https://mundoja.org/"):
            raise ImportError_(f"{where}: la fuente debe ser una página de mundoja.org")
        if program.get("example") and EXAMPLE_MARK not in program["names"][LOCALE]:
            raise ImportError_(f"{where}: un ejemplo lleva «{EXAMPLE_MARK}» en el nombre")
        positions, section_slugs, labels = [], set(), set()
        for section in program["sections"]:
            if not section.get("slug") or section["slug"] in section_slugs:
                raise ImportError_(f"{where}: sección sin slug o repetida")
            section_slugs.add(section["slug"])
            if not (section.get("names") or {}).get(LOCALE):
                raise ImportError_(f"{where}/{section['slug']}: la sección necesita nombre en {LOCALE}")
            if not section.get("requirements"):
                raise ImportError_(f"{where}/{section['slug']}: sección sin requisitos")
            for requirement in section["requirements"]:
                positions.append(requirement.get("position"))
                label = requirement.get("label") or ""
                if not 1 <= len(label) <= 12 or label in labels:
                    raise ImportError_(f"{where}: label '{label}' vacío, largo o repetido")
                labels.add(label)
                if requirement.get("kind") != "TEXT":
                    raise ImportError_(f"{where} {label}: sólo requisitos TEXT (FREE)")
                if not (requirement.get("text") or {}).get(LOCALE, "").strip():
                    raise ImportError_(f"{where} {label}: falta el texto en {LOCALE}")
                if not (requirement.get("source_url") or {}).get(LOCALE):
                    raise ImportError_(f"{where} {label}: falta source_url en {LOCALE}")
        if positions != list(range(1, len(positions) + 1)):
            raise ImportError_(f"{where}: las posiciones deben ser 1..N sin huecos, en orden")


def content_hash(program: dict, license_: str | None) -> str:
    """What this importer writes for a program: a change here means «rewrite the draft»."""
    payload = {k: program.get(k) for k in ("kind", "code", "sort_order", "authority", "issuer_level", "names",
                                           "description", "source_url", "sections", "example")}
    payload["license"] = license_
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def counts(data: dict) -> list[dict]:
    return [
        {"slug": p["slug"], "kind": p["kind"], "example": bool(p.get("example")),
         "sections": len(p["sections"]), "requirements": sum(1 for _ in requirements_of(p))}
        for p in data["programs"]
    ]


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
def connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))


def _tag(prefix: str, value: str, sep: str = "-") -> str:
    return f"{prefix}{sep}{value}" if prefix else value


def _slug_regex(slug: str) -> str:
    return "^" + re.escape(slug) + "-v[0-9]+$"


def _check_kinds(cur) -> None:
    cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'programs_kind_check'")
    row = cur.fetchone()
    if not row or any(kind not in row[0] for kind in PROGRAM_KINDS):
        raise ImportError_("falta la migración 024_master_guide_catalog.sql (kinds MEDALLION / MASTERY / TRAINING)")


def run(conn, data: dict, *, publish: bool = False, operator: str | None = None, prefix: str = "") -> dict:
    """Apply the JSON inside the caller's transaction (the caller commits or rolls back).
    `prefix` exists for the tests: it tags slugs, codes and names so the cleanup removes them."""
    report = {"created": 0, "updated": 0, "unchanged": 0, "skipped_not_draft": [], "published": 0,
              "examples_not_published": 0, "requirements_written": 0}
    operator = operator or IMPORTER
    license_ = data.get("license")
    with conn.cursor() as cur:
        _check_kinds(cur)
        cur.execute("SELECT id FROM ministries WHERE slug = %s", (MINISTRY,))
        row = cur.fetchone()
        if not row:
            raise ImportError_(f"no existe el ministerio {MINISTRY}")
        ministry_id = row[0]

        for program in data["programs"]:
            slug = _tag(prefix, program["slug"])
            wanted = content_hash(program, license_)
            cur.execute(
                "SELECT id, status FROM programs WHERE ministry_id = %s AND (slug = %s OR slug ~ %s)"
                " ORDER BY version DESC LIMIT 1", (ministry_id, slug, _slug_regex(slug)))
            existing = cur.fetchone()
            if existing and existing[1] != "DRAFT":
                report["skipped_not_draft"].append(f"{slug} ({existing[1]})")
                continue
            fields = {
                "kind": program["kind"], "code": _tag(prefix, program["code"]),
                "name": _tag(prefix, program["names"][LOCALE], " "),
                "description": (program.get("description") or {}).get(LOCALE),
                "sort_order": int(program.get("sort_order") or 0), "authority": program["authority"],
                "issuer_level": program.get("issuer_level") or "ASSOCIATION", "source": SOURCE,
                "source_url": program["source_url"][LOCALE], "license": license_,
            }
            if existing is None:
                cur.execute(
                    "INSERT INTO programs (ministry_id, kind, slug, code, name, description, sort_order,"
                    " authority, status, issuer_level, version, source, source_url, license)"
                    " VALUES (%(ministry)s, %(kind)s, %(slug)s, %(code)s, %(name)s, %(description)s,"
                    " %(sort_order)s, %(authority)s, 'DRAFT', %(issuer_level)s, 1, %(source)s, %(source_url)s,"
                    " %(license)s) RETURNING id",
                    {**fields, "ministry": ministry_id, "slug": slug})
                program_id = cur.fetchone()[0]
                action = "created"
            else:
                program_id = existing[0]
                cur.execute(
                    "SELECT metadata_json->>'content_hash' FROM audit_log WHERE action = %s AND entity_type = 'PROGRAM'"
                    " AND entity_id = %s AND metadata_json->>'importer' = %s ORDER BY created_at DESC LIMIT 1",
                    (AUDIT_ACTION, str(program_id), IMPORTER))
                last = cur.fetchone()
                if last and last[0] == wanted:
                    report["unchanged"] += 1
                    action = None
                else:
                    # A DRAFT was never enrolled in: its content is recreated (sections cascade).
                    cur.execute("DELETE FROM program_sections WHERE program_id = %s", (program_id,))
                    cur.execute("DELETE FROM program_translations WHERE program_id = %s", (program_id,))
                    cur.execute(
                        "UPDATE programs SET kind = %(kind)s, code = %(code)s, name = %(name)s,"
                        " description = %(description)s, sort_order = %(sort_order)s, authority = %(authority)s,"
                        " issuer_level = %(issuer_level)s, source = %(source)s, source_url = %(source_url)s,"
                        " license = %(license)s, updated_at = now() WHERE id = %(id)s",
                        {**fields, "id": program_id})
                    action = "updated_draft"
            if action:
                _write_content(cur, program_id, program, license_, prefix, report)
                report["created" if action == "created" else "updated"] += 1
                cur.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, user_email, details, metadata_json)"
                    " VALUES (%s, 'PROGRAM', %s, %s, %s, %s::jsonb)",
                    (AUDIT_ACTION, str(program_id), operator, f"{action}: {MINISTRY}/{slug} ({IMPORTER})",
                     json.dumps({"importer": IMPORTER, "slug": slug, "kind": program["kind"],
                                 "content_hash": wanted, "author": data.get("author"),
                                 "example": bool(program.get("example")),
                                 "requirements": sum(1 for _ in requirements_of(program))})))
            if publish:
                if program.get("example"):
                    report["examples_not_published"] += 1
                else:
                    report["published"] += _publish(cur, program_id, slug, operator)
    return report


def _write_content(cur, program_id, program: dict, license_: str | None, prefix: str, report: dict) -> None:
    cur.execute(
        "INSERT INTO program_translations (program_id, locale, name, description, source, source_url, license)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (program_id, LOCALE, _tag(prefix, program["names"][LOCALE], " "),
         (program.get("description") or {}).get(LOCALE), SOURCE, program["source_url"][LOCALE], license_))
    for position, section in enumerate(program["sections"], start=1):
        cur.execute(
            "INSERT INTO program_sections (id, program_id, position, slug, name)"
            " VALUES (md5(%s::text || ':section:' || %s)::uuid, %s, %s, %s, %s) RETURNING id",
            (str(program_id), section["slug"], program_id, position, section["slug"], section["names"][LOCALE]))
        section_id = cur.fetchone()[0]
        cur.execute("INSERT INTO program_section_translations (section_id, locale, name) VALUES (%s, %s, %s)",
                    (section_id, LOCALE, section["names"][LOCALE]))
        for requirement in section["requirements"]:
            cur.execute(
                "INSERT INTO program_requirements (id, program_id, section_id, position, label, kind,"
                " evidence_required) VALUES (md5(%s::text || ':requirement:' || %s)::uuid, %s, %s, %s, %s,"
                " 'FREE', %s) RETURNING id",
                (str(program_id), requirement["position"], program_id, section_id, requirement["position"],
                 requirement["label"], bool(requirement.get("evidence_required"))))
            requirement_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO program_requirement_texts (requirement_id, locale, description, source,"
                " source_url, license) VALUES (%s, %s, %s, %s, %s, %s)",
                (requirement_id, LOCALE, requirement["text"][LOCALE], SOURCE,
                 requirement["source_url"][LOCALE], license_))
            report["requirements_written"] += 1


def _publish(cur, program_id, slug: str, operator: str) -> int:
    cur.execute("SELECT status FROM programs WHERE id = %s", (program_id,))
    if cur.fetchone()[0] != "DRAFT":
        return 0
    cur.execute("UPDATE programs SET status = 'PUBLISHED', published_at = now(), updated_at = now() WHERE id = %s",
                (program_id,))
    cur.execute(
        "INSERT INTO audit_log (action, entity_type, entity_id, user_email, details, metadata_json)"
        " VALUES ('PROGRAM_PUBLISH', 'PROGRAM', %s, %s, %s, %s::jsonb)",
        (str(program_id), operator, f"published: {MINISTRY}/{slug} ({IMPORTER})",
         json.dumps({"importer": IMPORTER, "slug": slug})))
    return 1


def db_counts(conn, prefix: str = "") -> list[tuple]:
    like = _tag(prefix, "", "-") + "%" if prefix else "%"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.slug, p.kind, p.status, p.version,"
            " (SELECT count(*) FROM program_sections s WHERE s.program_id = p.id),"
            " (SELECT count(*) FROM program_requirements r WHERE r.program_id = p.id)"
            " FROM programs p JOIN ministries m ON m.id = p.ministry_id"
            " WHERE m.slug = %s AND p.kind IN ('MEDALLION', 'MASTERY', 'TRAINING') AND p.slug LIKE %s"
            " ORDER BY p.sort_order, p.slug",
            (MINISTRY, like))
        return cur.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="por defecto: todo en una transacción y ROLLBACK")
    mode.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL")
    parser.add_argument("--publish", action="store_true", help="publica las certificaciones EMC (nunca los ejemplos)")
    parser.add_argument("--operator", default=os.environ.get("USER"), help="quién importa (audit_log)")
    args = parser.parse_args()
    try:
        data = load(pathlib.Path(args.data).expanduser())
    except ImportError_ as exc:
        sys.exit(f"ERROR: {exc}")
    for row in counts(data):
        print(f"{row['slug']} [{row['kind']}{', ejemplo' if row['example'] else ''}]: {row['sections']} secciones,"
              f" {row['requirements']} requisitos")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Sin DATABASE_URL: solo se validaron los datos.")
        return
    with connect(url) as conn:
        try:
            report = run(conn, data, publish=args.publish, operator=args.operator)
            after = db_counts(conn)
        except ImportError_ as exc:
            conn.rollback()
            sys.exit(f"ERROR: {exc}")
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("slug | kind | estado | versión | secciones | requisitos")
    for row in after:
        print(" | ".join(str(value) for value in row))
    if not args.commit:
        print("SIMULACRO: nada se escribió (usa --commit).")


if __name__ == "__main__":
    main()
