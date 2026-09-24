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
