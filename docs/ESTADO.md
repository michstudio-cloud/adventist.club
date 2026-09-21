# Estado de la plataforma — 19 sep 2026

Documento de traspaso. Todo lo marcado ✅ fue **probado de verdad** (no solo escrito).

## Arquitectura vigente

```
conquistadores.app  (Next.js · Vercel · repo michstudio-cloud/conquistadores.app)
        │  NEXT_PUBLIC_CERTIFICATES_API_URL
        ▼
api.adventist.club  (FastAPI · Render «adventist-club-api» · este repo, carpeta backend/)
        │  SQLAlchemy async + asyncpg
        ▼
Neon PostgreSQL     (proyecto patient-bonus-15067785 · branch production · db neondb)

media.adventist.club → Cloudflare R2 (parches, logos, recursos)
```

Supabase y MongoDB quedan **retirados**. El backend central es multi-ministerio
(ministries / applications / organizations); nada nuevo debe quedar atado a Conquistadores.

## ✅ Funcionando en producción

| Área | Estado |
|---|---|
| `api.adventist.club` | HTTPS, DB conectada, CORS para `conquistadores.app`, `www` y los alias `conquisapp-*-mich-studio.vercel.app` |
| Certificados | `POST /certificates/prototype-batch` (201), `GET /certificates/verify/{no}`, SHA-256, QR; imposición real (`/printing/layout`, `/printing/pdf`: márgenes por lado, gaps, bleed, marcas de corte, orientación auto) |
| Auth | registro, login (+TOTP), refresh, verificación de email, reset de contraseña. `JWT_SECRET` configurado en Render |
| Usuarios / tutelas / organizaciones | alcance jerárquico con `ltree`; escrituras de organizaciones exigen rol admin |
| Especialidades | flujo DRAFT → ZONE_REVIEW → ASSOCIATION_REVIEW → PUBLISHED, versiones, auditoría |
| Catálogo | **809 especialidades, 13 categorías, 809 parches reales** en R2, PDF de requisitos y `source_url` por especialidad |
| Medios | `POST /media/upload` → R2 (probado con 809 subidas, 0 fallos) |
| Datos migrados | los 5 usuarios de Mongo están en Neon con su hash bcrypt original |
| Tests | 230 (`backend/tests`), necesitan un Postgres local en `TEST_DATABASE_URL` |

Decisiones de modelo: las «specialties» del sistema viejo **son** `honors`; los `org_nodes` viven en
`organizations` (tipo en minúsculas, `path` ltree); ids nuevos = `uuid5(ObjectId)`; `email_verifications`
no se migró (tokens efímeros).

## ✅ Vercel y dominios — resuelto (19 sep, noche)

La causa del «Resource provisioning failed» era el store Supabase «Conquistadores» suspendido y conectado al
proyecto `conquis.app` con `deployments.required = true`. Tras desconectarlo, los deploys de `main` salen `success`
y `conquistadores.app` / `www` ya sirven la app Next.js. Verificado en producción: `/`, `/categories` (809 tarjetas con
su parche desde R2, 13 categorías), `/certificates/new`, `/certificates/print`, `/clubs`, `/profile`, `/settings`,
`/verify/CC-2052D1C6B5` («Certificado válido») y CORS desde `https://www.conquistadores.app`.

- [x] La variable de Vercel `NEXT_PUBLIC_CERTIFICATES_API_URL` ya vale `https://api.adventist.club` (20 sep); es igual al
      default del código, se puede eliminar cuando se quiera.
- [ ] Archivar o borrar el proyecto viejo de Vercel `conquistadores.app` (SPA Vite del repo `church-path`) y, en Render,
      el servicio suspendido `adventist-api`.
- [ ] `/panel` redirige (307) a un login que aún depende de Supabase: ver pendientes del frontend.

## Pendiente — seguridad y limpieza (corto)

- [ ] **Rotar la contraseña del rol `neondb_owner`** en Neon (pasó por una conversación de trabajo) y
      actualizar `DATABASE_URL` en Render.
- [ ] Probar login de un usuario migrado con su contraseña real; después **eliminar el cluster de Atlas**.
      Respaldo completo en `~/adventist-mongo-backup-2026-09-19.json` (contiene emails y hashes: no subir a git).
- [ ] Borrar `~/.adventist-migration.env` cuando ya no haga falta (URIs de Mongo/Neon y la clave del importador).
- [ ] Borrar la rama de Neon `predeploy-backend-port` (`br-icy-poetry-b4040kdt`), usada solo para la prueba previa al deploy.
- [ ] `importer@adventist.club` quedó `INACTIVE`; se puede eliminar.
- [ ] En Neon quedan datos de prueba: certificados `CC-2052D1C6B5` y `CC-364E97AD06` (este último creado desde el sitio real el 20 sep), («PRUEBA Smoke Test»), club «Club de Prueba (smoke test)»,
      organización `PROTOTYPE`. Decidir si se conservan.
- [ ] Render: confirmar `ENVIRONMENT=production`, `FRONTEND_URL=https://conquistadores.app`, `RESEND_API_KEY`, `SENTRY_DSN`
      (sin ellos el servicio arranca igual, pero no envía correos ni reporta errores).
- [ ] Licencia de los parches: se tomaron de guiasmayores.com por decisión del responsable del proyecto; el sitio
      permite rastreo y no publica licencia. Conservar `source_url` y, si procede, pedir autorización formal.

## Idiomas y plantillas (21 sep)

Visión de producto del responsable: `docs/VISION_ECOSISTEMA.md` (+ `VISION_ANEXO_MVP.md`). Decisión y contrato en `docs/I18N_Y_PLANTILLAS.md`: next-intl + ICU en la UI, tablas de traducción para el catálogo,
plantillas de certificado en **SVG** renderizadas en el servidor con resvg (ejemplo en `templates/certificates/especialidad-basica/`).
Árbol de organizaciones cargado con el directorio mundial del Yearbook (992 entidades); emisor = `ISSUER_ORGANIZATION_CODE=NTAM`
(Asociación Norte de Tamaulipas), verificado en producción.

- [x] Motor de plantillas (21 sep): `POST /certificates/render` y `GET /certificates/templates` en producción; plantilla `ntam-maestria` con el diseño oficial de NTAM.
- [x] Fuentes Noto (OFL) en `fonts/` (21 sep): PNG/PDF en producción, con emblema oficial por defecto (`templates/assets/emblems/`), parche desde R2, QR, textos y firmas traducidos; verificado en español e inglés. Para árabe, hebreo y CJK faltan sus familias Noto.
- [x] Frontend: el asistente usa `render` (plantillas, tamaños, idioma, PDF; canvas sólo si la API cae) — 21 sep.
- [x] Alta de clubes con aprobación del coordinador de la Asociación (21 sep): migración 003 aplicada, 125 tests, búsqueda de asociaciones sin acentos.
- [x] `honor_translations` + `honor_category_translations` (migración 005, 21 sep): nombres oficiales desde la wiki de
      Conquistadores (en 506 · pt-BR 445 · fr 105 · uk 83 · de 12), `?locale=` en `/honors`, `/honors/categories` y
      `/honors/{id}`. Idioma siempre explícito (no `Accept-Language`). Detalle, atribución CC BY-SA y lista para enlazar
      a mano: `docs/ESPECIALIDADES_WIKI.md`. 131 tests.
- [x] Clubes por ubicación (migración 004): `GET /org-nodes/clubs/nearby`, `PUT /org-nodes/clubs/{id}/location`.
- [x] Requisitos oficiales por idioma (22 sep): migración 006, `catalog_tools/crawl_wiki_requirements.py` (1 página/10 s,
      HTML crudo en `~/adventist-wiki`, fuera del repo), `parse_wiki_requirements.py` e `import_wiki_requirements.py`.
      En Neon: **457 especialidades en español (4 060 requisitos) y 506 en inglés (4 571)**, todos con `source`,
      `source_url` y `license` CC BY-SA 3.0. 30 títulos sin página en español y 19 con traducción parcial (se omiten
      hasta que la wiki las complete; volver a ejecutar el rastreo + importador es idempotente). Nunca sustituye una
      lista escrita por un instructor. `is_theoretical` queda en su valor por defecto: decidirlo es tarea de revisión.
- [ ] `users.locale`.
- [ ] next-intl en el frontend (`/es`, `/en`), selector de idioma, Noto Sans, RTL.

## Bloque A — Portafolio (22 sep) — backend listo, sin desplegar

Spec: `docs/superpowers/specs/2026-09-22-portafolio-design.md`. Inscripción en una especialidad, progreso por requisito,
evidencias privadas, dictamen del club, «listo para certificar» automático y certificado ligado a la cuenta.

- Migración `007_portfolio.sql` (aditiva, idempotente): `honor_enrollments`, `requirement_progress`, `evidences` y columnas
  opcionales en `certificates` (`user_id`, `enrollment_id`, `issued_by_id`, `issued_role`; fuera del hash, los certificados
  ya emitidos siguen verificando).
- API `/api/v1/portfolio/*` (todo con sesión): `app/routers/portfolio.py` (fino) → `app/services/portfolio.py` (reglas y
  transiciones) → permisos `can_review` / `can_issue` / `can_view_portfolio` en `app/rbac.py`. La jurisdicción es el club
  **actual** del miembro; nadie dictamina ni certifica lo suyo. Auditoría: `ENROLL`, `ENROLLMENT_WITHDRAW`,
  `REQUIREMENT_SUBMIT`, `REQUIREMENT_REVIEW`, `EVIDENCE_ADD`, `EVIDENCE_REMOVE`, `CERTIFICATE_ISSUE`.
- La emisión vive en `app/services/certificates.py::issue_certificate`; la usan `prototype-batch` (mismo contrato) y el portafolio.
- Evidencias: bucket R2 **privado** (`app/services/private_storage.py`), subida directa con URL firmada PUT de 10 min que fija
  tipo y tamaño, lectura con URL firmada GET de 5 min. Variable nueva en Render: **`R2_PRIVATE_BUCKET_NAME`** (mismas
  credenciales R2; sin ella los endpoints de evidencia responden 503 y lo demás funciona). CORS del bucket: `PUT, GET, HEAD`
  desde `https://conquistadores.app`, `https://www.conquistadores.app` y `http://localhost:3100`, cabecera `content-type`.
- `migrations/purge_removed_evidence.py [--days 30] [--commit]`: borra del bucket lo `REMOVED` / `PENDING_UPLOAD` viejo y lo
  marca con `evidences.purged_at`. Simulacro por defecto.
- Tests: 159 (`tests/test_portfolio.py`, `tests/test_private_storage.py`). El Postgres local necesita antes
  `backend/tests/sql/base_certificates.sql` (las tablas de certificados son anteriores a las migraciones numeradas; **sólo
  para bases de prueba**) y después `007`. Con eso también corre por fin una prueba real de `prototype-batch`.
- **Orden de despliegue**: 1) `007_portfolio.sql` en Neon; 2) bucket privado + CORS + `R2_PRIVATE_BUCKET_NAME` en Render;
  3) backend; 4) frontend. Nada destructivo.
- Un `INSTRUCTOR` sólo dictamina si su cuenta está `VERIFIED` y activa; un director, si su club está aprobado
  (`rbac.club_staff_in_good_standing`). `VERIFIED` hoy sólo significa correo confirmado o visto bueno de un administrador:
  la protección real es que `POST /auth/register` ya no acepta `organization_id`.
- [ ] Ver el portafolio y abrir evidencias sigue la jerarquía de `can_view_user`: un instructor del club aún sin verificar
      no dictamina, pero sí ve a los miembros de su club. Decidir en el bloque E si la lectura también exige verificación.

## Bloque E — Membresía de club (22 sep) — E1–E4 en backend, sin desplegar

Spec: `docs/superpowers/specs/2026-09-22-membresia-de-club-design.md` (ver «Desviaciones» al final).
Pertenecer a un club deja de ser un campo suelto: `club_memberships` es el libro (quién entró, cómo, quién lo
aprobó, cuándo salió) y `users.organization_id` sigue siendo la verdad del RBAC, pero ahora los escribe **un
solo servicio**, `app/services/memberships.py`. Ningún router toca esas dos columnas por su cuenta,
`PATCH /users/{id}` incluido.

- **E1 — 2FA obligatorio para `MASTER_GC`** (`008_mfa_recovery.sql`). Política en `services/mfa.policy_error`,
  aplicada en `deps.get_current_user`; `get_authenticated_user` es la autenticación a secas. Tokens de
  `POST /auth/mfa/verify` llevan el *claim* `mfa` y `refresh` lo hereda. 10 códigos de recuperación
  (`XXXXX-XXXXX`, sólo hash SHA-256, mostrados una vez), `POST /auth/mfa/recovery-codes` para regenerarlos y
  `POST /users/{id}/mfa-reset` sólo por otro MASTER y nunca sobre sí mismo. Último recurso con un solo MASTER:
  `migrations/reset_mfa.py --email … --commit`.
- **E2 — Núcleo de membresía** (`008b_club_membership.sql`). Roles nuevos `CLUB_SECRETARY` y `COUNSELOR`,
  `club_memberships` con relleno no destructivo desde los `organization_id` de hoy, `GET/DELETE /memberships/me`,
  nómina, cambio de rol, baja con motivo y `PATCH /clubs/{id}/profile`. El traslado es cerrar una membresía y
  activar otra en la misma transacción. `portfolio.on_club_changed` mueve las inscripciones `IN_PROGRESS` /
  `READY` al club nuevo y congela `CERTIFIED`.
- **E3 — Invitaciones y consentimiento** (`008c_club_invitations.sql`). Enlace de un uso (activa a un adulto) y
  multiuso (sólo `STUDENT`, siempre con confirmación del director); los roles de personal son nominales.
  `/join` y `/consent` con un solo 404 para cualquier enlace inservible. Un menor no queda activo sin la
  autorización de un adulto **para ese club**. `POST /auth/register` acepta `invitation_token`.
- **E4 — Solicitudes desde `/clubs` y traslados** (sin migración). Solicitar, aprobar, rechazar, «aprobar todas»
  y cerrar la puerta (`accepts_requests`). Un menor que solicita no es visible para el club hasta que su tutor
  autoriza.

**Variable nueva en Render: `MASTER_MFA_ENFORCED`** (por defecto `false`). Con `false` no se exige nada y el
comportamiento es el de hoy; con `true`, un `MASTER_GC` sin MFA sólo puede darse de alta (403 en el resto) y un
token sin el *claim* recibe 401.

**Orden de despliegue** (cada incremento se despliega solo; migración en Neon **antes** del backend):

1. `008_mfa_recovery.sql` → backend. **Antes de encender `MASTER_MFA_ENFORCED`**: listar las cuentas MASTER sin
   segundo factor (`python backend/migrations/reset_mfa.py --list`) y darlas de alta. Sólo entonces poner la
   variable en `true`, para que nadie pueda inscribir un segundo factor con sólo la contraseña.
2. `008b_club_membership.sql` → backend. Después, **revisar a mano las membresías `BACKFILL`**: antes de cerrar
   `POST /auth/register` un `INSTRUCTOR` podía apuntarse solo a cualquier club; quien lo hiciera se da de baja
   desde la nómina.
3. `008c_club_invitations.sql` → backend.
4. E4 no lleva migración.

`007_portfolio.sql` (bloque A) aún no está en Neon: aplícalo antes o después, da igual. `on_club_changed`
comprueba una vez si la tabla existe y no hace nada si falta, así que E2 puede desplegarse sin A.

Nada es destructivo; cada paso se deshace desactivando código, no datos.

- [ ] E5 (unidades), E6 (zona e iglesia), E7 (carta de la iglesia), E8 (Secretaría completa) y E9 (correos de
      avance del portafolio) siguen pendientes. Las juntas que dejan E2–E4: `club_memberships.invitation_id`
      ya tiene su FK y `unit_id` lo añade `008d`; `can_view_roster` ya admite al `COUNSELOR` (hoy no ve a
      nadie porque no hay unidades); `may_handle_minors` es el único punto que falta dentro de `can_review`,
      `can_issue` y `can_view_portfolio` para que E7 los cierre sobre menores.

## Pendiente — backend

- [ ] Firmas criptográficas reales con `issuer_keys` / `certificate_signatures` (hoy solo hash SHA-256; una imagen de firma **no** es una firma).
- [ ] `prototype-batch` acepta `ministry`/`application` (20 sep) pero sigue sin exigir autenticación: decidir quién puede emitir.
- [x] Impresión (20 sep): `POST /printing/layout` + `/printing/pdf` con orientación auto, márgenes por lado, gaps X/Y, bleed, marcas de corte reales y retícula centrada (`app/printing.py`, 13 tests).
- [ ] Persistir `print_presets` / `print_jobs` (tablas existen, sin uso).
- [ ] Las «Doctrinales» (28) no tienen PDF de requisitos en la fuente.
- [ ] Tests de los endpoints de certificados (no corren en local porque el Postgres de pruebas no tiene esas tablas): añadir el DDL base a un fixture.
- [ ] Alembic (o similar) en lugar de SQL suelto cuando haya una segunda migración.
- [ ] Limpieza: la raíz de este repo aún contiene una app Vite vieja con Supabase (`App.tsx`, `components/`, `services/supabaseClient.ts`…) que no se despliega.

## Pendiente — frontend (repo conquistadores.app)

Ver `ESTADO.md` en ese repo. Auth y panel ya corren sobre este API (Supabase retirado el 20 sep). Pendiente allí: catálogo de
componentes, logos que faltan, UI de tutelas, clubes desde `org-nodes`.

## Operación

```bash
# tests
cd backend && TEST_DATABASE_URL=postgresql://USER@127.0.0.1:PORT/DB python -m pytest -q

# catálogo (idempotente)
DATABASE_URL=... python backend/migrations/import_honor_catalog.py backend/migrations/data/honors_pathfinders_es.json [--commit]

# herramientas del catálogo (rastreo respetuoso → normalizar a WebP 512px → subir por la API → escribir image_url)
backend/migrations/catalog_tools/{crawl,fetch_images,process,upload,set_image_urls}.py
```

Accesos usados: conectores de Vercel (team `mich-studio`; no pasar `slug`), Render (workspace `tea-d6csa724d50c73abt570`,
servicio `srv-dandrdp42hec73e5tcp0`) y Neon. Los CLI locales `vercel` y `render` están en **otras** cuentas.
Falta autorizar el conector de Cloudflare (DNS).

## Errores resueltos hoy (para no repetirlos)

| Síntoma | Causa raíz |
|---|---|
| Vercel «Resource provisioning failed» | store Supabase suspendido conectado al proyecto (resuelto al desconectarlo) |
| `next start` 500 en todas las rutas | carpetas dinámicas hermanas con nombres distintos (`[id]` vs `[courseId]`, `[hash]` vs `[certificateNo]`) |
| 500 al crear certificados | `honors.category_id` apuntaba a una tabla sin modelo SQLAlchemy (`NoReferencedTableError`) |
| `api.adventist.club` 404 | CNAME a un servicio viejo de Render (`adventist-api`) |
| Atlas `TLSV1_ALERT_INTERNAL_ERROR` | IP no incluida en Network Access |
| R2 `AccessDenied` en `PutObject` | el token de R2 tenía filtro de IP |
