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
| Tests | 92 (`backend/tests`), necesitan un Postgres local en `TEST_DATABASE_URL` |

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

Decisión y contrato en `docs/I18N_Y_PLANTILLAS.md`: next-intl + ICU en la UI, tablas de traducción para el catálogo,
plantillas de certificado en **SVG** renderizadas en el servidor con resvg (ejemplo en `templates/certificates/especialidad-basica/`).
Árbol de organizaciones cargado con el directorio mundial del Yearbook (992 entidades); emisor = `ISSUER_ORGANIZATION_CODE=NTAM`
(Asociación Norte de Tamaulipas), verificado en producción.

- [ ] Motor de plantillas + `POST /certificates/render` (resvg, fuentes Noto incrustadas, `data-fit`).
- [ ] `users.locale`, `honor_translations`, `Accept-Language`; catálogo en inglés y portugués.
- [ ] next-intl en el frontend (`/es`, `/en`), selector de idioma, Noto Sans, RTL.

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
