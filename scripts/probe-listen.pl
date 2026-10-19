#!/usr/bin/perl
# Mithorer am Signal-Call-Tunnel-Socket: zaehlt empfangene Bytes, schreibt
# sie nach /tmp/probe.raw und pumpt Stille zurueck (Keepalive).
use strict; use warnings;
use IO::Socket::UNIX;
my $t0 = time();
sub note { printf "%d %s\n", time()-$t0, shift; }
my $sock_path;
for (1..180) {
    my @c = glob("/tmp/signal-call-*.sock");
    if (@c) { @c = sort { (stat($a))[9] <=> (stat($b))[9] } @c; $sock_path = $c[-1]; last; }
    sleep 1;
}
die "kein socket\n" unless $sock_path;
print "socket=$sock_path\n"; note("waiting");
my $s = IO::Socket::UNIX->new(Peer => $sock_path) or die "connect: $!";
note("connected");
open(my $out, ">", "/tmp/probe.raw") or die $!;
binmode $out;
my $rx = 0; my $last = 0;
$| = 1;
while (1) {
    my $data;
    eval {
        local $SIG{ALRM} = sub { die "timeout\n"; };
        alarm 1;
        my $peer = recv($s, $data, 65536, 0);
        alarm 0;
        if (defined $peer) {
            if (!length $data) { note("eof"); last; }
            $rx += length $data;
            print $out $data;
        }
        1;
    } or do { };  # Timeout -> weiter, Stille senden
    eval { send($s, "\x00" x 960, 0) or die "send: $!"; 1 } or do { note("senderr $@"); last; };
    if (time() - $last > 5) { note("rx=$rx"); $last = time(); }
}
close $out;
note("done rx=$rx");
