"""Spanish requirements of the 34 Spiritual Adventurer awards from mundoja.org (DIA volunteers,
«nuevo currículo»), for import_adventurer_awards.py.

    python catalog_tools/fetch_mundoja_awards.py [--cache ~/Documents/DEEL/aventureros/mundoja-fichas]
                                                 [--out data/adventurer_awards_mundoja_es.json] [--offline]

Reads `data/adventurer_awards_mundoja_map.csv` (slug -> ficha_url, name_es), downloads every
ficha once into --cache (the site answers 403 without browser headers, so a Chrome User-Agent,
`Accept: text/html` and the awards page as Referer are sent) and writes, per award, the name
exactly as mundoja writes it and the numbered requirements of the «Requisitos» tab as they
stand, sub-items included (a nested <ol> keeps its letters, a <ul> becomes «•»), whether the site
writes them as an <ol> or as «1.…<br>» paragraphs. The
«Ayuda» tab (instructor help) is kept per requirement as `instructor_notes_es` when it is a
numbered list. `--offline` only parses what is already cached.

Text: mundoja.org volunteers, no explicit licence: rows keep source='mundoja.org', the ficha as
source_url and license NULL. The texts are copied, never edited here.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
import time
import unicodedata
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
MAP_CSV = BACKEND / "data" / "adventurer_awards_mundoja_map.csv"
DEFAULT_OUT = BACKEND / "data" / "adventurer_awards_mundoja_es.json"
DEFAULT_CACHE = pathlib.Path.home() / "Documents" / "DEEL" / "aventureros" / "mundoja-fichas"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://mundoja.org/especialidades/aventureros",
}


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace(" ", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str, path: pathlib.Path, offline: bool) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    if offline:
        raise SystemExit(f"--offline y no está en caché: {url}")
    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                html = response.read().decode("utf-8", errors="replace")
            break
        except (TimeoutError, OSError):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    path.write_text(html, encoding="utf-8")
    time.sleep(1.0)
    return html


ROMAN = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]


def _marker(tag, n: int) -> str:
    """Marker of the n-th <li> of a sub-list: letters unless the list says otherwise."""
    if tag.name == "ul":
        return "•"
    style = (tag.get("style") or "").lower() + " " + (tag.get("type") or "")
    if "roman" in style or tag.get("type") == "i":
        return ROMAN[n] + "."
    if "decimal" in style or tag.get("type") == "1":
        return f"{n + 1})"
    if "upper-alpha" in style or tag.get("type") == "A":
        return chr(ord("A") + n) + "."
    return chr(ord("a") + n) + "."


def _li_text(li) -> tuple[str, list]:
    nested = li.find_all(["ol", "ul"], recursive=False)
    for child in nested:
        child.extract()
    return clean(li.get_text(" ")), nested


def _sub_lines(tag, depth: int = 0) -> list[str]:
    out = []
    for n, li in enumerate(tag.find_all("li", recursive=False)):
        text, nested = _li_text(li)
        out.append("  " * (depth + 1) + f"{_marker(tag, n)} {text}")
        for child in nested:
            out.extend(_sub_lines(child, depth + 1))
    return out


def linearize(pane) -> list[str]:
    """A tab as numbered lines, whatever its markup: «<p>1. …</p>», «1.…<br>2.…», an <ol> of
    requirements, or <p> requirements with an <ol> of sub-items after one of them. The first
    list met before any numbered line is the list of requirements (its <li> become «N. …»);
    a list after a numbered line holds that requirement's sub-items («a. …»)."""
    lines: list[str] = []

    def numbered_so_far() -> bool:
        return any(NUMBER_RE.match(line) for line in lines)

    def walk(node) -> None:
        for child in node.children:
            name = getattr(child, "name", None)
            if name is None:
                text = clean(str(child))
                if text:
                    lines.append(text)
            elif name in ("ol", "ul"):
                if not numbered_so_far() and name == "ol":
                    for n, li in enumerate(child.find_all("li", recursive=False), start=1):
                        text, nested = _li_text(li)
                        lines.append(f"{n}. {text}")
                        for sub in nested:
                            lines.extend(_sub_lines(sub))
                else:
                    lines.extend(_sub_lines(child))
            elif name == "br":
                lines.append("\n")
            elif name in ("p", "h3", "h4", "h5", "strong", "em", "span", "b", "i", "a", "u"):
                has_block = child.find(["ol", "ul", "p", "div"])
                if has_block:
                    walk(child)
                    continue
                for br in child.find_all("br"):
                    br.replace_with("\n")
                lines.extend(clean(part) for part in child.get_text("").split("\n") if clean(part))
            else:
                walk(child)

    walk(pane)
    return [line for line in lines if line and line != "\n"]


NUMBER_RE = re.compile(r"^(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*[.)]\s*(.*)$")
SUB_RE = re.compile(r"^([a-z]|[ivx]{1,4})\s*[.)]\s*(.+)$|^([•·\-])\s*(.+)$")
INDENTED_RE = re.compile(r"^ +\S")


def numbered_lines(lines: list[str]) -> tuple[list[dict], list[str]]:
    """«1.», «a.» lines → [{position, last, text, sub}] (the same rules as the PDF extractor:
    a number counts only when it is the next one; a plain line after the sub-items closes the
    requirement; any other plain line continues the text above)."""
    items: list[dict] = []
    before: list[str] = []
    for line in lines:
        number = NUMBER_RE.match(line)
        expected = items[-1]["last"] + 1 if items else 1
        if number and int(number.group(1)) == expected and number.group(3):
            last = int(number.group(2) or number.group(1))
            items.append({"position": expected, "last": last, "text": clean(number.group(3)), "sub": []})
            continue
        if not items:
            before.append(line)
            continue
        item = items[-1]
        if INDENTED_RE.match(line):        # a sub-item line already built by linearize()
            item["sub"].append(line)
            continue
        sub = SUB_RE.match(line)
        if sub:
            marker = (sub.group(1) + ".") if sub.group(1) else "•"
            item["sub"].append(f"  {marker} {clean(sub.group(2) or sub.group(4))}")
        elif item["sub"]:
            item["sub"].append(line)
        else:
            item["text"] = f"{item['text']} {line}"
    return items, before


def parse(html: str) -> tuple[list[dict], dict[int, str], str | None]:
    """(requirements, notes by position, note text outside the numbered help)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    panes = soup.select("div.sppb-tab-pane")
    labels = [clean(a.get_text(" ")).lower() for a in soup.select("ul.sppb-nav li a")]
    by_label = dict(zip(labels, panes))
    req_pane = by_label.get("requisitos") or (panes[0] if panes else None)
    if req_pane is None:
        return [], {}, None
    items, _ = numbered_lines(linearize(req_pane))
    requirements = [{"position": i["position"], "text_es": i["text"], "sub": i["sub"]} for i in items]
    notes: dict[int, str] = {}
    extra = None
    help_pane = by_label.get("ayuda")
    if help_pane is not None:
        items, rest = numbered_lines(linearize(help_pane))
        for item in items:
            for position in range(item["position"], item["last"] + 1):
                notes[position] = "\n".join([item["text"], *item["sub"]]).strip()
        rest = [r for r in rest if r and "puedes contactarnos" not in r and "correo" not in r.lower()]
        extra = "\n".join(rest) or None
    return requirements, notes, extra


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    cache = pathlib.Path(args.cache).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    with MAP_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    awards = {}
    for row in rows:
        url = row["ficha_url"]
        html = fetch(url, cache / (url.rstrip("/").rsplit("/", 1)[-1] + ".html"), args.offline)
        requirements, notes, extra = parse(html)
        if not requirements:
            print(f"AVISO {row['slug']}: sin requisitos en {url}", file=sys.stderr)
        for requirement in requirements:
            requirement["instructor_notes_es"] = notes.get(requirement["position"])
        awards[row["slug"]] = {
            "name_es": row["name_es"],
            "source": "mundoja.org",
            "source_url": url,
            "license": None,
            "requirements": requirements,
            "general_notes_es": extra,
        }
        print(f"{row['slug']}: {len(requirements)} requisitos, {len(notes)} notas")
    out = pathlib.Path(args.out)
    out.write_text(json.dumps({
        "source": "mundoja.org",
        "note": "Requisitos en español de mundoja.org (voluntarios de Mundo J.A.), copiados tal cual; sin licencia explícita.",
        "fetched_by": "backend/migrations/catalog_tools/fetch_mundoja_awards.py",
        "awards": awards,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(awards)} fichas → {out}")


if __name__ == "__main__":
    main()
