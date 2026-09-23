"""Turn a downloaded class section page of the Pathfinder Wiki into its requirements.

Pages: `Investiture_Achievement/<Class>/<Section>[/es]` (the Pathfinder classes) and
`Master_Guide/<Section>[/es]`. Unlike the honour pages (`AY_Honors/<title>/Requirements`, see
parse_wiki_requirements.py) every requirement is drawn by a template as a small table:

    <td bgcolor="#8080e0"> Requirement 2a | Requisito 2a | 2a </td>   <- the label cell
    <td bgcolor="#c0c0ff"> the requirement text, with <ul>/<dl> sub-items </td>

followed by the instructor's notes (never imported). Two templates exist (the newer one wraps
the table in `class="ansreq-header"` and puts the label in its own column), but both paint the
label cell #8080e0 and the text cell #c0c0ff, which is what this parser keys on.

On the class pages an <h1> carrying the ribbon ("Trail Friend", "Amigo de la Naturaleza")
opens the requirements of the ADVANCED level; everything after it is flagged `advanced` (a few
pages forget that heading: the level then starts at «Complete <Class> requirements»).
The sub-items are read with the same line reader as the honour parser: one line per block,
indented two spaces per <ul>/<dl> level.
"""
from __future__ import annotations

import html
import re
import sys
import urllib.parse

from parse_wiki_requirements import _Lines, translation_progress  # noqa: F401  (re-exported)

LABEL_CELL = re.compile(r'<td\b[^>]*bgcolor="#8080e0"[^>]*>', re.I)
TEXT_CELL = re.compile(r'<td\b[^>]*bgcolor="#?c0c0ff"[^>]*>', re.I)
LABEL = re.compile(r"(\d{1,2}\s*[a-z]?)\s*$", re.I)
HONOR_LINK = re.compile(r'href="/w/AY_Honors/([^"#?]+)"')
HEADER = re.compile(r'<th><font[^>]*><b>([^<]+)</b></font>', re.S)
ADVANCED_START = re.compile(
    r"^(?:complete\s+\w+\s+requirements|completar\s+los\s+requisitos\s+de\s+\w+)\.?$", re.I)


def content_area(page: str) -> str:
    start = page.find('class="mw-content-ltr mw-parser-output"')
    start = page.rfind("<", 0, start) if start > 0 else 0
    end = page.find('<div class="printfooter"', start)
    return page[start:end if end > 0 else None]


def _inner(page: str, open_match: re.Match, tag: str = "td") -> tuple[str, int]:
    """Inner HTML of the element opened at `open_match`, honouring nested elements of the same tag."""
    depth, pos = 1, open_match.end()
    tags = re.compile(rf"<(/?){tag}\b[^>]*>", re.I)
    while depth:
        found = tags.search(page, pos)
        if found is None:
            return page[open_match.end():], len(page)
        depth += -1 if found.group(1) else 1
        pos = found.end()
    return page[open_match.end():found.start()], pos


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _lines(fragment: str) -> list[tuple[int, str]]:
    reader = _Lines()
    reader.feed(fragment)
    reader._flush()
    return reader.lines


def title_parts(page: str) -> list[str]:
    """The translated page title, e.g. ['Logros para la Investidura', 'Amigo', 'Desarrollo personal']
    or ['Guía Mayor', 'Prerrequisitos']; falls back to the template header of the class pages."""
    found = re.search(r"<title>(.*?) - Pathfinder Wiki</title>", page, re.S)
    if not found:
        found = HEADER.search(content_area(page))
    return [clean(part) for part in found.group(1).split("/")] if found else []


def advanced_heading(page: str) -> str | None:
    found = re.search(r"<h1\b[^>]*>(.*?)</h1>", content_area(page), re.S)
    return clean(re.sub(r"<[^>]+>", "", found.group(1))) or None if found else None


def honor_links(fragment: str) -> list[str]:
    """Wiki titles of the AY_Honors pages linked from a requirement (honours AND categories)."""
    titles = []
    for raw in HONOR_LINK.findall(fragment):
        title = urllib.parse.unquote(html.unescape(raw)).replace("_", " ")
        title = re.sub(r"/(Requirements|es|en|fr|pt-br)$", "", title, flags=re.I)
        title = re.sub(r"/Requirements$", "", title, flags=re.I)  # ".../Requirements/es"
        if title not in titles:
            titles.append(title)
    return titles


def parse(page: str) -> list[dict]:
    """[{label, text, sub_items, description, honor_links, advanced}] in page order."""
    area = content_area(page)
    h1 = re.search(r"<h1\b", area)
    advanced_from = h1.start() if h1 else len(area)
    labels = list(LABEL_CELL.finditer(area))
    rows = []
    for n, label_open in enumerate(labels):
        label_html, after = _inner(area, label_open)
        label = LABEL.search(clean(re.sub(r"<[^>]+>", " ", label_html)))
        limit = labels[n + 1].start() if n + 1 < len(labels) else len(area)
        text_open = TEXT_CELL.search(area, after, limit)
        if label is None or text_open is None:
            continue
        text_html, _ = _inner(area, text_open)
        lines = _lines(text_html)
        if not lines:
            continue
        base = min(depth for depth, _ in lines)
        shaped = ["  " * (depth - base) + text for depth, text in lines]
        rows.append({
            "label": re.sub(r"\s+", "", label.group(1)).lower(),
            "text": lines[0][1] if lines[0][0] == base else shaped[0].strip(),
            "sub_items": shaped[1:],
            "description": "\n".join(shaped),
            "honor_links": honor_links(text_html),
            "advanced": label_open.start() > advanced_from,
        })
    if h1 is None:
        # A few pages forget the ribbon <h1>; the advanced level then starts at its first
        # requirement, «Complete <Class> requirements» / «Completar los requisitos de <Clase>».
        start = next((n for n, row in enumerate(rows) if ADVANCED_START.match(row["text"])), None)
        for row in rows[start:] if start is not None else []:
            row["advanced"] = True
    return rows


HEADING = re.compile(r"<h([2-5])\b[^>]*>(.*?)</h\1>", re.S)
TOP_HEADING = re.compile(r"^(\d{1,2})\s*[.)]\s*(.+)$", re.S)
SUB_HEADING = re.compile(r"^([a-z]|[ivx]{1,5}|\d{1,2})\s*[.)]\s*\S", re.I)


def parse_headings(page: str) -> list[dict]:
    """The Master Guide pages write each requirement as an <h2> "N. text" and its sub-items as
    deeper headings ("a. …" <h3>, "i. …" <h4>); the prose between them is instructor help.
    Same output shape as `parse` (never `advanced`)."""
    rows, current = [], None
    for found in HEADING.finditer(content_area(page)):
        level = int(found.group(1))
        inner = found.group(2)
        if 'id="mw-toc-heading"' in found.group(0):
            continue
        text = clean(re.sub(r"<[^>]+>", " ", inner))
        text = re.sub(r"\s+([.,;:])", r"\1", text)
        if level == 2:
            top = TOP_HEADING.match(text)
            current = None
            if top:
                current = {"label": top.group(1), "lines": [top.group(2).strip()], "html": [inner]}
                rows.append(current)
            continue
        if current is not None and SUB_HEADING.match(text):
            current["lines"].append("  " * (level - 2) + text)
            current["html"].append(inner)
    return [{
        "label": row["label"],
        "text": row["lines"][0],
        "sub_items": row["lines"][1:],
        "description": "\n".join(row["lines"]),
        "honor_links": honor_links(" ".join(row["html"])),
        "advanced": False,
    } for row in rows]


if __name__ == "__main__":
    for path in sys.argv[1:]:
        page = open(path, encoding="utf-8", errors="ignore").read()
        rows = parse(page) or parse_headings(page)
        print(f"== {path}: {title_parts(page)} advanced={advanced_heading(page)!r} {len(rows)} requisitos")
        for row in rows:
            flag = "A" if row["advanced"] else " "
            print(f"  {flag} {row['label']:>4} {row['description'][:150]!r} {row['honor_links'] or ''}")
