-- Reference data every new install needs. NEW databases only (scripts/db_bootstrap.sh).
-- Most of it is already seeded by the migrations (001: Pathfinder honor categories; 012:
-- adventurers + master-guides); this file makes the guarantee explicit and idempotent, so a
-- future migration that stops seeding cannot leave a node without its ministries.
INSERT INTO ministries (slug, name) VALUES
  ('pathfinders', 'Pathfinders'),
  ('adventurers', 'Adventurers'),
  ('master-guides', 'Master Guides')
ON CONFLICT (slug) DO NOTHING;

DO $$
BEGIN
  IF (SELECT count(*) FROM ministries WHERE slug IN ('pathfinders', 'adventurers', 'master-guides')) <> 3 THEN
    RAISE EXCEPTION 'minimal seed: ministries missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM honor_categories c JOIN ministries m ON m.id = c.ministry_id
                 WHERE m.slug = 'pathfinders') THEN
    RAISE EXCEPTION 'minimal seed: honor categories missing (migration 001 should seed them)';
  END IF;
END $$;

ANALYZE;
