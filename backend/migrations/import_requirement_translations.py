"""Carga traducciones NO OFICIALES de requisitos, nombres y descripciones de especialidades.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_requirement_translations.py                    # simulacro (ROLLBACK)
    DATABASE_URL=... python import_requirement_translations.py --commit           # escribe
    DATABASE_URL=... python import_requirement_translations.py --locale pt-BR --commit
    DATABASE_URL=... python import_requirement_translations.py data/requirement_translations/es/aves-de-rapina.json

Datos: `backend/data/requirement_translations/<locale>/<honor-slug>.json` (procedimiento completo en
docs/TRADUCCIONES.md), un fichero por especialidad e idioma:

    {"honor_slug": "aves-de-rapina", "ministry": "pathfinders",          # ministry: opcional
     "source_locale": "en", "target_locale": "es",
     "source": "traduccion-no-oficial-pathfinder-wiki",
     "source_url": "https://wiki.pathfindersonline.org/w/AY_Honors/Raptors/Requirements",
     "license": "CC BY-SA 3.0",
     "name": null, "description": null,                                  # null = no se toca
     "name_source": null, "name_source_url": null, "name_license": null, # opcional: origen del nombre si
                                                                         # no es la traducción (p. ej. el
                                                                         # título en inglés de la wiki)
     "requirements": [{"position": 1, "description": "...", "instructions": null}, ...]}

Reglas:
  * solo escribe filas cuyo `source` empieza por `traduccion-no-oficial-` (la convención del importador
    de Aventureros): una lista escrita por un instructor (sin `source`) o una lista oficial
    (`pathfinder-wiki`, `guiasmayores.com`, `mundoja.org`…) en ese idioma NUNCA se toca; tampoco un
    nombre en honor_translations con otro origen;
  * los requisitos se casan con la lista origen (`source_locale`) por `position`: tienen que ser las
    mismas posiciones; `is_theoretical` se copia de la lista origen;
  * idempotente: por (especialidad, locale, position) solo se escribe lo que cambió (el contenido de
    cada fila se compara entero); una segunda corrida sin cambios no escribe nada. Cada especialidad
    que cambia deja una fila en audit_log (HONOR_TRANSLATION_IMPORT) con el hash del fichero;
  * las filas sobrantes (la traducción anterior tenía más posiciones) se borran solo si no cuelgan de
    ellas preguntas de examen ni progreso de miembros;
  * español: honors.name / honors.description son el texto fuente, así que `name`/`description` de un
    fichero es/ se ignoran;
  * simulacro por defecto: todo en una transacción y ROLLBACK; --commit escribe.
Una lista oficial que llegue después (import_wiki_requirements.py con la página 100 % traducida)
reemplaza estas filas; import_wiki_names.py hace lo mismo con los nombres.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "requirement_translations"
PREFIX = "traduccion-no-oficial-"
NAME_SOURCES_ALLOWED = ("pathfinder-wiki",)  # a name copied verbatim from the wiki (its English title)
AUDIT_ACTION = "HONOR_TRANSLATION_IMPORT"
IMPORTER = "import_requirement_translations"
SOURCE_LOCALE = "es"
LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
KEYS = {"honor_slug", "source_locale", "target_locale", "source", "source_url", "license", "name",
        "description", "requirements"}
OPTIONAL = {"ministry", "name_source", "name_source_url", "name_license", "notes"}


class DocumentError(Exception):
    """A translation file that does not make sense: nothing of it is loaded."""


def language(locale: str) -> str:
    return locale.lower().split("-")[0]


def _clean(value):
    if value is None:
        return None
    value = str(value).rstrip()
    return value if value.strip() else None


# ----------------------------------------------------------------------------
# Files (no database)
# ----------------------------------------------------------------------------
def check_document(doc: dict, origin: str = "") -> dict:
    """Normalise one translation file or raise DocumentError."""
    where = origin or doc.get("honor_slug", "?")
    missing = KEYS - doc.keys()
    if missing:
        raise DocumentError(f"{where}: faltan claves {sorted(missing)}")
    unknown = doc.keys() - KEYS - OPTIONAL
    if unknown:
        raise DocumentError(f"{where}: claves desconocidas {sorted(unknown)}")
    for key in ("source_locale", "target_locale"):
        if not LOCALE_RE.match(doc[key] or ""):
            raise DocumentError(f"{where}: {key} inválido {doc[key]!r} (es, en, pt-BR…)")
    if language(doc["source_locale"]) == language(doc["target_locale"]):
        raise DocumentError(f"{where}: origen y destino son el mismo idioma")
    if not str(doc["source"] or "").startswith(PREFIX) or len(doc["source"]) > 40:
        raise DocumentError(f"{where}: source debe empezar por {PREFIX!r} (máx. 40 caracteres)")
    if doc["license"] is not None and len(doc["license"]) > 40:
        raise DocumentError(f"{where}: license de más de 40 caracteres")
    name_source = doc.get("name_source")
    if name_source is not None and not (name_source.startswith(PREFIX) or name_source in NAME_SOURCES_ALLOWED):
        raise DocumentError(f"{where}: name_source {name_source!r} no permitido")
    name = _clean(doc["name"])
    if name is not None and len(name) > 180:
        raise DocumentError(f"{where}: nombre de más de 180 caracteres")
    rows, seen = [], set()
    for req in doc["requirements"]:
        position = req.get("position")
        if not isinstance(position, int) or position in seen:
            raise DocumentError(f"{where}: posición inválida o repetida {position!r}")
        seen.add(position)
        text = _clean(req.get("description"))
        if text is None:
            raise DocumentError(f"{where}: requisito {position} sin texto")
        rows.append({"position": position, "description": text, "instructions": _clean(req.get("instructions"))})
    rows.sort(key=lambda r: r["position"])
    payload = {k: doc.get(k) for k in sorted(KEYS | OPTIONAL) if k != "notes"}
    return {
        "honor_slug": doc["honor_slug"],
        "ministry": doc.get("ministry"),
        "source_locale": doc["source_locale"],
        "target_locale": doc["target_locale"],
        "source": doc["source"],
        "source_url": doc["source_url"],
        "license": doc["license"],
        "name": name,
        "description": _clean(doc["description"]),
        "name_source": name_source or doc["source"],
        "name_source_url": doc.get("name_source_url") if name_source else doc["source_url"],
        "name_license": doc.get("name_license") if name_source else doc["license"],
        "requirements": rows,
        "hash": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "origin": origin,
    }


def files(paths: list[str], locale: str | None = None) -> list[pathlib.Path]:
    found = []
    for raw in paths or [str(DATA)]:
        path = pathlib.Path(raw)
        if path.is_dir():
            found += sorted(p for p in path.rglob("*.json") if p.name != "pending.json")
        else:
            found.append(path)
    if locale:
        found = [p for p in found if p.parent.name.lower() == locale.lower()]
    return found


def load_documents(paths: list[str], locale: str | None = None) -> list[dict]:
    docs = []
    for path in files(paths, locale):
        doc = check_document(json.loads(path.read_text(encoding="utf-8")), str(path))
        if path.parent.name.lower() != doc["target_locale"].lower():
            raise DocumentError(f"{path}: está en la carpeta {path.parent.name} pero target_locale es "
                                f"{doc['target_locale']}")
        docs.append(doc)
    return docs


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
def connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))


def _is_ours(source: str | None) -> bool:
    return bool(source) and source.startswith(PREFIX)


def new_report() -> dict:
    return {"documents": 0, "honors_missing": [], "structure_mismatch": [],
            "requirement_lists_written": 0, "requirement_lists_unchanged": 0, "requirement_rows_inserted": 0,
            "requirement_rows_updated": 0, "requirement_rows_deleted": 0, "requirement_rows_kept_in_use": 0,
            "kept_instructor_list": [], "kept_official_list": [], "names_written": 0, "names_unchanged": 0,
            "names_kept": [], "names_ignored_source_language": 0}


def run(conn, docs: list[dict], *, operator: str | None = None, report: dict | None = None) -> dict:
    """Apply the documents inside the caller's transaction (it commits or rolls back)."""
    report = report or new_report()
    operator = operator or IMPORTER
    with conn.cursor() as cur:
        for doc in docs:
            report["documents"] += 1
            label = f"{doc['target_locale']}/{doc['honor_slug']}"
            if doc["ministry"]:
                cur.execute("SELECT h.id FROM honors h JOIN ministries m ON m.id = h.ministry_id"
                            " WHERE h.slug = %s AND m.slug = %s", (doc["honor_slug"], doc["ministry"]))
            else:
                cur.execute("SELECT id FROM honors WHERE slug = %s", (doc["honor_slug"],))
            found = cur.fetchall()
            if len(found) != 1:
                report["honors_missing"].append(label + (" (ambiguo: añade ministry)" if found else ""))
                continue
            honor_id = found[0][0]
            written = {"requirements": _requirements(cur, honor_id, doc, report, label),
                       "name": _name(cur, honor_id, doc, report, label)}
            if written["requirements"] or written["name"]:
                cur.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, user_email, details, metadata_json)"
                    " VALUES (%s, 'HONOR', %s, %s, %s, %s::jsonb)",
                    (AUDIT_ACTION, str(honor_id), operator, f"{label} ({IMPORTER})",
                     json.dumps({"importer": IMPORTER, "slug": doc["honor_slug"], "locale": doc["target_locale"],
                                 "source": doc["source"], "content_hash": doc["hash"], **written})))
    return report


def _requirements(cur, honor_id, doc: dict, report: dict, label: str) -> int:
    """Rows written for this document (0 = nothing changed or the list is not ours to write)."""
    if not doc["requirements"]:
        return 0
    target, lang = doc["target_locale"], language(doc["target_locale"])
    cur.execute("SELECT position, is_theoretical FROM honor_requirements WHERE honor_id = %s AND locale = %s"
                " ORDER BY position", (honor_id, doc["source_locale"]))
    origin = dict(cur.fetchall())
    if sorted(origin) != [r["position"] for r in doc["requirements"]]:
        report["structure_mismatch"].append(f"{label}: origen {doc['source_locale']} {sorted(origin)} ≠ "
                                            f"traducción {[r['position'] for r in doc['requirements']]}")
        return 0
    cur.execute("SELECT id, position, description, instructions, is_theoretical, source, source_url, license,"
                " locale FROM honor_requirements WHERE honor_id = %s AND (lower(locale) = %s OR lower(locale)"
                " LIKE %s) ORDER BY position, created_at", (honor_id, lang, lang + "-%"))
    stored = cur.fetchall()
    if any(row[5] is None for row in stored):
        report["kept_instructor_list"].append(label)
        return 0
    if any(not _is_ours(row[5]) for row in stored):
        report["kept_official_list"].append(label)
        return 0
    mine = {}
    for row in stored:
        if row[8] == target:
            mine.setdefault(row[1], row)  # a duplicate position (should not happen) is left alone
    changes = 0
    for req in doc["requirements"]:
        wanted = (req["description"], req["instructions"], origin[req["position"]], doc["source"],
                  doc["source_url"], doc["license"])
        current = mine.pop(req["position"], None)
        if current is None:
            cur.execute("INSERT INTO honor_requirements (id, honor_id, position, description, instructions,"
                        " is_theoretical, locale, source, source_url, license)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (uuid.uuid4(), honor_id, req["position"], *wanted[:3], target, *wanted[3:]))
            report["requirement_rows_inserted"] += 1
            changes += 1
        elif tuple(current[2:8]) != wanted:
            cur.execute("UPDATE honor_requirements SET description = %s, instructions = %s, is_theoretical = %s,"
                        " source = %s, source_url = %s, license = %s WHERE id = %s", (*wanted, current[0]))
            report["requirement_rows_updated"] += 1
            changes += 1
    for leftover in mine.values():
        cur.execute("DELETE FROM honor_requirements r WHERE r.id = %s"
                    " AND NOT EXISTS (SELECT 1 FROM honor_questions q WHERE q.requirement_id = r.id)"
                    " AND NOT EXISTS (SELECT 1 FROM requirement_progress p WHERE p.requirement_id = r.id)",
                    (leftover[0],))
        if cur.rowcount:
            report["requirement_rows_deleted"] += 1
            changes += 1
        else:
            report["requirement_rows_kept_in_use"] += 1
    report["requirement_lists_written" if changes else "requirement_lists_unchanged"] += 1
    return changes


def _name(cur, honor_id, doc: dict, report: dict, label: str) -> bool:
    if doc["name"] is None and doc["description"] is None:
        return False
    target, lang = doc["target_locale"], language(doc["target_locale"])
    if lang == SOURCE_LOCALE:
        report["names_ignored_source_language"] += 1
        return False
    cur.execute("SELECT locale, name, description, source, source_url, license FROM honor_translations"
                " WHERE honor_id = %s AND (lower(locale) = %s OR lower(locale) LIKE %s)",
                (honor_id, lang, lang + "-%"))
    stored = {row[0]: row for row in cur.fetchall()}
    current = stored.get(target)
    wanted = (doc["name"], doc["description"], doc["name_source"], doc["name_source_url"], doc["name_license"])
    if current is not None and tuple(current[1:]) == wanted:
        report["names_unchanged"] += 1
        return False
    if any(locale != target for locale in stored) or (current is not None and not _is_ours(current[3])):
        report["names_kept"].append(label)
        return False
    if doc["name"] is None:
        if current is None:  # a description alone needs a name row to hang from
            report["names_kept"].append(label + " (sin nombre)")
            return False
        wanted = (current[1], *wanted[1:])
    cur.execute(
        "INSERT INTO honor_translations (honor_id, locale, name, description, source, source_url, license)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (honor_id, locale) DO UPDATE SET"
        " name = EXCLUDED.name, description = EXCLUDED.description, source = EXCLUDED.source,"
        " source_url = EXCLUDED.source_url, license = EXCLUDED.license, updated_at = now()",
        (honor_id, target, *wanted))
    report["names_written"] += 1
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help=f"ficheros o carpetas (por defecto {DATA})")
    parser.add_argument("--locale", help="solo la carpeta de este locale (es, en, pt-BR)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="por defecto: todo en una transacción y ROLLBACK")
    mode.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL")
    parser.add_argument("--operator", default=os.environ.get("USER"), help="quién importa (audit_log)")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    try:
        docs = load_documents(args.paths, args.locale)
    except (DocumentError, json.JSONDecodeError) as exc:
        sys.exit(f"ERROR: {exc}")
    with connect(url) as conn:
        report = run(conn, docs, operator=args.operator)
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({**report, "committed": bool(args.commit)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
