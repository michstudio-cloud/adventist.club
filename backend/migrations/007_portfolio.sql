-- 007_portfolio.sql
-- Bloque A — Portafolio: enrollment in an honor, progress per requirement, private
-- evidence, review verdicts and the certificate linked to the account.
-- Spec: docs/superpowers/specs/2026-09-22-portafolio-design.md
-- Additive only and idempotent: new tables, new NULLable columns on certificates,
-- new indexes. Nothing is dropped, rewritten or backfilled.
-- Apply BEFORE deploying the code that maps these tables.

-- ---------------------------------------------------------------------------
-- One row per person and honor version. `honor_id` pins the published version
-- the member enrolled in; `locale` is the requirement list they see.
-- `club_id` follows the member's current club while the enrollment is open and
-- stays frozen once certified. COURSE (+ course_id) arrives with Bloque B.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS honor_enrollments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  honor_id uuid NOT NULL REFERENCES honors(id),
  mode varchar(10) NOT NULL DEFAULT 'CLUB'
    CHECK (mode IN ('CLUB','COURSE')),
  club_id uuid REFERENCES organizations(id) ON DELETE SET NULL,
  locale varchar(16) NOT NULL DEFAULT 'es',
  status varchar(15) NOT NULL DEFAULT 'IN_PROGRESS'
    CHECK (status IN ('IN_PROGRESS','READY','CERTIFIED','WITHDRAWN')),
  certificate_id uuid REFERENCES certificates(id) ON DELETE SET NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  ready_at timestamptz,
  certified_at timestamptz,
  withdrawn_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- One live enrollment per person and honor; withdrawn ones are history.
CREATE UNIQUE INDEX IF NOT EXISTS honor_enrollments_user_honor_active_key
  ON honor_enrollments (user_id, honor_id) WHERE status <> 'WITHDRAWN';
CREATE INDEX IF NOT EXISTS ix_honor_enrollments_club_status ON honor_enrollments (club_id, status);
CREATE INDEX IF NOT EXISTS ix_honor_enrollments_user_status ON honor_enrollments (user_id, status);

-- ---------------------------------------------------------------------------
-- All rows are created at enrollment. `requirement_position` is the key that is
-- stable across languages; `requirement_id` is only the row the member saw.
-- `is_practical` is copied so a later change of honor_requirements.is_theoretical
-- cannot alter an enrollment in progress.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS requirement_progress (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enrollment_id uuid NOT NULL REFERENCES honor_enrollments(id) ON DELETE CASCADE,
  requirement_position integer NOT NULL,
  requirement_id uuid REFERENCES honor_requirements(id) ON DELETE SET NULL,
  is_practical boolean NOT NULL,
  status varchar(12) NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','SUBMITTED','COMPLETE','INCOMPLETE')),
  completed_via varchar(8)
    CHECK (completed_via IN ('REVIEW','EXAM')),
  member_note text CHECK (char_length(member_note) <= 2000),
  submitted_at timestamptz,
  reviewed_by_id uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at timestamptz,
  review_note text CHECK (char_length(review_note) <= 2000),
  UNIQUE (enrollment_id, requirement_position)
);

-- The review queue reads only what is waiting for a verdict.
CREATE INDEX IF NOT EXISTS ix_requirement_progress_submitted
  ON requirement_progress (enrollment_id) WHERE status = 'SUBMITTED';

-- ---------------------------------------------------------------------------
-- Evidence lives in the PRIVATE bucket: `storage_key` is a key, never a URL.
-- Removal is logical; migrations/purge_removed_evidence.py deletes the objects.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidences (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  progress_id uuid NOT NULL REFERENCES requirement_progress(id) ON DELETE CASCADE,
  uploaded_by_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind varchar(8) NOT NULL,
  status varchar(15) NOT NULL DEFAULT 'PENDING_UPLOAD'
    CHECK (status IN ('PENDING_UPLOAD','ACTIVE','REMOVED')),
  storage_key text NOT NULL UNIQUE,
  content_type varchar(40) NOT NULL,
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  sha256 char(64),
  taken_on date,
  place varchar(180),
  caption varchar(500),
  created_at timestamptz NOT NULL DEFAULT now(),
  removed_at timestamptz,
  -- when purge_removed_evidence.py deleted the object, so it is not retried forever
  purged_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_evidences_progress_status ON evidences (progress_id, status);
-- For the purge script: removed or never-finished uploads by age.
CREATE INDEX IF NOT EXISTS ix_evidences_purge
  ON evidences (status, created_at) WHERE status <> 'ACTIVE' AND purged_at IS NULL;

-- ---------------------------------------------------------------------------
-- Certificates issued from a portfolio point at the account, the enrollment and
-- whoever issued them. Certificates issued before stay unlinked (all NULL).
-- These columns are NOT part of the certificate hash, so existing hashes verify.
-- ---------------------------------------------------------------------------
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS enrollment_id uuid REFERENCES honor_enrollments(id) ON DELETE SET NULL;
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS issued_by_id uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS issued_role varchar(40);

CREATE INDEX IF NOT EXISTS ix_certificates_user ON certificates (user_id) WHERE user_id IS NOT NULL;
-- An enrollment is certified once.
CREATE UNIQUE INDEX IF NOT EXISTS certificates_enrollment_id_key
  ON certificates (enrollment_id) WHERE enrollment_id IS NOT NULL;
