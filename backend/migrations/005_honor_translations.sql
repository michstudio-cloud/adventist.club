-- 005_honor_translations.sql — additive, idempotent.
-- honors.name / honor_categories.name stay as the source text (Spanish); other languages live per row here.
CREATE TABLE IF NOT EXISTS honor_translations (
  honor_id    uuid NOT NULL REFERENCES honors(id) ON DELETE CASCADE,
  locale      varchar(16) NOT NULL,            -- BCP 47: en, pt-BR, fr, de, uk…
  name        varchar(180) NOT NULL,
  description text,
  source      varchar(40),                     -- e.g. pathfinder-wiki
  source_url  text,
  license     varchar(40),                     -- e.g. CC BY-SA 3.0 (attribution is mandatory)
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (honor_id, locale)
);
CREATE INDEX IF NOT EXISTS ix_honor_translations_locale ON honor_translations (locale);
CREATE INDEX IF NOT EXISTS ix_honor_translations_name_trgm ON honor_translations USING gin (name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS honor_category_translations (
  category_id uuid NOT NULL REFERENCES honor_categories(id) ON DELETE CASCADE,
  locale      varchar(16) NOT NULL,
  name        varchar(120) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (category_id, locale)
);

-- Link to the official source (wiki.pathfindersonline.org, "AY Honors/<wiki_title>") and its public facts.
ALTER TABLE honors ADD COLUMN IF NOT EXISTS wiki_title      varchar(200);
ALTER TABLE honors ADD COLUMN IF NOT EXISTS authority       varchar(10);   -- GC, NAD, SAD…
ALTER TABLE honors ADD COLUMN IF NOT EXISTS skill_level     smallint;
ALTER TABLE honors ADD COLUMN IF NOT EXISTS year_introduced smallint;
CREATE INDEX IF NOT EXISTS ix_honors_wiki_title ON honors (wiki_title) WHERE wiki_title IS NOT NULL;
