-- 029_event_offline_lock.sql
-- Evento operado sin internet en un nodo local (bloque I · iteración 4; spec
-- _dev-eventos/SPEC_RED_LOCAL.md §3, nodo-evento/README.md).
--
-- QUÉ CAMBIA (aditiva; no borra, no renombra, no reescribe filas):
--   events               + offline_locked_at, offline_locked_by_id, offline_lock_reason,
--                          offline_node_label
--                          «en nodo local»: mientras offline_locked_at no es NULL, la nube RECHAZA
--                          toda escritura del evento (423) y sólo permite consultar. Lo pone el
--                          script `export` (o la coordinación por el API) y lo quita `import`.
--   event_offline_syncs  tabla nueva: cada export (prod -> nodo) e import (nodo -> prod) de un
--                        evento, con la huella de cada fila al exportar (para detectar conflictos
--                        al importar) y el reporte del import. Sin datos personales: sólo ids y md5.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 028. Idempotente.
-- Vuelta atrás (el código anterior ignora todo esto):
--   DROP TABLE event_offline_syncs;
--   ALTER TABLE events DROP COLUMN offline_locked_at, DROP COLUMN offline_locked_by_id,
--     DROP COLUMN offline_lock_reason, DROP COLUMN offline_node_label;

ALTER TABLE events
  ADD COLUMN IF NOT EXISTS offline_locked_at timestamptz,
  ADD COLUMN IF NOT EXISTS offline_locked_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS offline_lock_reason text,
  ADD COLUMN IF NOT EXISTS offline_node_label varchar(120);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'events_offline_lock_ck') THEN
    -- Bloqueado => con motivo; desbloqueado => sin rastros (el historial vive en audit_log).
    ALTER TABLE events ADD CONSTRAINT events_offline_lock_ck CHECK (
      (offline_locked_at IS NULL AND offline_locked_by_id IS NULL AND offline_lock_reason IS NULL
         AND offline_node_label IS NULL)
      OR (offline_locked_at IS NOT NULL AND length(btrim(offline_lock_reason)) > 0));
  END IF;
END;
$$;

COMMENT ON COLUMN events.offline_locked_at IS
  'Evento en nodo local (029): no NULL = la nube sólo permite leer este evento.';

CREATE TABLE IF NOT EXISTS event_offline_syncs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  -- EXPORT: prod -> nodo (se guarda en prod); NODE_COPY: la misma copia vista desde el nodo;
  -- IMPORT: nodo -> prod (se guarda en prod, con el reporte).
  kind varchar(10) NOT NULL CHECK (kind IN ('EXPORT', 'NODE_COPY', 'IMPORT')),
  -- IMPORT: a qué EXPORT corresponde.
  export_id uuid REFERENCES event_offline_syncs(id) ON DELETE SET NULL,
  node_label varchar(120),
  actor_email citext,
  -- {"<tabla>": {"<id>": "<md5 de la fila>"}} al exportar.
  snapshot jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(snapshot) = 'object'),
  report jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(report) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS event_offline_syncs_event_idx ON event_offline_syncs (event_id, created_at DESC);

COMMENT ON TABLE event_offline_syncs IS
  'Exports/imports de un evento hacia/desde el nodo local (029). snapshot = huellas md5 por fila.';
