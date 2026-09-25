-- 027_event_branding.sql
-- Marca ligera por evento (spec docs/superpowers/specs/2026-09-24-eventos.md §4: «marca ligera
-- del evento»), configurable desde el panel de coordinación.
--
-- QUÉ CAMBIA: añade TRES columnas opcionales a `events`. No toca otras tablas, no borra, no
-- renombra y no reescribe filas (todas quedan en NULL = la marca por defecto de la plataforma).
--
--   brand_logo_url  logo del evento (bucket público, `events/<id>/brand-<hash>.<ext>`)
--   brand_color     color principal, hex #RRGGBB
--   brand_accent    color de acento, hex #RRGGBB
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 026. Idempotente.
-- Vuelta atrás: ALTER TABLE events DROP COLUMN brand_logo_url, DROP COLUMN brand_color,
--   DROP COLUMN brand_accent;

ALTER TABLE events
  ADD COLUMN IF NOT EXISTS brand_logo_url text,
  ADD COLUMN IF NOT EXISTS brand_color varchar(7),
  ADD COLUMN IF NOT EXISTS brand_accent varchar(7);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'events_brand_color_ck') THEN
    ALTER TABLE events ADD CONSTRAINT events_brand_color_ck
      CHECK (brand_color IS NULL OR brand_color ~ '^#[0-9A-Fa-f]{6}$');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'events_brand_accent_ck') THEN
    ALTER TABLE events ADD CONSTRAINT events_brand_accent_ck
      CHECK (brand_accent IS NULL OR brand_accent ~ '^#[0-9A-Fa-f]{6}$');
  END IF;
END;
$$;
