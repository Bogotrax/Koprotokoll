"""Befristeter Endpunkt-Nachweis, ausdrücklich KEIN passiver Gesprächsbegleiter.

Nur mit --accept-call wird ein eingehender Anruf angenommen und aufgenommen.
Alle Beteiligten müssen vor dem Test über die Aufnahme informiert sein.
Benötigt die manuelle TCP-RPC-Brücke und das Audio-Relay (siehe Zwischenstand).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from koprotokoll.signal_calls import _Recorder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-call", action="store_true",
                        help="Testannahme und Aufnahme ausdrücklich aktivieren")
    parser.add_argument("--rpc-port", type=int, default=6003)
    parser.add_argument("--audio-port", type=int, default=9002)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    if not args.accept_call:
        parser.error("Dieser Endpunkt-Test erfordert ausdrücklich --accept-call")

    output = ROOT / "data" / "media"
    output.mkdir(parents=True, exist_ok=True)
    recorder = None
    call_id = None
    ended = False
    accepted = False
    buffer = b""
    with socket.create_connection(("127.0.0.1", args.rpc_port), timeout=5) as rpc:
        rpc.settimeout(1)

        def send(method: str, params: dict, request_id: int) -> None:
            rpc.sendall((json.dumps({"jsonrpc": "2.0", "id": request_id,
                                    "method": method, "params": params}) + "\n").encode())

        try:
            send("subscribeCallEvents", {}, 1)
            deadline = time.monotonic() + args.seconds
            print("Befristete Testannahme aktiv; warte auf Anruf.", flush=True)
            while time.monotonic() < deadline and not ended:
                try:
                    chunk = rpc.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    message = json.loads(line)
                    if "error" in message:
                        raise RuntimeError(f"RPC-Fehler: {message['error']}")
                    if message.get("method") != "callEvent":
                        continue
                    event = message["params"]["result"]
                    if call_id is not None and event.get("callId") != call_id:
                        continue
                    state = event.get("state")
                    print(f"Zustand: {state}; Grund: {event.get('reason')}", flush=True)
                    if state == "RINGING_INCOMING" and not accepted:
                        call_id = event["callId"]
                        accepted = True
                        send("acceptCall", {"callId": call_id}, 3)
                    if state == "CONNECTED" and accepted and recorder is None:
                        path = output / f"signal-incoming-{call_id}.wav"
                        recorder = _Recorder(f"tcp://127.0.0.1:{args.audio_port}", path, 48000)
                        print(f"Aufnahme: {path}", flush=True)
                    if state == "ENDED" and accepted:
                        ended = True
        finally:
            if accepted and not ended:
                try:
                    send("hangupCall", {"callId": call_id}, 9)
                except OSError:
                    print("Testanruf bitte am Telefon beenden.", flush=True)
            if recorder is not None:
                recorder.stop()
                print(f"PCM-Bytes: {recorder.bytes_written}; Fehler: {recorder.error}", flush=True)
    return 0 if recorder is not None and recorder.bytes_written and not recorder.error else 1


if __name__ == "__main__":
    sys.exit(main())