# Bloques B, C y D — Cursos del instructor virtual, exámenes y certificación por curso

Fecha: 22 sep 2026 · Estado: **propuesta, pendiente de aprobación del responsable** (ver §0).
Repos: `adventist.club` (FastAPI, este) y `conquistadores.app` (Next.js). Todo es aditivo.
Depende de: bloque A (`2026-09-22-portafolio-design.md`, aprobado). **Este documento no cambia A**: se enchufa en
los puntos que A dejó previstos (`honor_enrollments.mode = 'COURSE'` + `course_id`, `requirement_progress.completed_via = 'EXAM'`,
`can_review` / `can_issue` con el instructor del curso, certificados con `issued_by_id` / `issued_role = 'INSTRUCTOR'`).
Los tres puntos donde B–D tocan código de A están listados en §2.2 y ninguno altera su contrato ni sus datos.

Objetivo del responsable: «Las especialidades tendrán también instructores virtuales que suben la especialidad
desarrollada y crean exámenes virtuales como un curso, y al final el Conquistador obtiene el certificado aprobado por
ese instructor. Si pide requisitos físicos, el miembro los sube y el director del club o el instructor los aprueba.»
Regla de la visión: sin parte física, aprobar el examen (≥ 80 %) emite el certificado automáticamente; con parte
física, aprueba un director o el instructor tras revisar la evidencia.

## 0. Decisiones para el responsable

Son decisiones de producto que no se deducen de `VISION_ECOSISTEMA.md` ni de `VISION_ANEXO_MVP.md`. **El resto del
documento está escrito asumiendo la opción recomendada** (marcada ★). Cambiar una decisión afecta sólo a las secciones
indicadas.

| # | Decisión | Opciones | Recomendación y consecuencia |
|---|---|---|---|
| D1 | **Dónde vive la carta de la iglesia** (la exige la visión a instructores, directores y guías mayores; el mapa A–F la nombra en E, cuyo borrador propone `leader_verifications`: por **club** y con un solo paso de validación) | a) ★ **Una sola tabla de cartas en la plataforma**, genérica por `organization_id` y con el doble paso zona → Asociación para quien publica cursos; la crea el bloque que llegue antes y el otro la reutiliza. Aquí se especifica como `church_letters` · b) Esperar a E tal como está; mientras, el admin de Asociación marca a mano «instructor verificado» · c) Dos tablas, una por bloque | **a)**. El instructor virtual puede no pertenecer a ningún club, así que una carta ligada a `club_id` no le sirve; y dos tablas de cartas serían dos verdades. Lo único que B–D necesitan de la verificación es **una función**, `instructor_is_verified(db, user)`: si el responsable prefiere la tabla de E, se generaliza su `club_id` a organización, se añade el paso de Asociación para instructores de curso, esa función pasa a leerla y se omite la migración `009`; nada más de este documento se mueve. Con b) los cursos se lanzan sin carta auditada (contradice «Toda certificación debe estar supervisada»). Afecta a §3.1. |
| D2 | **Quién puede unirse a un curso** (la visión dice «solo se inscribe si pertenece a un club»; A §2.5 anticipa el curso como salida para quien no tiene club) | a) ★ Cualquier cuenta activa; los menores sólo con tutela `GRANTED` · b) Sólo miembros de un club aprobado · c) Menores sólo con club; adultos libres | **a)**. El curso es la vía para quien no tiene club cerca; la supervisión la pone el instructor verificado (carta + protección infantil) y el consentimiento del tutor. Con b) un miembro sin club sigue sin nadie que lo dictamine, que es justo el hueco que A dejó señalado. Afecta a §3.6 (una condición en `join`). |
| D3 | **Alcance territorial de un curso publicado** | a) ★ Global: cualquier miembro ve y toma cualquier curso publicado; la ficha muestra qué Asociación lo aprobó · b) Sólo miembros bajo la Asociación que lo aprobó · c) Configurable por curso | **a)**. «Instructor virtual» implica alcance más allá del club; la jurisdicción de la visión limita actos de autoridad (dictaminar, certificar), no quién puede aprender. Hoy hay una sola Asociación activa (NTAM), así que b) y a) se comportan igual hasta que entre la segunda. Consecuencia de a): una Asociación no puede restringir a sus miembros a sus propios instructores. Con b) se añade un filtro por `org_scope_id` en descubrimiento y `join`, y los miembros sin organización no verían cursos (choca con D2a). Afecta a §3.6. |
| D4 | **Conflicto entre director e instructor al dictaminar un requisito práctico en modalidad COURSE** | a) ★ Cualquiera de los dos dictamina; un `COMPLETE` sólo lo reabre quien lo dio, el instructor del curso o MASTER_GC (el instructor tiene la última palabra porque firma el certificado) · b) Prioridad del director: si el miembro tiene club, sólo su director dictamina lo práctico («en primer lugar el director del club físico») · c) Último dictamen gana, sin restricciones | **a)**. Respeta la frase del responsable («el director o el instructor») y evita el ping-pong de c). El director que discrepe de un `COMPLETE` del instructor escala por la vía de la visión (Director → Zona → Asociación), que puede anular el certificado (§5.5). Con b) el instructor virtual no puede completar el curso de un miembro con club sin esperar al director. Afecta a §5.2. |
| D5 | **Qué ve el miembro tras un intento de examen** | a) ★ Reprobado: nota, desglose por requisito y qué preguntas falló (sin la respuesta correcta). Aprobado o intentos agotados: además respuestas correctas y explicación · b) Siempre todo · c) Sólo la nota | **a)**. Con bancos pequeños, b) convierte el segundo intento en memorizar respuestas; c) no enseña nada. Afecta a §4.5. |
| D6 | **Quién puede ofrecer cursos** (hoy cada usuario tiene **un** rol) | a) ★ Sólo rol `INSTRUCTOR` verificado · b) También `CLUB_DIRECTOR` aprobado con carta · c) Multirrol | **a)**. Un director ya certifica a su club en modalidad CLUB; el curso virtual es para llegar fuera. b) mezcla en una persona los dos lados de D4; c) es un cambio de identidad transversal (fuera de alcance). Consecuencia de a): un director que quiera dar cursos virtuales necesita otra cuenta o cambiar de rol. Afecta a §3.1 y §3.7. |
| D7 | **Vigencia de la verificación del instructor** | a) ★ Fecha `valid_until` opcional que fija quien autoriza; sin política automática · b) Caducidad anual obligatoria · c) Sin caducidad | **a)**. Cuesta una columna y deja a la Asociación decidir; b) genera trabajo de renovación que nadie ha pedido. Debe resolverse igual que la vigencia que se apruebe para la carta en el bloque E (una sola regla para toda la plataforma): si allí se fija un plazo, `valid_until` pasa a rellenarse con ese plazo por defecto. Afecta a §3.1. |

## 1. Hallazgos del código que condicionan el diseño

Se verificaron en el repositorio antes de diseñar; cada uno explica una decisión posterior.

1. **`users.verification_status = 'VERIFIED'` hoy significa «correo verificado»** (`auth.py::_mark_verified`) y además
   lo escribe `POST /users/{id}/verify`. No sirve como «instructor activo verificado» ⇒ la verificación del instructor
   necesita su propio registro (§3.1).
2. **`INSTRUCTOR` es un rol de autorregistro** y `GET /honors/{id}/instructor` entrega el banco **con respuestas** de
   cualquier especialidad publicada a cualquier `INSTRUCTOR`. Un miembro con una segunda cuenta leería las respuestas
   ⇒ el banco de un curso no puede vivir en `honor_questions` y ese endpoint se endurece antes de abrir exámenes (§4.1).
3. **`honor_requirements.is_theoretical` vale `true` por defecto en los 8 631 requisitos importados** («decidirlo es
   tarea de revisión», `ESTADO.md`). Si la emisión automática se guiara por esa marca, Natación se certificaría con un
   test ⇒ cada curso declara cómo evalúa cada requisito y esa declaración pasa por la revisión zonal y de Asociación
   (§3.2 `course_requirements`).
4. **`honor_questions.requirement_id` apunta a una fila que es por versión y por idioma**; `_copy_requirements` clona
   todas las preguntas al versionar y `PUT /honors/{id}` las borra en cascada. Varios instructores sobre la misma
   especialidad oficial pisarían un único banco ⇒ el banco del instructor pertenece **al curso** (§4.1).
5. Las 809 especialidades del catálogo no tienen `created_by_id`, están `PUBLISHED` y por tanto son inmutables; los
   parámetros `exam_*` de `honors` valen sus valores por defecto ⇒ el curso lleva sus propios parámetros de examen.
6. El cuarto tipo de pregunta modelado es **`ESSAY`**, no «carga de evidencia» (`CHECK` de `honor_questions`). La
   evidencia multimedia de un menor ya tiene un único cauce seguro: el requisito práctico de A (bucket privado, URL
   firmada, dictamen). Duplicarlo dentro del examen crearía un segundo circuito de fotos de menores ⇒ en C «evidencia»
   es un requisito `EVIDENCE` del curso, no un tipo de pregunta (§4.1).
7. `main.py::canonical()` define el hash del certificado; **no puede cambiar** o dejarían de verificar los ya emitidos.
   `instructor_name` y `director_name` ya forman parte del hash y las plantillas ya los pintan como firmas ⇒ el título
   del curso se muestra por relación (inscripción → curso), fuera del hash (§5.4).
8. El API corre en 0,15 CPU sin tareas en segundo plano ⇒ la caducidad de intentos se resuelve de forma perezosa, al
   tocar el intento (§4.4). Ningún diseño de este documento necesita cron ni colas.
9. El coordinador de zona cuelga **al lado** de los clubes, no encima (`rbac.py::club_scope_paths`) ⇒ la revisión de
   cursos y cartas usa ese mismo cálculo de alcance, no `org_in_user_scope` a secas.
10. En el frontend, carpetas dinámicas hermanas con nombres distintos rompen `next start` (`ESTADO.md`) ⇒ todas las
    rutas nuevas usan `[id]` en su primer nivel (§8).

## 2. Encaje con el bloque A

### 2.1 Qué usa cada bloque de lo que A ya dejó

| Pieza de A | B | C | D |
|---|---|---|---|
| `honor_enrollments.mode` | pasa a `COURSE` al unirse y vuelve a `CLUB` al salir | lee | lee |
| `honor_enrollments.course_id` (A lo anuncia; lo crea B) | crea la columna y la escribe | lee | lee |
| `requirement_progress.is_practical` | lo ajusta al unirse según el plan del curso (sólo filas no `COMPLETE`) | — | lee para la emisión automática |
| `requirement_progress.completed_via = 'EXAM'` | — | lo escribe al aprobar | — |
| Regla 2 de A (todos `COMPLETE` ⇒ `READY`) | se reutiliza tal cual | se invoca tras aprobar | es **la** regla de `READY` en COURSE |
| `can_review` | añade la cláusula del instructor del curso | — | — |
| `can_issue` | — | — | añade la cláusula del instructor del curso |
| `services/certificates.py::issue_certificate` | — | — | se llama igual; `issued_role = 'INSTRUCTOR'` |
| `services/private_storage.py` | cartas de iglesia (`letters/…`) | — | — |
| `GET /portfolio/review/queue` | incluye las inscripciones de los cursos del instructor | excluye requisitos `EXAM` | cola «listos para certificar» del instructor |

### 2.2 Puntos de contacto con código de A (sin cambiar su contrato)

1. **Visibilidad por inscripción.** A da lectura del portafolio a «revisores con jurisdicción sobre alguna inscripción
   suya». Para un instructor **virtual** (ajeno al club del menor) eso sería demasiado: vería todo el portafolio.
   B añade `can_view_enrollment(db, actor, enrollment)` = `can_view_portfolio(db, actor, dueño)` **o** «es el
   instructor del curso de *esta* inscripción». Los endpoints de A que reciben una inscripción o una evidencia
   (`GET /enrollments/{id}`, `GET /evidences/{id}/url`) pasan a usarla. El instructor del curso **no** entra en
   `can_view_portfolio`: nunca ve `GET /portfolio/users/{id}`.
2. **Serialización.** `GET /portfolio/enrollments/{id}` añade `course: {id, title, instructor_name} | null` y, por
   requisito, `assessment` (`EXAM` | `REVIEW` | `EVIDENCE` | `null` en CLUB). Campos nuevos, ninguno cambia.
3. **Anulación.** El servicio de anulación (§5.5) es la única transición `CERTIFIED → WITHDRAWN`. `DELETE
   /portfolio/enrollments/{id}` sigue respondiendo 409 para una inscripción certificada, como en A.

## 3. Bloque B — Cursos del instructor virtual

Un **curso** es la oferta que un instructor verificado hace de **una versión publicada de una especialidad** en **un
idioma**: lecciones con material, un plan de evaluación por requisito y (desde C) un banco de preguntas y un examen.
Varios instructores pueden ofrecer la misma especialidad; cada curso es independiente.

### 3.1 Verificación del instructor — migración `009_church_letters.sql`

`church_letters` (propósito único: la carta firmada por la iglesia que respalda un cargo, con su validación):

| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users NOT NULL ON DELETE CASCADE | quien la presenta |
| role_requested | varchar(40) NOT NULL DEFAULT 'INSTRUCTOR' | se valida en el servicio contra `LETTER_ROLES = ("INSTRUCTOR",)`; E y F amplían la constante sin migrar |
| organization_id | uuid FK organizations NOT NULL | organización del usuario al presentarla; fija quién la revisa y no cambia después |
| church_name | varchar(180) NOT NULL | |
| pastor_name | varchar(180) NULL | |
| church_org_id | uuid FK organizations NULL | para cuando E cargue iglesias; hoy siempre NULL |
| storage_key | text NOT NULL UNIQUE | bucket **privado** de A: `letters/<user_id>/<letter_id>.<ext>` |
| content_type | varchar(40) NOT NULL | `application/pdf`, `image/jpeg`, `image/png`, `image/webp` |
| size_bytes | bigint NOT NULL CHECK (> 0) | máx. 10 MB |
| status | varchar(16) NOT NULL DEFAULT 'PENDING_UPLOAD' | `PENDING_UPLOAD` → `SUBMITTED` → `ZONE_VALIDATED` → `AUTHORIZED`; `REJECTED` desde `SUBMITTED` o `ZONE_VALIDATED`; `REVOKED` desde `AUTHORIZED` |
| zone_validated_by_id, zone_validated_at | uuid FK users NULL, timestamptz NULL | |
| decided_by_id, decided_at | uuid FK users NULL, timestamptz NULL | quien autorizó, rechazó o revocó |
| decision_note | text NULL (≤ 2000) | obligatoria en `REJECTED` y `REVOKED` |
| valid_until | date NULL | D7: la fija quien autoriza; NULL = sin caducidad |
| created_at, updated_at | timestamptz | |

Índice único parcial `(user_id, role_requested) WHERE status IN ('SUBMITTED','ZONE_VALIDATED','AUTHORIZED')`: una carta
viva por persona y cargo. Índice `(organization_id, status)` para la cola.

Flujo (el mismo escalón doble de la visión: «valida instructores con carta pastoral» la zona, «autoriza instructores»
la Asociación): el instructor sube la carta (URL firmada PUT, igual que una evidencia) → `SUBMITTED` → un
`ZONE_REVIEWERS` en alcance la valida → `ZONE_VALIDATED` → un `ASSOCIATION_REVIEWERS` en alcance la autoriza →
`AUTHORIZED`. Como en `STAGE_REVIEWERS` de especialidades, un revisor de Asociación puede dar también el paso zonal
(hoy casi ninguna Asociación tiene coordinadores de zona). Nadie valida su propia carta. El alcance se calcula con
`club_scope_paths` (hallazgo 9) sobre `church_letters.organization_id`; un usuario sin `organization_id` recibe 409
«Elige primero tu Asociación o club».

**Puerta única** en `app/rbac.py`:

```
instructor_is_verified(db, user) -> bool
  user.role == INSTRUCTOR  ∧  user.status == 'ACTIVE'  ∧  not user.is_minor
  ∧ user.verification_status == 'VERIFIED'            (correo verificado)
  ∧ user.child_protection_completed                    (curso de protección infantil; lo marca un admin, endpoint existente)
  ∧ ∃ church_letters(user, 'INSTRUCTOR', status = 'AUTHORIZED', valid_until IS NULL OR valid_until >= hoy)
```

Se consulta en: enviar un curso a revisión, publicarlo, mostrarlo en descubrimiento, aceptar inscripciones, dictaminar,
calificar, abrir sesión presencial y emitir. Un instructor suspendido, con carta revocada o caducada **deja de poder
actuar al instante** sin tocar sus cursos ni las inscripciones (§3.5). Crear y editar un borrador **no** exige la
puerta: el instructor puede preparar el curso mientras su carta está en trámite.

API — `app/routers/church_letters.py`, prefijo `/api/v1/church-letters`:

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `GET /me` | autenticado | lista de comprobación: `{role, email_verified, child_protection, letter: {status, valid_until, decision_note} \| null, verified}` |
| `POST /` `{church_name, pastor_name?, content_type, size_bytes}` | `INSTRUCTOR` adulto | crea `PENDING_UPLOAD` y devuelve `{letter, upload}` (PUT firmado 10 min). 409 si ya hay una carta viva; 409 sin organización; 503 sin bucket privado |
| `POST /{id}/complete` | quien la subió | HEAD del objeto (existe, tamaño y tipo coinciden) ⇒ `SUBMITTED`; si no, 409 |
| `GET /queue?status=SUBMITTED\|ZONE_VALIDATED` | `ZONE_REVIEWERS` | cartas en su alcance |
| `GET /{id}/url` | dueño o revisor en alcance | `{url, expires_in: 300}` |
| `POST /{id}/review` `{action: VALIDATE\|AUTHORIZE\|REJECT\|REVOKE, note?, valid_until?}` | revisor del escalón, en alcance, no el dueño | 403 fuera de escalón o alcance; 409 transición inválida; 422 `REJECT`/`REVOKE` sin nota |

### 3.2 Modelo de datos del curso — migración `009b_courses.sql`

#### `courses` (la oferta)
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| honor_id | uuid FK honors NOT NULL | **versión publicada** sobre la que se construyó; nunca cambia |
| instructor_id | uuid FK users NOT NULL | autor y responsable |
| org_scope_id | uuid FK organizations NOT NULL | organización del instructor al crearlo; decide qué revisores lo ven (como `honors.org_scope_id`) |
| locale | varchar(16) NOT NULL DEFAULT 'es' | un curso, un idioma; debe existir lista de requisitos de la especialidad en ese idioma |
| title | varchar(180) NOT NULL | p. ej. «Nudos con el Inst. Pérez» |
| summary | varchar(600) NULL | |
| cover_url | text NULL | sólo `https://media.adventist.club/…` |
| status | varchar(30) NOT NULL DEFAULT 'DRAFT' | `DRAFT` → `ZONE_REVIEW` → `ASSOCIATION_REVIEW` → `PUBLISHED` → `ARCHIVED` |
| archived_by_authority | boolean NOT NULL DEFAULT false | true cuando lo retira un revisor, no el instructor (§3.5) |
| archive_reason | text NULL (≤ 2000) | obligatorio si lo retira la autoridad |
| version | int NOT NULL DEFAULT 1 | |
| previous_version_id | uuid FK courses NULL | |
| changes_description | text NULL | |
| approved_zone_org_id, approved_association_org_id | uuid FK organizations NULL | |
| enrollment_open | boolean NOT NULL DEFAULT true | operativo: el instructor pausa altas sin despublicar |
| capacity | int NULL CHECK (capacity > 0) | operativo: NULL = sin cupo |
| published_at, archived_at, created_at, updated_at | timestamptz | |

Únicos parciales `(instructor_id, honor_id, locale) WHERE status = 'PUBLISHED'` y
`(instructor_id, honor_id, locale) WHERE status IN ('DRAFT','ZONE_REVIEW','ASSOCIATION_REVIEW')`: un curso vivo y, a lo
sumo, una versión nueva en preparación. Índices `(honor_id, status)`, `(instructor_id, status)`, `(org_scope_id, status)`.

**Contenido frente a operación.** Un curso `PUBLISHED` es inmutable en su *contenido* (datos de la ficha, lecciones,
plan de evaluación, banco y parámetros de examen): cambiarlo exige versión nueva y nueva revisión (visión: «versionado
de cursos y actividades»; la revisión es además el filtro de contenido para menores). Siguen editables los campos
*operativos*: `enrollment_open`, `capacity` y, desde C, el código de sesión.

#### `course_lessons` (las lecciones, con sus bloques)
| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| course_id | uuid FK courses ON DELETE CASCADE | |
| position | int NOT NULL | orden; índice `(course_id, position)`, no único (reordenar sin bloqueos, igual que `honor_requirements`) |
| title | varchar(180) NOT NULL | |
| requirement_positions | int[] NOT NULL DEFAULT '{}' | requisitos que cubre; informativo, pinta «Lección 2 · requisitos 3 y 4» |
| blocks | jsonb NOT NULL DEFAULT '[]' | lista ordenada de bloques (§3.3) |
| created_at, updated_at | timestamptz | |

Los bloques van en JSONB y no en una tabla: siempre se leen y se guardan con su lección, no se consultan sueltos y no
tienen ciclo de vida propio. Límites: 30 lecciones por curso, 40 bloques por lección, 200 KB por lección.

#### `course_requirements` (cómo evalúa este curso cada requisito)
| columna | tipo | notas |
|---|---|---|
| course_id | uuid FK courses ON DELETE CASCADE | PK (course_id, requirement_position) |
| requirement_position | int NOT NULL | la clave estable de A |
| assessment | varchar(10) NOT NULL | `EXAM` (lo completa el examen; disponible desde C) · `REVIEW` (dictamen humano sin evidencia obligatoria) · `EVIDENCE` (práctico: dictamen humano con ≥ 1 evidencia) |
| draw_count | smallint NOT NULL DEFAULT 0 | preguntas que se sortean de su banco por intento; `CHECK ((assessment = 'EXAM') = (draw_count > 0))` |
| guidance | text NULL (≤ 2000) | qué evidencia o trabajo espera el instructor |

Reglas del plan: una fila por cada posición que A crearía al inscribirse en `course.locale` (se usa **la misma función
de A** que resuelve los requisitos de nivel superior, para que no puedan divergir); si la especialidad marca el requisito
como práctico (`is_theoretical = false`) el curso **debe** declararlo `EVIDENCE` — el curso puede ser más estricto que
la especialidad, nunca más laxo. Los revisores aprueban el plan junto con el contenido (hallazgo 3).

#### Cambios en tablas existentes (aditivos)
- En `009c_course_enrollment.sql` — `honor_enrollments`: `course_id uuid NULL REFERENCES courses(id)`, `course_joined_at timestamptz NULL`,
  `CHECK ((mode = 'COURSE') = (course_id IS NOT NULL))` (todas las filas actuales lo cumplen) e índice
  `(course_id, status) WHERE course_id IS NOT NULL`.
- En `009b_courses.sql`: `honor_reviews.course_id uuid NULL REFERENCES courses(id) ON DELETE CASCADE` + índice
  `(course_id, reviewed_at) WHERE course_id IS NOT NULL`. El historial de revisión del curso **reutiliza
  `honor_reviews`** (misma forma: revisor, acción, comentario); cada fila de curso lleva `honor_id = course.honor_id`
  y `course_id`. Único cambio en código existente: `honors.py::_build_detail` filtra `HonorReview.course_id IS NULL`
  para que el historial de una especialidad no muestre revisiones de cursos (con test de regresión).

**Reutilización de `honor_resources`:** no se copian ni se escriben. La página del curso muestra, junto a las lecciones,
los recursos oficiales de la especialidad (p. ej. el PDF de requisitos) leyéndolos de `GET /honors/{id}`. El material
propio del instructor vive en sus lecciones: `honor_resources` pertenece a la especialidad, es inmutable una vez
publicada y lo comparten todos los cursos.

### 3.3 Bloques de contenido

Validados en el servidor con una unión discriminada de Pydantic; cada bloque lleva un `id` corto estable.

| `type` | Campos | Reglas |
|---|---|---|
| `text` | `markdown` (≤ 20 000) | Markdown sin HTML crudo; el frontend lo pinta con un renderizador que no interpreta HTML; enlaces con `rel="noopener nofollow"` |
| `image` | `url`, `alt` (obligatorio, ≤ 300), `caption` (≤ 500) | `url` sólo de `media.adventist.club`; se sube con el `POST /media/upload` existente, carpeta nueva `courses` en `ALLOWED_FOLDERS` |
| `pdf` | `url`, `name` (≤ 255) | igual que imagen; máx. 50 MB (límite actual de `storage.py`) |
| `video` | `provider` (`youtube` \| `vimeo`), `video_id`, `title` (≤ 180), `caption?` | el servidor recibe la URL, la valida contra la lista de proveedores y guarda sólo proveedor + id; el frontend incrusta con `youtube-nocookie.com` / `player.vimeo.com`, sin reproducción automática |

El material del curso va al bucket **público** porque es contenido didáctico revisado, no datos personales. Norma para
autores y revisores (texto visible en el editor y en la pantalla de revisión): **ninguna foto en la que se reconozca a
un menor**. No se aloja vídeo propio (fuera de alcance).

### 3.4 Autoría y revisión

Mismo flujo y mismos revisores que las especialidades: `DRAFT → ZONE_REVIEW → ASSOCIATION_REVIEW → PUBLISHED`;
`REJECT` y `REQUEST_CHANGES` devuelven a `DRAFT` con comentario; el autor no revisa lo suyo; cada decisión añade una
fila a `honor_reviews` y otra a `audit_log` en la misma transacción. Las constantes del flujo (`DRAFT`…, `ZONE_REVIEWERS`,
`ASSOCIATION_REVIEWERS`, `STAGE_REVIEWERS`) se mueven de `routers/honors.py` a `app/workflow.py` sin cambiar
comportamiento, y las importan ambos routers. El alcance del revisor se calcula con `club_scope_paths` sobre
`courses.org_scope_id`. No hay atajo de publicación directa para MASTER_GC (no hace falta).

Validaciones de `submit` (400 con la lista de lo que falta): puerta `instructor_is_verified`; la especialidad sigue
`PUBLISHED`; ≥ 1 lección con ≥ 1 bloque; plan completo y coherente (§3.2); desde C, banco suficiente (§4.1).

Al aprobar la Asociación: `PUBLISHED`, `published_at`; si `previous_version_id` está `PUBLISHED`, pasa a `ARCHIVED`
(`archived_by_authority = false`) en la misma transacción, con ambas filas bloqueadas y en ese orden (primero se
archiva, luego se publica: el índice único parcial no es diferible).

### 3.5 Versiones, despublicación y qué pasa con los inscritos

| Suceso | Curso | Inscritos (`IN_PROGRESS` / `READY`) |
|---|---|---|
| El instructor crea versión nueva (`POST /courses/{id}/version`) | la nueva nace `DRAFT` copiando lecciones, plan y banco; la publicada **sigue viva** hasta que la nueva se publique | nada cambia; siguen en su versión (`course_id` no se mueve) y la terminan con el mismo material y el mismo examen |
| La versión nueva se publica | la anterior ⇒ `ARCHIVED` | siguen en la anterior hasta certificar o salir; quien quiera la nueva sale y se une (los intentos se cuentan por curso) |
| El instructor archiva su curso (`DELETE /courses/{id}`) | `ARCHIVED`, sin altas nuevas | conservan lecciones, examen e instructor hasta terminar |
| La autoridad retira el curso (`DELETE` por revisor en alcance, con motivo) | `ARCHIVED`, `archived_by_authority = true` | lecciones y examen se cierran; el intento abierto se anula sin contar; el instructor pierde `can_review` / `can_issue` sobre ese curso; el progreso **se conserva** y la pantalla ofrece «Pasar a modalidad club» o «Elegir otro curso» |
| El instructor deja de estar verificado (suspensión, carta revocada o caducada) | desaparece del descubrimiento; no admite altas | igual que la fila anterior, pero reversible: al recuperar la verificación todo vuelve a funcionar |
| La especialidad se versiona (la fila de `honors` pasa a `ARCHIVED`) | sin altas nuevas; el instructor ve «Actualiza tu curso a la versión N» (`POST /courses/{id}/version` con `honor_id` de la versión nueva; se copian las posiciones que sigan existiendo) | terminan sobre la versión que cursaban, igual que en A |

«Pasar a modalidad club» = `POST /courses/{id}/leave`: `mode = 'CLUB'`, `course_id = NULL`; **no se pierde nada**: las
filas `COMPLETE` (por dictamen o por examen) siguen completas y el director del club puede dictaminar el resto y emitir
según A.

### 3.6 Descubrimiento, inscripción y cupo

- **Descubrimiento:** `/honors/[id]` muestra «Cursos con instructor» con los cursos `PUBLISHED` de esa versión, con
  instructor verificado y `enrollment_open`, primero los del idioma de la interfaz. Cada tarjeta: título, instructor,
  idioma, Asociación que lo aprobó, número de lecciones, cómo se evalúa (n requisitos por examen, m con evidencia) y
  plazas libres. Lista pública (D3a); el contenido de las lecciones sólo para inscritos, autor y revisores.
- **Unirse** (`POST /courses/{id}/join`), en una transacción con el curso bloqueado (`FOR UPDATE`):
  1. Curso `PUBLISHED`, `enrollment_open`, especialidad `PUBLISHED`, instructor verificado; si no, 409.
  2. Cuenta activa; el instructor no se une a su propio curso (403); un menor necesita una tutela con
     `consent_status = 'GRANTED'` (403 «Tu tutor debe autorizar tu participación») — D2a.
  3. Cupo: inscripciones del curso en `IN_PROGRESS` o `READY` < `capacity`; si no, 409 «Curso lleno». No hay lista de espera.
  4. Inscripción: si no hay una viva para `(usuario, course.honor_id)` se crea con **el servicio de inscripción de A**
     y `locale = course.locale`. Si la hay: debe estar `IN_PROGRESS` (409 si `READY` o `CERTIFIED`), no estar en otro
     curso (409 «Sal primero del curso actual») y tener exactamente las mismas posiciones que el plan (409 si la lista
     de su idioma no coincide).
  5. Se fija `mode = 'COURSE'`, `course_id`, `course_joined_at` y se aplica el plan a las filas **no** `COMPLETE`:
     `is_practical = (assessment = 'EVIDENCE')`; las filas `SUBMITTED` de requisitos `EXAM` vuelven a `PENDING`
     (conservan la nota). Se recalcula la regla 2 de A. Idempotente: repetir la llamada devuelve la misma inscripción.
- **Salir** (`POST /courses/{id}/leave`): ver §3.5. Se permite en `IN_PROGRESS` y `READY`. El intento abierto se anula
  sin contar. `is_practical` no se relaja al salir.
- **Expulsar** (`DELETE /courses/{id}/members/{enrollment_id}` con motivo): mismo efecto que salir; el miembro ve el motivo.
- El miembro nunca ve a los demás inscritos. No hay foro, comentarios ni mensajes (§6).

### 3.7 Permisos (se extiende, no se duplica)

```
can_review(db, actor, enrollment)            # app/rbac.py — se añade UNA cláusula a la función de A
  … cláusulas de A sin cambios …
  ∨ ( enrollment.mode == 'COURSE'
      ∧ course.instructor_id == actor.id ∧ actor.id != enrollment.user_id
      ∧ course.status ∈ {PUBLISHED, ARCHIVED} ∧ not course.archived_by_authority
      ∧ instructor_is_verified(db, actor) )
```

La cláusula del director y del instructor **del club** de A no distingue modalidad, así que en COURSE siguen pudiendo
dictaminar (es el «director o instructor» del responsable). `can_view_enrollment` (§2.2) es la única función nueva de
lectura. Permisos de autoría: crea y edita el dueño (`INSTRUCTOR`, D6a); ven un curso no publicado su autor y los
revisores en alcance (404, no 403, para los demás, como en especialidades).

### 3.8 API — `app/routers/courses.py`, prefijo `/api/v1/courses`

Rutas literales antes que `/{course_id}`. Esquemas en `app/schemas/course.py`; lógica en `app/services/courses.py`.

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `GET /?honor_id=&locale=` | público | cursos descubribles de esa especialidad (tarjeta de §3.6). Nunca borradores |
| `GET /my/created?status=` | `INSTRUCTOR` | mis cursos, paginado |
| `GET /my/joined` | autenticado | cursos en los que estoy inscrito |
| `GET /pending/reviews` | `ZONE_REVIEWERS` | cursos en mi escalón y alcance |
| `POST /` `{honor_id, locale, title, summary?, cover_url?}` | `INSTRUCTOR` | 201 `DRAFT` con el plan precargado (`EVIDENCE` donde la especialidad marca práctico, `REVIEW` en el resto). 404 especialidad no publicada; 409 sin requisitos en ese idioma; 409 ya tiene un curso vivo o en preparación para esa especialidad e idioma; 409 sin organización |
| `GET /{id}` | público / inscrito | ficha, títulos de lecciones, plan, plazas; **con bloques** sólo para inscritos. Nunca incluye el banco. 404 si no es visible |
| `GET /{id}/instructor` | autor, revisor en alcance, MASTER_GC | todo: bloques, plan, historial de revisión y (desde C) banco con respuestas |
| `PUT /{id}` | autor, sólo `DRAFT` | ficha y (desde C) parámetros de examen. 400 si no es borrador |
| `POST /{id}/lessons` · `PUT /{id}/lessons/{lesson_id}` · `DELETE /{id}/lessons/{lesson_id}` | autor, sólo `DRAFT` | una lección con todos sus bloques; 422 bloque inválido; 413 lección > 200 KB |
| `PUT /{id}/lessons/order` `{lesson_ids: []}` | autor, sólo `DRAFT` | reordena; 422 si la lista no coincide |
| `PUT /{id}/plan` `[{position, assessment, guidance?}]` | autor, sólo `DRAFT` | plan completo; 422 si falta una posición o relaja un requisito práctico |
| `POST /{id}/submit` | autor | `DRAFT → ZONE_REVIEW`; 400 con la lista de lo que falta; 403 si no está verificado |
| `POST /{id}/review` `{action, comments?}` | revisor del escalón, en alcance, no el autor | mismo contrato que `POST /honors/{id}/review` |
| `POST /{id}/version` `{changes_description, honor_id?}` | autor | 201 `DRAFT` copia; 400 si el curso no está `PUBLISHED`; 409 si ya hay una versión en preparación |
| `DELETE /{id}` `{reason?}` | autor, o revisor en alcance con `reason` | `ARCHIVED` (§3.5) |
| `PATCH /{id}/operation` `{enrollment_open?, capacity?}` | autor verificado, curso `PUBLISHED` | 409 si `capacity` queda por debajo de los inscritos |
| `POST /{id}/join` · `POST /{id}/leave` | miembro | §3.6 |
| `GET /{id}/members` | autor, MASTER_GC | por inscripción: nombre, club, contadores de A, estado del examen. **Sin correo, fecha de nacimiento ni datos de contacto** |
| `DELETE /{id}/members/{enrollment_id}` `{reason}` | autor | §3.6; 422 sin motivo |

Errores en español y con los mismos códigos que el resto del API.

### 3.9 Reglas de integridad de B (en el servicio, con tests)

1. El contenido de un curso no `DRAFT` no se modifica (400); sólo los campos operativos.
2. `mode = 'COURSE'` ⇔ `course_id` no nulo (CHECK) y `course.honor_id = enrollment.honor_id` (servicio).
3. Un usuario tiene como máximo un curso por inscripción; una inscripción por especialidad (índice de A).
4. El plan nunca relaja un requisito práctico de la especialidad.
5. Todo lo que el instructor hace sobre inscripciones exige `instructor_is_verified` en ese momento.
6. Las reglas 1–5 de A siguen aplicando sin excepción a inscripciones COURSE.

## 4. Bloque C — Exámenes

El examen pertenece al curso. Se rinde dentro de una inscripción COURSE y, al aprobarse, completa los requisitos que el
plan marca `EXAM`.

### 4.1 Modelo de datos — migración `010_exams.sql`

**Paso previo de seguridad (hallazgo 2):** `GET /honors/{id}/instructor` pasa a permitir el banco con respuestas sólo
al creador, a los revisores en alcance y a MASTER_GC, también para especialidades publicadas. Se hace **antes** de
habilitar bancos de curso.

Columnas nuevas en `courses` (contenido; se revisan y quedan inmutables al publicar, salvo las dos de sesión):

| columna | tipo | notas |
|---|---|---|
| exam_passing_score | int NOT NULL DEFAULT 80 CHECK (BETWEEN 80 AND 100) | el instructor puede subir el umbral, nunca bajarlo de 80 |
| exam_time_limit_minutes | int NULL CHECK (BETWEEN 5 AND 180) | NULL = sin límite (valor por defecto y recomendado) |
| max_exam_attempts | int NOT NULL DEFAULT 3 CHECK (BETWEEN 1 AND 10) | |
| exam_mode | varchar(10) NOT NULL DEFAULT 'ONLINE' | `ONLINE` \| `IN_PERSON` (todo intento exige código de sesión) |
| session_code | varchar(8) NULL | operativo: código vigente (§4.6) |
| session_code_expires_at | timestamptz NULL | operativo |

`course_questions` (banco del curso; misma forma que `honor_questions` para reutilizar `QuestionIn` / `QuestionOut`):

| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| course_id, requirement_position | FK compuesta → `course_requirements` ON DELETE CASCADE | el banco es **por requisito** del curso |
| position | int NOT NULL | orden en el editor |
| question_text | text NOT NULL (≤ 1000) | |
| question_type | varchar(20) NOT NULL | `MULTIPLE_CHOICE`, `TRUE_FALSE`, `SHORT_ANSWER`, `ESSAY` (los cuatro ya modelados) |
| options | jsonb NULL | `MULTIPLE_CHOICE`: 2–6 opciones distintas |
| correct_answer | text NOT NULL | `MULTIPLE_CHOICE`: texto idéntico a una opción · `TRUE_FALSE`: `true`/`false` · `SHORT_ANSWER`: 1–10 respuestas aceptadas separadas por `\|` (≤ 120 c/u) · `ESSAY`: rúbrica para quien califica |
| points | int NOT NULL DEFAULT 1 CHECK (BETWEEN 1 AND 10) | |
| explanation | text NULL (≤ 1000) | se muestra según D5 |

Por qué tabla propia y no `honor_questions`: hallazgos 2 y 4. Para especialidades escritas por el propio instructor,
el editor ofrece **«Importar el banco de la especialidad»**: copia `honor_questions` → `course_questions` casando por
posición, sólo si `honor.created_by_id = course.instructor_id`.

`exam_attempts` (un intento):

| columna | tipo | notas |
|---|---|---|
| id | uuid PK | |
| enrollment_id | uuid FK honor_enrollments ON DELETE CASCADE | |
| course_id | uuid FK courses NOT NULL | los intentos se cuentan por `(enrollment_id, course_id)` |
| user_id | uuid FK users NOT NULL | desnormalizado para colas y límites |
| attempt_no | smallint NOT NULL | único `(enrollment_id, course_id, attempt_no)` |
| status | varchar(16) NOT NULL DEFAULT 'IN_PROGRESS' | `IN_PROGRESS` → `PASSED` \| `FAILED` \| `PENDING_GRADING` → `PASSED` \| `FAILED`; cualquiera → `VOIDED` |
| started_at, deadline_at | timestamptz NOT NULL | `deadline_at` = inicio + límite (con tiempo adicional) o, sin límite, inicio + 72 h |
| submitted_at, finished_at | timestamptz NULL | entrega · resultado definitivo |
| time_limit_minutes, passing_score | smallint | copia de los parámetros aplicados |
| points_total, points_awarded, score_percent | int, int NULL, smallint NULL | |
| completed_positions | int[] NOT NULL DEFAULT '{}' | requisitos que este intento completó (para revertir al anular) |
| proctored | boolean NOT NULL DEFAULT false | se inició con código de sesión válido |
| auto_submitted | boolean NOT NULL DEFAULT false | lo cerró el plazo, no el miembro |
| voided_by_id, voided_at, void_reason | uuid NULL, timestamptz NULL, text NULL | |

Único parcial `(enrollment_id) WHERE status = 'IN_PROGRESS'` (un intento abierto). Índice `(course_id, status)`.

`exam_answers` (el «papel» del intento: una fila por pregunta sorteada, creada al empezar):

| columna | tipo | notas |
|---|---|---|
| attempt_id, position | PK; FK exam_attempts ON DELETE CASCADE | orden en que se presenta |
| question_id | uuid FK course_questions | el curso publicado es inmutable, así que la pregunta no cambia bajo un intento |
| requirement_position | int NOT NULL | para el desglose |
| option_order | jsonb NULL | permutación de índices con la que se barajaron las opciones |
| response | text NULL (≤ 4000) | `MULTIPLE_CHOICE`: índice original de la opción · `TRUE_FALSE`: `true`/`false` · texto en las otras |
| answered_at | timestamptz NULL | |
| is_correct | boolean NULL | NULL = pendiente de calificar |
| points_possible, points_awarded | smallint, smallint NULL | |
| graded_by_id, graded_at, grader_note | uuid NULL, timestamptz NULL, text NULL (≤ 1000) | calificación manual |

También en `010_exams.sql`: `honor_enrollments.exam_extra_time_percent smallint NOT NULL DEFAULT 0 CHECK (IN (0,25,50,100))`
(adaptación de accesibilidad, §4.7).

Validación de banco al enviar a revisión: cada requisito `EXAM` tiene `draw_count ≥ 1` y banco ≥ `draw_count`; total
sorteado ≤ 60; cada pregunta cumple su tipo. Aviso no bloqueante para autor y revisores cuando el banco es menor que
2 × `draw_count` («el examen apenas variará entre intentos»).

### 4.2 Empezar un intento

`POST /exams/enrollments/{enrollment_id}/attempts` `{pledge: true, session_code?}`. Condiciones (409 salvo indicación):
dueño de la inscripción; inscripción COURSE `IN_PROGRESS`; curso `PUBLISHED` o `ARCHIVED` no retirado por la autoridad,
con instructor verificado; el plan tiene algún requisito `EXAM` y queda alguno sin completar («Ya completaste la parte
teórica»); intentos no anulados < `max_exam_attempts`; ningún intento `PENDING_GRADING`; `pledge` verdadero (422);
en `IN_PERSON`, código vigente (403). Si ya hay un intento abierto **se devuelve ése** (200): empezar es idempotente.

Sorteo (en el servidor, `secrets.SystemRandom`): por cada requisito `EXAM`, `draw_count` preguntas de su banco,
prefiriendo las que el miembro no vio en intentos anteriores; se baraja el orden de las preguntas y el de las opciones
y **se persiste** en `exam_answers`. El cliente recibe enunciado, tipo, opciones ya barajadas y puntos; el esquema de
salida `PaperQuestionOut` **no tiene** campos `correct_answer` ni `explanation`: no pueden filtrarse por construcción
(mismo criterio que el detalle público de especialidades).

### 4.3 Responder, reanudar y entregar

- `PUT /exams/attempts/{id}/answers/{position}` guarda **una** respuesta (autoguardado al cambiar de pregunta). Se
  puede navegar y corregir libremente hasta entregar.
- **Reanudar:** el intento vive en el servidor. Cerrar la pestaña, quedarse sin batería o cambiar de dispositivo no
  pierde nada: `GET /exams/attempts/{id}` devuelve el papel, lo respondido y `remaining_seconds`. El reloj **no se
  detiene** (el plazo es fijo). El frontend guarda además en `localStorage` las respuestas aún no confirmadas y las
  reenvía al recuperar conexión.
- `POST /exams/attempts/{id}/submit` entrega. Pasado `deadline_at` + 30 s de gracia, guardar responde 409 y el intento
  se cierra con lo guardado (`auto_submitted = true`).
- **Caducidad perezosa** (hallazgo 8): `finalize_if_expired(db, attempt)` se ejecuta en cada lectura o escritura del
  intento, al consultar el estado del examen, al empezar otro y en las vistas del instructor. No hay cron.

### 4.4 Calificación

| Tipo | Cómo se califica |
|---|---|
| `MULTIPLE_CHOICE`, `TRUE_FALSE` | automática |
| `SHORT_ANSWER` | automática contra las respuestas aceptadas tras normalizar (sin mayúsculas, acentos, puntuación ni espacios repetidos). Si **no** coincide queda **pendiente**, no incorrecta: un niño puede haber escrito algo válido que el instructor no previó |
| `ESSAY` | siempre manual, con la rúbrica a la vista del instructor |

Aprobado: `points_awarded × 100 ≥ passing_score × points_total` (aritmética entera, sin redondeos). La nota es
**global**, no por requisito; `score_percent = floor(100 × awarded / total)` es sólo informativa.

Resolución al entregar, para no dar trabajo manual inútil: si lo ya otorgado alcanza el umbral ⇒ `PASSED`; si ni
otorgando todo lo pendiente se alcanza ⇒ `FAILED`; en otro caso ⇒ `PENDING_GRADING`. El instructor del curso (verificado)
o MASTER_GC califican cada respuesta pendiente con 0…`points_possible` y nota opcional; tras cada calificación se
reevalúa y el intento se cierra en cuanto el resultado queda decidido. El director **no** califica exámenes: el examen
es del curso.

**Aprobar completa lo teórico** (misma transacción, inscripción bloqueada `FOR UPDATE`): cada fila de
`requirement_progress` cuyo requisito es `EXAM` en el plan y no está `COMPLETE` pasa a `status = 'COMPLETE'`,
`completed_via = 'EXAM'`, `reviewed_by_id = NULL`, `reviewed_at = now()`; sus posiciones se anotan en
`completed_positions`; se ejecuta la regla 2 de A; si procede, la emisión automática de §5.3.

En modalidad COURSE un requisito `EXAM` **sólo** se completa por examen: `PUT …/requirements/{position}` con
`SUBMITTED` y `POST …/review` con `COMPLETE` responden 409 «Este requisito se completa con el examen del curso». Las
filas que ya estaban `COMPLETE` al unirse se respetan.

**Anular un intento** (`POST /exams/attempts/{id}/void` con motivo; instructor verificado o MASTER_GC; nunca si la
inscripción está `CERTIFIED` — para eso está la anulación del certificado, §5.5): el intento deja de contar — es también
la forma de conceder otra oportunidad o de resolver un fallo técnico, sin más mecanismos. Si estaba `PASSED`, las filas
de `completed_positions` vuelven a `PENDING` (`completed_via = NULL`) y la inscripción se recalcula.

### 4.5 Qué se muestra tras un intento (D5a)

| Situación | El miembro (y su tutor) ven |
|---|---|
| `PENDING_GRADING` | «Tu instructor está revisando tus respuestas»; sin nota |
| `FAILED` con intentos restantes | nota, desglose por requisito, enunciado y su respuesta en las falladas, nota del instructor; **sin** respuesta correcta ni explicación; enlace a las lecciones de esos requisitos |
| `PASSED`, o `FAILED` sin intentos | todo lo anterior + respuesta correcta y explicación |
| `VOIDED` | que fue anulado y el motivo |

El instructor y MASTER_GC ven siempre el intento completo. El director y la jerarquía ven sólo estado y nota (vía
`can_view_enrollment`), nunca el texto de las respuestas.

### 4.6 Integridad del examen, proporcionada para menores

Se hace: sorteo, barajado y calificación en el servidor; las respuestas correctas no salen antes de tiempo; plazo
controlado por el servidor; intentos limitados; un único intento abierto; **promesa** antes de empezar («Haré este
examen por mí mismo»); IP en `audit_log` al empezar y al entregar (ya es la práctica del API); límites de frecuencia
con el `limiter` existente (empezar 10/min, guardar 120/min, 5 códigos de sesión fallidos en 10 min ⇒ 429).

**No se hace, por decisión de diseño:** cámara, micrófono, captura de pantalla, bloqueo del navegador, detección de
cambio de pestaña, huella del dispositivo ni geolocalización. Son invasivos para menores y poco fiables.

**«Proctor ligero»:** el instructor abre una sesión presencial (`POST /courses/{id}/exam-session {minutes}`, 15–240,
120 por defecto) y obtiene un código de 6 caracteres sin ambiguos (sin 0/O/1/I), que dicta en el aula. Vive en
`courses.session_code` + `session_code_expires_at`; abrir otra lo sustituye; `DELETE` la cierra. El intento iniciado con
código vigente queda `proctored = true`. En `IN_PERSON` el código es obligatorio; en `ONLINE`, opcional. El historial de
sesiones queda en `audit_log` (`EXAM_SESSION_OPEN` / `_CLOSE`); no hace falta una tabla.

### 4.7 Accesibilidad (WCAG 2.2 AA)

- **Tiempo:** sin límite por defecto. Con límite, hay tiempo adicional por miembro (+25 / +50 / +100 %,
  `exam_extra_time_percent`) que fija el tutor de un menor, el propio miembro si es adulto o el instructor del curso:
  es una adaptación que el usuario puede activar por sí mismo (WCAG 2.2.1) y no exige ningún canal de mensajes con el
  instructor, que lo ve en su lista de inscritos. Se aplica al calcular `deadline_at` del siguiente intento. Avisos a
  5 y 1 minuto por `aria-live="polite"`; el contador se puede ocultar. El tiempo mostrado viene del servidor.
- Una pregunta por pantalla; controles nativos (`radio`, `textarea`) con `fieldset`/`legend`; todo operable con teclado;
  foco visible; navegador de preguntas con estado en texto («3 · respondida»), no sólo color.
- Resultados con icono **y** texto; contraste AA en claro y oscuro; tipografía ampliable al 200 % sin pérdida;
  `prefers-reduced-motion` respetado; sin arrastrar y soltar.
- Contenido del curso: `alt` obligatorio en imágenes, `title` en vídeos, atributo `lang` cuando el idioma del curso
  difiere del de la interfaz.

### 4.8 API

Añadidos al router de cursos (`/api/v1/courses`), porque son parte de la autoría y la operación del curso:

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `PUT /{id}` (de B) acepta además `exam_passing_score`, `exam_time_limit_minutes`, `max_exam_attempts`, `exam_mode` | autor, `DRAFT` | 422 fuera de rango |
| `PUT /{id}/requirements/{position}/questions` `{draw_count, question_bank: [QuestionIn]}` | autor, `DRAFT` | reemplaza el banco de ese requisito y lo marca `EXAM`; 422 pregunta inválida; 409 si la especialidad lo marca práctico |
| `POST /{id}/import-honor-bank` | autor, `DRAFT` | §4.1; 403 si no es el creador de la especialidad |
| `POST /{id}/exam-session` `{minutes?}` · `DELETE /{id}/exam-session` | autor verificado, curso `PUBLISHED` | §4.6; devuelve `{code, expires_at}` |

`app/routers/exams.py`, prefijo `/api/v1/exams` (todo autenticado); lógica en `app/services/exams.py`:

| Método y ruta | Quién | Qué · errores |
|---|---|---|
| `GET /enrollments/{enrollment_id}` | `can_view_enrollment` | parámetros, intentos usados y restantes, intento abierto, último resultado |
| `POST /enrollments/{enrollment_id}/attempts` | dueño | §4.2 |
| `GET /attempts/{id}` | dueño; tutor con consentimiento, instructor del curso y MASTER_GC (detalle, sólo una vez terminado); resto de `can_view_enrollment` (sólo estado y nota) | en curso, **sólo para el dueño**: papel + respondido + `remaining_seconds`; terminado: §4.5 |
| `PUT /attempts/{id}/answers/{position}` `{response}` | dueño | 409 cerrado o fuera de plazo; 422 respuesta no válida para el tipo |
| `POST /attempts/{id}/submit` | dueño | resultado según §4.4–4.5; idempotente si ya estaba entregado |
| `GET /grading/queue?course_id=` | instructor verificado | intentos `PENDING_GRADING` de sus cursos |
| `POST /attempts/{id}/answers/{position}/grade` `{points_awarded, note?}` | instructor del curso verificado, MASTER_GC | 409 si la respuesta no está pendiente; 422 fuera de rango |
| `POST /attempts/{id}/void` `{reason}` | instructor del curso verificado, MASTER_GC | 422 sin motivo; 409 inscripción certificada |
| `PUT /enrollments/{enrollment_id}/extra-time` `{percent}` | tutor con consentimiento `GRANTED`, el dueño si es adulto, o el instructor del curso verificado | 403 un menor sobre sí mismo; 409 con intento abierto (se aplica desde el siguiente) |

### 4.9 Reglas de integridad de C

1. Un intento abierto por inscripción; los intentos `VOIDED` no cuentan para el máximo.
2. El papel de un intento no cambia después de creado; la calificación usa las preguntas del curso publicado, inmutables.
3. `completed_via = 'EXAM'` sólo lo escribe el servicio de exámenes, y sólo sobre requisitos `EXAM` del plan.
4. Nada se escribe en una inscripción `CERTIFIED` o `WITHDRAWN` (regla 3 de A): ni intentos, ni notas, ni anulaciones.
5. Nadie califica ni anula su propio intento (el instructor no puede estar inscrito en su curso).

## 5. Bloque D — Certificación por curso

### 5.1 La regla de `READY` en COURSE

**Una inscripción COURSE está `READY` ⇔ todas sus filas de `requirement_progress` están `COMPLETE`.** Es la regla 2
de A, sin cambios. Lo que define D es cómo llega cada fila a `COMPLETE`:

| `assessment` en el plan | Se completa cuando… | Quién |
|---|---|---|
| `EXAM` | un intento del curso queda `PASSED` (`completed_via = 'EXAM'`) | el sistema |
| `REVIEW` | dictamen `COMPLETE` | instructor del curso, o director / instructor del club del miembro |
| `EVIDENCE` | dictamen `COMPLETE` con ≥ 1 evidencia `ACTIVE` (regla 1 de A) | los mismos |
| (cualquiera) ya `COMPLETE` al unirse | se respeta | — |

### 5.2 Quién dictamina lo práctico y cómo se resuelven conflictos (D4a)

- Dictaminan, sobre una inscripción COURSE: el **instructor del curso** y, si el miembro pertenece a un club aprobado,
  su **director o el instructor de ese club**. Es `can_review` (§3.7); no hay otra función.
- Una fila `SUBMITTED` la dictamina el primero que llegue; ambos la ven en su cola.
- Un `INCOMPLETE` (siempre con nota) devuelve el requisito al miembro; al reenviarlo, vuelve a poder dictaminar cualquiera.
- Un `COMPLETE` sólo lo **reabre** (dictamen `INCOMPLETE` + nota) quien lo dio, el instructor del curso o MASTER_GC. El
  director no deshace un `COMPLETE` del instructor; si discrepa, escala (Director → Zona → Asociación), que puede anular
  el certificado. Esta restricción es una regla del servicio de dictamen sólo para `mode = 'COURSE'`; en CLUB rige A.
- El instructor del curso puede reabrir también una fila completada por examen o por un dictamen anterior a la
  inscripción en el curso: como firma el certificado, responde de todo lo que certifica. Una fila `EXAM` reabierta sólo
  se vuelve a completar aprobando el examen (si no quedan intentos, el instructor anula uno, §4.4).
- Regla 5 de A intacta: nadie dictamina ni certifica su propia inscripción.

### 5.3 Quién aprueba y emite

```
can_issue(db, actor, enrollment)             # app/rbac.py — se añade la rama COURSE prevista por A
  mode == 'CLUB'   → como en A
  mode == 'COURSE' → MASTER_GC  ∨  (mismas condiciones que la cláusula del instructor en can_review)
```

En COURSE el director **no** emite (así lo fija A). Si miembro y director prefieren la vía del club, el miembro sale del
curso (§3.5) y la inscripción, ya en CLUB, la emite el director sin perder nada.

**Emisión manual** (hay requisitos `REVIEW` o `EVIDENCE`): el instructor ve «Listos para certificar», abre la inscripción
y emite con el mismo `POST /portfolio/enrollments/{id}/certificate` de A (plantilla, fecha, lugar). El servicio rellena
`issued_by_id = instructor`, `issued_role = 'INSTRUCTOR'`, `instructor_name = nombre del instructor` (no editable en
COURSE) y `director_name` = nombre del último `CLUB_DIRECTOR` que dictaminó un requisito de esa inscripción, o NULL.

**Emisión automática** (regla de la visión): se dispara **sólo** cuando un intento pasa a `PASSED` (al entregar o al
terminar de calificarse), eso deja la inscripción `READY` **y ninguna de sus filas tiene `is_practical = true`**. En la
misma petición, dentro de un `SAVEPOINT`: `issue_certificate(...)` con plantilla por defecto del servidor,
`issued_date` = fecha UTC de `finished_at`, `place = NULL`, `instructor_name` = instructor del curso, `director_name = NULL`,
`issued_by_id = instructor`, `issued_role = 'INSTRUCTOR'`; evento `issued` con `{auto: true, attempt_id}`; la inscripción
pasa a `CERTIFIED`. La aprobación del instructor es **previa y general**: al enviar a revisión un curso sin requisitos
`EVIDENCE`, la pantalla le hace aceptar «Los certificados de este curso se emitirán a mi nombre al aprobar el examen», y
zona y Asociación lo aprueban sabiéndolo. Si la emisión falla (plantilla, emisor), el `SAVEPOINT` se revierte, el
resultado del examen **no se pierde**, la inscripción queda `READY` en la cola del instructor y el error va a Sentry.
Cualquier otro camino a `READY` (último dictamen humano, filas completas de antes) es emisión manual.

Emisor del certificado: el mismo que en A (`ISSUER_ORGANIZATION_CODE`). Varios emisores llegarán con `issuer_keys`
(fuera de alcance).

### 5.4 Qué muestra el certificado

- **Impreso / PNG / PDF:** miembro, especialidad, fecha, lugar, folio, QR y firmas. `instructor_name` es la firma del
  instructor (las plantillas actuales ya la pintan) y `director_name` la del director si dictaminó algo. Ambos ya están
  en el hash: no cambia `canonical()` (hallazgo 7).
- **Título del curso:** se obtiene por `certificates.enrollment_id → honor_enrollments.course_id → courses.title`. Se
  muestra en el portafolio y en la verificación; el motor de plantillas lo recibe como variable opcional `course_title`
  (la plantilla que no la declare la ignora). No entra en el hash: es contexto, no el hecho certificado.
- **Verificación pública** (`GET /certificates/verify/{no}`) añade `mode`, `instructor_name` y `course_title`. Ningún
  identificador de usuario ni dato nuevo del menor.
- No se añade `course_id` a `certificates`: la inscripción queda congelada al certificar y ya lo conserva.

### 5.5 Anulación — migración `011_certificate_revocation.sql`

`certificates`: `revoked_at timestamptz NULL`, `revoked_by_id uuid NULL REFERENCES users(id)`, `revocation_reason text NULL`.

`app/services/certificates.py::revoke_certificate(db, certificate, actor, reason)`: `status = 'revoked'` (la verificación
pública ya responde «no válido» para todo lo que no sea `issued`), columnas de anulación, evento `revoked` en
`certificate_events`, `audit_log`. La inscripción pasa de `CERTIFIED` a `WITHDRAWN` (única transición así, §2.2) y
conserva `certificate_id` como historia: queda congelada y el miembro puede volver a empezar la especialidad. No hay
«des-anular».

`can_revoke(db, actor, certificate)`: MASTER_GC, o `ASSOCIATION_REVIEWERS` cuyo alcance contenga el club de la
inscripción (CLUB) o `courses.org_scope_id` (COURSE). El instructor y el director **no** anulan: lo piden a su
Asociación. Endpoint: `POST /api/v1/portfolio/certificates/{certificate_id}/revoke {reason}` (403 fuera de alcance;
409 ya anulado; 422 sin motivo).

Ganchos que D deja listos sin construirlos: todos los certificados de un curso o de un instructor se obtienen con una
consulta (`certificates → honor_enrollments.course_id → courses.instructor_id`); la anulación masiva y las alertas
automáticas de la visión quedan fuera de alcance.

### 5.6 Reglas de integridad de D

1. En COURSE sólo emite el instructor del curso (verificado, curso no retirado por la autoridad) o MASTER_GC.
2. La emisión automática nunca ocurre si alguna fila es práctica, y sólo la dispara un intento `PASSED`.
3. Una inscripción se certifica una vez (índice único de A); un certificado anulado no se reemite: se empieza de nuevo.
4. La emisión automática y el resultado del examen son atómicos por separado: el fallo de la primera no revierte el segundo.

## 6. Privacidad y protección de menores (transversal)

- **Mínimo dato al instructor virtual:** de cada inscrito ve nombre, club y progreso en *su* curso; nunca correo, fecha
  de nacimiento, contacto ni el resto del portafolio (§2.2). Las evidencias las abre con la URL firmada de 5 min de A.
- **Sin canal privado adulto–menor:** no hay mensajes, foro ni comentarios. Los únicos textos del instructor hacia un
  menor son la nota de dictamen (A) y la nota de calificación; ambos los ve también el tutor. El miembro no ve a otros inscritos.
- **Consentimiento:** un menor sólo se une a un curso con tutela `GRANTED`; el tutor ve en sólo lectura curso,
  instructor, intentos y resultados, y es quien activa el tiempo adicional del menor (§4.7).
- **Respuestas de examen** (texto libre de un menor): las ven el miembro, su tutor, el instructor del curso y
  MASTER_GC. La jerarquía y el director sólo ven estado y nota.
- **Contenido:** todo lo que un menor ve en un curso ha pasado por zona y Asociación; vídeo sólo incrustado en modo de
  privacidad; prohibidas fotos de menores reconocibles en el material.
- **Carta de la iglesia:** dato personal con firma ⇒ bucket privado, URL firmada, visible sólo para su dueño y los
  revisores en alcance.
- **Instructor:** adulto, con curso de protección infantil y carta autorizada; pierde toda capacidad en cuanto falla
  una condición. Su nombre es público (firma certificados); nada más de su cuenta lo es.
- **Sin vigilancia** durante el examen (§4.6). La IP queda sólo en `audit_log`, como en el resto del API.
- Conservación: intentos y respuestas viven mientras viva la inscripción; se borran en cascada con ella.

## 7. Auditoría

Cada acción escribe su fila de `audit_log` en la misma transacción (`record_audit`). No se audita el autoguardado de
cada respuesta.

| `entity_type` | `action` |
|---|---|
| `CHURCH_LETTER` | `LETTER_SUBMIT`, `LETTER_VALIDATE`, `LETTER_AUTHORIZE`, `LETTER_REJECT`, `LETTER_REVOKE` |
| `COURSE` | `CREATE`, `UPDATE`, `SUBMIT`, `APPROVE`, `REJECT`, `REQUEST_CHANGES`, `COURSE_VERSION`, `ARCHIVE` (metadatos: `by_authority`, motivo), `COURSE_OPERATION_UPDATE`, `EXAM_SESSION_OPEN`, `EXAM_SESSION_CLOSE` |
| `ENROLLMENT` | `COURSE_JOIN`, `COURSE_LEAVE`, `COURSE_MEMBER_REMOVE`, `EXAM_EXTRA_TIME`, `REQUIREMENT_COMPLETE_EXAM` (metadatos: intento, posiciones) |
| `EXAM_ATTEMPT` | `EXAM_START` (metadatos: `proctored`), `EXAM_SUBMIT` (metadatos: `auto_submitted`, estado, nota), `EXAM_GRADE`, `EXAM_VOID` |
| `CERTIFICATE` | `CERTIFICATE_ISSUE` (de A; metadatos añaden `mode`, `course_id`), `CERTIFICATE_AUTO_ISSUE` (actor = quien provocó la petición; metadatos: `issued_by_id`, `attempt_id`), `CERTIFICATE_REVOKE` |

## 8. Frontend (`conquistadores.app`) — kit `cq-*`, responsive, skeletons reales

`lib/api/courses.ts`, `lib/api/exams.ts`, `lib/api/church-letters.ts` + tipos; todo por el proxy autenticado (añadir
`courses`, `exams` y `church-letters` a `ALLOWED_PREFIXES`). La lista pública de cursos de `/honors/[id]` se pide desde
el servidor de Next, como `catalog-server.ts`. La navegación de 5 elementos **no cambia**: todo entra por Panel, por
`/honors/[id]` y por `/portfolio`. Primer nivel dinámico siempre `[id]` (hallazgo 10).

**Miembro**
- `/honors/[id]`: sección **Cursos con instructor** (`CourseCard`) → hoja con la ficha → **Unirme**. Menor sin tutela
  concedida: explicación y enlace a tutelas. Ya inscrito: chip «En el curso de …» y acceso directo.
- `/courses/[id]`: lecciones (`LessonBlocks`), recursos oficiales de la especialidad, requisitos agrupados por forma de
  evaluación — los `REVIEW` y `EVIDENCE` reutilizan `RequirementProgress`, `EvidenceSheet` y `EvidenceThumb` de A; los
  `EXAM` muestran «Se completa con el examen» —, tarjeta del examen (intentos, umbral, tiempo, estado) y avisos de §3.5
  con sus dos salidas. La última lección abierta se recuerda en `localStorage`.
- `/courses/[id]/exam`: condiciones, promesa, código de sesión si aplica, **Empezar** / **Continuar**.
  `/courses/[id]/exam/[attemptId]`: `ExamQuestion`, `ExamNavigator`, `ExamTimer`, autoguardado con indicador y, al
  terminar, `AttemptResult` (§4.5). Con certificado automático: «¡Especialidad certificada!» con enlace al certificado.
- `/portfolio` (A): la tarjeta de inscripción muestra curso e instructor.

**Instructor**
- Panel → **Enseñar** → `/teach`: `VerificationChecklist` (correo, protección infantil, carta con su estado; subir
  carta), mis cursos por estado, colas **Por revisar**, **Por calificar**, **Listos para certificar**.
- `/teach/courses/new?honor=` y `/teach/courses/[id]`: editor por pestañas — Ficha · Lecciones (`BlockEditor`, reordenar
  con botones subir/bajar) · Requisitos y evaluación (`AssessmentPicker`, con los prácticos bloqueados en `EVIDENCE`) ·
  Banco de preguntas (`QuestionEditor`) · Examen · Enviar (lista de lo que falta, aceptación de emisión automática si
  aplica, historial de revisión). Publicado: sólo lectura + **Nueva versión** + operación (cupo, abrir/cerrar altas).
- `/teach/courses/[id]/members` (progreso, tiempo adicional de cada inscrito, anular intento, expulsar) y
  `/teach/courses/[id]/grading`; `SessionCodeDialog` muestra el código en grande con su cuenta atrás. Dictamen y emisión
  reutilizan las pantallas de A.

**Revisor (zona / Asociación)**
- Panel: `pending-reviews.tsx` se amplía con **Cursos por revisar** y **Cartas por validar**.
- `/review/courses/[id]`: el curso tal como lo verá el miembro + plan + banco con respuestas + avisos (banco corto,
  emisión automática) + decisión con comentario. `/review/letters/[id]`: visor de la carta (URL firmada), datos del
  instructor y decisión. Retirar un curso publicado y revocar una carta, con motivo obligatorio.
- Panel de Asociación: **Anular certificado** desde la ficha de verificación, con motivo.

**Tutor:** portafolio del menor en lectura (A) con curso, instructor, intentos y resultados; selector **Tiempo
adicional en exámenes** (0 / 25 / 50 / 100 %) por inscripción. El miembro adulto tiene el mismo selector en la
pantalla del examen.

Componentes nuevos del kit, con entrada en `/styleguide` y skeleton: `CourseCard`, `LessonBlocks`, `BlockEditor`,
`AssessmentPicker`, `QuestionEditor`, `ExamQuestion`, `ExamNavigator`, `ExamTimer`, `AttemptResult`,
`VerificationChecklist`, `SessionCodeDialog`.

## 9. Pruebas

Backend (pytest de integración, TDD; el reloj se inyecta para probar plazos):
- **Verificación:** transiciones de la carta; nadie valida la suya; alcance con coordinador de zona y sin él; cada
  condición de `instructor_is_verified` por separado; efecto inmediato de `REVOKE` y de `valid_until` vencido.
- **Cursos:** inmutabilidad del contenido publicado; únicos parciales (curso vivo + versión en preparación); plan
  completo y nunca más laxo que la especialidad; validaciones de `submit`; revisión por escalón y alcance; publicar
  versión archiva la anterior en la misma transacción; bloques inválidos (HTML, dominio ajeno, proveedor de vídeo
  desconocido); el historial de una especialidad **no** muestra revisiones de cursos.
- **Inscripción:** `join` idempotente; última plaza con dos peticiones simultáneas; menor sin tutela; conversión de una
  inscripción CLUB conservando lo completo; ajuste de `is_practical`; `leave` y expulsión conservan progreso;
  cada fila de la tabla de §3.5; `can_view_enrollment` (el instructor ve su inscripción y **no** `GET /portfolio/users/{id}`).
- **Exámenes:** endurecimiento de `/honors/{id}/instructor`; el papel nunca contiene `correct_answer` ni `explanation`
  (por esquema y por test sobre el JSON); sorteo respeta `draw_count` y prefiere no vistas; barajado persistido; inicio
  idempotente; reanudar; gracia y caducidad perezosa; máximo de intentos y anulados que no cuentan; normalización de
  respuesta corta; las tres salidas al entregar; calificación manual que cierra el intento; aprobar completa sólo filas
  `EXAM` no completas y dispara la regla 2; anular un `PASSED` revierte exactamente `completed_positions`; `IN_PERSON`
  sin código; límite de códigos fallidos; tiempo adicional; nada se escribe en una inscripción congelada.
- **Certificación:** matriz de `can_review` / `can_issue` en COURSE (instructor del curso, otro instructor, director del
  club del miembro, director ajeno, tutor, admin, MASTER, dueño); reglas de reapertura de D4; requisito `EXAM` no
  dictaminable; emisión automática sólo sin filas prácticas y sólo desde un intento; fallo de la emisión automática no
  pierde el examen; `issued_role`, `instructor_name` y `director_name` correctos; **los certificados existentes siguen
  verificando** (hash intacto); anulación: alcance, `WITHDRAWN`, verificación pública, nueva inscripción posible.
- Auditoría de cada acción de §7. Los tests de A siguen verdes sin modificarse, salvo los de visibilidad de §2.2.

Frontend: `tsc`, `eslint`, `next build`; `scripts/e2e-auth.mjs` ampliado (subir carta → validar → autorizar → crear
curso → revisar → publicar → unirse → examen → certificado automático; y la variante con evidencia y emisión manual);
navegador a 375, 768, 1280 y 1920 px en claro y oscuro; examen completo **sólo con teclado** y prueba con VoiceOver del
temporizador y del resultado.

## 10. Incrementos (en orden de dependencia; cada uno se puede desplegar solo)

Despliegue de cada uno, como en A: migración en Neon **antes** del backend; backend; frontend. Nada destructivo. Lo que
aún no existe no se ve: las secciones nuevas sólo aparecen cuando el API devuelve datos.

| # | Incremento | Bloque | Migración | Entrega utilizable | Depende de |
|---|---|---|---|---|---|
| I1 | Verificación del instructor | B | `009_church_letters.sql` | carta, cola de validación, `instructor_is_verified`, lista de comprobación en `/teach` | bucket privado de A |
| I2 | Autoría, revisión y publicación de cursos | B | `009b_courses.sql` | editor con lecciones y plan (`REVIEW` / `EVIDENCE`), flujo zona → Asociación, versiones, vista pública del curso | I1 |
| I3 | Inscripción a cursos + dictamen y **emisión manual** del instructor | B + D | `009c_course_enrollment.sql` | descubrimiento en `/honors/[id]`, unirse / salir / cupo, colas del instructor, `can_review` y `can_issue` con el instructor, `can_view_enrollment`. **Curso completo sin examen**: lo teórico lo dictamina una persona | A desplegado, I2 |
| I4 | Banco de preguntas y plan `EXAM` | C | `010_exams.sql` | endurecimiento de `/honors/{id}/instructor`, banco por requisito, parámetros de examen, importación del banco propio, revisión con banco y avisos. Los cursos ya publicados siguen igual; los exámenes llegan con una versión nueva del curso | I2 y `007` aplicada |
| I5 | Intentos de examen | C | — (tablas de `010`) | empezar, responder, reanudar, entregar, calificación automática, resultados (D5), tiempo adicional, completar requisitos `EXAM` | I3, I4 |
| I6 | Calificación manual, anulación y sesión presencial | C | — | cola «Por calificar», anular intento, código de sesión. Hasta desplegarlo, el envío a revisión de un curso rechaza bancos con `SHORT_ANSWER` o `ESSAY` y el modo `IN_PERSON`: los exámenes de I5 son sólo de calificación automática y ningún intento queda esperando a nadie | I5 |
| I7 | Emisión automática, curso en la verificación y anulación de certificados | D | `011_certificate_revocation.sql` | certificado al aprobar cuando no hay parte práctica, `course_title` e instructor en la verificación pública, anulación por la Asociación | I5 |

Numeración: `008` queda reservada para otro bloque; aquí va un número por bloque (B = `009`, C = `010`, D = `011`) con
sufijo de letra por incremento, de modo que `012` en adelante queda libre para los bloques siguientes. Todas son
aditivas e idempotentes (`IF NOT EXISTS`), como `007`.

## 11. Fuera de alcance de B, C y D

Marketplace, pagos y comisiones a instructores; funciones sociales (foros, comentarios, mensajería, valoraciones de
cursos, listas de compañeros); SCORM / xAPI / LTI; alojamiento de vídeo propio; seguimiento de lección vista y
certificados de asistencia; lista de espera y aprobación manual de altas; cursos en varios idiomas en una sola ficha
(se crea un curso por idioma); traspaso de un curso a otro instructor; multirrol (D6); el curso de protección infantil
como contenido (sólo se comprueba su marca); preguntas con imagen, de emparejar u ordenar, y respuesta múltiple;
umbral por requisito; espera obligatoria entre intentos; vigilancia de examen de cualquier tipo; alertas automáticas
de fraude y anulación masiva; varios emisores y firma criptográfica (`issuer_keys`); correos de aviso (unirse,
resultado, certificado): llegan con el bloque E, como los de A; borrado de datos a petición (política transversal
pendiente).

## 12. Autorrevisión

- **Marcadores vacíos:** ninguno; todas las tablas, límites, códigos de error y constantes tienen valor.
- **Contradicciones con A:** A no cambia de esquema ni de contrato. `READY` sigue siendo la regla 2; `can_review` y
  `can_issue` ganan exactamente las cláusulas que A anunció; `completed_via = 'EXAM'` e `issued_role = 'INSTRUCTOR'`
  se usan como A los definió. Los tres contactos con código de A (§2.2) son: una función de lectura más estrecha para el
  instructor virtual, campos nuevos en una respuesta y una transición que sólo ejecuta la anulación. La restricción de
  reapertura de D4 aplica sólo a COURSE; en CLUB rige la regla 4 de A tal cual.
- **Ambigüedad resuelta:** «evidencia» en un examen = requisito `EVIDENCE`, no tipo de pregunta (hallazgo 6); «instructor
  verificado» = `instructor_is_verified`, no `verification_status` (hallazgo 1); «sin parte física» = ninguna fila con
  `is_practical = true` según el plan **revisado** del curso, no según el valor por defecto del catálogo (hallazgo 3);
  el reloj del examen no se detiene al reanudar; la fecha de la emisión automática es UTC.
- **Alcance:** siete tablas nuevas, cada una con un propósito (`church_letters`, `courses`, `course_lessons`,
  `course_requirements`, `course_questions`, `exam_attempts`, `exam_answers`). Se reutilizan `honor_reviews`, el flujo y
  los revisores de especialidades, `POST /media/upload`, el almacenamiento privado, el servicio de inscripción, las
  pantallas de dictamen y emisión y `issue_certificate` de A, `club_scope_paths` y `record_audit`. Se descartaron por
  YAGNI: tabla de bloques, tabla de sesiones presenciales, tabla de revisiones de curso, `course_id` en `certificates`,
  seguimiento de lecciones y concesión de intentos extra (lo cubre anular un intento).
- **Riesgo a vigilar:** I3 permite cursos sin examen en los que todo lo teórico se dictamina a mano; es coherente con A,
  pero conviene que el responsable confirme que quiere publicar I3 antes de I5 o esperar a tener exámenes.
