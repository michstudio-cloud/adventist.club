# Bloque A — Portafolio: inscripción, progreso, evidencias, dictamen y certificado

Fecha: 22 sep 2026 · Estado: **aprobado por el responsable** (enfoque 1, sección de datos y mapa A→F).
Repos: `adventist.club` (FastAPI, este) y `conquistadores.app` (Next.js). Todo es aditivo.

## 0. Mapa del producto (cada bloque: spec → plan → implementación)

| # | Bloque | Entrega | Depende de |
|---|---|---|---|
| A | Portafolio (este) | Inscripción, progreso por requisito, evidencias privadas, dictamen, «listo para certificar», certificado ligado a la cuenta, vista del tutor | lo actual |
| B | Cursos del instructor virtual | Oferta de un instructor verificado sobre una especialidad oficial: lecciones y material, con el flujo de revisión existente | A |
| C | Exámenes | Intentos, temporizador, umbral ≥ 80 % sobre el banco de preguntas ya modelado; aprobar completa los requisitos teóricos | A, B |
| D | Certificación por curso | Teórico por examen + práctico dictaminado → aprueba el instructor → certificado | A, B, C |
| E | Membresía de club | Unidades, invitaciones, zona e iglesia al crear club, carta de la iglesia, 2FA obligatorio MASTER | independiente |
| F | Guías Mayores / Aventureros | Mismo motor con secciones de carpeta, mentor y bitácora | A |

Decisiones del responsable que gobiernan A:
1. Alcance: progreso + evidencias + revisión (sin exámenes todavía).
2. Cualquier cuenta lleva su progreso y guarda evidencias; **para que alguien dictamine hace falta un revisor con jurisdicción**.
3. El certificado lo emite el **director con un botón** cuando todo está completo (modalidad CLUB).
4. Evidencia **obligatoria sólo en requisitos prácticos** (`honor_requirements.is_theoretical = false`).
5. Futuro (B–D): modalidad COURSE con instructor virtual; lo teórico se completa por examen; lo práctico lo
   dictamina el director del club **o** el instructor del curso; el instructor aprueba el certificado.
   La base de A debe admitirlo sin rehacer nada.

## 1. Modelo de datos — migración `007_portfolio.sql` (aditiva, idempotente)

### `honor_enrollments`
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users, NOT NULL | quien cursa |
| honor_id | uuid FK honors, NOT NULL | la versión publicada al inscribirse (queda fijada) |
| mode | varchar(10) NOT NULL DEFAULT 'CLUB' | `CLUB` \| `COURSE` (COURSE llega en el bloque B junto con `course_id`) |
| club_id | uuid FK organizations NULL | club del miembro; se refresca con su club actual cada vez que el miembro o un revisor escribe en la inscripción; queda congelado al certificar |
| locale | varchar(16) NOT NULL DEFAULT 'es' | idioma de los requisitos que ve |
| status | varchar(15) NOT NULL DEFAULT 'IN_PROGRESS' | `IN_PROGRESS` → `READY` → `CERTIFIED`; `WITHDRAWN` |
| certificate_id | uuid FK certificates NULL | |
| started_at, ready_at, certified_at, withdrawn_at, updated_at | timestamptz | |

Índice único parcial `(user_id, honor_id) WHERE status <> 'WITHDRAWN'`. Índices por `(club_id, status)` y `(user_id, status)`.

### `requirement_progress`
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| enrollment_id | uuid FK honor_enrollments ON DELETE CASCADE | |
| requirement_position | int NOT NULL | **clave estable entre idiomas**: el requisito 3 en `es` y en `en` es el mismo |
| requirement_id | uuid FK honor_requirements NULL ON DELETE SET NULL | fila concreta que vio el miembro (informativo) |
| is_practical | boolean NOT NULL | copia de `NOT is_theoretical` al crear la fila; evita que cambiar la marca después altere inscripciones en curso |
| status | varchar(12) NOT NULL DEFAULT 'PENDING' | `PENDING` → `SUBMITTED` → `COMPLETE` \| `INCOMPLETE`; `INCOMPLETE` → `SUBMITTED` |
| completed_via | varchar(8) NULL | `REVIEW` (dictamen humano) \| `EXAM` (bloque C) |
| member_note | text NULL (≤ 2000) | |
| submitted_at | timestamptz NULL | |
| reviewed_by_id | uuid FK users NULL | |
| reviewed_at | timestamptz NULL | |
| review_note | text NULL (≤ 2000) | obligatoria si el dictamen es INCOMPLETE |

Único `(enrollment_id, requirement_position)`. Las filas se crean todas al inscribirse (una por requisito de nivel
superior del idioma resuelto; si la especialidad no tiene requisitos cargados, la inscripción se rechaza con 409).

### `evidences`
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| progress_id | uuid FK requirement_progress ON DELETE CASCADE | |
| uploaded_by_id | uuid FK users | |
| kind | varchar(8) | `image` \| `pdf` (más adelante video, audio, link) |
| status | varchar(15) NOT NULL DEFAULT 'PENDING_UPLOAD' | `PENDING_UPLOAD` → `ACTIVE` → `REMOVED` |
| storage_key | text NOT NULL UNIQUE | clave en el bucket **privado**; nunca una URL |
| content_type | varchar(40), size_bytes bigint, sha256 char(64) NULL | |
| taken_on | date NULL, place varchar(180) NULL, caption varchar(500) NULL | |
| created_at, removed_at | timestamptz | borrado lógico; el objeto lo purga un script aparte |

Máximo `EVIDENCE_MAX_PER_REQUIREMENT = 6` activas por requisito.

### Cambios en `certificates` (columnas opcionales)
`user_id` FK users, `enrollment_id` FK honor_enrollments, `issued_by_id` FK users, `issued_role` varchar(40).
Los certificados ya emitidos quedan sin vínculo.

### Reglas de integridad (en el servicio, con tests)
1. Dictamen `COMPLETE` en un requisito práctico exige ≥ 1 evidencia `ACTIVE`.
2. Tras cada dictamen se recalcula la inscripción: todos `COMPLETE` ⇒ `READY` (+`ready_at`); si alguno deja de
   estarlo ⇒ `IN_PROGRESS`.
3. `CERTIFIED` y `WITHDRAWN` congelan: ni progreso, ni evidencias, ni dictámenes.
4. Un requisito `COMPLETE` no admite cambios del miembro ni altas/bajas de evidencia (el revisor puede
   reabrirlo dictaminando `INCOMPLETE` con nota mientras la inscripción no esté certificada).
5. Nadie dictamina ni certifica su propia inscripción.

## 2. Permisos y flujo

Una sola función decide quién revisa: `can_review(db, actor, enrollment) -> bool` en `app/rbac.py`.
- `MASTER_GC`: sí.
- `CLUB_DIRECTOR` (club aprobado, no bloqueado) o `INSTRUCTOR` cuya `organization_id` es el **club actual del
  miembro** (`users.organization_id` del inscrito, organización de tipo club y activa): sí.
- (Bloque B) instructor del curso de una inscripción `COURSE`: se añade aquí y sólo aquí.
- El propio inscrito: nunca.
`can_issue(db, actor, enrollment)`: `MASTER_GC` o `CLUB_DIRECTOR` del club actual del miembro (modalidad CLUB);
en COURSE será el instructor del curso (bloque D).
`can_view_portfolio(db, actor, target_user)`: el propio usuario; tutor con `guardianships.consent_status = 'GRANTED'`;
quien cumpla `can_view_user` (jerarquía); revisores con jurisdicción sobre alguna inscripción suya.

Flujo CLUB:
1. Miembro abre la especialidad → **Empezar** → inscripción + filas de progreso.
2. Por requisito: escribe nota, sube evidencias, marca **Enviar a revisión** (`SUBMITTED`). Puede retirarlo a
   `PENDING` mientras no esté dictaminado.
3. Revisor (director / instructor del club) ve la cola **Por revisar**, abre la inscripción, mira evidencias
   (URL firmada de 5 min) y dictamina `COMPLETE` o `INCOMPLETE` + observación.
4. Todo completo ⇒ `READY`; el director ve **Listos para certificar** y emite: elige plantilla, fecha y lugar;
   se crea el certificado (mismo emisor NTAM, folio, hash y QR que hoy) ligado a la cuenta y a la inscripción;
   la inscripción pasa a `CERTIFIED`.
5. Sin club: el miembro puede avanzar y enviar; la pantalla explica que nadie puede dictaminar hasta que
   pertenezca a un club aprobado (o, en el futuro, se inscriba en un curso).
Tutor: sólo lectura del portafolio del menor. Auditoría en `audit_log`: `ENROLL`, `ENROLLMENT_WITHDRAW`,
`REQUIREMENT_SUBMIT`, `REQUIREMENT_REVIEW`, `EVIDENCE_ADD`, `EVIDENCE_REMOVE`, `CERTIFICATE_ISSUE`.

## 3. API — `app/routers/portfolio.py`, prefijo `/api/v1/portfolio` (todo autenticado)

| Método y ruta | Quién | Qué |
|---|---|---|
| `POST /enrollments` `{honor_id, locale?}` | cualquiera activo | 201 detalle; **idempotente**: si ya hay una activa la devuelve (200). 404 especialidad no publicada; 409 sin requisitos |
| `GET /enrollments?status=` | yo | mis inscripciones con contadores (`total`, `complete`, `submitted`, `incomplete`) |
| `GET /enrollments/{id}` | `can_view_portfolio` | especialidad (nombre en el idioma), requisitos con texto + progreso + evidencias (metadatos, **sin URLs**), permisos calculados (`can_review`, `can_issue`, `is_owner`) |
| `DELETE /enrollments/{id}` | dueño | `WITHDRAWN` (409 si `CERTIFIED`) |
| `PUT /enrollments/{id}/requirements/{position}` `{status: SUBMITTED\|PENDING, member_note?}` | dueño | regla 3 y 4 |
| `POST /enrollments/{id}/requirements/{position}/evidences` `{content_type, size_bytes, sha256?, taken_on?, place?, caption?}` | dueño | crea evidencia `PENDING_UPLOAD` y devuelve `{evidence, upload: {url, method: "PUT", headers, expires_in}}` |
| `POST /evidences/{id}/complete` | quien la subió | comprueba el objeto (HEAD: existe, tamaño y tipo coinciden) ⇒ `ACTIVE`; si no, 409 |
| `GET /evidences/{id}/url` | `can_view_portfolio` | `{url, expires_in: 300}` firmada |
| `DELETE /evidences/{id}` | dueño (regla 4) | `REMOVED` |
| `GET /review/queue?status=SUBMITTED\|READY` | revisores | requisitos por dictaminar / inscripciones listas, de los miembros de su club |
| `POST /enrollments/{id}/requirements/{position}/review` `{verdict: COMPLETE\|INCOMPLETE, note?}` | `can_review` | reglas 1, 2, 5; 422 si INCOMPLETE sin nota |
| `POST /enrollments/{id}/certificate` `{template?, issued_date, place?, instructor_name?}` | `can_issue` | 409 si no está `READY`; devuelve el certificado |
| `GET /users/{user_id}` | `can_view_portfolio` | portafolio: inscripciones + certificados de esa persona |
| `GET /me` | yo | lo mismo para mí |

La creación del certificado sale de `main.py` (hoy dentro de `prototype-batch`) a `app/services/certificates.py::issue_certificate(...)`
y la usan ambos. `prototype-batch` no cambia de contrato.
Errores en español, mismos códigos que el resto del API. Esquemas en `app/schemas/portfolio.py`; lógica en
`app/services/portfolio.py`; el router sólo valida, llama y serializa.

## 4. Almacenamiento privado de evidencias

`media.adventist.club` es público: **las evidencias no van ahí**. Bucket nuevo en R2, sin dominio público:
- Ajustes: `R2_PRIVATE_BUCKET_NAME` (sin valor ⇒ los endpoints de evidencia responden 503 «Almacenamiento privado no
  configurado»; todo lo demás funciona). Mismas credenciales R2.
- Clave: `evidence/<user_id>/<enrollment_id>/<evidence_id>.<ext>` (extensión por MIME validado).
- Subida directa del navegador con **URL firmada PUT** (10 min) que fija `Content-Type` y `Content-Length`; el API
  (0.15 CPU) nunca recibe el archivo. Lectura con **URL firmada GET de 5 min**, nunca guardada ni cacheada.
- Tipos: `image/jpeg`, `image/png`, `image/webp`, `application/pdf`. Máx. 10 MB imagen, 20 MB PDF.
- Privacidad de menores: el frontend **recodifica las fotos en el navegador** antes de subir (canvas → JPEG/WebP,
  lado mayor ≤ 2000 px): elimina EXIF/GPS y reduce peso.
- CORS del bucket (lo configura el responsable en Cloudflare): `PUT, GET, HEAD` desde
  `https://conquistadores.app`, `https://www.conquistadores.app` y `http://localhost:3100`; cabeceras `content-type`.
- `migrations/purge_removed_evidence.py`: borra del bucket los objetos `REMOVED`/`PENDING_UPLOAD` con más de 30 días
  (simulacro por defecto, `--commit`).
- `app/services/private_storage.py`: `presign_put`, `presign_get`, `head`, `delete`; cliente inyectable para tests.

## 5. Frontend (`conquistadores.app`) — kit `cq-*`, responsive según la escala, skeletons reales

- `lib/api/portfolio.ts` + tipos; todo por el proxy autenticado `/api/v1/*` (añadir `portfolio` a `ALLOWED_PREFIXES`).
- **`/honors/[id]`**: invitado → lista local como hoy + «Inicia sesión para guardar tu avance». Con sesión →
  **Empezar esta especialidad**; al inscribirse, lo marcado en local se envía una vez como `SUBMITTED`. Cada requisito
  muestra estado (pendiente / enviado / completo / incompleto + observación del revisor), nota, miniaturas de
  evidencia y **Añadir evidencia** (hoja: cámara o archivo, fecha, lugar, descripción; recodificado y barra de
  progreso). Los prácticos indican «requiere evidencia». Sin club: aviso con enlace a `/clubs`.
- **`/portfolio`** (enlazado desde Panel y Perfil; la navegación de 5 elementos no cambia): en curso, listas para
  certificar, certificadas (con su certificado y verificación). `/portfolio/[userId]` en sólo lectura para tutor y
  jerarquía.
- **Panel del director / instructor**: **Por revisar** (cola) → pantalla de revisión de una inscripción: visor de
  evidencias (URL firmada al abrir), dictamen con observación; **Listos para certificar** → diálogo de emisión
  (plantillas del servidor, fecha, lugar) → descarga PNG/PDF con el motor actual.
- **Panel del tutor**: sus menores → portafolio en lectura.
- Componentes nuevos del kit (con su entrada en `/styleguide`): `RequirementProgress`, `EvidenceThumb`,
  `EvidenceSheet`, `ReviewVerdict`, `EnrollmentCard` y sus skeletons.

## 6. Pruebas y despliegue

Backend (pytest de integración, TDD): inscripción idempotente y fijada a versión/idioma; máquina de estados del
requisito; práctico sin evidencia no se completa; `READY` automático y su reversión; congelado al certificar;
matriz de permisos (dueño, director de su club, instructor de su club, director de otro club, tutor con y sin
consentimiento, admin de asociación, MASTER, anónimo); nadie se dictamina a sí mismo; firma de URLs con cliente
simulado; `complete` rechaza tamaño/tipo distinto; emisión crea certificado con `user_id`, `issued_by_id` y pasa a
`CERTIFIED`; auditoría de cada acción; `prototype-batch` sigue igual.
Frontend: `tsc`, `eslint`, `next build`, ampliación de `scripts/e2e-auth.mjs` (inscribirse, enviar, dictaminar,
certificar contra el API local) y verificación en navegador a 375, 768, 1280 y 1920 px, claro y oscuro.
Despliegue: aplicar `007` en Neon **antes** de subir el backend; crear bucket privado + CORS + variable en Render
(responsable); backend; frontend. Nada destructivo.

## Fuera de alcance de A
Exámenes, cursos, unidades e invitaciones, secciones de Guía Mayor, vídeo/audio, firma criptográfica del
certificado, notificaciones por correo de dictámenes (se añaden con el bloque E).
