"""Shared by export_translation_sources.py and validate_requirement_translations.py.

Which list a translation starts from, and the `source`/`license` convention of the result:
an unofficial translation keeps the origin in its name — `traduccion-no-oficial-<origin>` — the
source_url of the page it was translated from and that page's licence (CC BY-SA 3.0 for the wiki:
a derivative must keep it).
"""
from __future__ import annotations

import pathlib
import re
from urllib.parse import quote

PREFIX = "traduccion-no-oficial-"
SOURCE_ORDER = ("en", "es")  # translate from the original (the wiki writes in English), then Spanish
WIKI = "https://wiki.pathfindersonline.org/w/AY_Honors/"
WIKI_LICENSE = "CC BY-SA 3.0"
WIKI_FOLDER = pathlib.Path("~/adventist-wiki/requirements").expanduser()
MARKER = re.compile(r"^(\s*)(\(?(?:[a-z]|[ivxl]{1,5}|\d{1,2})[.)])\s")


def language(locale: str) -> str:
    return locale.lower().split("-")[0]


def translated_source(source: str | None) -> str:
    """`source` of a translation made from a list whose source is `source`."""
    if source is None:
        return PREFIX + "instructor"
    if source.startswith(PREFIX):
        return source
    return (PREFIX + source)[:40]


def pick_source_locale(stored: list[str], target: str) -> str | None:
    candidates = [loc for loc in SOURCE_ORDER if loc in stored and language(loc) != language(target)]
    candidates += sorted(loc for loc in stored if loc not in candidates and language(loc) != language(target))
    return candidates[0] if candidates else None


def wiki_page_url(title: str) -> str:
    return WIKI + quote(title.replace(" ", "_"), safe="()_-.,'!")


def wiki_display_name(title: str) -> str:
    """The English honour name from its wiki title: without the default authority suffix « (GC)» and
    without the number the wiki puts in front of the doctrinal honours («04 - God the Son…»)."""
    return re.sub(r"^\d{2} - ", "", title).removesuffix(" (GC)")


def wiki_file_name(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9()._-]+", "_", title) + ".html"


def structure(text: str) -> list[str]:
    """The skeleton of a requirement: per line, its indentation and sub-item marker (a., i., 1.)
    or '·' for a line without one."""
    out = []
    for line in text.split("\n"):
        found = MARKER.match(line)
        out.append(f"{len(found.group(1))}{found.group(2)}" if found else f"{len(line) - len(line.lstrip())}·")
    return out


NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text: str) -> list[str]:
    """Digits of a text without its sub-item markers (so '1.' of a list does not count)."""
    body = "\n".join(MARKER.sub(r"\1", line) for line in text.split("\n"))
    return [n.replace(",", ".") for n in NUMBER.findall(body)]
