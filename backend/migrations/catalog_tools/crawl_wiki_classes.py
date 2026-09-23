"""Download, politely and resumably, the Pathfinder class and Master Guide pages of the wiki.

    python3 crawl_wiki_classes.py [Friend Companion … Master_Guide]   # default: everything

Cache: ~/adventist-wiki/classes/raw/<path with / -> __>.html (outside the repo); 404s are
remembered as .404 files; a cached page is never fetched again, so a rerun only fetches what
is missing. Log: ~/adventist-wiki/classes/crawl.log.

Where the pages are: `AY_Classes` does not exist (404). The index is
`Investiture_Achievement` (NAD classes, 2011+) and `Master_Guide`; each class page is a grid
of links to eight section pages (`Investiture_Achievement/<Class>/<Section>`), each Master Guide
tab is `Master_Guide/<Section>`; the Spanish page is the same path + `/es`.

Rules (docs/ESPECIALIDADES_WIKI.md, robots.txt): only normal content pages under
/w/Investiture_Achievement, /w/Master_Guide or /w/AY_… — never /w/api.php, /w/index.php? or
/w/Special: — identifiable User-Agent, one request every 10 s. When another crawl of the same
wiki is running (its stamp file changed in the last minute) this one slows to one request
every 20 s and keeps >= 5 s after the other's last request.
"""
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = "https://wiki.pathfindersonline.org/w/"
AGENT = "AdventistClubBot/1.0 (+https://adventist.club; support@adventist.club)"
HOME = pathlib.Path.home() / "adventist-wiki" / "classes"
RAW = HOME / "raw"
STAMP = HOME / ".last_request"
OTHER = pathlib.Path.home() / "adventist-wiki" / "second-pass" / ".last_request"
LOG = HOME / "crawl.log"
ALLOWED = ("AY_", "Investiture_Achievement", "Master_Guide")

CLASSES = ["Friend", "Companion", "Explorer", "Ranger", "Voyager", "Guide"]
SECTIONS = ["Personal_Growth", "Spiritual_Discovery", "Serving_Others", "Making_Friends",
            "Health_and_Fitness", "Nature_Study", "Outdoor_Living", "Honor_Enrichment"]
MG_SECTIONS = ["Prerequisites", "Spiritual_Development", "Leadership_Development",
               "Child_Development", "Fitness_Lifestyle_Development", "Skills_Development",
               "Documentation"]


def url_for(path: str) -> str:
    path = path.strip("/")
    if not path.startswith(ALLOWED) or any(b in path for b in ("api.php", "index.php", "Special:", "?")):
        raise ValueError(f"forbidden path {path!r}")
    return ROOT + urllib.parse.quote(path.replace(" ", "_"), safe="()_-.,'!/")


def cache_file(path: str) -> pathlib.Path:
    return RAW / (path.strip("/").replace("/", "__").replace(" ", "_") + ".html")


def _age(stamp: pathlib.Path) -> float:
    return time.time() - stamp.stat().st_mtime if stamp.exists() else 1e9


def _pace():
    """>= 20 s between our requests while the other crawl is active (10 s otherwise), and our
    request lands half-way between two of the other crawl's (which fires every 10 s): only
    when its last request is 5-6.5 s old."""
    while True:
        other_active = _age(OTHER) < 60
        mine = 20 if other_active else 10
        wait = max(mine - _age(STAMP), 5 - _age(OTHER), 0)
        if wait > 0:
            time.sleep(wait + 0.2)
            continue
        if other_active and _age(OTHER) > 6.5:
            time.sleep(0.25)  # wait for the other crawl's next request, then 5 s more
            continue
        return


def get(path: str) -> str:
    """'cached' | 'ok' | '404' | 'err …'."""
    target = cache_file(path)
    missing = target.with_suffix(".404")
    if target.exists() or missing.exists():
        return "cached"
    url = url_for(path)
    _pace()
    HOME.mkdir(parents=True, exist_ok=True)
    STAMP.touch()
    RAW.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=40) as r:
            body = r.read()
        part = target.with_suffix(".part")
        part.write_bytes(body)
        part.replace(target)  # atomic: an interrupted run never leaves half a page behind
        status = "ok"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            missing.write_text(url)
            status = "404"
        else:
            status = f"err HTTP {e.code}"
            if e.code == 429 or e.code >= 500:
                time.sleep(300)  # the wiki is struggling: back off five minutes
    except Exception as e:  # network hiccup: retried on the next run
        status = f"err {e!r}"
    with LOG.open("a") as log:
        log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {status} {url}\n")
    return status


def paths(only: list[str]) -> list[str]:
    out = []
    if not only or "Master_Guide" in only:
        out += ["Master_Guide", "Master_Guide/es"]
        for section in MG_SECTIONS:
            out += [f"Master_Guide/{section}", f"Master_Guide/{section}/es"]
    for name in CLASSES:
        if only and name not in only:
            continue
        base = f"Investiture_Achievement/{name}"
        out += [base, base + "/es"]
        for section in SECTIONS:
            out += [f"{base}/{section}", f"{base}/{section}/es"]
    return out


if __name__ == "__main__":
    todo = paths(sys.argv[1:])
    errors = 0
    for n, path in enumerate(todo, 1):
        status = get(path)
        if status != "cached":
            print(f"[{n}/{len(todo)}] {status} {path}", flush=True)
        errors = errors + 1 if status.startswith("err") else 0
        if errors >= 3:
            print("stopping: 3 errors in a row, the wiki is struggling; rerun later", flush=True)
            break
    print("done", flush=True)
