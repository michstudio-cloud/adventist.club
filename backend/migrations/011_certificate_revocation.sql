-- 011_certificate_revocation.sql
-- Bloque D · I7 — La anulación de un certificado ya emitido.
-- Spec: docs/superpowers/specs/2026-09-22-cursos-examenes-certificacion-design.md §5.5
--
-- Añade TRES columnas a `certificates`: `revoked_at`, `revoked_by_id` y
-- `revocation_reason`. Aditiva e idempotente: no borra, renombra ni reescribe nada, y
-- ninguna fila existente cambia de valor (las tres nacen NULL, que es exactamente lo que
-- significa «no anulado»). Aplicar ANTES de desplegar el código, y DESPUÉS de 010b.
--
-- Anular NO es borrar: la fila del certificado se conserva entera, con su folio, su hash y
-- su historia en `certificate_events`. Lo único que cambia es `status`, que pasa a
-- 'revoked', y la verificación pública — que ya responde «no válido» a todo lo que no sea
-- 'issued' — pasa a decir «revocado» con su fecha.
--
-- Ninguna de las tres columnas entra en `canonical()` (app/services/certificates.py), de
-- modo que el hash de un certificado anulado sigue siendo el mismo: verifica como
-- «revocado», nunca como «modificado», y los certificados ya emitidos siguen verificando.

ALTER TABLE certificates ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS revoked_by_id uuid REFERENCES users(id);
ALTER TABLE certificates ADD COLUMN IF NOT EXISTS revocation_reason text;

-- La cola de «qué anuló esta Asociación» y las consultas de auditoría.
CREATE INDEX IF NOT EXISTS ix_certificates_revoked
  ON certificates (revoked_at DESC) WHERE revoked_at IS NOT NULL;

COMMENT ON COLUMN certificates.revoked_at IS
  'Bloque D I7: cuándo se anuló. NULL = vigente. Anular nunca borra la fila.';
COMMENT ON COLUMN certificates.revocation_reason IS
  'Bloque D I7: motivo obligatorio de la anulación; no se muestra en la verificación pública.';
