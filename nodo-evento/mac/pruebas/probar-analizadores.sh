#!/bin/bash
# Compila y corre las pruebas de los analizadores y del Depurador (sin la app, sin Xcode: sólo swiftc).
# Además corre las pruebas de depurar.pl con los mismos casos (pruebas/depurador/casos.txt).
#   pruebas/probar-analizadores.sh          → pruebas con pruebas/muestras
#   pruebas/probar-analizadores.sh --real   → además revisa ESTA Mac (sólo lectura) y lista los títulos
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ARCH="$(uname -m)"
# main.swift es código de nivel superior: se compila SIN -parse-as-library (los archivos de app/
# sólo tienen declaraciones, así que compilan igual que dentro de la app).
swiftc -O -swift-version 5 -target "$ARCH-apple-macos13.0" -o "$TMP/probar" \
  "$MAC_DIR/app/Analisis.swift" "$MAC_DIR/app/Depurador.swift" "$MAC_DIR/app/Interferencias.swift" "$MAC_DIR/app/SeguridadMac.swift" \
  "$MAC_DIR/app/Dispositivos.swift" "$MAC_DIR/pruebas/analizadores/main.swift"
"$TMP/probar" "$MAC_DIR/pruebas/muestras" "$@"
perl "$MAC_DIR/pruebas/depurador/probar.pl"
