-- 022 · Perfil del club: varios ministerios, dirección de Google Places y logo.
--
-- 1. Un club puede tener TODOS los ministerios (Conquistadores, Aventureros, Guías Mayores,
--    Jóvenes…). La verdad pasa a ser la tabla puente `organization_ministries`.
--    `organizations.ministry_id` (019) se CONSERVA como «ministerio principal»: es DERIVADO
--    — el primero que se eligió — y lo escribe el API cada vez que cambia la lista
--    (`app/services/ministries.py`, `set_club_ministries`). Nada nuevo debe leerlo como la
--    lista completa: sólo lo usan el color de la inicial del logo y los clientes anteriores.
-- 2. Dirección elegida con Google Places (o escrita / pegada como enlace): `address` (texto
--    formateado), `place_id` (id estable de Google) y `maps_url` (enlace para abrirla). Las
--    coordenadas siguen en `latitude/longitude`, que es lo que lee el mapa.
-- 3. `logo_url`: el logo del club (bucket público, `clubs/<id>/logo-<hash>.webp`). Hasta hoy
--    vivía en `metadata_json.profile.logo_url` (bloque H): se copia aquí y la columna manda.
--
-- Idempotente: se puede aplicar dos veces sin efecto.

CREATE TABLE IF NOT EXISTS organization_ministries (
  organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  ministry_id     uuid        NOT NULL REFERENCES ministries(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (organization_id, ministry_id)
);

COMMENT ON TABLE organization_ministries IS
  'Ministerios de un club (022). organizations.ministry_id es el principal, derivado de aquí.';

-- «Clubes de Aventureros»: el filtro `?ministry=` entra por el ministerio.
CREATE INDEX IF NOT EXISTS organization_ministries_ministry_idx
  ON organization_ministries (ministry_id);

-- Relleno: cada club con ministerio principal tiene su fila. Nunca duplica ni inventa nada.
INSERT INTO organization_ministries (organization_id, ministry_id, created_at)
SELECT o.id, o.ministry_id, o.created_at
  FROM organizations o
 WHERE o.type = 'club'
   AND o.ministry_id IS NOT NULL
ON CONFLICT (organization_id, ministry_id) DO NOTHING;

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS address  text         NULL,
  ADD COLUMN IF NOT EXISTS place_id varchar(255) NULL,
  ADD COLUMN IF NOT EXISTS maps_url text         NULL,
  ADD COLUMN IF NOT EXISTS logo_url text         NULL;

COMMENT ON COLUMN organizations.address IS 'Dirección formateada del lugar de reunión (Google Places o texto).';
COMMENT ON COLUMN organizations.place_id IS 'Google Places place_id del lugar de reunión, si se eligió con el autocompletado.';
COMMENT ON COLUMN organizations.maps_url IS 'Enlace para abrir el lugar en Google Maps.';
COMMENT ON COLUMN organizations.logo_url IS 'Logo del club (bucket público de medios).';

-- El logo del bloque H: de metadata_json.profile.logo_url a la columna, sin pisar uno nuevo.
UPDATE organizations
   SET logo_url = metadata_json->'profile'->>'logo_url'
 WHERE type = 'club'
   AND logo_url IS NULL
   AND jsonb_typeof(metadata_json->'profile') = 'object'
   AND coalesce(metadata_json->'profile'->>'logo_url', '') <> '';
