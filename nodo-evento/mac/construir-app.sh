#!/usr/bin/env bash
# Compila «Nodo Camporee.app» (SwiftUI, sin proyecto de Xcode) en la ruta indicada, SIN firmar:
#   ./nodo-evento/mac/construir-app.sh "/ruta/Nodo Camporee.app" [versión-corta] [versión-de-build]
# Lo usan instalar.sh (instalación desde el repo) y empaquetar.sh (DMG portátil).
# Requiere las herramientas de línea de órdenes de Xcode (swiftc).
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")" && pwd)"
NODO_DIR="$(cd "$MAC_DIR/.." && pwd)"
APP_NAME="Nodo Camporee"
APP="${1:?uso: construir-app.sh /ruta/Nodo\ Camporee.app [versión] [build]}"
SHORT="${2:-1.0}"
VERSION="${3:-$(git -C "$NODO_DIR" rev-parse --short HEAD 2>/dev/null || echo dev)}"
ARCH="${NODO_APP_ARCH:-$(uname -m)}"
case "$APP" in *.app) ;; *) echo "la ruta debe terminar en .app: $APP" >&2; exit 2 ;; esac

command -v swiftc >/dev/null || { echo "Falta swiftc: instala las herramientas de Xcode con  xcode-select --install" >&2; exit 1; }
TMP="$(mktemp -d "${TMPDIR:-/tmp}/nodo-icono.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -swift-version 5 -parse-as-library -target "$ARCH-apple-macos13.0" \
  -o "$APP/Contents/MacOS/$APP_NAME" \
  "$MAC_DIR/app/App.swift" "$MAC_DIR/app/Motor.swift" "$MAC_DIR/app/Llavero.swift" "$MAC_DIR/app/Vistas.swift" \
  "$MAC_DIR/app/DNS.swift" "$MAC_DIR/app/API.swift" "$MAC_DIR/app/Conectar.swift" "$MAC_DIR/app/Red.swift"

swift "$MAC_DIR/app/icono.swift" "$TMP/icono.png" >/dev/null
mkdir -p "$TMP/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$TMP/icono.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$TMP/icono.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

install -m 755 "$MAC_DIR/nodo-mac.sh" "$APP/Contents/Resources/nodo-mac.sh"
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
  <key>CFBundleShortVersionString</key><string>$SHORT</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleDevelopmentRegion</key><string>es</string>
  <key>NSRemovableVolumesUsageDescription</key><string>Copia los respaldos del evento a la memoria USB NODO-RESPALDO.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Guarda los respaldos del evento en Documentos › Nodo Camporee.</string>
  <key>NSLocalNetworkUsageDescription</key><string>Revisa el router y que los teléfonos lleguen al nodo; responde el DNS del evento en «Mi red».</string>
  <!-- Sólo para pruebas con una API local (http://127.0.0.1): la API real es HTTPS. -->
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
echo "app compilada: $APP ($ARCH, $SHORT/$VERSION)"
