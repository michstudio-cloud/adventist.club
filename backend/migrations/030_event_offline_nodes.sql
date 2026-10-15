-- 030_event_offline_nodes.sql
-- Varios nodos locales por evento + preparación/subida por el API («Nodo Camporee», modo
-- automático). Spec: _dev-eventos/CONTRATO_NODO_LOGIN.md.
--
-- QUÉ CAMBIA (aditiva; no borra, no renombra, no reescribe filas):
--   event_offline_nodes  tabla nueva: cada nodo (Mac + router) que opera un evento sin internet.
--                        En PRODUCCIÓN hay una fila por nodo preparado (alcance, estado, clave
--                        pública Ed25519 con la que firma sus subidas, huellas base para detectar
--                        conflictos). En la base de UN NODO hay sólo su propia fila con local = true:
--                        así la API del nodo sabe qué actividades puede capturar.
--   event_offline_syncs  + node_id (qué nodo hizo cada EXPORT / IMPORT).
--   node_tls_certs       tabla nueva: caché del certificado Let's Encrypt de los nombres del nodo
--                        (la clave privada va cifrada con una clave derivada de un secreto del API).
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 029. Idempotente.
-- Vuelta atrás (el código anterior ignora todo esto):
--   ALTER TABLE event_offline_syncs DROP COLUMN node_id;
--   DROP TABLE event_offline_nodes; DROP TABLE node_tls_certs;

CREATE TABLE IF NOT EXISTS event_offline_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  label varchar(120) NOT NULL CHECK (length(btrim(label)) > 0),
  coordinator_id uuid REFERENCES users(id) ON DELETE SET NULL,
  coordinator_email citext,
  -- {"all": bool, "activity_ids": ["<uuid>", …], "check_in": bool}
  scope jsonb NOT NULL DEFAULT '{"all": true, "activity_ids": [], "check_in": true}'::jsonb
    CHECK (jsonb_typeof(scope) = 'object'),
  -- PREPARED (preparado) → PARTIAL (subió avance) → FINAL (subió final) · CLOSED (cerrado por un admin)
  status varchar(10) NOT NULL DEFAULT 'PREPARED' CHECK (status IN ('PREPARED', 'PARTIAL', 'FINAL', 'CLOSED')),
  -- Ed25519 pública (base64) atada al nodo al preparar; NULL = preparado por el modo manual (sin API).
  sign_key text,
  -- Último EXPORT de este nodo.
  export_id uuid REFERENCES event_offline_syncs(id) ON DELETE SET NULL,
  -- {"<tabla>": {"<id>": "<md5>"}}: lo que la nube sabía de cada fila según ESTE nodo (el export y,
  -- tras cada subida, lo subido). Base del «3 vías» de su siguiente subida.
  baseline jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(baseline) = 'object'),
  -- Sólo en la base del propio nodo: «esta base ES ese nodo».
  local boolean NOT NULL DEFAULT false,
  -- Sólo en el nodo: material privado del nodo (clave de firma), cifrado con un secreto del nodo,
  -- y la clave pública del servidor para cifrar las subidas.
  node_private_enc text,
  import_key text,
  prepared_at timestamptz NOT NULL DEFAULT now(),
  uploads integer NOT NULL DEFAULT 0,
  last_upload_at timestamptz,
  final_at timestamptz,
  conflicts integer NOT NULL DEFAULT 0,
  last_report jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(last_report) = 'object'),
  closed_at timestamptz,
  closed_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  close_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT event_offline_nodes_close_ck CHECK (
    (status <> 'CLOSED') OR (closed_at IS NOT NULL AND length(btrim(close_reason)) > 0))
);
CREATE INDEX IF NOT EXISTS event_offline_nodes_event_idx ON event_offline_nodes (event_id, prepared_at);
-- Una base de nodo representa a UN nodo por evento.
CREATE UNIQUE INDEX IF NOT EXISTS event_offline_nodes_local_uq ON event_offline_nodes (event_id) WHERE local;

COMMENT ON TABLE event_offline_nodes IS
  'Nodos locales de un evento (030). En producción: uno por nodo preparado. En un nodo: su propia fila (local).';

ALTER TABLE event_offline_syncs
  ADD COLUMN IF NOT EXISTS node_id uuid REFERENCES event_offline_nodes(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS node_tls_certs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- Nombres del certificado, ordenados y separados por coma.
  names text NOT NULL,
  -- true = Let's Encrypt de pruebas (no confiable).
  staging boolean NOT NULL DEFAULT false,
  fullchain_pem text NOT NULL,
  -- La clave privada, cifrada (AES-256-GCM, clave derivada de NODO_CERT_SECRET / JWT_SECRET).
  privkey_enc text NOT NULL,
  not_after timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT node_tls_certs_names_uq UNIQUE (names, staging)
);

COMMENT ON TABLE node_tls_certs IS
  'Certificado TLS de los nombres del nodo local (030), reutilizado mientras le queden >30 días.';
