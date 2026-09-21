-- 006_requirement_locale.sql — additive, idempotent.
-- A requirement list exists once per language. Rows written by instructors keep locale 'es' and no source;
-- rows imported from the official wiki carry source / source_url / license (CC BY-SA: attribution is mandatory).
ALTER TABLE honor_requirements ADD COLUMN IF NOT EXISTS locale     varchar(16) NOT NULL DEFAULT 'es';
ALTER TABLE honor_requirements ADD COLUMN IF NOT EXISTS source     varchar(40);
ALTER TABLE honor_requirements ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE honor_requirements ADD COLUMN IF NOT EXISTS license    varchar(40);
CREATE INDEX IF NOT EXISTS ix_honor_requirements_honor_locale ON honor_requirements (honor_id, locale, position);
