#!/usr/bin/env bash
# Construye e instala «Nodo Camporee.app» en ~/Applications con un alias en el Escritorio.
# Correr UNA vez (y otra vez cada que se actualice el repo):  ./nodo-evento/mac/instalar.sh
# Requiere las herramientas de línea de órdenes de Xcode (swiftc). No toca secretos.
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")" && pwd)"
NODO_DIR="$(cd "$MAC_DIR/.." && pwd)"
APP_NAME="Nodo Camporee"
DEST="${NODO_INSTALL_DIR:-$HOME/Applications}"
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/nodo-app.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT
APP="$BUILD/$APP_NAME.app"

command -v swiftc >/dev/null || { echo "Falta swiftc: instala las herramientas de Xcode con  xcode-select --install" >&2; exit 1; }

echo "1/5 Compilando la app…"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -swift-version 5 -parse-as-library -target "$(uname -m)-apple-macos13.0" \
  -o "$APP/Contents/MacOS/$APP_NAME" \
  "$MAC_DIR/app/App.swift" "$MAC_DIR/app/Motor.swift" "$MAC_DIR/app/Llavero.swift" "$MAC_DIR/app/Vistas.swift"

echo "2/5 Ícono…"
swift "$MAC_DIR/app/icono.swift" "$BUILD/icono.png" >/dev/null
mkdir -p "$BUILD/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icono.png" --out "$BUILD/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$BUILD/icono.png" --out "$BUILD/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

echo "3/5 Paquete…"
install -m 755 "$MAC_DIR/nodo-mac.sh" "$APP/Contents/Resources/nodo-mac.sh"
VERSION="$(git -C "$NODO_DIR" rev-parse --short HEAD 2>/dev/null || echo dev)"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>club.adventist.nodo-camporee</string>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleDevelopmentRegion</key><string>es</string>
  <key>NSRemovableVolumesUsageDescription</key><string>Copia los respaldos del evento a la memoria USB NODO-RESPALDO.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Guarda los respaldos del evento en Documentos › Nodo Camporee.</string>
  <key>NSLocalNetworkUsageDescription</key><string>Revisa el router GL.iNet y que los teléfonos lleguen al nodo.</string>
</dict></plist>
PLIST

echo "4/5 Firma…"
# Una identidad de desarrollador estable conserva los permisos (Llavero, USB) entre versiones;
# si no hay, firma local (macOS volverá a pedir permiso al Llavero tras cada actualización).
# Opcional: NODO_CODESIGN_ID="Apple Development: …" (puede pedir permiso al Llavero para firmar).
IDENT="${NODO_CODESIGN_ID:-}"
if [ -n "$IDENT" ] && codesign --force --deep --timestamp=none -s "$IDENT" "$APP" 2>/dev/null; then
  echo "   firmada con: $IDENT"
else
  codesign --force --deep -s - "$APP"; echo "   firma local (ad-hoc)"
fi

echo "5/5 Instalando en $DEST…"
mkdir -p "$DEST"
if [ -d "$DEST/$APP_NAME.app" ]; then rm -rf "$DEST/$APP_NAME.app.viejo"; mv "$DEST/$APP_NAME.app" "$DEST/$APP_NAME.app.viejo"; fi
ditto "$APP" "$DEST/$APP_NAME.app"
rm -rf "$DEST/$APP_NAME.app.viejo"
touch "$DEST/$APP_NAME.app"

# Copia de trabajo del nodo FUERA de Documentos (macOS pide permisos para Documentos/Escritorio
# y una pregunta sin contestar dejaría el nodo esperando): compose, Caddyfile, scripts y, ahí
# mismo, los datos (datos/) y el certificado (tls/actual/). No se borra nada de lo que ya exista.
SUPPORT="$HOME/Library/Application Support/$APP_NAME"
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
CONF="$HOME/Library/Application Support/$APP_NAME/config.env"
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
