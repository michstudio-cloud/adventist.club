# Aventureros · clases (currículo GC / «Nuevo currículo» DIA)

Encargo del propietario (2026-09-24): «Aventureros está en inglés, pero puedes buscar en
https://mundoja.org/clubes/aventureros para rellenar lo que falte. […] vamos a usar pero dejar los
enlaces para dejar su autoría.»

## Qué hay

| Dónde | Qué |
|---|---|
| `backend/data/adventurer_classes.json` | Las 6 clases: secciones, 121 requisitos en es y en, destino (award/categoría), `source_url` en todo, awards requeridos / opcionales / del Award Book por clase. |
| `backend/migrations/catalog_tools/build_adventurer_classes.py` | Genera el JSON desde la tabla curada; `--check` compara el español con las páginas de mundoja guardadas. |
| `backend/migrations/import_adventurer_classes.py` | Importador idempotente (`--dry-run` / `--commit` / `--publish` / `--image-base-url`). |
| `backend/data/adventurer_classes/<slug>.webp` | Emblemas de clase (arte GC de los pines, tomado de mundoja), recortados, ≤ 256 px, 13–16 KB. **Sin subir a R2.** |
| `backend/data/adventurer_resources.json` | Recursos del club (4 páginas de mundoja + manual GC + materiales por clase), resumen y enlaces. No se carga. |
| `~/Documents/DEEL/aventureros/clases/` | `original/` (PNG de mundoja), `<slug>.webp`, `mundoja-html/` (copia de las páginas consultadas). |
| `backend/tests/test_adventurer_classes_import.py` | JSON, importador, idempotencia, `--publish`, recomendaciones. |

## Fuentes y qué sale de cada una
- **GC *Adventurer Director's Manual*** (74 págs., 2020): los seis niveles (Little Lamb, Early
  Bird, Busy Bee, Sunbeam, Builder, Helping Hands; págs. 9, 24, 54), las cuatro categorías «My God,
  My Self, My Family, My World» (pág. 10), voto y ley (pág. 17). **No trae los requisitos de las
  clases**: remite a los seis libros de actividades (págs. 24, 54 y 70), que no están en las fuentes
  entregadas. Por eso no hay texto oficial en inglés de los requisitos.
- **mundoja.org** (voluntarios Mundo J.A, DIA), una página por clase: requisitos en español del
  «Nuevo currículo» (= currículo GC; las mismas clases y los mismos awards que el *Award Book
  2020*). Es la base del español, copiado tal cual salvo 3 erratas (`es_fixes` en el JSON: «dÍa»,
  «Jesús}», «Lectura l.»). `--check` verifica que cada texto sigue en las páginas guardadas.
- **Inglés**: traducción **no oficial** del español de mundoja, marcada en cada requisito
  (`translation.en = "unofficial"`, `source = 'traduccion-no-oficial-mundoja'` en la base), salvo
  los nombres de sección My God / My Self / My Family / My World (manual pág. 10, `en_official`) y
  los nombres de los awards (oficiales, del Award Book).
- Totales: 121 requisitos; **121 en español oficial de mundoja** (3 con errata corregida), **121 en
  inglés no oficial** (63 de ellos son «Complete the <award oficial> award.»).

## Nombres (equivalencias)

| slug | mundoja (es, exacto) | Manual GC (en) | Award Book / CSV `class_en` | CSV `class_es` | Edad (mundoja) |
|---|---|---|---|---|---|
| `corderitos` | Corderitos | Little Lamb | Little Lamb | Corderitos | 4 |
| `aves-madrugadoras` | Aves Madrugadoras | Early Bird | Early Bird | Aves Madrugadoras | 5 |
| `abejas-industriosas` | Abejas Industriosas | Busy Bee | Busy Bee | Abejas Industriosas | 6 |
| `rayos-de-sol` | Rayos de Sol | Sunbeam | Sunbeam | Rayos de Sol | 7 |
| `constructor` | **Constructor** | Builder | Builder | ~~Constructores~~ → **Constructor** | 8 |
| `manos-ayudadoras` | Manos Ayudadoras | Helping Hands | Helping Hands | Manos Ayudadoras | 9 |

- **Corregido**: mundoja llama a la clase «Constructor» (singular, en la ficha y en el índice);
  el CSV decía «Constructores». Cambiado en `adventurer_awards_library.csv`,
  `adventurer_awards.json`, `tools/adventurer_patches.py`, `import_adventurer_awards.py` y su test.
  En producción las descripciones de 29 awards dicen «Clase sugerida: Constructores.»: se
  corrigen al volver a correr `import_adventurer_awards.py --commit` (solo reescribe la
  descripción; los requisitos no cambian).
- **Castorcitos ≠ Aves Madrugadoras**: la URL de mundoja sigue siendo `/castorcitos2`, pero la
  clase de 5 años del currículo nuevo es *Aves Madrugadoras* (Early Bird). «Castorcitos» (≈ Eager
  Beaver de la NAD) es la **cartilla anterior** de la DIA (`/castorcitos2/598-castorcitos`).
- El manual dice «Helping Hands» (plural); el Award Book usa ambas formas.

## Estructura y etiquetas
Cinco secciones por clase: **I Requisitos básicos**, **II Mi Dios**, **III Yo mismo**, **IV Mi
familia**, **V Mi mundo**. Dentro de II–V, tres temas fijos (12 en total: *El plan de Dios para
salvarme · El mensaje de Dios para mí · El poder de Dios en mi vida · Soy especial · Puedo tomar
buenas decisiones · Puedo cuidar mi cuerpo · Tengo una familia · En las familias se cuidan unos a
otros · Mi familia me ayuda a cuidarme · El mundo de los amigos · El mundo de los demás · El mundo
de la naturaleza*). Etiqueta = `Sección.Tema[letra]` (II.1a, IV.3…): el dígito es el tema. En
Constructor y Manos Ayudadoras mundoja reinicia o salta la numeración; se normaliza y el original
queda en `mundoja_label`. El tema va en el JSON (`topic`), **no en la base** (el modelo no tiene
subsecciones; el dígito de la etiqueta lo conserva). Propuesta si se quiere mostrar: columna
`program_requirements.topic` o subsecciones.

| Clase | Requisitos | Award concreto (HONOR) | Otros |
|---|---|---|---|
| Corderitos | 19 | 13 | V.3 «al menos dos de» 5 awards (TEXT con opciones) |
| Aves Madrugadoras | 19 | 13 | — |
| Abejas Industriosas | 22 | 9 | — |
| Rayos de Sol | 19 | 9 | — |
| Constructor | 23 | 10 | V.3 = un award de la categoría Naturaleza (`av-naturaleza`) |
| Manos Ayudadoras | 19 | 9 | III.1b = cualquier award (HONOR sin destino); II.3b y V.3 TEXT |

## Recomendaciones award → clase
Las recomendaciones (`GET /programs/{id}/recommendations`, `services/program_recommendations.py`)
salen de los requisitos: HONOR con destino → ese award («all»); HONOR con categoría → hasta 8
sugerencias de la categoría («one»); HONOR sin destino → «any»; TEXT que nombra awards → el
emparejador de nombres (solo lee awards **publicados**). `HONOR_MINISTRY_OF` ya apunta
`adventurers → adventurers`, así que funciona sin cambios de código. Resultado: 65 requisitos
recomiendan (63 awards concretos + Naturaleza + «cualquiera»).

Los awards **opcionales** de cada clase («Otras especialidades … si el tiempo lo permite» de
mundoja) y la lista completa de la clase según el Award Book (`class_en` del CSV) van en
`programs[].awards` del JSON (`optional_mundoja`, `award_book_class`). **No se cargan**: el modelo
deriva las recomendaciones solo de los requisitos y un requisito opcional no existe. Propuesta:
tabla `program_suggested_honors (program_id, honor_id, kind OPTIONAL|BOOK)` y un bloque «Otras
especialidades para esta clase» en la ficha.

## Diferencias GC / DIA encontradas
1. **Requisitos**: no hay texto GC que comparar (el manual no los trae). Lo comparable coincide:
   las seis clases, las cuatro categorías y los awards. Cada award que el *Award Book 2020* asigna
   a una clase aparece en la página de esa clase en mundoja (requerido u opcional): 0 faltantes en
   las seis clases.
2. **Listas de awards de mundoja con un award de otra clase del libro**: *Biblia I* aparece en la
   página de Corderitos (en el libro es de Busy Bee; en Abejas es requisito II.2) y *Nadador II* en
   la de Aves Madrugadoras (libro: Busy Bee; en Abejas es opcional). Probable errata de mundoja.
3. **Aves Madrugadoras I.3 «Completa la especialidad Aves Madrugadoras»**: no existe un award con
   ese nombre ni en el libro ni en mundoja. Se apunta a **Birds** (mundoja: «Pajaritos», en la lista
   de la clase; mismos requisitos) — **inferido**, anotado en `note`. Cotejar con el libro de
   actividades antes de publicar.
4. **Nombres de awards distintos** entre la ficha de requisitos de mundoja y el catálogo cargado
   (`adventurer_awards_es.json`): «Escuchando la Historia I» / «Escuchar historias I»,
   «Corderito Lanudo» / «Corderito lanudo», «Usar los Dedos» / «Juegos con los dedos», «Sano y
   Fuerte» / «Soy saludable», «Alimentos Sanos» / «Alimentos saludables», «Diversión con Modales» /
   «Buenos modales», «Tesoro Escondido» / «Búsqueda del tesoro», «Cultura Física» / «Diversión con
   la actividad física», «Analista de comunicación» / «Crítico de medios», «Amigo cariñoso» /
   «Amigo bondadoso», «Diversión con naciones» / «Diversión con países», «Álbum de fotos» / «Mi
   libro de imágenes», «Bloques» / «Bloques de construcción», «Escuchar» / «Escuchando»… Los
   destinos se resolvieron a mano (no dependen del nombre). Si se quiere que el español del
   catálogo de awards coincida con mundoja, es otra tarea (nombres de 136 awards no espirituales).
5. **«Miel» en los opcionales de Manos Ayudadoras**: la página de awards de la clase muestra
   *Abejas* (Honeybees); *Honey* es de Abejas Industriosas. Se tomó Honeybees (inferido).
6. **Cartilla anterior DIA** (enlazada desde cada clase): estructura distinta (Castorcitos en
   lugar de Aves Madrugadoras; requisitos «cantar / escuchar / decir tres cosas / manualidad» por
   categoría). No se carga; queda enlazada en `adventurer_resources.json`.

## Emblemas
PNG 257×249 de mundoja (`/images/2022/04/20/img-nuevo-logo-<clase>.png`): arte de los pines GC
(el mismo de las portadas de los libros de actividades del manual, págs. 24/55), descargable.
Recortados al alfa y guardados en WebP (calidad 90) en `backend/data/adventurer_classes/` y en
`~/Documents/DEEL/aventureros/clases/`. El JSON lleva `emblem.file`, `emblem.source_url` y
`emblem.url = null`: tras subirlos a R2, o se rellena `emblem.url` o se pasa
`--image-base-url <carpeta pública>` al importador. Sin URL, un `image_url` ya puesto se conserva.

## Recursos
`backend/data/adventurer_resources.json`: *Soy padre o madre*, *Programa anual*, *Ideas para
reuniones*, *Ceremonias y eventos* (título, resumen propio de 2–3 líneas, apartados, enlace,
`author = 'Mundo J.A (voluntarios)'`), el índice del manual GC y los materiales de cada clase
(cartilla DIA, libro de actividades GC, manual de ayuda; enlaces, no descargados).
No existe un mecanismo de recursos por ministerio o programa (`honor_resources` y `courses` cuelgan
de una especialidad; `programs` no tiene enlaces), así que **no se cargan**. Propuesta:
1. sección **«Recursos»** en la página del ministerio Aventureros (tarjetas con título, resumen,
   autor y enlace externo), alimentada por este JSON servido como estático o por una tabla
   `ministry_resources (ministry_id, slug, kind, audience, title, summary, url, author, position)`;
2. en la ficha de cada clase, bloque **«Materiales»** con los tres enlaces de `class_materials`
   (tabla hermana `program_resources` o columna `links jsonb` en `programs`).

## Producción (coordinador)
```bash
cd backend/migrations
# 0) (opcional) corregir «Constructores» → «Constructor» en las descripciones de los awards
DATABASE_URL=... python import_adventurer_awards.py --commit
# 1) simulacro y carga en BORRADOR
DATABASE_URL=... python import_adventurer_classes.py
DATABASE_URL=... python import_adventurer_classes.py --commit
# 2) emblemas: subir backend/data/adventurer_classes/*.webp a R2 y volver a correr con la carpeta
DATABASE_URL=... python import_adventurer_classes.py --commit --image-base-url https://media.adventist.club/<carpeta>
# 3) publicar (decisión del propietario): primero los awards, luego las clases
DATABASE_URL=... python import_adventurer_awards.py --commit --publish
DATABASE_URL=... python import_adventurer_classes.py --commit --publish
```
`--publish` falla (y no escribe nada) si algún award de destino no está publicado, igual que
`POST /programs/{id}/publish`. Una clase ya publicada no se reescribe nunca.
