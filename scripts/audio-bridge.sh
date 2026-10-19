#!/bin/sh
# Audio-Brücke: verbindet TCP-Port 9002 mit dem NEUESTEN Signal-Call-Socket.
# Wechselt automatisch, wenn ein neuer Anruf einen neuen Socket anlegt.
while true; do
    f=$(ls -t /tmp/signal-call-*.sock 2>/dev/null | head -n1)
    if [ -n "$f" ]; then
        cur=$(ps -o args= -C socat 2>/dev/null | grep -o 'UNIX-CONNECT:[^ ]*' | head -n1)
        if [ "$cur" != "UNIX-CONNECT:$f" ]; then
            pkill -f "TCP-LISTEN:9002" 2>/dev/null
        fi
        if ! ps -o args= -C socat 2>/dev/null | grep -q "TCP-LISTEN:9002"; then
            socat TCP-LISTEN:9002,bind=0.0.0.0,reuseaddr,fork "UNIX-CONNECT:$f" &
        fi
    fi
    sleep 1
done
