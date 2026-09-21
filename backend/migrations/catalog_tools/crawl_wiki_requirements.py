"""Polite, resumable download of the official requirement pages for every linked honour.

    python crawl_wiki_requirements.py ../data/wiki_honor_links.json ~/adventist-wiki es [en pt-br fr]

Only normal /w/ pages (robots.txt forbids api.php, index.php? and Special: for generic bots), one
request every 10 s, identifiable User-Agent. Raw HTML stays outside the repo; a page that is already
on disk is never fetched again, so the run can be stopped and resumed. 404 = not translated yet.
"""
import json, pathlib, re, sys, time, urllib.error, urllib.parse, urllib.request

WIKI = "https://wiki.pathfindersonline.org/w/AY_Honors/"
AGENT = "adventist.club requirements importer (contact: michstudio.com)"
DELAY = 10


def main():
    links, out, langs = json.load(open(sys.argv[1], encoding="utf-8")), pathlib.Path(sys.argv[2]).expanduser(), sys.argv[3:]
    titles = sorted({h["wiki_title"] for h in links["honors"]})
    for lang in langs:
        folder = out / "requirements" / lang
        folder.mkdir(parents=True, exist_ok=True)
        for n, title in enumerate(titles, 1):
            target = folder / (re.sub(r"[^A-Za-z0-9()._-]+", "_", title) + ".html")
            missing = target.with_suffix(".404")
            if target.exists() or missing.exists():
                continue
            url = WIKI + urllib.parse.quote(title.replace(" ", "_"), safe="()_-.,'!") + "/Requirements" + ("" if lang == "en" else "/" + lang)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=40) as response:
                    target.write_bytes(response.read())
                print(f"[{lang} {n}/{len(titles)}] ok {title}", flush=True)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    missing.write_text(url)
                    print(f"[{lang} {n}/{len(titles)}] 404 {title}", flush=True)
                elif error.code in (429, 503):
                    print(f"[{lang}] {error.code}: backing off 5 min", flush=True)
                    time.sleep(300)
                else:
                    print(f"[{lang} {n}/{len(titles)}] HTTP {error.code} {title}", flush=True)
            except Exception as error:  # network hiccup: leave it for the next run
                print(f"[{lang} {n}/{len(titles)}] {error!r} {title}", flush=True)
            time.sleep(DELAY)


if __name__ == "__main__":
    main()
