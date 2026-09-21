-- 010b_exam_session_code.sql
-- Bloque C · I6 — El código de la sesión presencial, guardado con hash.
-- Spec: docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md §4.6
--
-- Añade UNA columna a `courses`: `session_code_hash varchar(64)`. Aditiva e idempotente:
-- no borra, renombra ni reescribe nada. Aplicar ANTES de desplegar el código, y DESPUÉS
-- de 010.
--
-- Por qué una columna nueva y no `courses.session_code`: el código es un secreto compartido
-- que el instructor dicta en el aula y que abre un intento de examen. Guardarlo en claro
-- deja el secreto legible para cualquiera que pueda leer la fila del curso (un volcado, una
-- consulta de soporte), así que se guarda como SHA-256 salado con el id del curso —
-- `sha256('<course_id>:<CODIGO>')`, 64 caracteres hexadecimales, que no caben en el
-- `varchar(8)` de `session_code`. Esa columna anterior se conserva intacta y sin uso: hoy
-- vale NULL en todas las filas (I5 no permitía abrir sesiones) y quitarla sería destructivo.
--
-- El salado por curso hace que el mismo código en dos cursos tenga dos hashes distintos:
-- un código no se puede probar contra otro curso ni reconocer entre cursos.

ALTER TABLE courses ADD COLUMN IF NOT EXISTS session_code_hash varchar(64);

COMMENT ON COLUMN courses.session_code_hash IS
  'Bloque C I6: SHA-256 de ''<course_id>:<CODIGO>'' de la sesión presencial vigente. '
  'Sustituye a session_code, que queda sin uso y siempre NULL.';
