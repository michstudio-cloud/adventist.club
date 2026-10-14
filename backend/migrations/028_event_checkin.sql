-- 028_event_checkin.sql
-- Check-in con QR, participación automática y pase estable (bloque I · iteración 3;
-- spec _dev-eventos/SPEC_CHECKIN_JUEZ.md «Backend» 1–7).
--
-- QUÉ CAMBIA (aditiva; no borra, no renombra, no reescribe filas):
--   event_registrations  + pass_token_enc     el pase CIFRADO (AES-GCM, clave derivada del secreto
--                                             de la app) para mostrarlo sin rotarlo. El hash sigue
--                                             siendo lo único con lo que se busca. NULL = pase
--                                             antiguo: el API lo regenera UNA vez al pedirlo.
--                        + checked_in_at, checked_in_by_id, checked_in_as
--                                             la llegada vigente del club (NULL = no ha llegado).
--   event_check_ins      tabla nueva: cada check-in y su anulación (quién, cuándo, por qué). La fila
--                        sin `undone_at` es la vigente (a lo más una por inscripción).
--   evaluations          + check_in_id        el check-in que otorgó esta evaluación (AUTO_CHECKIN);
--                                             anular el check-in anula lo que otorgó.
--                        captured_as admite además AUTO_CHECKIN y AUTO_SCAN (el CHECK se amplía).
--   org_invitations      + return_to          a dónde vuelve el invitado tras aceptar (sólo hosts
--                                             propios; lo valida el API).
--
-- `event_activities.config.auto_award` (null | "on_checkin" | "on_scan") vive en el JSON de la
-- actividad: no necesita columna.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 027. Idempotente.
-- Vuelta atrás (el código anterior ignora todo esto, salvo el CHECK: anula antes las
-- evaluaciones AUTO_* o ponles captured_as = 'JUDGE'):
--   ALTER TABLE evaluations DROP COLUMN check_in_id;
--   DROP TABLE event_check_ins;
--   ALTER TABLE event_registrations DROP COLUMN pass_token_enc, DROP COLUMN checked_in_at,
--     DROP COLUMN checked_in_by_id, DROP COLUMN checked_in_as;
--   ALTER TABLE org_invitations DROP COLUMN return_to;
--   (y restaurar evaluations_captured_as_ck a ('JUDGE', 'COORDINATION')).

ALTER TABLE event_registrations
  ADD COLUMN IF NOT EXISTS pass_token_enc text,
  ADD COLUMN IF NOT EXISTS checked_in_at timestamptz,
  ADD COLUMN IF NOT EXISTS checked_in_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS checked_in_as varchar(12);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'event_registrations_checked_in_as_ck') THEN
    ALTER TABLE event_registrations ADD CONSTRAINT event_registrations_checked_in_as_ck
      CHECK (checked_in_as IS NULL OR checked_in_as IN ('JUDGE', 'COORDINATION'));
  END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS event_check_ins (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  registration_id uuid NOT NULL REFERENCES event_registrations(id) ON DELETE CASCADE,
  checked_in_at timestamptz NOT NULL DEFAULT now(),
  checked_in_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  -- Con qué papel: JUDGE (juez del evento) o COORDINATION (coordinación / administración).
  checked_in_as varchar(12) NOT NULL CHECK (checked_in_as IN ('JUDGE', 'COORDINATION')),
  -- PASS (se leyó el QR) | MANUAL (se eligió el club de la lista).
  method varchar(8) NOT NULL DEFAULT 'PASS' CHECK (method IN ('PASS', 'MANUAL')),
  undone_at timestamptz,
  undone_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  undo_reason text,
  CONSTRAINT event_check_ins_undo_ck CHECK (
    (undone_at IS NULL AND undo_reason IS NULL)
    OR (undone_at IS NOT NULL AND length(btrim(undo_reason)) > 0))
);
-- A lo más un check-in vigente por inscripción (dos escaneos simultáneos: uno gana).
CREATE UNIQUE INDEX IF NOT EXISTS event_check_ins_current_uq
  ON event_check_ins (registration_id) WHERE undone_at IS NULL;
CREATE INDEX IF NOT EXISTS event_check_ins_registration_idx ON event_check_ins (registration_id);

COMMENT ON TABLE event_check_ins IS
  'Llegadas de los clubes a un evento (028). Vigente = undone_at NULL; anular exige motivo.';

ALTER TABLE evaluations
  ADD COLUMN IF NOT EXISTS check_in_id uuid REFERENCES event_check_ins(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS evaluations_check_in_idx ON evaluations (check_in_id) WHERE check_in_id IS NOT NULL;

-- captured_as: el CHECK de 025 es anónimo (columna en CREATE TABLE y en ADD COLUMN): se busca por
-- su definición y se reemplaza por uno con nombre que admite los dos modos automáticos.
DO $$
DECLARE
  found record;
BEGIN
  FOR found IN
    SELECT c.conname FROM pg_constraint c
    WHERE c.conrelid = 'evaluations'::regclass AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%captured_as%'
      AND c.conname <> 'evaluations_captured_as_ck'
  LOOP
    EXECUTE format('ALTER TABLE evaluations DROP CONSTRAINT %I', found.conname);
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluations_captured_as_ck') THEN
    ALTER TABLE evaluations ADD CONSTRAINT evaluations_captured_as_ck
      CHECK (captured_as IN ('JUDGE', 'COORDINATION', 'AUTO_CHECKIN', 'AUTO_SCAN'));
  END IF;
END;
$$;

ALTER TABLE org_invitations ADD COLUMN IF NOT EXISTS return_to text;
