-- NEW DATABASES ONLY (tests, the event node): never run against production.
--
-- Legacy tables that pre-date migrations/001: in production (Neon) they come from the old
-- platform and no numbered migration creates them. This is their minimal pre-001 shape, taken
-- from app/models.py (only the columns NOT annotated as added by a migration); the migrations
-- add everything else. `UNIQUE (ministry_id, slug)` on categories is what 001's ON CONFLICT uses.
-- Applied by scripts/db_bootstrap.sh before 010_certificates_base.sql and migrations/001.
-- Idempotent (IF NOT EXISTS).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS ministries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug varchar(60) NOT NULL UNIQUE,
  name varchar(120) NOT NULL,
  status varchar(20) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS organizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id uuid REFERENCES organizations(id),
  type varchar(40) NOT NULL,
  name varchar(180) NOT NULL,
  code varchar(60),
  status varchar(20) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS honor_categories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ministry_id uuid REFERENCES ministries(id),
  name varchar(120) NOT NULL,
  slug varchar(120) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ministry_id, slug)
);

CREATE TABLE IF NOT EXISTS honors (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ministry_id uuid REFERENCES ministries(id),
  category_id uuid REFERENCES honor_categories(id),
  name varchar(180) NOT NULL,
  slug varchar(180) NOT NULL,
  image_url text,
  source_url text,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- The ministry that exists before 001 (001 seeds the Pathfinder honor categories from it;
-- 012 adds adventurers and master-guides).
INSERT INTO ministries (slug, name) SELECT 'pathfinders', 'Pathfinders'
WHERE NOT EXISTS (SELECT 1 FROM ministries WHERE slug = 'pathfinders');
