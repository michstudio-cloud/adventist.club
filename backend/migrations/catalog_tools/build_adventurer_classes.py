"""Build `backend/data/adventurer_classes.json`: the six Adventurer classes (GC curriculum,
«Nuevo currículo» of the Inter-American Division) with their sections and requirements in
Spanish and English, the awards each requirement asks for and the awards each class suggests.

    python migrations/catalog_tools/build_adventurer_classes.py            # writes the JSON
    python migrations/catalog_tools/build_adventurer_classes.py --check    # + compares the Spanish
          with the mundoja pages saved in ~/Documents/DEEL/aventureros/clases/mundoja-html/

Sources (the curated table below was written from them on 2026-09-24):
  * mundoja.org (Mundo J.A, volunteers of the Inter-American Division), one page per class:
    https://mundoja.org/clubes/aventureros/<page> — the Spanish text of every requirement is
    theirs, verbatim except for the typos listed in `ES_FIXES`, and every requirement keeps the
    page as its `source_url.es` (authorship: owner's instruction «dejar los enlaces»);
  * GC *Adventurer Director's Manual* (GC Youth Ministries, 2020): the six levels (Little Lamb,
    Early Bird, Busy Bee, Sunbeam, Builder, Helping Hands) and the four categories «My God, My
    Self, My Family, My World» (page 10). The manual does NOT print the class requirements (they
    live in the six activity books, p. 24 and 54), so the English requirement text is an
    UNOFFICIAL translation of mundoja's Spanish, marked as such (`translation.en`); award names
    in English are the official ones of the *Adventurer Award Book 2020* (data/adventurer_awards.json);
  * data/adventurer_awards_library.csv: the class the Award Book suggests for each award.

Labels are normalised to «Section.Topic[letter]» (I.1, II.1a, II.3c…): the digit is the topic
inside the category (1 = God's plan / I am special / I have a family / world of friends…), as on
the mundoja pages; where mundoja's own numbering restarts or skips (Constructor, Manos
Ayudadoras) the original is kept in `mundoja_label`.

The network is never touched here.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import pathlib
import re
import sys
import unicodedata

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
DATA = BACKEND / "data"
OUT = DATA / "adventurer_classes.json"
AWARDS_JSON = DATA / "adventurer_awards.json"
LIBRARY_CSV = DATA / "adventurer_awards_library.csv"
HTML_DIR = pathlib.Path.home() / "Documents" / "DEEL" / "aventureros" / "clases" / "mundoja-html"

MUNDOJA = "https://mundoja.org"
MANUAL_URL = "https://www.gcyouthministries.org/ministries/adventurers/"
MANUAL_PAGE = MANUAL_URL + "#page={}"
LICENSE = "© GC Youth Ministries, permiso pendiente"
AWARD_PREFIX = "av-"
NATURE_CATEGORY = "av-naturaleza"

SECTIONS = {
    # key: (roman, slug, es, en, en is official (manual p. 10)?)
    "I": ("requisitos-basicos", "Requisitos básicos", "Basic Requirements", False),
    "II": ("mi-dios", "Mi Dios", "My God", True),
    "III": ("yo-mismo", "Yo mismo", "My Self", True),
    "IV": ("mi-familia", "Mi familia", "My Family", True),
    "V": ("mi-mundo", "Mi mundo", "My World", True),
}
# The twelve topics (mundoja prints them in capitals without accents: «EL MENSAJE DE DIOS PARA MI»).
TOPICS = {
    "plan": ("El plan de Dios para salvarme", "God's Plan to Save Me"),
    "mensaje": ("El mensaje de Dios para mí", "God's Message to Me"),
    "poder": ("El poder de Dios en mi vida", "God's Power in My Life"),
    "especial": ("Soy especial", "I Am Special"),
    "decisiones": ("Puedo tomar buenas decisiones", "I Can Make Good Choices"),
    "cuerpo": ("Puedo cuidar mi cuerpo", "I Can Care for My Body"),
    "familia": ("Tengo una familia", "I Have a Family"),
    "cuidan": ("En las familias se cuidan unos a otros", "Families Care for Each Other"),
    "ayuda": ("Mi familia me ayuda a cuidarme", "My Family Helps Me Care for Myself"),
    "amigos": ("El mundo de los amigos", "The World of Friends"),
    "demas": ("El mundo de los demás", "The World of Other People"),
    "naturaleza": ("El mundo de la naturaleza", "The World of Nature"),
}
# Typos of the mundoja text corrected in the JSON (wrong → right). Documented, never silent.
ES_FIXES = {
    "dÍa": "día",
    "(además de Jesús} y por qué": "(además de Jesús) y por qué",
    "Lectura l.": "Lectura I.",
}

# ----------------------------------------------------------------------------------------------
# The classes. A requirement is a dict:
#   l  label (normalised)          ml mundoja's own label when it differs
#   t  topic key (None = basic)    k  TEXT | HONOR | HONOR_FROM_CATEGORY | HONOR_ANY
#   h  award slug (HONOR)          es / en text     sub_es / sub_en sub-items
#   options  award slugs of a «choose N of» list, with choose = N
#   note  reviewer note (why a target was inferred, etc.)
# ----------------------------------------------------------------------------------------------
def H(label, topic, slug, es, en=None, **extra):
    return {"l": label, "t": topic, "k": "HONOR", "h": slug, "es": es, "en": en, **extra}


def T(label, topic, es, en, **extra):
    return {"l": label, "t": topic, "k": "TEXT", "es": es, "en": en, **extra}


PLEDGE_EN = "Say and learn by heart the Adventurer Pledge."

CLASSES = [
    {
        "slug": "corderitos", "code": "CORDERITOS", "page": "corderitos", "age": 4,
        "names": {"es": "Corderitos", "en": "Little Lamb"},
        "motto": {"es": "Descubre el amor de Jesús.", "en": "Discover the love of Jesus."},
        "requirements": [
            T("I.1", None, "Repite y aprende de memoria el Voto de los Aventureros.", PLEDGE_EN),
            H("I.2", None, "story-listening-i", "Completa la especialidad Escuchando la Historia I"),
            H("I.3", None, "wooly-lamb", "Completa la especialidad Corderito Lanudo"),
            T("II.1a", "plan", "Colorea el número de cada día de la creación.",
              "Color the number of each day of Creation."),
            T("II.1b", "plan", "Cuéntale a un adulto alguna de las historias de la creación: la creación de"
              " los animales, la creación del hombre, la creación del Sábado.",
              "Tell an adult one of the Creation stories: the creation of the animals, the creation of"
              " man, the creation of the Sabbath."),
            H("II.2a", "mensaje", "my-friend-jesus", "Completa la especialidad Mi amigo Jesús"),
            H("II.2b", "mensaje", "little-boy-jesus", "Completa la especialidad El Niño Jesús"),
            T("II.3a", "poder", "Celebra el culto familiar regularmente. Mantén un registro.",
              "Take part in family worship regularly. Keep a record."),
            T("II.3b", "poder", "Pregunta a tus padres o tutores cuál es su dÍa favorito de la creación.",
              "Ask your parents or guardians which is their favorite day of Creation."),
            H("II.3c", "poder", "bible-friends-i", "Completa la especialidad Amigos de la Biblia I"),
            H("III.1", "especial", "finger-play", "Completa la especialidad Usar los Dedos"),
            H("III.2", "decisiones", "sharing", "Completa la especialidad Compartir"),
            H("III.3", "cuerpo", "healthy-foods", "Completa la especialidad Alimentos Sanos"),
            H("IV.1", "familia", "my-family", "Completa la especialidad Mi Familia"),
            H("IV.2", "cuidan", "special-helper", "Completa la especialidad Ayudante Especial"),
            H("IV.3", "ayuda", "healthy-me", "Completa la especialidad Sano y Fuerte"),
            H("V.1", "amigos", "creation", "Completa la especialidad Creación"),
            H("V.2", "demas", "community-helpers", "Completa la especialidad Ayudante en la comunidad"),
            T("V.3", "naturaleza",
              "Completa por lo menos dos de las siguientes especialidades de la clase de Corderitos:",
              "Complete at least two of the following Little Lamb awards:",
              sub_es=["Cuerpos de agua", "Insectos", "Estrellas", "Clima I", "Animales de Zoológico"],
              options=["bodies-of-water", "insects", "stars", "weather-i", "zoo-animals"], choose=2),
        ],
        "optional": ["alphabet-i", "colors", "music-i", "numbers", "trains-and-trucks", "trikes-and-bikes"],
        "optional_es": ["Alfabeto I", "Colores", "Música I", "Números", "Camiones y Trenes",
                        "Triciclos y bicicletas"],
        "page_awards": ["alphabet-i", "healthy-foods", "bible-friends-i", "zoo-animals", "special-helper",
                        "community-helpers", "bible-i", "colors", "sharing", "wooly-lamb", "creation",
                        "bodies-of-water", "story-listening-i", "stars", "insects", "my-family", "music-i",
                        "numbers", "healthy-me", "weather-i", "trains-and-trucks", "trikes-and-bikes",
                        "finger-play"],
    },
    {
        "slug": "aves-madrugadoras", "code": "AVES", "page": "castorcitos2", "age": 5,
        "names": {"es": "Aves Madrugadoras", "en": "Early Bird"},
        "motto": {"es": "Aprendemos a amar y obedecer.", "en": "We learn to love and obey."},
        "requirements": [
            T("I.1", None, "Repite de memoria la Ley de los Aventureros",
              "Say the Adventurer Law by heart."),
            H("I.2", None, "story-listening-ii", "Completa la especialidad Escuchando la Historia II"),
            H("I.3", None, "birds", "Completa la especialidad Aves Madrugadoras",
              note="Inferido: el Award Book no tiene un award «Early Bird»; el award de aves de la clase"
                   " es Birds, que mundoja llama «Pajaritos» (ficha 627-pajaritos-av, en su lista de Aves"
                   " Madrugadoras). Cotejar con el libro de actividades."),
            H("II.1a", "plan", "jesus-star", "Completa los requisitos de la especialidad La Estrella de Jesús",
              "Complete the requirements of the Jesus' Star award."),
            T("II.1b", "plan", "Colorea los cuadros de personajes bíblicos que oraron: Samuel, Daniel, Jonás,"
              " David.", "Color the pictures of Bible characters who prayed: Samuel, Daniel, Jonah, David."),
            T("II.1c", "plan", "Aprende a orar de forma independiente.", "Learn to pray on your own."),
            H("II.2", "mensaje", "bible-friends-ii", "Completa la especialidad Amigos de la Biblia II"),
            T("II.3a", "poder", "Celebra el culto familiar regularmente. Lleva un registro.",
              "Take part in family worship regularly. Keep a record."),
            T("II.3b", "poder", "Pregunta a alguien que conozcas por qué ora.",
              "Ask someone you know why they pray."),
            H("II.3c", "poder", "gods-world", "Completa la especialidad El mundo de Dios"),
            H("III.1", "especial", "left-and-right", "Completa la especialidad Izquierda y Derecha"),
            H("III.2", "decisiones", "manners-fun", "Completa la especialidad Diversión con Modales"),
            H("III.3", "cuerpo", "know-your-body", "Completa la especialidad Conoce tu Cuerpo"),
            T("IV.1", "familia", "Di el quinto mandamiento: \"Honra a tu padre y a tu madre\" (Éxodo 20:12).",
              "Say the fifth commandment: \"Honor your father and your mother\" (Exodus 20:12)."),
            H("IV.2", "cuidan", "home-helper-i", "Completa la especialidad Ayudante en el Hogar I"),
            H("IV.3", "ayuda", "fire-safety", "Completa la especialidad Seguridad contra Incendios"),
            H("V.1", "amigos", "my-community-friends", "Completa la especialidad Mis Amigos de la Comunidad"),
            H("V.2", "demas", "playing-with-friends", "Completa la especialidad Jugando con Amigos"),
            H("V.3", "naturaleza", "scavenger-hunt", "Completa la especialidad Tesoro Escondido"),
        ],
        "optional": ["alphabet-ii", "animal-homes", "animals", "cyclist-i", "swimmer-i", "crayons-and-markers",
                     "gadgets-and-sand", "jigsaw-puzzles", "pets", "shapes-and-sizes", "sponge-art",
                     "stamping-fun-i", "toys"],
        "optional_es": ["Alfabeto II", "Hogares de Animales", "Animales*", "Ciclismo I", "Nadador I",
                        "Crayones y Marcadores*", "Medidas y Arena", "Rompecabezas*", "Mascotas",
                        "Tamaños y Formas*", "Arte de Esponja*", "Diversión con Sellos", "Juguetes*"],
        "optional_note": {
            "es": "* Especialidades sugeridas para incluirlas en el plan anual, aunque no son requeridas para"
                  " obtener el botón de investidura.",
            "en": "* Awards suggested for the yearly plan, although they are not required for the"
                  " investiture pin.",
        },
        "page_awards": ["alphabet-ii", "bible-friends-ii", "animals", "sponge-art", "birds", "home-helper-i",
                        "cyclist-i", "know-your-body", "crayons-and-markers", "manners-fun", "gods-world",
                        "story-listening-ii", "stamping-fun-i", "jesus-star", "shapes-and-sizes",
                        "gadgets-and-sand", "animal-homes", "left-and-right", "playing-with-friends", "toys",
                        "jesus-special-supper", "pets", "my-community-friends", "swimmer-i", "swimmer-ii",
                        "jigsaw-puzzles", "fire-safety", "scavenger-hunt"],
    },
    {
        "slug": "abejas-industriosas", "code": "ABEJAS", "page": "abejas", "age": 6,
        "names": {"es": "Abejas Industriosas", "en": "Busy Bee"},
        "motto": {"es": "Trabajamos y compartimos.", "en": "We work and share."},
        "requirements": [
            T("I.1", None, "Repetir de memoria y aceptar el Voto de los Aventureros.",
              "Say by heart and accept the Adventurer Pledge."),
            H("I.2", None, "reading-i", "Completa la especialidad Lectura l."),
            H("I.3", None, "flowers", "Completa la especialidad Flores."),
            T("II.1a", "plan", "Crea un gráfico de historias o un cuaderno que muestre el orden en que ocurrieron"
              " los eventos (escribe o pídele a alguien que escriba los números en el orden en que estos"
              " sucedieron o sucederán).",
              "Make a story chart or a notebook that shows the order in which the events happened (write,"
              " or ask someone to write, the numbers in the order in which they happened or will happen)."),
            T("II.1b", "plan", "Haz un dibujo o cuenta una de las historias anteriores para mostrar cuánto Jesús"
              " se preocupa por ti.",
              "Draw a picture of, or tell, one of the stories above to show how much Jesus cares for you."),
            H("II.2", "mensaje", "bible-i", "Completa la especialidad Biblia I"),
            T("II.3a", "poder", "Emplea un tiempo regular en quietud con Jesús para hablar con Él y aprender"
              " acerca de Él. Mantén un registro.",
              "Spend regular quiet time with Jesus to talk with Him and learn about Him. Keep a record."),
            T("II.3b", "poder", "Pregunta a dos personas cómo muestran a otros que Jesús se preocupa por ellos.",
              "Ask two people how they show others that Jesus cares for them."),
            H("II.3c", "poder", "delightful-sabbath", "Completa la especialidad Sábado de Delicia"),
            T("III.1", "especial", "Haz un folleto donde aparezcan todas las personas que se preocupan por ti"
              " como Jesús lo haría.", "Make a booklet of all the people who care for you as Jesus would."),
            T("III.2a", "decisiones", "Nombra al menos 4 sentimientos", "Name at least four feelings."),
            T("III.2b", "decisiones", "Juega un juego de los sentimientos.", "Play a feelings game."),
            H("III.3", "cuerpo", "health-specialist", "Completa la especialidad Especialista en Salud"),
            T("IV.1", "familia", "Haz un dibujo o un recorte de algo especial sobre cada miembro de tu familia",
              "Draw a picture of, or cut out, something special about each member of your family."),
            T("IV.2a", "cuidan", "Descubre lo que el quinto mandamiento (Éxodo 20:12) dice sobre las familias.",
              "Find out what the fifth commandment (Exodus 20:12) says about families."),
            T("IV.2b", "cuidan", "Representa tres maneras en las que puedes honrar a tu familia.",
              "Act out three ways in which you can honor your family."),
            H("IV.2c", "cuidan", "home-helper-ii", "Completa la especialidad Ayudante en el Hogar II"),
            H("IV.3", "ayuda", "safety-specialist", "Completa la especialidad Especialista en Seguridad"),
            H("V.1", "amigos", "listening", "Completa la especialidad Escuchar"),
            T("V.2a", "demas", "Habla sobre el voluntariado que hacen las personas en tu iglesia.",
              "Talk about the volunteer work people do in your church."),
            T("V.2b", "demas", "Encuentra una forma de ayudar.", "Find a way to help."),
            H("V.3", "naturaleza", "friend-of-animals", "Completa la especialidad Amigo de los Animales"),
        ],
        "optional": ["artist", "butterflies", "buttons", "fish", "guide", "music-ii", "potatoes", "sand-art",
                     "spotter", "swimmer-ii"],
        "optional_es": ["Artista", "Mariposas", "Botones", "Peces", "Guía", "Música II", "Papas",
                        "Arte con Arena", "Observador", "Nadador II"],
        "page_awards": ["friend-of-animals", "sand-art", "artist", "home-helper-ii", "bible-i", "buttons",
                        "listening", "health-specialist", "safety-specialist", "flowers", "guide", "reading-i",
                        "butterflies", "honey", "music-ii", "spotter", "potatoes", "fish",
                        "delightful-sabbath"],
    },
    {
        "slug": "rayos-de-sol", "code": "RAYOS", "page": "rayos-de-sol", "age": 7,
        "names": {"es": "Rayos de Sol", "en": "Sunbeam"},
        "motto": {"es": "Brillamos para Jesús.", "en": "We shine for Jesus."},
        "requirements": [
            T("I.1", None, "Repite de memoria y acepta la Ley de los Aventureros.",
              "Say by heart and accept the Adventurer Law."),
            H("I.2", None, "reading-ii", "Completa la Especialidad Lectura II"),
            H("I.3", None, "seasons", "Completa la especialidad Estaciones"),
            T("II.1", "plan", "Crea un libro de historia que muestre la vida de Jesús: nacimiento, bautismo,"
              " milagros, parábolas, muerte, resurrección y su retorno al cielo.",
              "Make a story book that shows the life of Jesus: birth, baptism, miracles, parables, death,"
              " resurrection and His return to heaven."),
            H("II.2", "mensaje", "bible-ii", "Completa la especialidad Biblia II"),
            T("II.3a", "poder", "Emplea un tiempo regular en quietud con Jesús para hablar con Él y aprender"
              " acerca de Él. Mantén un registro.",
              "Spend regular quiet time with Jesus to talk with Him and learn about Him. Keep a record."),
            T("II.3b", "poder", "Pregunta a tres personas cuál es su historia favorita de la vida de Jesús"
              " (encontrada en los evangelios).",
              "Ask three people which is their favorite story of the life of Jesus (found in the Gospels)."),
            H("II.3c", "poder", "parables-of-jesus", "Completa la especialidad Parábolas de Jesús"),
            T("III.1", "especial", "Haz un trazo de ti mismo. Decóralo con imágenes y palabras que digan buenas"
              " cosas sobre ti. Comparte tu dibujo con tu grupo. Felicita los dibujos de los demás. Di a los"
              " otros algo que los haga especiales.",
              "Make an outline of yourself. Decorate it with pictures and words that say good things about"
              " you. Share your drawing with your group. Compliment the others' drawings. Tell the others"
              " something that makes them special."),
            T("III.2", "decisiones", "Participa en un juego o actividad sobre decisiones.",
              "Take part in a game or activity about choices."),
            H("III.3", "cuerpo", "fitness-fun", "Completa la especialidad Cultura Física"),
            T("IV.1", "familia", "Pide a cada miembro de tu familia que te cuente algunos de sus recuerdos"
              " favoritos", "Ask each member of your family to tell you some of their favorite memories."),
            T("IV.2a", "cuidan", "Muestra cómo Jesús puede ayudarte a lidiar con los desacuerdos. Usa: títeres,"
              " dramatización, juego de roles, etc.",
              "Show how Jesus can help you deal with disagreements. Use puppets, drama, role play, etc."),
            H("IV.2b", "cuidan", "acts-of-kindness", "Completa la especialidad Actos de Bondad"),
            H("IV.3", "ayuda", "road-safety", "Completa la especialidad Seguridad en la Carretera"),
            H("V.1", "amigos", "courtesy", "Completa la especialidad Cortesía"),
            T("V.2a", "demas", "Explora tu vecindario. Haz una lista de cosas buenas y cosas que puedes ayudar a"
              " mejorar.", "Explore your neighborhood. Make a list of good things and of things you can"
              " help to improve."),
            T("V.2b", "demas", "De tu lista de opciones, escoge una forma de mejorar tu vecindario y dedica"
              " tiempo realizándola.", "From your list, choose one way to improve your neighborhood and"
              " spend time doing it."),
            H("V.3", "naturaleza", "friend-of-nature", "Completa la especialidad Amigo de la Naturaleza"),
        ],
        "optional": ["feathered-friends", "ladybugs", "seeds", "trees", "whales"],
        "optional_es": ["Aves", "Mariquita", "Semillas", "Árboles", "Ballenas"],
        "page_awards": ["camper", "acts-of-kindness", "friend-of-jesus", "friend-of-nature", "trees", "handicraft",
                        "whales", "bible-ii", "cooking-fun", "collector", "courtesy", "fitness-fun", "glue-right",
                        "skier", "seasons", "baking", "gardener", "reading-ii", "ladybugs", "feathered-friends",
                        "road-safety", "seeds"],
    },
    {
        "slug": "constructor", "code": "CONSTRUCTOR", "page": "constructor", "age": 8,
        "names": {"es": "Constructor", "en": "Builder"},
        "motto": {"es": "Construimos sobre la Roca.", "en": "We build on the Rock."},
        "requirements": [
            T("I.1", None, "Recitar de memoria el Voto de los Aventureros y la Ley.",
              "Say by heart the Adventurer Pledge and Law.", ml="I."),
            T("I.2", None, "Explicar el Voto y la Ley a través de arte o un drama.",
              "Explain the Pledge and the Law through art or drama.", ml="II."),
            H("I.3", None, "reading-iii", "Desarrollar la especialidad de Lectura III", ml="III."),
            H("I.4", None, "building-blocks", "Desarrollar la especialidad de Bloques", ml="IV."),
            T("II.1a", "plan", "Crear un cuadro histórico o un lapbook que muestre en orden cuándo las siguientes"
              " historias tomaron lugar. Noé, Abraham, Moisés, Rut, David, Daniel, Ester.",
              "Make a history chart or a lapbook that shows in order when the following stories took place:"
              " Noah, Abraham, Moses, Ruth, David, Daniel, Esther.", ml="1."),
            T("II.1b", "plan", "Hacer un diorama, un poema o una canción acerca de una de las historias"
              " anteriores para mostrarle a alguien cómo vivir para Dios.",
              "Make a diorama, a poem or a song about one of the stories above to show someone how to live"
              " for God.", ml="2."),
            H("II.2", "mensaje", "bible-iii", "Completa la especialidad Biblia III", ml="1."),
            T("II.3a", "poder", "Acostumbrarse a tener regularmente un momento tranquilo para hablar con Jesús y"
              " aprender de él. Mantener un registro.",
              "Get used to having a regular quiet time to talk with Jesus and learn from Him. Keep a record.",
              ml="1."),
            T("II.3b", "poder", "Preguntarle a tres personas quién es su héroe bíblico favorito (además de Jesús}"
              " y por qué.", "Ask three people who their favorite Bible hero is (besides Jesus) and why.",
              ml="2."),
            H("II.3c", "poder", "prayer", "Completa la especialidad Oración", ml="c."),
            T("III.1", "especial", "Crear un álbum de recortes, cartel o collage de cosas que muestren lo que"
              " puede hacer para servir a Dios y a los demás.",
              "Make a scrapbook, poster or collage of things that show what you can do to serve God and"
              " others."),
            H("III.2a", "decisiones", "media-critic", "Desarrollar la especialidad de Analista de comunicación",
              ml="2."),
            H("III.2b", "decisiones", "wise-steward", "Desarrollar la especialidad de Mayordomo sabio", ml="3."),
            H("III.3", "cuerpo", "temperance", "Completa la especialidad de Temperancia", ml="4."),
            T("IV.1a", "familia", "Compartir una forma en que su familia ha cambiado con el tiempo. Compartir cómo"
              " le hacen sentir estos cambios.",
              "Share one way in which your family has changed over time. Share how these changes make you"
              " feel.", ml="1."),
            T("IV.1b", "familia", "Buscar en la Biblia una historia de una familia como la suya.",
              "Find in the Bible a story of a family like yours.", ml="2."),
            T("IV.2a", "cuidan", "Jugar un juego para que cada miembro de su familia muestre su aprecio a cada uno"
              " de los otros miembros de la familia.",
              "Play a game in which each member of your family shows appreciation for each of the other"
              " family members.", ml="1."),
            H("IV.2b", "cuidan", "family-helper", "Completa la especialidad Ayudante de la familia", ml="2."),
            H("IV.3", "ayuda", "first-aid-helper", "Completa la especialidad Ayudante de primeros auxilios"),
            H("V.1", "amigos", "caring-friend", "Completa la especialidad de Amigo cariñoso"),
            T("V.2a", "demas", "Conocer y explicar el himno nacional y la bandera de su país.",
              "Know and explain the national anthem and the flag of your country."),
            T("V.2b", "demas", "Nombrar la capital y el líder de su país.",
              "Name the capital and the leader of your country."),
            {"l": "V.3", "t": "naturaleza", "k": "HONOR_FROM_CATEGORY", "c": NATURE_CATEGORY,
             "es": "Desarrollar una especialidad de la naturaleza que aún no haya obtenido antes.",
             "en": "Complete a Nature award you have not earned before."},
        ],
        "optional": [],
        "optional_es": [],
        "page_awards": ["caring-friend", "build-and-fly", "home-craft", "bead-craft", "astronomer", "family-helper",
                        "first-aid-helper", "bible-iii", "building-blocks", "canoer", "cyclist-ii",
                        "sewing-fun", "media-critic", "disciples", "tin-can-fun", "gymnast", "magnet-fun-i",
                        "magnet-fun-ii", "lizards", "reading-iii", "swimmer-iii", "olympics",
                        "saving-animals", "hand-shadows", "postcards", "troubadour"],
    },
    {
        "slug": "manos-ayudadoras", "code": "MANOS", "page": "manos-ayudadoras", "age": 9,
        "names": {"es": "Manos Ayudadoras", "en": "Helping Hands"},
        "motto": {"es": "Servimos a Dios y a los demás.", "en": "We serve God and others."},
        "requirements": [
            T("I.1", None, "Repite de memoria y acepta el Voto y la Ley de los Aventureros.",
              "Say by heart and accept the Adventurer Pledge and Law."),
            H("I.2", None, "reading-iv", "Completa la Especialidad Lectura IV"),
            H("I.3", None, "hands-of-service", "Completa la especialidad Manos de servicio"),
            T("II.1", "plan", "Crear un dibujo o diagrama que muestre en orden cuándo estos eventos tomaron"
              " lugar.", "Make a drawing or a chart that shows in order when these events took place.",
              sub_es=["Pablo, Martín Lutero, Elena de White, usted mismo"],
              sub_en=["Paul, Martin Luther, Ellen White, yourself"]),
            H("II.2", "mensaje", "bible-iv", "Completa la especialidad Biblia IV"),
            T("II.3a", "poder", "Acostumbrarse a tener regularmente un momento tranquilo para hablar con Jesús y"
              " aprender de él. Mantener un registro.",
              "Get used to having a regular quiet time to talk with Jesus and learn from Him. Keep a record."),
            T("II.3b", "poder", "Preguntarle a tres personas (además de los de su familia) por qué decidieron dar"
              " sus vidas a Jesús O desarrollar la especialidad de Pasos a Jesús.",
              "Ask three people (outside your family) why they decided to give their lives to Jesus OR"
              " complete the Steps to Jesus award.", options=["steps-to-jesus"], choose=1,
              note="Alternativa: texto libre o el award Pasos a Jesús; queda como TEXT (el recomendador"
                   " lo lee del texto cuando el award esté publicado)."),
            H("II.3c", "poder", "my-church", "Completa la especialidad Mi iglesia"),
            T("III.1a", "especial", "Hacer una lista de intereses y habilidades especiales que Dios le haya dado.",
              "Make a list of the special interests and abilities God has given you."),
            {"l": "III.1b", "t": "especial", "k": "HONOR_ANY",
             "es": "Demostrar y compartir su talento al desarrollar una de las especialidades de Aventureros que"
                   " permite la expresión de talentos personales.",
             "en": "Show and share your talent by completing one of the Adventurer awards that allows the"
                   " expression of personal talents."},
            T("III.2", "decisiones", "Aprender los pasos de tomar buenas decisiones. Demostrar o explicar cómo"
              " usarlos para resolver dos problemas de la vida real.",
              "Learn the steps of making good choices. Show or explain how to use them to solve two"
              " real-life problems."),
            H("III.3", "cuerpo", "hygiene", "Completa la especialidad Higiene"),
            T("IV.1a", "familia", "Hacer un banderín o estandarte de familia", "Make a family pennant or banner.",
              ml="1."),
            H("IV.1b", "familia", "my-picture-book", "Desarrollar la especialidad de Álbum de fotos, usando fotos"
              " de la historia de su familia.",
              "Complete the My Picture Book award, using photos of your family's history.", ml="2.",
              note="«Álbum de fotos» de mundoja (ficha 520-album-de-fotos) es My Picture Book del Award Book"
                   " (mismos requisitos: libro de al menos 6 páginas, Joel 1:3)."),
            T("IV.2", "cuidan", "Ayudar a planificar un culto especial con su familia, una noche familiar o una"
              " salida con toda la familia. Reportar sobre qué hicieron con su grupo",
              "Help plan a special worship with your family, a family night or an outing with the whole"
              " family. Report to your group on what you did.", ml="3."),
            H("IV.3", "ayuda", "cooperation", "Completa la especialidad Cooperación", ml="4."),
            H("V.1", "amigos", "early-adventist-pioneers", "Completa la especialidad Pioneros adventistas"),
            H("V.2", "demas", "country-fun", "Desarrollar la especialidad de Diversión con naciones",
              note="«Diversión con naciones» (mundoja, imagen diversion-campestre) es Country Fun."),
            T("V.3", "naturaleza", "Completar dos especialidades de naturaleza no obtenidas anteriormente",
              "Complete two Nature awards you have not earned before.",
              note="Dos awards: queda como TEXT (HONOR_FROM_CATEGORY se cumple con uno solo)."),
        ],
        "optional": ["safe-water", "basket-maker", "carpenter", "stamping-fun-ii", "environmentalist",
                     "outdoor-explorer", "fruits-of-the-spirit", "geologist", "habitats", "sign-language",
                     "honeybees", "prayer-warrior", "rainbow-promise", "pearly-gates", "reporter", "skater",
                     "steps-to-jesus", "bible-royalty", "tabernacle", "technology"],
        "optional_es": ["Agua potable", "Canastero", "Carpintero", "Diversión con sellos", "Ecólogo",
                        "Explorador", "Fruto del Espíritu", "Geólogo", "Hábitat", "Lenguaje de signos", "Miel",
                        "Paladín de oración", "Promesa del arco iris", "Puertas de perla", "Reportero",
                        "Patinador", "Pasos a Jesús", "Realeza bíblica", "Tabernáculo", "Tecnología"],
        "optional_notes": {"honeybees": "«Miel» en la lista de Manos Ayudadoras: la página de awards de la"
                                        " clase muestra Abejas (Honeybees, 533-abejas); Honey es de Abejas"
                                        " Industriosas. Inferido."},
        "page_awards": ["honeybees", "safe-water", "my-picture-book", "bible-iv", "basket-maker", "carpenter",
                        "cooperation", "country-fun", "environmentalist", "stamping-fun-ii", "outdoor-explorer",
                        "fruits-of-the-spirit", "geologist", "habitats", "hygiene", "reading-iv",
                        "sign-language", "hands-of-service", "basic-knots", "skater",
                        "early-adventist-pioneers", "bible-royalty", "reporter", "technology", "weather-ii",
                        "archer"],
    },
]
# mundoja's class logo files (images/2022/04/20/img-nuevo-logo-<name>.png).
LOGO = {"corderitos": "corderos", "aves-madrugadoras": "aves", "abejas-industriosas": "abejas",
        "rayos-de-sol": "rayos", "constructor": "constructor", "manos-ayudadoras": "manos"}
# Book class names as written in the library CSV (class_en).
BOOK_CLASS = {"corderitos": "Little Lamb", "aves-madrugadoras": "Early Bird", "abejas-industriosas": "Busy Bee",
              "rayos-de-sol": "Sunbeam", "constructor": "Builder", "manos-ayudadoras": "Helping Hands"}


# ----------------------------------------------------------------------------------------------
def fix_es(text: str) -> str:
    for wrong, right in ES_FIXES.items():
        text = text.replace(wrong, right)
    return text.strip()


def load_awards() -> dict[str, dict]:
    awards = {a["slug"]: a for a in json.loads(AWARDS_JSON.read_text(encoding="utf-8"))["awards"]}
    with LIBRARY_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            awards[row["slug"]]["library_class_en"] = row["class_en"]
    return awards


def build() -> dict:
    awards = load_awards()
    programs = []
    for order, klass in enumerate(CLASSES, start=1):
        page_url = f"{MUNDOJA}/clubes/aventureros/{klass['page']}"
        by_section: dict[str, list] = {}
        for req in klass["requirements"]:
            by_section.setdefault(req["l"].split(".")[0], []).append(req)
        sections, position, required = [], 0, []
        for roman, (slug, es, en, official) in SECTIONS.items():
            reqs = by_section.get(roman) or []
            out = []
            for req in reqs:
                position += 1
                kind = req["k"]
                # An HONOR requirement always asks for evidence (import_ay_classes, §12.7).
                item = {"position": position, "label": req["l"], "kind": kind,
                        "evidence_required": kind != "TEXT"}
                if req.get("ml"):
                    item["mundoja_label"] = req["ml"]
                if req["t"]:
                    item["topic"] = {"key": req["t"], "es": TOPICS[req["t"]][0], "en": TOPICS[req["t"]][1]}
                en_text = req.get("en")
                if kind == "HONOR":
                    award = awards[req["h"]]
                    item["target_honor_slug"] = AWARD_PREFIX + req["h"]
                    en_text = en_text or f"Complete the {award['title_en']} award."
                    required.append(req["h"])
                if kind == "HONOR_FROM_CATEGORY":
                    item["target_category_slug"] = req["c"]
                item["text"] = {"es": fix_es(req["es"]), "en": en_text}
                sub_en = req.get("sub_en")
                if sub_en is None and req.get("sub_es"):
                    sub_en = [awards[s]["title_en"] for s in req["options"]]  # official award names
                item["sub_items"] = {"es": [fix_es(s) for s in req.get("sub_es", [])], "en": sub_en or []}
                if req.get("options"):
                    item["options"] = [AWARD_PREFIX + s for s in req["options"]]
                    item["choose"] = req["choose"]
                item["translation"] = {"es": "mundoja", "en": "unofficial"}
                item["source_url"] = {"es": page_url, "en": page_url}
                if req.get("note"):
                    item["note"] = req["note"]
                out.append(item)
            sections.append({
                "slug": slug, "position": len(sections) + 1, "names": {"es": es, "en": en},
                "en_official": official,
                "source_url": {"es": page_url, "en": MANUAL_PAGE.format(10) if official else page_url},
                "requirements": out,
            })
        book_class = BOOK_CLASS[klass["slug"]]
        book_awards = [s for s, a in awards.items() if a.get("library_class_en") == book_class]
        listed = set(required) | set(klass["optional"]) | set(klass["page_awards"])
        programs.append({
            "kind": "CLASS", "ministry": "adventurers", "slug": klass["slug"], "code": klass["code"],
            "sort_order": order, "authority": "GC", "issuer_level": "CLUB", "age": klass["age"],
            "names": klass["names"],
            "description": {
                "es": f"Clase de Aventureros para niños de {klass['age']} años. {klass['motto']['es']}",
                "en": f"Adventurer class for children aged {klass['age']}. {klass['motto']['en']}",
            },
            "source_url": {"es": page_url, "en": MANUAL_PAGE.format(24)},
            "emblem": {"file": f"adventurer_classes/{klass['slug']}.webp", "url": None,
                       "source_url": f"{MUNDOJA}/images/2022/04/20/img-nuevo-logo-{LOGO[klass['slug']]}.png"},
            "awards": {
                "required": [AWARD_PREFIX + s for s in required],
                "optional_mundoja": [AWARD_PREFIX + s for s in klass["optional"]],
                "optional_mundoja_es": klass["optional_es"],
                "optional_note": klass.get("optional_note"),
                "optional_notes": {AWARD_PREFIX + k: v for k, v in (klass.get("optional_notes") or {}).items()},
                "mundoja_class_page": [AWARD_PREFIX + s for s in klass["page_awards"]],
                "award_book_class": [AWARD_PREFIX + s for s in book_awards],
                "award_book_class_not_listed_by_mundoja": [AWARD_PREFIX + s for s in book_awards if s not in listed],
                "listed_by_mundoja_other_book_class": {
                    AWARD_PREFIX + s: awards[s]["library_class_en"]
                    for s in sorted(listed) if awards[s].get("library_class_en") != book_class},
            },
            "sections": sections,
        })
    return {
        "source": "mundoja.org",
        "source_url": f"{MUNDOJA}/clubes/aventureros",
        "author": "Mundo J.A (voluntarios)",
        "license": LICENSE,
        "retrieved": "2026-09-24",
        "generated_by": "migrations/catalog_tools/build_adventurer_classes.py",
        "manual": {"title": "GC Adventurer Director's Manual (GC Youth Ministries, 2020)", "url": MANUAL_URL,
                   "local_file": "~/Documents/DEEL/aventureros/directors-manual.pdf"},
        "note": ("Spanish = mundoja.org «Nuevo currículo» (DIA, = GC curriculum), verbatim except ES_FIXES."
                 " English = UNOFFICIAL translation of that Spanish (the GC manual does not print the"
                 " requirements; they are in the GC activity books), except the section names My God /"
                 " My Self / My Family / My World (manual p. 10) and the award names (Award Book 2020)."),
        "es_fixes": ES_FIXES,
        "programs": programs,
    }


# ----------------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", html.unescape(text))
    return re.sub(r"\s+", " ", text).strip()


def check_against_html(data: dict, html_dir: pathlib.Path) -> list[str]:
    """Every Spanish text (before ES_FIXES) must appear in the saved mundoja page of its class."""
    problems = []
    raw = {k["slug"]: k for k in CLASSES}
    for program in data["programs"]:
        page = raw[program["slug"]]["page"]
        path = html_dir / f"clubes_aventureros_{page}.html"
        if not path.exists():
            problems.append(f"{program['slug']}: falta {path}")
            continue
        text = _norm(re.sub(r"<[^>]+>", " ", path.read_text(encoding="utf-8")))
        for req in raw[program["slug"]]["requirements"]:
            for piece in [req["es"], *req.get("sub_es", [])]:
                if _norm(piece) not in text:
                    problems.append(f"{program['slug']} {req['l']}: no está en mundoja: {piece[:60]}")
        for name in raw[program["slug"]]["optional_es"]:
            if _norm(name) not in text:
                problems.append(f"{program['slug']}: opcional no está en mundoja: {name}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--check", action="store_true", help="compare the Spanish with the saved mundoja pages")
    parser.add_argument("--html-dir", default=str(HTML_DIR))
    args = parser.parse_args()
    data = build()
    if args.check:
        problems = check_against_html(data, pathlib.Path(args.html_dir).expanduser())
        for problem in problems:
            print("DIFERENCIA", problem)
        if problems:
            sys.exit(1)
        print("Español: todo coincide con las páginas de mundoja guardadas.")
    pathlib.Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for program in data["programs"]:
        reqs = [r for s in program["sections"] for r in s["requirements"]]
        print(f"{program['slug']}: {len(reqs)} requisitos, {len(program['awards']['required'])} awards requeridos,"
              f" {len(program['awards']['optional_mundoja'])} opcionales")


if __name__ == "__main__":
    main()
