-- 020 · La firma manuscrita de una cuenta.
--
-- Una imagen (PNG con fondo transparente) que el asistente «Crear certificado» pone sobre la
-- línea de firma del director(a) o del instructor(a). Sin cuenta la firma vive sólo en la
-- petición de ese certificado; con cuenta se guarda aquí para reutilizarla.
--
-- Guarda la URL pública del objeto en el bucket de medios (`signatures/<uuid>.png`, clave de
-- 128 bits sin listado). Sólo la escribe `POST|DELETE /api/v1/users/me/signature`, y sólo la
-- devuelven `GET /auth/me` y `GET /users/me` (nunca listados ni perfiles de otras personas).
-- NULL = sin firma guardada.
--
-- Idempotente: se puede aplicar dos veces sin efecto.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS signature_url text NULL;

COMMENT ON COLUMN users.signature_url IS
  'Firma manuscrita guardada (URL del bucket de medios, carpeta signatures/). Sólo la ve su dueño.';
