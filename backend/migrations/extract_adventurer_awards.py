"""Extract the requirements (and the instructor's «Supporting Answers») of the 164 Adventurer
awards from the *Adventurer Award Book 2020* (GC Youth Ministries) into
`data/adventurer_awards.json`, the English half of what import_adventurer_awards.py loads.

    pip install pymupdf        # requirements-tools.txt; not installed on Render
    python extract_adventurer_awards.py [--pdf "~/Documents/DEEL/aventureros/Award Book 2020.pdf"]
                                        [--out data/adventurer_awards.json] [--only slug,slug]

The awards, their order and their first page come from the patch-library manifest
(`data/adventurer_awards_library.csv`, 164 rows). An award runs from its page to the page before
the next award; inside that range:

  * the title is the big type (≥ 24 pt) at the top of the first page (not read as text);
  * «Requirements» (18 pt heading) starts the numbered list; «Supporting Answers» starts the
    instructor's guide, which ends the list; «Updated in: …» is kept apart as `updated_in`;
  * «Page N» footers, empty pages, the category tables («Title / Class / Page») and stray pages
    that repeat another award's «Requirements» (p. 186) are skipped;
  * a line «N.» (or «N)») opens requirement N only when N is the next number expected, so an
    inner list that restarts at 1 stays inside its requirement; «a.», «b)», «i.», «•», «-»
    lines are sub-items; any other line continues the item above it (wrapped text);
  * sub-items are stored with their marker («  a. moon rise», «  • Draw …»), one per line after
    the requirement text and indented two spaces per level, exactly like
    `honor_requirements.description` stores them for Pathfinder honours (the honour sheet renders
    that form: see app/services/honor_sheet.parse_requirement).

«Supporting Answers» are split the same way: text before the first number is the award's general
note (`general_notes_en`), «N.» paragraphs become `instructor_notes_en` of requirement N (the
book numbers them like the requirements in almost every award). Awards whose guide does not
follow the numbering keep it whole in `general_notes_en`.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
LIBRARY_CSV = BACKEND / "data" / "adventurer_awards_library.csv"
DEFAULT_OUT = BACKEND / "data" / "adventurer_awards.json"
DEFAULT_PDF = pathlib.Path.home() / "Documents" / "DEEL" / "aventureros" / "Award Book 2020.pdf"
BOOK = "Adventurer Award Book 2020"
BOOK_URL = "https://www.gcyouthministries.org/ministries/adventurers/"

TITLE_SIZE = 24.0
FOOTER_Y = 735.0
CLASS_NAMES = {"Little Lamb", "Early Bird", "Busy Bee", "Sunbeam", "Builder", "Helping Hand",
               "Helping Hands", "Multi-level"}

NUMBER_RE = re.compile(r"^\s*(\d{1,2})(\s*[-–&,]\s*\d{1,2})?\s*([.)])\s*(.*)$")
SUB_RE = re.compile(r"^\s*(\(?(?:[a-zA-Z]|[ivx]{1,4})[.)]|[•\-–▪◦·*o_])\s+(.*)$")
LEAK_RE = re.compile(r"(?:^|(?<=\s))(?:Answers?(?: for #\d+)?|Idea for #\d+|Suggestion for #\d+)\s*:")
UPDATED_RE = re.compile(r"^\s*updated\s+in\s*:?\s*(.*)$", re.I)
PAGE_RE = re.compile(r"^\s*page\s+\d+\s*$", re.I)


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\t", " ").replace(" ", " ")
    text = text.replace("​", "").replace("﻿", "")
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------------------------------------------------------
# Reading the pages
# ----------------------------------------------------------------------------
def page_lines(page) -> tuple[list[dict], str]:
    """Visual lines of a page in reading order: [{x, y, size, text}], and the page title."""
    lines, title = [], []
    for block in page.get_text("dict", sort=True)["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            raw = "".join(s["text"] for s in line["spans"])
            size = max(s["size"] for s in spans)
            x, y = line["bbox"][0], line["bbox"][1]
            if size >= TITLE_SIZE:
                title.append(clean(raw))
                continue
            if y > FOOTER_Y and PAGE_RE.match(clean(raw)):
                continue
            if PAGE_RE.match(clean(raw)):
                continue
            # MuPDF sometimes puts the marker's tab inside the first span: keep a single space.
            lines.append({"x": round(x, 1), "y": round(y, 1), "size": round(size, 1), "text": raw})
    return lines, " ".join(title).strip()


def is_table_page(lines: list[dict]) -> bool:
    texts = [clean(l["text"]) for l in lines]
    if {"Title", "Class", "Page"} <= set(texts):
        return True
    return sum(1 for t in texts if t in CLASS_NAMES) >= 5


def heading(line: dict) -> str | None:
    text = clean(line["text"]).rstrip(":").lower()
    if line["size"] >= 16 and text in ("requirements", "supporting answers"):
        return text
    if text in ("requirements", "supporting answers", "supporting answer"):
        return "supporting answers" if text.startswith("supporting") else text
    return None


# ----------------------------------------------------------------------------
# Requirements: numbered items with their sub-items
# ----------------------------------------------------------------------------
def split_requirements(lines: list[dict]) -> tuple[list[str], list[dict]]:
    """Returns (lines before «1.», items). Each item: {position, x, head: [..], sub: [[marker,
    text, level], ..]}; wrapped lines are joined to the part they continue.

    A number opens an item only when it is the next one expected. Requirement 1 printed without
    its «1.» (Technology, p. 177) is rebuilt from the lines before «2.». A sub-item drawn further
    right than the first one of its item (same page), or a roman «i.» that is not the letter
    after the previous one («d. … i. ii.»), is one level deeper. A plain line back at the
    requirement's margin after its list is a closing sentence («Memorize and repeat two of them.»).
    """
    preamble: list[dict] = []
    items: list[dict] = []

    def feed(item: dict, line: dict) -> None:
        sub = SUB_RE.match(line["text"])
        if sub and clean(sub.group(2)):
            marker = sub.group(1)
            if marker in ("o", "_"):
                marker = "•"
            letters = [m for m, _t, lvl in item["sub"] if lvl == 1 and re.fullmatch(r"[a-zA-Z][.)]", m)]
            if marker == "I." and letters and letters[-1] == "k.":
                marker = "l."          # «I.  Diskette» after «k.» is a misprinted «l.»
            level, bare = 1, marker.strip("().")
            if not item["sub"]:
                item["sub_at"] = (line["page"], line["x"])
            elif line["page"] == item["sub_at"][0] and line["x"] > item["sub_at"][1] + 8:
                level = 2
            elif re.fullmatch(r"[ivx]{1,4}", bare) and letters:
                previous = letters[-1].strip("().").lower()
                if not (len(bare) == 1 and ord(bare) == ord(previous) + 1):
                    level = 2
            item["sub"].append([marker, clean(sub.group(2)), level])
            return
        body = clean(line["text"])
        if not body:
            return
        if not item["head"]:
            item["head"].append(body)
        elif item["sub"] and line["page"] == item["page"] and line["x"] <= item["x"] + 2:
            item["sub"].append(["", body, 0])
        elif item["sub"]:
            item["sub"][-1][1] = (item["sub"][-1][1] + " " + body).strip()
        else:
            item["head"].append(body)

    for line in lines:
        number = NUMBER_RE.match(line["text"])
        if number and not number.group(2):
            n, body = int(number.group(1)), clean(number.group(4))
            expected = len(items) + 1
            if n == 2 and not items and preamble:
                first = {"position": 1, "x": preamble[0]["x"], "page": preamble[0]["page"], "head": [], "sub": []}
                for pre in preamble:
                    feed(first, pre)
                items.append(first)
                preamble, expected = [], 2
            if n == expected:
                items.append({"position": n, "x": line["x"], "page": line["page"],
                              "head": [body] if body else [], "sub": []})
                continue
        if not items:
            preamble.append(line)
            continue
        feed(items[-1], line)
    return [clean(l["text"]) for l in preamble], items


def _join(parts: list[str]) -> str:
    text = " ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", text).strip()


def sub_line(marker: str, text: str, level: int) -> str:
    """One stored sub-item line, indented two spaces per level (the form the Pathfinder wiki
    import stores and app/services/honor_sheet.parse_requirement nests by); a closing sentence
    has neither marker nor indent."""
    if level == 0 or not marker:
        return text
    return "  " * level + f"{marker} {text}"


# ----------------------------------------------------------------------------
# Instructor notes («Supporting Answers»): numbered paragraphs, free text inside
# ----------------------------------------------------------------------------
def flow(texts: list[str]) -> str:
    """Visual lines back into paragraphs: a line joins the one above unless it starts with a
    marker, or the line above ended a paragraph (ends with «:» or is clearly shorter than a
    full line)."""
    out: list[str] = []
    last_raw = ""
    for raw in texts:
        text = clean(raw)
        if not text:
            continue
        marker = SUB_RE.match(text) or NUMBER_RE.match(text)
        breaks = not out or marker or last_raw.endswith(":") or len(last_raw) < 55
        if breaks:
            out.append(text)
        else:
            out[-1] = f"{out[-1]} {text}"
        last_raw = text
    return "\n".join(out)


def split_notes(lines: list[dict]) -> tuple[str | None, dict[int, str], list[str]]:
    """(general note, {position: note}, problems). «N.» opens the note of requirement N when N
    is the next one expected; «1-2.» covers both. Inside a note, a «1.» (or a number in another
    style, «1)» against «1.») opens a list of that note — recipes, steps — and its numbers stay
    inside while they follow on, even when one happens to be the next number expected."""
    general: list[str] = []
    notes: list[dict] = []
    style = None
    inner_next = None
    for line in lines:
        text = line["text"]
        number = NUMBER_RE.match(text)
        if number and number.group(4) and not re.match(r"^\s*\d{1,2}(\s*[-–&,]\s*\d{1,2})?\s*[.)]\s", text):
            number = None              # «8.Search online…»: a misprint inside the note above
        if number:
            n = int(number.group(1))
            last = int(number.group(2).strip(" -–&,")) if number.group(2) else n
            punct = number.group(3)
            expected = notes[-1]["last"] + 1 if notes else 1
            inner = bool(notes) and (punct != style or n == inner_next or (n == 1 and expected != 1))
            if not inner and n == expected:
                style = style or punct
                inner_next = None
                notes.append({"position": n, "last": last, "texts": [number.group(4)]})
                continue
            if notes:
                inner_next = n + 1
        if notes:
            notes[-1]["texts"].append(text)
        else:
            general.append(text)
    by_position: dict[int, str] = {}
    for note in notes:
        body = flow(note["texts"])
        for position in range(note["position"], note["last"] + 1):
            by_position[position] = body
    return flow(general) or None, by_position, []


def split_leaked_answers(lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """A few awards print the answer to a requirement right under the list («Answer for #3:»,
    «Answers: …», «Idea for #8: …»): from there on it is instructor text, not a requirement."""
    for n, line in enumerate(lines):
        match = LEAK_RE.search(line["text"])
        if not match:
            continue
        before = line["text"][:match.start()]
        kept = lines[:n] + ([{**line, "text": before}] if clean(before) else [])
        leaked = [{**line, "text": line["text"][match.start():]}] + lines[n + 1:]
        return kept, leaked
    return lines, []


# ----------------------------------------------------------------------------
# One award
# ----------------------------------------------------------------------------
def extract_award(doc, row: dict, last_page: int) -> dict:
    first = int(row["page"])
    warnings: list[str] = []
    req_lines: list[dict] = []
    ans_lines: list[dict] = []
    updated: list[str] = []
    pages_used: list[int] = []
    title = ""
    state = "before"
    for pno in range(first, last_page + 1):
        lines, page_title = page_lines(doc[pno - 1])
        for line in lines:
            line["page"] = pno
        if pno == first:
            title = page_title
        if not lines:
            continue
        if is_table_page(lines):
            continue
        heads = [heading(l) for l in lines]
        if pno != first and "requirements" in heads:
            warnings.append(f"página {pno} repite una lista «Requirements» (se ignora)")
            continue
        pages_used.append(pno)
        for line, head in zip(lines, heads):
            if head == "requirements":
                state = "requirements" if state == "before" else state
                continue
            if head == "supporting answers":
                state = "answers"
                continue
            updated_match = UPDATED_RE.match(clean(line["text"]))
            if updated_match:
                updated.append(clean(updated_match.group(1)))
                continue
            if state == "requirements":
                req_lines.append(line)
            elif state == "answers":
                ans_lines.append(line)
            elif clean(line["text"]):
                warnings.append(f"texto antes de «Requirements» en p. {pno}: {clean(line['text'])[:60]}")

    req_lines, leaked = split_leaked_answers(req_lines)
    preamble, items = split_requirements(req_lines)
    requirements = [{
        "position": item["position"],
        "text_en": _join(item["head"]),
        "sub": [sub_line(m, t, lvl) for m, t, lvl in item["sub"]],
        "instructor_notes_en": None,
    } for item in items]
    intro = _join(preamble) or None

    general_notes, by_position, _ = split_notes(ans_lines)
    notes_numbered = bool(by_position) and max(by_position) <= len(requirements)
    if notes_numbered:
        for requirement in requirements:
            requirement["instructor_notes_en"] = by_position.get(requirement["position"])
    elif by_position:
        warnings.append("las «Supporting Answers» no siguen la numeración: se guardan enteras en general_notes_en")
        general_notes = flow([l["text"] for l in ans_lines])
    if leaked:
        general_notes = "\n".join(p for p in (general_notes, flow([l["text"] for l in leaked])) if p)
        warnings.append("respuestas impresas bajo los requisitos: van a general_notes_en")
    if not ans_lines:
        warnings.append("sin «Supporting Answers»")
    if not requirements:
        warnings.append("sin requisitos")

    return {
        "slug": row["slug"],
        "title_en": row["name_en"],
        "title_on_page": title,
        "formerly_en": row.get("formerly_en") or None,
        "category_en": row["category_en"],
        "category_es": row["category_es"],
        "class_en": row["class_en"],
        "class_es": row["class_es"],
        "page": first,
        "pages": pages_used,
        "intro_en": intro,
        "requirements": requirements,
        "general_notes_en": general_notes,
        "instructor_notes_numbered": notes_numbered,
        "updated_in": "; ".join(u for u in updated if u) or None,
        "warnings": warnings,
    }


def read_manifest(path: pathlib.Path = LIBRARY_CSV) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return sorted(rows, key=lambda r: int(r["page"]))


def extract(pdf: pathlib.Path, only: set[str] | None = None) -> dict:
    import pymupdf

    doc = pymupdf.open(str(pdf))
    rows = read_manifest()
    awards = []
    for n, row in enumerate(rows):
        last = int(rows[n + 1]["page"]) - 1 if n + 1 < len(rows) else doc.page_count
        if only and row["slug"] not in only:
            continue
        awards.append(extract_award(doc, row, last))
    return {
        "source": "gc-award-book-2020",
        "book": BOOK,
        "source_url": BOOK_URL,
        "license": "© GC Youth Ministries, permiso pendiente",
        "extracted_by": "backend/migrations/extract_adventurer_awards.py",
        "awards": awards,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--only", help="comma-separated slugs (prints them, does not write)")
    args = parser.parse_args()
    pdf = pathlib.Path(args.pdf).expanduser()
    if not pdf.exists():
        sys.exit(f"No existe el PDF: {pdf}")
    only = set(args.only.split(",")) if args.only else None
    data = extract(pdf, only)
    if only:
        print(json.dumps(data["awards"], ensure_ascii=False, indent=1))
        return
    out = pathlib.Path(args.out)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    total = sum(len(a["requirements"]) for a in data["awards"])
    warned = [a for a in data["awards"] if a["warnings"]]
    print(f"{len(data['awards'])} awards, {total} requisitos → {out}")
    for award in warned:
        print(f"  {award['slug']} (p. {award['page']}): {'; '.join(award['warnings'])}")


if __name__ == "__main__":
    main()
