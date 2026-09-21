-- 008_mfa_recovery.sql
-- Bloque E, incremento E1 — 2FA obligatorio para MASTER_GC: códigos de recuperación.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §5.8
-- Aditiva e idempotente: una tabla nueva y sus índices. No borra, renombra ni
-- reescribe nada. Aplicar ANTES de desplegar el código que la mapea.
--
-- El código se guarda SÓLO como SHA-256 en hexadecimal (64 caracteres) y se
-- muestra una única vez al titular. `used_at` marca el consumo: las filas se
-- conservan para que la auditoría pueda explicar un acceso.

CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash char(64) NOT NULL UNIQUE,
  used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- El canje sólo mira los códigos vivos de una persona.
CREATE INDEX IF NOT EXISTS ix_mfa_recovery_codes_user_unused
  ON mfa_recovery_codes (user_id) WHERE used_at IS NULL;
