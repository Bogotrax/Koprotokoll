"""Signal-Anrufe aufnehmen: Ausgehender Anruf über signal-cli (JSON-RPC) mit dem
Signal-Call-Tunnel im Pipe-Modus, Ton-Mitschnitt vom Unix-Socket des Tunnels.

Der Ablauf entspricht dem erfolgreichen End-to-End-Test vom 18.10.2026:

1. ``startCall`` über den rohen JSON-RPC von signal-cli (Port 6001).
2. Der signal-call-tunnel (Wrapper mit ``SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe``)
   schreibt das Anruf-Audio als rohes PCM 16 Bit/Mono in einen Unix-Socket.
3. Ein Hintergrund-Thread liest den Socket und schreibt ein WAV in das
   Medienverzeichnis (Aufnahmerate aus den Settings, z. B. 48 kHz).
4. Beim Auflegen wird das WAV als Meeting in die Pipeline gegeben
   (Konvertieren → Transkribieren → Sprecher → Zusammenfassen).

Nur für Linux/macOS lauffähig (Unix-Sockets); die Tests faken JSON-RPC und Socket.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import Settings
from .db import Database
from .pipeline import Pipeline
from .signal import SignalClient, SignalError, normalize_number

log = logging.getLogger(__name__)

# Wartezeit (Sekunden), bis der Tunnel-Socket beim Anrufbeginn bereit ist.
SOCKET_TIMEOUT = 20.0


class SignalCallError(RuntimeError):
    pass


# Call-ID des letzten Anrufs (aus signal-cli-Nachrichten mitgelesen).
_last_call_id: int | None = None


def _jsonrpc(url: str, method: str, params: dict) -> dict | list:
    global _last_call_id
    """Schickt einen JSON-RPC-2.0-Aufruf an den rohen signal-cli-Dienst.

    Der JSON-RPC-Socket von signal-cli (Standard: 127.0.0.1:6001) ist kein
    HTTP-Server, sondern nimmt **ein JSON-Objekt pro Zeile** über rohes TCP
    entgegen und antwortet ebenfalls zeilenweise. Wird der Dienst in Docker
    betrieben, lauscht er nur im Container-Loopback – dann zeigt ``url``
    auf eine socat-Brücke (z. B. ``tcp://127.0.0.1:6003``).
    Alternativ wird noch ein HTTP-POST-Endpunkt unterstützt (``http://…``).
    """
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params}).encode()
    try:
        if url.startswith("tcp://"):
            host, _, port = url[6:].rpartition(":")
            if not host or not port.isdigit():
                raise ValueError(f"Ungültige JSON-RPC-Adresse: {url!r} (erwartet tcp://host:port)")
            with socket.create_connection((host, int(port)), timeout=30) as s:
                s.settimeout(30)
                s.sendall(payload + b"\n")
                buf = b""
                lines: list[dict] = []
                while True:
                    if b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            msg = json.loads(line or b"{}")
                        except json.JSONDecodeError:
                            continue
                        lines.append(msg)
                        # Call-ID des laufenden Anrufs mitlesen (kommt in
                        # Benachrichtigungen und Antworten von signal-cli).
                        for key in ("callId", "call_id"):
                            if isinstance(msg.get(key), int):
                                _last_call_id = msg[key]
                        if msg.get("id") == 1 or "error" in msg:
                            data = msg
                            break
                    chunk = s.recv(65536)
                    if not chunk:
                        data = json.loads(buf.split(b"\n", 1)[0] or b"{}") if buf else {}
                        break
                    buf += chunk
                line = buf.split(b"\n", 1)[0]
                if not lines:
                    data = json.loads(line or b"{}")
        else:
            req = urllib.request.Request(f"{url}/", data=payload, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise SignalCallError(f"Signal-JSON-RPC {e.code}: {detail}") from e
    except OSError as e:
        raise SignalCallError(
            "Signal-JSON-RPC nicht erreichbar – läuft der Signal-Dienst? "
            "(docker compose -f docker-compose.signal.yml up -d)") from e
    if "error" in data:
        raise SignalCallError(f"Signal-JSON-RPC: {data['error'].get('message', data['error'])}")
    return data.get("result", data)


def _open_audio_source(socket_pattern: str):
    """Öffnet die Audio-Quelle des Signal-Call-Tunnels.

    Zwei Varianten:
    - ``tcp://host:port``: verbindet sich direkt über TCP (praktisch, wenn
      Koprotokoll auf einem anderen Rechner läuft als der Signal-Dienst,
      z. B. Windows-Host → Docker-Container; die Brücke stellt ``socat``).
    - sonst: Unix-Socket-Glob-Muster (z. B. ``/tmp/signal-call-*.sock``),
      für den Fall, dass Koprotokoll dort läuft, wo der Tunnel schreibt.

    In beiden Fällen wird bidirektional gelesen und geschrieben: Der Tunnel
    erwartet regelmäßig zurückgeschickte Stille, sonst stockt der Stream.
    """

    class SocketStream:
        """Stream-ähnlicher Wrapper: recv zum Lesen, sendall als Rückkanal."""

        def __init__(self, sock):
            self._sock = sock

        def read(self, n: int) -> bytes:
            # Wichtig: Auch ohne empfangene Daten sofort Stille zurückschicken –
            # der Tunnel sendet erst, wenn der Empfänger selbst Audio liefert
            # (Herzfrequenz des Datenstroms, siehe pipe_client.py).
            try:
                data = self._sock.recv(n)
            except socket.timeout:
                data = b""  # noch nichts da – trotzdem weiterpumpen
            self._sock.sendall(b"\x00" * 960)
            return data

        def close(self) -> None:
            try:
                self._sock.close()
            except OSError:
                pass

    sock = None
    if socket_pattern.startswith("tcp://"):
        host, _, port = socket_pattern[6:].rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"Ungültige TCP-Adresse für Signal-Audio: {socket_pattern!r} "
                             "(erwartet tcp://host:port)")
        sock = socket.create_connection((host, int(port)), timeout=SOCKET_TIMEOUT)
        sock.settimeout(1.0)
        return SocketStream(sock)

    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        raise OSError("Unix-Sockets werden auf diesem System nicht unterstützt. "
                      "Setze KOPROTOKOLL_SIGNAL_AUDIO_SOCKET auf tcp://host:port, "
                      "wenn der Signal-Dienst in Docker läuft.")
    deadline = time.time() + SOCKET_TIMEOUT
    while time.time() < deadline:
        candidates = glob.glob(socket_pattern)
        if candidates:
            path = max(candidates, key=os.path.getmtime)  # neuester Socket = laufender Anruf
            sock = socket.socket(af_unix, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(path)
            return SocketStream(sock)
        time.sleep(0.2)
    raise OSError(f"Kein Tunnel-Socket gefunden (Muster {socket_pattern}) – läuft der Anruf?")



class _Recorder:
    """Liest rohes PCM vom Tunnel-Socket und schreibt es als WAV-Datei."""

    def __init__(self, socket_path: str, wav_path: Path, rate: int):
        self.socket_path, self.wav_path, self.rate = socket_path, wav_path, rate
        self.stop_event = threading.Event()
        self.bytes_written = 0
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, name="signal-call-recorder", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        deadline = time.time() + SOCKET_TIMEOUT
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                src = _open_audio_source(self.socket_path)
            except OSError:
                time.sleep(0.2)  # Tunnel-Socket ist evtl. noch nicht da – erneut versuchen
                continue
            try:
                with sf.SoundFile(str(self.wav_path), "w", samplerate=self.rate,
                                  channels=1, subtype="PCM_16") as wav:
                    while not self.stop_event.is_set():
                        try:
                            chunk = src.read(4096)
                        except socket.timeout:
                            continue  # kurze Sendepause – weiterlesen, Stop bleibt möglich
                        except OSError:
                            if self.bytes_written == 0:
                                raise  # Verbindung weg, bevor Ton kam -> neu verbinden
                            log.warning("Signal-Anruf-Aufnahme unterbrochen")
                            break  # wir hatten schon Ton -> Auflegen, fertig
                        if not chunk:
                            time.sleep(0.05)  # noch kein Audio – Stop-Event prüfen und weiter
                            continue
                        self.bytes_written += len(chunk)
                        wav.write(np.frombuffer(chunk, dtype=np.int16))
            except OSError as e:
                log.warning("Signal-Anruf: Audio-Verbindung fehlgeschlagen: %s", e)
            finally:
                try:
                    src.close()
                except OSError:
                    pass
            if self.bytes_written:
                return  # Ton war da, Verbindung ist zu (Auflegen) – fertig
            time.sleep(0.2)  # kein Ton -> Verbindung erneut aufbauen (Socket kommt evtl. erst jetzt)
        if not self.bytes_written:
            self.error = "Kein Audio vom Tunnel-Socket – lief der Anruf über den Tunnel?"

    def stop(self) -> None:
        self.stop_event.set()
        self._thread.join(timeout=10)


class SignalCallManager:
    """Startet, überwacht und beendet die Aufnahme von Signal-Anrufen."""

    def __init__(self, settings: Settings, db: Database, pipeline: Pipeline, signal: SignalClient):
        self.s, self.db, self.pipeline, self.signal = settings, db, pipeline, signal
        self._lock = threading.Lock()
        self._recorder: _Recorder | None = None
        self.meeting_id: int | None = None
        self.number: str | None = None
        self.started_at: float | None = None
        self._call_id: int | None = None

    def status(self) -> dict:
        return {"recording": self._recorder is not None, "number": self.number,
                "meeting_id": self.meeting_id, "started_at": self.started_at,
                "seconds": round(time.time() - self.started_at, 1) if self.started_at else None}

    def start(self, number: str, title: str | None = None) -> dict:
        with self._lock:
            if self._recorder is not None:
                raise SignalCallError("Es läuft bereits eine Signal-Anruf-Aufnahme")
            number = normalize_number(number)
            accounts = self.signal.accounts()
            if not accounts:
                raise SignalError("Signal ist nicht verbunden – bitte das Signal-Konto verknüpfen")
            _jsonrpc(self.s.signal_jsonrpc_url, "startCall",
                     {"account": accounts[0], "recipient": number})
            self._call_id = _last_call_id

            meeting_id = self.db.create_meeting(
                title or f"Signal-Anruf {number}", "Signal-Anruf",
                meta={"signal_call": number, "audio_rate": self.s.signal_audio_rate})
            wav = self.s.media_dir / f"{meeting_id}-signal.wav"
            self.db.update_meeting(meeting_id, audio_path=str(wav))
            self._recorder = _Recorder(self.s.signal_audio_socket, wav, self.s.signal_audio_rate)
            self.meeting_id, self.number, self.started_at = meeting_id, number, time.time()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self._recorder is None:
                raise SignalCallError("Es läuft gerade keine Signal-Anruf-Aufnahme")
            meeting_id, recorder = self.meeting_id, self._recorder
            recorder.stop()
            self._recorder = None
            self.started_at = None

            accounts: list[str] = []
            try:
                accounts = self.signal.accounts()
            except (SignalError, OSError):
                pass
            if accounts and self.number:
                try:
                    params = {"account": accounts[0]}
                    if self._call_id is not None:
                        params["callId"] = self._call_id
                    else:
                        params["recipient"] = self.number
                    _jsonrpc(self.s.signal_jsonrpc_url, "hangupCall", params)
                except SignalCallError as e:
                    log.warning("Anruf konnte nicht aufgelegt werden: %s", e)

            number, self.number = self.number, None
            self.meeting_id = None
            if recorder.bytes_written == 0:
                err = recorder.error or "Kein Audio aufgezeichnet – lief der Anruf über den Tunnel?"
                self.db.update_meeting(meeting_id, status="error", error=err)
                raise SignalCallError(err)
            self.pipeline.submit(meeting_id)
            return {"ok": True, "meeting_id": meeting_id, "number": number}
