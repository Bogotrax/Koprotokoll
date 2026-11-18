"""Lokale Hardwareaufnahme über ffmpeg, unabhängig von Meeting-Anwendungen.

Das Modul konfiguriert weder das Betriebssystem noch einen Audio-Treiber. Es
zeichnet genau den vom Betriebssystem bereitgestellten Eingangs- oder
Loopback-Kanal in eine WAV-Datei auf. Die Datei kann anschließend wie jeder
andere Upload in Koprotokoll verarbeitet werden.
"""

from __future__ import annotations

import sys
import signal
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from array import array
import math
import re
import json
import platform
import shutil


def backend(platform: str | None = None) -> str:
    """ffmpeg-Eingabemodul für die drei unterstützten Desktop-Plattformen."""
    platform = platform or sys.platform
    if platform == "darwin":
        return "avfoundation"
    if platform.startswith("linux"):
        return "pulse"
    if platform.startswith("win"):
        return "dshow"
    raise ValueError(f"Nicht unterstütztes Betriebssystem: {platform}")


SYSTEM_AUDIO_DEVICE = "__koprotokoll_system_audio__"
MACOS_CAPTURE_HELPER = Path(__file__).with_name("capture_macos.swift")
WINDOWS_CAPTURE_HELPER = Path(__file__).with_name("capture_windows.py")


def list_devices_command(platform: str | None = None) -> list[str]:
    """Befehl, der die vom Betriebssystem sichtbaren Audioquellen ausgibt."""
    source = backend(platform)
    if source == "avfoundation":
        return ["ffmpeg", "-hide_banner", "-f", source, "-list_devices", "true", "-i", ""]
    if source == "pulse":
        return ["ffmpeg", "-hide_banner", "-sources", source, "-i", "dummy"]
    return ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", source, "-i", "dummy"]


def record_command(device: str, output: Path, seconds: float | None = None, channels: int = 2,
                   sample_rate: int = 48_000, platform: str | None = None) -> list[str]:
    """Baut einen sicheren ffmpeg-Aufruf für eine verlustfreie Originalaufnahme."""
    if not device.strip():
        raise ValueError("Bitte eine Audioquelle mit --device angeben.")
    if seconds is not None and seconds <= 0:
        raise ValueError("--seconds muss größer als 0 sein.")
    if channels not in (1, 2):
        raise ValueError("--channels muss 1 oder 2 sein.")
    if sample_rate not in (16_000, 24_000, 44_100, 48_000):
        raise ValueError("--sample-rate muss 16000, 24000, 44100 oder 48000 sein.")
    if output != Path("-") and output.suffix.lower() != ".wav":
        raise ValueError("Die Originalaufnahme muss die Endung .wav haben.")
    if not output.parent.exists():
        raise ValueError(f"Der Zielordner existiert nicht: {output.parent}")

    source = backend(platform)
    if source == "avfoundation":
        input_arg = f":{device}"
    elif source == "dshow":
        input_arg = f"audio={device}"
    else:
        input_arg = device
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-n",
        "-f", source, "-i", input_arg,
    ]
    if seconds is not None:
        command += ["-t", str(seconds)]
    command += ["-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le"]
    if output == Path("-"):
        return command + ["-f", "s16le", "pipe:1"]
    return command + [str(output)]


def _audio_duration(path: Path) -> float:
    """Liest die Containerdauer einer Spur einschließlich Float-PCM-WAV."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe wurde nicht gefunden; die Audiowege lassen sich nicht vergleichen.")
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Audiospur ließ sich nicht prüfen: {path.name}") from exc
    try:
        duration = float(proc.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Dauer der Audiospur ließ sich nicht lesen: {path.name}") from exc
    if proc.returncode != 0 or not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Ungültige Audiospur: {path.name}")
    return duration


def _validate_stem_durations(system_path: Path, mic_path: Path, tolerance: float = 0.5) -> tuple[float, float]:
    system_duration = _audio_duration(system_path)
    mic_duration = _audio_duration(mic_path)
    if abs(system_duration - mic_duration) > tolerance:
        raise RuntimeError(
            "Systemton- und Mikrofonspur sind unterschiedlich lang "
            f"({system_duration:.2f} s und {mic_duration:.2f} s); Aufnahme bleibt lokal und wird nicht ausgewertet."
        )
    return system_duration, mic_duration


def parse_devices(details: str, platform: str | None = None) -> list[dict[str, str]]:
    """Extrahiert Audioquellen aus der ffmpeg-Ausgabe, soweit das Modul sie benennt."""
    source = backend(platform)
    lines = details.splitlines()
    if source == "avfoundation":
        out = []
        in_audio_section = False
        for line in lines:
            if "audio devices:" in line:
                in_audio_section = True
                continue
            if in_audio_section and "video devices:" in line:
                break
            if not in_audio_section:
                continue
            match = re.search(r"\[(\d+)\]\s+(.+)$", line)
            if match:
                out.append({"id": match.group(1), "name": match.group(2).strip()})
        return out
    if source == "dshow":
        return [
            {"id": line.split('"')[1], "name": line.split('"')[1]}
            for line in lines if '"' in line and "(audio)" in line
        ]
    return [
        {"id": line.strip().lstrip("* "), "name": line.strip().lstrip("* ")}
        for line in lines if line.strip().startswith("*")
    ]


@dataclass
class ActiveCapture:
    device: str
    path: Path
    process: subprocess.Popen
    channels: int
    sample_rate: int
    started_at: float
    level_db: float = -120.0
    reader: threading.Thread | None = None
    ready: threading.Event | None = None
    system_path: Path | None = None
    mic_path: Path | None = None
    error: str | None = None


class HardwareRecorder:
    """Eine exklusive, lokale Hardwareaufnahme pro Koprotokoll-Instanz."""

    def __init__(self, capture_dir: Path):
        self.capture_dir = capture_dir
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._active: ActiveCapture | None = None

    def devices(self) -> dict:
        try:
            proc = subprocess.run(list_devices_command(), capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg wurde nicht gefunden") from exc
        details = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        devices = parse_devices(details)
        if sys.platform == "darwin":
            major = int(platform.mac_ver()[0].split(".", 1)[0] or 0)
            if major >= 15 and MACOS_CAPTURE_HELPER.exists() and shutil.which("swiftc"):
                devices.insert(0, {"id": SYSTEM_AUDIO_DEVICE,
                                   "name": "Systemton + Standardmikrofon (macOS)"})
        if sys.platform == "win32" and WINDOWS_CAPTURE_HELPER.exists():
            devices.insert(0, {"id": SYSTEM_AUDIO_DEVICE,
                               "name": "Systemton + Standardmikrofon (Windows)"})
        return {"backend": backend(), "devices": devices, "details": details}

    def status(self) -> dict:
        with self._lock:
            active = self._active
            if active is None:
                return {"active": False}
            return {"active": active.process.poll() is None, "device": active.device,
                    "started_at": active.started_at, "level_db": active.level_db, "error": active.error}

    def start(self, device: str, channels: int = 2, sample_rate: int = 48_000) -> dict:
        with self._lock:
            if self._active is not None and self._active.process.poll() is None:
                raise RuntimeError("Es läuft bereits eine Hardwareaufnahme.")
            path = self.capture_dir / f"aufnahme-{int(time.time() * 1000)}.wav"
            is_system_mix = device == SYSTEM_AUDIO_DEVICE
            channels, sample_rate = (2, 48_000) if is_system_mix else (channels, sample_rate)
            system_path = self.capture_dir / f"{path.stem}.system.wav"
            mic_path = self.capture_dir / f"{path.stem}.microphone.wav"
            if is_system_mix:
                if sys.platform == "darwin":
                    command = [self._system_helper_executable(), "--capture-audio",
                               str(system_path), str(mic_path)]
                elif sys.platform == "win32":
                    command = [sys.executable, str(WINDOWS_CAPTURE_HELPER), "--capture-audio",
                               str(system_path), str(mic_path)]
                else:
                    raise ValueError(
                        "Systemtonaufnahme ist in dieser Version nur für macOS und Windows verfügbar.")
            else:
                command = record_command(device, Path("-"), channels=channels, sample_rate=sample_rate)
            stdin = subprocess.PIPE if is_system_mix and sys.platform == "win32" else subprocess.DEVNULL
            try:
                process = subprocess.Popen(command, stdin=stdin, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL)
            except FileNotFoundError as exc:
                raise RuntimeError("ffmpeg wurde nicht gefunden") from exc
            active = ActiveCapture(device=device, path=path, process=process, channels=channels,
                                   sample_rate=sample_rate, started_at=time.time(), ready=threading.Event(),
                                   system_path=system_path if is_system_mix else None,
                                   mic_path=mic_path if is_system_mix else None)
            self._active = active
            target = self._read_system_status if is_system_mix else self._write_pcm
            active.reader = threading.Thread(target=target, args=(active,), daemon=True)
            active.reader.start()
            if not active.ready.wait(timeout=60 if is_system_mix else 8) or active.error:
                self._active = None
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                active.reader.join(timeout=5)
                raise RuntimeError(active.error or "Der Audioeingang lieferte beim Start keine Samples. Audio-Freigaben und Quelle prüfen.")
            active.started_at = time.time()
            return self.status()

    def _system_helper_executable(self) -> str:
        from .config import Settings
        from .desktop import build_app

        # Derselbe App-Bundle trägt die Audio-Zwecktexte und die macOS-Identität.
        app = build_app(Settings(data_dir=self.capture_dir.parent), configure_launcher=False)
        return str(app / "Contents" / "MacOS" / "Koprotokoll")

    @staticmethod
    def _write_pcm(active: ActiveCapture) -> None:
        """Schreibt die WAV lokal und misst den Spitzenpegel laufend aus PCM-Samples."""
        try:
            with active.path.open("xb") as raw, wave.open(raw, "wb") as output:
                output.setnchannels(active.channels)
                output.setsampwidth(2)
                output.setframerate(active.sample_rate)
                assert active.process.stdout is not None
                while chunk := active.process.stdout.read(16_384):
                    usable = len(chunk) - (len(chunk) % (2 * active.channels))
                    if not usable:
                        continue
                    chunk = chunk[:usable]
                    output.writeframesraw(chunk)
                    if active.ready is not None:
                        active.ready.set()
                    samples = array("h")
                    samples.frombytes(chunk)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    peak = max((abs(sample) for sample in samples), default=0) / 32768
                    active.level_db = max(-120.0, 20 * math.log10(peak)) if peak else -120.0
        except Exception:
            # Fehlende/kaputte WAV wird nach dem Stopp an die Oberfläche gemeldet.
            return

    @staticmethod
    def _read_system_status(active: ActiveCapture) -> None:
        assert active.process.stdout is not None
        for line in active.process.stdout:
            try:
                status = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            active.level_db = float(status.get("level_db", -120.0))
            if status.get("error"):
                active.error = str(status["error"])
                if active.ready is not None:
                    active.ready.set()
                continue
            if status.get("ready") and active.ready is not None:
                active.ready.set()
        if active.ready is not None and not active.ready.is_set():
            active.error = active.error or "Systemaudio-Aufnahme wurde vor dem ersten Sample beendet. Audio-Freigaben prüfen."
            active.ready.set()

    def stop(self) -> tuple[Path, float, str]:
        with self._lock:
            active = self._active
            if active is None:
                raise RuntimeError("Es läuft keine Hardwareaufnahme.")
            self._active = None
        if active.process.poll() is None:
            if active.system_path is not None and sys.platform == "win32":
                # Der Windows-Helfer wird über eine Stoppzeile beendet; Signale erreichen ihn nicht.
                try:
                    if active.process.stdin is not None:
                        active.process.stdin.write(b"stop\n")
                        active.process.stdin.flush()
                        active.process.stdin.close()
                except (OSError, ValueError):
                    pass
            else:
                active.process.send_signal(signal.SIGINT)
        try:
            active.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            active.process.kill()
            active.process.wait()
        if active.reader is not None:
            active.reader.join(timeout=5)
            if active.reader.is_alive():
                raise RuntimeError("Die Audioaufnahme konnte nicht sauber abgeschlossen werden.")
        if active.error:
            raise RuntimeError(active.error)
        if active.system_path is not None and active.process.returncode:
            raise RuntimeError("Die Systemaudio-Aufnahme wurde mit einem Fehler beendet; die Spuren bleiben lokal.")
        if active.system_path is not None and active.mic_path is not None:
            if not active.system_path.is_file() or not active.mic_path.is_file():
                raise RuntimeError("Systemton oder Standardmikrofon lieferte keine Audiodatei.")
            _validate_stem_durations(active.system_path, active.mic_path)
            mixed = subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                "-i", str(active.system_path), "-i", str(active.mic_path),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]",
                "-map", "[a]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(active.path),
            ], capture_output=True, text=True, check=False)
            if mixed.returncode != 0:
                raise RuntimeError(f"Systemton und Mikrofon ließen sich nicht mischen: {mixed.stderr.strip()[-500:]}")
        if not active.path.exists() or active.path.stat().st_size <= 44:
            raise RuntimeError("Die Audioquelle lieferte keine aufnehmbare Datei.")
        try:
            with wave.open(str(active.path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
        except (wave.Error, ZeroDivisionError) as exc:
            raise RuntimeError("Die Aufnahme ist keine lesbare PCM-WAV-Datei.") from exc
        if duration <= 0:
            raise RuntimeError("Die Audioquelle lieferte keine hörbare Aufnahme.")
        return active.path, duration, active.device
