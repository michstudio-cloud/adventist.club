#!/usr/bin/env bash
# Pruebas del motor (v3) sin tocar la instalación real (otra carpeta de datos, otro prefijo de
# LaunchAgents):
#   • bloqueo en el router por MAC (GL.iNet/OpenWrt y MikroTik) — sólo el guion (--simular), y
#     también en un OpenWrt de verdad (imagen openwrt/rootfs) si está a la mano: uci + idempotencia;
#   • contraseña del Wi-Fi: validación y guion (la clave nunca aparece en la salida);
#   • imágenes versionadas: un manifiesto con una imagen mínima servida en 127.0.0.1 → se carga sólo
#     si falta, se verifica el sha256 (un archivo alterado se rechaza) y la 2.ª vez «al día».
# Uso: ./nodo-evento/mac/pruebas/v3-motor.sh    (necesita Colima perfil «nodo» encendido)
set -uo pipefail
MAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
M="$MAC_DIR/nodo-mac.sh"
T="$(mktemp -d "${HOME}/.nodo-v3-prueba.XXXXXX")"   # bajo $HOME: Colima lo ve
export NODO_APP_SUPPORT="$T/soporte" NODO_AGENT_PREFIX="club.adventist.nodo-camporee.prueba-v3"
PUERTO="${PUERTO:-18766}"; SRV=""
export DOCKER_HOST="unix://$HOME/.colima/nodo/docker.sock"
limpiar() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null; docker rmi -f nodo-v3-prueba:1 >/dev/null 2>&1; docker rm -f nodo-v3-openwrt >/dev/null 2>&1; rm -rf "$T"; }
trap limpiar EXIT
fallas=0
ok() { printf '  ✓ %s\n' "$*"; }
mal() { printf '  ✗ %s\n' "$*"; fallas=$((fallas + 1)); }
contiene() { printf '%s' "$1" | grep -qF -- "$2"; }

echo "1) Bloqueo en el router (guion)"
g="$("$M" router-bloquear --mac AA:BB:CC:11:22:33 --simular --tipo glinet)"
contiene "$g" "macfilter='deny'" && contiene "$g" "ban_time" && contiene "$g" "aa:bb:cc:11:22:33" && ok "GL.iNet/OpenWrt: lista negra + expulsión sin recargar a todos" || mal "guion OpenWrt"
contiene "$g" "wifi reload" && mal "bloquear NO debe recargar el Wi-Fi" || ok "bloquear no recarga el Wi-Fi de los demás"
g="$("$M" router-bloquear --mac AA:BB:CC:11:22:33 --quitar --simular --tipo openwrt)"
contiene "$g" "del_list" && ok "OpenWrt: quitar" || mal "quitar OpenWrt"
g="$("$M" router-bloquear --mac aa:bb:cc:11:22:33 --simular --tipo mikrotik)"
contiene "$g" 'access-list add mac-address=AA:BB:CC:11:22:33' && contiene "$g" 'comment="nodo-bloqueo"' && ok "MikroTik: access-list (RouterOS 7 y 6)" || mal "guion MikroTik"
"$M" router-bloquear --mac "zz:zz" --simular >/dev/null 2>&1 && mal "aceptó una MAC inválida" || ok "MAC inválida rechazada"
"$M" router-bloquear --mac AA:BB:CC:11:22:33 --simular --tipo dns >/dev/null 2>&1 && mal "aceptó un router manual" || ok "router manual (Google Wifi): explica que se pausa en Google Home"

echo "2) Contraseña del Wi-Fi"
out="$(WIFI_PASSWORD='pino-faro-4217' "$M" wifi-clave --simular --tipo glinet --ssid 'Camporee Zona A')"
contiene "$out" "psk2" && ok "WPA2 (psk2) en las redes de la LAN" || mal "guion wifi"
contiene "$out" "pino-faro-4217" && mal "¡la clave aparece en la salida!" || ok "la clave no aparece en la salida (••••)"
out="$(WIFI_PASSWORD='pino-faro-4217' "$M" wifi-clave --simular --tipo mikrotik)"
contiene "$out" "wpa2-psk" && ok "MikroTik: wpa2-psk" || mal "guion MikroTik wifi"
WIFI_PASSWORD='corta' "$M" wifi-clave --simular >/dev/null 2>&1 && mal "aceptó una clave corta" || ok "clave de menos de 8 rechazada"
WIFI_PASSWORD='con"comillas' "$M" wifi-clave --simular >/dev/null 2>&1 && mal "aceptó comillas" || ok "clave con comillas rechazada"

echo "3) OpenWrt de verdad (uci): bloquear dos veces y quitar"
if docker image inspect openwrt/rootfs:armsr-armv8 >/dev/null 2>&1; then
  docker run -d --name nodo-v3-openwrt openwrt/rootfs:armsr-armv8 /bin/sh -c 'mkdir -p /var/lock /tmp; sleep 300' >/dev/null
  docker exec nodo-v3-openwrt sh -c 'touch /etc/config/wireless; uci set wireless.ap0=wifi-iface; uci set wireless.ap0.mode=ap; uci set wireless.ap0.network=lan; uci set wireless.sta=wifi-iface; uci set wireless.sta.mode=sta; uci commit wireless; printf "#!/bin/sh\nexit 0\n" > /sbin/wifi; chmod +x /sbin/wifi' 
  for i in 1 2; do "$M" router-bloquear --mac AA:BB:CC:11:22:33 --simular --tipo openwrt | docker exec -i nodo-v3-openwrt sh -s >/dev/null; done
  lst="$(docker exec nodo-v3-openwrt uci -q get wireless.ap0.maclist)"; f="$(docker exec nodo-v3-openwrt uci -q get wireless.ap0.macfilter)"
  [ "$lst" = "aa:bb:cc:11:22:33" ] && [ "$f" = deny ] && ok "uci: maclist una sola vez (idempotente) y macfilter=deny" || mal "uci tras bloquear: «$lst» «$f»"
  [ -z "$(docker exec nodo-v3-openwrt uci -q get wireless.sta.maclist)" ] && ok "no toca la interfaz cliente (sta)" || mal "tocó sta"
  "$M" router-bloquear --mac AA:BB:CC:11:22:33 --quitar --simular --tipo openwrt | docker exec -i nodo-v3-openwrt sh -s >/dev/null
  [ -z "$(docker exec nodo-v3-openwrt uci -q get wireless.ap0.maclist)" ] && [ -z "$(docker exec nodo-v3-openwrt uci -q get wireless.ap0.macfilter)" ] && ok "quitar deja el Wi-Fi como estaba" || mal "quitar"
  docker exec nodo-v3-openwrt sh -c 'uci set wireless.ap0.macfilter=allow; uci commit wireless'
  o="$("$M" router-bloquear --mac AA:BB:CC:44:55:66 --simular --tipo openwrt | docker exec -i nodo-v3-openwrt sh -s 2>&1)"
  contiene "$o" "lista PERMITIDA" && [ "$(docker exec nodo-v3-openwrt uci -q get wireless.ap0.macfilter)" = allow ] && ok "con lista permitida (allow) no la cambia y avisa" || mal "allow"
  WIFI_PASSWORD='pino-faro-4217' "$M" wifi-clave --simular --tipo openwrt >/dev/null  # sólo validación
else
  echo "  (sin la imagen openwrt/rootfs: se salta)"
fi

echo "4) Imágenes versionadas"
mkdir -p "$T/img" "$T/web/v1"
printf 'hola\n' > "$T/img/x"; printf 'FROM scratch\nCOPY x /x\n' > "$T/img/Dockerfile"
docker build -q -t nodo-v3-prueba:1 "$T/img" >/dev/null || { mal "docker build"; exit 1; }
ID="$(docker image inspect --format '{{.Id}}' nodo-v3-prueba:1)"
docker save nodo-v3-prueba:1 | gzip -n > "$T/web/v1/9-prueba.tar.gz"
SHA="$(shasum -a 256 "$T/web/v1/9-prueba.tar.gz" | cut -d' ' -f1)"; TAM="$(stat -f %z "$T/web/v1/9-prueba.tar.gz")"
printf '{"version":"v1","archivos":[{"nombre":"9-prueba.tar.gz","imagen":"nodo-v3-prueba:1","id":"%s","sha256":"%s","tam":%s}]}\n' "$ID" "$SHA" "$TAM" > "$T/man.json"
( cd "$T/web" && exec python3 -m http.server "$PUERTO" --bind 127.0.0.1 >/dev/null 2>&1 ) & SRV=$!; sleep 1
docker rmi -f nodo-v3-prueba:1 >/dev/null
e="$("$M" imagenes-estado "$T/man.json")"
contiene "$e" '"faltan":1' && ok "estado: falta 1 imagen" || mal "estado: $e"
"$M" imagenes-actualizar --manifiesto "$T/man.json" --base "http://127.0.0.1:$PUERTO" >/dev/null 2>&1
[ "$(docker image inspect --format '{{.Id}}' nodo-v3-prueba:1 2>/dev/null)" = "$ID" ] && ok "bajó (127.0.0.1), verificó sha256 y cargó la imagen" || mal "no cargó"
[ -s "$NODO_APP_SUPPORT/imagenes/$SHA-9-prueba.tar.gz" ] && ok "quedó en la caché local (para cargar sin internet)" || mal "caché"
o="$("$M" imagenes-actualizar --manifiesto "$T/man.json" --base "http://127.0.0.1:1" 2>&1)"
contiene "$o" "al día" && ok "2.ª vez: al día (nada que bajar)" || mal "2.ª vez: $o"
docker rmi -f nodo-v3-prueba:1 >/dev/null
o="$("$M" imagenes-actualizar --manifiesto "$T/man.json" --base "http://127.0.0.1:1" 2>&1)"
[ "$(docker image inspect --format '{{.Id}}' nodo-v3-prueba:1 2>/dev/null)" = "$ID" ] && ok "sin internet: la cargó de la caché" || mal "sin internet: $o"
docker rmi -f nodo-v3-prueba:1 >/dev/null; rm -rf "$NODO_APP_SUPPORT/imagenes"
printf 'basura' >> "$T/web/v1/9-prueba.tar.gz"
o="$("$M" imagenes-actualizar --manifiesto "$T/man.json" --base "http://127.0.0.1:$PUERTO" 2>&1)"
docker image inspect nodo-v3-prueba:1 >/dev/null 2>&1 && mal "cargó un archivo alterado" || ok "archivo alterado (sha256 distinto): rechazado"
[ "$(cat "$NODO_APP_SUPPORT/run/imagenes-version" 2>/dev/null)" != "" ] && ok "versión cargada recordada (run/imagenes-version)" || mal "imagenes-version"

echo
[ $fallas = 0 ] && echo "Motor v3: todo bien" || { echo "Motor v3: $fallas falla(s)"; exit 1; }
