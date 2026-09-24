-- 018_onboarding.sql
-- «Primer uso guiado»: cuándo terminó (o saltó) la persona la guía de bienvenida.
--
-- Añade UNA columna a `users`: `onboarding_completed_at` (timestamptz, NULL = todavía no).
-- La escribe sólo `PATCH /api/v1/users/me/onboarding` (terminar o «saltar» la marcan igual) y
-- la expone `GET /auth/me`. La app muestra `/bienvenida` a un conquistador cuando es NULL.
--
-- Las cuentas que YA existen quedan marcadas con su fecha de alta: la guía es para quien se
-- registra a partir de ahora, no para interrumpir a quien lleva meses usando la app. Sólo se
-- tocan filas con la columna en NULL y creadas antes de esta migración, así que ejecutarla dos
-- veces no cambia nada (idempotente) y nunca «desmarca» a nadie.
--
-- Aplicar ANTES de desplegar el código, y DESPUÉS de 016_certificate_locale.sql (el 017 es
-- independiente).
-- Vuelta atrás: `ALTER TABLE users DROP COLUMN onboarding_completed_at;`

ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at timestamptz;

-- Relleno de las cuentas anteriores, SOLO la primera vez: el comentario de la columna hace de
-- marca de «ya se rellenó», para que una segunda ejecución no marque a quien se registró entre
-- una y otra (esa persona todavía tiene que ver la guía).
DO $$
BEGIN
  IF col_description('users'::regclass,
       (SELECT attnum FROM pg_attribute
         WHERE attrelid = 'users'::regclass AND attname = 'onboarding_completed_at')) IS NULL THEN
    UPDATE users SET onboarding_completed_at = created_at WHERE onboarding_completed_at IS NULL;
  END IF;
END $$;

COMMENT ON COLUMN users.onboarding_completed_at IS
  'Primer uso guiado (/bienvenida) terminado o saltado. NULL = pendiente. Cuentas anteriores a 018: su created_at.';
