-- 008e_club_placement.sql
-- Bloque E, incremento E6 — Zona e iglesia del club.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §3 y §5.5
--
-- SÓLO ÍNDICES. La zona («zona / distrito») y la iglesia ya son nodos de
-- `organizations` (`type = 'zone'`, `'church'`, los dos ya en `ORG_HIERARCHY`):
-- de su `path` sale el alcance del coordinador de zona con el RBAC que ya
-- existe, así que no hace falta ninguna tabla ni columna nueva. La etiqueta
-- «distrito» viaja en `metadata_json.kind = 'district'`; no hay tipo nuevo.
--
-- Aditiva e idempotente: no borra, renombra ni reescribe nada.
-- Aplicar ANTES de desplegar el código.
--
-- ANTES DE APLICAR EN PRODUCCIÓN: comprobar que no hay zonas ni iglesias
-- duplicadas bajo un mismo padre, porque el índice único de abajo las
-- rechazaría. Hoy no hay zonas cargadas:
--
--   SELECT parent_id, lower(name), count(*)
--     FROM organizations
--    WHERE type IN ('zone','church') AND status = 'active'
--    GROUP BY 1, 2 HAVING count(*) > 1;

-- Las lecturas nuevas preguntan «qué cuelga de este nodo y de qué tipo»:
-- iglesias de una zona, clubes de una iglesia, clubes sin ubicar de una
-- asociación.
CREATE INDEX IF NOT EXISTS ix_organizations_parent_type ON organizations (parent_id, type);

-- Dos zonas (o dos iglesias) activas del mismo padre no comparten nombre: es
-- lo que convierte «Iglesia Central» en una sola fila que la asociación elige
-- en vez de duplicar.
CREATE UNIQUE INDEX IF NOT EXISTS organizations_zone_church_name_key
  ON organizations (parent_id, lower(name))
  WHERE type IN ('zone', 'church') AND status = 'active';
