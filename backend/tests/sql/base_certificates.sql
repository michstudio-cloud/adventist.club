-- TEST DATABASES ONLY. The certificate tables pre-date the numbered migrations: in
-- production (Neon) they already exist and no file in migrations/ creates them.
-- This is their shape as mapped in app/models.py, so a local Postgres can run the
-- certificate and portfolio tests. Apply it BEFORE migrations/007_portfolio.sql.
-- Idempotent (IF NOT EXISTS); it never touches an existing table.

CREATE TABLE IF NOT EXISTS applications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ministry_id uuid REFERENCES ministries(id),
  name varchar(160) NOT NULL,
  slug varchar(80) NOT NULL UNIQUE,
  domain varchar(255),
  status varchar(20) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clubs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  ministry_id uuid NOT NULL REFERENCES ministries(id),
  name varchar(180) NOT NULL,
  code varchar(60),
  status varchar(20) NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS certificate_templates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ministry_id uuid REFERENCES ministries(id),
  organization_id uuid REFERENCES organizations(id),
  name varchar(180) NOT NULL,
  width double precision NOT NULL,
  height double precision NOT NULL,
  unit varchar(8) NOT NULL DEFAULT 'in',
  orientation varchar(20) NOT NULL DEFAULT 'landscape',
  bleed double precision NOT NULL DEFAULT 0,
  safe_margin double precision NOT NULL DEFAULT 0,
  supports_svg boolean NOT NULL DEFAULT false,
  background_url text,
  layout_json jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS certificates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id uuid REFERENCES applications(id),
  ministry_id uuid NOT NULL REFERENCES ministries(id),
  organization_id uuid NOT NULL REFERENCES organizations(id),
  club_id uuid REFERENCES clubs(id),
  honor_id uuid REFERENCES honors(id),
  template_id uuid NOT NULL REFERENCES certificate_templates(id),
  certificate_no varchar(80) NOT NULL UNIQUE,
  recipient_name varchar(180) NOT NULL,
  club_name_snapshot varchar(180),
  honor_name_snapshot varchar(180) NOT NULL,
  issued_date date NOT NULL,
  place varchar(180),
  instructor_name varchar(180),
  director_name varchar(180),
  status varchar(30) NOT NULL DEFAULT 'issued',
  certificate_hash varchar(64),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS certificate_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  certificate_id uuid NOT NULL REFERENCES certificates(id),
  event_type varchar(40) NOT NULL,
  actor_id uuid,
  metadata_json jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
