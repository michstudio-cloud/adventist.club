"""Exporta el material de trabajo para traducir un lote de especialidades. SOLO LECTURA.

    DATABASE_URL=... python export_translation_sources.py --locale pt-BR --slugs acolchado aves-de-rapina --out /tmp/lote
    DATABASE_URL=... python export_translation_sources.py --locale pt-BR --pending ../../data/requirement_translations/pending.json \
        --offset 120 --limit 15 --out /tmp/lote-09

Por especialidad escribe `<out>/<slug>.json` con:
  * honor       slug, nombre fuente (es), wiki_title, nombres que ya existen en otros idiomas;
  * source      la lista origen (en si existe, si no es): position, description, instructions,
                is_theoretical, y su source / source_url / license;
  * reference   material de apoyo que NO se copia tal cual: la lista en el otro idioma (p. ej. la
                española de la wiki cuando se traduce del inglés al portugués) y, para es, la página
                parcialmente traducida de la wiki si está en ~/adventist-wiki (términos oficiales);
  * draft       el fichero de salida ya rellenado con los metadatos (source, source_url, license,
                nombre de la wiki si procede) y los requisitos con "description": "" — se completa y
                se guarda en backend/data/requirement_translations/<locale>/<slug>.json.
Nunca escribe en la base.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from translation_common import (  # noqa: E402
    WIKI_FOLDER, WIKI_LICENSE, language, pick_source_locale, translated_source, wiki_display_name, wiki_file_name,
    wiki_page_url,
)


def connect(url: str):
    import psycopg

    conn = psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    conn.read_only = True
    return conn


def rows_of(cur, honor_id, locale):
    cur.execute("SELECT position, description, instructions, is_theoretical, source, source_url, license"
                " FROM honor_requirements WHERE honor_id = %s AND locale = %s ORDER BY position, created_at",
                (honor_id, locale))
    return [dict(zip(("position", "description", "instructions", "is_theoretical", "source", "source_url",
                      "license"), r)) for r in cur.fetchall()]


def partial_wiki_page(title: str | None, lang: str) -> dict | None:
    if not title:
        return None
    path = WIKI_FOLDER / lang / wiki_file_name(title)
    if not path.exists():
        return None
    try:
        from parse_wiki_requirements import parse, translation_progress
    except ImportError:
        return None
    page = path.read_text(encoding="utf-8", errors="ignore")
    rows = parse(page)
    return {"file": str(path), "progress": translation_progress(page),
            "rows": [{"position": r["position"], "description": r["description"]} for r in rows]} if rows else None


def bundle(cur, slug: str, target: str, ministry: str | None) -> dict:
    if ministry:
        cur.execute("SELECT h.id, h.slug, h.name, h.description, h.wiki_title, h.source_url, m.slug FROM honors h"
                    " JOIN ministries m ON m.id = h.ministry_id WHERE h.slug = %s AND m.slug = %s", (slug, ministry))
    else:
        cur.execute("SELECT h.id, h.slug, h.name, h.description, h.wiki_title, h.source_url, m.slug FROM honors h"
                    " JOIN ministries m ON m.id = h.ministry_id WHERE h.slug = %s", (slug,))
    found = cur.fetchall()
    if len(found) != 1:
        raise SystemExit(f"{slug}: {'no existe' if not found else 'ambiguo, usa --ministry'}")
    honor_id, slug, name, description, wiki_title, honor_url, ministry_slug = found[0]
    cur.execute("SELECT locale, name, description, source FROM honor_translations WHERE honor_id = %s", (honor_id,))
    names = {loc: {"name": n, "description": d, "source": s} for loc, n, d, s in cur.fetchall()}
    cur.execute("SELECT DISTINCT locale FROM honor_requirements WHERE honor_id = %s", (honor_id,))
    stored = [r[0] for r in cur.fetchall()]
    lang = language(target)
    has_target = any(language(loc) == lang for loc in stored)
    src_locale = None if has_target else pick_source_locale(stored, target)
    src_rows = rows_of(cur, honor_id, src_locale) if src_locale else []

    if src_rows:
        first = src_rows[0]
        source, source_url, license_ = translated_source(first["source"]), first["source_url"], first["license"]
        if first["source"] == "pathfinder-wiki":
            license_ = WIKI_LICENSE
    else:  # only the name to translate: its origin is the wiki page, else the catalogue entry
        src_locale = "es"
        if wiki_title:
            source, source_url, license_ = translated_source("pathfinder-wiki"), wiki_page_url(wiki_title), WIKI_LICENSE
        else:
            origin = "guiasmayores.com" if honor_url and "guiasmayores.com" in honor_url else "catalogo"
            source, source_url, license_ = translated_source(origin), honor_url, None

    existing_name = next((v for k, v in names.items() if language(k) == lang), None)
    draft_name, name_meta = None, {}
    if lang != "es" and existing_name is None:
        if lang == "en" and wiki_title:
            draft_name = wiki_display_name(wiki_title)
            name_meta = {"name_source": "pathfinder-wiki", "name_source_url": wiki_page_url(wiki_title),
                         "name_license": WIKI_LICENSE}
        else:
            draft_name = ""  # to translate
    draft_description = None
    if lang != "es" and (description or "").strip() and not (existing_name and existing_name["description"]):
        draft_description = ""  # to translate

    reference = {}
    for loc in stored:
        if loc != src_locale and language(loc) != lang:
            reference[f"list_{loc}"] = [{"position": r["position"], "description": r["description"]}
                                        for r in rows_of(cur, honor_id, loc)]
    wiki_partial = partial_wiki_page(wiki_title, "pt-br" if lang == "pt" else lang)
    if wiki_partial and not has_target:
        reference["wiki_partial_translation"] = wiki_partial

    draft = {
        "honor_slug": slug, "ministry": ministry_slug,
        "source_locale": src_locale, "target_locale": target,
        "source": source, "source_url": source_url, "license": license_,
        "name": draft_name, "description": draft_description,
        **name_meta,
        "requirements": [{"position": r["position"], "description": "", "instructions": "" if r["instructions"] else None}
                         for r in src_rows],
    }
    return {
        "honor": {"slug": slug, "ministry": ministry_slug, "name_es": name, "description_es": description,
                  "wiki_title": wiki_title, "names": names, "target_has_list": has_target,
                  "target_name": existing_name},
        "source": {"locale": src_locale if src_rows else None, "rows": src_rows},
        "reference": reference,
        "draft": draft,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--locale", required=True, help="locale objetivo: es, en, pt-BR")
    ap.add_argument("--slugs", nargs="*", default=[])
    ap.add_argument("--pending", help="pending.json: toma los slugs pendientes (requisitos y nombres) de este locale")
    ap.add_argument("--ministry", default="pathfinders")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    slugs = list(args.slugs)
    if args.pending:
        pending = json.loads(pathlib.Path(args.pending).read_text(encoding="utf-8"))[args.locale][args.ministry]
        slugs += sorted(set(pending["requirements"]) | set(pending["names"]))
    slugs = slugs[args.offset:args.offset + args.limit if args.limit else None]
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with connect(url) as conn, conn.cursor() as cur:
        for slug in slugs:
            data = bundle(cur, slug, args.locale, args.ministry)
            (out / f"{slug}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n",
                                              encoding="utf-8")
    print(f"{len(slugs)} especialidades -> {out}")


if __name__ == "__main__":
    main()
