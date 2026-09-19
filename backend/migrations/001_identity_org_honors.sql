-- 001_identity_org_honors.sql
-- Additive only: no table/column drops, no column rewrites, no data changes to
-- existing rows (except backfilling the new honors.status from honors.active).
-- The only DROPs are `DROP CONSTRAINT IF EXISTS` on CHECKs this file re-adds.
-- Brings the legacy MongoDB domain (users, org tree, specialties, guardianships,
-- email verifications, audit log) into the central multi-ministry schema.
-- Idempotent: safe to run more than once.

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Organizations: legacy org_nodes (DIVISION > UNION > ASSOCIATION > ZONE >
-- CHURCH > CLUB > UNIT) live in the existing organizations tree.
-- `path` replaces Mongo's hand-maintained path/path_ids; subtree RBAC becomes
-- `WHERE path <@ :user_path`.
-- ---------------------------------------------------------------------------
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS path ltree;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS city varchar(120);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS state varchar(120);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS country varchar(120);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS latitude double precision;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS longitude double precision;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS metadata_json jsonb;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS legacy_mongo_id varchar(24);

CREATE UNIQUE INDEX IF NOT EXISTS organizations_legacy_mongo_id_key
  ON organizations (legacy_mongo_id) WHERE legacy_mongo_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_organizations_path ON organizations USING gist (path);

-- ---------------------------------------------------------------------------
-- Users: one identity across every ministry app.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email citext NOT NULL UNIQUE,
  password_hash text NOT NULL,
  name varchar(180) NOT NULL,
  avatar_url text,
  role varchar(40) NOT NULL DEFAULT 'STUDENT'
    CHECK (role IN ('MASTER_GC','ADMIN_DIVISION','ADMIN_UNION','ADMIN_ASSOCIATION',
                    'COORDINATOR_ZONE','CLUB_DIRECTOR','INSTRUCTOR','STUDENT','PARENT_GUARDIAN')),
  organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  is_minor boolean NOT NULL DEFAULT false,
  birth_date date,
  mfa_secret text,
  mfa_enabled boolean NOT NULL DEFAULT false,
  verification_status varchar(20) NOT NULL DEFAULT 'PENDING'
    CHECK (verification_status IN ('PENDING','VERIFIED','REJECTED')),
  child_protection_completed boolean NOT NULL DEFAULT false,
  child_protection_completed_at timestamptz,
  status varchar(20) NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE','SUSPENDED','INACTIVE')),
  last_login timestamptz,
  legacy_mongo_id varchar(24) UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_organization_id ON users (organization_id);
CREATE INDEX IF NOT EXISTS ix_users_role ON users (role);

CREATE TABLE IF NOT EXISTS guardianships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  guardian_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  child_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  relationship varchar(20) NOT NULL DEFAULT 'PARENT'
    CHECK (relationship IN ('PARENT','LEGAL_GUARDIAN','OTHER')),
  consent_status varchar(20) NOT NULL DEFAULT 'PENDING'
    CHECK (consent_status IN ('PENDING','APPROVED','REJECTED')),
  consent_granted_at timestamptz,
  legacy_mongo_id varchar(24) UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (guardian_id, child_id),
  CHECK (guardian_id <> child_id)
);
CREATE INDEX IF NOT EXISTS ix_guardianships_child_id ON guardianships (child_id);

CREATE TABLE IF NOT EXISTS email_verifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token text NOT NULL UNIQUE,
  code varchar(6) NOT NULL,
  type varchar(30) NOT NULL CHECK (type IN ('email_verification','password_reset')),
  used boolean NOT NULL DEFAULT false,
  used_at timestamptz,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_email_verifications_user_type ON email_verifications (user_id, type);
CREATE INDEX IF NOT EXISTS ix_email_verifications_expires_at ON email_verifications (expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  action varchar(40) NOT NULL,
  entity_type varchar(40) NOT NULL,
  entity_id text NOT NULL,
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  user_email citext,
  user_role varchar(40),
  organization_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  details text,
  metadata_json jsonb,
  ip_address inet,
  legacy_mongo_id varchar(24) UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_log_entity ON audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_user_id ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log (created_at DESC);

-- ---------------------------------------------------------------------------
-- Honors: legacy "specialties" are honors. Existing columns stay untouched;
-- image_url is the patch image, source_url keeps catalogue provenance.
-- ---------------------------------------------------------------------------
ALTER TABLE honors ADD COLUMN IF NOT EXISTS code varchar(40);
ALTER TABLE honors ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS difficulty_level varchar(20);
ALTER TABLE honors ADD COLUMN IF NOT EXISTS honor_type varchar(20);
ALTER TABLE honors ADD COLUMN IF NOT EXISTS status varchar(30);
ALTER TABLE honors ADD COLUMN IF NOT EXISTS org_scope_id uuid REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS estimated_hours integer;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS exam_passing_score integer NOT NULL DEFAULT 80;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS exam_time_limit_minutes integer;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS max_exam_attempts integer NOT NULL DEFAULT 3;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS created_by_id uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS approved_zone_org_id uuid REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS approved_association_org_id uuid REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS thumbnail_url text;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS previous_version_id uuid REFERENCES honors(id) ON DELETE SET NULL;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS changes_description text;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS published_at timestamptz;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS legacy_mongo_id varchar(24);

-- Existing catalogue rows predate the review workflow: active ones are published.
UPDATE honors SET status = CASE WHEN active THEN 'PUBLISHED' ELSE 'ARCHIVED' END WHERE status IS NULL;
ALTER TABLE honors ALTER COLUMN status SET DEFAULT 'DRAFT';
ALTER TABLE honors ALTER COLUMN status SET NOT NULL;

-- Re-created on every run so the file stays idempotent (plain statements only:
-- some migration runners split on ';' and cannot handle DO blocks).
ALTER TABLE honors DROP CONSTRAINT IF EXISTS honors_status_check;
ALTER TABLE honors ADD CONSTRAINT honors_status_check
  CHECK (status IN ('DRAFT','ZONE_REVIEW','ASSOCIATION_REVIEW','PUBLISHED','ARCHIVED'));
ALTER TABLE honors DROP CONSTRAINT IF EXISTS honors_difficulty_level_check;
ALTER TABLE honors ADD CONSTRAINT honors_difficulty_level_check
  CHECK (difficulty_level IS NULL OR difficulty_level IN ('BEGINNER','INTERMEDIATE','ADVANCED'));
ALTER TABLE honors DROP CONSTRAINT IF EXISTS honors_honor_type_check;
ALTER TABLE honors ADD CONSTRAINT honors_honor_type_check
  CHECK (honor_type IS NULL OR honor_type IN ('OFFICIAL_GC','DIVISIONAL','LOCAL'));

-- The unique `code` index that Mongo never actually created.
CREATE UNIQUE INDEX IF NOT EXISTS honors_ministry_id_code_key
  ON honors (ministry_id, code) WHERE code IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS honors_legacy_mongo_id_key
  ON honors (legacy_mongo_id) WHERE legacy_mongo_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_honors_status ON honors (status);
CREATE INDEX IF NOT EXISTS ix_honors_category_id ON honors (category_id);
CREATE INDEX IF NOT EXISTS ix_honors_created_by_id ON honors (created_by_id);
CREATE INDEX IF NOT EXISTS ix_honors_name_trgm ON honors USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS honor_requirements (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  honor_id uuid NOT NULL REFERENCES honors(id) ON DELETE CASCADE,
  position integer NOT NULL DEFAULT 0,
  description text NOT NULL,
  is_theoretical boolean NOT NULL DEFAULT true,
  instructions text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_honor_requirements_honor ON honor_requirements (honor_id, position);

CREATE TABLE IF NOT EXISTS honor_questions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  requirement_id uuid NOT NULL REFERENCES honor_requirements(id) ON DELETE CASCADE,
  position integer NOT NULL DEFAULT 0,
  question_text text NOT NULL,
  question_type varchar(20) NOT NULL
    CHECK (question_type IN ('MULTIPLE_CHOICE','TRUE_FALSE','SHORT_ANSWER','ESSAY')),
  options jsonb,
  correct_answer text NOT NULL,
  points integer NOT NULL DEFAULT 1,
  explanation text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_honor_questions_requirement ON honor_questions (requirement_id, position);

CREATE TABLE IF NOT EXISTS honor_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  honor_id uuid NOT NULL REFERENCES honors(id) ON DELETE CASCADE,
  reviewer_id uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewer_name varchar(180),
  reviewer_role varchar(40),
  action varchar(20) NOT NULL CHECK (action IN ('APPROVE','REJECT','REQUEST_CHANGES')),
  comments text,
  reviewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_honor_reviews_honor ON honor_reviews (honor_id, reviewed_at);

CREATE TABLE IF NOT EXISTS honor_resources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  honor_id uuid NOT NULL REFERENCES honors(id) ON DELETE CASCADE,
  position integer NOT NULL DEFAULT 0,
  name varchar(255) NOT NULL,
  url text NOT NULL,
  type varchar(40),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_honor_resources_honor ON honor_resources (honor_id, position);

-- Official Pathfinder honor taxonomy (GC/NAD), previously a Python enum.
INSERT INTO honor_categories (ministry_id, name, slug)
SELECT m.id, c.name, c.slug
FROM ministries m
CROSS JOIN (VALUES
  ('Artes y Habilidades Manuales', 'arts-crafts-hobbies'),
  ('Salud y Ciencia',              'health-science'),
  ('Artes Domésticas',             'household-arts'),
  ('Naturaleza',                   'nature'),
  ('Industrias al Aire Libre',     'outdoor-industries'),
  ('Recreación',                   'recreation'),
  ('Crecimiento Espiritual',       'spiritual-growth'),
  ('Vocacional',                   'vocational')
) AS c(name, slug)
WHERE m.slug = 'pathfinders'
ON CONFLICT (ministry_id, slug) DO NOTHING;
