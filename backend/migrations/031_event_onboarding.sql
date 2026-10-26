-- 031_event_onboarding.sql
-- Alta desde la interfaz del evento («Vincular director», «Invitar juez»): una invitación de
-- organización puede llevar QUÉ HACER al aceptarla. Spec: _dev-eventos/FIDELIDAD_PROTOTIPO.md §Parte 2.
--
-- QUÉ CAMBIA (aditiva; no borra, no renombra, no reescribe filas):
--   org_invitations  + on_accept jsonb   lo que se aplica UNA vez al aceptar, p. ej.
--                      {"type": "event_staff", "event_id": "<uuid>", "staff_role": "JUDGE",
--                       "activity_ids": ["<uuid>", …] | null (todas)}  → crea filas de event_staff;
--                      {"type": "event_director", "event_id": "<uuid>", "name": "…"}  → sólo marca que
--                       la invitación de CLUB_DIRECTOR salió desde ese evento (se lista allí).
--                    + rol 'EVENT_STAFF' en el CHECK de role: invitación sin rol de plataforma (juez o
--                      coordinación de UN evento), siempre con on_accept.
--   índice parcial por on_accept->>'event_id' (lista de invitaciones de un evento).
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 030. Idempotente.
-- Vuelta atrás (el código anterior ignora on_accept; NO revertir mientras haya filas EVENT_STAFF):
--   DROP INDEX IF EXISTS ix_org_invitations_on_accept_event;
--   ALTER TABLE org_invitations DROP CONSTRAINT IF EXISTS org_invitations_on_accept_check;
--   ALTER TABLE org_invitations DROP CONSTRAINT org_invitations_role_check, ADD CONSTRAINT
--     org_invitations_role_check CHECK (role IN ('ADMIN_ASSOCIATION','COORDINATOR_ZONE','INSTRUCTOR','CLUB_DIRECTOR'));
--   ALTER TABLE org_invitations DROP COLUMN on_accept;

ALTER TABLE org_invitations ADD COLUMN IF NOT EXISTS on_accept jsonb;

ALTER TABLE org_invitations DROP CONSTRAINT IF EXISTS org_invitations_role_check;
ALTER TABLE org_invitations ADD CONSTRAINT org_invitations_role_check
  CHECK (role IN ('ADMIN_ASSOCIATION','COORDINATOR_ZONE','INSTRUCTOR','CLUB_DIRECTOR','EVENT_STAFF'));

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'org_invitations_on_accept_check') THEN
    ALTER TABLE org_invitations ADD CONSTRAINT org_invitations_on_accept_check
      CHECK ((on_accept IS NULL OR jsonb_typeof(on_accept) = 'object')
             AND (role <> 'EVENT_STAFF' OR on_accept IS NOT NULL));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_org_invitations_on_accept_event
  ON org_invitations ((on_accept->>'event_id')) WHERE on_accept IS NOT NULL;
