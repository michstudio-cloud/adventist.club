#!/bin/bash
# Captura las salidas de las órdenes de sólo lectura que usan los analizadores (Interferencias,
# Seguridad de la Mac, Dispositivos) para hacer una muestra de pruebas, YA DEPURADAS
# (docs/EGRESO.md, README.md «Muestras de pruebas»):
#
#   pruebas/capturar-muestras.sh GRUPO            → pruebas/muestras/GRUPO/*.txt
#   pruebas/capturar-muestras.sh GRUPO --salida D → D/*.txt (p. ej. para revisarlas antes)
#
# Siempre: `ps` sólo con `-axo comm=` (NUNCA args/command: así se filtró una llave de Google en
# oct 2026), todo pasa por depurar.pl (llaves, tokens, KEY=valor, contraseñas del Wi-Fi, IPv6 de
# enlace local, /Users/<nombre>, nombre de esta Mac), las MAC quedan en 02:00:5e:00:00:00 y al
# final `depurar.pl --buscar` revisa lo escrito: si encuentra algo, BORRA la captura y falla.
# No hay opción para guardar sin depurar ni para añadir órdenes: si hace falta otra, se agrega
# aquí (con su depuración) y se revisa en el commit.
set -euo pipefail
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPURAR="$MAC_DIR/depurar.pl"
grupo="${1:-}"; shift || true
salida=""
while [ $# -gt 0 ]; do
  case "$1" in
    --salida) salida="$2"; shift ;;
    *) echo "opción desconocida: $1 (no hay modo sin depurar)" >&2; exit 2 ;;
  esac
  shift
done
case "$grupo" in ""|-*|*/*|*..*) echo "uso: capturar-muestras.sh GRUPO [--salida DIR]" >&2; exit 2 ;; esac
dest="${salida:-$MAC_DIR/pruebas/muestras/$grupo}"
mkdir -p "$dest"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export LC_ALL=C LANG=C

# Muestra = depurada + MAC sin ningún byte del equipo.
guardar() {  # guardar ARCHIVO [--procesos] < salida
  local f="$1"; shift
  perl "$DEPURAR" "$@" | perl -pe 's{\bxx([:\-])xx\1xx\1xx\1[0-9A-Fa-f]{1,2}\1[0-9A-Fa-f]{1,2}\b}{join($1, qw(02 00 5e 00 00 00))}ge' > "$TMP/$f"
}
leer() { "$@" 2>&1 || true; }

leer /sbin/ifconfig | guardar ifconfig.txt
leer /usr/sbin/netstat -rn -f inet | guardar netstat-rn-inet.txt
leer /usr/sbin/netstat -anv -p tcp | awk 'NR<=2 || /LISTEN/' | guardar netstat-anv-tcp-listen.txt
leer /usr/sbin/scutil --dns | guardar scutil-dns.txt
leer /usr/sbin/scutil --nc list | guardar scutil-nc-list.txt
leer /usr/bin/systemextensionsctl list | guardar systemextensionsctl-list.txt
leer /bin/ps -axo comm= | guardar ps-axo-comm.txt --procesos
FW=/usr/libexec/ApplicationFirewall/socketfilterfw
leer "$FW" --getglobalstate | guardar fw-getglobalstate.txt
leer "$FW" --getblockall | guardar fw-getblockall.txt
leer "$FW" --getstealthmode | guardar fw-getstealthmode.txt
leer "$FW" --listapps | guardar fw-listapps.txt
leer /usr/bin/fdesetup status | guardar fdesetup-status.txt
leer /usr/sbin/sharing -l | guardar sharing-l.txt
leer /usr/sbin/cupsctl | guardar cupsctl.txt
leer /usr/sbin/sysadminctl -screenLock status | guardar sysadminctl-screenlock.txt
leer /usr/bin/defaults read com.apple.screensaver | guardar defaults-screensaver.txt
leer /usr/bin/defaults read com.apple.sharingd DiscoverableMode | guardar defaults-sharingd-discoverable.txt
# De estas dos sólo las claves que miran los analizadores (el resto puede traer rutas y nombres).
{ echo '{'; leer /usr/bin/defaults read com.apple.amp.mediasharingd | grep -E '^[[:space:]]*"?[a-z-]*sharing-enabled"?[[:space:]]*=' || true; echo '}'; } | guardar defaults-mediasharingd.txt
{ echo '{'; leer /usr/bin/defaults read /Library/Preferences/com.apple.loginwindow | grep -E '^[[:space:]]*GuestEnabled[[:space:]]*=' || true; echo '}'; } | guardar defaults-loginwindow.txt
# launchctl print trae «arguments» y «environment» del servicio: se quitan esos bloques.
leer /bin/launchctl print system/com.apple.screensharing \
  | awk '/^[[:space:]]*(arguments|environment|inherited environment|default environment)[[:space:]]*=[[:space:]]*\{/ {skip=1; next} skip && /^[[:space:]]*\}/ {skip=0; next} !skip' \
  | guardar launchctl-screensharing.txt
leer /sbin/route -n get default | guardar route-get-default.txt
leer /usr/sbin/arp -an | guardar arp-an.txt
leer /usr/sbin/networksetup -getdnsservers Wi-Fi | guardar networksetup-getdnsservers.txt

# Última revisión: si queda algo con forma de secreto, no se escribe nada.
if ! perl "$DEPURAR" --buscar "$TMP"/*.txt >"$TMP/hallazgos" 2>&1; then
  echo "La captura todavía tiene algo con forma de secreto (no se guardó nada):" >&2
  sed "s|$TMP/||" "$TMP/hallazgos" >&2
  exit 1
fi
cp "$TMP"/*.txt "$dest/"
echo "Muestras depuradas en $dest ($(ls "$TMP"/*.txt | wc -l | tr -d ' ') archivos). Revísalas antes de añadirlas al repo:"
echo "  nodo-evento/mac/pruebas/buscar-secretos.sh"
