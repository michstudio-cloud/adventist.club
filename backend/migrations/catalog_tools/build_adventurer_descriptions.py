"""Write `data/adventurer_awards_descriptions.csv`: one row per Adventurer award with its description
in Spanish and English and its class as a «level» (the slug of the class program in
`data/adventurer_classes.json`), so the web can show the English description and filter by class.

    python migrations/catalog_tools/build_adventurer_descriptions.py [--check]

Nothing is downloaded and nothing touches the database. The descriptions are exactly the ones
`import_adventurer_awards.py` builds (the book's introduction, if any, plus «Clase sugerida: …» /
«Suggested class: …» or the Multi-level sentence), so a reimport and this file never disagree.
`--check` fails (exit 1) when the CSV on disk is not what the data would produce.

Columns:
  slug, honor_slug          `community-helpers`, `av-community-helpers`
  class_en, class_es        as the importer names them (Helping Hands, Constructor, Multi-level…)
  level_slug, level_order   `corderitos`…`manos-ayudadoras` (1–6, the ages 4–9) or `multinivel` (empty order)
  description_es, description_en
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND / "migrations"))

import import_adventurer_awards as importer  # noqa: E402

OUT = BACKEND / "data" / "adventurer_awards_descriptions.csv"
CLASSES_JSON = BACKEND / "data" / "adventurer_classes.json"

# class_en of adventurer_awards.json -> slug of the class program (the importer writes it to honors.level_slug)
LEVELS = importer.LEVELS
FIELDS = ["slug", "honor_slug", "class_en", "class_es", "level_slug", "level_order", "description_es",
          "description_en"]


def level_orders() -> dict[str, int]:
    """Program slug -> 1..6 in age order, read from the class catalogue (so a renamed class fails here)."""
    programs = json.loads(CLASSES_JSON.read_text(encoding="utf-8"))["programs"]
    ordered = sorted(programs, key=lambda p: p["age"])
    return {p["slug"]: i for i, p in enumerate(ordered, start=1)}


def rows() -> list[dict]:
    orders = level_orders()
    plans = importer.build(importer.load())
    out = []
    for plan in plans:
        level = LEVELS[plan["class_en"]]
        if level != "multinivel" and level not in orders:
            raise SystemExit(f"{plan['slug']}: la clase {level!r} no está en adventurer_classes.json")
        class_es, class_en = importer.CLASSES[plan["class_en"]]
        out.append({
            "slug": plan["slug"],
            "honor_slug": f"av-{plan['slug']}",
            "class_en": class_en,
            "class_es": class_es,
            "level_slug": level,
            "level_order": orders.get(level, ""),
            "description_es": plan["description_es"],
            "description_en": plan["description_en"],
        })
    return sorted(out, key=lambda r: r["slug"])


def render(data: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(data)
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="no escribe; falla si el CSV no está al día")
    args = parser.parse_args()
    data = rows()
    text = render(data)
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            raise SystemExit(f"{OUT.name} no está al día: ejecuta build_adventurer_descriptions.py")
        print(f"{OUT.name}: al día")
        return
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT.relative_to(BACKEND)}: {len(data)} awards")


if __name__ == "__main__":
    main()
