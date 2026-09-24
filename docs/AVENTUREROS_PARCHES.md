# Aventureros · librería de parches (Award Book 2020)

Encargo del propietario (2026-09-24): «extrae los parches [de Aventureros] por ahora y deja la
librería lista». Esto es **solo la librería de imágenes**: no crea honors, categorías ni filas en
ninguna base de datos.

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
  Builder→Constructores, Helping Hand(s)→Manos Ayudadoras, Multi-level→Multinivel.
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

## Licencia
El libro es © GC Youth Ministries. Antes de publicar los parches o los textos, confirmar el permiso
de uso (ver `docs/PENDIENTES_2026-09-24.md` §6).
