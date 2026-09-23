"""Load the Pathfinder classes and the Master Guide curriculum (block F) from
`data/ay_classes.json` into the program catalogue of 012_programs.sql.

    python import_ay_classes.py [--data data/ay_classes.json] [--sql-out FILE] [--dry-run]
    DATABASE_URL=... python import_ay_classes.py --commit [--operator you@example.com]

The JSON is built from the Pathfinder Wiki pages by `catalog_tools/build_ay_classes.py`
(pages downloaded beforehand by `catalog_tools/crawl_wiki_classes.py`; this script never
touches the network). Text: Pathfinder Wiki, CC BY-SA 3.0 — every program, translation and
requirement text row keeps `source`, `source_url` and `license`.

One code path: the script turns the JSON into ONE SQL transaction.
  * `--dry-run` (the default) validates the JSON, prints the counts and writes that SQL to
    `--sql-out` (default ~/adventist-wiki/classes/import.sql) so it can be applied with
    `psql -v ON_ERROR_STOP=1 -f import.sql`. It needs no database. With DATABASE_URL set it
    also checks, READ-ONLY, that every ministry / category / honour the JSON names exists.
  * `--commit` executes exactly that SQL against DATABASE_URL.

Rules (same as import_programs.py, spec §7 and D2):
  * everything enters as DRAFT; the owner compares it with the manual in force and publishes
    it with `POST /api/v1/programs/{id}/publish`;
  * idempotent by `programs.slug` within its ministry: the latest version of the slug is
    rewritten in place while it is a DRAFT (same program id; sections, requirements and texts
    are recreated with ids derived from the program id, so a rerun leaves identical rows);
  * a PUBLISHED or ARCHIVED program is NEVER touched: the SQL skips it with a NOTICE (use
    import_programs.py, which creates the next version as a draft, if the content changed);
  * a language is loaded for a program only when it has a text for EVERY requirement;
  * a target that does not exist in the catalogue makes the whole transaction fail loudly.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_DATA = HERE / "data" / "ay_classes.json"
DEFAULT_SQL = pathlib.Path.home() / "adventist-wiki" / "classes" / "import.sql"

PROGRAM_KINDS = {"CLASS": "CLASS", "MASTER_GUIDE": "CURRICULUM"}
# JSON kind -> program_requirements.kind. HONOR_ANY is «any one honour» (012: an HONOR
# requirement with both targets NULL).
REQUIREMENT_KINDS = {"TEXT": "FREE", "HONOR": "HONOR", "HONOR_FROM_CATEGORY": "HONOR",
                     "HONOR_ANY": "HONOR", "PROGRAM": "PROGRAM"}
# Honours and their categories live in the Pathfinder catalogue, also for the Master Guide.
HONOR_MINISTRY = "pathfinders"


class ImportError_(Exception):
    """Loud failure: nothing is written when the data does not make sense."""


# ----------------------------------------------------------------------------
# Reading and checking the JSON (no database)
# ----------------------------------------------------------------------------
def load(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict) -> None:
    seen: set[tuple[str, str]] = set()
    for program in data.get("programs") or []:
        where = program.get("slug") or "?"
        for field in ("kind", "ministry", "slug", "names", "sections"):
            if not program.get(field):
                raise ImportError_(f"{where}: falta el campo obligatorio '{field}'")
        if program["kind"] not in PROGRAM_KINDS:
            raise ImportError_(f"{where}: kind debe ser uno de {sorted(PROGRAM_KINDS)}")
        if (program["ministry"], program["slug"]) in seen:
            raise ImportError_(f"{where}: slug repetido en el ministerio {program['ministry']}")
        seen.add((program["ministry"], program["slug"]))
        if not (program["names"].get("es") or program["names"].get("en")):
            raise ImportError_(f"{where}: el programa necesita nombre en es o en")
        positions, section_slugs = [], set()
        for section in program["sections"]:
            if not section.get("slug") or not section.get("names"):
                raise ImportError_(f"{where}: cada sección necesita slug y names")
            if section["slug"] in section_slugs:
                raise ImportError_(f"{where}: sección repetida {section['slug']}")
            section_slugs.add(section["slug"])
            if not section.get("requirements"):
                raise ImportError_(f"{where}/{section['slug']}: sección sin requisitos")
            for requirement in section["requirements"]:
                positions.append(requirement.get("position"))
                _check_requirement(where, requirement)
        if not positions:
            raise ImportError_(f"{where}: el programa no tiene ningún requisito")
        if positions != list(range(1, len(positions) + 1)):
            raise ImportError_(f"{where}: las posiciones deben ser 1..N sin huecos, en orden")
        if not complete_locales(program):
            raise ImportError_(f"{where}: ningún idioma cubre todos los requisitos")
    # A PROGRAM target must come BEFORE the program that asks for it (the SQL looks it up),
    # or already be in the catalogue; the SQL fails loudly otherwise.
    order = {(p["ministry"], p["slug"]): n for n, p in enumerate(data.get("programs") or [])}
    for n, program in enumerate(data.get("programs") or []):
        for requirement in _requirements(program):
            target = order.get((program["ministry"], requirement.get("target_program_slug")))
            if target is not None and target >= n:
                raise ImportError_(f"{program['slug']}: {requirement['target_program_slug']}"
                                   " debe ir antes en el archivo")


def _check_requirement(where: str, requirement: dict) -> None:
    kind = requirement.get("kind", "TEXT")
    position = requirement.get("position")
    if kind not in REQUIREMENT_KINDS:
        raise ImportError_(f"{where}: kind '{kind}' no existe (requisito {position})")
    if not requirement.get("label") or len(requirement["label"]) > 12:
        raise ImportError_(f"{where}: el requisito {position} necesita label de 1 a 12 caracteres")
    honor, category = requirement.get("target_honor_slug"), requirement.get("target_category_slug")
    program = requirement.get("target_program_slug")
    if kind == "HONOR" and (not honor or category):
        raise ImportError_(f"{where}: el requisito {position} (HONOR) necesita target_honor_slug y nada más")
    if kind == "HONOR_FROM_CATEGORY" and (not category or honor):
        raise ImportError_(f"{where}: el requisito {position} (HONOR_FROM_CATEGORY) necesita target_category_slug")
    if kind == "PROGRAM" and not program:
        raise ImportError_(f"{where}: el requisito {position} (PROGRAM) necesita target_program_slug")
    if kind in ("TEXT", "HONOR_ANY") and (honor or category or program):
        raise ImportError_(f"{where}: el requisito {position} ({kind}) no lleva destino")
    if kind != "PROGRAM" and program:
        raise ImportError_(f"{where}: el requisito {position} ({kind}) no puede apuntar a un programa")
    if not (requirement.get("text") or {}):
        raise ImportError_(f"{where}: el requisito {position} no tiene texto")


def _requirements(program: dict):
    for section in program["sections"]:
        yield from section["requirements"]


def description(requirement: dict, locale: str) -> str | None:
    """The stored text: the requirement and its sub-items, one per line (like honor_requirements)."""
    text = (requirement.get("text") or {}).get(locale)
    if not text:
        return None
    items = (requirement.get("sub_items") or {}).get(locale) or []
    return "\n".join([text, *items])


def complete_locales(program: dict) -> list[str]:
    """Languages with a text for every requirement (and a name): only these are loaded."""
    locales = set(program["names"])
    for requirement in _requirements(program):
        locales &= {loc for loc, text in (requirement.get("text") or {}).items() if text}
    return sorted(locales)


def counts(data: dict) -> list[dict]:
    rows = []
    for program in data["programs"]:
        reqs = list(_requirements(program))
        loaded = complete_locales(program)
        rows.append({
            "slug": program["slug"],
            "ministry": program["ministry"],
            "kind": PROGRAM_KINDS[program["kind"]],
            "sections": len(program["sections"]),
            "requirements": len(reqs),
            "by_kind": {k: sum(1 for r in reqs if r.get("kind", "TEXT") == k)
                        for k in REQUIREMENT_KINDS if any(r.get("kind", "TEXT") == k for r in reqs)},
            "locales_loaded": loaded,
            "locales_skipped": {
                loc: sum(1 for r in reqs if not (r.get("text") or {}).get(loc))
                for loc in sorted({loc for r in reqs for loc in (r.get("text") or {})} - set(loaded))
            },
        })
    return rows


# ----------------------------------------------------------------------------
# SQL
# ----------------------------------------------------------------------------
def q(value) -> str:
    """SQL literal (standard_conforming_strings: only the quote needs doubling)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _slug_regex(slug: str) -> str:
    return "^" + "".join("\\" + c if not c.isalnum() else c for c in slug) + "-v[0-9]+$"


def _targets(data: dict) -> list[tuple[str, str]]:
    found = set()
    for program in data["programs"]:
        found.add(("ministry", program["ministry"]))
        for requirement in _requirements(program):
            if requirement.get("target_category_slug"):
                found.add(("category", requirement["target_category_slug"]))
            if requirement.get("target_honor_slug"):
                found.add(("honor", requirement["target_honor_slug"]))
    found.add(("ministry", HONOR_MINISTRY))
    return sorted(found)


def build_sql(data: dict, operator: str | None = None) -> str:
    source = data.get("source") or "pathfinder-wiki"
    license_ = data.get("license") or "CC BY-SA 3.0"
    operator = operator or "import_ay_classes"
    out = [
        "-- Generated by backend/migrations/import_ay_classes.py from data/ay_classes.json.",
        "-- Pathfinder classes + Master Guide (block F) as DRAFT programs. Text: Pathfinder Wiki,",
        f"-- {license_} (source/source_url/license on every row). ONE transaction; rerunnable.",
        "-- Apply: psql \"$DATABASE_URL\" -v ON_ERROR_STOP=1 -f import.sql",
        "\\set ON_ERROR_STOP on",
        "BEGIN;",
        "",
        "-- 0. Every ministry, honour category and honour named by the data must exist.",
        "DO $$",
        "DECLARE missing text;",
        "BEGIN",
        "  SELECT string_agg(v.what || ' ' || v.slug, ', ' ORDER BY v.what, v.slug) INTO missing",
        "    FROM (VALUES",
        ",\n".join(f"      ({q(what)}, {q(slug)})" for what, slug in _targets(data)),
        "    ) AS v(what, slug)",
        "   WHERE (v.what = 'ministry' AND NOT EXISTS (SELECT 1 FROM ministries m WHERE m.slug = v.slug))",
        "      OR (v.what = 'category' AND NOT EXISTS (",
        "            SELECT 1 FROM honor_categories c JOIN ministries m ON m.id = c.ministry_id",
        f"             WHERE m.slug = {q(HONOR_MINISTRY)} AND c.slug = v.slug))",
        "      OR (v.what = 'honor' AND NOT EXISTS (",
        "            SELECT 1 FROM honors h JOIN ministries m ON m.id = h.ministry_id",
        f"             WHERE m.slug = {q(HONOR_MINISTRY)} AND h.slug = v.slug));",
        "  IF missing IS NOT NULL THEN",
        "    RAISE EXCEPTION 'import_ay_classes: no existen en el catálogo: %', missing;",
        "  END IF;",
        "END",
        "$$;",
        "",
    ]
    for program in data["programs"]:
        out.append(_program_sql(program, source, license_, operator))
    slugs = ", ".join(q(p["slug"]) for p in data["programs"])
    out += [
        "-- Final counts (latest version of each slug).",
        "SELECT m.slug AS ministry, p.slug, p.kind, p.status, p.version,",
        "       (SELECT count(*) FROM program_sections s WHERE s.program_id = p.id) AS sections,",
        "       (SELECT count(*) FROM program_requirements r WHERE r.program_id = p.id) AS requirements,",
        "       (SELECT count(*) FROM program_requirements r WHERE r.program_id = p.id AND r.kind = 'HONOR') AS honor_reqs,",
        "       (SELECT string_agg(t.locale || ':' || t.n, ' ' ORDER BY t.locale) FROM (",
        "          SELECT x.locale, count(*) AS n FROM program_requirement_texts x",
        "            JOIN program_requirements r ON r.id = x.requirement_id",
        "           WHERE r.program_id = p.id GROUP BY x.locale) t) AS texts",
        "  FROM programs p JOIN ministries m ON m.id = p.ministry_id",
        f" WHERE p.slug IN ({slugs})",
        " ORDER BY m.slug, p.sort_order, p.slug;",
        "",
        "COMMIT;",
        "",
    ]
    return "\n".join(out)


def _program_sql(program: dict, source: str, license_: str, operator: str) -> str:
    slug = program["slug"]
    locales = complete_locales(program)
    names, descriptions = program["names"], program.get("description") or {}
    urls = program.get("source_url") or {}
    base_name = names.get("es") or names.get("en")
    base_description = descriptions.get("es") or descriptions.get("en")
    base_url = urls.get("en") or urls.get("es")
    kind = PROGRAM_KINDS[program["kind"]]
    common = {
        "kind": q(kind), "code": q(program.get("code")), "name": q(base_name),
        "description": q(base_description), "sort_order": q(int(program.get("sort_order") or 0)),
        "authority": q(program.get("authority")),
        "issuer_level": q(program.get("issuer_level") or ("ASSOCIATION" if kind == "CURRICULUM" else "CLUB")),
        "source": q(source), "source_url": q(base_url), "license": q(license_),
    }
    lines = [
        f"-- {program['ministry']}/{slug}: {len(program['sections'])} secciones,"
        f" {sum(1 for _ in _requirements(program))} requisitos, idiomas {locales}",
        "DO $$",
        "DECLARE",
        "  v_ministry uuid; v_honors uuid; v_program uuid; v_status text; v_action text;",
        "  v_section uuid; v_requirement uuid; v_target uuid;",
        "BEGIN",
        f"  SELECT id INTO v_ministry FROM ministries WHERE slug = {q(program['ministry'])};",
        f"  SELECT id INTO v_honors FROM ministries WHERE slug = {q(HONOR_MINISTRY)};",
        "  SELECT id, status INTO v_program, v_status FROM programs",
        f"   WHERE ministry_id = v_ministry AND (slug = {q(slug)} OR slug ~ {q(_slug_regex(slug))})",
        "   ORDER BY version DESC LIMIT 1;",
        "  IF v_program IS NOT NULL AND v_status <> 'DRAFT' THEN",
        "    RAISE NOTICE '%: la versión vigente está % y no se toca (import_programs.py crea la siguiente versión)',",
        f"      {q(slug)}, v_status;",
        "    RETURN;",
        "  END IF;",
        "  IF v_program IS NULL THEN",
        "    v_program := gen_random_uuid(); v_action := 'created';",
        "    INSERT INTO programs (id, ministry_id, kind, slug, code, name, description, sort_order,",
        "        authority, status, issuer_level, version, source, source_url, license)",
        f"      VALUES (v_program, v_ministry, {common['kind']}, {q(slug)}, {common['code']},",
        f"        {common['name']}, {common['description']}, {common['sort_order']},",
        f"        {common['authority']}, 'DRAFT', {common['issuer_level']}, 1, {common['source']},",
        f"        {common['source_url']}, {common['license']});",
        "  ELSE",
        "    v_action := 'updated_draft';",
        "    -- a DRAFT was never enrolled in: its content is recreated (sections cascade).",
        "    DELETE FROM program_sections WHERE program_id = v_program;",
        "    DELETE FROM program_translations WHERE program_id = v_program;",
        f"    UPDATE programs SET kind = {common['kind']}, code = {common['code']}, name = {common['name']},",
        f"        description = {common['description']}, sort_order = {common['sort_order']},",
        f"        authority = {common['authority']}, issuer_level = {common['issuer_level']},",
        f"        source = {common['source']}, source_url = {common['source_url']},",
        f"        license = {common['license']}, updated_at = now()",
        "      WHERE id = v_program;",
        "  END IF;",
    ]
    for locale in locales:
        lines += [
            "  INSERT INTO program_translations (program_id, locale, name, description, source, source_url, license)",
            f"    VALUES (v_program, {q(locale)}, {q(names[locale])}, {q(descriptions.get(locale))},",
            f"      {q(source)}, {q(urls.get(locale) or base_url)}, {q(license_)});",
        ]
    for section_position, section in enumerate(program["sections"], start=1):
        section_names = section["names"]
        lines += [
            f"  v_section := md5(v_program::text || ':section:' || {q(section['slug'])})::uuid;",
            "  INSERT INTO program_sections (id, program_id, position, slug, name)",
            f"    VALUES (v_section, v_program, {section_position}, {q(section['slug'])},"
            f" {q(section_names.get('es') or section_names.get('en'))});",
        ]
        for locale in locales:
            if section_names.get(locale):
                lines.append(
                    "  INSERT INTO program_section_translations (section_id, locale, name)"
                    f" VALUES (v_section, {q(locale)}, {q(section_names[locale])});")
        for requirement in section["requirements"]:
            lines += _requirement_sql(program, requirement, locales, source, license_, base_url)
    lines += [
        "  INSERT INTO audit_log (action, entity_type, entity_id, user_email, details)",
        f"    VALUES ('PROGRAM_IMPORT', 'PROGRAM', v_program::text, {q(operator)},",
        f"      v_action || {q(': ' + program['ministry'] + '/' + slug + ', idiomas ' + ','.join(locales) + ' (import_ay_classes)')});",
        f"  RAISE NOTICE '%: %', {q(slug)}, v_action;",
        "END",
        "$$;",
        "",
    ]
    return "\n".join(lines)


def _requirement_sql(program, requirement, locales, source, license_, base_url) -> list[str]:
    kind = requirement.get("kind", "TEXT")
    db_kind = REQUIREMENT_KINDS[kind]
    # §12.7: an HONOR/PROGRAM requirement always asks for evidence (the manual path).
    evidence = True if db_kind in ("HONOR", "PROGRAM") else bool(requirement.get("evidence_required"))
    honor = category = target_program = "NULL"
    lines = []
    if requirement.get("target_honor_slug"):
        honor = ("(SELECT h.id FROM honors h WHERE h.ministry_id = v_honors AND h.slug = "
                 f"{q(requirement['target_honor_slug'])} ORDER BY h.version DESC LIMIT 1)")
    if requirement.get("target_category_slug"):
        category = ("(SELECT c.id FROM honor_categories c WHERE c.ministry_id = v_honors AND c.slug = "
                    f"{q(requirement['target_category_slug'])} LIMIT 1)")
    if requirement.get("target_program_slug"):
        target = requirement["target_program_slug"]
        lines += [
            "  SELECT id INTO v_target FROM programs",
            f"   WHERE ministry_id = v_ministry AND slug = {q(target)};",
            "  IF v_target IS NULL THEN",
            "    RAISE EXCEPTION 'import_ay_classes: % requisito %: el programa % no existe en el catálogo',",
            f"      {q(program['slug'])}, {q(requirement['label'])}, {q(target)};",
            "  END IF;",
        ]
        target_program = "v_target"
    lines += [
        f"  v_requirement := md5(v_program::text || ':requirement:' || {requirement['position']})::uuid;",
        "  INSERT INTO program_requirements (id, program_id, section_id, position, label, kind,",
        "      evidence_required, target_honor_id, target_category_id, target_program_id)",
        f"    VALUES (v_requirement, v_program, v_section, {requirement['position']}, {q(requirement['label'])},",
        f"      {q(db_kind)}, {q(evidence)}, {honor}, {category}, {target_program});",
    ]
    urls = requirement.get("source_url") or {}
    for locale in locales:
        lines += [
            "  INSERT INTO program_requirement_texts (requirement_id, locale, description, source, source_url, license)",
            f"    VALUES (v_requirement, {q(locale)}, {q(description(requirement, locale))},",
            f"      {q(source)}, {q(urls.get(locale) or base_url)}, {q(license_)});",
        ]
    return lines


# ----------------------------------------------------------------------------
# Database (read-only check for --dry-run, the transaction for --commit)
# ----------------------------------------------------------------------------
def _connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))


def check_targets(data: dict, url: str) -> list[str]:
    """READ-ONLY: which ministries / categories / honours named by the JSON do not exist."""
    missing = []
    with _connect(url) as conn, conn.cursor() as cur:
        for what, slug in _targets(data):
            if what == "ministry":
                cur.execute("SELECT 1 FROM ministries WHERE slug = %s", (slug,))
            elif what == "category":
                cur.execute("SELECT 1 FROM honor_categories c JOIN ministries m ON m.id = c.ministry_id"
                            " WHERE m.slug = %s AND c.slug = %s", (HONOR_MINISTRY, slug))
            else:
                cur.execute("SELECT 1 FROM honors h JOIN ministries m ON m.id = h.ministry_id"
                            " WHERE m.slug = %s AND h.slug = %s", (HONOR_MINISTRY, slug))
            if cur.fetchone() is None:
                missing.append(f"{what} {slug}")
        conn.rollback()
    return missing


def execute(sql: str, url: str) -> tuple[list[str], list[tuple]]:
    """Run the generated script (it carries its own BEGIN/COMMIT). Returns notices and counts."""
    notices: list[str] = []
    body = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
    with _connect(url) as conn:
        conn.autocommit = True
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary))
        try:
            conn.execute(body)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        final = body[body.index("-- Final counts"):body.rindex("COMMIT;")]
        rows = conn.execute(final).fetchall()
    return notices, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--sql-out", default=str(DEFAULT_SQL))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: counts + SQL file, no writes")
    mode.add_argument("--commit", action="store_true", help="execute the SQL against DATABASE_URL")
    parser.add_argument("--operator", default=os.environ.get("USER"),
                        help="who imports, for the PROGRAM_IMPORT audit rows")
    args = parser.parse_args()
    try:
        data = load(pathlib.Path(args.data).expanduser())
    except ImportError_ as exc:
        sys.exit(f"ERROR: {exc}")
    sql = build_sql(data, args.operator)
    out = pathlib.Path(args.sql_out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8")

    for row in counts(data):
        skipped = f" (omitidos: {row['locales_skipped']})" if row["locales_skipped"] else ""
        print(f"{row['ministry']}/{row['slug']} [{row['kind']}]: {row['sections']} secciones,"
              f" {row['requirements']} requisitos {row['by_kind']}, idiomas {row['locales_loaded']}{skipped}")
    print(f"SQL: {out} ({len(sql.splitlines())} líneas)")

    url = os.environ.get("DATABASE_URL")
    if not args.commit:
        if url:
            missing = check_targets(data, url)
            print("Destinos: todos existen." if not missing else f"FALTAN en el catálogo: {missing}")
        print("SIMULACRO: nada se ha escrito. Aplica el SQL con psql o repite con --commit.")
        return
    if not url:
        sys.exit("Set DATABASE_URL")
    notices, rows = execute(sql, url)
    for notice in notices:
        print("NOTICE", notice)
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print("Todo entra como BORRADOR: publícalo sólo tras cotejarlo con el manual vigente.")


if __name__ == "__main__":
    main()
