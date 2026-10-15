#!/usr/bin/env bash
# Prueba SIN router de la «Red pública del evento» en un OpenWrt real (imagen openwrt/rootfs, uci y
# dnsmasq de verdad): aplica el guion dos veces (idempotente), revisa reserva, DNS con lista permitida
# + sumidero, etiqueta DHCP de la Mac, reglas de firewall y aislamiento de Wi-Fi; arranca dnsmasq
# con esas direcciones y pregunta (permitido → nodo, cualquier otro → 0.0.0.0, portal → nodo);
# luego «quitar» y comprueba que vuelve a lo normal.
#   pruebas/red-publica-openwrt.sh            (usa el docker del perfil de Colima «nodo»)
set -euo pipefail
MOTOR="$(cd "$(dirname "$0")/.." && pwd)/nodo-mac.sh"
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/nodo/docker.sock}"
IMG="openwrt/rootfs:armsr-armv8"
IP=192.168.8.150; MAC=aa:bb:cc:dd:ee:01
TMPS="$(mktemp -d)"; trap 'rm -rf "$TMPS"' EXIT
cfg="$TMPS/config.env"
printf 'PUBLICA_SSID=%q\nPUBLICA_ABIERTA=1\nRED_PORTAL=exito\nPUBLICA_SOLO_MAC=1\nDOMINIOS=%q\n' 'Camporee Universo de Dios' \
  "events.adventist.club api.adventist.club admin.adventist.club media.adventist.club" > "$cfg"
GUION="$(NODO_ROUTER_IP_OVERRIDE=$IP NODO_ROUTER_MAC_OVERRIDE=$MAC NODO_APP_SUPPORT="$TMPS" "$MOTOR" red-publica-guion --tipo glinet)"
QUITAR="$(NODO_ROUTER_IP_OVERRIDE=$IP NODO_ROUTER_MAC_OVERRIDE=$MAC NODO_APP_SUPPORT="$TMPS" "$MOTOR" red-publica-quitar --tipo glinet --simular \
  | sed -n '/^-----$/,/^-----$/p' | sed '1d;$d')"
docker run --rm -i "$IMG" /bin/sh -s <<SH
set -e
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
config wifi-iface 'guest2g'
	option device 'mt798111'
	option network 'guest'
	option mode 'ap'
	option ssid 'GL-Guest'
W
uci add_list dhcp.@dnsmasq[0].address='/otro.ejemplo/10.0.0.1'
touch /etc/config/firewall
uci add firewall rule >/dev/null; uci set firewall.@rule[-1].name='Allow-Ping'
uci commit
printf '#!/bin/sh\nexit 0\n' > /etc/init.d/dnsmasq; chmod +x /etc/init.d/dnsmasq
printf '#!/bin/sh\nexit 0\n' > /etc/init.d/firewall; chmod +x /etc/init.d/firewall
printf '#!/bin/sh\nexit 0\n' > /sbin/wifi; chmod +x /sbin/wifi
cat > /tmp/guion.sh <<'G'
$GUION
G
cat > /tmp/quitar.sh <<'G'
$QUITAR
G
fail=0
echo "--- 1ª vez"; sh /tmp/guion.sh
uci export dhcp > /tmp/d1; uci export wireless > /tmp/w1; uci export firewall > /tmp/f1
echo "--- 2ª vez"; sh /tmp/guion.sh
uci export dhcp > /tmp/d2; uci export wireless > /tmp/w2; uci export firewall > /tmp/f2
cmp -s /tmp/d1 /tmp/d2 && cmp -s /tmp/w1 /tmp/w2 && cmp -s /tmp/f1 /tmp/f2 && echo "OK idempotente" || { echo "FALLA idempotencia"; diff /tmp/d1 /tmp/d2 || true; diff /tmp/f1 /tmp/f2 || true; fail=1; }
[ "\$(uci get dhcp.nodo_camporee.ip)" = $IP ] && echo "OK reserva -> $IP" || { echo FALLA reserva; fail=1; }
[ "\$(uci get dhcp.nodo_camporee.tag)" = nodocamporee ] && uci get dhcp.nodocamporee.dhcp_option | grep -q '6,1.1.1.1' && echo "OK la Mac recibe DNS públicos (etiqueta DHCP)" || { echo FALLA etiqueta; fail=1; }
[ "\$(uci get firewall.nodo_dns.src_ip)" = '!$IP' ] && [ "\$(uci get firewall.nodo_dns.dest_port)" = '53 853' ] && echo "OK sin desvíos de DNS (salvo la Mac)" || { echo FALLA firewall dns; fail=1; }
uci get firewall.nodo_doh.dest_ip | grep -q '8.8.8.8' && echo "OK DoH conocidos bloqueados" || { echo FALLA doh; fail=1; }
[ "\$(uci get firewall.nodo_solo_mac.target)" = REJECT ] && echo "OK sólo la Mac sale a internet" || { echo FALLA solo mac; fail=1; }
uci show firewall | grep -q "name='Allow-Ping'" && echo "OK otras reglas intactas" || { echo FALLA otras reglas; fail=1; }
[ "\$(uci get wireless.default_radio0.isolate)" = 1 ] && [ "\$(uci get wireless.default_radio0.encryption)" = none ] && [ "\$(uci get wireless.default_radio0.ssid)" = 'Camporee Universo de Dios' ] && echo "OK Wi-Fi abierto, aislado y renombrado" || { echo FALLA wifi; fail=1; }
[ "\$(uci get wireless.guest2g.ssid)" = 'GL-Guest' ] && [ -z "\$(uci -q get wireless.guest2g.isolate)" ] && echo "OK red de invitados intacta" || { echo FALLA invitados; fail=1; }
# dnsmasq de verdad con las mismas direcciones
args=""; for v in \$(uci get dhcp.@dnsmasq[0].address); do args="\$args --address=\$v"; done
dnsmasq --no-resolv --no-hosts -p 5353 --listen-address=127.0.0.1 --bind-interfaces \$args --pid-file=/tmp/dm.pid
sleep 1
q() { nslookup -port=5353 "\$1" 127.0.0.1 2>/dev/null | awk '/^Address/ {print \$NF}' | grep -v ':5353' | tail -1; }
for d in events.adventist.club api.adventist.club admin.adventist.club media.adventist.club nodo.local captive.apple.com connectivitycheck.gstatic.com; do
  [ "\$(q \$d)" = $IP ] && echo "OK dnsmasq: \$d -> $IP" || { echo "FALLA dnsmasq \$d -> \$(q \$d)"; fail=1; }
done
for d in example.com www.facebook.com dns.google; do
  [ "\$(q \$d)" = 0.0.0.0 ] && echo "OK dnsmasq: \$d -> 0.0.0.0 (bloqueado)" || { echo "FALLA dnsmasq \$d -> \$(q \$d)"; fail=1; }
done
kill \$(cat /tmp/dm.pid)
echo "--- quitar"; sh /tmp/quitar.sh
addr="\$(uci get dhcp.@dnsmasq[0].address)"
echo "\$addr" | grep -q '/#/' && { echo "FALLA: quedó el sumidero"; fail=1; } || echo "OK sumidero quitado"
echo "\$addr" | grep -q '/events.adventist.club/$IP' && echo "OK nombres del evento siguen (configuración normal)" || { echo FALLA normal; fail=1; }
echo "\$addr" | grep -q 'captive.apple.com' && { echo FALLA portal; fail=1; } || echo "OK portal quitado"
uci -q get firewall.nodo_dns >/dev/null && { echo FALLA firewall; fail=1; } || echo "OK reglas nodo_* quitadas"
[ -z "\$(uci -q get wireless.default_radio0.isolate)" ] && echo "OK aislamiento quitado" || { echo FALLA isolate; fail=1; }
exit \$fail
SH
