#!/usr/bin/env bash
# Empaqueta «Nodo Camporee» PORTÁTIL: un DMG firmado que se instala en otra Mac sin los repos y
# sin internet (README.md §10). Contiene la app y, dentro de ella (Contents/Resources):
#   nodo/          compose, Caddyfile, nodo.sh, respaldo/, tls/*.sh   (lo que copia instalar.sh)
#   imagenes/*.tar las imágenes de Docker ya construidas (api, web, postgres, caddy, lego) para
#                  `docker load` (las capas ya van comprimidas dentro del tar)
#   herramientas/  colima + limactl + docker + docker-compose (sin Homebrew), share/lima, licencias
#                  y la imagen de la máquina virtual de Colima (vm/), para crearla sin descargar
#   paquete.env    versión del paquete (sin secretos)
#
# Uso (en la Mac del propietario, CON internet, con Colima/Docker de Homebrew):
#   FRONTEND_DIR=/ruta/al/frontend ./nodo-evento/mac/empaquetar.sh [--salida DIR] [--sin-notarizar]
#
# Variables opcionales:
#   NODO_FIRMA      identidad (por defecto «Developer ID Application: MATIAS ADAN ISLAS CHIRINOS (K8KM5TV738)»)
#   NODO_NOTARIO    perfil de notarytool en el Llavero (por defecto «nodo-camporee»)
#   NODO_BUILD_PROFILE  perfil de Colima con el que se construyen las imágenes (por defecto «nodo»;
#                   si estaba apagado se enciende y se vuelve a apagar al final). Las imágenes se
#                   exportan a archivos: NO cambian las etiquetas de ese Docker.
# Sólo Apple Silicon (arm64): ver README §10 «Macs Intel».
# Nunca pide ni maneja la contraseña de Apple: la notarización usa el perfil guardado por el propietario.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
MAC_DIR="$(cd "$(dirname "$0")" && pwd)"
NODO_SRC="$(cd "$MAC_DIR/.." && pwd)"
REPO="$(cd "$NODO_SRC/.." && pwd)"
APP_NAME="Nodo Camporee"
IDENT="${NODO_FIRMA:-Developer ID Application: MATIAS ADAN ISLAS CHIRINOS (K8KM5TV738)}"
NOTARIO="${NODO_NOTARIO:-nodo-camporee}"
BUILD_PROFILE="${NODO_BUILD_PROFILE:-nodo}"
SALIDA="$REPO/dist"
NOTARIZAR=1
while [ $# -gt 0 ]; do
  case "$1" in
    --salida) SALIDA="$2"; shift 2 ;;
    --sin-notarizar) NOTARIZAR=0; shift ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
ARCH="$(uname -m)"
[ "$ARCH" = arm64 ] || { echo "Sólo se empaqueta en una Mac Apple (arm64); esta es $ARCH (README §10)." >&2; exit 1; }
PLAT="linux/arm64"; VM_ARCH="arm64"; GA_ARCH="aarch64"

paso() { printf '\n== %s\n' "$*"; }
falla() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- Comprobaciones --------------------------------------------------------------------------------
FRONTEND_DIR="${FRONTEND_DIR:-}"
[ -n "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ] || falla "FRONTEND_DIR debe apuntar al repo del frontend (mismo commit que producción)"
FRONTEND_DIR="$(cd "$FRONTEND_DIR" && pwd)"
[ -f "$REPO/backend/requirements.txt" ] || falla "no encuentro $REPO/backend"
command -v swiftc >/dev/null || falla "falta swiftc (xcode-select --install)"
security find-identity -v -p codesigning | grep -qF "\"$IDENT\"" || falla "no está la identidad de firma en el Llavero: $IDENT"
for t in colima limactl docker; do command -v "$t" >/dev/null || falla "falta $t (brew install colima docker docker-compose)"; done
BE_SHA="$(git -C "$REPO" rev-parse --short HEAD)"
FE_SHA="$(git -C "$FRONTEND_DIR" rev-parse --short HEAD)"
[ -z "$(git -C "$REPO" status --porcelain -- backend nodo-evento)" ] || echo "AVISO: el backend tiene cambios sin commit (se empaqueta tal cual)"
[ -z "$(git -C "$FRONTEND_DIR" status --porcelain)" ] || echo "AVISO: el frontend tiene cambios sin commit (se empaqueta tal cual)"
VERSION="$(date +%Y%m%d.%H%M)-${BE_SHA}-${FE_SHA}"
echo "Paquete $VERSION · backend $BE_SHA · frontend $FE_SHA · $ARCH"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/nodo-paquete.XXXXXX")"
ENCENDI_COLIMA=0
limpiar() {
  rm -rf "$WORK"
  if [ "$ENCENDI_COLIMA" = 1 ]; then echo "Apagando Colima «$BUILD_PROFILE» (lo encendió este script)…"; colima stop --profile "$BUILD_PROFILE" >/dev/null 2>&1 || true; fi
}
trap limpiar EXIT

# --- 1. Imágenes de Docker ------------------------------------------------------------------------
paso "1/6 Imágenes de Docker ($PLAT) con Colima «$BUILD_PROFILE»"
if ! colima status --profile "$BUILD_PROFILE" >/dev/null 2>&1; then
  colima start --profile "$BUILD_PROFILE" >/dev/null 2>&1 || falla "no pude encender Colima «$BUILD_PROFILE»"
  ENCENDI_COLIMA=1
fi
export DOCKER_HOST="unix://$HOME/.colima/$BUILD_PROFILE/docker.sock"
docker info >/dev/null 2>&1 || falla "Docker de «$BUILD_PROFILE» no responde"
IMG="$WORK/imagenes"; mkdir -p "$IMG"
# Se exportan directo a archivo (type=docker): las etiquetas :local de ese Docker no cambian.
docker buildx build --builder default --platform "$PLAT" --progress plain \
  -f "$NODO_SRC/api.Dockerfile" -t nodo-evento-api:local \
  --label org.opencontainers.image.revision="$BE_SHA" \
  --output "type=docker,dest=$IMG/2-api.tar" "$REPO/backend" 2>&1 | grep -E '^#[0-9]+ (DONE|ERROR|CACHED)|ERROR' | tail -3
[ -s "$IMG/2-api.tar" ] || falla "no se construyó la imagen de la API"
docker buildx build --builder default --platform "$PLAT" --progress plain \
  -f "$NODO_SRC/web.Dockerfile" -t nodo-evento-web:local --build-arg BUILD_ID="$FE_SHA" \
  --label org.opencontainers.image.revision="$FE_SHA" \
  --output "type=docker,dest=$IMG/3-web.tar" "$FRONTEND_DIR" 2>&1 | grep -E '^#[0-9]+ (DONE|ERROR|CACHED)|ERROR' | tail -3
[ -s "$IMG/3-web.tar" ] || falla "no se construyó la imagen web"
BASES="postgres:16-alpine caddy:2.10-alpine goacme/lego:v4.26.0"
for b in $BASES; do
  docker image inspect "$b" >/dev/null 2>&1 || docker pull -q --platform "$PLAT" "$b" >/dev/null || falla "no pude descargar $b"
done
# shellcheck disable=SC2086
docker save --platform "$PLAT" -o "$IMG/1-base.tar" $BASES || falla "docker save de las imágenes base"
chmod 644 "$IMG"/*.tar
ls -lh "$IMG" | awk 'NR>1{print "   " $5 "  " $9}'

# --- 2. Herramientas (Colima, Lima, Docker) -------------------------------------------------------
paso "2/6 Colima, Lima y Docker (sin Homebrew en la otra Mac)"
HERR="$WORK/herramientas"; mkdir -p "$HERR/bin" "$HERR/share/lima" "$HERR/licencias" "$HERR/vm"
real() { python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$1"; }
COLIMA_BIN="$(real "$(command -v colima)")"; LIMACTL_BIN="$(real "$(command -v limactl)")"; DOCKER_BIN="$(real "$(command -v docker)")"
COMPOSE_BIN=""
for c in "$HOME/.docker/cli-plugins/docker-compose" /opt/homebrew/lib/docker/cli-plugins/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose; do
  [ -x "$c" ] && { COMPOSE_BIN="$(real "$c")"; break; }
done
[ -n "$COMPOSE_BIN" ] || falla "no encuentro el plugin docker-compose"
for b in "$COLIMA_BIN" "$LIMACTL_BIN" "$DOCKER_BIN" "$COMPOSE_BIN"; do
  lipo -archs "$b" 2>/dev/null | grep -qw arm64 || falla "$b no es arm64"
  # Sólo bibliotecas del sistema (binarios de Go): se pueden llevar a otra Mac tal cual.
  if otool -L "$b" | tail -n +2 | awk '{print $1}' | grep -vqE '^(/usr/lib/|/System/)'; then falla "$b enlaza bibliotecas fuera del sistema"; fi
done
install -m 755 "$COLIMA_BIN" "$HERR/bin/colima"
install -m 755 "$LIMACTL_BIN" "$HERR/bin/limactl"
install -m 755 "$DOCKER_BIN" "$HERR/bin/docker"
install -m 755 "$COMPOSE_BIN" "$HERR/bin/docker-compose"
LIMA_SHARE="$(cd "$(dirname "$LIMACTL_BIN")/../share/lima" && pwd)"
cp "$LIMA_SHARE/lima-guestagent.Linux-$GA_ARCH.gz" "$HERR/share/lima/"
cp -R "$LIMA_SHARE/templates" "$HERR/share/lima/"
for k in "$COLIMA_BIN" "$LIMACTL_BIN" "$DOCKER_BIN" "$COMPOSE_BIN"; do
  keg="$(cd "$(dirname "$k")/.." && pwd)"; n="$(basename "$k")"
  for f in LICENSE NOTICE; do [ -f "$keg/$f" ] && cp "$keg/$f" "$HERR/licencias/$n-$f"; done
done
COLIMA_VER="$(colima version | awk '/^colima version/{print $3}')"
cat > "$HERR/licencias/LEEME.txt" <<TXT
Herramientas de terceros incluidas sin cambios (licencias en esta carpeta):
  colima $COLIMA_VER (MIT) · lima $(limactl --version | awk '{print $3}') (Apache-2.0)
  docker CLI $(docker version --format '{{.Client.Version}}' 2>/dev/null) (Apache-2.0) · docker compose $(docker compose version --short) (Apache-2.0)
  imagen de la máquina virtual: abiosoft/colima-core (Ubuntu 24.04 minimal + Docker)
TXT
# Imagen de la máquina virtual que esta versión de Colima descargaría (URL y sha512 vienen dentro
# del propio binario de Colima). Colima la busca en su caché por el sha256 de la URL.
read -r VM_URL VM_SHA512 VM_FILE < <(strings "$COLIMA_BIN" | awk -v a="$VM_ARCH" '$1==a && $2=="docker" && $3 ~ /^https:/ {print $3, $4, $5; exit}')
[ -n "${VM_URL:-}" ] || falla "no encuentro la URL de la imagen de la máquina virtual dentro de colima"
VM_CACHE="$(printf '%s' "$VM_URL" | shasum -a 256 | cut -d' ' -f1)"
CACHED="$HOME/Library/Caches/colima/caches/$VM_CACHE"
if [ -s "$CACHED" ] && [ "$(shasum -a 512 "$CACHED" | cut -d' ' -f1)" = "$VM_SHA512" ]; then
  cp "$CACHED" "$HERR/vm/$VM_FILE"
else
  echo "   descargando $VM_FILE…"; curl -fL --retry 3 -o "$HERR/vm/$VM_FILE" "$VM_URL"
  [ "$(shasum -a 512 "$HERR/vm/$VM_FILE" | cut -d' ' -f1)" = "$VM_SHA512" ] || falla "sha512 de la imagen de la máquina virtual no coincide"
fi
du -sh "$HERR"/bin/* "$HERR/vm/$VM_FILE" | awk '{print "   " $1 "  " $2}' | sed "s#$WORK/##"

# --- 3. La app con todo dentro --------------------------------------------------------------------
paso "3/6 App"
STAGE="$WORK/dmg"; mkdir -p "$STAGE"
APP="$STAGE/$APP_NAME.app"
"$MAC_DIR/construir-app.sh" "$APP" "1.1" "$VERSION"
RES="$APP/Contents/Resources"
mkdir -p "$RES/nodo/respaldo" "$RES/nodo/tls"
for f in docker-compose.yml Caddyfile nodo.sh .env.example api.Dockerfile api.Dockerfile.dockerignore web.Dockerfile web.Dockerfile.dockerignore; do
  install -m 644 "$NODO_SRC/$f" "$RES/nodo/$f"
done
chmod 755 "$RES/nodo/nodo.sh"
install -m 755 "$NODO_SRC/respaldo/respaldo.sh" "$RES/nodo/respaldo/respaldo.sh"
install -m 755 "$NODO_SRC/tls/ca-local.sh" "$NODO_SRC/tls/certificado-letsencrypt.sh" "$RES/nodo/tls/"
mkdir -p "$RES/nodo/portal" && install -m 644 "$NODO_SRC/portal/bienvenida.html" "$RES/nodo/portal/"
mv "$IMG" "$RES/imagenes"
mv "$HERR" "$RES/herramientas"
{
  printf 'PAQUETE_VERSION=%q\n' "$VERSION"
  printf 'PAQUETE_ARCH=%q\n' "$ARCH"
  printf 'PAQUETE_BACKEND=%q\n' "$BE_SHA"
  printf 'PAQUETE_FRONTEND=%q\n' "$FE_SHA"
  printf 'PAQUETE_COLIMA=%q\n' "$COLIMA_VER"
  printf 'PAQUETE_VM_CACHE=%q\n' "$VM_CACHE"
  printf 'PAQUETE_VM_FILE=%q\n' "$VM_FILE"
  printf 'PAQUETE_IMAGENES=%q\n' "nodo-evento-api:local nodo-evento-web:local $BASES"
} > "$RES/paquete.env"

# --- 4. Firma (de adentro hacia afuera) ----------------------------------------------------------
paso "4/6 Firma: $IDENT"
ENT="$WORK/limactl.entitlements"
# Mínimo: limactl crea la máquina virtual con Virtualization.framework.
cat > "$ENT" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.virtualization</key><true/>
</dict></plist>
XML
firmar() { codesign --force --options runtime --timestamp -s "$IDENT" "$@"; }
for b in colima docker docker-compose; do firmar "$RES/herramientas/bin/$b"; done
firmar --entitlements "$ENT" "$RES/herramientas/bin/limactl"
firmar "$APP"
codesign --verify --strict --deep -v "$APP" 2>&1 | tail -2
codesign -d --entitlements - "$RES/herramientas/bin/limactl" 2>/dev/null | grep -q virtualization || falla "limactl sin el permiso de virtualización"

# --- 5. DMG ----------------------------------------------------------------------------------------
paso "5/6 DMG"
ln -s /Applications "$STAGE/Aplicaciones"
cat > "$STAGE/LÉEME.txt" <<TXT
Nodo Camporee $VERSION (Mac con chip Apple: M1 o más nueva)

1. Arrastra «Nodo Camporee» a «Aplicaciones».
2. Ábrela desde Aplicaciones (no desde esta ventana) y pulsa «Instalar».
   No necesita internet ni Homebrew: Colima, Docker y el sistema vienen dentro.
3. Ajustes › Secretos (EVENT_PASS_SECRET) y Certificado (copiarlo de la otra Mac o pedir uno).
4. «Encender».

Guía completa: nodo-evento/mac/README.md (§10, otra Mac).
TXT
mkdir -p "$SALIDA"
DMG="$SALIDA/$APP_NAME.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "$APP_NAME" -srcfolder "$STAGE" -fs HFS+ -format ULFO -ov "$DMG" || falla "hdiutil create"
codesign --force --timestamp -s "$IDENT" "$DMG"
codesign --verify -v "$DMG" 2>&1 | tail -1

# --- 6. Notarización -----------------------------------------------------------------------------
paso "6/6 Notarización"
if [ "$NOTARIZAR" = 1 ] && xcrun notarytool history --keychain-profile "$NOTARIO" >/dev/null 2>&1; then
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARIO" --wait || falla "la notarización falló (xcrun notarytool log <id> --keychain-profile $NOTARIO)"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
  spctl -a -vvv -t install "$DMG"
  NOTARIZADO="sí (grapada)"
else
  NOTARIZADO="NO"
  motivo="no existe el perfil «$NOTARIO» en el Llavero"; [ "$NOTARIZAR" = 1 ] || motivo="se pidió --sin-notarizar"
  cat <<TXT
Sin notarizar ($motivo).
El propietario lo crea UNA vez en su Terminal (pide la contraseña específica de app de su Apple ID;
nadie más la ve):

  xcrun notarytool store-credentials "$NOTARIO" --apple-id <correo-del-Apple-ID> --team-id K8KM5TV738

y después vuelve a correr este script (o sólo notariza el DMG ya hecho):

  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARIO" --wait && xcrun stapler staple "$DMG"
TXT
fi
shasum -a 256 "$DMG" | awk '{print $1}' > "$DMG.sha256"
echo
echo "Listo: $DMG ($(du -h "$DMG" | cut -f1 | tr -d ' ')) · versión $VERSION · notarizado: $NOTARIZADO"
echo "sha256 $(cat "$DMG.sha256")"
