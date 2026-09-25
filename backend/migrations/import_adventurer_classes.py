"""Load the six Adventurer classes (Corderitos … Manos Ayudadoras) from
`data/adventurer_classes.json` into the program catalogue (012_programs.sql) of the
`adventurers` ministry, with their sections, requirements in Spanish and English and the
awards each requirement asks for (which is what `/programs/{id}/recommendations` shows).

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_adventurer_classes.py                 # simulacro: una transacción y ROLLBACK
    DATABASE_URL=... python import_adventurer_classes.py --commit        # escribe (BORRADOR)
    DATABASE_URL=... python import_adventurer_classes.py --commit --publish
    ... --image-base-url https://media.adventist.club/programs/adventurers   # emblemas ya subidos a R2

Without DATABASE_URL it only validates the JSON and prints the counts.

The JSON is built by `catalog_tools/build_adventurer_classes.py` (Spanish of mundoja.org, English
unofficial translation, award targets); nothing is downloaded here.

Rules (same spirit as import_ay_classes.py and import_adventurer_awards.py):
  * everything enters as DRAFT with authority 'GC' and issuer_level 'CLUB'; `--publish` publishes,
    but — like `POST /programs/{id}/publish` — only when every award a class points at is already
    PUBLISHED (publish the awards first: import_adventurer_awards.py --commit --publish); otherwise
    the whole transaction fails loudly and nothing is written;
  * idempotent by (ministry, slug): the latest version of the slug is rewritten in place while it
    is a DRAFT, and ONLY when the content changed since this importer last wrote it (content hash
    in audit_log, action PROGRAM_IMPORT); a rerun with the same JSON writes nothing;
  * a PUBLISHED or ARCHIVED program is never rewritten (import_programs.py makes the next version);
  * requirement kinds: TEXT -> FREE; HONOR -> HONOR with the `av-` award as target; HONOR_FROM_CATEGORY
    -> HONOR with the award category as target; HONOR_ANY -> HONOR with no target («any award»);
  * texts: es = mundoja.org (source 'mundoja.org', its class page as source_url), en = unofficial
    translation (source 'traduccion-no-oficial-mundoja'); licence '© GC Youth Ministries, permiso
    pendiente' (the curriculum belongs to the GC; mundoja is kept as author through the links);
  * `image_url`: the emblem URL when the JSON has one or --image-base-url is given; otherwise an
    existing image_url is kept (the coordinator uploads the WebP of data/adventurer_classes/ to R2);
  * a target award / category / ministry that does not exist makes the whole transaction fail.
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
DEFAULT_DATA = DATA / "adventurer_classes.json"

MINISTRY = "adventurers"
IMPORTER = "import_adventurer_classes"
AUDIT_ACTION = "PROGRAM_IMPORT"
SOURCES = {"es": "mundoja.org", "en": "traduccion-no-oficial-mundoja"}
LOCALES = ("es", "en")
PROGRAM_KINDS = {"CLASS": "CLASS"}
REQUIREMENT_KINDS = {"TEXT": "FREE", "HONOR": "HONOR", "HONOR_FROM_CATEGORY": "HONOR", "HONOR_ANY": "HONOR"}


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
    slugs, codes = set(), set()
    for program in programs:
        where = program.get("slug") or "?"
        for field in ("kind", "ministry", "slug", "code", "names", "sections", "source_url"):
            if not program.get(field):
                raise ImportError_(f"{where}: falta el campo obligatorio '{field}'")
        if program["kind"] not in PROGRAM_KINDS or program["ministry"] != MINISTRY:
            raise ImportError_(f"{where}: sólo clases (CLASS) del ministerio {MINISTRY}")
        if program["slug"] in slugs or program["code"] in codes:
            raise ImportError_(f"{where}: slug o código repetido")
        slugs.add(program["slug"])
        codes.add(program["code"])
        for locale in LOCALES:
            if not program["names"].get(locale) or not program["source_url"].get(locale):
                raise ImportError_(f"{where}: falta el nombre o la fuente en {locale}")
        positions, section_slugs = [], set()
        for section in program["sections"]:
            if not section.get("slug") or section["slug"] in section_slugs:
                raise ImportError_(f"{where}: sección sin slug o repetida")
            section_slugs.add(section["slug"])
            if not all(section.get("names", {}).get(loc) for loc in LOCALES):
                raise ImportError_(f"{where}/{section['slug']}: la sección necesita nombre es y en")
            if not section.get("requirements"):
                raise ImportError_(f"{where}/{section['slug']}: sección sin requisitos")
            for requirement in section["requirements"]:
                positions.append(requirement.get("position"))
                _check_requirement(where, requirement)
        if positions != list(range(1, len(positions) + 1)):
            raise ImportError_(f"{where}: las posiciones deben ser 1..N sin huecos, en orden")


def _check_requirement(where: str, requirement: dict) -> None:
    kind = requirement.get("kind")
    position = requirement.get("position")
    if kind not in REQUIREMENT_KINDS:
        raise ImportError_(f"{where}: kind '{kind}' no existe (requisito {position})")
    label = requirement.get("label") or ""
    if not 1 <= len(label) <= 12:
        raise ImportError_(f"{where}: el requisito {position} necesita label de 1 a 12 caracteres")
    honor, category = requirement.get("target_honor_slug"), requirement.get("target_category_slug")
    if kind == "HONOR" and (not honor or category):
        raise ImportError_(f"{where} {label}: HONOR necesita target_honor_slug y nada más")
    if kind == "HONOR_FROM_CATEGORY" and (not category or honor):
        raise ImportError_(f"{where} {label}: HONOR_FROM_CATEGORY necesita target_category_slug")
    if kind in ("TEXT", "HONOR_ANY") and (honor or category):
        raise ImportError_(f"{where} {label}: {kind} no lleva destino")
    for locale in LOCALES:
        if not (requirement.get("text") or {}).get(locale, "").strip():
            raise ImportError_(f"{where} {label}: falta el texto en {locale}")
        if not (requirement.get("source_url") or {}).get(locale):
            raise ImportError_(f"{where} {label}: falta source_url en {locale}")


def description(requirement: dict, locale: str) -> str:
    """The stored text: the requirement and its sub-items, one per line (like import_ay_classes)."""
    items = (requirement.get("sub_items") or {}).get(locale) or []
    return "\n".join([requirement["text"][locale], *items])


def content_hash(program: dict, image_url: str | None) -> str:
    """What this importer writes for a program: a change here means «rewrite the draft»."""
    payload = {k: program.get(k) for k in ("kind", "code", "sort_order", "authority", "issuer_level", "names",
                                           "description", "source_url", "sections")}
    payload["image_url"] = image_url
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def counts(data: dict) -> list[dict]:
    rows = []
    for program in data["programs"]:
        reqs = list(requirements_of(program))
        rows.append({
            "slug": program["slug"],
            "sections": len(program["sections"]),
            "requirements": len(reqs),
            "by_kind": {k: sum(1 for r in reqs if r["kind"] == k) for k in REQUIREMENT_KINDS
                        if any(r["kind"] == k for r in reqs)},
            "es_mundoja": sum(1 for r in reqs if (r.get("translation") or {}).get("es") == "mundoja"),
            "en_unofficial": sum(1 for r in reqs if (r.get("translation") or {}).get("en") == "unofficial"),
        })
    return rows


def image_url_for(program: dict, base_url: str | None) -> str | None:
    emblem = program.get("emblem") or {}
    if emblem.get("url"):
        return emblem["url"]
    if base_url and emblem.get("file"):
        return base_url.rstrip("/") + "/" + pathlib.PurePosixPath(emblem["file"]).name
    return None


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


def _resolve_targets(cur, ministry_id, data: dict, prefix: str) -> tuple[dict, dict]:
    """Every award / category the JSON names, by slug -> (id, status). Missing ones fail loudly."""
    honors, categories, missing = {}, {}, []
    for program in data["programs"]:
        for requirement in requirements_of(program):
            if requirement.get("target_honor_slug"):
                slug = _tag(prefix, requirement["target_honor_slug"])
                if slug not in honors:
                    cur.execute("SELECT id, status FROM honors WHERE ministry_id = %s AND slug = %s"
                                " ORDER BY version DESC LIMIT 1", (ministry_id, slug))
                    honors[slug] = cur.fetchone()
                    if honors[slug] is None:
                        missing.append(f"award {slug}")
            if requirement.get("target_category_slug"):
                slug = _tag(prefix, requirement["target_category_slug"])
                if slug not in categories:
                    cur.execute("SELECT id FROM honor_categories WHERE ministry_id = %s AND slug = %s",
                                (ministry_id, slug))
                    row = cur.fetchone()
                    categories[slug] = row[0] if row else None
                    if row is None:
                        missing.append(f"categoría {slug}")
    if missing:
        raise ImportError_("no existen en el catálogo de Aventureros: " + ", ".join(sorted(set(missing)))
                           + " (carga antes los awards: import_adventurer_awards.py --commit)")
    return honors, categories


def run(conn, data: dict, *, publish: bool = False, operator: str | None = None, prefix: str = "",
        image_base_url: str | None = None) -> dict:
    """Apply the JSON inside the caller's transaction (the caller commits or rolls back).
    `prefix` exists for the tests: it tags program slugs/codes/names and the target award and
    category slugs, so the test cleanup removes what they create."""
    report = {"created": 0, "updated": 0, "unchanged": 0, "skipped_not_draft": [], "published": 0,
              "requirements_written": 0, "texts_written": 0}
    operator = operator or IMPORTER
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM ministries WHERE slug = %s", (MINISTRY,))
        row = cur.fetchone()
        if not row:
            raise ImportError_(f"no existe el ministerio {MINISTRY}")
        ministry_id = row[0]
        honors, categories = _resolve_targets(cur, ministry_id, data, prefix)

        for program in data["programs"]:
            slug = _tag(prefix, program["slug"])
            image_url = image_url_for(program, image_base_url)
            wanted = content_hash(program, image_url)
            cur.execute(
                "SELECT id, status FROM programs WHERE ministry_id = %s AND (slug = %s OR slug ~ %s)"
                " ORDER BY version DESC LIMIT 1", (ministry_id, slug, _slug_regex(slug)))
            existing = cur.fetchone()
            if existing and existing[1] != "DRAFT":
                report["skipped_not_draft"].append(f"{slug} ({existing[1]})")
                continue
            fields = {
                "kind": PROGRAM_KINDS[program["kind"]], "code": _tag(prefix, program["code"]),
                "name": _tag(prefix, program["names"]["es"], " "),
                "description": (program.get("description") or {}).get("es"),
                "sort_order": int(program.get("sort_order") or 0), "authority": program.get("authority") or "GC",
                "issuer_level": program.get("issuer_level") or "CLUB", "source": SOURCES["es"],
                "source_url": program["source_url"]["es"], "license": data.get("license"),
            }
            if existing is None:
                cur.execute(
                    "INSERT INTO programs (ministry_id, kind, slug, code, name, description, image_url, sort_order,"
                    " authority, status, issuer_level, version, source, source_url, license)"
                    " VALUES (%(ministry)s, %(kind)s, %(slug)s, %(code)s, %(name)s, %(description)s, %(image_url)s,"
                    " %(sort_order)s, %(authority)s, 'DRAFT', %(issuer_level)s, 1, %(source)s, %(source_url)s,"
                    " %(license)s) RETURNING id",
                    {**fields, "ministry": ministry_id, "slug": slug, "image_url": image_url})
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
                        " description = %(description)s, image_url = COALESCE(%(image_url)s, image_url),"
                        " sort_order = %(sort_order)s, authority = %(authority)s, issuer_level = %(issuer_level)s,"
                        " source = %(source)s, source_url = %(source_url)s, license = %(license)s,"
                        " updated_at = now() WHERE id = %(id)s",
                        {**fields, "image_url": image_url, "id": program_id})
                    action = "updated_draft"
            if action:
                _write_content(cur, program_id, program, data, honors, categories, prefix, report)
                report["created" if action == "created" else "updated"] += 1
                cur.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, user_email, details, metadata_json)"
                    " VALUES (%s, 'PROGRAM', %s, %s, %s, %s::jsonb)",
                    (AUDIT_ACTION, str(program_id), operator, f"{action}: {MINISTRY}/{slug} ({IMPORTER})",
                     json.dumps({"importer": IMPORTER, "slug": slug, "content_hash": wanted,
                                 "requirements": sum(1 for _ in requirements_of(program))})))
            if publish:
                report["published"] += _publish(cur, program_id, slug, honors, operator)
    return report


def _write_content(cur, program_id, program: dict, data: dict, honors: dict, categories: dict, prefix: str,
                   report: dict) -> None:
    license_ = data.get("license")
    for locale in LOCALES:
        cur.execute(
            "INSERT INTO program_translations (program_id, locale, name, description, source, source_url, license)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (program_id, locale, _tag(prefix, program["names"][locale], " "),
             (program.get("description") or {}).get(locale),
             SOURCES["es"] if locale == "es" else "gc-adventurer-directors-manual",
             program["source_url"][locale], license_))
    for position, section in enumerate(program["sections"], start=1):
        cur.execute(
            "INSERT INTO program_sections (id, program_id, position, slug, name)"
            " VALUES (md5(%s::text || ':section:' || %s)::uuid, %s, %s, %s, %s) RETURNING id",
            (str(program_id), section["slug"], program_id, position, section["slug"], section["names"]["es"]))
        section_id = cur.fetchone()[0]
        for locale in LOCALES:
            cur.execute("INSERT INTO program_section_translations (section_id, locale, name) VALUES (%s, %s, %s)",
                        (section_id, locale, section["names"][locale]))
        for requirement in section["requirements"]:
            kind = requirement["kind"]
            honor = category = None
            if requirement.get("target_honor_slug"):
                honor = honors[_tag(prefix, requirement["target_honor_slug"])][0]
            if requirement.get("target_category_slug"):
                category = categories[_tag(prefix, requirement["target_category_slug"])]
            db_kind = REQUIREMENT_KINDS[kind]
            # §12.7: an HONOR requirement always asks for evidence.
            evidence = db_kind == "HONOR" or bool(requirement.get("evidence_required"))
            cur.execute(
                "INSERT INTO program_requirements (id, program_id, section_id, position, label, kind,"
                " evidence_required, target_honor_id, target_category_id)"
                " VALUES (md5(%s::text || ':requirement:' || %s)::uuid, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (str(program_id), requirement["position"], program_id, section_id, requirement["position"],
                 requirement["label"], db_kind, evidence, honor, category))
            requirement_id = cur.fetchone()[0]
            report["requirements_written"] += 1
            for locale in LOCALES:
                cur.execute(
                    "INSERT INTO program_requirement_texts (requirement_id, locale, description, source,"
                    " source_url, license) VALUES (%s, %s, %s, %s, %s, %s)",
                    (requirement_id, locale, description(requirement, locale), SOURCES[locale],
                     requirement["source_url"][locale], license_))
                report["texts_written"] += 1


def _publish(cur, program_id, slug: str, honors: dict, operator: str) -> int:
    cur.execute("SELECT status FROM programs WHERE id = %s", (program_id,))
    if cur.fetchone()[0] != "DRAFT":
        return 0
    cur.execute(
        "SELECT r.label, h.slug, h.status FROM program_requirements r JOIN honors h ON h.id = r.target_honor_id"
        " WHERE r.program_id = %s AND h.status <> 'PUBLISHED' ORDER BY r.position", (program_id,))
    pending = cur.fetchall()
    if pending:
        raise ImportError_(
            f"{slug}: no se puede publicar, hay awards de destino sin publicar: "
            + ", ".join(f"{label} → {honor} ({status})" for label, honor, status in pending)
            + " (publica antes los awards: import_adventurer_awards.py --commit --publish)")
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
            "SELECT p.slug, p.status, p.version, p.image_url IS NOT NULL,"
            " (SELECT count(*) FROM program_sections s WHERE s.program_id = p.id),"
            " (SELECT count(*) FROM program_requirements r WHERE r.program_id = p.id),"
            " (SELECT count(*) FROM program_requirements r WHERE r.program_id = p.id AND r.kind = 'HONOR'),"
            " (SELECT count(*) FROM program_requirement_texts t JOIN program_requirements r ON r.id = t.requirement_id"
            "   WHERE r.program_id = p.id)"
            " FROM programs p JOIN ministries m ON m.id = p.ministry_id"
            " WHERE m.slug = %s AND p.kind = 'CLASS' AND p.slug LIKE %s ORDER BY p.sort_order, p.slug",
            (MINISTRY, like))
        return cur.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="por defecto: todo en una transacción y ROLLBACK")
    mode.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL")
    parser.add_argument("--publish", action="store_true",
                        help="publica las clases (exige los awards de destino ya publicados)")
    parser.add_argument("--image-base-url", help="URL pública de la carpeta con los emblemas <slug>.webp")
    parser.add_argument("--operator", default=os.environ.get("USER"), help="quién importa (audit_log)")
    args = parser.parse_args()
    try:
        data = load(pathlib.Path(args.data).expanduser())
    except ImportError_ as exc:
        sys.exit(f"ERROR: {exc}")
    for row in counts(data):
        print(f"{row['slug']}: {row['sections']} secciones, {row['requirements']} requisitos {row['by_kind']},"
              f" es mundoja {row['es_mundoja']}, en no oficial {row['en_unofficial']}")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Sin DATABASE_URL: solo se validaron los datos.")
        return
    with connect(url) as conn:
        try:
            report = run(conn, data, publish=args.publish, operator=args.operator,
                         image_base_url=args.image_base_url)
            after = db_counts(conn)
        except ImportError_ as exc:
            conn.rollback()
            sys.exit(f"ERROR: {exc}")
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("slug | estado | versión | emblema | secciones | requisitos | HONOR | textos")
    for row in after:
        print(" | ".join(str(v) for v in row))
    if not args.commit:
        print("SIMULACRO: nada se ha escrito (ROLLBACK). Repite con --commit.")
    elif not args.publish:
        print("Las clases entran como BORRADOR: publícalas con --publish cuando el propietario lo decida.")


if __name__ == "__main__":
    main()
