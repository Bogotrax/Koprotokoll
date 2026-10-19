"""Rein beobachtender Signal-Anruf-Test: nur zuschauen, nichts steuern.

Das Skript meldet sich per ``subscribeCallEvents`` beim signal-cli-Dienst an
und protokolliert, was ein verknüpftes Gerät sieht, während Menschen am Handy
telefonieren. Es sendet **niemals** ``acceptCall``, ``startCall`` oder
``hangupCall``, verbindet den Audio-Socket nicht und schreibt keine Audiodatei.

Alle Gesprächsteilnehmenden müssen informiert und einverstanden sein.
Benötigt die RPC-Brücke (Standard: 127.0.0.1:6003) zum signal-cli-Daemon.
"""

from __future__ import annotations

import argparse
import glob
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _describe(message: dict) -> str:
    """Kurzbeschreibung einer JSON-RPC-Zeile für die Konsole."""
    method = message.get("method")
    if method == "callEvent":
        event = message.get("params", {}).get("result", {})
        reason = f" reason={event.get('reason')}" if event.get("reason") else ""
        direction = "ausgehend" if event.get("isOutgoing") else "eingehend"
        return (f"callEvent: state={event.get('state')} "
                f"callId={event.get('callId')} ({direction}){reason}")
    if method == "receive":
        envelope = message.get("params", {}).get("envelope", {})
        name = envelope.get("sourceName") or envelope.get("sourceUuid") or "?"
        call_message = envelope.get("callMessage") or {}
        if not call_message:
            return f"receive: Nachricht von {name} (kein callMessage)"
        return f"receive: callMessage von {name}: {', '.join(sorted(call_message))}"
    if "error" in message:
        return f"RPC-Fehler: {message['error']}"
    result = json.dumps(message.get("result"), ensure_ascii=False)
    return f"RPC-Antwort: {result}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-port", type=int, default=6003,
                        help="TCP-Port der RPC-Brücke (Standard: 6003)")
    parser.add_argument("--seconds", type=int, default=180,
                        help="Beobachtungsdauer in Sekunden (Standard: 180)")
    parser.add_argument("--log", type=Path, default=None,
                        help="Protokolldatei (Standard: data/signal-observe-<zeit>.log)")
    args = parser.parse_args()

    log_path = args.log or (ROOT / "data" /
                            f"signal-observe-{time.strftime('%Y%m%d-%H%M%S')}.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    print("Rein passive Beobachtung: es wird NICHT abgenommen, NICHT angerufen")
    print("und NICHT aufgenommen. Ende: Strg+C oder Beobachtungsdauer abwarten.")
    print(f"Protokoll: {log_path}")

    states: list[str] = []
    devices: set[str] = set()
    lines_seen = 0
    buffer = b""
    try:
        with socket.create_connection(("127.0.0.1", args.rpc_port), timeout=5) as rpc, \
                log_path.open("w", encoding="utf-8") as log_file:
            rpc.settimeout(1)
            request = {"jsonrpc": "2.0", "id": 1,
                       "method": "subscribeCallEvents", "params": {}}
            rpc.sendall((json.dumps(request) + "\n").encode())
            log_file.write(json.dumps(request, ensure_ascii=False) + "\n")
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                try:
                    chunk = rpc.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    print("Verbindung zum Daemon geschlossen.")
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    lines_seen += 1
                    log_file.write(raw.decode("utf-8", "replace") + "\n")
                    log_file.flush()
                    try:
                        message = json.loads(raw or b"{}")
                    except json.JSONDecodeError:
                        continue
                    print(f"[{_ts()}] {_describe(message)}", flush=True)
                    if message.get("method") != "callEvent":
                        continue
                    event = message.get("params", {}).get("result", {})
                    state = event.get("state")
                    if state:
                        states.append(f"{event.get('callId')}:{state}")
                    for key in ("inputDeviceName", "outputDeviceName"):
                        if event.get(key):
                            devices.add(event[key])
    except KeyboardInterrupt:
        print("\nBeobachtung abgebrochen.")

    # Tunnel-Sockets entstehen im Container unter /tmp; am Windows-Host sind
    # sie nicht sichtbar. Deshalb nur eine informative Ausgabe, kein Kriterium.
    socks = sorted(glob.glob("/tmp/signal-call-*.sock"))
    print("---- Zusammenfassung ----")
    print(f"Protokollierte JSON-Zeilen: {lines_seen}")
    print(f"Zustandsfolgen: {'; '.join(states) or 'keine callEvent-Ereignisse'}")
    print(f"Audio-Gerätenamen: {', '.join(sorted(devices)) or 'keine'}")
    print(f"Tunnel-Sockets (/tmp/signal-call-*.sock): "
          f"{', '.join(socks) if socks else 'keine sichtbar (liegen im Container)'}")
    print("Audio wurde bewusst weder verbunden noch aufgezeichnet.")
    print(f"Vollständiges Protokoll: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())