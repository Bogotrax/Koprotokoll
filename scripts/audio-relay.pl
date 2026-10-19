#!/usr/bin/perl
# Dauerhaftes Audio-Relay für den Signal-Call-Tunnel.
#
# Problem: Der Tunnel akzeptiert pro Anruf nur EINEN Client am Unix-Socket.
# Jede Neuverbindung (z. B. socat fork) wird abgelehnt.
#
# Lösung: Dieses Relay verbindet sich EINMAL pro Anruf mit dem Tunnel-Socket
# und hält die Verbindung. Koprotokoll verbindet sich beliebig oft über TCP
# (Port 9002) und bekommt die Audiodaten weitergereicht.
use strict; use warnings;
$| = 1;  # Log sofort schreiben
use IO::Socket::UNIX;
use IO::Socket::INET;
use IO::Select;

my $TCP_PORT = 9002;
my $t0 = time();
sub note { printf "%d %s\n", time() - $t0, shift; }

# TCP-Server: immer da, beliebig viele Client-Neuverbindungen möglich.
my $server = IO::Socket::INET->new(
    LocalPort => $TCP_PORT, Listen => 5, ReuseAddr => 1, Proto => 'tcp')
    or die "TCP-Listen: $!";

my ($unix, $tunnel_path);
my %clients;    # fileno => Handle
my ($rx, $last_ka) = (0, 0);

note("relay ready on tcp $TCP_PORT");
while (1) {
    # Tunnel-Socket suchen und EINMALIG übernehmen – immer der NEUESTE Socket;
    # alte Sockets toter Anrufe werden ignoriert (sie sind älter als 5 Minuten).
    unless ($unix) {
        my $now = time();
        my @c = grep { $now - (stat($_))[9] < 300 } glob("/tmp/signal-call-*.sock");
        if (@c) {
            my ($p) = sort { (stat($b))[9] <=> (stat($a))[9] } @c;
            if (!defined $tunnel_path || $p ne $tunnel_path) {
                if (my $u = IO::Socket::UNIX->new(Peer => $p)) {
                    $unix = $u; $tunnel_path = $p; $rx = 0;
                    note("claimed $p");
                } else {
                    undef $tunnel_path;  # belegt -> später erneut versuchen
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
                note("client connected (" . scalar(keys %clients) . ")");
            }
        } elsif ($unix && $fn == fileno($unix)) {
            my $d;
            my $n = sysread($unix, $d, 65536);
            if (!defined $n || $n == 0) {
                note("tunnel closed (rx=$rx)");
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
                note("client gone");
            } elsif ($unix) {
                syswrite($unix, $d);  # Rückkanal-Stille an den Tunnel
            }
        }
    }

    # Keepalive: alle Sekunde Stille an den Tunnel, auch ohne Empfang.
    if ($unix && time() - $last_ka >= 1) {
        syswrite($unix, "\x00" x 960);
        $last_ka = time();
    }
}
