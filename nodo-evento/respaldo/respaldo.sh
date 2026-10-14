#!/bin/sh
# Respaldo automático del nodo: pg_dump (formato custom, comprimido) cada RESPALDO_CADA_MIN
# minutos a /respaldos (montado desde RESPALDO_DIR: USB o segunda unidad). Escribe primero a un
# temporal y luego renombra: un corte de luz nunca deja un respaldo a medias con nombre válido.
# Conserva los RESPALDO_CONSERVAR más recientes y deja `ultimo.dump` apuntando al último.
set -u
EVERY="${RESPALDO_CADA_MIN:-10}"
KEEP="${RESPALDO_CONSERVAR:-200}"
DIR=/respaldos
echo "respaldo: cada ${EVERY} min a ${DIR}, se conservan ${KEEP}"
while true; do
  if [ ! -w "$DIR" ]; then
    echo "$(date '+%F %T') ERROR: ${DIR} no se puede escribir (¿USB desconectada?)" >&2
  else
    stamp="$(date '+%Y%m%d-%H%M%S')"
    tmp="${DIR}/.nodo-${stamp}.dump.tmp"
    out="${DIR}/nodo-${stamp}.dump"
    if pg_dump -Fc -Z 6 -f "$tmp" && pg_restore -l "$tmp" >/dev/null 2>&1; then
      mv "$tmp" "$out"
      cp "$out" "${DIR}/.ultimo.tmp" && mv "${DIR}/.ultimo.tmp" "${DIR}/ultimo.dump"
      sync
      echo "$(date '+%F %T') ok ${out} ($(du -h "$out" | cut -f1))"
      # Borra los más viejos (nombres con fecha: el orden alfabético es el cronológico).
      ls -1 "${DIR}"/nodo-*.dump 2>/dev/null | sort | head -n "-${KEEP}" | while read -r old; do rm -f "$old"; done
    else
      rm -f "$tmp"
      echo "$(date '+%F %T') ERROR: pg_dump falló" >&2
    fi
  fi
  sleep $((EVERY * 60))
done
