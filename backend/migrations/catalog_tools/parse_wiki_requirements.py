"""Turn a downloaded "AY Honors/<title>/Requirements[/lang]" page into numbered requirements.

The wiki writes each requirement as <p><b>N. text</b></p> and its sub-items as nested
<dl><dd><b>a. text</b></dd></dl>. One top-level number = one requirement; sub-items stay inside
its text, one per line, indented two spaces per level — the shape `honor_requirements` expects.
"""
from __future__ import annotations

import html, re, sys
from html.parser import HTMLParser

TOP = re.compile(r"^(\d{1,2})[.)](?:\s+(.*)|\s*)$", re.S)   # the text may come in the next block


class _Lines(HTMLParser):
    """Text lines of the requirement area with their <dl> nesting depth."""
    BLOCKS = {"p", "dd", "li", "div", "tr", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.skip, self.buffer, self.lines = 0, 0, [], []

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        if text:
            self.lines.append((self.depth, text))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "table", "sup") or (tag == "span" and ("class", "mw-editsection") in attrs):
            self.skip += 1
        if tag in self.BLOCKS or tag == "dl" or tag == "br":
            self._flush()
        if tag in ("dl", "ul", "ol"):
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "table", "sup") or (tag == "span" and self.skip):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCKS or tag in ("dl", "ul", "ol"):
            self._flush()
        if tag in ("dl", "ul", "ol"):
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data)


def requirement_area(page: str) -> str:
    # from the opening tag itself: text written directly inside the div (no <p>) must not swallow the attribute
    start = page.find('id="myTabContent"')
    if start < 0:
        start = page.find('class="mw-content-ltr mw-parser-output"')
    if start > 0:
        start = page.rfind("<", 0, start)
    end = page.find('<div class="printfooter"', start)
    return page[start:end if end > 0 else None]


def parse(page: str) -> list[dict]:
    parser = _Lines()
    parser.feed(requirement_area(page))
    parser._flush()
    requirements, current, base, section = [], None, None, None
    for depth, text in parser.lines:
        top = TOP.match(text)
        # a top-level requirement carries the next number and is never deeper than requirement 1
        if top and int(top.group(1)) == len(requirements) + 1 and (base is None or depth <= base):
            base = depth if base is None else base
            current = {"position": int(top.group(1)), "lines": [(top.group(2) or "").strip()], "section": section}
            requirements.append(current)
            section = None
        elif base is not None and depth < base:
            section = text          # unnumbered heading above the numbered level ("Sección Uno - TEÓRICO")
        elif current is not None and current["lines"] == [""]:
            current["lines"][0] = text           # "1." and its sentence were split into two paragraphs
        elif current is not None:
            current["lines"].append("  " * max(depth - base, 1) + text)
        elif len(text) < 80 and "myTabContent" not in text:
            section = text          # heading before requirement 1
    return [{"position": r["position"], "description": "\n".join(r["lines"]).strip(), "section": r["section"]}
            for r in requirements]


def translation_progress(page: str) -> int | None:
    found = re.search(r"translation is (\d+)% complete", page)
    return int(found.group(1)) if found else None


if __name__ == "__main__":
    for path in sys.argv[1:]:
        rows = parse(open(path, encoding="utf-8", errors="ignore").read())
        print(f"== {path}: {len(rows)} requisitos")
        for row in rows[:3]:
            print(f"  {row['position']}. {row['description'][:160]!r}")
