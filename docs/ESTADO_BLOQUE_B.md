# Estado del bloque B — Cursos del instructor virtual (I1 e I2)

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

## Ajustes nuevos
Ninguno. I1 reutiliza `R2_PRIVATE_BUCKET_NAME` (el bucket privado del bloque A); sin ese ajuste, los
endpoints de la carta responden 503 y todo lo demás sigue funcionando. I2 no añade ajustes: el
material de las lecciones usa el bucket público que ya existe.

## Orden de despliegue
1. Aplicar en Neon, **antes** de subir el backend y en este orden:
   `backend/migrations/009_church_letters.sql` y después `backend/migrations/009b_courses.sql`.
   Las dos son aditivas e idempotentes (`IF NOT EXISTS`, columnas nullable); no borran ni reescriben
   nada, y se pueden aplicar dos veces sin efecto.
2. Backend (Render).
3. Frontend: hasta que exista, nada de esto se ve; el API es aditivo y ningún contrato anterior cambia.

## Lo que falta (I3–I7)
`009c_course_enrollment.sql` (inscripción a cursos: `honor_enrollments.course_id`, unirse, salir,
cupo, `can_review` / `can_issue` con el instructor y `can_view_enrollment`), `010_exams.sql` (banco de
preguntas, intentos y calificación) y `011_certificate_revocation.sql` (emisión automática y anulación).
Las costuras que I1–I2 dejan preparadas están anotadas en el código con el incremento al que pertenecen
(`app/services/courses.py::_enrolled_count`, la visibilidad de los bloques para el inscrito en
`_detail` y `get_detail`).
