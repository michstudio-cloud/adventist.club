#!/usr/bin/env bash
# Construye e instala «Nodo Camporee.app» en ~/Applications con un alias en el Escritorio.
# Correr UNA vez (y otra vez cada que se actualice el repo):  ./nodo-evento/mac/instalar.sh
# Requiere las herramientas de línea de órdenes de Xcode (swiftc). No toca secretos.
# Para pasar la app a OTRA Mac sin repos ni internet: empaquetar.sh (DMG portátil, README §10).
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")" && pwd)"
NODO_DIR="$(cd "$MAC_DIR/.." && pwd)"
APP_NAME="Nodo Camporee"
DEST="${NODO_INSTALL_DIR:-$HOME/Applications}"
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/nodo-app.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
APP="$BUILD/$APP_NAME.app"

echo "1/3 Compilando la app…"
"$MAC_DIR/construir-app.sh" "$APP" >/dev/null

echo "2/3 Firma…"
# macOS reciente RECHAZA al abrir una app con firma local (ad-hoc) reinstalada: se firma con el
# Developer ID del propietario si está en el Llavero (runtime endurecido + sello de tiempo) y, si
# existe el perfil de notarytool «nodo-camporee», se notariza y se grapa. Sin esa identidad: ad-hoc.
# NODO_CODESIGN_ID="…" fuerza otra identidad; NODO_NOTARIO cambia el perfil de notarización.
DEVID="Developer ID Application: MATIAS ADAN ISLAS CHIRINOS (K8KM5TV738)"
IDENT="${NODO_CODESIGN_ID:-}"
if [ -z "$IDENT" ] && security find-identity -v -p codesigning 2>/dev/null | grep -qF "\"$DEVID\""; then IDENT="$DEVID"; fi
if [ -n "$IDENT" ]; then
  # De adentro hacia afuera (la app del repo no lleva otros ejecutables; el motor es un recurso).
  codesign --force --options runtime --timestamp -s "$IDENT" "$APP"
  codesign --verify --strict --deep "$APP"
  echo "   firmada con: $IDENT"
  NOTARIO="${NODO_NOTARIO:-nodo-camporee}"
  if xcrun notarytool history --keychain-profile "$NOTARIO" >/dev/null 2>&1; then
    echo "   notarizando (necesita internet, 1–5 min)…"
    ditto -c -k --keepParent "$APP" "$BUILD/notarizar.zip"
    if xcrun notarytool submit "$BUILD/notarizar.zip" --keychain-profile "$NOTARIO" --wait >/dev/null \
       && xcrun stapler staple "$APP" >/dev/null; then echo "   notarizada y grapada"
    else echo "   AVISO: no se pudo notarizar (queda firmada; macOS puede pedir «Abrir de todos modos»)"; fi
  else
    echo "   sin notarizar (no existe el perfil «$NOTARIO»: README §10)"
  fi
else
  codesign --force --deep -s - "$APP"; echo "   firma local (ad-hoc): macOS puede rechazarla al abrir; mejor con Developer ID"
fi

echo "3/3 Instalando en $DEST…"
mkdir -p "$DEST"
if [ -d "$DEST/$APP_NAME.app" ]; then rm -rf "$DEST/$APP_NAME.app.viejo"; mv "$DEST/$APP_NAME.app" "$DEST/$APP_NAME.app.viejo"; fi
ditto "$APP" "$DEST/$APP_NAME.app"
rm -rf "$DEST/$APP_NAME.app.viejo"
touch "$DEST/$APP_NAME.app"

# Copia de trabajo del nodo FUERA de Documentos (macOS pide permisos para Documentos/Escritorio
# y una pregunta sin contestar dejaría el nodo esperando): compose, Caddyfile, scripts y, ahí
# mismo, los datos (datos/) y el certificado (tls/actual/). No se borra nada de lo que ya exista.
SUPPORT="${NODO_APP_SUPPORT:-$HOME/Library/Application Support/$APP_NAME}"
RUNTIME="$SUPPORT/nodo"
mkdir -p "$RUNTIME/respaldo" "$RUNTIME/tls"
for f in docker-compose.yml Caddyfile api.Dockerfile api.Dockerfile.dockerignore web.Dockerfile web.Dockerfile.dockerignore nodo.sh .env.example; do
  install -m 644 "$NODO_DIR/$f" "$RUNTIME/$f"
done
chmod 755 "$RUNTIME/nodo.sh"
install -m 755 "$NODO_DIR/respaldo/respaldo.sh" "$RUNTIME/respaldo/respaldo.sh"
install -m 755 "$NODO_DIR/tls/ca-local.sh" "$NODO_DIR/tls/certificado-letsencrypt.sh" "$RUNTIME/tls/"
# Configuración inicial (sin secretos).
MOTOR="$DEST/$APP_NAME.app/Contents/Resources/nodo-mac.sh"
"$MOTOR" config set NODO_DIR "$RUNTIME" >/dev/null
"$MOTOR" config set BACKEND_DIR "$(cd "$NODO_DIR/../backend" && pwd)" >/dev/null
CONF="$SUPPORT/config.env"
if ! grep -q '^FRONTEND_DIR=' "$CONF" 2>/dev/null; then
  for cand in "${FRONTEND_DIR:-}" "$NODO_DIR/../../frontend" "$NODO_DIR/../../conquistadores.app"; do
    [ -n "$cand" ] && [ -f "$cand/package.json" ] && { "$MOTOR" config set FRONTEND_DIR "$(cd "$cand" && pwd)" >/dev/null; break; }
  done
fi

# Alias en el Escritorio (Finder; si macOS no deja, un enlace simbólico).
if [ "${NODO_SIN_ALIAS:-0}" != 1 ]; then
  rm -f "$HOME/Desktop/$APP_NAME" 2>/dev/null || true
  # perl alarm: si macOS deja la pregunta de permisos sin respuesta, no se queda colgado.
  exec 3>&2 2>/dev/null
  ( perl -e 'alarm 20; exec @ARGV' osascript -e "tell application \"Finder\" to make alias file to (POSIX file \"$DEST/$APP_NAME.app\") at (path to desktop folder) with properties {name:\"$APP_NAME\"}" ) >/dev/null 2>&1 \
    || ln -sfn "$DEST/$APP_NAME.app" "$HOME/Desktop/$APP_NAME"
  exec 2>&3 3>&-
fi
echo "Listo: $DEST/$APP_NAME.app (y en el Escritorio). Ábrela con doble clic."
