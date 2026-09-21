# Estado del bloque F — Guías Mayores y Aventureros sobre el mismo motor

Spec: `docs/superpowers/specs/2026-09-22-guias-mayores-aventureros-design.md`.
Rama: `block-f-programs`. **Nada desplegado todavía.** Este archivo es el único estado de F;
`docs/ESTADO.md` y `docs/ESTADO_BLOQUE_B.md` no se tocan.

## Qué está hecho

| Incremento | Estado | Migración | Entrega |
|---|---|---|---|
| F1 — Programas y tarjeta digital | **implementado, sin desplegar** | `012_programs.sql` | Catálogo de programas, inscripción de programa sobre el motor del bloque A, requisitos `FREE` por secciones, certificado de investidura del director, importador, permisos y auditoría |
| F2 — Requisitos enlazados y horas | **implementado, sin desplegar** | `013_activity_logs.sql` | `HONOR` (concreto y abierto), `PROGRAM`, `HOURS`; registro y aprobación de actividades; enlaces idempotentes |
| F3–F9 | no empezados | — | Ver «Lo que falta» |

## Orden de despliegue (obligatorio)

1. `psql … -f backend/migrations/012_programs.sql` en Neon, **antes** del backend.
2. `psql … -f backend/migrations/013_activity_logs.sql`.
3. Desplegar el backend (Render).
4. Frontend (`conquistadores.app`), cuando exista la pantalla de clases.

Se despliega **a oscuras**: un programa no existe para nadie hasta que se publica
(`POST /api/v1/programs/{id}/publish`, sólo `MASTER_GC`). Publicar *Amigo* es el interruptor.

Vuelta atrás de `012`: mientras no haya ninguna fila con `honor_enrollments.program_id IS NOT
NULL` se revierte borrando las tablas nuevas, el CHECK y reponiendo el `NOT NULL` de
`honor_id`. Después ya no. `013` se revierte borrando `activity_logs` mientras no haya
requisitos `HOURS` completados.

## Lo que el responsable tiene que hacer antes de publicar «Amigo»

1. **Cotejar el currículo con el manual vigente** (decisión D2). Lo importado entra como
   `DRAFT`; nadie lo ve hasta que una persona lo compara y se marca `authority` (`IAD` para
   NTAM). El importador nunca publica.
2. **Conseguir el contenido**: la wiki oficial (CC BY-SA 3.0, con `source`, `source_url` y
   `license` en cada fila, que el frontend debe mostrar) o el manual de la División, que **no**
   es CC BY-SA y necesita autorización de uso por escrito antes de cargarlo.
3. **Diseñar la plantilla de investidura**. `templates/certificates/investidura-clase/` existe
   con un diseño **provisional** (el armazón de `especialidad-basica` con los textos de
   investidura) para que el flujo funcione de punta a punta. Sustituirlo con
   `tools/design_to_template.py` manteniendo los `id`. Sin plantilla de tipo `program` para el
   ministerio, la emisión responde 409.
4. **Decidir la insignia** de cada clase (`programs.image_url`, R2 público).

## Desviaciones y decisiones tomadas al implementar

Están escritas en la sección «Desviaciones» al final de la spec de F. En una línea:

- `programs.issuer_level` entra ya en `012` (la spec lo dejaba para `014`), porque la decisión
  D3 es de F1 y la columna **nace con comportamiento**: un director de club no inviste un
  programa `ASSOCIATION`. La rama que *concede* ese permiso a la Asociación sigue siendo F4.
- El detalle de la inscripción devuelve `sections[]` con las posiciones de cada sección y sus
  contadores, y `requirements[]` sigue siendo la lista plana del bloque A. Así la tarjeta se
  agrupa sin cambiar ni un campo del contrato de especialidades.
- `EnrollmentSummary.honor` pasa a ser opcional y aparecen `type` y `program`. En una
  inscripción de especialidad `honor` sigue viniendo siempre: el frontend actual no se entera.
- Las horas aprobadas cuentan desde `started_at` de la inscripción, como dice la spec, y
  `quantity {approved, target}` viaja en el requisito.

## Lo que falta (F3–F9)

- **F3** (sin migración): inscripción del club en bloque, matriz miembros × requisitos y firma
  en bloque. Las costuras están puestas: `can_bulk_sign` / `can_enroll_member` se escriben
  junto a `can_approve_activity` en `app/rbac.py`, y la firma en bloque es la única excepción
  a la máquina de estados del bloque A.
- **F4**: `guiasmayores.app`, monorepo con `cq-kit`, `X-Application`, emisión y revisión por la
  Asociación (`can_issue` ya tiene el punto exacto donde entra esa rama), prerrequisitos
  (`requires_verified`, `requires_child_protection`, `min_age`) en `014`.
- **F5** mentoría (`015`), **F6** bitácora cifrada (`016`), **F7** revisión trimestral (`017`),
  **F8** Aventureros y perfiles gestionados (`018`), **F9** requisito de curso / examen (`019`,
  `kind = 'COURSE'`, que **no** está declarado en el CHECK de `program_requirements` a
  propósito: una columna sin comportamiento se pudre).
- Frontend de F1–F3 completo (`/classes`, `/classes/[id]`, `/activity`, panel del director).
- `catalog_tools/crawl_wiki_programs.py` y `parse_wiki_program.py`: el inventario
  `migrations/data/wiki_program_pages.csv` y el rastreo (una petición cada 10 s, nunca
  `api.php`) siguen pendientes. El importador ya lee JSON de disco y no necesita red.
