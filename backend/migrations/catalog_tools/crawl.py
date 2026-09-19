import re, html, json, time, urllib.request, urllib.parse, unicodedata
BASE="https://www.guiasmayores.com"; UA="Mozilla/5.0 (adventist.club catalog importer; contact michstudio.com@gmail.com)"
CATS=[  # site page -> (slug, display name)
 ("/especialidades-ja---actividades-agropecuarias.html","outdoor-industries","Industrias Agropecuarias"),
 ("/especialidades-ja---actividades-recreacionales.html","recreation","Recreación"),
 ("/especialidades-ja---actividades-vocacionales.html","vocational","Vocacional"),
 ("/especialidades-ja---adra.html","adra","ADRA"),
 ("/especialidades-ja---artes-y-actividades-manuales.html","arts-crafts-hobbies","Artes y Habilidades Manuales"),
 ("/especialidades-ja---artes-domeacutesticas.html","household-arts","Artes Domésticas"),
 ("/especialidades-ja---crecimiento-espiritual-actividades-misioneras-y-herencia.html","spiritual-growth","Crecimiento Espiritual, Actividades Misioneras y Herencia"),
 ("/especialidades-ja---doctrinales.html","doctrinal","Doctrinales"),
 ("/especialidades-ja---estudio-de-la-naturaleza.html","nature","Naturaleza"),
 ("/especialidades-ja---salud-y-ciencia.html","health-science","Salud y Ciencia"),
 ("/especialidades-ja---servicios-comunitarios-adventistas.html","community-services","Servicios Comunitarios Adventistas"),
 ("/especialidades-ja---asociacioacuten-de-florida.html","florida-conference","Especialidades de la Asociación de Florida"),
 ("/especialidades-ja---maestriacuteas.html","masters","Maestrías"),
]
def get(path):
    req=urllib.request.Request(BASE+urllib.parse.quote(path,safe="/%-._~"),headers={"User-Agent":UA})
    return urllib.request.urlopen(req,timeout=40).read().decode("utf8","ignore")
def slugify(v):
    v=unicodedata.normalize("NFKD",v).encode("ascii","ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+","-",v).strip("-").lower()
PAT=re.compile(r"<div class=\"wsite-image[^>]*>\s*(?:<a href=['\"]([^'\"]+)['\"][^>]*>)?\s*<img src=\"([^\"]+)\"(?: alt=\"([^\"]*)\")?[^>]*/>\s*(?:</a>)?\s*<div[^>]*>(.*?)</div>",re.S)
out=[]
for path,cslug,cname in CATS:
    h=get(path); body=h[h.find("<body"):]
    n=0
    for href,src,alt,cap in PAT.findall(body):
        name=html.unescape(re.sub(r"<[^>]+>","",cap)).strip() or html.unescape(alt or "").strip()
        if not name or name.lower() in ("picture","foto"): continue
        out.append({"name":re.sub(r"\s+"," ",name),"slug":slugify(name),"category_slug":cslug,"category_name":cname,
                    "image_src":BASE+src.split("?")[0],"requirements_pdf":(BASE+href if href and href.startswith("/") else href or None),
                    "source_url":BASE+path}); n+=1
    print(f"{cslug:22} {n}")
    time.sleep(2)
json.dump(out,open("manifest.json","w"),ensure_ascii=False,indent=1)
print("TOTAL",len(out),"unique slugs",len({o['slug'] for o in out}),"with pdf",sum(1 for o in out if o['requirements_pdf']))
