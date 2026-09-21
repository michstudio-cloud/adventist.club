"""Mark which imported requirements are PRACTICAL (the member must upload evidence).

    DATABASE_URL=... python classify_practical_requirements.py [--sample 40] [--commit]

Every requirement imported from the wiki arrived as theoretical. This marks as practical
(`is_theoretical = false`) only the ones that START with a clear "do it" verb — conservative on
purpose: a doubtful requirement stays as an answer requirement, and reviewers can flip the mark in
the editor. The decision is taken on the Spanish text (English when there is no Spanish) and applied
to the same position in every language of that honour, so all locales agree.
Only rows with `source = 'pathfinder-wiki'` are touched; lists written by instructors never.
Enrollments already started keep the value they copied. Dry run unless --commit.
"""
import argparse, json, os, random, re, sys, unicodedata

import psycopg

# Verbs that mean "produce / perform something a reviewer can only judge by seeing it". Writing a
# report, visiting, interviewing or helping are left out on purpose: they can be answered in text.
DO_ES = ("demostrar", "demuestre", "hacer", "haga", "realizar", "realice", "construir", "construya", "elaborar",
         "elabore", "confeccionar", "confeccione", "preparar", "prepare", "cocinar", "cocine", "hornear", "hornee",
         "dibujar", "dibuje", "pintar", "pinte", "fotografiar", "fotografie", "tomar fotografias", "coleccionar",
         "coleccione", "recolectar", "recolecte", "plantar", "plante", "sembrar", "siembre", "cultivar", "cultive",
         "armar", "arme", "montar", "monte", "tejer", "teja", "coser", "cosa", "bordar", "borde", "tallar", "talle",
         "modelar", "modele", "nadar", "nade", "acampar", "acampe", "participar", "participe", "completar una",
         "completar un", "caminar", "camine", "correr", "corra", "ejecutar", "ejecute", "tocar", "toque", "cantar",
         "cante", "presentar", "presente", "exhibir", "exhiba", "crear", "cree", "disenar", "disene", "fabricar",
         "fabrique", "reparar", "repare", "instalar", "instale", "encender", "encienda", "atar", "ate", "practicar",
         "practique", "llevar un registro", "mantener un registro", "observar y registrar")
DO_EN = ("demonstrate", "make", "build", "construct", "prepare", "cook", "bake", "draw", "paint", "photograph",
         "take photographs", "collect", "plant", "grow", "assemble", "knit", "sew", "carve", "model", "swim", "camp",
         "participate", "complete a", "complete an", "hike", "run", "perform", "play", "sing", "present", "display",
         "create", "design", "repair", "install", "light", "tie", "practice", "keep a record", "keep a log",
         "observe and record")
# Openers that look like doing but are really an answer.
ANSWER_FIRST = ("hacer una lista", "haga una lista", "hacer un listado", "make a list", "hacer una definicion",
                "presentar una lista", "preparar una lista", "prepare a list", "crear una lista")


def plain(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# "Hacer dos de las siguientes:", "Do the following:" — a container: its sub-items decide, so it stays
# an answer requirement (conservative). And products that are text, not handiwork.
CONTAINER = re.compile(r"^(hacer|haga|realizar|realice|completar|complete|do|complete)\s+((al menos|por lo menos|at least)\s+)?"
                       r"(lo siguiente|the following|(una?|uno|dos|tres|cuatro|cinco|one|two|three|four|five|\d+)\s+(de|of)\b)")
TEXT_PRODUCT = re.compile(r"^\w+\s+(y presentar\s+)?(una?|unas|un breve|listas?|a|an)\s*(presentacion|presentaciones|descripcion|breve descripcion|informe|estudio|resumen|"
                          r"plan|leccion|discurso|documento|tabla|entrevista|investigacion|reporte|ensayo|de\b|report|presentation|"
                          r"summary|plan|lesson|speech|document|table|interview|essay)")


def is_practical(description, locale):
    first = plain(description.split("\n", 1)[0]).strip(" ¿¡\"'«(")
    if first.startswith(ANSWER_FIRST) or CONTAINER.match(first) or TEXT_PRODUCT.match(first):
        return False
    verbs = DO_ES if locale.startswith("es") else DO_EN
    return any(re.match(rf"{re.escape(v)}\b", first) for v in verbs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL")
    with psycopg.connect(url.replace("postgresql+asyncpg://", "postgresql://")) as conn, conn.cursor() as cur:
        cur.execute("SELECT honor_id, position, locale, description FROM honor_requirements"
                    " WHERE source = 'pathfinder-wiki' ORDER BY honor_id, position, locale")
        by_key = {}
        for honor_id, position, locale, description in cur.fetchall():
            by_key.setdefault((honor_id, position), {})[locale] = description
        practical, answers = [], []
        for key, texts in by_key.items():
            locale = "es" if "es" in texts else sorted(texts)[0]
            (practical if is_practical(texts[locale], locale) else answers).append((key, locale, texts[locale]))
        for (honor_id, position), _, _ in practical:
            cur.execute("UPDATE honor_requirements SET is_theoretical = false WHERE honor_id = %s AND position = %s"
                        " AND source = 'pathfinder-wiki' AND is_theoretical", (honor_id, position))
        if args.commit:
            conn.commit()
        else:
            conn.rollback()
    rnd = random.Random(7)
    show = lambda rows: [text.split("\n", 1)[0][:110] for _, _, text in rnd.sample(rows, min(args.sample, len(rows)))]
    print(json.dumps({"requirements": len(by_key), "practical": len(practical), "answer": len(answers),
                      "sample_practical": show(practical), "sample_answer": show(answers),
                      "committed": args.commit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
