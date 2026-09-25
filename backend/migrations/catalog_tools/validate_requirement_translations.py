"""Valida ficheros de traducción contra la lista origen que está en la base. SOLO LECTURA.

    DATABASE_URL=... python validate_requirement_translations.py ../../data/requirement_translations/es
    DATABASE_URL=... python validate_requirement_translations.py ../../data/requirement_translations/pt-BR/acolchado.json ...

ERRORES (el lote no se entrega así):
  * el fichero no pasa check_document del importador (claves, source traduccion-no-oficial-*, locales…);
  * la especialidad no existe, o ya tiene lista en ese idioma que no es una traducción no oficial;
  * posiciones distintas de la lista origen, o un requisito con otro número de líneas o con otros
    sub-incisos (a., b., i., …, misma sangría) que el origen; instructions vacías donde el origen las
    tiene (o al revés);
  * source / source_url / license no siguen la convención de la lista origen;
  * nombre vacío ("") en en/pt-BR cuando hay que traducirlo.
AVISOS (revisar uno a uno; se corrigen o se justifican en el informe del lote):
  * líneas con dos o más palabras comunes del idioma origen (inglés residual, español residual);
  * números del origen que no aparecen en la traducción (una medida convertida se AÑADE entre
    paréntesis, nunca sustituye a la original).
Sale con código 1 si hay errores, 2 si solo hay avisos, 0 si todo está limpio.
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from translation_common import WIKI_LICENSE, language, numbers, structure, translated_source  # noqa: E402
from import_requirement_translations import DocumentError, check_document, files  # noqa: E402

WORD = re.compile(r"[A-Za-zÀ-ÿ']+")
ENGLISH = {"the", "and", "of", "with", "your", "following", "least", "each", "which", "what", "how", "their",
           "from", "this", "that", "these", "those", "should", "must", "will", "have", "has", "identify",
           "describe", "explain", "discuss", "write", "know", "demonstrate", "about", "into", "they", "them",
           "it", "its", "be", "or", "is", "are", "you", "one", "two", "three", "who", "when", "where", "why",
           "list", "make", "take", "using", "own", "other", "such", "some", "any", "an", "on", "in",
           "to", "at", "by"}
# Spanish words that are not Portuguese (nor English) words
SPANISH = {"los", "las", "del", "el", "y", "una", "con", "sus", "cómo", "qué", "cuál", "cuáles", "debe", "hacer",
           "conocer", "describir", "nombrar", "siguientes", "también", "muy", "hay", "usted", "año", "años",
           "niños", "puede", "tiene", "tener", "mientras", "después", "cuando", "donde", "dónde", "estos",
           "ellos", "nombre"}
# Portuguese words that are not Spanish words
PORTUGUESE = {"você", "não", "uma", "com", "dos", "das", "pelo", "pela", "seu", "sua", "seus", "suas", "também",
              "então", "ou", "em", "na", "no", "nas", "nos", "ao", "aos", "é", "são", "fazer", "conhecer"}


def leftovers(text: str, source_lang: str, target_lang: str) -> list[str]:
    """Common words of the wrong language in a translated line."""
    words = {w.lower() for w in WORD.findall(text)}
    if target_lang == "en":
        return sorted(words & (PORTUGUESE - {"no"} if source_lang == "pt" else SPANISH - {"con"}))
    found = words & ENGLISH
    if target_lang == "pt":
        found |= words & SPANISH
    return sorted(found)


def validate(cur, path: pathlib.Path) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    raw = json.loads(path.read_text(encoding="utf-8"))
    try:
        doc = check_document(raw, str(path))
    except DocumentError as exc:
        return [str(exc)], []
    target, lang = doc["target_locale"], language(doc["target_locale"])
    if path.parent.name.lower() != target.lower():
        errors.append(f"carpeta {path.parent.name} ≠ target_locale {target}")
    q = "SELECT h.id, h.name FROM honors h JOIN ministries m ON m.id = h.ministry_id WHERE h.slug = %s"
    cur.execute(q + (" AND m.slug = %s" if doc["ministry"] else ""),
                (doc["honor_slug"], doc["ministry"]) if doc["ministry"] else (doc["honor_slug"],))
    found = cur.fetchall()
    if len(found) != 1:
        return errors + [f"especialidad {doc['honor_slug']} {'no existe' if not found else 'ambigua'}"], warnings
    honor_id = found[0][0]
    cur.execute("SELECT locale, source FROM honor_requirements WHERE honor_id = %s", (honor_id,))
    for locale, source in set(cur.fetchall()):
        if doc["requirements"] and language(locale) == lang and not (source or "").startswith("traduccion-no-oficial-"):
            errors.append(f"ya tiene lista {locale} de {source or 'un instructor'}: no se sobrescribe")
            break
    if raw.get("name") == "" and lang != "es":
        errors.append("falta el nombre traducido (name vacío)")
    if raw.get("description") == "" and lang != "es":
        errors.append("falta la descripción traducida (description vacía)")

    cur.execute("SELECT position, description, instructions, source, source_url, license FROM honor_requirements"
                " WHERE honor_id = %s AND locale = %s ORDER BY position", (honor_id, doc["source_locale"]))
    origin = {r[0]: r for r in cur.fetchall()}
    if doc["requirements"]:
        if sorted(origin) != [r["position"] for r in doc["requirements"]]:
            errors.append(f"posiciones {[r['position'] for r in doc['requirements']]} ≠ origen {sorted(origin)}")
        first = next(iter(origin.values()), None)
        if first is not None:
            want_source = translated_source(first[3])
            want_license = WIKI_LICENSE if first[3] == "pathfinder-wiki" else first[5]
            if doc["source"] != want_source:
                errors.append(f"source {doc['source']!r}, debería ser {want_source!r}")
            if doc["source_url"] != first[4]:
                errors.append(f"source_url {doc['source_url']!r}, debería ser {first[4]!r}")
            if doc["license"] != want_license:
                errors.append(f"license {doc['license']!r}, debería ser {want_license!r}")
    src_lang = language(doc["source_locale"])
    for req in doc["requirements"]:
        row = origin.get(req["position"])
        if row is None:
            continue
        pos = req["position"]
        if structure(req["description"]) != structure(row[1]):
            errors.append(f"req {pos}: estructura {structure(req['description'])} ≠ origen {structure(row[1])}")
        if bool(req["instructions"]) != bool((row[2] or "").strip()):
            errors.append(f"req {pos}: instructions {'sobran' if req['instructions'] else 'faltan'}")
        for text, src in ((req["description"], row[1]), (req["instructions"] or "", row[2] or "")):
            for n, line in enumerate(text.split("\n"), 1):
                words = leftovers(line, src_lang, lang)
                if len(words) >= 2:
                    warnings.append(f"req {pos} línea {n}: ¿{src_lang} residual? {words}: {line.strip()[:90]}")
            missing = collections.Counter(numbers(src)) - collections.Counter(numbers(text))
            if missing:
                warnings.append(f"req {pos}: números del origen ausentes {sorted(missing)}")
    if doc["name"]:
        words = leftovers(doc["name"], src_lang, lang)
        if len(words) >= 2 and not raw.get("name_source"):
            warnings.append(f"nombre: ¿residual? {words}")
    return errors, warnings


def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        sys.exit(__doc__)
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    import psycopg

    total = collections.Counter()
    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn, conn.cursor() as cur:
        conn.read_only = True
        for path in files(paths):
            errors, warnings = validate(cur, path)
            total["files"] += 1
            total["errors"] += len(errors)
            total["warnings"] += len(warnings)
            for line in errors:
                print(f"ERROR  {path.parent.name}/{path.name}: {line}")
            for line in warnings:
                print(f"AVISO  {path.parent.name}/{path.name}: {line}")
    print(f"{total['files']} ficheros, {total['errors']} errores, {total['warnings']} avisos")
    sys.exit(1 if total["errors"] else 2 if total["warnings"] else 0)


if __name__ == "__main__":
    main()
