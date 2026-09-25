"""Carga listas OFICIALES de requisitos que no vienen de la Pathfinder Wiki (para esas está
import_wiki_requirements.py): el PDF de la división que aprobó la especialidad (SPD), la ficha de
guiasmayores.com, Wikibooks para las especialidades ADRA descontinuadas, etc.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_official_requirements.py                  # simulacro (ROLLBACK)
    DATABASE_URL=... python import_official_requirements.py --commit --operator <email>
    DATABASE_URL=... python import_official_requirements.py data/official_requirements/es/nudos-avanzado.json

Datos: `backend/data/official_requirements/<locale>/<honor-slug>.json`, uno por especialidad e idioma:

    {"honor_slug": "nudos-avanzado", "ministry": "pathfinders", "locale": "es",
     "source": "guiasmayores.com",                        # nunca traduccion-no-oficial-* ni pathfinder-wiki
     "source_url": "https://www.guiasmayores.com/uploads/…/nudos_avanzado.pdf",
     "license": null,                                     # la de la fuente (null = no declara ninguna)
     "retrieved": "2026-09-25", "notes": "…",             # opcionales, no se cargan
     "requirements": [{"position": 1, "description": "…", "instructions": null,
                       "is_theoretical": true}]}          # is_theoretical opcional

Reglas (las mismas que el importador de la wiki):
  * una lista escrita por un instructor (filas sin `source`) en ese idioma nunca se toca, ni una lista
    oficial de OTRA fuente en ese idioma (la de la wiki incluida): se informa y se deja;
  * una traducción no oficial (`traduccion-no-oficial-*`) de ese locale se reemplaza en el sitio, por
    posición (mismos ids: el progreso de los miembros se conserva), igual que las filas de esta misma fuente;
  * idempotente: solo se escribe lo que cambió; las filas sobrantes se borran solo si no cuelgan de
    ellas preguntas de examen ni progreso; cada especialidad que cambia deja una fila
    HONOR_OFFICIAL_IMPORT en audit_log con el hash del fichero;
  * `is_theoretical`: si el fichero no lo trae, al insertar queda el valor por defecto y al actualizar
    se conserva (es decisión de un revisor, no de la fuente);
  * simulacro por defecto: todo en una transacción y ROLLBACK; --commit escribe.
Las traducciones no oficiales a otros idiomas se hacen después, desde esta lista, con el flujo de
docs/TRADUCCIONES.md (import_requirement_translations.py).
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
DATA = HERE.parent / "data" / "official_requirements"
UNOFFICIAL = "traduccion-no-oficial-"
WIKI_SOURCE = "pathfinder-wiki"
AUDIT_ACTION = "HONOR_OFFICIAL_IMPORT"
IMPORTER = "import_official_requirements"
LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
KEYS = {"honor_slug", "locale", "source", "source_url", "license", "requirements"}
OPTIONAL = {"ministry", "retrieved", "notes"}


class DocumentError(Exception):
    """A file that does not make sense: nothing of it is loaded."""


def language(locale: str) -> str:
    return locale.lower().split("-")[0]


def _clean(value):
    if value is None:
        return None
    value = str(value).rstrip()
    return value if value.strip() else None


def check_document(doc: dict, origin: str = "") -> dict:
    where = origin or doc.get("honor_slug", "?")
    missing = KEYS - doc.keys()
    if missing:
        raise DocumentError(f"{where}: faltan claves {sorted(missing)}")
    unknown = doc.keys() - KEYS - OPTIONAL
    if unknown:
        raise DocumentError(f"{where}: claves desconocidas {sorted(unknown)}")
    if not LOCALE_RE.match(doc["locale"] or ""):
        raise DocumentError(f"{where}: locale inválido {doc['locale']!r} (es, en, pt-BR…)")
    source = doc["source"] or ""
    if not source or len(source) > 40 or source.startswith(UNOFFICIAL) or source == WIKI_SOURCE:
        raise DocumentError(f"{where}: source {source!r} no vale aquí (oficial, máx. 40 caracteres; la wiki va"
                            " por import_wiki_requirements.py, las traducciones por import_requirement_translations.py)")
    if not _clean(doc["source_url"]):
        raise DocumentError(f"{where}: falta source_url")
    if doc["license"] is not None and len(doc["license"]) > 40:
        raise DocumentError(f"{where}: license de más de 40 caracteres")
    rows, seen = [], set()
    for req in doc["requirements"]:
        position = req.get("position")
        if not isinstance(position, int) or position in seen:
            raise DocumentError(f"{where}: posición inválida o repetida {position!r}")
        seen.add(position)
        text = _clean(req.get("description"))
        if text is None:
            raise DocumentError(f"{where}: requisito {position} sin texto")
        theoretical = req.get("is_theoretical")
        if theoretical is not None and not isinstance(theoretical, bool):
            raise DocumentError(f"{where}: is_theoretical del requisito {position} no es booleano")
        rows.append({"position": position, "description": text, "instructions": _clean(req.get("instructions")),
                     "is_theoretical": theoretical})
    if not rows:
        raise DocumentError(f"{where}: sin requisitos")
    rows.sort(key=lambda r: r["position"])
    if [r["position"] for r in rows] != list(range(1, len(rows) + 1)):
        raise DocumentError(f"{where}: las posiciones deben ser 1…{len(rows)}")
    payload = {k: doc.get(k) for k in sorted(KEYS | {"ministry"})}
    return {"honor_slug": doc["honor_slug"], "ministry": doc.get("ministry"), "locale": doc["locale"],
            "source": source, "source_url": doc["source_url"], "license": doc["license"], "requirements": rows,
            "hash": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "origin": origin}


def files(paths: list[str], locale: str | None = None) -> list[pathlib.Path]:
    found = []
    for raw in paths or [str(DATA)]:
        path = pathlib.Path(raw)
        found += sorted(path.rglob("*.json")) if path.is_dir() else [path]
    if locale:
        found = [p for p in found if p.parent.name.lower() == locale.lower()]
    return found


def load_documents(paths: list[str], locale: str | None = None) -> list[dict]:
    docs = []
    for path in files(paths, locale):
        doc = check_document(json.loads(path.read_text(encoding="utf-8")), str(path))
        if path.parent.name != doc["locale"] or path.stem != doc["honor_slug"]:
            raise DocumentError(f"{path}: la ruta debe ser <locale>/<honor_slug>.json")
        docs.append(doc)
    return docs


def connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))


def new_report() -> dict:
    return {"documents": 0, "honors_missing": [], "lists_written": 0, "lists_unchanged": 0, "rows_inserted": 0,
            "rows_updated": 0, "rows_deleted": 0, "rows_kept_in_use": 0, "unofficial_replaced": [],
            "kept_instructor_list": [], "kept_official_list": []}


def run(conn, docs: list[dict], *, operator: str | None = None, report: dict | None = None) -> dict:
    """Apply the documents inside the caller's transaction (it commits or rolls back)."""
    report = report or new_report()
    with conn.cursor() as cur:
        for doc in docs:
            report["documents"] += 1
            label = f"{doc['locale']}/{doc['honor_slug']}"
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
            changes = _write(cur, honor_id, doc, report, label)
            if changes:
                cur.execute(
                    "INSERT INTO audit_log (action, entity_type, entity_id, user_email, details, metadata_json)"
                    " VALUES (%s, 'HONOR', %s, %s, %s, %s::jsonb)",
                    (AUDIT_ACTION, str(honor_id), operator or IMPORTER, f"{label} ({IMPORTER})",
                     json.dumps({"importer": IMPORTER, "slug": doc["honor_slug"], "locale": doc["locale"],
                                 "source": doc["source"], "source_url": doc["source_url"],
                                 "content_hash": doc["hash"], "rows_written": changes})))
    return report


def _write(cur, honor_id, doc: dict, report: dict, label: str) -> int:
    lang = language(doc["locale"])
    cur.execute("SELECT id, position, description, instructions, is_theoretical, source, source_url, license,"
                " locale FROM honor_requirements WHERE honor_id = %s AND (lower(locale) = %s OR lower(locale)"
                " LIKE %s) ORDER BY position, created_at", (honor_id, lang, lang + "-%"))
    stored = cur.fetchall()
    if any(row[5] is None for row in stored):
        report["kept_instructor_list"].append(label)
        return 0
    if any(row[5] != doc["source"] and not row[5].startswith(UNOFFICIAL) for row in stored):
        report["kept_official_list"].append(label)
        return 0
    mine = {}
    # this source's rows first, then an unofficial translation of the same locale (replaced in place)
    for row in sorted((r for r in stored if r[8] == doc["locale"]), key=lambda r: r[5] != doc["source"]):
        mine.setdefault(row[1], row)
    if any(row[5].startswith(UNOFFICIAL) for row in mine.values()):
        report["unofficial_replaced"].append(label)
    changes = 0
    for req in doc["requirements"]:
        current = mine.pop(req["position"], None)
        theoretical = req["is_theoretical"]
        if current is None:
            cur.execute("INSERT INTO honor_requirements (id, honor_id, position, description, instructions,"
                        " is_theoretical, locale, source, source_url, license)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (uuid.uuid4(), honor_id, req["position"], req["description"], req["instructions"],
                         True if theoretical is None else theoretical, doc["locale"], doc["source"],
                         doc["source_url"], doc["license"]))
            report["rows_inserted"] += 1
            changes += 1
            continue
        wanted = (req["description"], req["instructions"], current[4] if theoretical is None else theoretical,
                  doc["source"], doc["source_url"], doc["license"])
        if tuple(current[2:8]) != wanted:
            cur.execute("UPDATE honor_requirements SET description = %s, instructions = %s, is_theoretical = %s,"
                        " source = %s, source_url = %s, license = %s WHERE id = %s", (*wanted, current[0]))
            report["rows_updated"] += 1
            changes += 1
    for leftover in mine.values():
        cur.execute("DELETE FROM honor_requirements r WHERE r.id = %s"
                    " AND NOT EXISTS (SELECT 1 FROM honor_questions q WHERE q.requirement_id = r.id)"
                    " AND NOT EXISTS (SELECT 1 FROM requirement_progress p WHERE p.requirement_id = r.id)",
                    (leftover[0],))
        if cur.rowcount:
            report["rows_deleted"] += 1
            changes += 1
        else:
            report["rows_kept_in_use"] += 1
    report["lists_written" if changes else "lists_unchanged"] += 1
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help=f"ficheros o carpetas (por defecto {DATA})")
    parser.add_argument("--locale", help="solo la carpeta de este locale (es, en, pt-BR)")
    parser.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL (por defecto ROLLBACK)")
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
    print(json.dumps({**report, "committed": args.commit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
