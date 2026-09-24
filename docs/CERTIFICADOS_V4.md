# Certificados de especialidad v4: plantillas por elementos en el motor real

Decisión del 23 sep 2026. Fuente: `~/Documents/DEEL/certificados-diseno/especialidades-v4/`
(entregado por el responsable: «ya hay nuevos diseños»). Esa carpeta **sólo se lee**.

## Qué paquetes hay en la carpeta del diseño y cuál se usa

| Carpeta | Qué es | Estado |
|---|---|---|
| `especialidades-v4/` (+ `especialidades-v4-completo.zip`, un `.zip` por diseño, `index.html`) | 4 diseños por elementos (`plantilla.json` + `traducciones.json`), es/en/pt/fr | **Fuente. Instalado.** |
| `especialidades-v3-multilingue/` (+ zip) | versión anterior multilingüe | sustituida por v4, no se instala |
| `especialidades-inspo-v2/` (+ zip) | inspiración anterior | sustituida por v4, no se instala |
| `propuestas-especialidades-2026-09-23/`, `propuestas-maestria-2026-09-23/` | propuestas (fondo + muestra) | no se instalan |
| `ejemplo-ntam-maestria/` | el diseño que dio `ntam-maestria` (converter `tools/design_to_template.py`) | ya instalado antes |
| `degradados-svg/` (+ zip), `emblema conquistadores.svg`, `LEEME.md` | recursos y guía del diseñador | — |

## Camino elegido: un compilador, no un segundo motor

`tools/compile_element_template.py <carpeta-v4> --slug <slug> [--install] [--out DIR] [--preview DIR]`
convierte el paquete en una plantilla **del motor existente** (`backend/app/certificates/render.py`):
mismo `load_template`, mismo `fill_svg`, mismo resvg, mismos `GET /templates` y `POST /render`,
misma emisión. Descartado: portar `renderizador.js` como un motor paralelo (dos caminos de render,
dos contratos) y rasterizar (`design_to_template.py` hace fondo PNG; aquí todo sigue vectorial).

| Paquete v4 | Plantilla del motor |
|---|---|
| página 1100 × 850 (Carta horizontal) | `viewBox="0 0 792 612"` (puntos) + `<g transform="scale(0.72)">`: las coordenadas del diseño se conservan tal cual |
| `shape` (rect/path + atributos) | el mismo elemento SVG, vectorial, en el mismo orden |
| `image` con el emblema oficial | ranura `emblem` (el motor pone el emblema del ministerio; es el mismo archivo) |
| `image` `honor_image` | ranura `honor_patch` (contain, centrado, sin recorte: `preserveAspectRatio="xMidYMid meet"`) |
| `image` `qr` + `placeholder_asset` | ranura `qr`; sin QR se ve la caja gris del diseño (`data-placeholder-href`) |
| `text` con `source.kind = translation` | `<text id="t_<id>" data-string="<clave>">`; la clave sale de `strings.<locale>.json` |
| `text` con `source.kind = data` | `<text id="<clave>">` (`folio` → `certificate_no`) |
| `text` con `source.kind = date` | `<text id="issued_date" data-format="date-long">` |
| `max_width`, `min_font_size`, `max_lines`, `line_height`, `overflow` | `data-fit="shrink-wrap" data-max-width data-min-size data-max-lines data-line-height` |
| `anchor`/`align`, `rotate`, `color` | `text-anchor`, `transform="rotate(...)"`, `fill` |
| `visible_if {qr_image is_null}` | `data-hide-if-image="qr"` (el rótulo «QR» desaparece con un QR real) |
| `traducciones.json` | `strings.es|en|pt|fr.json` (+ `church_name` de `datos-ejemplo.json`) |
| — | `meta.json`: `{"kinds": ["honor"], "ministries": ["pathfinders"], "engine": "elements", "title", "source", "locales"}` |

Regenerar las cuatro (tras un cambio del diseñador):

```bash
V=~/Documents/DEEL/certificados-diseno/especialidades-v4
for p in "01-editorial-rojo editorial-rojo" "02-modular-azul modular-azul" \
         "03-reticula-verde reticula-verde" "04-academico academico"; do
  set -- $p; python tools/compile_element_template.py "$V/$1" --slug "especialidad-$2" --install
done
```

`template.svg` no se edita a mano: un test compila de nuevo desde la carpeta (si existe en la máquina)
y exige archivos idénticos.

## Plantillas instaladas

| Slug | Diseño | Tipo | Ministerio | Idiomas |
|---|---|---|---|---|
| `especialidad-editorial-rojo` | 01 Editorial rojo | honor | pathfinders | es, en, pt, fr |
| `especialidad-modular-azul` | 02 Modular azul | honor | pathfinders | es, en, pt, fr |
| `especialidad-reticula-verde` | 03 Retícula verde | honor | pathfinders | es, en, pt, fr |
| `especialidad-academico` | 04 Académico | honor | pathfinders | es, en, pt, fr |

Las plantillas anteriores (`especialidad-basica`, `-media`, `investidura-clase`, `ntam-maestria`)
no cambian. `GET /api/v1/certificates/templates` añade `title` (de `meta.json`) para que el asistente
muestre «Editorial rojo» en vez de derivarlo del slug; `?ministry=pathfinders&kind=honor` las incluye.

## Cambios del motor (`render.py`), sólo activos con los atributos nuevos

- `fit_lines` + `data-fit="shrink-wrap"`: el algoritmo de `renderizador.js` — de `font_size` a
  `min_font_size` en pasos de 1: una línea; si no, envoltura por palabras hasta `max_lines`, todas
  dentro de `max_width`. Si nada cabe: `TemplateError` con el nombre del campo (la API responde 422).
  **Nunca se recorta.** Las líneas son `<tspan x dy>` con `dy = tamaño × line_height`.
- Traducción faltante (`data-string` sin clave en el idioma) o idioma no soportado: `TemplateError`,
  sin español de relleno. (Las plantillas antiguas conservan su respaldo al español del SVG.)
- `data-required`: `recipient_name`, `honor_name`, `issued_date` son obligatorios (422 si faltan);
  el resto, si falta, desaparece (no se inventa nada).
- Fechas: `format_long_date` — `es` «21 de septiembre de 2026», `en` «September 21, 2026»,
  `pt` «21 de setembro de 2026», `fr` «21 septembre 2026» (lo mismo que `Intl.DateTimeFormat` con
  `{day, month: long, year}` en UTC). Si llega ya formateada (el asistente la manda así), se respeta.
- Alias del vocabulario v4: datos `folio` → `certificate_no`; imágenes `honor_image` → `honor_patch`,
  `qr_image` → `qr`.
- `data-synthetic-bold`: `04-academico` pide Noto Serif 700 pero el paquete trae sólo Noto Serif 400,
  así que sus vistas muestran la negrita sintética del navegador (mismo avance, contorno ensanchado
  `tamaño/32` como Skia). El compilador lo detecta (peso no suministrado en `fonts` del paquete) y el
  motor reproduce ese aspecto: con Noto Serif Bold real el titular salía un 6–8 % más ancho y distinto de lo
  aprobado. Si el diseñador entrega Noto Serif Bold en el paquete, al recompilar se usará la real.
- Medición: `_font` elige la familia exacta (`Noto Sans` ya no puede caer en `NotoSansMono`, el
  orden de `glob` lo hacía posible). Pillow sin raqm no aplica el kerning GPOS: mide ≤ 1 % más ancho
  que el navegador, así que en un caso límite elige un tamaño un punto menor; nunca desborda.

Fuentes: `fonts/` ya tenía Noto Sans 400/700 y Noto Serif 400 (+ Serif 700, Sans Mono) con `OFL.txt`;
los TTF del paquete tienen las mismas métricas (Noto Serif 400 es idéntico byte a byte). No se copió nada.

## Datos: de la emisión a la plantilla

`POST /api/v1/certificates/render` con `certificate_no` de un certificado **emitido** y una plantilla
`engine: elements` completa los datos desde el registro (`services/certificates.render_data`), y el
registro **manda** sobre lo que envíe el cliente (una página con folio no se puede alterar):

| Campo | Origen |
|---|---|
| `recipient_name`, `director_name`, `instructor_name` | `certificates` |
| `honor_name` | `honor_translations` en el idioma pedido; español (`honors.name`) si no hay; el snapshot si la especialidad ya no existe |
| `issued_date` | `certificates.issued_date` (ISO → fecha larga del idioma) |
| `certificate_no` (folio) | `certificates.certificate_no`; el QR de verificación lo añade el endpoint como siempre |
| `organization_name` | la asociación de la que cuelga el club de la inscripción; si no, la asociación de la organización emisora; si no, el nombre de la emisora |
| `church_name` | cadena traducida de la plantilla («Iglesia Adventista del Séptimo Día», «Seventh-day Adventist Church», «Igreja Adventista do Sétimo Dia», «Église adventiste du septième jour»); el cliente puede enviar otra |
| `honor_patch` | `honors.image_url` si es del bucket de medios (misma lista blanca que ya usa el endpoint); si no, sin insignia |

Sin folio (vista previa del asistente antes de emitir) se usan los datos del formulario; el QR es la
caja gris con «QR», como en las vistas del diseño.

## Verificación (23 sep 2026)

Render del motor a 300 dpi (3300 × 2550) con `datos-ejemplo.json` contra `vistas/<idioma>.png`
(Chrome), diferencia por píxel en escala de grises (0–255):

| Plantilla | media es / en / pt / fr | píxeles > 32 | píxeles > 128 | máx. |
|---|---|---|---|---|
| editorial-rojo | 0.39 / 0.36 / 0.39 / 0.36 | 0.45–0.49 % | ≤ 0.01 % | 183 |
| modular-azul | 0.31 / 0.29 / 0.31 / 0.29 | 0.36–0.38 % | ≤ 0.01 % | 187 |
| reticula-verde | 0.32 / 0.31 / 0.33 / 0.30 | 0.36–0.39 % | ≤ 0.02 % | 191 |
| academico | 0.31 / 0.30 / 0.31 / 0.29 | 0.36–0.38 % | ≤ 0.01 % | 185 |

Todo lo que difiere es el antialiasing de los bordes del texto (resvg frente a Skia); mismas posiciones,
tamaños, cortes de línea y colores. Un render a 300 dpi tarda ~1 s en un portátil (4× los píxeles de
¼ carta); en la instancia de 0.15 CPU, varios segundos, como las demás plantillas vectoriales.

Tests: `backend/tests/test_certificates_v4.py` (carga y `meta`, cada cadena traducida presente por
plantilla e idioma, nunca «QR» con QR real, fechas, encoger/envolver/error, alias, negrita sintética,
PNG/PDF/SVG, reproducibilidad del compilador, `GET /templates`, `POST /render` y datos desde la emisión).

## Firmas manuscritas (24 sep 2026)

Pedido del responsable: «nos falta agregar una imagen como firma. Para guardar el archivo pidamos que se
registren… sin cuenta haremos solo uno por uno».

**Ranuras.** Toda plantilla puede tener `<image id="signature_director">` y `<image id="signature_instructor">`
(`IMAGE_FIELDS` del motor). Sin imagen no se dibuja nada (`opacity="0"`, como el parche) y el nombre impreso
queda exactamente donde estaba.

- Compiladas (v4 y cualquier paquete nuevo, p. ej. `especialidad-dorada`): el compilador las **deriva** de
  cada texto de datos `director_name` / `instructor_name` y de su línea de firma (la regla horizontal
  `M x y h w` o `<line>` más cercana, a ≤ 3 cuerpos del nombre y solapada con su caja). Caja: el ancho de la
  línea; alto `2.2 × font_size` del nombre; termina a la altura de las mayúsculas del nombre
  (`baseline − 0.75 × cuerpo`) si el nombre va sobre la línea, o sobre la línea si el nombre va debajo. Sin
  línea, la caja del propio nombre (`max_width`). Se emite **antes** del nombre (el texto queda encima).
  `preserveAspectRatio="<x>YMax meet"`: contenida, nunca recortada, apoyada abajo y alineada como el nombre
  (`xMin` si el nombre es `start`, `xMid` si va centrado): en v4 los nombres van a la izquierda y una firma
  centrada sobre un nombre alineado a la izquierda se veía desplazada. En v4: `x = x del nombre`, `y = 667.7`,
  `235 × 30.8`. Recompilar con el mismo comando de arriba; el test de reproducibilidad sigue exigiendo archivos
  idénticos.
- `investidura-clase` (SVG hecho a mano): dos `<image>` añadidas a mano sobre sus líneas (`87.2 × 12.1`,
  apoyadas en la línea, `xMidYMax`: sus nombres van centrados debajo).

**`POST /certificates/render`.** `images.signature_director|signature_instructor` = data URL o URL https del
bucket de medios (lo mismo que las demás imágenes). Además, `app/certificates/signatures.py`:
sólo PNG / JPEG / WebP (**SVG rechazado**: es marcado y una firma no lo necesita), ≤ **400 KB**, ≤ 4000 px por
lado y ≤ 8 MP (un archivo pequeño que se descomprime en un lienzo enorme se rechaza), el contenido debe
coincidir con el tipo declarado. Lo que llega a resvg se recodifica a PNG (transparencia intacta) de 1600 px
como mucho en el lado largo: la ranura mide ~2.35 in, así que ni a 600 dpi se pierde nada y el tiempo de render
no depende de lo que se subió. Medido: editorial-rojo a 300 dpi 1.7–2.6 s con o sin dos firmas (sin diferencia
apreciable); `investidura-clase` 0.3 s; normalizar una firma, ~20 ms. Error → 422 con el motivo.

**Firma guardada en la cuenta** (`020_signatures.sql`: `users.signature_url`):
`POST /api/v1/users/me/signature` (multipart `file`) valida con las mismas reglas, sube el PNG normalizado por el
mismo camino de R2 que `POST /media/upload` (`storage.upload_bytes`) pero a su carpeta propia `signatures/`
(interna: `media/upload` no escribe ahí), guarda la URL, borra la anterior y registra `SIGNATURE_SAVED`.
`DELETE` la olvida (idempotente, borra el objeto, `SIGNATURE_DELETED`). Un menor no guarda firma (403).
`signature_url` sólo sale en `GET /auth/me` y `GET /users/me` (`UserResponse.from_model(..., own=True)`); los
listados y el registro de otra persona la llevan en `null`.

**Decisión: bucket público con clave impredecible, no el privado.** La firma existe para imprimirse: cada
certificado que la lleva es público por diseño (PNG/PDF compartido, verificación por QR), así que el archivo
privado no protegería algo que el propio certificado ya muestra. El bucket privado exigiría URL firmadas que
caducan (el asistente las perdería a mitad de un lote) y un camino especial en `/render`, que hoy sólo descarga
del host de medios. Protección real: clave `uuid4` de 128 bits sin listado, la URL sólo la ve su dueño, el
reemplazo y el borrado eliminan el objeto, y el render la vuelve a validar. Si el responsable prefiere privada, el
cambio es local: guardar la clave, subir con `private_storage` y que `/render` acepte `signature_*: "me"` con sesión.

## Pendiente (fuera del backend)

- El asistente (`conquistadores-app/lib/certificate-templates.ts`) no tiene etiqueta para los slugs
  nuevos: puede usar el `title` del listado. Hoy muestra «Especialidad editorial rojo» derivado del slug.
- Las traducciones del paquete son de prueba (lo dice el propio LEEME): validar la terminología local
  (p. ej. «EXPLORATEURS», «Honor completion») antes de emitir en masa; al cambiar `traducciones.json`
  se recompila.
- En `01-editorial-rojo` un nombre de especialidad a dos líneas deja la segunda a ~30 unidades de la
  fecha: es la geometría del diseño (el renderizador de referencia hace lo mismo).
