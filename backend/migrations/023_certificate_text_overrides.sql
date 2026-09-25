-- 023 · Frases editables del certificado.
--
-- Pedido del responsable (24 sep 2026): «Se otorga el presente certificado a:» y «por haber
-- cumplido satisfactoriamente los requisitos de la especialidad:» se pueden cambiar, pero SÓLO
-- quien tiene cuenta (asistente con sesión, emisión desde el portafolio, investidura del club).
-- Sin cuenta el certificado lleva las frases de la plantilla.
--
-- `text_overrides` guarda lo que se imprimió en lugar de esas frases: `{clave: texto}` con las
-- claves que la plantilla declara en su meta.json (`editable_strings`: `awarded`, `completion`,
-- o `t_awarded_to` / `t_for_completing` en la investidura). Sólo las que cambiaron: NULL = las
-- de la plantilla. Toda descarga posterior por folio (portafolio, /verify/<folio>,
-- POST /certificates/render con `certificate_no`) imprime estas y no las que mande quien pide.
-- Fuera del hash (`canonical()` está congelado): cambiar una frase no altera la verificación.
-- El evento `issued` registra qué claves cambiaron, nunca el texto.
--
-- Aditiva e idempotente: se puede aplicar dos veces sin efecto. Aplicar ANTES de desplegar el
-- código, y DESPUÉS de 022_club_ministries.sql.
-- Vuelta atrás: `ALTER TABLE certificates DROP COLUMN text_overrides;`

ALTER TABLE certificates ADD COLUMN IF NOT EXISTS text_overrides jsonb NULL;

COMMENT ON COLUMN certificates.text_overrides IS
  'Frases fijas de la plantilla reescritas al emitir con cuenta ({clave: texto}, claves de editable_strings del meta.json). NULL = las de la plantilla. Fuera del hash.';
