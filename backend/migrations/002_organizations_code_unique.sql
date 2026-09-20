CREATE UNIQUE INDEX IF NOT EXISTS organizations_code_key ON organizations (code) WHERE code IS NOT NULL;
