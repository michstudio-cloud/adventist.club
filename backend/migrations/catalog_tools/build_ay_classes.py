"""Build `migrations/data/ay_classes.json` from the downloaded class pages of the Pathfinder Wiki.

    python3 build_ay_classes.py [~/adventist-wiki/classes/raw] [--out ../data/ay_classes.json]

Input: pages cached by crawl_wiki_classes.py (never fetches anything):
  Investiture_Achievement/<Class>/<Section>[/es]   the six Pathfinder classes (NAD "Investiture
                                                  Achievement", 2011+), basic + advanced level
  Master_Guide[/<Section>][/es]                   the Master Guide curriculum (GC)
Output: one entry per program in the shape read by import_ay_classes.py.

What is decided here, and why:
  * The advanced level of a class ("Trail Friend" / «Amigo de la Naturaleza») is its own
    program; its slug comes from the Spanish ribbon name (ADVANCED_SLUGS).
    The wiki repeats «Complete Friend requirements» in every section of the advanced level;
    one `PROGRAM` requirement pointing at the basic class replaces them (the others are
    dropped and listed in `notes`), because the same investiture cannot satisfy two
    requirements of one enrollment (012, rule 9).
  * Requirement kinds are ANNOTATIONS for the owner to review (spec §7: the parser does
    not guess): `HONOR` only when the text asks to complete ONE named honour as a whole,
    `HONOR_FROM_CATEGORY` only for «one honour in <category>», `HONOR_ANY` for «any one
    honour not previously earned», `PROGRAM` for the advanced
    level's prerequisite. Anything else stays `TEXT`, with the honours it mentions in `notes`.
  * `label` is «<section roman>.<wiki label>» (I.2a): the wiki restarts numbering per section.
  * A Spanish text is attached per requirement by (section, wiki label); the importer loads a
    language for a program only when every requirement has it.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
import unicodedata
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from parse_wiki_class import (  # noqa: E402
    advanced_heading, clean, content_area, parse, parse_headings,
    title_parts, translation_progress,
)

WIKI = "https://wiki.pathfindersonline.org/w/"
SOURCE, LICENSE = "pathfinder-wiki", "CC BY-SA 3.0"
DATA = pathlib.Path(__file__).resolve().parents[1] / "data"
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]

# (wiki page, slug, code) in the order of the class card.
CLASSES = [
    ("Friend", "amigo", "AMIGO"),
    ("Companion", "companero", "COMPANERO"),
    ("Explorer", "explorador", "EXPLORADOR"),
    ("Ranger", "orientador", "ORIENTADOR"),
    ("Voyager", "viajero", "VIAJERO"),
    ("Guide", "guia", "GUIA"),
]
# Slug of the advanced level, from its Spanish ribbon name. Fixed here rather than derived:
# the wiki writes «Compañero de Excursionsimo» (sic) and a slug must not carry a typo.
ADVANCED_SLUGS = {
    "amigo": "amigo-de-la-naturaleza",
    "companero": "companero-de-excursionismo",
    "explorador": "explorador-de-campo-y-bosque",
    "orientador": "orientador-de-nuevas-fronteras",
    "viajero": "viajero-al-aire-libre",
    "guia": "guia-de-vida-primitiva",
}
# Section pages in the order of the card (the index grid lists them in two columns).
CLASS_SECTIONS = [
    ("Personal_Growth", "desarrollo-personal"),
    ("Spiritual_Discovery", "descubrimiento-espiritual"),
    ("Serving_Others", "sirviendo-a-otros"),
    ("Making_Friends", "haciendo-amigos"),
    ("Health_and_Fitness", "salud-y-aptitud-fisica"),
    ("Nature_Study", "estudio-de-la-naturaleza"),
    ("Outdoor_Living", "vida-al-aire-libre"),
    ("Honor_Enrichment", "especialidades-adicionales"),
]
# Master Guide tabs, in the wiki's order.
MG_SECTIONS = [
    ("Prerequisites", "prerrequisitos"),
    ("Spiritual_Development", "desarrollo-espiritual"),
    ("Skills_Development", "desarrollo-de-destrezas"),
    ("Child_Development", "desarrollo-del-nino"),
    ("Leadership_Development", "desarrollo-de-liderazgo"),
    ("Fitness_Lifestyle_Development", "estilo-de-vida-saludable"),
    ("Documentation", "documentacion"),
]
# Wiki honour categories (AY_Honors/<Category>) -> honor_categories.slug.
CATEGORY_EXTRA = {
    "adra": "adra",
    "adventist community services": "community-services",
    "masters": "masters",
    "spiritual growth, outreach, and heritage": "spiritual-growth",
    "spiritual growth": "spiritual-growth",
    "arts and crafts": "arts-crafts-hobbies",
    "arts, crafts, and hobbies": "arts-crafts-hobbies",
    "health and science": "health-science",
    "outdoor industries": "outdoor-industries",
    "household arts": "household-arts",
    "vocational": "vocational",
    "recreation": "recreation",
    "nature": "nature",
    "nature study": "nature",
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def page_url(path: str) -> str:
    return WIKI + urllib.parse.quote(path, safe="()_-.,'!/")


def cache_name(path: str) -> str:
    return path.replace("/", "__").replace(" ", "_") + ".html"


class Catalogue:
    """Our honour slugs and category slugs, keyed by the wiki's English titles."""

    def __init__(self, links_file: pathlib.Path):
        links = json.loads(links_file.read_text(encoding="utf-8"))
        self.honors = {}
        for h in links["honors"]:
            for name in [(h.get("names") or {}).get("en"), h["wiki_title"]]:
                if name:
                    self.honors[name.lower()] = h["slug"]
                    self.honors[name.lower().replace(" & ", " and ")] = h["slug"]
        self.missing = {h["wiki_title"].lower() for h in links.get("wiki_without_match") or []}
        self.categories = dict(CATEGORY_EXTRA)
        for slug, names in (links.get("categories") or {}).items():
            if names.get("en"):
                self.categories[names["en"].lower()] = slug

    def category(self, title: str) -> str | None:
        return self.categories.get(title.lower().strip())

    def honor(self, title: str) -> str | None:
        title = re.sub(r"\s+honou?r$", "", title.lower().strip()).replace(" & ", " and ")
        # "Beginner Swimming" is written "Swimming - Beginner" in the catalogue.
        level = re.match(r"^(beginner|intermediate|advanced)\s+(.+)$", title)
        variants = [title, f"{level.group(2)} - {level.group(1)}" if level else None]
        return next((self.honors[v] for v in variants if v and v in self.honors), None)


def read(raw: pathlib.Path, path: str) -> str | None:
    file = raw / cache_name(path)
    return file.read_text(encoding="utf-8", errors="ignore") if file.exists() else None


def translated_rows(en_rows: list[dict], es_page: str | None, report: dict, path: str) -> list[dict]:
    """The Spanish rows of a page. A page the wiki marks as partly translated is still used
    requirement by requirement: its untranslated units show the English source, so a row
    whose text equals the English one is dropped (and reported)."""
    if es_page is None:
        return []
    rows = rows_of(es_page)
    progress = translation_progress(es_page)
    if progress in (None, 100):
        return rows
    english = {(r["label"], r["advanced"]): r["text"] for r in en_rows}
    kept = [r for r in rows if english.get((r["label"], r["advanced"])) != r["text"]]
    report["partial_translations"].append(
        f"{path}/es ({progress}%): {len(kept)}/{len(en_rows)} requisitos traducidos")
    return kept


def rows_of(page: str | None) -> list[dict]:
    if not page:
        return []
    return parse(page) or parse_headings(page)


def intro(page: str | None) -> str | None:
    """The overview paragraphs of the Master Guide page (between the infobox and the end)."""
    if not page:
        return None
    area = content_area(page)
    start = area.find("</table>", area.find('class="infobox"'))
    paragraphs = [clean(re.sub(r"<[^>]+>", "", p)) for p in re.findall(r"<p>(.*?)</p>", area[start:], re.S)]
    paragraphs = [p for p in paragraphs if len(p) > 60]
    return "\n\n".join(paragraphs) or None


# ----------------------------------------------------------------------------
# Annotation of requirement kinds (reviewed by a person: every guess is in `notes`)
# ----------------------------------------------------------------------------
WHOLE_HONOR = re.compile(r"^(?:complete|earn)\s+the\s+(.+?)\s+(?:honou?r|award)\b", re.I)
PARTIAL = re.compile(r"requirements?\s*(?:#|number|no\.?)?\s*\d|requirements?\s+of", re.I)
ONE_HONOR = re.compile(r"^(?:complete|earn)\s+(?:an?|one)\b(?!.*\bhonou?rs\b).*\bhonou?r\b", re.I)
SKILL = re.compile(r"skill\s+level\s+(\d(?:\s*(?:,|or|-)\s*\d)*)", re.I)
NAMED = re.compile(r"(?:\bthe|\bof)\s+([A-Z][\w&'’ -]+?)\s+Honou?r\b")
# Category names as the requirement texts write them -> honor_categories.slug.
CATEGORY_WORDS = [
    (re.compile(r"arts?\s*(?:&|and)\s*crafts|arts,\s*crafts", re.I), "arts-crafts-hobbies"),
    (re.compile(r"household\s+arts", re.I), "household-arts"),
    (re.compile(r"\brecreation(?:al)?\b", re.I), "recreation"),
    (re.compile(r"\bvocational\b", re.I), "vocational"),
    (re.compile(r"outdoor\s+industr", re.I), "outdoor-industries"),
    (re.compile(r"health\s*(?:&|and)\s*science", re.I), "health-science"),
    (re.compile(r"\bnature\b", re.I), "nature"),
    (re.compile(r"spiritual\s+growth|outreach(?:,)?\s+(?:and|&)\s+heritage", re.I), "spiritual-growth"),
    (re.compile(r"\badra\b", re.I), "adra"),
    (re.compile(r"community\s+services", re.I), "community-services"),
]


def categories_in(text: str) -> list[str]:
    found = []
    for pattern, slug in CATEGORY_WORDS:
        if pattern.search(text) and slug not in found:
            found.append(slug)
    return found


def annotate(requirement: dict, en: dict, catalogue: Catalogue) -> None:
    """Suggest the kind of a requirement from its English text. Conservative on purpose:
    anything that is not exactly «one whole named honour» / «one honour of one category» /
    «any one honour» stays TEXT, and the honours or categories it mentions go to `notes`."""
    text = en["text"].strip()
    links = en["honor_links"]
    notes = []
    linked_categories = [c for c in (catalogue.category(t) for t in links) if c]
    honors = [t for t in links if not catalogue.category(t)]
    simple = not en["sub_items"] and not PARTIAL.search(text)
    whole = WHOLE_HONOR.match(text)
    if simple and whole:
        named = whole.group(1)
        several = re.findall(r"(?:^(?:complete|earn)\s+the\s+|\bor\s+(?:the\s+)?)(.+?)\s+honou?r\b", text, re.I)
        if re.search(r"\bor\b", named, re.I) or len(honors) > 1 or len(several) > 1:
            options = honors if len(honors) > 1 else (several if len(several) > 1 else [
                re.sub(r"\s+honou?r$", "", t.strip(), flags=re.I) for t in re.split(r"\s+or\s+|,", named, flags=re.I)])
            notes.append("a elegir entre: " + " | ".join(catalogue.honor(t) or f"?{t}" for t in options)
                         + " (el esquema admite una sola especialidad por requisito)")
        else:
            title = honors[0] if honors else named
            slug = catalogue.honor(title)
            if slug:
                requirement.update(kind="HONOR", target_honor_slug=slug)
            elif title.lower() in catalogue.missing:
                notes.append(f"la especialidad «{title}» existe en la wiki pero no en nuestro catálogo")
            else:
                notes.append(f"especialidad sin resolver: «{title}»")
    elif simple and ONE_HONOR.match(text) and not honors:
        found = linked_categories or categories_in(text)
        if len(found) == 1:
            requirement.update(kind="HONOR_FROM_CATEGORY", target_category_slug=found[0])
        elif found:
            notes.append("una especialidad de: " + " o ".join(found)
                         + " (el esquema admite una sola categoría por requisito)")
        else:
            requirement.update(kind="HONOR_ANY")
    if requirement.get("kind", "TEXT") == "TEXT":
        named = [t for t in NAMED.findall(text) if t not in honors] if not honors else []
        if re.search(r"one of the following honou?rs", text, re.I) and not honors:
            options = [re.sub(r"\s+honou?r$", "", i.strip(), flags=re.I) for i in en["sub_items"]]
            notes.append("a elegir entre: " + " | ".join(catalogue.honor(t) or f"?{t}" for t in options)
                         + " (el esquema admite una sola especialidad por requisito)")
        mentioned = [catalogue.honor(t) or f"?{t}" for t in honors + named]
        if mentioned and not any(n.startswith(("a elegir", "la especialidad", "especialidad sin")) for n in notes):
            notes.append("menciona especialidades: " + ", ".join(mentioned))
    skill = SKILL.search(text)
    if skill and requirement.get("kind", "TEXT") != "TEXT":
        level = re.sub(r"\s+or\s+", " o ", skill.group(1))
        notes.append(f"la wiki pide nivel de destreza {level} (el esquema no lo valida)")
    if notes:
        requirement["notes"] = "; ".join(notes)


# ----------------------------------------------------------------------------
# Programs
# ----------------------------------------------------------------------------
def _requirement(position, roman, row_en, row_es, url_en, url_es):
    requirement = {
        "position": position,
        "label": f"{roman}.{row_en['label']}",
        "wiki_label": row_en["label"],
        "kind": "TEXT",
        "evidence_required": False,
        "text": {"en": row_en["text"]},
        "sub_items": {"en": row_en["sub_items"]},
        "source_url": {"en": url_en},
    }
    if row_es:
        requirement["text"]["es"] = row_es["text"]
        requirement["sub_items"]["es"] = row_es["sub_items"]
        requirement["source_url"]["es"] = url_es
    return requirement


def build_class(raw, wiki_name, slug, code, sort_order, catalogue, report):
    base = f"Investiture_Achievement/{wiki_name}"
    class_en, class_es = read(raw, base), read(raw, base + "/es")
    names = {"en": title_parts(class_en)[-1] if class_en else wiki_name}
    if class_es:
        names["es"] = title_parts(class_es)[-1]
    basic = {"sections": [], "names": names}
    advanced = {"sections": [], "names": {}}
    for index, (page, section_slug) in enumerate(CLASS_SECTIONS):
        path = f"{base}/{page}"
        en_page, es_page = read(raw, path), read(raw, path + "/es")
        if en_page is None:
            report["missing_pages"].append(path)
            continue
        if es_page is None:
            report["missing_pages"].append(path + "/es")
        es_ok = es_page is not None
        section_names = {"en": title_parts(en_page)[-1]}
        if es_ok:
            section_names["es"] = title_parts(es_page)[-1]
        en_rows = rows_of(en_page)
        es_rows = {(r["label"], r["advanced"]): r for r in translated_rows(en_rows, es_page, report, path)}
        if advanced_heading(en_page):
            advanced["names"].setdefault("en", advanced_heading(en_page))
        if es_ok and advanced_heading(es_page):
            advanced["names"].setdefault("es", advanced_heading(es_page))
        for level, target in (("basic", basic), ("advanced", advanced)):
            rows = [r for r in en_rows if r["advanced"] == (level == "advanced")]
            if not rows:
                continue
            target["sections"].append({
                "slug": section_slug, "roman": ROMAN[index], "names": section_names,
                "source_url": {"en": page_url(path), **({"es": page_url(path + "/es")} if es_ok else {})},
                "rows": [(r, es_rows.get((r["label"], r["advanced"]))) for r in rows],
            })
            for r in rows:
                if es_ok and (r["label"], r["advanced"]) not in es_rows:
                    report["es_label_missing"].append(f"{path}/es #{r['label']}")
    programs = []
    for level, shape in (("BASIC", basic), ("ADVANCED", advanced)):
        if not shape["sections"]:
            continue
        adv_names = shape["names"]
        program = {
            "kind": "CLASS", "ministry": "pathfinders",
            "slug": slug if level == "BASIC" else ADVANCED_SLUGS.get(slug) or slugify(adv_names.get("es") or adv_names["en"]),
            "code": code if level == "BASIC" else f"{code}-AV",
            "level": level,
            "sort_order": sort_order if level == "BASIC" else 10 + sort_order,
            "authority": "NAD", "issuer_level": "CLUB",
            "names": shape["names"] if level == "BASIC" else adv_names,
            "description": {},
            "source": SOURCE, "license": LICENSE,
            "source_url": {"en": page_url(base), **({"es": page_url(base + "/es")} if class_es else {})},
            "sections": [],
        }
        if level == "ADVANCED":
            program["base_program_slug"] = slug
        position, prerequisite = 0, None
        for section in shape["sections"]:
            out = {"slug": section["slug"], "position": len(program["sections"]) + 1,
                   "names": section["names"], "requirements": []}
            for row_en, row_es in section["rows"]:
                is_prereq = level == "ADVANCED" and re.match(
                    r"^complete\s+\w+\s+requirements\.?$", row_en["text"].strip(), re.I)
                if is_prereq and prerequisite is not None:
                    prerequisite.setdefault("_dropped", []).append(f"{section['roman']}.{row_en['label']}")
                    continue
                position += 1
                requirement = _requirement(position, section["roman"], row_en, row_es,
                                           section["source_url"]["en"], section["source_url"].get("es"))
                if is_prereq:
                    requirement.update(kind="PROGRAM", target_program_slug=slug)
                    prerequisite = requirement
                else:
                    annotate(requirement, row_en, catalogue)
                out["requirements"].append(requirement)
            if out["requirements"]:
                program["sections"].append(out)
        if prerequisite is not None and prerequisite.get("_dropped"):
            dropped = prerequisite.pop("_dropped")
            prerequisite["notes"] = (
                "la wiki repite «Complete … requirements» en cada sección del nivel avanzado;"
                f" se conserva una sola vez (omitidos: {', '.join(dropped)})")
        programs.append(program)
    return programs


def build_master_guide(raw, catalogue, report):
    index_en, index_es = read(raw, "Master_Guide"), read(raw, "Master_Guide/es")
    program = {
        "kind": "MASTER_GUIDE", "ministry": "master-guides", "slug": "guia-mayor", "code": "GUIA-MAYOR",
        "level": None, "sort_order": 1, "authority": "GC", "issuer_level": "ASSOCIATION",
        "names": {"en": title_parts(index_en)[-1] if index_en else "Master Guide"},
        "description": {"en": intro(index_en)},
        "source": SOURCE, "license": LICENSE,
        "source_url": {"en": page_url("Master_Guide")},
        "sections": [],
    }
    if index_es:
        program["names"]["es"] = title_parts(index_es)[-1]
        program["description"]["es"] = intro(index_es)
        program["source_url"]["es"] = page_url("Master_Guide/es")
    position = 0
    for index, (page, section_slug) in enumerate(MG_SECTIONS):
        path = f"Master_Guide/{page}"
        en_page, es_page = read(raw, path), read(raw, path + "/es")
        if en_page is None:
            report["missing_pages"].append(path)
            continue
        es_ok = es_page is not None
        if es_page is None:
            report["missing_pages"].append(path + "/es")
        en_rows = rows_of(en_page)
        es_rows = {r["label"]: r for r in translated_rows(en_rows, es_page, report, path)}
        names = {"en": title_parts(en_page)[-1]}
        if es_ok:
            names["es"] = title_parts(es_page)[-1]
        section = {"slug": section_slug, "position": len(program["sections"]) + 1, "names": names,
                   "requirements": []}
        for row in en_rows:
            position += 1
            if es_ok and row["label"] not in es_rows:
                report["es_label_missing"].append(f"{path}/es #{row['label']}")
            requirement = _requirement(position, ROMAN[index], row, es_rows.get(row["label"]),
                                       page_url(path), page_url(path + "/es"))
            annotate(requirement, row, catalogue)
            section["requirements"].append(requirement)
        if section["requirements"]:
            program["sections"].append(section)
        else:
            report["sections_without_requirements"].append(path)
    return program


def build(raw: pathlib.Path, links_file: pathlib.Path) -> tuple[dict, dict]:
    catalogue = Catalogue(links_file)
    report = {"missing_pages": [], "partial_translations": [], "es_label_missing": [],
              "sections_without_requirements": []}
    programs = []
    for sort_order, (wiki_name, slug, code) in enumerate(CLASSES, start=1):
        programs += build_class(raw, wiki_name, slug, code, sort_order, catalogue, report)
    programs.append(build_master_guide(raw, catalogue, report))
    data = {
        "source": SOURCE,
        "license": LICENSE,
        "attribution": "Pathfinder Wiki (North American Division Youth & Young Adult Ministries),"
                       " https://wiki.pathfindersonline.org",
        "retrieved": datetime.date.today().isoformat(),
        "generated_by": "migrations/catalog_tools/build_ay_classes.py",
        "note": "Classes = NAD Investiture Achievement (2011+), authority NAD; the GC/IAD cards differ"
                " (decision D2: import as DRAFT, compare with the manual in force, then publish)."
                " Requirement kinds and targets are annotations to be reviewed (see notes).",
        "programs": programs,
    }
    return data, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("raw", nargs="?", default=str(pathlib.Path.home() / "adventist-wiki" / "classes" / "raw"))
    parser.add_argument("--links", default=str(DATA / "wiki_honor_links.json"))
    parser.add_argument("--out", default=str(DATA / "ay_classes.json"))
    parser.add_argument("--report", default=None, help="also write the build report as JSON here")
    args = parser.parse_args()
    data, report = build(pathlib.Path(args.raw).expanduser(), pathlib.Path(args.links))
    pathlib.Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.report:
        pathlib.Path(args.report).expanduser().write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for program in data["programs"]:
        reqs = [r for s in program["sections"] for r in s["requirements"]]
        es = sum(1 for r in reqs if r["text"].get("es"))
        kinds = {}
        for r in reqs:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        print(f"{program['slug']:28} {program['names'].get('en', '')!s:18} / {program['names'].get('es', '')!s:26}"
              f" secciones={len(program['sections'])} requisitos={len(reqs)} es={es} {kinds}")
    for key, values in report.items():
        if values:
            print(f"{key}: {values}")


if __name__ == "__main__":
    main()
