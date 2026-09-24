-- 019 · El ministerio del club como dato real.
--
-- Hasta ahora el ministerio de un club sólo existía como `organizations.metadata_json.ministry`
-- (un slug) y casi ningún club lo declaraba, así que la pestaña «Clases» ofrecía las clases de
-- todos los ministerios. Desde aquí es una columna con clave foránea: la regla 3 de ESTADO.md
-- (nada adivina un ministerio) la hace cumplir el API al crear un club; esta migración sólo
-- rellena lo que el propio club ya había declarado.
--
-- Idempotente: se puede aplicar dos veces sin efecto.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS ministry_id uuid NULL REFERENCES ministries(id);

-- Backfill: lo que el club declaró en metadata_json.ministry, si coincide con un ministerio.
-- Nunca pisa una columna ya rellenada ni inventa nada para quien no declaró.
UPDATE organizations o
   SET ministry_id = m.id
  FROM ministries m
 WHERE o.type = 'club'
   AND o.ministry_id IS NULL
   AND o.metadata_json ? 'ministry'
   AND m.slug = o.metadata_json->>'ministry';

-- Los clubes se filtran por ministerio (búsqueda, listas del admin); los demás nodos no lo usan.
CREATE INDEX IF NOT EXISTS organizations_club_ministry_idx
  ON organizations (ministry_id)
  WHERE type = 'club';
