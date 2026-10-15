#!/usr/bin/perl
# Pruebas de depurar.pl con los casos compartidos (casos.txt). Además comprueba que lo depurado
# pasa limpio por `depurar.pl --buscar` y que la entrada con secretos NO pasa.
#   perl pruebas/depurador/probar.pl
use strict;
use warnings;
use File::Basename qw(dirname);
use File::Temp qw(tempdir);

my $aqui = dirname(__FILE__);
my $mac = "$aqui/../..";
my $depurar = "$mac/depurar.pl";
$ENV{NODO_DEPURAR_EQUIPO} = "Mac de Prueba \x{d1}and\x{fa}|ana-prueba";
utf8::encode($ENV{NODO_DEPURAR_EQUIPO});

# Secretos de mentira armados aquí (nunca escritos tal cual en el repo). Mismo valor en las tres
# implementaciones de las pruebas (perl, Swift, Python).
sub repetir { my ($s, $n) = @_; substr($s x (int($n / length $s) + 1), 0, $n) }
my %M = (
    AIZA  => 'AI' . 'za' . repetir('Xy9_', 35),
    AQ    => 'AQ' . '.' . repetir('Ab3-', 40),
    YA29  => 'ya' . '29.' . repetir('a0B_', 32),
    GHP   => 'gh' . 'p_' . repetir('Z9y8', 36),
    GHO   => 'gh' . 'o_' . repetir('K7j6', 36),
    GHPAT => 'github' . '_pat_' . repetir('11AB', 48),
    SK    => 'sk' . '-ant-' . repetir('q1W2', 32),
    XOX   => 'xo' . 'xb-' . repetir('12345-', 24),
    JWT   => 'ey' . 'JhbGciOiJIUzI1NiJ9.' . 'ey' . 'JzdWIiOiIxMjM0NTY3ODkwIn0.' . repetir('dGVzdA', 30),
    B64   => repetir('QUJD', 48),
    PEM   => "-----BEGIN " . "PRIVATE KEY-----\n" . repetir('MIIEvQ', 64) . "\n" . repetir('AbCd', 64) . "\n-----END " . "PRIVATE KEY-----",
    AWS   => 'AK' . 'IA' . 'ABCDEFGHIJKLMNOP',
    EVP   => 'evp' . '_' . repetir('Pq7-', 43),
);
sub expandir { my ($t) = @_; $t =~ s/\{\{([A-Z0-9]+)\}\}/exists $M{$1} ? $M{$1} : die "marcador $1"/ge; $t }

open(my $fh, '<:raw', "$aqui/casos.txt") or die "no encuentro casos.txt";
my (@casos, $c, $sec);
while (my $l = <$fh>) {
    next if !$c && $l =~ /^#(?!##)/;
    if ($l =~ /^### (.*)/) { $c = { nombre => $1 }; push @casos, $c; $sec = undef; next }
    if ($l =~ /^--- (entrada|esperado|procesos)\s*$/) { $sec = $1; $c->{$sec} = ''; next }
    $c->{$sec} .= $l if $c && $sec;
}
my $dir = tempdir(CLEANUP => 1);
my ($ok, $mal, $n) = (0, 0, 0);
sub correr {
    my ($texto, @args) = @_;
    my $f = "$dir/t" . $n++;
    open(my $o, '>:raw', $f) or die; print $o $texto; close $o;
    my $salida = `perl "$depurar" @args "$f"`;
    return ($salida, $? >> 8);
}
for my $c (@casos) {
    my $entrada = expandir($c->{entrada});
    for my $modo (['esperado', ()], ['procesos', '--procesos']) {
        my ($clave, @args) = @$modo;
        next unless defined $c->{$clave};
        my ($salida) = correr($entrada, @args);
        if ($salida eq $c->{$clave}) { $ok++ }
        else { $mal++; print "✗ $c->{nombre} ($clave)\n--- obtuvo\n$salida--- esperaba\n$c->{$clave}" }
        my ($b, $codigo) = correr($salida, '--buscar');
        if ($codigo == 0) { $ok++ } else { $mal++; print "✗ $c->{nombre} ($clave): lo depurado no pasa --buscar\n$b" }
    }
    if ($entrada =~ /\{|[A-Za-z]/ && $entrada ne $c->{esperado}) {
        my ($b, $codigo) = correr($entrada, '--buscar');
        my $hay_secreto = $c->{esperado} =~ /(?<!fe80::)\[oculto\]|MAC-NODO|USUARIO/;
        if (!$hay_secreto || $codigo == 1) { $ok++ } else { $mal++; print "✗ $c->{nombre}: --buscar no vio nada en la entrada\n" }
    }
}
# Incidente real: la línea de `ps -axo pid,args` con --header X-Goog-Api-Key: AQ.… nunca sale tal cual.
my ($s) = correr("  1 /usr/bin/curl --header X-Goog-Api-Key: $M{AQ}\n", '--procesos');
if (index($s, $M{AQ}) < 0 && index($s, 'X-Goog') < 0) { $ok++ } else { $mal++; print "✗ incidente: quedó la llave o la cabecera\n" }
print "depurar.pl: $ok bien, $mal mal\n";
exit($mal ? 1 : 0);
