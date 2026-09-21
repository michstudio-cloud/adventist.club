-- 003_club_signup.sql
-- Club sign-up with coordinator approval. Additive and idempotent.
--
-- A club director registers, creates their club as an `organizations` row of
-- type `club` with status `pending` under an association, and a coordinator
-- (COORDINATOR_ZONE / ADMIN_ASSOCIATION or above) approves or rejects it.
-- `organizations.status` gains the values `pending` and `rejected` for clubs
-- (the column is free text, no constraint change needed).

-- accent-insensitive association search ("asociacion" must find "Asociación")
CREATE EXTENSION IF NOT EXISTS unaccent;

ALTER TABLE users ADD COLUMN IF NOT EXISTS club_approval varchar(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS club_approval_reason text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS club_approval_at timestamptz;

COMMENT ON COLUMN users.club_approval IS
  'CLUB_DIRECTOR only: PENDING / APPROVED / REJECTED for the club they requested. NULL when not applicable.';

-- pending-clubs listing and public "active only" reads.
CREATE INDEX IF NOT EXISTS ix_organizations_type_status ON organizations (type, status);
-- Association search by name (ILIKE '%q%').
CREATE INDEX IF NOT EXISTS ix_organizations_type_name ON organizations (type, name);
