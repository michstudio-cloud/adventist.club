#!/usr/bin/env bash
# Prueba de punta a punta de las actualizaciones (Sparkle) SIN tocar la app instalada:
#   • compila la app dos veces (build N y N+1) con OTRA identidad (club.adventist.nodo-camporee.prueba-act),
#     su propia carpeta de datos y un appcast local servido desde una carpeta temporal en 127.0.0.1;
#   • firma con el Developer ID (runtime endurecido) y genera el appcast con una llave EdDSA DE PRUEBA
#     (desechable, creada aquí; la del Llavero del propietario no se toca ni se pide);
#   • 1) con el nodo «ENCENDIDO» (run/encendido) la revisión en segundo plano se rechaza y la app NO cambia;
#   • 2) con el nodo apagado, la app baja la actualización y se instala sola al salir (build N+1);
#   • 3) un archivo con la firma EdDSA alterada se rechaza.
# Uso: ./nodo-evento/mac/pruebas/actualizacion-sparkle.sh   (tarda 1–2 min; necesita el Developer ID)
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IDENT="${NODO_FIRMA:-Developer ID Application: MATIAS ADAN ISLAS CHIRINOS (K8KM5TV738)}"
BID="club.adventist.nodo-camporee.prueba-act"
PUERTO="${PUERTO:-18765}"
T="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/nodo-act.XXXXXX")" && pwd -P)"
SP="$("$MAC_DIR/obtener-sparkle.sh")"
APP="$T/Aplicaciones/Nodo Camporee.app"
SOPORTE="$T/soporte"; mkdir -p "$SOPORTE/run" "$T/Aplicaciones" "$T/feed"
SRV=""
matar() {
  # La app de prueba y los procesos de Sparkle que la atienden (Autoupdate, Updater) ANTES de
  # borrar nada: una ventana de prueba huérfana confundiría al operador.
  pkill -f "$T/" 2>/dev/null || true
  local i; for i in $(seq 1 20); do pgrep -f "$T/" >/dev/null || break; sleep 0.5; done
  pkill -9 -f "$T/" 2>/dev/null || true
}
limpiar() {
  matar
  [ -n "$SRV" ] && kill "$SRV" 2>/dev/null || true
  defaults delete "$BID" >/dev/null 2>&1 || true
  rm -rf "$T" "$HOME/Library/Caches/$BID" "$HOME/Library/Application Support/$BID" 2>/dev/null || true
}
trap limpiar EXIT
ok() { printf '  ✓ %s\n' "$*"; }
mal() { printf '  ✗ %s\n' "$*"; exit 1; }
version() { /usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$APP/Contents/Info.plist"; }
avisar() { swift "$MAC_DIR/pruebas/avisar.swift" "$1"; }

# Llave EdDSA de prueba (Ed25519): semilla de 32 bytes en base64, como la exporta generate_keys -x.
openssl genpkey -algorithm ed25519 -out "$T/clave.pem" 2>/dev/null || { echo "falta openssl con ed25519"; exit 1; }
openssl pkey -in "$T/clave.pem" -outform DER | tail -c 32 | base64 > "$T/clave"; chmod 600 "$T/clave"
export NODO_ED_PUBLICA="$(openssl pkey -in "$T/clave.pem" -pubout -outform DER | tail -c 32 | base64)"
N=100
echo "1/5 Compilando build $N y $((N + 1))"
export NODO_BUNDLE_ID="$BID" NODO_FEED_URL="http://127.0.0.1:$PUERTO/appcast.xml"
"$MAC_DIR/construir-app.sh" "$APP" "3.0.$N" "$N" >/dev/null
"$MAC_DIR/firmar-app.sh" "$APP" "$IDENT" >/dev/null
NUEVA="$T/nueva/Nodo Camporee.app"; mkdir -p "$T/nueva"
"$MAC_DIR/construir-app.sh" "$NUEVA" "3.0.$((N + 1))" "$((N + 1))" >/dev/null
"$MAC_DIR/firmar-app.sh" "$NUEVA" "$IDENT" >/dev/null
ditto -c -k --sequesterRsrc --keepParent "$NUEVA" "$T/feed/Nodo-Camporee-$((N + 1)).zip"
[ "$(version)" = "$N" ] && ok "app instalada de prueba: build $N" || mal "versión inicial"

echo "2/5 Appcast firmado (EdDSA de prueba)"
"$SP/bin/generate_appcast" --ed-key-file "$T/clave" --download-url-prefix "http://127.0.0.1:$PUERTO/" -o "$T/feed/appcast.xml" "$T/feed" >/dev/null
grep -q 'sparkle:edSignature=' "$T/feed/appcast.xml" && ok "appcast con firma EdDSA" || mal "appcast sin firma"
( cd "$T/feed" && exec python3 -m http.server "$PUERTO" --bind 127.0.0.1 >/dev/null 2>&1 ) & SRV=$!
sleep 1
defaults write "$BID" feedPruebas "$NODO_FEED_URL"
defaults write "$BID" actualizarSoloPruebas -bool true
defaults write "$BID" SUEnableAutomaticChecks -bool true

# -g -j: en segundo plano y oculta (no roba el foco; su ventana dice «PRUEBA — no usar»).
abrir() { open -g -j -n "$APP" --env NODO_APP_SUPPORT="$SOPORTE" --args --prueba; sleep 4; }
cerrar_y_esperar() { avisar salir; local i; for i in $(seq 1 40); do pgrep -f "$T/Aplicaciones/Nodo Camporee.app/Contents/MacOS" >/dev/null || break; sleep 0.5; done; sleep "${1:-2}"; }

echo "3/5 Nodo ENCENDIDO: no debe actualizarse"
date > "$SOPORTE/run/encendido"
abrir; avisar "actualizar:fondo"; sleep 6; cerrar_y_esperar 6
[ "$(version)" = "$N" ] && ok "con el nodo encendido la app sigue en build $N" || mal "se actualizó con el nodo encendido"

echo "4/5 Nodo apagado: baja e instala al salir"
rm -f "$SOPORTE/run/encendido"
abrir; avisar "actualizar:fondo"
for i in $(seq 1 30); do ls "$HOME/Library/Caches/$BID/org.sparkle-project.Sparkle/PersistentDownloads" 2>/dev/null | grep -q . && break; sleep 1; done
sleep 4; cerrar_y_esperar 1
for i in $(seq 1 40); do [ "$(version)" = "$((N + 1))" ] && break; sleep 1; done
[ "$(version)" = "$((N + 1))" ] || { echo "--- bitácora de la app de prueba:"; cat "$SOPORTE/run/actualizaciones.log" 2>/dev/null; }
[ "$(version)" = "$((N + 1))" ] && ok "actualizada sola a build $((N + 1)) (en la copia de prueba)" || mal "no se actualizó (sigue en $(version))"
codesign --verify --strict --deep "$APP" && ok "firma de la app actualizada válida" || mal "firma inválida"

echo "5/5 Firma EdDSA alterada: se rechaza"
"$MAC_DIR/construir-app.sh" "$NUEVA" "3.0.$((N + 2))" "$((N + 2))" >/dev/null
"$MAC_DIR/firmar-app.sh" "$NUEVA" "$IDENT" >/dev/null
rm -f "$T/feed"/*.zip "$T/feed"/*.delta
ditto -c -k --sequesterRsrc --keepParent "$NUEVA" "$T/feed/Nodo-Camporee-$((N + 2)).zip"
"$SP/bin/generate_appcast" --ed-key-file "$T/clave" --download-url-prefix "http://127.0.0.1:$PUERTO/" -o "$T/feed/appcast.xml" "$T/feed" >/dev/null
# Cambiar un carácter de la firma (sigue siendo base64 válido).
python3 - "$T/feed/appcast.xml" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p).read()
m = re.search(r'sparkle:edSignature="([^"]+)"', s)
sig = m.group(1); c = "A" if sig[5] != "A" else "B"
open(p, "w").write(s.replace(sig, sig[:5] + c + sig[6:]))
PY
rm -rf "$HOME/Library/Caches/$BID/org.sparkle-project.Sparkle" 2>/dev/null || true
abrir; avisar "actualizar:fondo"; sleep 10; cerrar_y_esperar 6
[ "$(version)" = "$((N + 1))" ] && ok "firma alterada: no se instaló (sigue en $((N + 1)))" || mal "¡se instaló una actualización con firma inválida!"
echo "--- bitácora:"; cat "$SOPORTE/run/actualizaciones.log" 2>/dev/null | tail -12
echo "Prueba de actualizaciones: todo bien"
