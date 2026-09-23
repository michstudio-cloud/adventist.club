-- 015_secretaria.sql
-- Bloque H — Secretaría del club: cargos, «pasar lista» por reunión y puntuación.
-- Spec: docs/superpowers/specs/2026-09-23-secretaria-club.md §1 y §2
--
-- QUÉ CAMBIA:
--   1. Tabla nueva `club_officers` (§1): los cargos del club. Un cargo es un TÍTULO, no un
--      permiso. Nunca se borra una fila: un cargo se cierra con `until`.
--   2. Tabla nueva `club_meetings` (§2): las reuniones del club, únicas por
--      club + fecha + tipo + título.
--   3. Tabla nueva `club_attendance`: el estado de cada miembro en cada reunión
--      (PRESENT | ABSENT | JUSTIFIED). Lo que suma XP sigue siendo `activity_logs`.
--   4. `activity_logs`: columna nueva `meeting_id` (NULL para todo lo existente) con su
--      índice único parcial: una sola asistencia por reunión y persona.
--
-- No borra, no renombra y no reescribe ninguna fila existente.
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, y DESPUÉS de 014_profiles.sql.
-- Idempotente: se puede ejecutar dos veces.
-- Vuelta atrás: `DROP INDEX ux_activity_logs_meeting_user; ALTER TABLE activity_logs
-- DROP COLUMN meeting_id; DROP TABLE club_attendance, club_meetings, club_officers;`
-- (las asistencias ya registradas quedan como `activity_logs` sueltos y siguen sumando XP).

-- ---------------------------------------------------------------------------
-- 1. Cargos
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_officers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  club_id uuid NOT NULL REFERENCES organizations(id),
  -- La membresía de la persona en ESTE club: si la cuenta se borra, el cargo se va con ella.
  membership_id uuid NOT NULL REFERENCES club_memberships(id) ON DELETE CASCADE,
  title varchar(12) NOT NULL CHECK (title IN (
    'DIRECTOR', 'SUBDIRECTOR', 'SECRETARIO', 'TESORERO', 'CAPELLAN', 'CONSEJERO',
    'INSTRUCTOR', 'OTRO'
  )),
  -- Sólo con OTRO, y OTRO siempre lo lleva: la píldora tiene que decir algo.
  custom_title varchar(60),
  since date NOT NULL DEFAULT current_date,
  until date,
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT club_officers_custom_title_check
    CHECK ((title = 'OTRO') = (custom_title IS NOT NULL)),
  CONSTRAINT club_officers_dates_check CHECK (until IS NULL OR until >= since)
);

-- Un cargo abierto por persona (y por título libre, para OTRO). La API además trata como
-- vigente un cargo con `until` futuro.
CREATE UNIQUE INDEX IF NOT EXISTS ux_club_officers_open
  ON club_officers (membership_id, title, lower(coalesce(custom_title, '')))
  WHERE until IS NULL;
CREATE INDEX IF NOT EXISTS ix_club_officers_club ON club_officers (club_id);

-- ---------------------------------------------------------------------------
-- 2. Reuniones
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_meetings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  club_id uuid NOT NULL REFERENCES organizations(id),
  held_on date NOT NULL,
  kind varchar(12) NOT NULL CHECK (kind IN ('REUNION', 'CAMPAMENTO', 'SERVICIO', 'OTRO')),
  title varchar(120),
  notes varchar(500),
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Única por club + fecha + tipo + título (sin título cuenta como el título vacío).
CREATE UNIQUE INDEX IF NOT EXISTS ux_club_meetings_identity
  ON club_meetings (club_id, held_on, kind, lower(coalesce(title, '')));

-- ---------------------------------------------------------------------------
-- 3. La lista de cada reunión
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_attendance (
  meeting_id uuid NOT NULL REFERENCES club_meetings(id) ON DELETE CASCADE,
  membership_id uuid NOT NULL REFERENCES club_memberships(id) ON DELETE CASCADE,
  status varchar(10) NOT NULL CHECK (status IN ('PRESENT', 'ABSENT', 'JUSTIFIED')),
  recorded_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (meeting_id, membership_id)
);

-- El % de asistencia de un miembro (nómina y resumen).
CREATE INDEX IF NOT EXISTS ix_club_attendance_membership ON club_attendance (membership_id);

-- ---------------------------------------------------------------------------
-- 4. activity_logs.meeting_id
-- ---------------------------------------------------------------------------
ALTER TABLE activity_logs
  ADD COLUMN IF NOT EXISTS meeting_id uuid NULL REFERENCES club_meetings(id) ON DELETE SET NULL;

-- Una asistencia por reunión y persona: «pasar lista» dos veces no suma dos veces.
CREATE UNIQUE INDEX IF NOT EXISTS ux_activity_logs_meeting_user
  ON activity_logs (meeting_id, user_id)
  WHERE meeting_id IS NOT NULL;
