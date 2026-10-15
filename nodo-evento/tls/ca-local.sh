#!/usr/bin/env bash
# Alternativa SIN certificado público: una CA propia (al estilo mkcert, sólo con openssl).
# Sirve para pruebas y como plan B, pero cada teléfono debe INSTALAR Y CONFIAR en la CA:
#   - iPhone: abrir ca-local.pem (AirDrop/correo/descarga) → Ajustes › General › VPN y gestión de
#     dispositivos › instalar perfil → Ajustes › General › Información › Ajustes de confianza de
#     certificados › activar la CA. (Dos pasos; mucha fricción con 100 teléfonos.)
#   - Android: Ajustes › Seguridad › Cifrado y credenciales › Instalar certificado › Certificado de CA.
# Además, si el teléfono ya visitó events.adventist.club en internet, el navegador puede exigir
# el certificado público (HSTS) y rechazar la CA local: por eso el plan A es Let's Encrypt.
#
#   ./tls/ca-local.sh [nombre-o-IP …]
# Sin argumentos: events.adventist.club admin.adventist.club api.adventist.club localhost.
# Las IPs (p. ej. 10.20.40.10) se añaden como IP SAN. Reutiliza la CA si ya existe.
# Resultado: tls/actual/{fullchain.pem,privkey.pem,ca-local.pem}; la CA en tls/ca-local/ (600).
set -euo pipefail
cd "$(dirname "$0")"
names=("$@")
[ ${#names[@]} -gt 0 ] || names=(events.adventist.club admin.adventist.club api.adventist.club localhost)
mkdir -p ca-local actual
umask 077
if [ ! -s ca-local/ca.key ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 825 -nodes \
    -keyout ca-local/ca.key -out ca-local/ca.pem \
    -subj "/O=Nodo local del evento/CN=Nodo evento CA local $(date +%Y%m%d)" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
  echo "CA nueva: tls/ca-local/ca.pem"
fi
san=""
for n in "${names[@]}"; do
  if [[ "$n" =~ ^[0-9.]+$ || "$n" == *:* ]]; then san+="IP:$n,"; else san+="DNS:$n,"; fi
done
san="${san%,}"
cat > ca-local/leaf.ext <<EXT
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$san
EXT
openssl req -newkey rsa:2048 -nodes -keyout actual/privkey.pem -out ca-local/leaf.csr \
  -subj "/CN=${names[0]}" 2>/dev/null
# 397 días: el máximo que aceptan los navegadores de Apple para un certificado de servidor.
openssl x509 -req -in ca-local/leaf.csr -CA ca-local/ca.pem -CAkey ca-local/ca.key -CAcreateserial \
  -days 397 -sha256 -extfile ca-local/leaf.ext -out ca-local/leaf.pem 2>/dev/null
cat ca-local/leaf.pem ca-local/ca.pem > actual/fullchain.pem
cp ca-local/ca.pem actual/ca-local.pem
chmod 644 actual/fullchain.pem actual/ca-local.pem
chmod 600 actual/privkey.pem ca-local/ca.key
echo "Certificado de la CA local para: $san"
echo "Instala en los teléfonos: tls/actual/ca-local.pem (huella SHA-256:"
openssl x509 -noout -fingerprint -sha256 -in actual/ca-local.pem | cut -d= -f2
echo ")"
