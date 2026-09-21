# Idiomas en toda la plataforma y plantillas de certificado en SVG

Decisión de arquitectura (21 sep 2026). Objetivo: UI y certificados en cualquier idioma del
mundo, con plantillas diseñadas por personas, no dibujadas en código.

## 1. Cómo se hace bien: un solo modelo de idiomas para todo

| Capa | Mecanismo | Formato |
|---|---|---|
| UI (Next.js) | **next-intl** (App Router, Server Components, rutas `/{locale}/…`, RTL) | Mensajes ICU en `messages/{locale}.json` |
| Fechas, números, plurales | `Intl` del navegador y de Node (sin librerías extra) | ICU MessageFormat |
| Contenido de datos (especialidades, categorías, nombres de organizaciones) | Tablas de traducción en Postgres, idioma por fila | `honor_translations(honor_id, locale, name, description)` etc. |
| Certificados | Plantillas **SVG** con textos por clave; render en el servidor con **resvg** | `templates/certificates/<slug>/template.svg` + `strings.{locale}.json` |
| Gestión de traducciones | **Weblate** o **Tolgee** (open source, autoalojables); export a los JSON | JSON ICU |

Por qué next-intl y no otra: es la opción con soporte de primera clase para App Router y
Server Components, usa ICU (plurales, género, `select`) y resuelve la detección de idioma y el
RTL; Lingui y Paraglide pesan menos pero exigen tooling de compilación y no aportan nada que
necesitemos aquí. Comparativas: [Intlayer benchmark](https://intlayer.org/en-GB/doc/benchmark/nextjs),
[guía 2026](https://gundogmuseray.medium.com/the-definitive-guide-to-i18n-libraries-for-next-js-react-in-2026-8102c7f68a77),
[next-intl vs i18next vs Lingui](https://trybuildpilot.com/910-next-intl-vs-i18next-vs-lingui-2026),
[docs Next.js](https://nextjs.org/docs/app/guides/internationalization).

Reglas que evitan el 90 % de los problemas:
- Ningún texto visible vive en el código: todo pasa por una clave. Español es el idioma fuente.
- `locale` viaja en la URL (`/es/…`, `/en/…`, `/pt-BR/…`) y se guarda en el usuario (`users.locale`).
- Tipografía **Noto Sans** (+ Noto Sans Arabic/Hebrew/CJK según idioma) para cubrir todos los alfabetos
  en UI y en certificados; `dir="rtl"` automático para árabe, hebreo, persa, urdu.
- El backend acepta `Accept-Language` y `?locale=` y devuelve el nombre traducido cuando existe,
  con el español como respaldo.

## 2. Certificados: plantillas SVG diseñadas por personas

Sí, **en SVG**. Es vectorial, editable en Figma / Illustrator / Inkscape, imprime nítido a cualquier
tamaño y el servidor puede rellenarlo y convertirlo a PNG/PDF con **resvg** (Rust, sin navegador,
fuentes propias, texto multilingüe): [resvg](https://github.com/linebender/resvg),
[resvg-py](https://github.com/sparkfish/resvg-py), [resvg-js](https://github.com/thx/resvg-js).

### Contrato de plantilla (lo que me entregas)

1. Un archivo `template.svg` **a tamaño físico real** con `viewBox` en puntos (1 in = 72):
   ¼ carta `0 0 396 306`, ½ carta `0 0 612 396`, carta horizontal `0 0 792 612`.
2. Los elementos dinámicos son `<text>` (no convertidos a curvas) con un `id` fijo. Los demás elementos
   pueden ir como trazados. Ids reconocidos:

   | id | Contenido |
   |---|---|
   | `recipient_name` | nombre del participante |
   | `honor_name` | nombre de la especialidad (en el idioma del certificado) |
   | `club_name`, `place`, `issued_date` | datos de emisión (`issued_date` ya formateada al idioma) |
   | `director_name`, `instructor_name` | firmas |
   | `issuer_name` | organización emisora (p. ej. Asociación Norte de Tamaulipas) |
   | `certificate_no` | folio |
   | `t_*` | cualquier texto fijo traducible (`t_title`, `t_awarded_to`, `t_for_completing`…) |

3. Imágenes dinámicas como `<image>` con `id`: `emblem` (emblema oficial), `honor_patch` (parche real),
   `qr` (código de verificación), `issuer_logo`. El servidor sustituye el `href`.
4. Textos fijos: el `<text id="t_title">` lleva el español; el archivo `strings.<locale>.json` da la
   traducción de cada `t_*` por idioma. Si no hay traducción se usa el español.
5. Tipografías: solo **Noto Sans / Noto Serif** (o una fuente con licencia libre que entregues en
   `.ttf` junto a la plantilla). Cada `<text>` declara `font-family`; el servidor incrusta la fuente.
   Nada de fuentes del sistema.
6. Cada texto dinámico declara cómo se adapta si no cabe, con `data-fit="shrink"` (reduce el tamaño
   hasta `data-min-size`) o `data-fit="wrap"` (parte en dos líneas). Los nombres largos son la norma.
7. `text-anchor="middle"` para centrados; para idiomas RTL el servidor ajusta `direction` y el ancla.
8. Sin scripts, sin CSS externo, sin enlaces a archivos remotos. Colores en hex.

Ejemplo de referencia: `templates/certificates/especialidad-basica/` (plantilla ¼ carta con la
composición actual y `strings.es.json`, `strings.en.json`).

### Cómo se renderiza

`POST /api/v1/certificates/render` con `{template, locale, data}` → el servidor carga la plantilla,
sustituye textos e imágenes, aplica ajuste de tamaño, incrusta fuentes y devuelve PNG (300 DPI)
o PDF; la imposición para imprimir reutiliza `/printing/pdf`. El navegador deja de dibujar en canvas:
el certificado será idéntico en el móvil, en el PDF y en la verificación pública.

## 3. Orden de implementación

1. Backend: `locale` en usuarios; tablas `honor_translations` y `honor_category_translations`;
   `Accept-Language`; catálogo con nombres en inglés (fuente: Adventist Youth honors) y portugués.
2. Backend: motor de plantillas SVG + resvg (`app/certificates/render.py`), endpoint `render`,
   plantilla de ejemplo con tests de sustitución, ajuste y RTL.
3. Frontend: next-intl con `/es` y `/en` (rutas, mensajes ICU, selector de idioma en Configuración),
   Noto Sans, `dir` automático; el asistente pide el idioma del certificado.
4. Traducciones: Weblate autoalojado o Tolgee; los JSON se exportan al repo.
5. Más idiomas: portugués, francés, inglés primero (mayor membresía); después los demás por demanda.

## De un diseño a una plantilla (21 sep 2026)

El responsable diseña en su herramienta (Canva exporta el texto en curvas, sin ids) y deja por cada diseño
`fondo.*` (arte sin los textos variables) y `muestra.*` (mismo arte con textos de ejemplo) en su carpeta local
`~/Documents/DEEL/certificados-diseno/<diseño>/` (guía para el diseñador: `LEEME.md` de esa carpeta).
`tools/design_to_template.py <carpeta> --slug <plantilla> [--install]` rasteriza el fondo a 300 dpi, detecta cada
texto por diferencia entre muestra y fondo (posición, tamaño, color, alineación), escribe `campos.json` para
nombrar los campos y genera `vista-previa.png` + `comparacion.png` con el motor real.

Rendimiento: un fondo raster (`<image id="background">`) **no** pasa por resvg; se pega con Pillow
(`split_raster_background`). En la instancia de 0.15 CPU resvg tardaba 31 s por certificado a 300 dpi.
