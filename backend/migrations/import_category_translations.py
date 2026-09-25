"""Nombres de categoría en otros idiomas (`honor_category_translations`) que no salen del índice de la
Pathfinder Wiki: ADRA, Doctrinales, Servicios Comunitarios, Asociación de Florida, Maestrías y las seis
categorías de Aventureros. Las 8 categorías generales de Conquistadores las carga import_wiki_names.py.

    pip install "psycopg[binary]"
    DATABASE_URL=... python import_category_translations.py                  # simulacro (ROLLBACK)
    DATABASE_URL=... python import_category_translations.py --commit

Datos: backend/data/category_translations.json (cada nombre con su fuente). Reglas:
  * upsert por (categoría, locale); solo escribe lo que cambió; el español es honor_categories.name y no
    se toca;
  * `legacy_locales` ({"pt": "pt-BR"}): una fila en el locale viejo se pasa al nuevo si la categoría
    no lo tiene, y se borra si ya lo tiene. El API resuelve UN locale para toda la tabla (exacto →
    idioma → región), así que con filas 'pt' (Aventureros) y 'pt-BR' (Conquistadores) mezcladas,
    ?locale=pt dejaba sin traducir las de Conquistadores y ?locale=pt-BR las de Aventureros;
  * idempotente; simulacro por defecto.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "category_translations.json"


def load(path: pathlib.Path = DATA) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["categories"]:
        if not entry.get("ministry") or not entry.get("slug") or not entry.get("names"):
            raise ValueError(f"entrada incompleta: {entry}")
        for locale, name in entry["names"].items():
            if locale.lower().startswith("es") or not name or not name.strip() or len(name) > 120:
                raise ValueError(f"{entry['slug']}: nombre {locale}={name!r} no válido")
        if entry.get("source") not in data.get("sources", {}):
            raise ValueError(f"{entry['slug']}: fuente {entry.get('source')!r} sin describir en 'sources'")
    return data


def run(conn, data: dict) -> dict:
    report = {"written": 0, "unchanged": 0, "missing_categories": [], "legacy_renamed": 0, "legacy_deleted": 0}
    with conn.cursor() as cur:
        for entry in data["categories"]:
            cur.execute("SELECT c.id FROM honor_categories c JOIN ministries m ON m.id = c.ministry_id"
                        " WHERE m.slug = %s AND c.slug = %s", (entry["ministry"], entry["slug"]))
            row = cur.fetchone()
            if row is None:
                report["missing_categories"].append(f"{entry['ministry']}/{entry['slug']}")
                continue
            for locale, name in entry["names"].items():
                cur.execute(
                    "INSERT INTO honor_category_translations (category_id, locale, name) VALUES (%s, %s, %s)"
                    " ON CONFLICT (category_id, locale) DO UPDATE SET name = EXCLUDED.name, updated_at = now()"
                    " WHERE honor_category_translations.name IS DISTINCT FROM EXCLUDED.name",
                    (row[0], locale, name))
                report["written" if cur.rowcount else "unchanged"] += 1
        for old, new in data.get("legacy_locales", {}).items():
            cur.execute("DELETE FROM honor_category_translations o WHERE o.locale = %s AND EXISTS (SELECT 1 FROM"
                        " honor_category_translations n WHERE n.category_id = o.category_id AND n.locale = %s)",
                        (old, new))
            report["legacy_deleted"] += cur.rowcount
            cur.execute("UPDATE honor_category_translations SET locale = %s, updated_at = now() WHERE locale = %s",
                        (new, old))
            report["legacy_renamed"] += cur.rowcount
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=str(DATA))
    parser.add_argument("--commit", action="store_true", help="escribe en DATABASE_URL (por defecto ROLLBACK)")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    data = load(pathlib.Path(args.path))
    import psycopg

    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn:
        report = run(conn, data)
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    print(json.dumps({**report, "committed": args.commit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
