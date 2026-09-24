# Pendientes al cierre del 2026-09-24

Estado de la plataforma al terminar la jornada y lo que queda por hacer, en orden de prioridad.
Producción: backend `api.adventist.club` (Render), frontend `conquistadores.app` / `admin.adventist.club` (Vercel), base Neon con migraciones **001–022 aplicadas**. Backend 1018 pruebas; frontend 24 tests + tsc/eslint/build; e2e 12/12 jornadas.

## 1. En producción desde hoy

| Bloque | Qué hace | Notas |
|---|---|---|
| Avisos | Correo + bandeja `/avisos` con campana; director (requisitos por revisar, 1/12 h por club), miembro (requisito aprobado, especialidad lista, certificado, horas aprobadas o rechazadas, XP), instructor de curso, cartas por vencer | Firma suelta de requisito de clase: solo bandeja; correo al quedar lista (decisión del propietario) |
| Primer uso guiado | `/bienvenida`, 4 pasos, solo cuentas nuevas de miembro; `/panel` redirige hasta terminar o saltar | Las 7 cuentas previas quedaron marcadas como vistas |
| Ministerio del club | Varios ministerios por club (`organization_ministries`, principal en `ministry_id`), obligatorio al crear y al aprobar solicitudes, chips y filtro en admin, «Clases» ofrece las de todos sus ministerios | «Jóvenes» (`youth`) existe como ministerio |
| Perfil del club | Dirección con Google Places (o texto + enlace de Maps sin clave), `place_id`, `maps_url`, logo del club (subida con recorte, 512 px / 300 KB) | Clave `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` pendiente del propietario: guía en `conquistadores-app/docs/GOOGLE_PLACES.md` |
| Certificados | Solo diseños por elementos: **Marco multicolor** (primero y por defecto), Especialidad dorada, Editorial rojo, Modular azul, Retícula verde, Académico marfil; investidura de clase | Clásicas y NTAM retiradas (`listed: false`), sus certificados emitidos siguen descargándose |
| Firmas | Dibujada o subida, impresa sobre la línea de firma; guardada solo con cuenta (bucket público, clave aleatoria); copia inmutable en cada certificado emitido; `/verify` con descarga | Sin cuenta: un certificado por vez (UI y API) |
| Asistente | es/en/pt, miniaturas estáticas cacheadas en R2 (0 renders al abrir), club con autocompletado + «Asociación o misión», lote con diseño e idioma guardados | Advent Sans: origen adventist.design (OFL 1.1), registrado en `fonts/OFL-adventsans.txt` |
| Sistema de diseño | `app/globals.css` es un índice; tokens, primitivas, componentes y `features/*.css`; kit de escritorio fase 0–1 (`cq-desk-shell`, riel, ⌘K, bento, KPI, tabla densa, panel lateral, filtros…) desde 1024 px, móvil intacto | Reglas: parches sin caja, una sola búsqueda, riel con emblema desnudo (`docs/DESIGN_SYSTEM.md` del frontend) |

## 2. Trabajo en curso (ramas sin fusionar, frontend)

Tres agentes de la **fase 2 de escritorio** quedaron trabajando al cierre. Sus ramas viven en el repo `conquistadores-app`; hay que fusionarlas en este orden, correr gates (`tsc`, `eslint`, `build`, `npm test`), comparar capturas 375/768 con `main` (deben ser idénticas) y publicar:

1. `feat/desktop-member` — `/portfolio` y `/panel` en bento (KPI, siguiente paso, mi clase, colección, avisos); incluye el arreglo de los 401 de `/api/session` sin sesión.
2. `feat/desktop-honor` — `/honors/[id]` a dos columnas, parche sin caja, barra de acciones fija, migas.
3. `feat/desktop-catalog` — `/categories` con riel de filtros, retícula densa sin cajas, hovercard, búsqueda solo desde ⌘K.

Estado al cierre: `feat/desktop-catalog` **terminada** (commit `043df76`: gates, 25 tests, e2e 10 en verde; capturas 375/768 idénticas a main; búsqueda vía paleta ⌘K «Filtrar el catálogo por …»; tandas de 96 con carga automática; pendiente comprobar en producción el nº de requisitos de la vista previa). `feat/desktop-honor` **terminada** (commit `19f0279`: gates, 20 tests, e2e 04/10; móvil idéntico; migas vía `DeskCrumbsProvider`; nota: el gris de las opciones no marcadas de `cq-segmented` queda en 4,18:1 en claro en toda la app — corregir en `primitives.css`). `feat/desktop-member` **terminada** (commit `f3473c4`: gates, 20 tests, e2e 01/04/08/10; móvil idéntico; `/api/session` responde 200 con usuario nulo y una sola llamada; tabla de usuarios del MASTER en `cq-datatable`). Las tres ramas quedan listas para fusionar en el orden indicado; al fusionar, revisar conflictos en `components/app-shell.tsx` (migas + sesión compartida), `app/styleguide/styleguide.tsx` (SECTIONS) y `lib/i18n/messages/*`.

Corrección publicada tras el cierre: `/verify/<folio>` muestra el certificado real renderizado desde el registro (diseño, firmas, QR) en vez de la maqueta CSS; los folios antiguos sin diseño guardado usan el diseño por defecto (frontend `8c3607b`).

Si alguno no terminó, su worktree está en el scratchpad de la sesión (`wt-desk-member|honor|catalog`); la rama conserva los commits.

## 3. Siguiente plan (fases 3–5 de escritorio)

- Fase 3: panel del director (`04`) y asistente de certificados a dos columnas (`05`).
- Fase 4: admin de clubes con `cq-datatable`, panel lateral y acciones en masa (`06`).
- Fase 5: regresión, contraste AA, teclado.
- Backend que falta para completar las maquetas: agenda/eventos del club, serie semanal de actividad (mapas de calor), acciones en masa, plantilla del certificado en JSON.
- Deuda del kit: rellenos y márgenes a tokens, ~160 tamaños sueltos en TSX, retirar alias `.cq-roll-foot`, `.cq-pp-avatar`, `--elev-*`. Hacerlo **después** de fusionar la fase 2.
- Página `/auth/verify-email`: no conoce `next`; guardar `next` al registrarse y ofrecer «Continuar».
- `/teach/courses/{id}`: falta pestaña visible «por revisar» (destino del aviso al instructor).
- Certificados del lote con sesión siguen como «No oficial» en `/verify` (el lote no guarda emisor).
- Cambiar de club desde el selector del armazón (no existe el dato de múltiples clubes por persona).
- Prueba de carga del render cuando Render pase a `starter`.

## 4. Decisiones y acciones del propietario

Bloquean el lanzamiento público:
1. Render: `free` → `starter` y health check en `/api/v1/health`.
2. 2FA en las cuentas MASTER y luego `MASTER_MFA_ENFORCED=true`.
3. `SENTRY_DSN` en Render.
4. Publicar las clases desde `/admin/clases` (contenido NAD; confirmar o sustituir por DIA).
5. Clave de Google Places (ver `docs/GOOGLE_PLACES.md` del frontend).
6. Prueba real desde el teléfono: foto de evidencia a R2 y carta de iglesia.
7. Subir el logo y la dirección del club Jadhai desde «Editar».
8. Rellenar `/fuentes` (`[ORIGEN DE LAS IMÁGENES]`, `[ESTADO DEL PERMISO DE GUÍAS MAYORES]`) y `lib/legal.ts`; decidir si se muestran los créditos CC BY-SA (`SHOW_SOURCES`).
9. Validar las traducciones de prueba de los certificados (en/pt/fr).

Decisiones de seguridad abiertas (informe `docs/SEGURIDAD_REVISION_2026-09.md`): IP real para el límite de login detrás de Vercel (SEC-12), subir `next` (DEP-01), correo verificado para invitar personal (SEC-06), cerrar la herramienta abierta de certificados (SEC-03), revocación de sesiones (SEC-13).

Decisiones tomadas hoy (ya aplicadas): firma guardada en bucket público; «uno por uno» sin cuenta también en el API; correo de clase solo al quedar lista; el club de una solicitud recibe ministerio al aprobar; acento índigo y lateral abierta/riel según pantalla en escritorio; módulos sin dato no se montan.

## 5. Entorno local (para retomar)

- Postgres de pruebas: cluster en el scratchpad de la sesión (`pgdata-main`, puerto 55432, rol `test`, bases `etl_hontr` y `etl_e2e`, migraciones hasta 022 y semillas). Vive bajo `/private/tmp`: la limpieza nocturna de macOS puede borrarlo; receta de reconstrucción en la memoria del asistente (`local-verification-setup`).
- Suite backend: `TZ=UTC TEST_DATABASE_URL=postgresql://test@127.0.0.1:55432/etl_hontr python -m pytest -q`.
- Frontend: `npx tsc --noEmit && npx eslint . --ignore-pattern '.claude/**' && rm -rf .next && npm run build && npm test`.
- e2e: `scripts/e2e/run.mjs` (ver cabecera del archivo).
- Publicar: push a `main` de cada repo (Render y Vercel despliegan solos); aplicar antes en Neon cualquier migración nueva (siguiente número libre: **023**).

## 6. Especialidades de Aventureros (awards) — pendiente, documentado

**Fuente oficial** (entregada por el propietario el 2026-09-24): *Adventurer Award Book 2020*, GC Youth Ministries
(`~/Documents/DEEL/aventureros/Award Book 2020.pdf`, 410 páginas, © GC Youth Ministries Department, foto Shutterstock)
y la página https://www.gcyouthministries.org/ministries/adventurers/ (bloquea las descargas automatizadas: 403; hay que
abrirla a mano para bajar recursos, parches e idiomas).

**Qué contiene el libro** (índice extraído en `backend/data/adventurer_awards_index.csv`, 155 awards):

| Categoría (inglés) | Awards | Página |
|---|---|---|
| Community | 7 | 13 |
| Crafts (antes «Arts and Crafts») | 29 | 35 |
| Home (antes «Household Arts») | 31 | 103 |
| Nature | 35 | 181 |
| Recreation | 23 | 265 |
| Spiritual | 30 | 325 |

Cada award trae: título, **clase sugerida** (Little Lamb 24, Early Bird 27, Busy Bee 20, Sunbeam 23, Builder 29,
Helping Hand 32), «Requirements» (lista numerada) y «Supporting Answers» (notas para el instructor). Edad 4–9 años.
No trae imágenes de los parches.

**Plan de importación (cuando se retome):**
1. Ministerio `adventurers` ya existe (`GET /ministries`). Crear las 6 categorías con `ministry_id=adventurers`
   (Comunidad, Manualidades, Hogar, Naturaleza, Recreación, Espiritual) sin mezclarlas con las de Conquistadores.
2. Importador `backend/migrations/import_adventurer_awards.py` al estilo de `import_ay_classes.py`: lee el índice CSV,
   extrae de cada página «Requirements» → `honor_requirements` (inglés, `source='gc-award-book-2020'`) y «Supporting
   Answers» → recurso/nota de instructor; `honor_type='award'`, `status='DRAFT'` hasta revisión del propietario.
3. Traducción al español: buscar la edición DIA/IAD del Award Book (o traducir con revisión); igual para pt.
4. Parches: conseguir el set oficial (AdventSource / DIA) y subirlos a R2 como los 809 de Conquistadores
   (`tools/` de catálogo); sin parche se usa la inicial sobre el color del ministerio.
5. Clases de Aventureros (`programs`, kind CLASS, ministerio `adventurers`): Little Lamb, Early Bird, Busy Bee, Sunbeam,
   Builder, Helping Hand; recomendaciones de awards por clase a partir de la columna «clase» del índice.
6. Certificado de award: reutilizar los diseños por elementos con textos «Certificado de Award / Aventureros» (nuevas
   claves en `strings.*.json`) y el emblema de Aventureros (`public/brand/aventureros.svg`).
7. Licencia: el libro es © GC Youth Ministries; confirmar permiso de uso de los textos antes de publicarlos
   (igual que con guiasmayores.com). Mientras tanto, DRAFT y `SHOW_SOURCES` apagado.

### 6.1 Fuentes revisadas y parches (2026-09-24, noche)

| Fuente | Qué aporta | Estado |
|---|---|---|
| *Award Book 2020* (PDF) | Requisitos + guía del instructor de los 155 awards, en inglés. **Los parches van en vector** (dibujos en el PDF; solo «Artist» y «Building Blocks» son raster, 360×265 y 86×61 px). | **Extraídos los 155** a `~/Documents/DEEL/aventureros/parches-pdf/` (PNG 300 dpi con transparencia + SVG por award; índice `backend/data/adventurer_awards_patches.csv`). Herramienta: PyMuPDF (`pymupdf` en el venv de pruebas). |
| mundoja.org/especialidades/aventureros (DIA, «nuevo currículo») | Solo la categoría **Espirituales**: 34 awards con **nombre en español**, ficha con **requisitos en español** y guía del instructor (voluntarios de Mundo J.A). Imágenes PNG de **158×158 px** (misma familia de diseño que el libro). | Descargadas a `~/Documents/DEEL/aventureros/parches-mundoja/` (índice `backend/data/adventurer_awards_mundoja.csv`). El sitio bloquea `curl` sin cabeceras de navegador y las imágenes se cargan por JS. |
| clubministries.org/adventurers (NAD) | Lista de 137 awards por clase (21 Little Lamb stars, 26 Eager Beaver chips, 17 Busy Bee, 23 Sunbeam, 28 Builder, 24 Helping Hand, 10 multinivel, 2 especiales), Director's Guide en inglés/español, logos e imágenes de Aventureros; fichas en Wikibooks y en el USB de AdventSource. | Contexto y mapeo award → clase (NAD). |
| guiasmayores.com/especialidades-de-aventureros | Fichas por nivel en español (Estrellitas de Corderito, Fichas de Castorcito, Abejita Industriosa, Rayito de Sol, Constructor, Manitas Ayudadoras, Multinivel, Otras regiones), libro de la AG y póster. | Contexto; sin permiso de uso (como con Conquistadores). |

**Recomendación al retomar:** usar los **vectores del Award Book** como imagen de catálogo de los 155 (calidad ilimitada, set completo y coherente) y mundoja.org como fuente de **nombres y requisitos en español** de Espirituales (y para comparar diseños). Si el propietario prefiere las imágenes de mundoja, solo cubren 34 awards y a 158 px (se verían borrosas en escritorio y en certificados). Confirmar el permiso de uso de los textos del libro (© GC Youth Ministries) antes de publicar.

### 6.2 Librería de parches lista (2026-09-24, noche)

`tools/adventurer_patches.py` (+ `docs/AVENTUREROS_PARCHES.md`): **164 awards** (el libro trae 9 «multinivel» que el índice
omitía: Dogs, Universe, Horsemanship, Photo Fun, Snowshoeing, Bible Storytelling, Bread of Life, Good Samaritan, Purity).
Todos limpios en la hoja de contacto (`~/Documents/DEEL/aventureros/libreria/contact-sheet.png`). En el repo:
`backend/data/adventurer_awards_library.csv` (manifiesto con slug, nombres y clases es/en, hashes, notas),
`backend/data/adventurer_awards/webp/` (164 × 512 px, 2,8 MB) y `svg/` (162, 2,0 MB; «Artist» y «Building Blocks» son
raster en el libro: PNG nativos de 351×250 y 86×61 px, este último necesita sustituto). Correspondencia con los 34 de
mundoja en `adventurer_awards_mundoja_map.csv` (todos casan). **Subida a R2 pendiente**: `upload --r2` con las variables
`R2_*` de Render (clave estable `patches/adventurers/<slug>.webp`, caché inmutable) o `--api` con un token INSTRUCTOR+
(nombre elegido por el API). Licencia © GC Youth Ministries: confirmar antes de publicar.
