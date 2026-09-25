-- 025_events.sql
-- Eventos y puntajes (spec docs/superpowers/specs/2026-09-24-eventos.md §3.1).
--
-- QUÉ CAMBIA: crea OCHO tablas nuevas y nada más. No toca ninguna tabla existente, no borra,
-- no renombra y no reescribe filas.
--
--   events                  el evento (camporee, campamento…) de una organización dueña
--   event_activities        actividades, rondas y estaciones (kind + config declarativa)
--   event_adjustment_types  catálogo de bonificaciones/penalizaciones del evento
--   event_staff             COORDINATOR / JUDGE del evento (permiso CONTEXTUAL: no toca users.role)
--   event_registrations     clubes oficiales inscritos, con pase QR opaco (sólo su hash)
--   evaluations             la captura vigente de (registro, actividad): hechos + puntos calculados
--   evaluation_revisions    historial inmutable de correcciones y anulaciones (motivo obligatorio)
--   event_adjustments       bonificaciones y penalizaciones con aprobación y anulación
--
-- Total oficial = Σ evaluaciones CONFIRMED (de actividades que cuentan) + Σ BONUS − Σ PENALTY
-- aprobados y no anulados; sin piso ni techo implícitos (lo calcula app/services/event_scores.py).
-- Borrar un evento (sólo en DRAFT, por el API) arrastra todo lo suyo en cascada.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 023. Idempotente.
-- Vuelta atrás: DROP TABLE event_adjustments, evaluation_revisions, evaluations,
--   event_registrations, event_staff, event_adjustment_types, event_activities, events;

CREATE TABLE IF NOT EXISTS events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  ministry_id uuid NOT NULL REFERENCES ministries(id),
  name varchar(200) NOT NULL,
  slug varchar(120) NOT NULL,
  venue varchar(200),
  city varchar(120),
  starts_on date NOT NULL,
  ends_on date NOT NULL,
  status varchar(12) NOT NULL DEFAULT 'DRAFT'
    CHECK (status IN ('DRAFT', 'OPEN', 'IN_PROGRESS', 'CLOSED', 'ARCHIVED')),
  registration_closes_on date,
  -- Sube cada vez que cambia una regla de puntaje; cada evaluación guarda la suya.
  rules_version integer NOT NULL DEFAULT 1 CHECK (rules_version >= 1),
  -- [{key, label, min, max}]: min inclusivo (null = sin piso), max informativo.
  honor_bands jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(honor_bands) = 'array'),
  source_note text,
  template_of_id uuid REFERENCES events(id) ON DELETE SET NULL,
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT events_dates_ck CHECK (ends_on >= starts_on),
  CONSTRAINT events_org_slug_uq UNIQUE (organization_id, slug)
);
CREATE INDEX IF NOT EXISTS events_org_idx ON events (organization_id, starts_on DESC);

COMMENT ON TABLE events IS
  'Eventos con puntaje (025). La organización dueña es p.ej. una asociación; ver spec eventos §3.';

CREATE TABLE IF NOT EXISTS event_activities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  -- Ronda o estación dentro de una actividad `group` del mismo evento (un solo nivel).
  parent_id uuid REFERENCES event_activities(id) ON DELETE CASCADE,
  position integer NOT NULL DEFAULT 0,
  name varchar(200) NOT NULL,
  description text,
  kind varchar(20) NOT NULL
    CHECK (kind IN ('participation', 'rubric', 'bands', 'per_correct', 'stations', 'group')),
  max_points numeric(8,2) CHECK (max_points IS NULL OR max_points >= 0),
  -- Datos por kind (app/services/event_scoring.py), NUNCA fórmulas ejecutables.
  config jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(config) = 'object'),
  -- TO_DEFINE: la asociación aún no completa sus reglas; bloquea SÓLO sus capturas.
  status varchar(10) NOT NULL DEFAULT 'READY' CHECK (status IN ('READY', 'TO_DEFINE')),
  counts_to_total boolean NOT NULL DEFAULT true,
  schedule_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_activities_event_idx ON event_activities (event_id, parent_id, position);

CREATE TABLE IF NOT EXISTS event_adjustment_types (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind varchar(8) NOT NULL CHECK (kind IN ('BONUS', 'PENALTY')),
  label varchar(200) NOT NULL,
  -- NULL = monto por definir: bloquea aplicar ajustes de este tipo hasta que se defina.
  points numeric(8,2) CHECK (points IS NULL OR points > 0),
  max_per_event integer CHECK (max_per_event IS NULL OR max_per_event >= 1),
  max_per_club integer CHECK (max_per_club IS NULL OR max_per_club >= 1),
  position integer NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_adjustment_types_event_idx ON event_adjustment_types (event_id, position);

CREATE TABLE IF NOT EXISTS event_staff (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role varchar(12) NOT NULL CHECK (role IN ('COORDINATOR', 'JUDGE')),
  -- NULL = todas las actividades. Sólo JUDGE lleva actividad.
  activity_id uuid REFERENCES event_activities(id) ON DELETE CASCADE,
  active boolean NOT NULL DEFAULT true,
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  ended_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT event_staff_activity_ck CHECK (role = 'JUDGE' OR activity_id IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS event_staff_active_uq
  ON event_staff (event_id, user_id, role, coalesce(activity_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE active;
CREATE INDEX IF NOT EXISTS event_staff_user_idx ON event_staff (user_id) WHERE active;

CREATE TABLE IF NOT EXISTS event_registrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  club_id uuid NOT NULL REFERENCES organizations(id),
  status varchar(10) NOT NULL DEFAULT 'REGISTERED' CHECK (status IN ('REGISTERED', 'WITHDRAWN')),
  -- sha256 del pase (QR opaco). Nunca en claro; regenerarlo revoca el anterior.
  pass_token_hash varchar(64) UNIQUE,
  -- {"<activity_id>": true}: finalistas elegidos a mano por la coordinación.
  finalist_flags jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(finalist_flags) = 'object'),
  registered_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT event_registrations_uq UNIQUE (event_id, club_id)
);
CREATE INDEX IF NOT EXISTS event_registrations_club_idx ON event_registrations (club_id);

CREATE TABLE IF NOT EXISTS evaluations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id uuid NOT NULL REFERENCES event_registrations(id) ON DELETE CASCADE,
  activity_id uuid NOT NULL REFERENCES event_activities(id) ON DELETE CASCADE,
  -- Hechos capturados (aciertos, criterios, estaciones); nunca el total.
  inputs jsonb NOT NULL CHECK (jsonb_typeof(inputs) = 'object'),
  points numeric(8,2) NOT NULL,
  breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
  rules_version integer NOT NULL,
  status varchar(10) NOT NULL DEFAULT 'CONFIRMED' CHECK (status IN ('CONFIRMED', 'VOID')),
  judge_id uuid REFERENCES users(id) ON DELETE SET NULL,
  -- Reintentar la misma petición devuelve la misma evaluación; nunca suma dos veces.
  idempotency_key varchar(100) NOT NULL UNIQUE,
  revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
-- Una vigente por (registro, actividad).
CREATE UNIQUE INDEX IF NOT EXISTS evaluations_current_uq
  ON evaluations (registration_id, activity_id) WHERE status = 'CONFIRMED';
CREATE INDEX IF NOT EXISTS evaluations_activity_idx ON evaluations (activity_id);

CREATE TABLE IF NOT EXISTS evaluation_revisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  evaluation_id uuid NOT NULL REFERENCES evaluations(id) ON DELETE CASCADE,
  -- Los valores ANTERIORES al cambio (la revisión que se reemplaza).
  revision integer NOT NULL,
  inputs jsonb NOT NULL,
  points numeric(8,2) NOT NULL,
  status varchar(10) NOT NULL,
  rules_version integer NOT NULL,
  judge_id uuid REFERENCES users(id) ON DELETE SET NULL,
  -- CORRECTION | VOID
  action varchar(12) NOT NULL CHECK (action IN ('CORRECTION', 'VOID')),
  reason text NOT NULL CHECK (length(btrim(reason)) > 0),
  changed_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  idempotency_key varchar(100) UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT evaluation_revisions_uq UNIQUE (evaluation_id, revision)
);

-- Historial inmutable: ninguna fila se reescribe (borrar sólo ocurre en cascada).
CREATE OR REPLACE FUNCTION evaluation_revisions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'evaluation_revisions es inmutable';
END;
$$;
DROP TRIGGER IF EXISTS evaluation_revisions_no_update ON evaluation_revisions;
CREATE TRIGGER evaluation_revisions_no_update
  BEFORE UPDATE ON evaluation_revisions
  FOR EACH ROW EXECUTE FUNCTION evaluation_revisions_immutable();

CREATE TABLE IF NOT EXISTS event_adjustments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id uuid NOT NULL REFERENCES event_registrations(id) ON DELETE CASCADE,
  activity_id uuid REFERENCES event_activities(id) ON DELETE SET NULL,
  kind varchar(8) NOT NULL CHECK (kind IN ('BONUS', 'PENALTY')),
  adjustment_type_id uuid REFERENCES event_adjustment_types(id) ON DELETE SET NULL,
  -- Magnitud (> 0); el signo lo da `kind`.
  points numeric(8,2) NOT NULL CHECK (points > 0),
  reason text NOT NULL CHECK (length(btrim(reason)) > 0),
  created_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  -- NULL = pendiente de aprobación: no cuenta en el total.
  approved_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  approved_at timestamptz,
  voided_at timestamptz,
  voided_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  void_reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_adjustments_registration_idx ON event_adjustments (registration_id);
CREATE INDEX IF NOT EXISTS event_adjustments_type_idx ON event_adjustments (adjustment_type_id);
