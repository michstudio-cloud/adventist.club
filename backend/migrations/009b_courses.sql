-- 009b_courses.sql
-- Bloque B · I2 — El curso del instructor virtual: la oferta que un instructor verificado
-- hace de UNA versión publicada de una especialidad en UN idioma, con sus lecciones y su
-- plan de evaluación por requisito.
-- Spec: docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md §3.2
--
-- Añade tres tablas (`courses`, `course_lessons`, `course_requirements`) y UNA columna
-- nullable a `honor_reviews` para que el historial de revisión del curso reutilice esa
-- tabla en vez de duplicarla. Aditiva e idempotente: nada se borra, renombra ni reescribe;
-- todas las filas actuales de `honor_reviews` quedan con `course_id` NULL, que es lo que
-- significa «revisión de la especialidad».
-- Aplicar ANTES de desplegar el código que la mapea, y DESPUÉS de 009_church_letters.sql.

-- ---------------------------------------------------------------------------
-- La oferta. `honor_id` fija la versión publicada sobre la que se construyó y
-- nunca cambia; `org_scope_id` es la organización del instructor al crearla y
-- decide qué revisores la ven (igual que honors.org_scope_id).
-- El contenido (ficha, lecciones, plan) es inmutable al publicar: cambiarlo
-- exige versión nueva y nueva revisión. `enrollment_open` y `capacity` son
-- operativos y sí se mueven sobre un curso publicado.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  honor_id uuid NOT NULL REFERENCES honors(id),
  instructor_id uuid NOT NULL REFERENCES users(id),
  org_scope_id uuid NOT NULL REFERENCES organizations(id),
  locale varchar(16) NOT NULL DEFAULT 'es',
  title varchar(180) NOT NULL,
  summary varchar(600),
  cover_url text,
  status varchar(30) NOT NULL DEFAULT 'DRAFT'
    CHECK (status IN ('DRAFT','ZONE_REVIEW','ASSOCIATION_REVIEW','PUBLISHED','ARCHIVED')),
  -- true sólo cuando lo retira un revisor, no el instructor: el curso se cierra para todos.
  archived_by_authority boolean NOT NULL DEFAULT false,
  archive_reason text CHECK (char_length(archive_reason) <= 2000),
  version integer NOT NULL DEFAULT 1,
  previous_version_id uuid REFERENCES courses(id) ON DELETE SET NULL,
  changes_description text,
  approved_zone_org_id uuid REFERENCES organizations(id),
  approved_association_org_id uuid REFERENCES organizations(id),
  enrollment_open boolean NOT NULL DEFAULT true,
  capacity integer CHECK (capacity > 0),
  published_at timestamptz,
  archived_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Un curso vivo y, a lo sumo, una versión nueva en preparación, por instructor,
-- especialidad e idioma.
CREATE UNIQUE INDEX IF NOT EXISTS courses_instructor_honor_locale_published_key
  ON courses (instructor_id, honor_id, locale) WHERE status = 'PUBLISHED';
CREATE UNIQUE INDEX IF NOT EXISTS courses_instructor_honor_locale_draft_key
  ON courses (instructor_id, honor_id, locale)
  WHERE status IN ('DRAFT','ZONE_REVIEW','ASSOCIATION_REVIEW');

CREATE INDEX IF NOT EXISTS ix_courses_honor_status ON courses (honor_id, status);
CREATE INDEX IF NOT EXISTS ix_courses_instructor_status ON courses (instructor_id, status);
CREATE INDEX IF NOT EXISTS ix_courses_org_scope_status ON courses (org_scope_id, status);

-- ---------------------------------------------------------------------------
-- Las lecciones, con sus bloques de contenido en JSONB: siempre se leen y se
-- guardan con su lección, no se consultan sueltos y no tienen ciclo de vida
-- propio (por eso no hay tabla de bloques). Límites en el servicio: 30 lecciones
-- por curso, 40 bloques por lección, 200 KB por lección.
-- `position` no es único: reordenar no debe pelearse con un índice, igual que en
-- honor_requirements.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS course_lessons (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  position integer NOT NULL,
  title varchar(180) NOT NULL,
  -- Informativo: pinta «Lección 2 · requisitos 3 y 4».
  requirement_positions integer[] NOT NULL DEFAULT '{}',
  blocks jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_course_lessons_course ON course_lessons (course_id, position);

-- ---------------------------------------------------------------------------
-- Cómo evalúa ESTE curso cada requisito de la especialidad. Se crea una fila por
-- cada posición que la inscripción de A crearía en `courses.locale`.
-- El curso puede ser más estricto que la especialidad (un requisito teórico puede
-- pedir evidencia), nunca más laxo: si la especialidad lo marca práctico, el plan
-- debe declararlo EVIDENCE (regla del servicio, con tests).
-- `EXAM` y `draw_count` quedan admitidos por el esquema desde ya —el plan lo
-- revisan zona y Asociación junto con el contenido— pero el API sólo los acepta
-- con el banco de preguntas del incremento I4.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS course_requirements (
  course_id uuid NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  requirement_position integer NOT NULL,
  assessment varchar(10) NOT NULL CHECK (assessment IN ('EXAM','REVIEW','EVIDENCE')),
  -- Preguntas que se sortean por intento; sólo un requisito EXAM tiene banco.
  draw_count smallint NOT NULL DEFAULT 0
    CHECK ((assessment = 'EXAM') = (draw_count > 0)),
  guidance text CHECK (char_length(guidance) <= 2000),
  PRIMARY KEY (course_id, requirement_position)
);

-- ---------------------------------------------------------------------------
-- El historial de revisión del curso reutiliza `honor_reviews` (misma forma:
-- revisor, acción, comentario). Cada fila de curso lleva honor_id = course.honor_id
-- y course_id; las de la especialidad siguen con course_id NULL.
-- ---------------------------------------------------------------------------
ALTER TABLE honor_reviews ADD COLUMN IF NOT EXISTS course_id uuid REFERENCES courses(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_honor_reviews_course
  ON honor_reviews (course_id, reviewed_at) WHERE course_id IS NOT NULL;
