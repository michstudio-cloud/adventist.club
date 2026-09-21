-- 008g_notify_progress.sql
-- Bloque E, incremento E9 — Correos de avance del portafolio.
-- Spec: docs/superpowers/specs/2026-09-22-membresia-de-club-design.md §3 y §5.10
--
-- Aditiva e idempotente: UNA columna con valor por defecto, así que ninguna
-- fila existente cambia de comportamiento (todo el mundo sigue recibiendo los
-- avisos de avance hasta que decida lo contrario).
-- Aplicar ANTES de desplegar el código.
--
-- Los correos de SEGURIDAD (MFA), de invitación, de consentimiento y de
-- decisión de membresía NO se pueden apagar: esta preferencia sólo silencia
-- los avisos de avance (dictamen incompleto, lista para certificar, certificado
-- emitido). El tope de uno por inscripción cada 12 h vive en `notification_log`,
-- que ya existe desde `008c`.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS notify_progress boolean NOT NULL DEFAULT true;
