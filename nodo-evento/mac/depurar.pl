#!/usr/bin/perl
# Depurador de textos de la Mac (docs/EGRESO.md). Quita de cualquier salida de órdenes del sistema
# (ps, netstat, scutil, ifconfig, arp, launchctl, systemextensionsctl, router, registros de Caddy/API)
# lo que no debe salir de la Mac ni quedar en una muestra de pruebas:
#   llaves y tokens (AIza…, AQ.…, ya29., ghp_/gho_/github_pat_, sk-…, xox?-, AKIA…, Bearer/Basic,
#   cabeceras X-…-Key/Token/Secret, Authorization, Cookie), JWT (eyJ…), pares KEY=valor de entorno,
#   banderas --token/--password/--api-key, credenciales en URLs, bloques PEM privados, base64 largo
#   junto a key/token/secret, contraseñas del Wi-Fi (uci, MikroTik, QR WIFI:), MAC (se dejan los
#   2 últimos bytes), IPv6 de enlace local, rutas /Users/<nombre> y el nombre/usuario de esta Mac.
# Es el MISMO criterio que app/Depurador.swift (la app) y backend/app/services/nodo_egreso.py
# (servidor); los casos de prueba compartidos están en pruebas/depurador/.
#
#   depurar.pl [--procesos] [--sin-equipo] [archivo…]   → texto depurado por stdout (stdin si no hay archivos)
#       --procesos   además deja sólo el ejecutable de cada línea (sin argumentos: ps -axo args/command)
#       --sin-equipo no busca el nombre de esta Mac ni del usuario (pruebas reproducibles)
#   depurar.pl --buscar archivo…  → informa «archivo:línea: tipo» (NUNCA el valor) y sale con 1 si hay algo
#       --solo-tokens  sólo llaves/tokens con forma inconfundible (para revisar cualquier archivo del repo)
use strict;
use warnings;

my $O = '[oculto]';
my ($procesos, $buscar, $sin_equipo, $solo_tokens) = (0, 0, 0, 0);
my @archivos;
for my $a (@ARGV) {
    if ($a eq '--procesos') { $procesos = 1 }
    elsif ($a eq '--buscar') { $buscar = 1 }
    elsif ($a eq '--sin-equipo') { $sin_equipo = 1 }
    elsif ($a eq '--solo-tokens') { $solo_tokens = 1 }
    elsif ($a eq '--') { next }
    else { push @archivos, $a }
}

# --- Identidad de esta Mac (nombre del equipo, usuario, nombre completo) ----------------------
my @equipo;
if (defined $ENV{NODO_DEPURAR_EQUIPO}) {
    # Pruebas: nombres fijos separados por «|» en vez de los de esta Mac.
    @equipo = sort { length($b) <=> length($a) } grep { length($_) >= 3 } split /\|/, $ENV{NODO_DEPURAR_EQUIPO};
} elsif (!$sin_equipo && !$ENV{NODO_DEPURAR_SIN_EQUIPO}) {
    my @cand;
    if ($^O eq 'darwin') {
        for my $k (qw(ComputerName LocalHostName HostName)) {
            my $v = `/usr/sbin/scutil --get $k 2>/dev/null`; chomp $v; push @cand, $v;
        }
        my $full = `/usr/bin/id -F 2>/dev/null`; chomp $full; push @cand, $full;
    }
    my $h = `hostname 2>/dev/null`; chomp $h; push @cand, $h, (split /\./, $h)[0] // '';
    push @cand, scalar(getpwuid($<)) // '';
    my %visto;
    # Los más largos primero (para que «Mac de Ana» se reemplace antes que «Ana»).
    @equipo = sort { length($b) <=> length($a) }
              grep { length($_) >= 3 && !$visto{lc $_}++ && !/^(root|admin|test|localhost|USUARIO)$/i } @cand;
}

# --- Patrones de secretos: [tipo, regex, reemplazo] -------------------------------------------
# El reemplazo es una sub que recibe los grupos; así el valor nunca se copia a la salida.
my $B64 = qr{[A-Za-z0-9+/_\-]};
my @SECRETOS = (
    ['bloque PEM privado', qr{-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----.*?(?:-----END \1PRIVATE KEY-----|\z)}s,
        sub { "-----BEGIN $_[0]PRIVATE KEY----- $O -----END $_[0]PRIVATE KEY-----" }],
    ['cabecera con secreto', qr{\b(X-[A-Za-z0-9\-]*(?:Key|Token|Secret|Signature|Auth[A-Za-z]*)|Authorization|Proxy-Authorization|Cookie|Set-Cookie)([ \t]*:[ \t]*)(?!\[oculto\])([^'"\s][^'"\r\n]*)}i,
        sub { "$_[0]$_[1]$O" }],
    ['credencial en URL', qr{(\b[a-z][a-z0-9+.\-]*://)[^/\s:@'"]*:[^/\s@'"]+@}i, sub { "$_[0]$O@" }],
    ['Bearer/Basic', qr{\b(Bearer|Basic)[ \t]+(?!\[oculto\])(?=[A-Za-z0-9\-._~+/]*[0-9\-._~+/])[A-Za-z0-9\-._~+/]{12,}=*}, sub { "$_[0] $O" }],
    ['llave de Google (AIza)', qr{AIza[0-9A-Za-z_\-]{30,}}, sub { $O }],
    ['token de Google (AQ.)', qr{\bAQ\.[0-9A-Za-z_\-.]{16,}}, sub { $O }],
    ['token OAuth de Google (ya29.)', qr{\bya29\.[0-9A-Za-z_\-.]{10,}}, sub { $O }],
    ['token de GitHub', qr{\b(?:gh[pousr]_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})}, sub { $O }],
    ['llave sk-', qr{\bsk-[0-9A-Za-z_\-]{16,}}, sub { $O }],
    ['token de Slack', qr{\bxox[abposr]-[0-9A-Za-z\-]{10,}}, sub { $O }],
    ['llave de AWS', qr{\b(?:AKIA|ASIA)[0-9A-Z]{16}\b}, sub { $O }],
    ['pase del evento (evp_)', qr{\bevp_[A-Za-z0-9_\-]{16,}}, sub { $O }],
    ['JWT', qr{\beyJ[0-9A-Za-z_\-]{8,}(?:\.[0-9A-Za-z_\-]+){0,2}}, sub { $O }],
    ['contraseña del Wi-Fi (QR)', qr{(WIFI:[^\n]*?\bP:)((?:\\;|[^;\n])+)}, sub { "$_[0]$O" }],
    ['contraseña del Wi-Fi (uci)', qr{(\boption[ \t]+(?:key|password|sae_password|wpa_passphrase)[ \t]+|\.(?:key|password|sae_password|wpa_passphrase)=)('[^'\n]*'|"[^"\n]*"|[^\s'"]+)},
        sub { my ($p, $v) = @_; my $q = $v =~ /^(['"])/ ? $1 : ''; "$p$q$O$q" }],
    ['contraseña del Wi-Fi (MikroTik/wpa)', qr{\b(wpa2?-pre-shared-key|passphrase|psk|wpa_passphrase|sae_password)=("[^"\n]*"|'[^'\n]*'|[^\s"';]+)},
        sub { "$_[0]=$O" }],
    ['variable de entorno con secreto', qr{\b([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PWD|CREDENTIALS?|DSN|PRIVATE|AUTH)[A-Z0-9_]*)=(?!\[oculto\])("[^"\n]*"|'[^'\n]*'|[^\s"']+)},
        sub { "$_[0]=$O" }],
    ['bandera con secreto', qr{((?<![\w-])--?(?:[A-Za-z0-9]+[-_])*(?:api[-_]?key|key|token|secret|password|passwd|pass|pwd|auth|credentials?)(?:=|[ \t]+))(?!\[oculto\])("[^"\n]*"|'[^'\n]*'|[^\s"']+)}i,
        sub { "$_[0]$O" }],
    ['base64 junto a key/token/secret', qr{(\b(?:[A-Za-z0-9]+[-_])*(?:key|token|secret|passw(?:or)?d|credentials?|signature|auth(?:orization)?)s?\b["']?[ \t]*[:=]?[ \t]*["']?)($B64{32,}={0,2})}i,
        sub { "$_[0]$O" }],
);

sub depurar_texto {
    my ($t) = @_;
    for my $p (@SECRETOS) {
        my ($re, $f) = ($p->[1], $p->[2]);
        $t =~ s{$re}{ $f->(map { defined $_ ? $_ : '' } ($1, $2, $3)) }ge;
    }
    # IPv6 de enlace local (antes que las MAC: fe80::a:b:c… no debe tomarse como MAC).
    $t =~ s{\bfe80:[0-9A-Fa-f:]*}{fe80::$O}gi;
    # MAC (también la forma sin ceros de `arp -an`): se dejan los dos últimos bytes para poder
    # reconocer un equipo sin identificarlo.
    $t =~ s{(?<![0-9A-Fa-f:\-])([0-9A-Fa-f]{1,2})([:\-])([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})\2([0-9A-Fa-f]{1,2})(?![0-9A-Fa-f:\-])}{xx$2xx$2xx$2xx$2$6$2$7}g;
    # Rutas personales.
    $t =~ s{/Users/(?!USUARIO\b|Shared\b)[^/\s"':]+}{/Users/USUARIO}g;
    $t =~ s{/home/(?!USUARIO\b)[^/\s"':]+}{/home/USUARIO}g;
    # Nombre del equipo y del usuario.
    for my $e (@equipo) { my $q = quotemeta $e; $t =~ s{(?<![A-Za-z0-9])$q(?![A-Za-z0-9])}{MAC-NODO}gi; }
    return $t;
}

# Deja sólo el ejecutable: corta antes del primer argumento (bandera, otra ruta, comillas,
# KEY=valor o URL). Las rutas con espacios («Google Chrome.app») se conservan.
sub solo_ejecutable {
    my ($l) = @_;
    my $nl = $l =~ s/\n\z// ? "\n" : "";
    # Columnas numéricas al principio (PID, PPID…) se conservan.
    my ($pre, $resto) = $l =~ /^(\s*(?:\d+\s+)*)(.*)$/s;
    $resto =~ s{\s+(?=-|/|"|'|[A-Za-z_][A-Za-z0-9_]*=|[a-z][a-z0-9+.\-]*://).*$}{}s;
    return "$pre$resto$nl";
}

sub leer {
    my ($ruta) = @_;
    local $/;
    if (defined $ruta) {
        open(my $fh, '<:raw', $ruta) or do { warn "no pude leer $ruta\n"; return undef };
        my $t = <$fh>; close $fh; return $t // '';
    }
    binmode STDIN, ':raw';
    my $t = <STDIN>; return $t // '';
}

if ($buscar) {
    # Detección: sólo secretos (no MAC/IPv6/rutas: las muestras ya vienen enmascaradas), más las
    # rutas personales con un nombre real y el nombre de esta Mac.
    my $hay = 0;
    my $placeholder = qr{^(?:\[oculto\]|<[^>]*>|\$\{?[A-Za-z_]|…|\.\.\.|x+$|X+$|\*+$|USUARIO|CAMBIA|changeme|example|ejemplo|dummy|prueba)}i;
    for my $ruta (@archivos) {
        my $t = leer($ruta); next unless defined $t;
        next if $t =~ /\x00/;   # binario
        my @hall;
        for my $p (@SECRETOS) {
            my ($tipo, $re) = @$p;
            next if $solo_tokens && $tipo !~ /PEM|Google|GitHub|sk-|Slack|AWS|JWT/;
            while ($t =~ m{$re}g) {
                # Los grupos se copian ANTES de cualquier otra coincidencia (que los borraría).
                my ($g2, $g3, $fin, $ini) = ($2, $3, pos($t), $-[0]);
                my $con_valor = $tipo =~ /entorno|bandera|base64|Wi-Fi/;
                my $valor = ($con_valor && defined $g2) ? $g2 : substr($t, $ini, $fin - $ini);
                $valor =~ s/^['"]|['"]$//g;
                next if $tipo =~ /entorno|bandera|Wi-Fi/ && (length($valor) < 8 || $valor =~ $placeholder);
                next if $tipo eq 'cabecera con secreto' && (length($g3 // '') < 8 || ($g3 // '') =~ $placeholder);
                next if $tipo eq 'Bearer/Basic' && $valor =~ /\[oculto\]/;
                next if $tipo eq 'bloque PEM privado' && $valor =~ /PRIVATE KEY-----\s*\[oculto\]\s*-----END/;
                my $linea = 1 + (substr($t, 0, $ini) =~ tr/\n//);
                push @hall, [$linea, $tipo];
            }
        }
        if ($solo_tokens) { goto INFORMAR }
        # IPv6 de enlace local con la MAC adentro (EUI-64: …ff:fe…): identifica la tarjeta de red.
        while ($t =~ m{\bfe80::(?:[0-9a-f]{1,4}:){0,2}[0-9a-f]{0,2}ff:fe[0-9a-f]{1,2}:[0-9a-f]{1,4}}gi) {
            push @hall, [1 + (substr($t, 0, $-[0]) =~ tr/\n//), 'IPv6 con la MAC (EUI-64)'];
        }
        while ($t =~ m{/Users/(?!USUARIO\b|Shared\b|runner\b)[A-Za-z][^/\s"':]*}g) {
            push @hall, [1 + (substr($t, 0, $-[0]) =~ tr/\n//), 'ruta con nombre de usuario'];
        }
        for my $e (@equipo) {
            my $q = quotemeta $e;
            while ($t =~ m{(?<![A-Za-z0-9])$q(?![A-Za-z0-9])}gi) {
                push @hall, [1 + (substr($t, 0, $-[0]) =~ tr/\n//), 'nombre de esta Mac o de su usuario'];
            }
        }
        INFORMAR:
        my %uno;
        for my $h (sort { $a->[0] <=> $b->[0] } @hall) {
            next if $uno{"$h->[0] $h->[1]"}++;
            print "$ruta:$h->[0]: $h->[1]\n"; $hay = 1;
        }
    }
    exit($hay ? 1 : 0);
}

my @entradas = @archivos ? @archivos : (undef);
for my $ruta (@entradas) {
    my $t = leer($ruta); next unless defined $t;
    if ($procesos) { $t = join('', map { solo_ejecutable($_) } split(/(?<=\n)/, $t)); $t =~ s/[ \t]+$//mg; }
    print depurar_texto($t);
}
