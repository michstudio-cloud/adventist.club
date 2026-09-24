-- 017_notifications.sql
-- «Avisos»: la bandeja in-app de cada persona (la campana de conquistadores.app).
--
-- QUÉ CAMBIA: crea UNA tabla nueva, `notifications`, y nada más. No toca ninguna tabla
-- existente, no borra, no renombra y no reescribe ninguna fila.
--
-- Una fila por aviso y persona. El mismo evento que manda un correo (portafolio, horas,
-- clases, XP, solicitudes, cartas…) deja aquí su fila; la fábrica común vive en
-- app/services/notifications.py. `notification_log` (008c) sigue siendo el registro de
-- CORREOS y el tope de 12 h; esta tabla es lo que la persona lee en la app.
--
-- `kind` + `data` permiten a la app pintar el aviso en el idioma de quien lo lee;
-- `title` y `body` son el texto en español que queda como respaldo. `link` es SIEMPRE una
-- ruta relativa de la app (`/portfolio/...`), nunca una URL externa. Mientras un aviso
-- sigue sin leer, uno nuevo del mismo tipo y sobre la misma entidad lo actualiza (`count`
-- sube) en vez de apilar filas: el director que recibe diez envíos ve UN aviso con «10».
--
-- APLICAR ANTES DE DESPLEGAR EL CÓDIGO, y DESPUÉS de 016_certificate_locale.sql.
-- Idempotente. Vuelta atrás: `DROP TABLE notifications;` (sólo se pierde la bandeja).

CREATE TABLE IF NOT EXISTS notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- De quién es la bandeja. En cascada: borrar la cuenta borra sus avisos.
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind varchar(40) NOT NULL,
  title varchar(200) NOT NULL,
  body text,
  link varchar(500) CHECK (link IS NULL OR (link LIKE '/%' AND link NOT LIKE '//%')),
  entity_type varchar(40),
  entity_id text,
  -- Cuántos eventos resume este aviso (sube mientras sigue sin leer).
  count integer NOT NULL DEFAULT 1 CHECK (count >= 1),
  -- Parámetros para pintarlo en otro idioma: nombre de la especialidad, horas, puntos…
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- La bandeja se lee siempre de la más reciente a la más antigua.
CREATE INDEX IF NOT EXISTS notifications_user_created_idx
  ON notifications (user_id, created_at DESC);
-- El contador de la campana y la fusión de avisos sin leer.
CREATE INDEX IF NOT EXISTS notifications_user_unread_idx
  ON notifications (user_id, kind, entity_id) WHERE read_at IS NULL;

COMMENT ON TABLE notifications IS
  'Bandeja in-app («Avisos»). Una fila por aviso y persona; se fusionan los no leídos del mismo tipo y entidad.';
