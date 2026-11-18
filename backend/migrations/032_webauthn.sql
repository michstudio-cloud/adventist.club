-- 032_webauthn.sql
-- Passkeys (WebAuthn): entrar sin contraseña y segundo factor. Plan: docs/PASSKEYS.md.
--
-- QUÉ CAMBIA (aditiva; no borra, no renombra, no reescribe filas; NO toca `users`):
--   webauthn_credentials  una fila por passkey registrada. Guarda la llave PÚBLICA (COSE): no es un
--                         secreto, la privada nunca sale del dispositivo o del llavero de la persona.
--   webauthn_challenges   el reto de cada ceremonia (alta o entrada), de UN solo uso y con caducidad.
--                         Se reclama con un UPDATE … RETURNING, igual que los códigos de recuperación.
--
-- `users.mfa_enabled` y `users.mfa_secret` conservan su significado (TOTP). El paquete del nodo del
-- evento no exporta estas tablas: en el nodo se entra sólo con contraseña, como hasta ahora.
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, después de 031. Idempotente. Mientras `WEBAUTHN_RP_ID` no
-- tenga valor el código no lee ni escribe estas tablas.
-- Vuelta atrás (el código anterior las ignora; se pierden las passkeys registradas):
--   DROP TABLE IF EXISTS webauthn_challenges;
--   DROP TABLE IF EXISTS webauthn_credentials;

CREATE TABLE IF NOT EXISTS webauthn_credentials (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Identificador que elige el autenticador (hasta 1023 bytes según la norma).
  credential_id bytea NOT NULL UNIQUE,
  public_key bytea NOT NULL,
  -- Contador de firmas. Las passkeys sincronizadas (iCloud, Google) reportan siempre 0.
  sign_count bigint NOT NULL DEFAULT 0,
  -- Modelo del autenticador: sólo para etiquetar («Llavero de iCloud», «Google», …).
  aaguid uuid,
  transports text[] NOT NULL DEFAULT '{}',
  -- Sincronizable entre dispositivos / ya respaldada en la nube del proveedor.
  backup_eligible boolean NOT NULL DEFAULT false,
  backed_up boolean NOT NULL DEFAULT false,
  name varchar(60) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_webauthn_credentials_user ON webauthn_credentials (user_id);

CREATE TABLE IF NOT EXISTS webauthn_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  challenge bytea NOT NULL,
  purpose varchar(12) NOT NULL CHECK (purpose IN ('register', 'login')),
  -- De quién es la ceremonia. NULL en la entrada sin contraseña: todavía no se sabe quién es.
  user_id uuid REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  used_at timestamptz
);

-- La limpieza de retos vencidos (al crear uno nuevo) recorre por caducidad.
CREATE INDEX IF NOT EXISTS ix_webauthn_challenges_expires ON webauthn_challenges (expires_at);
