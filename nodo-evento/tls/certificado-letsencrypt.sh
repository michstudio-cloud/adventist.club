#!/usr/bin/env bash
# Certificado REAL de Let's Encrypt para el nodo, por DNS-01 en Cloudflare, ANTES del evento
# (con internet). Con él, los teléfonos abren https://events.adventist.club en el nodo sin avisos,
# con cámara, service worker y caché funcionando igual que en producción.
#
# Requisitos: Docker (usa la imagen oficial goacme/lego; nada que instalar) y un token de API de
# Cloudflare con permiso «Zone · DNS · Edit» SÓLO para la zona adventist.club.
#
#   export CF_DNS_API_TOKEN      # pegarlo con: read -rs CF_DNS_API_TOKEN && export CF_DNS_API_TOKEN
#   export LE_EMAIL=correo@del-propietario
#   ./tls/certificado-letsencrypt.sh [--staging] [--renovar] [dominio …]
#
# Sin dominios: events.adventist.club admin.adventist.club api.adventist.club media.adventist.club.
# --staging  prueba contra el entorno de pruebas de Let's Encrypt (certificado NO confiable).
# --renovar  renueva el existente (Let's Encrypt lo emite por ~90 días: obtenerlo 1–3 semanas
#            antes del evento basta; renovar si faltan menos de 30 días).
# El token sólo se pasa por el entorno: nunca se escribe en disco, en argumentos ni en la salida.
# Resultado: tls/actual/fullchain.pem + privkey.pem (600), que Caddy usa al arrancar.
set -euo pipefail
cd "$(dirname "$0")"
staging=0; mode=run; domains=()
for arg in "$@"; do
  case "$arg" in
    --staging) staging=1 ;;
    --renovar) mode=renew ;;
    -*) echo "opción desconocida: $arg" >&2; exit 2 ;;
    *) domains+=("$arg") ;;
  esac
done
[ ${#domains[@]} -gt 0 ] || domains=(events.adventist.club admin.adventist.club api.adventist.club media.adventist.club)
: "${CF_DNS_API_TOKEN:?exporta CF_DNS_API_TOKEN (token de Cloudflare, Zone DNS Edit)}"
: "${LE_EMAIL:?exporta LE_EMAIL (correo para avisos de Let’s Encrypt)}"

args=(--accept-tos --email "$LE_EMAIL" --dns cloudflare --dns.resolvers 1.1.1.1:53 --path /data)
for d in "${domains[@]}"; do args+=(--domains "$d"); done
[ "$staging" = 1 ] && args+=(--server https://acme-staging-v02.api.letsencrypt.org/directory)
if [ "$mode" = renew ]; then args+=(renew --days 30); else args+=(run); fi

mkdir -p letsencrypt actual
suffix=""
[ "$staging" = 1 ] && suffix=" [STAGING]"
echo "Let’s Encrypt (${mode}) para: ${domains[*]}${suffix}"
# -e CF_DNS_API_TOKEN (sin valor): Docker toma el valor del entorno, no de la línea de órdenes.
docker run --rm -e CF_DNS_API_TOKEN -v "$PWD/letsencrypt:/data" goacme/lego:v4.26.0 "${args[@]}"

crt="letsencrypt/certificates/${domains[0]}.crt"
key="letsencrypt/certificates/${domains[0]}.key"
[ -s "$crt" ] && [ -s "$key" ] || { echo "no se encontró el certificado en letsencrypt/certificates/" >&2; exit 1; }
cp "$crt" actual/fullchain.pem
install -m 600 "$key" actual/privkey.pem
rm -f actual/ca-local.pem
chmod 600 letsencrypt/certificates/*.key 2>/dev/null || true
echo "Listo: tls/actual/fullchain.pem (vence: $(openssl x509 -enddate -noout -in actual/fullchain.pem | cut -d= -f2))"
openssl x509 -noout -ext subjectAltName -in actual/fullchain.pem
echo "Reinicia Caddy para usarlo: ./nodo.sh reiniciar caddy"
