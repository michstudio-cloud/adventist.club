-- 021 · Las firmas manuscritas quedan en el certificado emitido.
--
-- Hasta 020 la firma se imprimía al generar y se perdía: volver a descargar un certificado ya
-- emitido (portafolio, /verify/<folio>, POST /certificates/render con `certificate_no`) salía sin
-- firma. Regla del responsable: «para guardar el archivo pidamos que se registren» — sólo se guarda
-- cuando emite una persona con cuenta (portafolio, investidura, curso automático, asistente con
-- sesión). El asistente sin cuenta no guarda nada.
--
-- Cada columna es la URL pública de una COPIA inmutable en el bucket de medios
-- (`certificates/signatures/<id del certificado>/<uuid>.png`, PNG normalizado), nunca la firma
-- guardada en la cuenta (`users.signature_url`), que su dueño puede reemplazar o borrar.
-- La copia es del certificado: anular el certificado o borrar la cuenta firmante no la toca.
-- Fuera del hash (`canonical()` está congelado): añadir o no una firma no altera la verificación.
-- NULL = sin firma en esa línea.
--
-- Idempotente: se puede aplicar dos veces sin efecto.

ALTER TABLE certificates
  ADD COLUMN IF NOT EXISTS signature_director_url text NULL,
  ADD COLUMN IF NOT EXISTS signature_instructor_url text NULL;

COMMENT ON COLUMN certificates.signature_director_url IS
  'Firma del director(a) impresa al emitir: copia inmutable en certificates/signatures/<id>/ del bucket de medios.';
COMMENT ON COLUMN certificates.signature_instructor_url IS
  'Firma del instructor(a) impresa al emitir: copia inmutable en certificates/signatures/<id>/ del bucket de medios.';
