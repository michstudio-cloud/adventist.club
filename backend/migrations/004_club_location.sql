-- 004_club_location.sql — additive, idempotent.
-- Clubs are found by location: bounding-box prefilter for GET /org-nodes/clubs/nearby.
CREATE INDEX IF NOT EXISTS ix_organizations_club_location
  ON organizations (latitude, longitude)
  WHERE type = 'club' AND latitude IS NOT NULL;
