-- 016_certificate_locale.sql
-- El idioma en que se emitió un certificado.
--
-- Añade UNA columna a `certificates`: `locale` (es | en | pt | fr, lo que hable la
-- plantilla elegida). Al emitir desde el portafolio se guarda el idioma que eligió quien
-- emite; `POST /certificates/render` con folio lo usa por defecto cuando no se pide otro.
--
-- Aditiva e idempotente: no borra, renombra ni reescribe nada. Las filas existentes quedan
-- en 'es', que es el idioma en que se imprimían hasta hoy. `locale` NO entra en
-- `canonical()` (app/services/certificates.py): el hash de todo certificado ya emitido
-- sigue siendo el mismo y sigue verificando.
-- Aplicar ANTES de desplegar el código, y DESPUÉS de 015_secretaria.sql.
-- Vuelta atrás: `ALTER TABLE certificates DROP COLUMN locale;`

ALTER TABLE certificates ADD COLUMN IF NOT EXISTS locale varchar(8) NOT NULL DEFAULT 'es';

COMMENT ON COLUMN certificates.locale IS
  'Idioma en que se emitió (es|en|pt|fr, según la plantilla). Fuera del hash. Filas anteriores a 016: es.';
