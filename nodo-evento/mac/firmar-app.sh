#!/usr/bin/env bash
# Firma «Nodo Camporee.app» de ADENTRO hacia AFUERA con runtime endurecido y sello de tiempo
# (requisito de la notarización): Sparkle (servicios XPC, Autoupdate, Updater.app, el marco), las
# herramientas que trae el paquete portátil (si las hay) y al final la app.
#   ./nodo-evento/mac/firmar-app.sh "/ruta/Nodo Camporee.app" "Developer ID Application: …"
# Con identidad «-» firma local (ad-hoc), sólo para pruebas.
set -euo pipefail
APP="${1:?uso: firmar-app.sh APP IDENTIDAD}"; IDENT="${2:?falta la identidad}"
# Ad-hoc («-»): sin runtime endurecido ni sello de tiempo; con runtime, la validación de bibliotecas
# rechaza Sparkle (dyld: «different Team IDs») y la app no abre.
TS=(--timestamp --options runtime); [ "$IDENT" = "-" ] && TS=()
firmar() { codesign --force ${TS[@]+"${TS[@]}"} -s "$IDENT" "$@"; }
SP="$APP/Contents/Frameworks/Sparkle.framework"
if [ -d "$SP" ]; then
  # Orden de la guía de Sparkle («Sandboxing / Code signing»): de lo de dentro a lo de fuera.
  # Downloader.xpc conserva sus permisos de red (sólo los usa una app con sandbox; ésta no la tiene).
  for x in "$SP/Versions/B/XPCServices/Installer.xpc"; do [ -d "$x" ] && firmar "$x"; done
  for x in "$SP/Versions/B/XPCServices/Downloader.xpc"; do [ -d "$x" ] && firmar --preserve-metadata=entitlements "$x"; done
  firmar "$SP/Versions/B/Autoupdate"
  firmar "$SP/Versions/B/Updater.app"
  firmar "$SP"
fi
H="$APP/Contents/Resources/herramientas/bin"
if [ -d "$H" ]; then
  ENT="$(mktemp "${TMPDIR:-/tmp}/limactl.XXXXXX")"
  # limactl crea la máquina virtual con Virtualization.framework.
  cat > "$ENT" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.virtualization</key><true/></dict></plist>
XML
  for b in colima docker docker-compose; do [ -f "$H/$b" ] && firmar "$H/$b"; done
  [ -f "$H/limactl" ] && firmar --entitlements "$ENT" "$H/limactl"
  rm -f "$ENT"
fi
firmar "$APP"
codesign --verify --strict --deep "$APP"
echo "firmada: $APP ($IDENT)"
