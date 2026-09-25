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
| `especialidad-editorial-rojo` | Editorial rosa y rojo (`replica-especialidad-editorial/`, 24 sep 2026; antes 01 Editorial rojo) | honor | pathfinders | es, en, pt, fr |
| `especialidad-modular-azul` | Bloques azules (`replica-especialidad-azul/`, 24 sep 2026; antes 02 Modular azul) | honor | pathfinders | es, en, pt, fr |
| `especialidad-reticula-verde` | 03 Retícula verde | honor | pathfinders | es, en, pt, fr |
| `especialidad-academico` | 04 Académico | honor | pathfinders | es, en, pt, fr |
| `especialidad-dorada` | Especialidad dorada (`replica-especialidad/`, 24 sep 2026) | honor | pathfinders | es, en, pt, fr |

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

## Especialidad dorada (replica-especialidad)

Entregado por el propietario el 24 sep 2026: `~/Documents/DEEL/certificados-diseno/replica-especialidad/`
(`plantilla.json` schema 1.0, `traducciones.json` es/en/pt/fr, `datos-ejemplo.json`, `recursos/`,
`vistas/*.svg`, `muestra.png`). Mismo contrato que v4, así que se **compila** con el mismo
`tools/compile_element_template.py` (extendido, no bifurcado) a `templates/certificates/especialidad-dorada/`.
`renderizador.js`, `payload.js`, `index.html` y `recursos/qr-referencia.svg` no se usan.

Regenerar (tras un cambio del diseñador; también copia a `fonts/` las caras que falten):

```bash
python tools/compile_element_template.py ~/Documents/DEEL/certificados-diseno/replica-especialidad \
  --slug especialidad-dorada --install [--preview /tmp/vistas]
```

### Qué añade al compilador (los cuatro v4 recompilan byte a byte igual)

| Paquete | Plantilla del motor |
|---|---|
| página 1600 × 1237 (no es exactamente 11:8,5: 1,2935 frente a 1,2941) | `<g transform="translate(0.2037 0) scale(0.494745)">`: escala uniforme y centrado, lo mismo que hace el navegador con el `viewBox` del diseño (`meet`). Más de un 1 % de desproporción es un error de compilación |
| `frame` (`marco.svg`), `church_logo` (`logo.svg`), `seal` (`sello.svg`) + `paper` | **aplanados** en `background.webp` (ver abajo); `<image id="background">` a página completa, fuera del grupo escalado |
| `honor_image` | ranura `honor_patch`, `preserveAspectRatio="xMidYMid meet"` (parche sin contenedor, proporciones intactas) |
| `qr` + `qr-marcador.svg` | ranura `qr`; sin QR, la caja gris (`data-placeholder-href`) |
| `church_name` (dato, objeto por idioma) | `<text id="church_name" data-fallback-string="church_name">`: `strings.<idioma>.json` lo trae de `datos-ejemplo.json`; el cliente puede mandar otro |
| `association_name` (dato, objeto por idioma) | `<text id="association_name">` **sin** respaldo: viene del árbol de organizaciones (abajo); si no hay, la línea no se dibuja |
| `folio` | `certificate_no` |
| `date-value` con `{day: 2-digit, month: 2-digit, year: numeric}` | `data-format="date-numeric"`: es/pt/fr `21/09/2026`, en `09/21/2026` (Intl en-US). Acepta ISO o la fecha larga que manda el asistente («21 de septiembre de 2026», «September 21, 2026»…) y la reescribe |
| `title_0`/`title_1`, `awarded`, `completion`, `date`, `director`, `instructor` | `data-string`; `title` (lista) y `association` (una asociación de ejemplo) **no** pasan a `strings`: nada puede imprimir la asociación de muestra |
| `max_width`/`min_font_size`/`max_lines`/`line_height` | lo de siempre: `fit_lines` (encoger → envolver → 422) |

Otras reglas nuevas del compilador: sólo `fit: contain` sin recorte; una imagen fija que es un SVG
recibe un `clipPath` con su caja (el navegador recorta un `<image>` a su caja y resvg no: sin eso el
logo y el sello pintaban la página completa del PDF de la que son ventanas).

### Fuentes

`fonts/` recibe `Poppins-Black.ttf` (900), `Poppins-Bold.ttf` (700), `Poppins-Light.ttf` (300),
`Lato-Regular.ttf` (400), `AdventSans-Beta.otf` («Advent Sans» 400) con `OFL-poppins.txt`,
`OFL-lato.txt` y `OFL-adventsans.txt`. resvg las encuentra por familia y peso (fontdb, reglas CSS).
El medidor de `fit_lines` (`_font`) ahora también elige por **peso** (antes sólo normal/negrita, que
habría medido Poppins 900 con la Bold) y mira `.otf`; con las Noto elige exactamente lo mismo que antes.

**Advent Sans Beta**: el paquete no trae licencia. La tabla `name` de la fuente declara «licensed
under the SIL Open Font License, Version 1.1», diseño del equipo de Monotype, «Noto is a trademark of
Google Inc.», sin aviso de copyright. `OFL-adventsans.txt` recoge eso + el texto OFL. **Pendiente del
propietario**: confirmar el origen (kit de identidad de la IASD) y que la OFL declarada vale para esta
versión Beta antes de imprimir en masa.

### Raster: de 4 × 1,7 MB a una página WebP de 136 KB

`marco.svg`, `logo.svg`, `sello.svg` y `qr-referencia.svg` eran la **misma** página PNG 3300 × 2550
(la del PDF original, 1,30 MB, en base64 ×4): el marco la recorta con un `clipPath` con hueco y el logo
y el sello son ventanas (`viewBox`) sobre ella. Incrustarlos tal cual daba un `template.svg` de
~6,9 MB y resvg tardaba **~10,5 s sólo en esas capas** por cada render a 300 dpi en un portátil
(decodifica y remuestrea la página entera cuatro veces); en la instancia de 0,15 CPU, más de un minuto.

El compilador detecta que las capas del fondo (las primeras, fijas: papel, marco, logo, sello; nada
dinámico debajo de ellas) llevan raster y las dibuja **una vez**, con resvg, a la resolución del
paquete (`raster_width × raster_height` = 3300 × 2550, 300 dpi: la nativa del PDF, no se inventa
detalle) sobre papel blanco, y guarda `background.webp` (WebP q90, `method=6`, determinista: el test
de recompilación lo compara byte a byte). El hueco bajo la tarjeta queda blanco liso, que no pesa.
`render_png` ya separaba un `<image id="background">` de archivo y lo pegaba con Pillow (caché por
tamaño), el mismo camino que `ntam-maestria`.

| | antes (incrustado) | después |
|---|---|---|
| `template.svg` | ~6,9 MB | 5,4 KB |
| carpeta de la plantilla | ~6,9 MB | ~144 KB (`background.webp` 136 KB) |
| capas del fondo por render 300 dpi | ~10,5 s en resvg | decodificar 0,12 s la primera vez por tamaño; luego pegar |
| `POST /render` PNG 300 dpi (portátil, cargado) | — | ~2,1 s (1,5 s resvg de textos y parche, 0,3 s PNG) — como los v4 (~1,8 s en la misma máquina) |
| `POST /render` PDF 300 dpi / PNG 96 dpi | — | ~3,9 s / ~0,65 s |

Error de re-codificación frente al PNG sin pérdida: media 0,40/255, máx. 18.

### Datos desde la emisión (`POST /render` con `certificate_no`)

| Campo | Origen |
|---|---|
| `association_name` | **sólo** la asociación más cercana por encima del club del miembro (`honor_enrollments.club_id` → `organizations.path`, ancestro de tipo `association`). Sin inscripción, sin club o club fuera de una asociación: vacío, y la línea desaparece. No cae a la asociación de la emisora (eso sigue siendo `organization_name` de los v4) |
| `instructor_name`, `director_name`, `recipient_name`, `honor_name` (en el idioma), `issued_date`, `certificate_no` | como en los v4 |
| `church_name` | cadena de la plantilla en el idioma del certificado |

Con folio, **todos** esos campos salen del registro o de ninguna parte: `POST /render` borra los que
mande el cliente antes de completar (`RECORD_FIELDS`), así que un registro sin asociación no imprime
la que escriba el cliente (antes, un campo vacío en el registro dejaba pasar el del cliente).

### Verificación (24 sep 2026)

Render del motor a 300 dpi con `datos-ejemplo.json` contra `vistas/<idioma>.svg` capturado en Chrome
headless a 3300 × 2550 (diferencia por píxel, escala de grises): es 0,71 · en 0,64 · pt 0,70 · fr 0,65
de media; píxeles > 32: 0,46–0,56 %; > 128: ≤ 0,006 %; todo en bordes de letra (antialiasing resvg
frente a Skia). Mismas posiciones, tamaños, fuentes y cortes que las vistas. Tests:
`backend/tests/test_certificate_dorada.py`.

Observación de diseño (se reproduce tal cual, no se corrige): el recorte del sello incluye un borde
blanco de la tarjeta del PDF original, visible como un pequeño escalón a la derecha de la tarjeta sobre
el sello; está igual en `muestra.png` y en las vistas.

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

**La firma queda en el certificado emitido** (`021_certificate_signatures.sql`, `app/services/certificate_signatures.py`).
Antes de 021 la firma se imprimía al generar y se perdía: volver a descargar desde el portafolio, `/verify/<folio>`
o `POST /render` con `certificate_no` salía sin firma. Ahora `certificates.signature_director_url` y
`certificates.signature_instructor_url` (text NULL, fuera del hash: `canonical()` sigue congelado) guardan la URL de
una **copia inmutable** en el bucket de medios, `certificates/signatures/<id del certificado>/<uuid>.png` (PNG ya
normalizado, subido por `storage.upload_bytes`; carpeta interna: `POST /media/upload` no puede escribir ahí ni en
`signatures/` — ahora resuelve la carpeta antes de construir la clave).

- **Sólo con cuenta** («para guardar el archivo pidamos que se registren»). Rutas que emiten:
  - portafolio `POST /portfolio/enrollments/{id}/certificate` (`CertificateIssue.signature_director|signature_instructor`);
  - investidura en bloque `POST /clubs/{club}/classes/{program}/invest` (`InvestIn`, mismos campos; la firma se
    valida y se lee **una vez** y cada certificado recibe su copia);
  - curso automático (`auto_certificate`): nadie hace clic, así que no se pide nada: si el instructor del curso
    tiene firma guardada (`users.signature_url`) se copia a su línea. Mejor esfuerzo: si el bucket falla, el
    certificado sale sin firma (se registra en el log), nunca se bloquea la emisión;
  - asistente `POST /certificates/prototype-batch` **con sesión** (el Bearer por el proxy): mismos campos; una sola
    copia por firma para todo el lote (en la carpeta del primer certificado; como nunca se borran, compartirla es
    lo mismo que copiarla), y el evento `issued` lleva `signed_by` (quién puso la firma; el lote sigue sin
    `issued_by_id`, así que sigue siendo «no oficial» en `/verify`). **Sin sesión los campos se ignoran**: el
    certificado anónimo no guarda firma y al volver a descargarlo sale sin ella salvo que quien lo pida la mande.
- **Qué se acepta** en cada campo: una data URL (dibujada o subida en ese momento; mismas reglas de
  `signatures.py`: PNG/JPEG/WebP, ≤ 400 KB, recodificada) o **la propia firma guardada de quien emite** (su
  `users.signature_url`; es la única que un cliente llega a ver). Cualquier otra URL —otro host, la firma guardada
  de otra persona, otro objeto del bucket— es 422 y no se emite nada. Así nadie puede poner la firma guardada de
  otro; lo dibujado tiene autor identificable (`issued_by_id`, y la fila de auditoría lleva `signed: [...]`).
- **Sin bucket** (almacenamiento sin configurar o caído) una emisión que pide firma responde 503/502 y no se emite
  nada (la inscripción sigue READY): la firma que se pidió o queda guardada o no hay certificado. Sin firma, igual
  que antes.
- **`POST /certificates/render` con `certificate_no`** (cualquier plantilla con ranura):
  - folio con firmas guardadas, o folio «oficial» (con `issued_by_id`, `user_id` o `enrollment_id`): **manda el
    registro**. Se imprimen sus copias y nada de lo que mande el cliente las reemplaza ni rellena una línea que el
    registro dejó sin firma (lo enviado ni se lee);
  - folio anónimo sin firmas guardadas (el asistente sin cuenta) o folio inexistente: la firma del cliente, como
    antes de 021;
  - certificado **anulado**: conserva sus firmas (documento histórico) pero no se imprime ninguna;
  - una copia que no se puede leer del bucket: 502 (nunca un certificado firmado que sale sin firma en silencio).
  Así la descarga del portafolio (`downloadPortfolioCertificate`) y la de `/verify/<folio>` salen firmadas sin
  que el cliente mande nada. `CertificateOut.signed` dice qué líneas están firmadas.
- **Nunca se borran**: anular el certificado no las toca; borrar (u olvidar) la firma guardada de la cuenta borra
  sólo `signatures/<uuid>.png`, nunca `certificates/signatures/…`; la baja de una cuenta (INACTIVE) tampoco. La copia
  es del certificado.
- **Límite del lote**: con sesión el lote llega por el proxy de la web (una sola dirección para todos), así que
  `30/hour` cuenta por cuenta cuando trae un access token válido (`rate_limit.account_or_ip`), por dirección si no.
- **Pendiente**: el lote del asistente no registra la plantilla del servidor (crea «Prototipo WxHin»), así que
  `/verify/<folio>` no ofrece descarga para esos folios (`template_slug = null`); `POST /render` con el folio sí
  imprime su firma guardada. Guardar la plantilla en el lote es un cambio aparte.

## Frases editables (24 sep 2026)

Pedido del responsable: «nos falta poder cambiar la frase de "Se otorga el presente certificado a:" y "por haber
cumplido satisfactoriamente los requisitos de la especialidad:", eso vamos a dejarlo igual solo para los usuarios
registrados». **Sin cuenta las frases son las de la plantilla**; con cuenta se pueden reescribir y quedan guardadas
con el certificado emitido, así `/verify/<folio>` y cualquier descarga posterior imprimen lo mismo.

**Qué frases.** Cada plantilla las declara en su `meta.json`:

| Plantilla | `editable_strings` |
|---|---|
| `especialidad-color`, `-dorada`, `-editorial-rojo`, `-modular-azul`, `-reticula-verde`, `-academico` | `["awarded", "completion"]` (sus `data-string`) |
| `investidura-clase` (SVG a mano, ids `t_*`) | `[{"key": "t_awarded_to", "role": "awarded"}, {"key": "t_for_completing", "role": "completion"}]` |
| `especialidad-basica`, `-basica-media`, `ntam-maestria` (retiradas) | ninguna |

Una entrada es la clave de `strings.<locale>.json` (si se llama como su papel) o `{key, role}`; `role` es
`awarded` | `completion` y sólo sirve para que la interfaz ponga la etiqueta en su idioma. Declarar una clave que no
está en el SVG, o un papel desconocido, es un `TemplateError` al leer la plantilla. El compilador
(`tools/compile_element_template.py`) emite `["awarded", "completion"]` cuando el paquete usa esas claves, así que
recompilar no las pierde. `investidura-clase` ganó `data-max-width="330"` + `data-min-size` + `data-max-lines="1"`
en esos dos `<text>` (sin `data-fit`: sin frase cambiada se dibuja exactamente como antes).

**`GET /certificates/templates`** añade `editable_strings: [{key, role, max_length, defaults: {es, en, pt, fr}}]`.
`max_length` sale de la caja: `data-max-width × data-max-lines` al `data-min-size`, entre el avance medio del texto
español de la propia plantilla en su fuente (×0,9 si envuelve, por lo que se pierde al cortar por palabras), nunca
menos que el texto de la plantilla y nunca más de **160**. Hoy: 138–160 en las v4, 120/126 en la investidura. Es
una estimación para el contador; si un texto concreto cabe lo decide `fit_lines`.

**`POST /certificates/render`** acepta `strings: {clave: texto}` (≤ 8 claves):
- sólo claves de `editable_strings` de esa plantilla; otra → 422 `{"code": "string_not_editable", "key"}`;
- una sola línea: cualquier carácter de control (salto de línea, tabulador…) o separador de línea/párrafo → 422
  `string_invalid`; los espacios repetidos se colapsan; vacío = la frase de la plantilla;
- más de `max_length` → 422 `string_too_long`;
- el motor pone el texto en lugar de la traducción y aplica el ajuste de siempre (encoger → envolver hasta
  `data-max-lines` → 422 «El texto del campo 'awarded' no cabe…», con la clave como campo).
- **Con `certificate_no` manda el registro**, como con los datos y las firmas: si el folio existe se imprimen sus
  `text_overrides` (o las de la plantilla si no tiene) y lo que mande el cliente se ignora; si no existe, las del
  cliente. `/render` es abierto (sin sesión), así que un anónimo puede *previsualizar* frases; lo que se guarda y lo
  que se vuelve a descargar por folio sólo lleva frases cuando las emitió alguien con cuenta.

**Persistencia** (`023_certificate_text_overrides.sql`): `certificates.text_overrides jsonb NULL`, `{clave: texto}`
sólo con lo que cambió (una frase igual a la de la plantilla en el idioma de emisión no se guarda). **Fuera del
hash** (`canonical()` congelado). La aceptan:
- `POST /certificates/prototype-batch` **sólo con sesión**: sin sesión, `strings` con algún texto → 422
  `strings_require_account` (vacías se ignoran). La respuesta del lote no cambia de forma;
- `POST /portfolio/enrollments/{id}/certificate` (`CertificateIssue.strings`);
- `POST /clubs/{club}/classes/{program}/invest` (`InvestIn.strings`, las mismas para todo el bloque; claves de la
  plantilla de investidura).
En las tres se valida todo (claves, longitud y **que quepa**, `check_overrides_fit` → 422 `string_does_not_fit`)
antes de escribir nada. `GET /certificates/verify/{no}` y `CertificateOut` (portafolio) devuelven `text_overrides`.
**Auditoría**: el evento `issued` lleva `text_overrides: ["awarded", …]` (las claves, nunca el texto) y la fila
`CERTIFICATE_ISSUE` del portafolio también.

**Frontend** (`conquistadores-app`): asistente, paso 3, bloque plegable «Frases del certificado» con un campo por
frase (valor = el de la plantilla en el idioma elegido, contador, «Restablecer») sólo con sesión; sin ella, la línea
«Crea tu cuenta para personalizar las frases». La hoja de emisión del portafolio lleva el mismo bloque. `/verify` y
las descargas no mandan nada: el servidor rellena desde el registro.

## Pendiente (fuera del backend)

- El asistente (`conquistadores-app/lib/certificate-templates.ts`) no tiene etiqueta para los slugs
  nuevos: puede usar el `title` del listado. Hoy muestra «Especialidad editorial rojo» derivado del slug.
- Las traducciones del paquete son de prueba (lo dice el propio LEEME): validar la terminología local
  (p. ej. «EXPLORATEURS», «Honor completion») antes de emitir en masa; al cambiar `traducciones.json`
  se recompila.
- (Histórico, el v4 `01-editorial-rojo` ya no está instalado) un nombre de especialidad a dos líneas
  dejaba la segunda a ~30 unidades de la fecha: era la geometría de aquel diseño.

## Especialidad · Marco multicolor (`replica-especialidad-color`)

Hermana de la dorada: mismo contrato, mismas fuentes y mismo compilador (`tools/compile_element_template.py
~/Documents/DEEL/certificados-diseno/replica-especialidad-color --slug especialidad-color --install`).
Marco de color plano y emblema de esquina aplanados en `background.webp` (~70 KB). Decisión del
propietario (2026-09-24): es la **primera del selector y la plantilla por defecto**
(`DEFAULT_CERTIFICATE_TEMPLATE`); la muestra de la portada de conquistadores.app sale de ella.
Test: `tests/test_certificate_color.py`.

## Miniaturas (24 sep 2026)

Problema (propietario: «en /certificates/new no cargan los diseños»): el paso 2 del asistente pedía
cada miniatura con `POST /certificates/render` (datos de muestra, 3 in a 96 dpi, de dos en dos) y la
vista previa grande era otro POST. Cada render tarda 0,5–8 s en producción (0,15 CPU; «dorada» y
«color», con fondo raster, son las lentas en frío) y `POST /render` tiene `30/minute` por cliente:
tras ir y volver un par de veces el límite se agotaba (429) y las miniaturas quedaban en gris hasta
recargar. Además cada visitante repetía los mismos renders.

**Endpoint**: `GET /api/v1/certificates/templates/{slug}/thumbnail.png?locale=es&w=576`
(`app/routers/template_thumbnails.py`, router propio registrado en `main.py`).

- `w` ∈ {288, 576, 1152} px (ancho exacto del PNG; otro valor → 422). `locale` debe ser uno de los
  idiomas de la plantilla; slug desconocido o idioma que la plantilla no tiene → 404.
- Datos de muestra fijos del servidor: «María López», especialidad «Campamento I» en el idioma de la
  plantilla (es «Campamento I», en «Camping Skills I», pt «Acampamento I», fr «Camping I»), «Club
  Orión», 21-09-2026 (fecha larga del idioma, o día/mes/año si la plantilla los separa), directora
  «Ana Ruiz», instructor «Luis Gómez». Sin QR ni firmas. Parche: la insignia de Campamento I del paquete
  `replica-especialidad/recursos/`, copiada en `templates/assets/samples/honor-patch.webp` (por eso el
  nombre de muestra es Campamento I y no el «Nudos» que usaba el asistente).
- Caché igual que las hojas PDF (`app/services/sheet_cache.py`): clave
  `thumbs/{slug}/{locale}-{w}-{etag12}.png` en el bucket público. El ETag es un hash de
  `template.svg`, `strings.*.json`, `meta.json`, `background.*`, el parche de muestra, los datos de
  muestra y el motor (`app/certificates/render.py`): cualquier cambio da otra clave, y tras subir la
  nueva se borran las versiones viejas de la misma variante. Con R2: 302 a la URL pública
  (`Cache-Control: public, max-age=86400`); sin R2 (local), el PNG directo. `If-None-Match` → 304.
- Nada se precalienta al arrancar (0,15 CPU): la primera petición renderiza, con un lock por clave
  (dos primeras peticiones simultáneas renderizan una vez) y como mucho 2 renders de miniatura a la
  vez; el PNG queda en una LRU en memoria (24 MB) que sirve mientras se sube a R2 o si R2 falla.
- Sin rate limit propio: es un GET cacheable con un conjunto acotado de variantes
  (plantillas × idiomas × 3 anchos). Test: `tests/test_template_thumbnails.py`.

**Asistente** (`conquistadores-app`): el selector usa `<img src=…thumbnail.png>` directo
(`loading="lazy"`, `srcSet` 288w/576w), sin cola ni caché propia; la vista previa grande del paso 2
usa la de 1152 px hasta que la persona escribe algún dato, y sólo entonces hace el `POST /render`
real (debounce de 600 ms, la petición anterior se cancela).

## Especialidad · Bloques azules (`replica-especialidad-azul`) — sustituye al «Modular azul» v4

Instalada el 2026-09-24 **sobre el mismo slug** `especialidad-modular-azul` (los certificados ya emitidos con el v4 se
re-renderizan con el diseño nuevo). Mismo contrato que dorada/color: página 1600×1237, Poppins/Lato/Advent Sans,
fondo aplanado `background.webp` (~70 KB), `association_name` desde la jerarquía, fecha numérica, frases editables.
Regenerar: `python tools/compile_element_template.py ~/Documents/DEEL/certificados-diseno/replica-especialidad-azul --slug especialidad-modular-azul --install`.
Tests: `tests/test_certificate_azul.py`; los tests genéricos del v4 (`test_certificates_v4.py`) usan `reticula-verde` y
`academico`, los dos v4 que siguen instalados. Desde el soporte de `text_transform` (abajo) la asociación y «OTORGA EL
PRESENTE CERTIFICADO A:» salen en mayúsculas como en su `muestra.png` (antes se imprimían tal cual llegaban).

## Especialidad · Editorial rosa y rojo (`replica-especialidad-editorial`) — sustituye al «Editorial rojo» v4

Instalada el 2026-09-24 **sobre el mismo slug** `especialidad-editorial-rojo` (los certificados ya emitidos con el v4 se
re-renderizan con el diseño nuevo). Página 1600×1237, Poppins 300/700, Lato, Advent Sans; papel, franjas rosa/roja y logo
aplanados en `background.webp`; `association_name` desde la jerarquía y en mayúsculas; fecha numérica; frases editables
(`awarded` en mayúsculas también si se reescribe). Es el primer diseño que usa `wrap_then_shrink_or_error` con
`wrap_strategy: "balanced"` y `flow_rules` (sección siguiente): el nombre largo va a dos líneas equilibradas de 105 y
empuja hacia abajo la línea divisoria, «por haber cumplido…» y el nombre de la especialidad; QR, fecha y firmas no se mueven.
Regenerar: `python tools/compile_element_template.py ~/Documents/DEEL/certificados-diseno/replica-especialidad-editorial --slug especialidad-editorial-rojo --install`.
Tests: `tests/test_certificate_editorial.py`.

Verificación (24 sep 2026): es/en/pt/fr a 110 dpi, nombre largo («María Fernanda López Hernández») y corto, frente a
`vistas/*.svg` rasterizadas con Brave sin interfaz al mismo tamaño: superpuestas, posiciones, fuentes, corte de líneas y
desplazamiento coinciden (diferencias sólo de antialiasing, < 1 px).

## flow_rules y wrap_then_shrink (contrato 1.0)

**Ajuste de texto.** `renderizador.js` (`fit`) usa el mismo bucle para `shrink_then_wrap_or_error` y
`wrap_then_shrink_or_error`: de `font_size` a `min_font_size` en pasos de 1, a cada tamaño una línea, si no la envoltura
en `max_lines`, y sólo si tampoco cabe el tamaño siguiente; si nada cabe, error. Es lo que ya hacía `fit_lines`, así que
el modo `overflow` no necesita atributo (el compilador sólo rechaza valores desconocidos). Lo que cambia es la
**estrategia de corte**:

- `wrap_strategy: "balanced"` (sólo con `max_lines: 2`; otro valor → `CompileError`) → `data-wrap="balanced"`. En vez de
  llenar la primera línea (voraz: «José Luis Martínez / Gómez»), se prueba cada corte entre palabras y se queda el de
  menor diferencia de ancho entre las dos líneas con ambas dentro de `max_width` («José Luis / Martínez Gómez»; el
  primero en caso de empate, como el JS). Si ningún corte cabe se baja un punto. `render.balanced_split` + `fit_lines(balanced=True)`.
- `text_transform: "uppercase"` → `data-text-transform="uppercase"`. El motor pasa a mayúsculas lo que imprime —traducción,
  dato (la asociación) o frase reescrita— antes de medirlo (`_transform_text` en `_apply_fit_wrap`); el compilador pone
  el texto de muestra del SVG en mayúsculas. `strings.<locale>.json` conserva el texto original (es lo que ve el formulario
  de frases editables).

**Flujo.** `flow_rules: [{"trigger": "recipient_name", "shift_elements": [...], "offset": "extra_line_height"}]`: los
elementos listados bajan `(líneas − 1) × tamaño × line_height` del disparador ya ajustado (dos líneas a 105 × 1.06 →
111,3 unidades del diseño; a 73 → 77,38). Depende del dato, así que lo resuelve el motor en cada render:

- Compilador (`flow_triggers`): valida disparador (un texto), elementos (existen, no el propio disparador, no en dos reglas)
  y `offset` (sólo `extra_line_height`), y marca cada elemento desplazable con `data-flow-trigger="<id del texto en
  template.svg>"` (p. ej. `recipient_name`; una traducción sería `t_<id>`). Si es un recorte SVG, también su `clipPath`;
  si es el nombre de un firmante, también su hueco de firma. Un elemento que se mueve **nunca se aplana** en
  `background.webp`: la tira de capas fijas del fondo se corta ahí (por eso `recipient_line` es ahora una `<line>` viva).
- Motor (`_apply_flow`, al final de `fill_svg`, cuando todos los textos tienen tamaño y líneas): cuenta los `<tspan>` del
  disparador y desplaza `y` / `y1` / `y2` (`<text>`, `<line>`, `<rect>`, `<image>`), como el JS; un elemento con
  `transform` o sin esas coordenadas (un `<path>`) recibe `translate(0 Δ)` delante. Un disparador opcional ausente → Δ = 0.
- Sin `flow_rules` ni los atributos nuevos no cambia nada: academico, color, dorada y reticula-verde recompilan byte a byte
  igual; modular-azul cambia sólo por `data-text-transform` (su diseño lo pide).

