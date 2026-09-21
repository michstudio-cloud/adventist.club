# Estado de los bloques B y C — Cursos del instructor virtual y exámenes (I1–I5)

Nota de trabajo del bloque B, aparte de `docs/ESTADO.md` para no chocar con la rama del bloque E.
Spec: `docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md` (§3 y §10).

## Qué existe ya en el backend

### I1 — Verificación del instructor (`migrations/009_church_letters.sql`)
- Tabla `church_letters`: una sola tabla de cartas para toda la plataforma (decisión D1), genérica
  por `organization_id` y con el doble escalón **zona → Asociación**. El bloque E la reutiliza
  ampliando `role_requested` (hoy `LETTER_ROLES = ("INSTRUCTOR",)` en `app/services/church_letters.py`);
  no hace falta migrar de nuevo.
- El documento va al **bucket privado** de A (`letters/<user_id>/<letter_id>.<ext>`), con el mismo
  apretón de manos que una evidencia: `POST` devuelve una URL PUT firmada de 10 min, el navegador
  sube el archivo y `POST /{id}/complete` comprueba el objeto (existe, tamaño y tipo declarados).
  Tipos: PDF, JPEG, PNG y WebP; máximo **10 MB**.
- Puerta única `app/rbac.py::instructor_is_verified(db, user)` — lo único que el resto del bloque
  consulta: rol `INSTRUCTOR`, cuenta `ACTIVE`, mayor de edad, `verification_status = 'VERIFIED'`
  (que significa **sólo** «correo verificado»), `child_protection_completed` y una carta
  `AUTHORIZED` vigente (`valid_until` NULL o futura).
- API `/api/v1/church-letters`: `GET /me` (lista de comprobación), `POST`, `POST /{id}/complete`,
  `GET /queue?status=`, `GET /{id}/url` (5 min, `Cache-Control: no-store`), `POST /{id}/review`
  (`VALIDATE | AUTHORIZE | REJECT | REVOKE`). Nadie valida su propia carta; la carta sólo la leen su
  dueño y los revisores en alcance; cada decisión escribe `audit_log` en la misma transacción.

### I2 — Cursos: autoría, revisión y publicación (`migrations/009b_courses.sql`)
- Tablas `courses`, `course_lessons` (bloques en JSONB) y `course_requirements` (plan de evaluación),
  más la columna `honor_reviews.course_id`: el historial de revisión del curso **reutiliza
  `honor_reviews`** y `GET /honors/{id}` filtra `course_id IS NULL` para no mezclarlos.
- Bloques de contenido validados en el servidor: `text` (Markdown sin HTML), `image` (con `alt`
  obligatorio), `pdf` y `video` (sólo YouTube o Vimeo; se guarda proveedor + id, nunca la URL).
  Imágenes y PDF sólo desde `media.adventist.club` (carpeta nueva `courses` en `ALLOWED_FOLDERS`).
  Límites: 30 lecciones por curso, 40 bloques por lección, 200 KB por lección.
- El plan se precarga de la especialidad al crear el curso y **nunca es más laxo**: un requisito
  práctico queda en `EVIDENCE`. Hoy el API sólo acepta `REVIEW` y `EVIDENCE`; el esquema ya admite
  `EXAM` + `draw_count` porque el banco de preguntas llega en I4.
- Flujo y revisores de las especialidades (`app/workflow.py`, compartido): `DRAFT → ZONE_REVIEW →
  ASSOCIATION_REVIEW → PUBLISHED → ARCHIVED`. Publicar una versión nueva archiva la anterior en la
  misma transacción. El contenido de un curso publicado es inmutable; `enrollment_open` y `capacity`
  siguen editables.
- API `/api/v1/courses`: `GET /` (escaparate público), `GET /my/created`, `GET /pending/reviews`,
  `POST /`, `GET /{id}`, `GET /{id}/instructor`, `PUT /{id}`, lecciones (`POST`, `PUT`, `DELETE`,
  `PUT /lessons/order`), `PUT /{id}/plan`, `POST /{id}/submit`, `POST /{id}/review`,
  `POST /{id}/version`, `DELETE /{id}` y `PATCH /{id}/operation`.

### I3 — Inscripción, dictamen del instructor y emisión manual (`migrations/009c_course_enrollment.sql`)
- `honor_enrollments` gana `course_id`, `course_joined_at` y `course_removed_reason`, el CHECK
  `((mode = 'COURSE') = (course_id IS NOT NULL))` y el índice `(course_id, status)`. Aditiva: todas las
  filas actuales son `CLUB` con `course_id` NULL y ya cumplen el CHECK.
- Unirse (`POST /courses/{id}/join`) **no crea una inscripción nueva**: usa la de A (o la crea con su
  propio servicio) y la pasa a `mode = 'COURSE'`. El plan del curso se aplica sólo a las filas que no
  están `COMPLETE` (`is_practical = (assessment = 'EVIDENCE')`); lo ya dictaminado no se toca. Cupo
  comprobado con el curso bloqueado (`FOR UPDATE`), sin lista de espera. Idempotente.
- Salir (`POST /courses/{id}/leave`) y expulsar (`DELETE /courses/{id}/members/{enrollment_id}`,
  con motivo obligatorio) devuelven la inscripción a `CLUB` **sin perder nada**; `is_practical` no se
  relaja. El motivo de la expulsión queda en `course_removed_reason` y lo lee el miembro.
- `app/rbac.py`: `is_course_instructor(db, actor, enrollment)` es la única cláusula nueva, y la usan
  `can_review` y `can_issue`. Exige curso `PUBLISHED`/`ARCHIVED` **no retirado por la autoridad**,
  que el actor sea el autor, que no sea su propia inscripción y `instructor_is_verified` **en ese
  momento**. `can_view_enrollment` (nueva) es lo que usan `GET /portfolio/enrollments/{id}` y
  `GET /portfolio/evidences/{id}/url`: el instructor del curso ve **esa** inscripción y nunca
  `GET /portfolio/users/{id}`.
- D4a en `services/portfolio.py::review_requirement`: en `COURSE`, un `COMPLETE` sólo lo reabre quien
  lo dio, el instructor del curso o MASTER_GC. En `CLUB` rige la regla 4 de A sin cambios.
- `GET /portfolio/review/queue` (SUBMITTED y READY) suma las inscripciones de los cursos del
  instructor verificado; un instructor que además es personal de un club ve las dos cosas.
- Emisión manual: el mismo `POST /portfolio/enrollments/{id}/certificate`. En `COURSE` el servicio
  fija `instructor_name` = nombre del instructor del curso (el formulario no lo cambia) y
  `director_name` = último `CLUB_DIRECTOR` que dictaminó un requisito, o NULL. `issued_role` sale
  de `issue_certificate` como `INSTRUCTOR`. `canonical()` **no se toca**.
- API nueva en `/api/v1/courses`: `GET /my/joined`, `POST /{id}/join`, `POST /{id}/leave`,
  `GET /{id}/members` (sin correo ni fecha de nacimiento) y `DELETE /{id}/members/{enrollment_id}`.
  `GET /{id}` entrega los bloques de las lecciones al inscrito, y el inscrito sigue viendo su curso
  cuando se archiva; si lo retira la autoridad, el contenido se cierra y el instructor pierde
  `can_review` / `can_issue` sobre él.

### I4 — Banco de preguntas del curso y plan `EXAM` (`migrations/010_exams.sql`)
- **Paso previo de seguridad (hallazgo 2):** `GET /honors/{id}/instructor` ya **no** entrega el banco
  con respuestas a cualquier `INSTRUCTOR`, tampoco de una especialidad publicada. Lo leen el creador,
  los revisores en alcance y MASTER_GC; el resto recibe 403 si la especialidad es pública y 404 si no.
- `010_exams.sql` añade `course_questions` (banco **por curso y requisito**, con FK compuesta a
  `course_requirements` y `ON DELETE CASCADE`), `exam_attempts`, `exam_answers`, seis columnas de
  examen en `courses` (`exam_passing_score` 80–100, `exam_time_limit_minutes` 5–180,
  `max_exam_attempts` 1–10, `exam_mode`, `session_code`, `session_code_expires_at`) y
  `honor_enrollments.exam_extra_time_percent` (0/25/50/100). Aditiva e idempotente.
- Un requisito llega a `EXAM` **por su banco**: `PUT /courses/{id}/requirements/{position}/questions`
  `{draw_count, question_bank}` reemplaza el banco y marca `EXAM`; 409 si la especialidad marca ese
  requisito práctico. `PUT /{id}/plan` actualiza las filas **en su sitio** (antes borraba y reinsertaba,
  lo que se habría llevado el banco por cascada) y 422 si declara `EXAM` sin banco; sacar un requisito
  de `EXAM` borra su banco y deja `draw_count = 0`.
- Validación de pregunta en el servidor: opción múltiple de 2 a 6 opciones distintas y respuesta
  correcta entre ellas; verdadero/falso sin opciones y con `true`/`false`; respuesta corta de 1 a 10
  alternativas separadas por `|` (≤ 120 c/u); `points` de 1 a 10; enunciado ≤ 1000.
- `POST /courses/{id}/import-honor-bank` copia `honor_questions` casando por posición, sólo si la
  especialidad la creó el propio instructor (403 si no); nunca importa un requisito práctico.
- `POST /{id}/submit` rechaza: banco más corto que el sorteo, más de 60 preguntas sorteadas en total,
  preguntas `SHORT_ANSWER` o `ESSAY` y `exam_mode = 'IN_PERSON'` — las dos últimas **hasta I6**, para
  que ningún intento quede esperando a un calificador que todavía no existe.
- Avisos no bloqueantes en `GET /{id}/instructor` (`warnings`): banco menor que 2 × `draw_count` y
  «este curso emitirá el certificado automáticamente al aprobar» cuando no hay requisitos con evidencia.
- Las respuestas correctas viven sólo en `CourseStaffDetail.question_banks`. `GET /courses/{id}` y el
  escaparate no declaran ese campo: no pueden filtrarlo por construcción.

### I5 — Intentos de examen (sin migración: usa las tablas de `010_exams.sql`)
- `app/services/exams.py` y `app/routers/exams.py` (prefijo `/api/v1/exams`, todo autenticado):
  `GET /enrollments/{id}` (tarjeta del examen), `POST /enrollments/{id}/attempts` (10/min),
  `PUT /enrollments/{id}/extra-time`, `GET /attempts/{id}`, `PUT /attempts/{id}/answers/{position}`
  (120/min) y `POST /attempts/{id}/submit`.
- **Todo lo que decide algo pasa en el servidor.** El sorteo usa `secrets.SystemRandom`: por cada
  requisito `EXAM` pendiente saca `draw_count` preguntas de su banco, prefiriendo las que el miembro
  no vio en intentos anteriores, baraja preguntas y opciones y **persiste** la permutación en
  `exam_answers.option_order`. El cliente responde con el índice de la opción **que ve**; el servidor
  lo traduce al índice original, que es lo que se guarda. El reloj es `deadline_at`, nunca una hora
  del navegador.
- **Las respuestas correctas no pueden filtrarse por construcción:** `PaperQuestionOut` no declara
  `correct_answer` ni `explanation`, y el resultado sin soluciones (`AnswerFeedbackOut`) tampoco.
  Sólo `AnswerSolutionOut` las lleva, y el servicio la construye únicamente cuando D5 lo permite
  (aprobado, o reprobado sin intentos restantes).
- **Caducidad perezosa** (hallazgo 8): `finalize_if_expired` corre en cada lectura y escritura del
  intento, en la tarjeta del examen y al empezar otro. No hay cron ni colas. Pasado `deadline_at`
  + 30 s de gracia, guardar responde 409 y el intento se cierra con `auto_submitted = true`.
  Sin límite de tiempo el plazo es de 72 h.
- **Calificación automática:** opción múltiple y verdadero/falso siempre; respuesta corta contra las
  alternativas aceptadas tras normalizar (minúsculas, sin acentos ni puntuación, espacios simples) y,
  si no coincide, **queda pendiente, no incorrecta**. Aprobado es `otorgado × 100 ≥ umbral × total`,
  con aritmética entera. Al entregar se resuelve en `PASSED`, `FAILED` o `PENDING_GRADING`.
- **Aprobar completa lo teórico** en la misma transacción, con la inscripción bloqueada: cada fila
  `EXAM` del plan que no estaba `COMPLETE` pasa a `COMPLETE` con `completed_via = 'EXAM'` y
  `reviewed_by_id = NULL`; sus posiciones quedan en `exam_attempts.completed_positions` (para que I6
  pueda revertir exactamente eso al anular) y se recalcula la regla 2 de A.
- En modalidad COURSE un requisito `EXAM` **sólo** se completa con el examen: `PUT .../requirements/
  {position}` con `SUBMITTED` y `POST .../review` con `COMPLETE` responden 409. Reabrirlo con
  `INCOMPLETE` sigue siendo cosa del instructor del curso (D4a), y las filas que ya estaban
  `COMPLETE` al unirse se respetan.
- **Quién ve qué (§4.5 y §6):** el dueño ve el papel mientras rinde y el resultado después; el
  instructor del curso, el tutor con consentimiento y MASTER_GC ven el intento entero **una vez
  terminado**; el director y la jerarquía, sólo estado y nota. De un intento en curso nadie más que
  su dueño ve las preguntas.
- **Accesibilidad:** `exam_extra_time_percent` (0/25/50/100) lo fija el miembro adulto, el tutor con
  consentimiento o el instructor del curso; 403 si un menor intenta dárselo a sí mismo y 409 con un
  intento abierto (se aplica desde el siguiente).

## Ajustes nuevos
Ninguno. I1 reutiliza `R2_PRIVATE_BUCKET_NAME` (el bucket privado del bloque A); sin ese ajuste, los
endpoints de la carta responden 503 y todo lo demás sigue funcionando. I2 no añade ajustes: el
material de las lecciones usa el bucket público que ya existe.

## Orden de despliegue
1. Aplicar en Neon, **antes** de subir el backend y en este orden:
   `backend/migrations/009_church_letters.sql`, `backend/migrations/009b_courses.sql`,
   `backend/migrations/009c_course_enrollment.sql`, `backend/migrations/010_exams.sql`,
   `backend/migrations/010b_exam_session_code.sql` y
   `backend/migrations/011_certificate_revocation.sql`.
   Todas son aditivas e idempotentes (`IF NOT EXISTS`, columnas nullable); no borran ni reescriben
   nada, y se pueden aplicar dos veces sin efecto.
2. Backend (Render).
3. Frontend: hasta que exista, nada de esto se ve; el API es aditivo y ningún contrato anterior cambia.

## I6 — Calificación manual, anulación y sesión presencial (hecho)
- **Cola de calificación:** `GET /api/v1/exams/grading/queue?course_id=&limit=&offset=` devuelve los
  intentos `PENDING_GRADING` de **mis** cursos; 403 para quien no enseña nada (misma regla que la cola
  de revisión de A). De cada inscrito viaja el nombre y nada más.
- **Calificar:** `POST /api/v1/exams/attempts/{id}/answers/{position}/grade {points_awarded, note?}`.
  0…`points_possible` (422 fuera de rango), 409 si la respuesta ya está calificada o el intento no
  está `PENDING_GRADING`, 404 si esa posición no es del intento. `is_correct` significa la nota
  completa: con crédito parcial la respuesta no cuenta como correcta. Tras cada calificación se
  reevalúa el intento (`_settle`) y se cierra en cuanto el resultado queda decidido; al aprobar
  completa los requisitos `EXAM` igual que la calificación automática.
- **Anular:** `POST /api/v1/exams/attempts/{id}/void {reason}`. Si el intento estaba `PASSED` se
  revierten **exactamente** las posiciones de `completed_positions` que siguen `COMPLETE` con
  `completed_via = 'EXAM'` (una fila que el instructor ya dictaminó a mano se respeta), vuelven a
  `PENDING` y se recalcula la regla 2 de A. 422 sin motivo, 409 si ya está anulado y 409 si la
  inscripción está congelada: un certificado se anula con `POST /portfolio/certificates/{id}/revoke`.
  Los intentos `VOIDED` no cuentan para el máximo, así que anular es también la forma de conceder
  otra oportunidad.
- **Sesión presencial:** `POST /api/v1/courses/{id}/exam-session {minutes?}` (15–240, 120 por defecto)
  devuelve `{code, expires_at}` **una sola vez**; `DELETE` la cierra. Código de 6 caracteres sin
  ambiguos (sin 0/O/1/I) sobre el alfabeto `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`.
- En `IN_PERSON` el código es obligatorio para empezar un intento (403 sin él o con uno vencido); en
  `ONLINE` es opcional y sólo decide `proctored`. Cinco códigos incorrectos en diez minutos y el
  miembro recibe 429.
- **Quién:** `rbac.can_grade_attempt` — el instructor de **ese** curso con su carta autorizada, o
  MASTER_GC. Nunca el dueño del intento, nunca otro instructor, nunca el director del club (el examen
  es del curso). La sesión la abre el autor verificado de un curso publicado.
- **D5 intacta:** mientras el intento está `PENDING_GRADING` el miembro no ve nota ni desglose.
- Se retiraron las dos puertas temporales del envío a revisión: un curso ya puede publicarse con
  preguntas `SHORT_ANSWER` o `ESSAY` y en modo `IN_PERSON`.

## I7 — Emisión automática, verificación pública y anulación (hecho)
- **Emisión automática** (`app/services/auto_certificate.py`): se dispara **sólo** desde un intento
  que pasa a `PASSED` —al entregar o al terminar de calificarse— y sólo si la inscripción queda
  `READY`, **ninguna** de sus filas de `requirement_progress` es práctica, no lleva ya certificado,
  el curso sigue vivo (no retirado por la autoridad) y la carta del instructor está autorizada en ese
  momento. Firma el instructor (`issued_role = 'INSTRUCTOR'`), fecha UTC de `finished_at`, sin lugar
  y sin línea de director; evento `issued` con `{auto: true, attempt_id}` y auditoría
  `CERTIFICATE_AUTO_ISSUE`. La inscripción pasa a `CERTIFIED`.
- Todo ello dentro de un **SAVEPOINT**: si la emisión falla, se revierte sólo el certificado, el
  aprobado y los requisitos completados sobreviven, la inscripción espera en «listos para certificar»
  y el error va al log (y a Sentry). Hay una prueba que rompe la emisión a propósito.
- **E9:** el miembro —y los tutores de un menor— reciben el aviso de certificado también cuando la
  emisión fue automática.
- **Verificación pública:** `GET /api/v1/certificates/verify/{no}` añade `mode`, `course_title`,
  `instructor_name` y `revoked_at`. El título del curso se obtiene **por relación** (certificado →
  inscripción → curso) y queda **fuera del hash**: `canonical()` no se toca y los certificados ya
  emitidos siguen verificando.
- **Anulación:** `POST /api/v1/portfolio/certificates/{certificate_id}/revoke {reason}`. La decide
  `rbac.can_revoke`: MASTER_GC, o un revisor de Asociación cuyo alcance cubra el club de la
  inscripción (CLUB) o el `org_scope_id` del curso (COURSE). El instructor que la firmó, el director
  y **el propio titular** no anulan. Motivo obligatorio (422), 409 si ya está anulada, 403 fuera de
  alcance, 404 si no existe.
- Anular **nunca es borrar**: la fila conserva folio, hash, firmas y su historia; cambia `status` a
  `revoked`, se escriben `revoked_at`, `revoked_by_id` y `revocation_reason`, se añade el evento
  `revoked` y la inscripción pasa `CERTIFIED → WITHDRAWN` (la única transición así de la plataforma)
  conservando `certificate_id`. El miembro puede empezar de nuevo la especialidad y el certificado
  anulado no se reemite en silencio. No hay «des-anular».
- La verificación pública de un certificado anulado dice «revocado» con su fecha, **sin el motivo** y
  sin ningún dato nuevo de la persona. Como las tres columnas quedan fuera de `canonical()`, verifica
  como «revocado» y nunca como «modificado».
- **Correo:** al titular y a los tutores de un menor por el mecanismo de E9
  (`notification_log`, kind `CERTIFICATE_REVOKED`). El correo dice qué certificado y que fue anulado;
  **no** lleva el motivo, ni quién decidió, ni el curso: el motivo se lee en el portafolio, detrás de
  una sesión.

## Hueco del API de cartas (hecho, con I7)
La UI podía validar, autorizar y rechazar, pero nunca llegar a `REVOKE`: nada devolvía una carta ya
`AUTHORIZED`. Se añaden, sin tocar quién puede ver qué:
- `GET /api/v1/church-letters/{id}` — dueño o revisor en alcance; 404 para el resto (confirmar que el
  id existe ya diría que alguien presentó una carta). Devuelve los mismos metadatos que la cola, con
  quién la presenta y su organización; **nunca** la clave de almacenamiento ni el documento, que
  sigue necesitando la URL firmada de `GET /{id}/url`.
- `GET /api/v1/church-letters/queue?status=` acepta además `AUTHORIZED`, `REJECTED` y `REVOKED`,
  paginado con `limit`/`offset`. Amplía lo que se **lista**, nunca quién puede verlo: un revisor que
  podía decidir sobre una carta la sigue leyendo después. El acto de revocar sigue siendo sólo de un
  revisor de Asociación.

## Lo que falta
- **Frontend.** Todo lo de §8 de la spec para I6 e I7: cola «Por calificar», `SessionCodeDialog`,
  anular intento desde la ficha del inscrito, «¡Especialidad certificada!» tras el examen, el curso y
  el instructor en la página pública de verificación y «Anular certificado» en el panel de Asociación.
- **Anulación masiva y alertas automáticas de fraude** (§5.5): quedan fuera de alcance; el gancho
  existe (`certificates → honor_enrollments.course_id → courses.instructor_id`).
- **Correos de «examen calificado»**: no existen. E9 avisa de `INCOMPLETE`, `READY` y `CERTIFIED`;
  un intento que pasa a `PASSED` o `FAILED` no manda nada, y un `PENDING_GRADING` que se resuelve
  tampoco. Si el responsable lo quiere, es un `kind` nuevo en `services/notifications.py`.
