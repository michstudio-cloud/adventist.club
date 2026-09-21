-- 010_exams.sql
-- Bloque C · I4 e I5 — El examen del curso: el banco de preguntas del instructor, los
-- parámetros del examen, el intento y su «papel» de respuestas.
-- Spec: docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md §4.1
--
-- Añade tres tablas (`course_questions`, `exam_attempts`, `exam_answers`), seis columnas a
-- `courses` y una a `honor_enrollments`. Aditiva e idempotente: nada se borra, renombra ni
-- reescribe, y las columnas nuevas nacen con el valor por defecto que ya describe la
-- realidad de las filas existentes (umbral 80, tres intentos, examen en línea, sin
-- tiempo adicional). Aplicar ANTES de desplegar el código, y DESPUÉS de 009c.
--
-- El banco vive en su propia tabla y NO en `honor_questions` porque `honor_questions`
-- cuelga de una fila de `honor_requirements`, que es por versión y por idioma y la
-- comparten todos los cursos de esa especialidad: dos instructores se pisarían el banco
-- (hallazgos 2 y 4 de la spec).

-- ---------------------------------------------------------------------------
-- Parámetros del examen del curso. Son CONTENIDO: se revisan con el resto y
-- quedan inmutables al publicar. Las dos de sesión son operativas (§4.6, I6).
-- ---------------------------------------------------------------------------
ALTER TABLE courses ADD COLUMN IF NOT EXISTS exam_passing_score integer NOT NULL DEFAULT 80;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS exam_time_limit_minutes integer;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS max_exam_attempts integer NOT NULL DEFAULT 3;
ALTER TABLE courses ADD COLUMN IF NOT EXISTS exam_mode varchar(10) NOT NULL DEFAULT 'ONLINE';
ALTER TABLE courses ADD COLUMN IF NOT EXISTS session_code varchar(8);
ALTER TABLE courses ADD COLUMN IF NOT EXISTS session_code_expires_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'courses_exam_passing_score_check') THEN
    ALTER TABLE courses ADD CONSTRAINT courses_exam_passing_score_check
      CHECK (exam_passing_score BETWEEN 80 AND 100);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'courses_exam_time_limit_check') THEN
    ALTER TABLE courses ADD CONSTRAINT courses_exam_time_limit_check
      CHECK (exam_time_limit_minutes IS NULL OR exam_time_limit_minutes BETWEEN 5 AND 180);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'courses_max_exam_attempts_check') THEN
    ALTER TABLE courses ADD CONSTRAINT courses_max_exam_attempts_check
      CHECK (max_exam_attempts BETWEEN 1 AND 10);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'courses_exam_mode_check') THEN
    ALTER TABLE courses ADD CONSTRAINT courses_exam_mode_check
      CHECK (exam_mode IN ('ONLINE','IN_PERSON'));
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- El banco del curso, por requisito del plan. La FK compuesta contra
-- `course_requirements` garantiza que ninguna pregunta sobrevive a la posición
-- que evalúa, ni al curso.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS course_questions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  course_id uuid NOT NULL,
  requirement_position integer NOT NULL,
  position integer NOT NULL,
  question_text text NOT NULL CHECK (char_length(question_text) BETWEEN 1 AND 1000),
  question_type varchar(20) NOT NULL
    CHECK (question_type IN ('MULTIPLE_CHOICE','TRUE_FALSE','SHORT_ANSWER','ESSAY')),
  -- MULTIPLE_CHOICE: de 2 a 6 opciones distintas (se valida en el servicio).
  options jsonb,
  correct_answer text NOT NULL,
  points integer NOT NULL DEFAULT 1 CHECK (points BETWEEN 1 AND 10),
  explanation text CHECK (char_length(explanation) <= 1000),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (course_id, requirement_position)
    REFERENCES course_requirements (course_id, requirement_position) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_course_questions_bank
  ON course_questions (course_id, requirement_position, position);

-- ---------------------------------------------------------------------------
-- Un intento. Los intentos se cuentan por (inscripción, curso) y los anulados
-- no cuentan. `completed_positions` guarda qué requisitos completó este intento
-- para poder revertir exactamente eso al anularlo (I6).
-- `deadline_at` lo fija el servidor al empezar: el reloj del examen es el suyo.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exam_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enrollment_id uuid NOT NULL REFERENCES honor_enrollments(id) ON DELETE CASCADE,
  course_id uuid NOT NULL REFERENCES courses(id),
  user_id uuid NOT NULL REFERENCES users(id),
  attempt_no smallint NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'IN_PROGRESS'
    CHECK (status IN ('IN_PROGRESS','PENDING_GRADING','PASSED','FAILED','VOIDED')),
  started_at timestamptz NOT NULL DEFAULT now(),
  deadline_at timestamptz NOT NULL,
  submitted_at timestamptz,
  finished_at timestamptz,
  time_limit_minutes smallint,
  passing_score smallint NOT NULL,
  points_total integer NOT NULL DEFAULT 0,
  points_awarded integer,
  score_percent smallint,
  completed_positions integer[] NOT NULL DEFAULT '{}',
  proctored boolean NOT NULL DEFAULT false,
  auto_submitted boolean NOT NULL DEFAULT false,
  voided_by_id uuid REFERENCES users(id),
  voided_at timestamptz,
  void_reason text CHECK (char_length(void_reason) <= 2000),
  UNIQUE (enrollment_id, course_id, attempt_no)
);
-- Un único intento abierto por inscripción.
CREATE UNIQUE INDEX IF NOT EXISTS exam_attempts_one_open_key
  ON exam_attempts (enrollment_id) WHERE status = 'IN_PROGRESS';
CREATE INDEX IF NOT EXISTS ix_exam_attempts_course_status ON exam_attempts (course_id, status);

-- ---------------------------------------------------------------------------
-- El «papel» del intento: una fila por pregunta sorteada, creada al empezar y
-- ya barajada. `option_order` es la permutación con la que se mostraron las
-- opciones, guardada para poder calificar y volver a pintar igual al reanudar.
-- El cliente nunca elige qué preguntas le tocan.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exam_answers (
  attempt_id uuid NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
  position integer NOT NULL,
  question_id uuid NOT NULL REFERENCES course_questions(id),
  requirement_position integer NOT NULL,
  option_order jsonb,
  response text CHECK (char_length(response) <= 4000),
  answered_at timestamptz,
  is_correct boolean,
  points_possible smallint NOT NULL DEFAULT 1,
  points_awarded smallint,
  graded_by_id uuid REFERENCES users(id),
  graded_at timestamptz,
  grader_note text CHECK (char_length(grader_note) <= 1000),
  PRIMARY KEY (attempt_id, position)
);
CREATE INDEX IF NOT EXISTS ix_exam_answers_question ON exam_answers (question_id);

-- ---------------------------------------------------------------------------
-- Accesibilidad (§4.7): tiempo adicional por inscripción, que fija el tutor de
-- un menor, el propio miembro adulto o el instructor del curso. Se aplica al
-- calcular el plazo del SIGUIENTE intento.
-- ---------------------------------------------------------------------------
ALTER TABLE honor_enrollments
  ADD COLUMN IF NOT EXISTS exam_extra_time_percent smallint NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'honor_enrollments_extra_time_check'
  ) THEN
    ALTER TABLE honor_enrollments ADD CONSTRAINT honor_enrollments_extra_time_check
      CHECK (exam_extra_time_percent IN (0, 25, 50, 100));
  END IF;
END
$$;
