"""Load a program (a class, Guía Mayor, EMC, CMJA) into the catalogue from JSON.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_programs.py migrations/data/programs [--commit]

It reads files that are ALREADY on disk. It never crawls: pages of the official wiki are
downloaded beforehand with `catalog_tools/` under the rules of docs/ESPECIALIDADES_WIKI.md
(one request every 10 s, never /w/api.php, identifiable User-Agent) and turned into the JSON
below by hand, because the class pages do not have the regular shape the honor pages have.

Layout (versioned in the repo):
    <folder>/<ministry>/<slug>.json            structure  (kind, targets, order)
    <folder>/<ministry>/<slug>.<locale>.json   text       (one file per language)

Structure file:
    {"ministry": "pathfinders", "kind": "CLASS", "slug": "amigo", "name": "Amigo",
     "code": "AMIGO", "sort_order": 1, "authority": "IAD", "issuer_level": "CLUB",
     "source": "pathfinder-wiki", "source_url": "...", "license": "CC BY-SA 3.0",
     "sections": [{"slug": "general", "name": "General", "requirements": [
         {"label": "1", "kind": "FREE", "evidence_required": false},
         {"label": "2", "kind": "HONOR", "target_honor_slug": "nudos"},
         {"label": "3", "kind": "HONOR", "target_category_slug": "naturaleza"},
         {"label": "4", "kind": "PROGRAM", "target_program_slug": "amigo"},
         {"label": "5", "kind": "HOURS", "target_quantity": 10,
          "activity_category": "SERVICE"}]}]}

Text file:
    {"name": "Amigo", "description": "...", "sections": {"general": "General"},
     "requirements": {"1": {"description": "...", "instructions": "..."}},
     "source": "pathfinder-wiki", "source_url": "...", "license": "CC BY-SA 3.0"}

Rules (spec §7 and decision D2):
  * the parser does NOT guess types: `kind` and the targets are annotated by a person and
    this script FAILS LOUDLY when a target slug does not exist in the catalogue;
  * everything enters as DRAFT and is published only once somebody has compared it with the
    manual in force (`POST /api/v1/programs/{id}/publish`, MASTER_GC);
  * a PUBLISHED program is NEVER modified: a change creates the next version as a draft;
  * a language is loaded only when it has a text for every requirement of the structure;
  * idempotent, and a DRY RUN unless `--commit` is passed.
"""
import argparse
import json
import os
import pathlib
import sys
import uuid

import psycopg

KINDS = ("FREE", "HONOR", "PROGRAM", "HOURS")
PROGRAM_KINDS = ("CLASS", "CURRICULUM")
ISSUER_LEVELS = ("CLUB", "ASSOCIATION")
ACTIVITY_CATEGORIES = ("SERVICE", "ATTENDANCE")


class ImportError_(Exception):
    """Loud failure: nothing is written when the data does not make sense."""


# ----------------------------------------------------------------------------
# Reading and checking the files (no database)
# ----------------------------------------------------------------------------
def read_structure(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for field in ("ministry", "kind", "slug", "name", "sections"):
        if not data.get(field):
            raise ImportError_(f"{path.name}: falta el campo obligatorio '{field}'")
    if data["kind"] not in PROGRAM_KINDS:
        raise ImportError_(f"{path.name}: kind debe ser uno de {PROGRAM_KINDS}")
    if data.get("issuer_level", "CLUB") not in ISSUER_LEVELS:
        raise ImportError_(f"{path.name}: issuer_level debe ser uno de {ISSUER_LEVELS}")
    position = 0
    for section in data["sections"]:
        if not section.get("slug") or not section.get("name"):
            raise ImportError_(f"{path.name}: cada sección necesita slug y name")
        for requirement in section.get("requirements") or []:
            position += 1
            requirement["position"] = position
            kind = requirement.setdefault("kind", "FREE")
            if kind not in KINDS:
                raise ImportError_(f"{path.name}: kind '{kind}' no existe (requisito {position})")
            if not requirement.get("label"):
                requirement["label"] = str(position)
            if kind == "HOURS":
                if not requirement.get("target_quantity"):
                    raise ImportError_(f"{path.name}: el requisito {position} (HOURS) necesita target_quantity")
                if requirement.get("activity_category") not in ACTIVITY_CATEGORIES:
                    raise ImportError_(
                        f"{path.name}: el requisito {position} (HOURS) necesita activity_category"
                        f" en {ACTIVITY_CATEGORIES}")
            if kind == "PROGRAM" and not requirement.get("target_program_slug"):
                raise ImportError_(f"{path.name}: el requisito {position} (PROGRAM) necesita target_program_slug")
            if kind == "HONOR" and requirement.get("target_honor_slug") and requirement.get("target_category_slug"):
                raise ImportError_(
                    f"{path.name}: el requisito {position} no puede apuntar a una especialidad Y a una categoría")
    if position == 0:
        raise ImportError_(f"{path.name}: el programa no tiene ningún requisito")
    return data


def read_texts(folder: pathlib.Path, slug: str, structure: dict) -> dict[str, dict]:
    """`{locale: text}` for the languages that cover EVERY requirement of the structure."""
    labels = [r["label"] for s in structure["sections"] for r in (s.get("requirements") or [])]
    texts, skipped = {}, []
    for path in sorted(folder.glob(f"{slug}.*.json")):
        locale = path.name[len(slug) + 1:-len(".json")]
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [label for label in labels if not (data.get("requirements") or {}).get(label)]
        if missing:
            skipped.append((locale, len(missing)))
            continue
        texts[locale] = data
    if not texts:
        raise ImportError_(
            f"{slug}: ningún idioma cubre los {len(labels)} requisitos"
            + (f" (incompletos: {skipped})" if skipped else ""))
    return texts, skipped


def fingerprint(structure: dict, texts: dict[str, dict]) -> str:
    """What makes a program a different VERSION: its structure and its texts."""
    shape = {
        "kind": structure["kind"],
        "sections": [
            {
                "slug": section["slug"],
                "requirements": [
                    {
                        "label": r["label"],
                        "kind": r["kind"],
                        "evidence_required": bool(r.get("evidence_required")),
                        "honor": r.get("target_honor_slug"),
                        "category": r.get("target_category_slug"),
                        "program": r.get("target_program_slug"),
                        "quantity": r.get("target_quantity"),
                        "activity": r.get("activity_category"),
                    }
                    for r in (section.get("requirements") or [])
                ],
            }
            for section in structure["sections"]
        ],
        "texts": {
            locale: {
                "name": body.get("name"),
                "requirements": {
                    label: _text_of(body, label) for label in (body.get("requirements") or {})
                },
            }
            for locale, body in sorted(texts.items())
        },
    }
    return json.dumps(shape, sort_keys=True, ensure_ascii=False)


def _text_of(body: dict, label: str) -> dict:
    entry = (body.get("requirements") or {}).get(label)
    if isinstance(entry, str):
        return {"description": entry, "instructions": None}
    return {"description": (entry or {}).get("description"),
            "instructions": (entry or {}).get("instructions")}


# ----------------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------------
def _resolve(cur, table: str, ministry_id, slug: str, what: str):
    cur.execute(
        f"SELECT id FROM {table} WHERE ministry_id = %s AND slug = %s ORDER BY slug LIMIT 1",
        (ministry_id, slug),
    )
    row = cur.fetchone()
    if row is None:
        raise ImportError_(f"{what} '{slug}' no existe en el catálogo: corrige el JSON o impórtalo antes")
    return row[0]


def _current_version(cur, ministry_id, base_slug: str):
    cur.execute(
        "SELECT id, status, version, slug FROM programs"
        " WHERE ministry_id = %s AND (slug = %s OR slug LIKE %s)"
        " ORDER BY version DESC LIMIT 1",
        (ministry_id, base_slug, f"{base_slug}-v%"),
    )
    return cur.fetchone()


def _write_program(cur, program_id, structure, texts, ministry_id, *, slug, version, previous_id):
    # `code` is unique per ministry, so a new version carries the same suffix as its slug.
    code = structure.get("code")
    if code and version > 1:
        code = f"{code}-v{version}"[:40]
    structure = {**structure, "code": code}
    cur.execute(
        "INSERT INTO programs (id, ministry_id, kind, slug, code, name, description, image_url,"
        " sort_order, authority, status, issuer_level, version, previous_version_id, source,"
        " source_url, license) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'DRAFT', %s, %s,"
        " %s, %s, %s, %s)",
        (program_id, ministry_id, structure["kind"], slug, structure.get("code"),
         structure["name"], structure.get("description"), structure.get("image_url"),
         structure.get("sort_order", 0), structure.get("authority"),
         structure.get("issuer_level", "CLUB"), version, previous_id, structure.get("source"),
         structure.get("source_url"), structure.get("license")),
    )
    for locale, body in texts.items():
        if not body.get("name"):
            continue
        cur.execute(
            "INSERT INTO program_translations (program_id, locale, name, description, source,"
            " source_url, license) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (program_id, locale) DO UPDATE SET name = EXCLUDED.name,"
            " description = EXCLUDED.description, updated_at = now()",
            (program_id, locale, body["name"], body.get("description"), body.get("source"),
             body.get("source_url"), body.get("license")),
        )
    _write_content(cur, program_id, structure, texts, ministry_id)


def _write_content(cur, program_id, structure, texts, ministry_id):
    """Sections, requirements and texts. Called on a program that has none (a fresh draft)."""
    for section_position, section in enumerate(structure["sections"], start=1):
        section_id = uuid.uuid4()
        cur.execute(
            "INSERT INTO program_sections (id, program_id, position, slug, name)"
            " VALUES (%s, %s, %s, %s, %s)",
            (section_id, program_id, section_position, section["slug"], section["name"]),
        )
        for locale, body in texts.items():
            name = (body.get("sections") or {}).get(section["slug"])
            if name:
                cur.execute(
                    "INSERT INTO program_section_translations (section_id, locale, name)"
                    " VALUES (%s, %s, %s) ON CONFLICT (section_id, locale) DO UPDATE"
                    " SET name = EXCLUDED.name, updated_at = now()",
                    (section_id, locale, name),
                )
        for requirement in section.get("requirements") or []:
            requirement_id = uuid.uuid4()
            honor_id = category_id = program_target = None
            if requirement.get("target_honor_slug"):
                honor_id = _resolve(cur, "honors", ministry_id, requirement["target_honor_slug"],
                                    "La especialidad")
            if requirement.get("target_category_slug"):
                category_id = _resolve(cur, "honor_categories", ministry_id,
                                       requirement["target_category_slug"], "La categoría")
            if requirement.get("target_program_slug"):
                program_target = _resolve(cur, "programs", ministry_id,
                                          requirement["target_program_slug"], "El programa")
            cur.execute(
                "INSERT INTO program_requirements (id, program_id, section_id, position, label,"
                " kind, evidence_required, target_honor_id, target_category_id, target_program_id,"
                " target_quantity, activity_category)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (requirement_id, program_id, section_id, requirement["position"],
                 requirement["label"], requirement["kind"],
                 bool(requirement.get("evidence_required")), honor_id, category_id, program_target,
                 requirement.get("target_quantity"), requirement.get("activity_category")),
            )
            for locale, body in texts.items():
                content = _text_of(body, requirement["label"])
                cur.execute(
                    "INSERT INTO program_requirement_texts (requirement_id, locale, description,"
                    " instructions, source, source_url, license)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (requirement_id, locale) DO UPDATE SET"
                    " description = EXCLUDED.description, instructions = EXCLUDED.instructions,"
                    " updated_at = now()",
                    (requirement_id, locale, content["description"], content["instructions"],
                     body.get("source") or structure.get("source"),
                     body.get("source_url") or structure.get("source_url"),
                     body.get("license") or structure.get("license")),
                )


def import_one(cur, structure: dict, texts: dict, *, operator: str | None) -> dict:
    cur.execute("SELECT id FROM ministries WHERE slug = %s", (structure["ministry"],))
    row = cur.fetchone()
    if row is None:
        raise ImportError_(f"El ministerio '{structure['ministry']}' no existe")
    ministry_id = row[0]
    base_slug = structure["slug"]
    current = _current_version(cur, ministry_id, base_slug)
    shape = fingerprint(structure, texts)

    if current is None:
        program_id = uuid.uuid4()
        _write_program(cur, program_id, structure, texts, ministry_id,
                       slug=base_slug, version=1, previous_id=None)
        action = "created"
    else:
        program_id, status, version, slug = current
        if status == "DRAFT":
            # A draft has never been enrolled in (only PUBLISHED programs can be), so it is
            # rewritten in place; its progress rows cannot exist.
            cur.execute("DELETE FROM program_sections WHERE program_id = %s", (program_id,))
            cur.execute(
                "UPDATE programs SET kind = %s, code = %s, name = %s, description = %s,"
                " image_url = %s, sort_order = %s, authority = %s, issuer_level = %s,"
                " source = %s, source_url = %s, license = %s, updated_at = now() WHERE id = %s",
                (structure["kind"], structure.get("code"), structure["name"],
                 structure.get("description"), structure.get("image_url"),
                 structure.get("sort_order", 0), structure.get("authority"),
                 structure.get("issuer_level", "CLUB"), structure.get("source"),
                 structure.get("source_url"), structure.get("license"), program_id),
            )
            _write_content(cur, program_id, structure, texts, ministry_id)
            action = "updated_draft"
        else:
            if _stored_fingerprint(cur, program_id, structure) == shape:
                return {"slug": slug, "action": "unchanged", "program_id": str(program_id)}
            # A published program is immutable: the change becomes the next version, in draft.
            program_id, version = uuid.uuid4(), version + 1
            _write_program(cur, program_id, structure, texts, ministry_id,
                           slug=f"{base_slug}-v{version}", version=version,
                           previous_id=current[0])
            action = "new_version"

    if operator:
        cur.execute(
            "INSERT INTO audit_log (id, action, entity_type, entity_id, user_email, details)"
            " VALUES (%s, 'PROGRAM_IMPORT', 'PROGRAM', %s, %s, %s)",
            (uuid.uuid4(), str(program_id), operator,
             f"{action}: {structure['ministry']}/{base_slug}, idiomas {sorted(texts)}"),
        )
    return {"slug": base_slug, "action": action, "program_id": str(program_id),
            "locales": sorted(texts)}


def _stored_fingerprint(cur, program_id, structure: dict) -> str:
    """The fingerprint of what is already stored, in the same shape as `fingerprint`."""
    cur.execute(
        "SELECT s.slug, r.label, r.kind, r.evidence_required, h.slug, c.slug, p.slug,"
        " r.target_quantity, r.activity_category, r.position"
        " FROM program_requirements r"
        " JOIN program_sections s ON s.id = r.section_id"
        " LEFT JOIN honors h ON h.id = r.target_honor_id"
        " LEFT JOIN honor_categories c ON c.id = r.target_category_id"
        " LEFT JOIN programs p ON p.id = r.target_program_id"
        " WHERE r.program_id = %s ORDER BY r.position",
        (program_id,),
    )
    sections: dict[str, list] = {}
    order: list[str] = []
    for slug, label, kind, evidence, honor, category, target, quantity, activity, _pos in cur.fetchall():
        if slug not in sections:
            sections[slug] = []
            order.append(slug)
        sections[slug].append({
            "label": label, "kind": kind, "evidence_required": bool(evidence), "honor": honor,
            "category": category, "program": target,
            "quantity": float(quantity) if quantity is not None else None,
            "activity": activity,
        })
    cur.execute(
        "SELECT t.locale, pt.name, r.label, t.description, t.instructions"
        " FROM program_requirement_texts t"
        " JOIN program_requirements r ON r.id = t.requirement_id"
        " LEFT JOIN program_translations pt ON pt.program_id = r.program_id AND pt.locale = t.locale"
        " WHERE r.program_id = %s ORDER BY t.locale, r.position",
        (program_id,),
    )
    texts: dict[str, dict] = {}
    for locale, name, label, description, instructions in cur.fetchall():
        entry = texts.setdefault(locale, {"name": name, "requirements": {}})
        entry["requirements"][label] = {"description": description, "instructions": instructions}
    cur.execute("SELECT kind, name FROM programs WHERE id = %s", (program_id,))
    kind, name = cur.fetchone()
    for locale, entry in texts.items():
        if entry["name"] is None:
            entry["name"] = name
    shape = {
        "kind": kind,
        "sections": [{"slug": slug, "requirements": sections[slug]} for slug in order],
        "texts": dict(sorted(texts.items())),
    }
    return json.dumps(shape, sort_keys=True, ensure_ascii=False)


def _normalise_quantities(structure: dict) -> None:
    for section in structure["sections"]:
        for requirement in section.get("requirements") or []:
            if requirement.get("target_quantity") is not None:
                requirement["target_quantity"] = float(requirement["target_quantity"])


def run(folder: pathlib.Path, url: str, commit: bool, operator: str | None) -> list[dict]:
    files = sorted(
        path for path in folder.glob("*/*.json") if path.name.count(".") == 1
    )
    if not files:
        raise ImportError_(f"No hay archivos de estructura en {folder}")
    report = []
    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn:
        with conn.cursor() as cur:
            for path in files:
                structure = read_structure(path)
                _normalise_quantities(structure)
                texts, skipped = read_texts(path.parent, path.stem, structure)
                result = import_one(cur, structure, texts, operator=operator)
                result["skipped_locales"] = skipped
                result["file"] = str(path)
                report.append(result)
        if commit:
            conn.commit()
        else:
            conn.rollback()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", default="data/programs")
    parser.add_argument("--commit", action="store_true", help="sin esto es un simulacro")
    parser.add_argument("--operator", default=os.environ.get("USER"),
                        help="quién importa, para la fila de auditoría PROGRAM_IMPORT")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    try:
        report = run(pathlib.Path(args.folder).expanduser(), url, args.commit, args.operator)
    except ImportError_ as exc:
        sys.exit(f"ERROR: {exc}")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not args.commit:
        print("\nSIMULACRO: nada se ha escrito. Añade --commit para aplicarlo.")
    print("Todo entra como BORRADOR: publícalo sólo tras cotejarlo con el manual vigente.")


if __name__ == "__main__":
    main()
