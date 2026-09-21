"""Link the official wiki index (AY Honors/es) with our honour catalogue. One-to-one, never guesses:
exact normalised names (+ category / authority tie-breaks), then a curated alias list for known typos.
Everything else goes to a review list with candidates."""
import json, re, sys, unicodedata, difflib, collections

CAT = {"Estudio de la naturaleza": "nature", "Artes y actividades manuales": "arts-crafts-hobbies",
       "Actividades recreacionales": "recreation",
       "Crecimiento espiritual, actividades misioneras y herencia": "spiritual-growth",
       "Actividades vocacionales": "vocational", "Salud y ciencia": "health-science",
       "Artes domésticas": "household-arts", "Actividades agropecuarias": "outdoor-industries"}
AUTH = {"division norteamericana": "NAD", "asociacion general": "GC", "division sudamericana": "SAD"}
# wiki English title -> our slug. Only spelling variants of the same honour, checked by hand.
ALIASES = {
    "Forestry - Advanced": "silvicutltura-avanzado", "Bible Evangelism": "evanglismo-biblico",
    "Adventurer for Christ": "aventuro-para-cristo", "Adventurer for Christ - Advanced": "aventuro-para-cristo-avanzado",
    "Geological Geocaching": "geocaching-geologico", "Geological Geocaching - Advanced": "geocaching-geologico-avanzado",
    "Model Railroad": "tren-modelismo", "Caving": "espeologia", "Caving - Advanced": "espeologia-avanzado",
    "African American Adventist Heritage in the NAD": "herencia-adventista-afroamericana",
    "African American Adventist Heritage in the NAD - Advanced": "herencia-adventista-afroamericana-avanzado",
    "Hot Air Balloons": "globo-aerostatico", "Christian Art of Preaching": "arte-cristiano-de-predicar",
    "Christian Art of Preaching - Advanced": "arte-cristiano-de-predicar-avanzado",
    "Leather Craft": "trabajos-en-cuero", "Leather Craft - Advanced": "trabajos-en-cuero-avanzado",
    "Soap Craft": "trabajos-en-jabon", "Soap Craft - Advanced": "trabajos-en-jabon-avanzado",
    "Environmental Conservation": "conservacion-ambiental", "Ultimate Disc": "ultimate-disc",
    "Thatching": "techado-con-paja", "Dog Care and Training": "cuidado-y-entrenamiento-de-perros",
}
# same honour under a different Spanish name, confirmed by comparing the embroidered patch
IMAGE_VERIFIED = {
    "Refugee Assistance": "asistencia-a-los-refugiados-community-services",
    "Rural Development": "desarrollo-rural-community-services",
    "Identifying Community Needs": "evaluacion-comunitaria-community-services",
    "Crisis Intervention": "intervencion-en-crisis-community-services",
    "Disaster Ministries": "ministerio-ante-el-desastre-community-services",
    "Feeding Ministries": "ministerio-de-alimentacion-community-services",
    "Tutoring": "tutoria-community-services",
    "Serving Communities": "sirviendo-a-las-comunidades-community-services",
    "Raptors": "aves-de-rapina", "Word Processing": "procesar-texto",
    "Tie-Dye": "tenido-anudado", "Tie-Dye - Advanced": "tenido-anudado-avanzado",
    "Laundering": "lavado-y-planchado", "Lapidary": "lapidacion",
}
# looked alike by name but the patch is a different honour: never offer these as candidates
IMAGE_REJECTED = {("Stewardship", "la-mayordomia"), ("Introduction to Adventist Pioneer Heritage", "pioneros-adventistas"),
                  ("First Aid", "primeros-auxilios-de-san-juan")}

def norm(s):
    s = unicodedata.normalize("NFKD", s.lower()); s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)", " ", s); s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in {"de", "del", "la", "las", "el", "los", "y", "en", "e", "a"})

def authority(name):
    m = re.search(r"\((.*?)\)", name)
    if not m: return None
    k = norm(m.group(1)); k2 = unicodedata.normalize("NFKD", m.group(1).lower())
    k2 = "".join(c for c in k2 if not unicodedata.combining(c))
    return AUTH.get(k2, "OTHER")

ours = json.load(open(sys.argv[1])); wiki = json.load(open(sys.argv[2]))
by_slug = {o["slug"]: o for o in ours}
by_name = collections.defaultdict(list)
for o in ours: by_name[norm(o["name"])].append(o)
taken, links, review = set(), [], []

def link(w, o, how):
    taken.add(o["slug"]); links.append({"slug": o["slug"], "name_es_ours": o["name"], "how": how, **w})

pending = []
for w in wiki:
    if w["en"] in ALIASES:
        link(w, by_slug[ALIASES[w["en"]]], "alias"); continue
    if w["en"] in IMAGE_VERIFIED:
        link(w, by_slug[IMAGE_VERIFIED[w["en"]]], "image"); continue
    pending.append(w)
for w in pending:
    cands = [o for o in by_name.get(norm(w["es"]), []) if o["slug"] not in taken]
    def score(o):
        a = authority(o["name"])
        return ((a == w["authority"]) * 4 + (a is None and w["authority"] == "GC") * 2 + (a is None) * 1
                + (o["category_slug"] == CAT.get(w["category_es"])) * 3)
    cands.sort(key=score, reverse=True)
    if cands and (len(cands) == 1 or score(cands[0]) > score(cands[1])):
        link(w, cands[0], "exact")
    else:
        review.append(w)
free = [o for o in ours if o["slug"] not in taken]
free_names = {norm(o["name"]): o for o in free}
out_review = []
for w in review:
    close = difflib.get_close_matches(norm(w["es"]), list(free_names), n=3, cutoff=0.55)
    out_review.append({**w, "candidates": [{"slug": free_names[c]["slug"], "name": free_names[c]["name"],
                                            "category": free_names[c]["category_name"]} for c in close if (w["en"], free_names[c]["slug"]) not in IMAGE_REJECTED]})
json.dump({"links": links, "review": out_review,
           "ours_without_wiki": [{"slug": o["slug"], "name": o["name"], "category": o["category_name"]} for o in free]},
          open(sys.argv[3], "w"), ensure_ascii=False, indent=1)
print("linked", len(links), collections.Counter(l["how"] for l in links), "| review", len(out_review), "| ours without wiki", len(free))
