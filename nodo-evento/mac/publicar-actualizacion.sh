#!/usr/bin/env bash
# Sube una actualización de «Nodo Camporee» (lo que dejó empaquetar.sh en <dist>/actualizaciones/)
# al bucket público de medios (Cloudflare R2) bajo https://media.adventist.club/nodo/.
#
#   ./nodo-evento/mac/publicar-actualizacion.sh [--carpeta DIR] [--simular]
#
# Credenciales SÓLO por el entorno (nunca se guardan ni se imprimen; viajan a curl por stdin):
#   R2_ACCOUNT_ID          id de la cuenta de Cloudflare
#   R2_ACCESS_KEY_ID       llave de API de R2 con permiso «Object Read & Write» SÓLO en el bucket
#   R2_SECRET_ACCESS_KEY
#   R2_BUCKET              el bucket que sirve media.adventist.club
#   NODO_PREFIJO           (opcional) carpeta dentro del bucket; por defecto «nodo»
#
# Orden (para que una Mac nunca vea un appcast que apunte a algo que aún no existe):
#   1) imágenes nuevas (imagenes/<versión>/…; si esa versión ya está publicada, se salta)
#   2) el .zip de la app (y los .delta)
#   3) appcast.xml al final (sin caché larga)
# Con --simular sólo dice qué subiría (no necesita credenciales).
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$MAC_DIR/../.." && pwd)"
CARPETA="$REPO/dist/actualizaciones"
SIMULAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --carpeta) CARPETA="$2"; shift 2 ;;
    --simular) SIMULAR=1; shift ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
[ -s "$CARPETA/appcast.xml" ] || { echo "No hay $CARPETA/appcast.xml: corre empaquetar.sh primero." >&2; exit 1; }
PREFIJO="${NODO_PREFIJO:-nodo}"
PUBLICO="https://media.adventist.club/$PREFIJO"

if [ $SIMULAR = 0 ]; then
  for v in R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET; do
    [ -n "${!v:-}" ] || { echo "Falta $v en el entorno (README §12: qué debe dar el propietario)." >&2; exit 1; }
  done
  ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com/${R2_BUCKET}/$PREFIJO"
fi

# curl con firma SigV4 (curl ≥ 7.75; el de macOS sirve). Llave y secreto por stdin (-K -): no
# aparecen en `ps` ni en el historial.
r2() {  # r2 METODO ruta-remota [archivo] [content-type] [cache-control]
  local metodo="$1" ruta="$2" archivo="${3:-}" tipo="${4:-application/octet-stream}" cache="${5:-public, max-age=31536000, immutable}"
  local args=(-sS --fail --retry 3 --aws-sigv4 "aws:amz:auto:s3" -K -)
  case "$metodo" in
    HEAD) args+=(-I -o /dev/null) ;;
    PUT) args+=(-T "$archivo" -H "Content-Type: $tipo" -H "Cache-Control: $cache" -o /dev/null) ;;
  esac
  printf 'user = "%s:%s"\n' "$R2_ACCESS_KEY_ID" "$R2_SECRET_ACCESS_KEY" | curl "${args[@]}" "$ENDPOINT/$ruta"
}

subir() {  # subir archivo ruta-remota tipo cache
  local tam; tam="$(du -h "$1" | cut -f1 | tr -d ' ')"
  if [ $SIMULAR = 1 ]; then echo "  subiría $2 ($tam)"; return 0; fi
  echo "  subiendo $2 ($tam)…"; r2 PUT "$2" "$1" "$3" "$4"
}

echo "Destino: $PUBLICO/"
echo "1) Imágenes"
for d in "$CARPETA"/imagenes/*/; do
  [ -d "$d" ] || continue
  ver="$(basename "$d")"
  if [ $SIMULAR = 0 ] && r2 HEAD "imagenes/$ver/imagenes.json" 2>/dev/null; then echo "  imagenes/$ver ya está publicada: se salta"; continue; fi
  for f in "$d"*.tar.gz; do subir "$f" "imagenes/$ver/$(basename "$f")" application/gzip "public, max-age=31536000, immutable"; done
  subir "$d/imagenes.json" "imagenes/$ver/imagenes.json" application/json "public, max-age=31536000, immutable"
done
echo "2) App"
for f in "$CARPETA"/*.zip "$CARPETA"/*.delta; do
  [ -f "$f" ] || continue
  if [ $SIMULAR = 0 ] && r2 HEAD "$(basename "$f")" 2>/dev/null; then echo "  $(basename "$f") ya está: se salta"; continue; fi
  subir "$f" "$(basename "$f")" application/octet-stream "public, max-age=31536000, immutable"
done
echo "3) appcast.xml"
subir "$CARPETA/appcast.xml" appcast.xml "application/xml" "public, max-age=300"
[ $SIMULAR = 1 ] && echo "Simulación: no se subió nada." || echo "Listo: $PUBLICO/appcast.xml"
