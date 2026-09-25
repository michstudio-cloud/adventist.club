"""Inventario de traducciones que faltan en el catálogo de especialidades. SOLO LECTURA.

    DATABASE_URL=... python requirement_translation_gaps.py                      # tabla + JSON
    DATABASE_URL=... python requirement_translation_gaps.py --json               # solo JSON
    DATABASE_URL=... python requirement_translation_gaps.py --pending ../../data/requirement_translations/pending.json

Por ministerio y locale objetivo (es, en, pt-BR):
  * honors            especialidades del ministerio (todas, cualquier estado)
  * with_list         tienen lista de requisitos en ese idioma (cualquier región: pt sirve a pt-BR,
                      que es lo que hace `best_locale` en el API)
  * to_translate      no la tienen pero existe una lista en otro idioma de la que traducir
  * requirements      nº de requisitos de esas listas origen (la que se usaría: en, luego es)
  * no_list_at_all    sin ninguna lista: no hay nada que traducir (se listan en el JSON)
  * names_missing     sin nombre en ese idioma (es: honors.name vacío; resto: sin fila en honor_translations)
  * descriptions_missing  la descripción origen (honors.description, en español) existe y falta en ese idioma
  * descriptions_no_source la descripción origen está vacía: no hay nada que traducir

`--pending` escribe la lista de slugs pendientes por locale y ministerio (la usan los agentes que
traducen; ver docs/TRADUCCIONES.md). Nunca escribe en la base.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

TARGETS = ("es", "en", "pt-BR")
SOURCE_LOCALE = "es"  # honors.name / honors.description are Spanish
# Preferred list to translate FROM: the original (English, the wiki's language), then Spanish.
SOURCE_ORDER = ("en", "es")


def language(locale: str) -> str:
    return locale.lower().split("-")[0]


def connect(url: str):
    import psycopg

    conn = psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    conn.read_only = True
    return conn


def load(conn) -> list[dict]:
    """One dict per honour: ministry, slug, name, description, lists {locale: rows}, names {locale}."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h.id, m.slug, h.slug, h.name, h.description, h.status FROM honors h"
            " JOIN ministries m ON m.id = h.ministry_id ORDER BY m.slug, h.slug")
        honors = {row[0]: {"ministry": row[1], "slug": row[2], "name": row[3], "description": row[4],
                           "status": row[5], "lists": {}, "names": {}} for row in cur.fetchall()}
        cur.execute("SELECT honor_id, locale, count(*) FROM honor_requirements GROUP BY 1, 2")
        for honor_id, locale, n in cur.fetchall():
            if honor_id in honors:
                honors[honor_id]["lists"][locale] = n
        cur.execute("SELECT honor_id, locale, name, description FROM honor_translations")
        for honor_id, locale, name, desc in cur.fetchall():
            if honor_id in honors:
                honors[honor_id]["names"][locale] = {"name": name, "description": desc}
    return list(honors.values())


def has_locale(stored, target: str) -> bool:
    return any(language(locale) == language(target) for locale in stored)


def source_list(lists: dict, target: str) -> tuple[str | None, int]:
    """The list a translation into `target` starts from: en, then es, then any other."""
    candidates = [loc for loc in SOURCE_ORDER if loc in lists and language(loc) != language(target)]
    candidates += sorted(loc for loc in lists if loc not in candidates and language(loc) != language(target))
    return (candidates[0], lists[candidates[0]]) if candidates else (None, 0)


def inventory(honors: list[dict]) -> dict:
    report: dict = {}
    for h in honors:
        for target in TARGETS:
            row = report.setdefault(h["ministry"], {}).setdefault(target, {
                "honors": 0, "with_list": 0, "to_translate": 0, "requirements": 0, "no_list_at_all": 0,
                "names_missing": 0, "descriptions_missing": 0, "descriptions_no_source": 0,
                "pending": {"requirements": [], "names": [], "no_list_at_all": []}})
            row["honors"] += 1
            if has_locale(h["lists"], target):
                row["with_list"] += 1
            else:
                src, n = source_list(h["lists"], target)
                if src:
                    row["to_translate"] += 1
                    row["requirements"] += n
                    row["pending"]["requirements"].append(h["slug"])
                else:
                    row["no_list_at_all"] += 1
                    row["pending"]["no_list_at_all"].append(h["slug"])
            has_source_description = bool((h["description"] or "").strip())
            if target == SOURCE_LOCALE:
                name_ok = bool((h["name"] or "").strip())
                desc_ok = has_source_description
            else:
                tr = next((v for k, v in h["names"].items() if language(k) == language(target)), None)
                name_ok = bool(tr and (tr["name"] or "").strip())
                desc_ok = bool(tr and (tr["description"] or "").strip())
            if not name_ok:
                row["names_missing"] += 1
                row["pending"]["names"].append(h["slug"])
            if not has_source_description:
                row["descriptions_no_source"] += 1
            elif not desc_ok:
                row["descriptions_missing"] += 1
    return report


def print_table(report: dict) -> None:
    cols = ("honors", "with_list", "to_translate", "requirements", "no_list_at_all", "names_missing",
            "descriptions_missing", "descriptions_no_source")
    head = f"{'ministerio':<13}{'locale':<7}" + "".join(f"{c:>13}" for c in ("total", "con lista", "a traducir",
                                                                            "requisitos", "sin ninguna", "sin nombre",
                                                                            "sin descr.", "descr. vacía"))
    print(head)
    print("-" * len(head))
    for ministry, targets in report.items():
        for target, row in targets.items():
            print(f"{ministry:<13}{target:<7}" + "".join(f"{row[c]:>13}" for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="solo el JSON (sin tabla)")
    ap.add_argument("--pending", help="escribe los slugs pendientes por locale/ministerio en este fichero")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    with connect(url) as conn:
        report = inventory(load(conn))
    summary = {m: {t: {k: v for k, v in row.items() if k != "pending"} for t, row in targets.items()}
               for m, targets in report.items()}
    if not args.json:
        print_table(report)
        print()
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.pending:
        pending = {target: {m: report[m][target]["pending"] for m in report} for target in TARGETS}
        pathlib.Path(args.pending).write_text(json.dumps(pending, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")
        print(f"pendientes -> {args.pending}", file=sys.stderr)


if __name__ == "__main__":
    main()
