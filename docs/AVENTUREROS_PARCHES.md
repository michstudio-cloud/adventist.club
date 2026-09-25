# Aventureros · librería de parches (Award Book 2020)

Encargo del propietario (2026-09-24): «extrae los parches [de Aventureros] por ahora y deja la
librería lista». Las secciones siguientes tratan **solo la librería de imágenes**; la carga de los
awards y sus requisitos a la base (encargo del mismo día: «súbelas a la base de datos junto con sus
requisitos») está en [Carga a la base](#carga-a-la-base).

## Qué hay

| Dónde | Qué |
|---|---|
| `tools/adventurer_patches.py` | Herramienta reproducible: `extract`, `manifest`, `verify`, `upload`. |
| `requirements-tools.txt` | `pymupdf`, `pillow`, `defusedxml`, `boto3` (+ `scour` opcional). No se instala en Render. |
| `backend/data/adventurer_awards_index.csv` | Índice del libro: categoría, título, clase, página. **164 awards** (ver abajo). |
| `backend/data/adventurer_awards_library.csv` | Manifiesto de la librería (una fila por award). |
| `backend/data/adventurer_awards_mundoja_map.csv` | Correspondencia manual con los 34 PNG de mundoja.org (Espirituales). |
| `backend/data/adventurer_awards/webp/*.webp` | 164 WebP de 512 px (2,8 MB en total). |
| `backend/data/adventurer_awards/svg/*.svg` | 162 SVG optimizados (2,0 MB en total). |
| `~/Documents/DEEL/aventureros/libreria/` | Todo: `svg/`, `png/` (1024 px, ~20 MB), `webp/`, `manifest.csv`, `extract.json`, `contact-sheet.png`, `mundoja-compare.png`. **Los PNG y el PDF no van al repo.** |

### 164 y no 155
Las tablas de cada categoría del propio libro (p. ej. págs. 182, 266 y 326) listan 164 awards: los 155 de las seis
clases y **9 «Multi-level»** que el índice anterior omitía: Dogs (201), Universe (251),
Horsemanship (291), Photo Fun (297), Snowshoeing (309), Bible Storytelling (349),
Bread of Life (351), Good Samaritan (369), Purity (397). Se añadieron al índice con clase
`Multi-level` → «Multinivel».

## Cómo se localiza el parche (sin coordenadas fijas)
Cada página de award dibuja una imagen raster a página completa (madera + cinta blanca) y, encima,
el parche como trazados vectoriales en la esquina superior izquierda. `extract`:

1. copia la página y le quita **todo el texto** (título, número de página);
2. redacta todo lo que está fuera de una zona de búsqueda amplia (x 40–265, y 15–175 pt): la imagen
   de fondo toca el borde de la zona y se elimina entera; el dibujo que queda **totalmente** fuera
   (pie de página, restos fuera de la página) también; lo que sale de la zona pero está recortado
   por el propio parche (p. ej. las montañas de *Habitats*) se conserva;
3. renderiza lo que queda con fondo transparente y toma la caja de los píxeles pintados,
   quedándose con el objeto más grande (los restos sueltos se descartan). Esa caja respeta los
   *clipping paths* del parche, cosa que la caja de `get_drawings()` no hace;
4. vuelve a redactar lo que queda fuera de esa caja y exporta:
   - `svg/<slug>.svg`: salida vectorial de MuPDF recortada a la caja + 2 % de margen, **sin
     `<image>`**, sin recortes a página completa, sin grupos vacíos ni metadatos, coordenadas a
     2 decimales (escalas de `matrix()` a 4) y, si `scour` está instalado, una pasada más (~10 %);
   - `png/<slug>.png`: 1024 px de ancho, transparente, recortado al contenido (render a 2048 y
     reducción Lanczos);
   - `webp/<slug>.webp`: 512 px, calidad 85, con alfa (mismo formato que los parches de
     Conquistadores en `patches/`, que salieron de `backend/migrations/catalog_tools/process.py`:
     recorte al alfa, ≤ 512 px, WebP con alfa).

Casos especiales:
- **Artist** (p. 39) y **Building Blocks** (p. 47) son JPEG con máscara suave en el libro: se
  exporta el píxel original con su alfa (`png/` y `webp/` a tamaño nativo, 351×250 y 86×61 px) y no
  se genera SVG (`has_svg=false`, anotado en `notes`). *Building Blocks* es de muy baja
  resolución: conviene buscar un sustituto.
- **Animal Homes** (p. 187) tiene 8 detallitos raster dentro del dibujo (sobre todo la sombra
  bajo el árbol): están en PNG/WebP, pero el SVG va sin ellos (anotado en `notes`).
- El nombre impreso en la cinta se compara con el del índice (`title_on_page` en `extract.json`).

## Manifiesto (`adventurer_awards_library.csv`)
`slug, name_en, formerly_en, category_en, category_es, class_en, class_es, page, kind, has_svg,
webp, png, svg, width_px, height_px, sha256_webp, webp_bytes, svg_bytes, mundoja_file, webp_url,
notes`

- `slug`: kebab-case ASCII del título inglés **sin** el «(Formerly …)», que va en `formerly_en`
  (`basic-knots`, antes «Knot Tying»). `&` → `and`, apóstrofos fuera (`gods-world`).
- `width_px/height_px`: del WebP publicado. Las rutas `webp/png/svg` son relativas a la librería.
- Categorías: Community→Comunidad, Crafts→Manualidades, Home→Hogar, Nature→Naturaleza,
  Recreation→Recreación, Spiritual→Espiritual.
- Clases (DIA): Little Lamb→Corderitos, Early Bird→**Aves Madrugadoras** (en guiasmayores.com es
  «Castorcitos»; usamos el nombre DIA), Busy Bee→Abejas Industriosas, Sunbeam→Rayos de Sol,
  Builder→Constructor (singular, como en mundoja.org; antes «Constructores»), Helping Hand(s)→Manos Ayudadoras, Multi-level→Multinivel.
- `mundoja_file`: solo Espirituales, desde `adventurer_awards_mundoja_map.csv`. Los 34 PNG de
  mundoja casan por nombre **y** por imagen (ver `mundoja-compare.png`); los de mundoja solo sirven
  para cotejar nombres en español, no como imagen final (158 px con el nombre impreso).
- `webp_url`: lo rellena `upload`. Si el WebP cambia (otro `sha256_webp`), `manifest` lo vacía.

## Comandos
Con el venv que tenga `requirements-tools.txt` (desde la raíz del repo):

```bash
python tools/adventurer_patches.py extract [--pdf "~/Documents/DEEL/aventureros/Award Book 2020.pdf"] \
       [--out ~/Documents/DEEL/aventureros/libreria] [--pages 15,39] [--only slug,slug]   # ~50 s los 164
python tools/adventurer_patches.py manifest [--no-copy]   # CSV del repo + libreria/manifest.csv; copia webp/svg al repo si ≤ 8 MB c/u
python tools/adventurer_patches.py verify                 # comprobaciones + contact-sheet.png + mundoja-compare.png
python tools/adventurer_patches.py upload --r2 --dry-run  # lista lo que subiría
```

`verify` falla (código 1) si falta un archivo, un WebP pesa > 120 KB, un WebP/PNG no tiene alfa o
las esquinas inferiores no son transparentes, un SVG lleva `<image>`, o la proporción ancho/alto se
sale ±15 % de la mediana (todos están entre 1,37 y 1,41; mediana 1,41). La hoja de contacto marca
en rojo lo sospechoso y pinta la transparencia como cuadrícula.

## Subida (pendiente: la hace el propietario)
Nada se ha subido. Dos modos:

### `--r2` (recomendado: clave estable)
```bash
export R2_ACCOUNT_ID=… R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=… R2_BUCKET_NAME=adventist-media
# opcional: R2_PUBLIC_URL (por defecto https://media.adventist.club)
python tools/adventurer_patches.py upload --r2 --dry-run
python tools/adventurer_patches.py upload --r2
```
Sube cada WebP a `patches/adventurers/<slug>.webp` con `Content-Type: image/webp` y
`Cache-Control: public, max-age=31536000, immutable`, y escribe
`https://media.adventist.club/patches/adventurers/<slug>.webp` en `webp_url` (CSV del repo y
`libreria/manifest.csv`). Es idempotente: si el objeto ya existe con los mismos bytes (ETag = MD5)
no lo vuelve a subir. Como la clave es estable y la caché inmutable, **si un parche cambia hay que
subirlo con otro nombre** (o purgar la caché de Cloudflare).
Necesita un token de API de R2 con permiso de escritura sobre el bucket (las mismas
`R2_*` que usa el backend en Render).

### `--api` (sin credenciales de R2)
```bash
python tools/adventurer_patches.py upload --api https://api.adventist.club --token <bearer>
```
Usa `POST /api/v1/media/upload` (multipart `file` + `folder=patches`), igual que los 809 de
Conquistadores (`backend/migrations/catalog_tools/upload.py`). Contrato (`app/routers/media.py`):
roles INSTRUCTOR, COORDINATOR_ZONE, ADMIN_* o MASTER_GC (SVG solo MASTER_GC); imágenes ≤ 10 MB.
**El API elige la clave** (`patches/<uuid>.webp`) y no fija `Cache-Control`: la URL que devuelve se
guarda en `webp_url`, pero no es estable entre subidas. Reanudable: salta las filas que ya tienen
`webp_url` (salvo `--force`).

## Carga a la base
Encargo del propietario (2026-09-24): «sube [las especialidades de Aventureros] a la base de datos junto
con sus requisitos». Tres pasos; los dos primeros ya están hechos y su resultado está en el repo.

| Paso | Herramienta | Resultado (en `backend/data/`) |
|---|---|---|
| 1. Requisitos en inglés del PDF | `backend/migrations/extract_adventurer_awards.py` (pymupdf) | `adventurer_awards.json`: 164 awards, 989 requisitos, sub-incisos, notas del instructor |
| 2a. Español de mundoja (Espirituales) | `backend/migrations/catalog_tools/fetch_mundoja_awards.py` | `adventurer_awards_mundoja_es.json`: 34 fichas (nombre, requisitos «tal cual», «Ayuda») |
| 2b. Español del resto | traducción (ver abajo) | `adventurer_awards_es.json`: nombres y requisitos de 133 awards |
| 3. Carga | `backend/migrations/import_adventurer_awards.py` | 6 categorías, 164 honors, 1.978 filas de requisitos |

### Extracción (paso 1)
Cada award va de su página (manifiesto) a la anterior al award siguiente. El título es el texto
grande (≥ 24 pt); «Requirements» abre la lista y «Supporting Answers» la cierra y abre la guía del
instructor. Un «N.» abre el requisito N solo si es el siguiente esperado (así una lista interna que
reinicia en 1 no rompe la numeración). Los sub-incisos se guardan **dentro del requisito**, una línea
por inciso con su marcador y dos espacios por nivel (`"  a. …"`, `"    i. …"`), igual que las filas de
la wiki de Conquistadores en `honor_requirements.description`: la hoja de la especialidad y el
editor los leen así. Una frase sin marcador después de los incisos («Memorize and repeat two of them.»)
queda como línea sin sangría. Casos del libro resueltos: *Technology* (p. 177) imprime el requisito 1
sin «1.»; «I. Diskette» tras «k.» es «l.»; *Cyclist I*, *Manners Fun* y *Toys* imprimen respuestas bajo
la lista («Answer for #3:», «Idea for #8:»), que pasan a notas; la p. 186 repite los requisitos de
*Animal Homes* y se ignora. «Supporting Answers» numeradas → `instructor_notes_en` del requisito
(«1-2.» cubre ambos); el texto sin número → `general_notes_en`. En 8 awards la guía del libro no sigue
la numeración (My Picture Book, Postcards, Collector, Geologist, Ladybugs, Trees, Camper, Swimmer II) y
se guarda entera en `general_notes_en`; 30 awards no traen guía. Seis awards comprobados a mano contra
el PDF (págs. 17, 177, 201, 203, 339, 405) quedan fijados en `backend/tests/test_adventurer_awards_import.py`.

```bash
python backend/migrations/extract_adventurer_awards.py [--pdf "~/Documents/DEEL/aventureros/Award Book 2020.pdf"]
python backend/migrations/catalog_tools/fetch_mundoja_awards.py [--offline]   # caché en ~/Documents/DEEL/aventureros/mundoja-fichas
```

### Español: oficial (mundoja) y **traducción no oficial**
- **mundoja.org** (voluntarios de Mundo J.A., DIA; sin licencia explícita): nombres de los 34 Espirituales
  y requisitos de 31 de ellos, copiados tal cual (`source='mundoja.org'`, `source_url` = ficha,
  `license` NULL). 191 requisitos. Arrastran erratas del sitio («gramo.» por «g.», «29: 44-» cortado).
- Tres fichas de mundoja traen **otra versión** (más antigua) de la lista, con distinto número de
  requisitos: *Amigo de Jesús* (9 vs 8), *Temperancia* (8 vs 7), *Mayordomo sabio* (8 vs 6). Por defecto
  se usa su nombre de mundoja pero la lista **traducida del libro 2020**; `--mundoja-always` carga la de mundoja.
- **Traducción no oficial** (`source='traduccion-no-oficial-gc-award-book-2020'`, licencia del libro):
  los nombres de 130 awards y los requisitos de 133 (798 requisitos) + las introducciones del libro
  («Awarded to Adventurers who read…»). La hizo una IA (Claude) a partir del texto extraído, fiel al
  libro (mismos requisitos, mismos incisos, en infinitivo, terminología DIA: Corderitos, Aves
  Madrugadoras, Abejas Industriosas, Rayos de Sol, Constructor, Manos Ayudadoras); se revisó por
  muestreo. **Debe revisarla una persona antes de publicarla.** Decisiones a revisar: *Build & Fly* 3
  («kit» del libro → «cometa»), *Outdoor Explorer* 5a (abecedario con ejemplos en español),
  *Basic Knots* 2 (nombres de nudos), *Baking* 3 (términos de repostería), *Gymnast* 6.
- El inglés va siempre como `locale='en'`, `source='gc-award-book-2020'`,
  `source_url=https://www.gcyouthministries.org/ministries/adventurers/#page=<pág. del libro>`,
  `license='© GC Youth Ministries, permiso pendiente'` (40 caracteres: el ancho de la columna; «(permiso
  pendiente)» entre paréntesis no cabía).

### Qué escribe el importador
- Ministerio `adventurers` (debe existir). Categorías `av-comunidad`, `av-manualidades`, `av-hogar`,
  `av-naturaleza`, `av-recreacion`, `av-espiritual` (nombre es + `honor_category_translations` en/pt).
- 164 `honors`: `slug='av-<slug>'`, `code` NULL, `honor_type='OFFICIAL_GC'` (el CHECK solo admite
  OFFICIAL_GC/DIVISIONAL/LOCAL; un award del libro de la Asociación General es lo más cercano a
  «award»), `authority='GC'`, `status='DRAFT'` al crearse, `active=true`, `image_url` = `webp_url` del
  manifiesto, `source_url` = página del libro, `description` = introducción del libro (si la hay) +
  «Clase sugerida: …» (o «Multinivel»). No hay programas de clases de Aventureros todavía, así que la
  clase va en la descripción y no como recomendación.
- `honor_translations` en (nombre y descripción del libro); pt no (no hay texto).
- `honor_requirements`: lista base `es` + lista `en` (un juego de filas por idioma, como Conquistadores).
- **Notas del instructor**: se quedan en el JSON y **no se cargan por defecto**: `instructions` se
  imprime bajo cada requisito en la hoja del miembro y esas notas traen las respuestas.
  `--instructor-notes` las carga igualmente.
- Idempotente: los honors se buscan por (ministerio, slug) y conservan su id; un honor existente
  conserva su estado; los requisitos solo se borran y recrean si cambió el hash de lo que carga el
  importador desde su última ejecución (queda en `audit_log`, acción `HONOR_IMPORT`), así que las
  ediciones hechas en la app y el progreso de los miembros sobreviven a una reejecución; una lista `es`
  escrita en el editor (filas sin `source`) no se toca nunca.

### Comandos (producción: los ejecuta el coordinador)
```bash
cd backend
DATABASE_URL="$NEON_URL" python migrations/import_adventurer_awards.py              # simulacro (ROLLBACK), imprime conteos
DATABASE_URL="$NEON_URL" python migrations/import_adventurer_awards.py --commit     # carga como BORRADOR
DATABASE_URL="$NEON_URL" python migrations/import_adventurer_awards.py --commit --publish   # decisión del propietario
```
Opciones: `--mundoja-always`, `--instructor-notes`, `--operator correo@…` (para `audit_log`).
Necesita `pip install "psycopg[binary]"` (como los demás importadores de `migrations/`; no está en `requirements.txt`) y no descarga nada.

## Licencia
El libro es © GC Youth Ministries. Antes de publicar los parches o los textos, confirmar el permiso
de uso (ver `docs/PENDIENTES_2026-09-24.md` §6).
