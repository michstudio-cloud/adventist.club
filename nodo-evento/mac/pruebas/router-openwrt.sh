#!/usr/bin/env bash
# Prueba SIN router: corre el guion de «Configurar router» dos veces dentro de un OpenWrt real
# (imagen oficial openwrt/rootfs, con uci de verdad) y comprueba el resultado y que repetirlo no
# cambia nada. dnsmasq y `wifi` se sustituyen por stubs (no hay procd en un contenedor).
#   pruebas/router-openwrt.sh            (usa el docker del perfil de Colima «nodo»)
set -euo pipefail
MOTOR="$(cd "$(dirname "$0")/.." && pwd)/nodo-mac.sh"
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/nodo/docker.sock}"
IMG="openwrt/rootfs:armsr-armv8"
IP=192.168.8.150; MAC=aa:bb:cc:dd:ee:01
GUION="$(NODO_ROUTER_IP_OVERRIDE=$IP NODO_ROUTER_MAC_OVERRIDE=$MAC WIFI_SSID='Camporee NTAM' WIFI_PASSWORD='clave de prueba' \
  NODO_APP_SUPPORT="$(mktemp -d)" DOMINIOS="events.adventist.club api.adventist.club admin.adventist.club" bash -c '
  cfg="$NODO_APP_SUPPORT/config.env"; printf "WIFI_SSID=%q\nDOMINIOS=%q\n" "$WIFI_SSID" "$DOMINIOS" > "$cfg"; "'"$MOTOR"'" router-guion')"
docker run --rm -i "$IMG" /bin/sh -s <<SH
set -e
# Estado inicial parecido a un GL.iNet con algo de uso previo
cat > /etc/config/wireless <<'W'
config wifi-device 'mt798111'
	option type 'mtwifi'
config wifi-iface 'default_radio0'
	option device 'mt798111'
	option network 'lan'
	option mode 'ap'
	option ssid 'GL-MT3000-abc'
	option encryption 'psk2'
	option key 'goodlife'
config wifi-iface 'default_radio1'
	option device 'mt798112'
	option network 'lan'
	option mode 'ap'
	option ssid 'GL-MT3000-abc-5G'
	option encryption 'psk2'
	option key 'goodlife'
config wifi-iface 'guest2g'
	option device 'mt798111'
	option network 'guest'
	option mode 'ap'
	option ssid 'GL-Guest'
W
uci add dhcp host >/dev/null; uci set dhcp.@host[-1].name=vieja; uci set dhcp.@host[-1].mac='AA:BB:CC:DD:EE:01'; uci set dhcp.@host[-1].ip=192.168.8.99
uci add dhcp host >/dev/null; uci set dhcp.@host[-1].name=impresora; uci set dhcp.@host[-1].mac='11:22:33:44:55:66'; uci set dhcp.@host[-1].ip=192.168.8.20
uci add_list dhcp.@dnsmasq[0].address='/events.adventist.club/192.168.8.99'
uci add_list dhcp.@dnsmasq[0].address='/otro.ejemplo/10.0.0.1'
uci commit
printf '#!/bin/sh\nexit 0\n' > /etc/init.d/dnsmasq; chmod +x /etc/init.d/dnsmasq
printf '#!/bin/sh\nexit 0\n' > /sbin/wifi; chmod +x /sbin/wifi
cat > /tmp/guion.sh <<'G'
$GUION
G
echo "--- 1ª vez"; sh /tmp/guion.sh
uci export dhcp > /tmp/d1; uci export wireless > /tmp/w1
echo "--- 2ª vez"; sh /tmp/guion.sh
uci export dhcp > /tmp/d2; uci export wireless > /tmp/w2
fail=0
cmp -s /tmp/d1 /tmp/d2 && cmp -s /tmp/w1 /tmp/w2 && echo "OK idempotente (2ª vez = mismo resultado)" || { echo "FALLA: la 2ª vez cambió algo"; diff /tmp/d1 /tmp/d2; fail=1; }
[ "\$(uci get dhcp.nodo_camporee.ip)" = $IP ] && [ "\$(uci get dhcp.nodo_camporee.mac)" = $MAC ] && echo "OK reserva $MAC -> $IP" || { echo FALLA reserva; fail=1; }
uci show dhcp | grep -q "name='vieja'" && { echo "FALLA: quedó la reserva vieja de la misma MAC"; fail=1; } || echo "OK reserva vieja de la misma MAC quitada"
uci show dhcp | grep -q "name='impresora'" && echo "OK otras reservas intactas" || { echo FALLA impresora; fail=1; }
addr="\$(uci get dhcp.@dnsmasq[0].address)"; echo "address = \$addr"
for d in events.adventist.club api.adventist.club admin.adventist.club; do
  n=\$(echo "\$addr" | tr ' ' '\n' | grep -c "^/\$d/"); v=\$(echo "\$addr" | tr ' ' '\n' | grep "^/\$d/")
  [ "\$n" = 1 ] && [ "\$v" = "/\$d/$IP" ] && echo "OK dns \$d -> $IP" || { echo "FALLA dns \$d (\$n entradas: \$v)"; fail=1; }
done
echo "\$addr" | grep -q '/otro.ejemplo/10.0.0.1' && echo "OK otras entradas DNS intactas" || { echo FALLA otras dns; fail=1; }
[ "\$(uci get wireless.default_radio0.ssid)" = 'Camporee NTAM' ] && [ "\$(uci get wireless.default_radio1.ssid)" = 'Camporee NTAM' ] && [ "\$(uci get wireless.default_radio1.key)" = 'clave de prueba' ] && echo "OK Wi-Fi 2.4/5 GHz renombrado con contraseña" || { echo FALLA wifi; fail=1; }
[ "\$(uci get wireless.guest2g.ssid)" = 'GL-Guest' ] && echo "OK red de invitados intacta" || { echo FALLA invitados; fail=1; }
exit \$fail
SH
