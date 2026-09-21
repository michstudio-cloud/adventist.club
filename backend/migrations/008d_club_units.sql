-- 008d_club_units.sql
-- Bloque E, incremento E5 — Unidades del club.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §3 y §5.4
--
-- Aditiva e idempotente: una tabla nueva (`club_units`), la columna
-- `club_memberships.unit_id` y las dos claves ajenas que `008b` y `008c`
-- dejaron pendientes (`club_memberships.unit_id`, `club_invitations.unit_id`).
-- No borra, renombra ni reescribe ninguna columna ni fila existente.
-- Aplicar ANTES de desplegar el código que la mapea.
--
-- Por qué una tabla ligera y no nodos `organizations.type = 'unit'`: si el
-- miembro colgara de la unidad, `users.organization_id` dejaría de ser el club
-- y se rompería `can_review` del bloque A; además las lecturas de `org-nodes`
-- son públicas y una unidad es un grupo de menores con nombre.

-- ---------------------------------------------------------------------------
-- La unidad: un grupo dentro del club, con consejero(a), tramo de edad (AVISO)
-- y cupo (DURO). Se reorganizan cada año: archivar una fila es trivial.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_units (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  club_id uuid NOT NULL REFERENCES organizations(id),
  name varchar(80) NOT NULL,
  -- Tramo de edad: sólo un aviso, los cumpleaños lo desajustan durante el año.
  -- `max_age` NULL = sin tope (Conquistadores usa 16+; Aventureros usará 6–9).
  min_age smallint,
  max_age smallint,
  capacity smallint CHECK (capacity BETWEEN 1 AND 50),
  counselor_id uuid REFERENCES users(id) ON DELETE SET NULL,
  status varchar(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT club_units_age_range_check
    CHECK (min_age IS NULL OR max_age IS NULL OR min_age <= max_age)
);

-- Dos unidades activas del mismo club no comparten nombre; una archivada libera el suyo.
CREATE UNIQUE INDEX IF NOT EXISTS club_units_active_name_key
  ON club_units (club_id, lower(name)) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_club_units_club_status ON club_units (club_id, status);
CREATE INDEX IF NOT EXISTS ix_club_units_counselor
  ON club_units (counselor_id) WHERE counselor_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- La membresía recuerda en qué unidad está. NULL = sin unidad, que es un estado
-- normal (una unidad llena deja entrar igual al club, sin unidad).
-- ---------------------------------------------------------------------------
ALTER TABLE club_memberships ADD COLUMN IF NOT EXISTS unit_id uuid;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'club_memberships_unit_id_fkey'
  ) THEN
    ALTER TABLE club_memberships
      ADD CONSTRAINT club_memberships_unit_id_fkey
      FOREIGN KEY (unit_id) REFERENCES club_units(id) ON DELETE SET NULL;
  END IF;
END
$$;

-- `008c` creó `club_invitations.unit_id` sin FK porque la tabla aún no existía.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'club_invitations_unit_id_fkey'
  ) THEN
    ALTER TABLE club_invitations
      ADD CONSTRAINT club_invitations_unit_id_fkey
      FOREIGN KEY (unit_id) REFERENCES club_units(id) ON DELETE SET NULL;
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_club_memberships_unit
  ON club_memberships (unit_id) WHERE unit_id IS NOT NULL;
