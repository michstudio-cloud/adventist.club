-- 032_event_node_settings.sql
-- «Nodo de evento» para otras asociaciones: la administración general (MASTER_GC) decide, por
-- evento, si se puede preparar un nodo y si ese nodo recibe el certificado HTTPS real.
--
-- QUÉ CAMBIA (aditiva; no borra, no renombra):
--   events + offline_node_enabled   «Preparar» un nodo (POST /events/{id}/offline/export) sólo si
--                                   está en true. Lo cambia sólo un MASTER_GC
--                                   (PUT /events/{id}/offline/settings).
--          + offline_node_real_cert el paquete del nodo lleva el certificado de Let's Encrypt (y su
--                                   llave) de events/api/admin/media.adventist.club SÓLO si está en
--                                   true; si no, el nodo usa su CA local (plan B).
--   Ninguna de las dos viaja de vuelta: el nodo no puede cambiarlas al subir
--   (nodo_sync_core.IGNORED).
--
-- Los eventos que YA prepararon un nodo (el camporee de noviembre de 2026) quedan habilitados para
-- no cortarles el flujo; el certificado real queda apagado para todos (se enciende a mano).
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 031. Idempotente.
-- Vuelta atrás (el código anterior ignora estas columnas):
--   ALTER TABLE events DROP COLUMN offline_node_enabled, DROP COLUMN offline_node_real_cert;

ALTER TABLE events
  ADD COLUMN IF NOT EXISTS offline_node_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS offline_node_real_cert boolean NOT NULL DEFAULT false;

UPDATE events SET offline_node_enabled = true
 WHERE NOT offline_node_enabled
   AND id IN (SELECT DISTINCT event_id FROM event_offline_nodes WHERE NOT local);
