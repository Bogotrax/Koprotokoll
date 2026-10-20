#!/usr/bin/perl
# Gesicherter Stand von /tmp/relay-next.pl, nur Kommentare/Logtexte übersetzt.
# Experiment: ein Unix-Socket pro Anruf, mehrere TCP-Abnehmer.
# Bekannte Grenzen: blockierende Clients, Teil-Schreibvorgänge, SIGPIPE.
use strict; use warnings;
$| = 1;
use IO::Socket::UNIX;
use IO::Socket::INET;
use IO::Select;

my $TCP_PORT = 9002;
my $t0 = time();
sub note { printf "%d %s\n", time() - $t0, shift; }
my $server = IO::Socket::INET->new(
    LocalPort => $TCP_PORT, Listen => 5, ReuseAddr => 1, Proto => 'tcp')
    or die "TCP-Listen: $!";
my ($unix, $tunnel_path);
my %clients;
my ($rx, $last_ka) = (0, 0);
note("Relay bereit auf TCP $TCP_PORT");
while (1) {
    unless ($unix) {
        my $now = time();
        my @c = grep { $now - (stat($_))[9] < 300 } glob("/tmp/signal-call-*.sock");
        if (@c) {
            my ($p) = sort { (stat($b))[9] <=> (stat($a))[9] } @c;
            if (!defined $tunnel_path || $p ne $tunnel_path) {
                if (my $u = IO::Socket::UNIX->new(Peer => $p)) {
                    $unix = $u; $tunnel_path = $p; $rx = 0;
                    note("Verbunden: $p");
                } else {
                    undef $tunnel_path;
                }
            }
        }
    }
    my $sel = IO::Select->new();
    $sel->add($server);
    $sel->add($unix) if $unix;
    $sel->add($_) for values %clients;
    for my $fh ($sel->can_read(1)) {
        my $fn = fileno($fh);
        if ($fn == fileno($server)) {
            if (my $c = $fh->accept) {
                $c->autoflush(1);
                $clients{fileno($c)} = $c;
                note("Client verbunden (" . scalar(keys %clients) . ")");
            }
        } elsif ($unix && $fn == fileno($unix)) {
            my $d;
            my $n = sysread($unix, $d, 65536);
            if (!defined $n || $n == 0) {
                note("Tunnel geschlossen (rx=$rx)");
                $unix->close;
                undef $unix;
                undef $tunnel_path;
            } else {
                $rx += $n;
                syswrite($_, $d) for values %clients;
            }
        } else {
            my $d;
            my $n = sysread($fh, $d, 65536);
            if (!defined $n || $n == 0) {
                delete $clients{$fn};
                note("Client getrennt");
            } elsif ($unix) {
                syswrite($unix, $d);  # Recorder liefert hier Stille, kein Mikrofon.
            }
        }
    }
    if ($unix && time() - $last_ka >= 1) {
        syswrite($unix, "\x00" x 960);
        $last_ka = time();
    }
}
