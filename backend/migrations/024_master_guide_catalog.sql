-- 024 · Catálogo de Guías Mayores y ministerio activo de la persona.
--
-- 1. `programs.kind` admite tres familias nuevas, todas del ministerio `master-guides`
--    (Guías Mayores) y con la misma estructura que una clase (secciones → requisitos,
--    `issuer_level`, `status`, atribución):
--      MEDALLION  medallones
--      MASTERY    maestrías (el certificado de maestría: la plantilla `ntam-maestria` sigue
--                 retirada; esto es el CATÁLOGO, no una plantilla)
--      TRAINING   certificaciones de entrenamiento del Ministerio de Clubes (EMC / CMT)
--    `CLASS` y `CURRICULUM` no cambian. La columna ya es varchar(12): sólo cambia el CHECK.
--    Los datos (borradores) los carga `migrations/import_master_guide_catalog.py`.
-- 2. `users.active_ministry_id`: el ministerio que la persona eligió en el selector del
--    armazón (Conquistadores, Aventureros, Guías Mayores…). NULL = el predeterminado (el
--    ministerio principal de su club o, sin club, Conquistadores). El API sólo acepta uno de
--    `ministries_available` (los de sus clubes; MASTER y administración: todos).
-- 3. `users.active_club_id`: el club elegido en el mismo selector cuando la persona tiene más
--    de uno a la vista (su membresía activa y el club que dirige). Hoy una persona tiene como
--    máximo UNA membresía activa (`club_memberships_one_active_key`), así que casi siempre es
--    NULL o ese club; es una preferencia, nunca un permiso.
--
-- Idempotente: se puede aplicar dos veces sin efecto. Sin bloques DO (los tests la aplican
-- partiendo por «;»).
--
-- VUELTA ATRÁS: mientras no haya programas de las familias nuevas, `DROP COLUMN` de las dos
-- columnas y reponer el CHECK de 012 (`kind IN ('CLASS', 'CURRICULUM')`).

ALTER TABLE programs DROP CONSTRAINT IF EXISTS programs_kind_check;
ALTER TABLE programs
  ADD CONSTRAINT programs_kind_check
  CHECK (kind IN ('CLASS', 'CURRICULUM', 'MEDALLION', 'MASTERY', 'TRAINING'));

COMMENT ON COLUMN programs.kind IS
  'CLASS / CURRICULUM (012) · MEDALLION / MASTERY / TRAINING: certificaciones de Guías Mayores (024)';

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS active_ministry_id uuid NULL REFERENCES ministries(id) ON DELETE SET NULL;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS active_club_id uuid NULL REFERENCES organizations(id) ON DELETE SET NULL;

COMMENT ON COLUMN users.active_ministry_id IS
  'Ministerio activo elegido en el selector (024). NULL = el predeterminado.';
COMMENT ON COLUMN users.active_club_id IS
  'Club activo elegido en el selector (024). Preferencia, nunca permiso.';
