-- 014_profiles.sql
-- Bloque G — Perfil público, foto de perfil, XP y «barra de buena conducta».
-- Spec: docs/superpowers/specs/2026-09-23-perfil-publico.md §2 y §4.1
--
-- QUÉ CAMBIA:
--   1. `users`: seis columnas nuevas (`handle`, `bio`, `cover_url`, `profile_visibility`,
--      `guardian_allows_avatar`, `handle_changed_at`), con sus CHECK y un índice único
--      sobre `handle`. Todas nacen NULL o con un valor por defecto que NO expone a nadie:
--      `profile_visibility = 'private'` y `guardian_allows_avatar = false`.
--   2. `handle` se RELLENA para las cuentas existentes con la parte local del correo
--      (sólo [a-z0-9_.], mínimo 3, sin palabras reservadas) y un sufijo numérico si ya
--      está ocupado. Es la ÚNICA reescritura de filas de esta migración: no toca ninguna
--      otra columna.
--   3. Un disparador BEFORE INSERT da el mismo `handle` por defecto a toda cuenta nueva,
--      venga del registro, de una invitación o de un script: así ningún camino de alta
--      tiene que acordarse de él.
--   4. Tabla nueva `xp_awards` (§4.1): los puntos que el director otorga. Nunca se borra
--      una fila: un error se corrige con otra fila negativa.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, y DESPUÉS de 013_activity_logs.sql.
-- Idempotente: se puede ejecutar dos veces (el relleno sólo toca filas con handle NULL).
-- Vuelta atrás: `DROP TRIGGER users_default_handle_trg ON users; DROP FUNCTION
-- users_set_default_handle(); DROP FUNCTION users_default_handle(text, uuid);
-- DROP TABLE xp_awards;` y `ALTER TABLE users DROP COLUMN ...` de las seis columnas.
--
-- La lista de palabras reservadas es la de app/services/profiles.py (RESERVED_HANDLES y
-- RESERVED_PREFIXES): si cambia allí, cámbiese aquí.

-- ---------------------------------------------------------------------------
-- 1. Columnas del perfil
-- ---------------------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS handle varchar(32);
ALTER TABLE users ADD COLUMN IF NOT EXISTS bio varchar(280);
ALTER TABLE users ADD COLUMN IF NOT EXISTS cover_url text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_visibility varchar(10) NOT NULL DEFAULT 'private';
ALTER TABLE users ADD COLUMN IF NOT EXISTS guardian_allows_avatar boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS handle_changed_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_profile_visibility_check') THEN
    ALTER TABLE users ADD CONSTRAINT users_profile_visibility_check
      CHECK (profile_visibility IN ('private', 'club', 'public'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_handle_format_check') THEN
    ALTER TABLE users ADD CONSTRAINT users_handle_format_check
      CHECK (handle IS NULL OR handle ~ '^[a-z0-9_.]{3,32}$');
  END IF;
END $$;

-- Único. Se crea ANTES del relleno para que la búsqueda de sufijo libre use el índice.
CREATE UNIQUE INDEX IF NOT EXISTS users_handle_key ON users (handle);

-- ---------------------------------------------------------------------------
-- 2. El handle por defecto: una sola función para el relleno y para el disparador
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION users_default_handle(p_email text, p_id uuid)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
  base text;
  bare text;
  candidate text;
  n integer := 1;
BEGIN
  base := regexp_replace(lower(split_part(coalesce(p_email, ''), '@', 1)), '[^a-z0-9_.]', '', 'g');
  base := left(base, 26);  -- deja sitio para el sufijo numérico dentro de los 32
  bare := regexp_replace(base, '[._]', '', 'g');
  IF length(base) < 3
     OR bare = ANY (ARRAY[
       'admin', 'administrator', 'administrador', 'adventist', 'adventistclub', 'conquistadores',
       'conquistador', 'aventureros', 'guiasmayores', 'guiamayor', 'mastergc', 'master', 'root',
       'system', 'sistema', 'staff', 'soporte', 'support', 'help', 'ayuda', 'official', 'oficial',
       'moderator', 'moderador', 'director', 'clubdirector', 'secretary', 'clubsecretary',
       'secretario', 'instructor', 'counselor', 'consejero', 'student', 'estudiante', 'guardian',
       'parentguardian', 'tutor', 'coordinator', 'coordinatorzone', 'coordinador', 'adminassociation',
       'admindivision', 'adminunion', 'me', 'api', 'www', 'mail', 'null', 'undefined', 'profile',
       'profiles', 'perfil', 'settings', 'login', 'logout', 'register', 'signup', 'user', 'users',
       'club', 'clubs', 'verify', 'u'
     ])
     OR bare ~ '^(admin|adventist|conquistador|mastergc)'
  THEN
    base := left('user' || base, 26);
  END IF;
  candidate := base;
  WHILE EXISTS (SELECT 1 FROM users WHERE handle = candidate AND id IS DISTINCT FROM p_id) LOOP
    n := n + 1;
    candidate := base || n::text;
  END LOOP;
  RETURN candidate;
END $$;

-- Relleno de las cuentas existentes, en orden de antigüedad: la más antigua se queda el
-- nombre limpio y las siguientes llevan sufijo.
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT id, email FROM users WHERE handle IS NULL ORDER BY created_at, id LOOP
    UPDATE users SET handle = users_default_handle(r.email, r.id) WHERE id = r.id;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION users_set_default_handle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.handle IS NULL THEN
    NEW.handle := users_default_handle(NEW.email, NEW.id);
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS users_default_handle_trg ON users;
CREATE TRIGGER users_default_handle_trg
  BEFORE INSERT ON users
  FOR EACH ROW EXECUTE FUNCTION users_set_default_handle();

COMMENT ON COLUMN users.handle IS
  'Bloque G: @handle público, único, ^[a-z0-9_.]{3,32}$. Bloqueado 30 días tras cambiarlo.';
COMMENT ON COLUMN users.profile_visibility IS
  'Bloque G: private | club | public. Un menor nunca es public (lo impide la API).';
COMMENT ON COLUMN users.guardian_allows_avatar IS
  'Bloque G: el tutor permite la foto de un menor. Hasta entonces se muestra la inicial.';

-- ---------------------------------------------------------------------------
-- 3. xp_awards (§4.1): puntos otorgados por el director
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS xp_awards (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- El club en el que se otorgó: decide el tope semanal y quién lo ve.
  club_id uuid NOT NULL REFERENCES organizations(id),
  awarded_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  category varchar(15) NOT NULL
    CHECK (category IN ('conducta', 'puntualidad', 'uniforme', 'participacion', 'servicio', 'otro')),
  points smallint NOT NULL CHECK (points BETWEEN -50 AND 50 AND points <> 0),
  note varchar(200),
  occurred_on date NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Historial propio, XP total y barra de conducta (por miembro y fecha)…
CREATE INDEX IF NOT EXISTS ix_xp_awards_user_occurred ON xp_awards (user_id, occurred_on);
-- …y el resumen semanal y el tope del club.
CREATE INDEX IF NOT EXISTS ix_xp_awards_club_occurred ON xp_awards (club_id, occurred_on);
