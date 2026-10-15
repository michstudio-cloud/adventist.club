#!/usr/bin/env bash
# Prueba del descubrimiento de nodos vecinos (Bonjour, _nodocamporee._tcp) en ESTA Mac:
# tres agentes `--malla` con carpetas aparte: A y B del mismo evento, C de otro evento.
# Debe pasar: A ve a B y B ve a A (127.0.0.1 con --local); nadie ve a C; C no ve a nadie;
# al apagar B, A lo olvida.
# Nota: dentro del .app, macOS 15 pide el permiso de «Red local» la primera vez (el operador lo
# concede una vez). Para no depender de esa ventana, la prueba usa el ejecutable FUERA del paquete.
# Uso: ./nodo-evento/mac/pruebas/malla-bonjour.sh [ruta/a/Nodo Camporee.app]   (compila una si no se da)
set -uo pipefail
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
T="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/nodo-bonjour.XXXXXX")" && pwd -P)"
APP="${1:-}"
if [ -z "$APP" ]; then APP="$T/Nodo Camporee.app"; NODO_BUNDLE_ID=club.adventist.nodo-camporee.prueba "$MAC_DIR/construir-app.sh" "$APP" >/dev/null 2>&1 || { echo "no compiló"; exit 1; }; fi
mkdir -p "$T/suelto/MacOS"; cp "$APP/Contents/MacOS/Nodo Camporee" "$T/suelto/MacOS/nodo"; ln -s "$APP/Contents/Frameworks" "$T/suelto/Frameworks"
E="$T/suelto/MacOS/nodo"
limpiar() { pkill -f "$T/" 2>/dev/null; sleep 0.5; rm -rf "$T"; }
trap limpiar EXIT
fallas=0; ok() { printf '  ✓ %s\n' "$*"; }; mal() { printf '  ✗ %s\n' "$*"; fallas=$((fallas + 1)); }
EV1="eeeeeeee-0000-4000-8000-$(printf '%012d' $$)"; EV2="ffffffff-0000-4000-8000-$(printf '%012d' $$)"
nodo() { mkdir -p "$T/$1"; printf '{"node":{"id":"%s","event_id":"%s","label":"%s"}}\n' "$2" "$3" "$4" > "$T/$1/nodo-info.json"
  ("$E" --malla --run "$T/$1" --puerto "$5" --local > "$T/$1/log" 2>&1 &) ; }
nodo a 11111111-aaaa-4aaa-8aaa-$(printf '%012d' $$) "$EV1" "Zona A" 18181
nodo b 22222222-bbbb-4bbb-8bbb-$(printf '%012d' $$) "$EV1" "Zona B" 18182
nodo c 33333333-cccc-4ccc-8ccc-$(printf '%012d' $$) "$EV2" "Otro evento" 18183
for i in $(seq 1 20); do grep -q 18182 "$T/a/malla-vecinos.json" 2>/dev/null && grep -q 18181 "$T/b/malla-vecinos.json" 2>/dev/null && break; sleep 1; done
grep -q '"url":"http:\\/\\/127.0.0.1:18182"' "$T/a/malla-vecinos.json" && ok "A encontró a B (http://127.0.0.1:18182)" || mal "A no encontró a B: $(cat "$T/a/malla-vecinos.json" 2>/dev/null)"
grep -q '18181' "$T/b/malla-vecinos.json" && ok "B encontró a A" || mal "B no encontró a A"
grep -q 18183 "$T/a/malla-vecinos.json" "$T/b/malla-vecinos.json" && mal "alguien vio al nodo de OTRO evento" || ok "el nodo de otro evento no aparece"
[ "$(cat "$T/c/malla-vecinos.json" 2>/dev/null)" = "[]" ] && ok "C (otro evento) no ve a nadie" || mal "C vio: $(cat "$T/c/malla-vecinos.json" 2>/dev/null)"
grep -q '"anunciado":true' "$T/a/malla-bonjour.json" 2>/dev/null && ok "estado de Bonjour para la ventana (anunciado)" || mal "malla-bonjour.json"
pkill -f "$T/b" ; for i in $(seq 1 15); do grep -q 18182 "$T/a/malla-vecinos.json" || break; sleep 1; done
grep -q 18182 "$T/a/malla-vecinos.json" && mal "A no olvidó a B al apagarse" || ok "B se apagó: A lo olvida"
echo; [ $fallas = 0 ] && echo "Bonjour: todo bien" || { echo "Bonjour: $fallas falla(s)"; exit 1; }
