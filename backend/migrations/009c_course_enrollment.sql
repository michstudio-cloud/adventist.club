-- 009c_course_enrollment.sql
-- Bloque B · I3 — Inscripción a un curso: la modalidad COURSE que el bloque A dejó
-- anunciada (`honor_enrollments.mode = 'COURSE'` + `course_id`).
-- Spec: docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md §3.2 y §3.6
--
-- Añade DOS columnas nullable a `honor_enrollments`, un CHECK que ata la modalidad al
-- curso y un índice parcial para el cupo y las colas del instructor. No crea tablas, no
-- borra, no renombra y no reescribe ninguna fila: todas las inscripciones existentes son
-- `mode = 'CLUB'` con `course_id` NULL, que es justo lo que el CHECK exige.
-- Aplicar ANTES de desplegar el código que la mapea, y DESPUÉS de 009b_courses.sql.

-- El curso en el que se cursa la especialidad. Sin ON DELETE: un curso no se borra
-- nunca (se archiva), y si alguien lo borrara a mano preferimos que la base lo impida
-- antes que dejar inscripciones huérfanas en modalidad COURSE.
ALTER TABLE honor_enrollments
  ADD COLUMN IF NOT EXISTS course_id uuid REFERENCES courses(id);
ALTER TABLE honor_enrollments
  ADD COLUMN IF NOT EXISTS course_joined_at timestamptz;

-- Por qué el instructor expulsó al miembro del curso (§3.6: «el miembro ve el motivo»).
-- Se escribe al expulsar y se limpia al unirse de nuevo; salir por voluntad propia lo deja NULL.
ALTER TABLE honor_enrollments
  ADD COLUMN IF NOT EXISTS course_removed_reason text
  CHECK (char_length(course_removed_reason) <= 2000);

-- Modalidad y curso son la misma verdad contada dos veces: o las dos o ninguna.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'honor_enrollments_mode_course_check'
  ) THEN
    ALTER TABLE honor_enrollments
      ADD CONSTRAINT honor_enrollments_mode_course_check
      CHECK ((mode = 'COURSE') = (course_id IS NOT NULL));
  END IF;
END
$$;

-- Cupo del curso (inscripciones IN_PROGRESS o READY) y cola del instructor.
CREATE INDEX IF NOT EXISTS ix_honor_enrollments_course_status
  ON honor_enrollments (course_id, status) WHERE course_id IS NOT NULL;
