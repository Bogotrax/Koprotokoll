from pathlib import Path
import json
import shutil
import subprocess
import sys
import wave
from array import array
from types import SimpleNamespace

import pytest

from koprotokoll.capture import backend, list_devices_command, parse_devices, record_command


@pytest.mark.parametrize(("platform", "expected"), [
    ("darwin", "avfoundation"), ("linux", "pulse"), ("win32", "dshow"),
])
def test_backend_for_supported_desktop_systems(platform, expected):
    assert backend(platform) == expected


def test_unknown_platform_is_rejected():
    with pytest.raises(ValueError, match="Nicht unterstütztes Betriebssystem"):
        backend("freebsd")


def test_list_devices_uses_os_backend():
    assert list_devices_command("darwin")[-3:] == ["true", "-i", ""]
    assert list_devices_command("linux")[-3:] == ["pulse", "-i", "dummy"]
    assert list_devices_command("win32")[-3:] == ["dshow", "-i", "dummy"]


def test_record_command_is_lossless_and_keeps_device_as_one_argument():
    command = record_command("USB Mikrofon; keine Shell", Path("original.wav"), 120, platform="darwin")
    assert command[command.index("-i") + 1] == ":USB Mikrofon; keine Shell"
    assert command[-4:] == ["-ar", "48000", "-c:a", "pcm_s16le", "original.wav"][-4:]
    assert "-n" in command


def test_record_command_without_duration_runs_until_explicit_stop():
    command = record_command("Mikrofon", Path("original.wav"), platform="darwin")
    assert "-t" not in command
    assert command[-1] == "original.wav"


def test_parse_macos_audio_devices_without_video_devices():
    details = """[AVFoundation] video devices:
[AVFoundation] [0] Bildschirm
[AVFoundation] audio devices:
[AVFoundation] [0] USB-Mikrofon
[AVFoundation] [1] Loopback
"""
    assert parse_devices(details, "darwin") == [
        {"id": "0", "name": "USB-Mikrofon"}, {"id": "1", "name": "Loopback"}
    ]


@pytest.mark.parametrize("kwargs", [
    {"device": "", "output": Path("a.wav"), "seconds": 1},
    {"device": "1", "output": Path("a.mp3"), "seconds": 1},
    {"device": "1", "output": Path("a.wav"), "seconds": 0},
    {"device": "1", "output": Path("kein-ordner/a.wav"), "seconds": 1},
])
def test_record_command_rejects_ambiguous_or_lossy_input(kwargs):
    with pytest.raises(ValueError):
        record_command(platform="darwin", **kwargs)


def test_validate_stem_durations_rejects_incomplete_microphone_track(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/ffprobe")
    durations = iter(("1.14\n", "0.10\n"))
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=next(durations), returncode=0,
    ))

    with pytest.raises(RuntimeError, match="unterschiedlich lang"):
        capture._validate_stem_durations(tmp_path / "system.wav", tmp_path / "mikrofon.wav")


def test_validate_stem_durations_accepts_aligned_tracks(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.shutil, "which", lambda _: "/usr/bin/ffprobe")
    durations = iter(("30.00\n", "29.92\n"))
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout=next(durations), returncode=0,
    ))

    assert capture._validate_stem_durations(tmp_path / "system.wav", tmp_path / "mikrofon.wav") == (30.0, 29.92)


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="Native Audio-Prüfung benötigt macOS und Swift.")
def test_native_audio_writer_aligns_system_and_resampled_microphone(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "audio-writer-test"
    subprocess.run(["swiftc", str(root / "koprotokoll/capture_macos.swift"),
                    str(root / "tests/fixtures/audio_writer_macos_test.swift"), "-o", str(binary)], check=True)
    subprocess.run([str(binary), str(tmp_path)], check=True, capture_output=True)
    for name in ("system.wav", "microphone.wav"):
        with wave.open(str(tmp_path / name), "rb") as audio:
            assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (48_000, 2, 2)
            assert abs(audio.getnframes() - 14_400) <= 1
            samples = array("h", audio.readframes(audio.getnframes()))
        assert max(abs(value) for value in samples) > 5_000
        if name == "microphone.wav":
            assert not any(samples[:4_798])
    linked = subprocess.check_output(["otool", "-L", str(binary)], text=True)
    assert "ScreenCaptureKit" not in linked
    for name in ("first-system.wav", "first-microphone.wav"):
        with wave.open(str(tmp_path / name), "rb") as audio:
            assert abs(audio.getnframes() - 9_600) <= 1


def test_system_helper_uses_app_identity_without_changing_launcher(tmp_path, monkeypatch):
    from koprotokoll.capture import HardwareRecorder
    import koprotokoll.desktop as desktop

    app = tmp_path / "desktop/Koprotokoll.app"
    calls = []

    def build(settings, **kwargs):
        calls.append((settings.data_dir, kwargs))
        return app

    monkeypatch.setattr(desktop, "build_app", build)
    recorder = HardwareRecorder(tmp_path / "captures")
    assert recorder._system_helper_executable() == str(app / "Contents/MacOS/Koprotokoll")
    assert calls == [(tmp_path, {"configure_launcher": False})]


@pytest.mark.parametrize("lines", [[], [json.dumps({"error": "Mikrofonfreigabe fehlt"}).encode()]])
def test_system_helper_errors_end_startup_without_waiting_a_minute(tmp_path, monkeypatch, lines):
    import koprotokoll.capture as capture

    commands = []

    def process(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=lines, poll=lambda: 1, wait=lambda **_: 1)

    monkeypatch.setattr(capture.sys, "platform", "darwin")
    monkeypatch.setattr(capture.subprocess, "Popen", process)
    recorder = capture.HardwareRecorder(tmp_path)
    monkeypatch.setattr(recorder, "_system_helper_executable", lambda: "/test/Koprotokoll")
    with pytest.raises(RuntimeError, match="Freigaben|freigabe"):
        recorder.start(capture.SYSTEM_AUDIO_DEVICE)
    assert commands[0][:2] == ["/test/Koprotokoll", "--capture-audio"]
    assert recorder.status() == {"active": False}


def test_windows_geraete_zeigen_systemton_eintrag(tmp_path, monkeypatch):
    import koprotokoll.capture as capture

    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout='"Mikrofon (audio)"', stderr="", returncode=0))
    geraete = capture.HardwareRecorder(tmp_path).devices()
    assert geraete["devices"][0] == {"id": capture.SYSTEM_AUDIO_DEVICE,
                                     "name": "Systemton + Standardmikrofon (Windows)"}


def test_windows_start_ruft_python_helfer_mit_stoppzeilen_stdin(tmp_path, monkeypatch):
    import koprotokoll.capture as capture

    commands = []

    def process(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(stdout=[], poll=lambda: 1, wait=lambda **_: 1,
                                stdin=None, returncode=0)

    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture.subprocess, "Popen", process)
    recorder = capture.HardwareRecorder(tmp_path)
    with pytest.raises(RuntimeError, match="vor dem ersten Sample"):
        recorder.start(capture.SYSTEM_AUDIO_DEVICE)
    command, kwargs = commands[0]
    assert command[0] == sys.executable
    assert command[1].endswith("capture_windows.py")
    assert command[2:] == ["--capture-audio", command[3], command[4]]
    assert command[3].endswith(".system.wav") and command[4].endswith(".microphone.wav")
    assert kwargs["stdin"] == subprocess.PIPE
    assert recorder.status() == {"active": False}


def test_windows_stop_schreibt_stoppzeile_und_schliesst_stdin(tmp_path, monkeypatch):
    import koprotokoll.capture as capture

    geschrieben = []

    class FakeStdin:
        def write(self, text):
            geschrieben.append(text)

        def flush(self):
            pass

        def close(self):
            geschrieben.append("geschlossen")

    process = SimpleNamespace(poll=lambda: None, wait=lambda **_: 0,
                              stdin=FakeStdin(), returncode=0)
    active = capture.ActiveCapture(
        device=capture.SYSTEM_AUDIO_DEVICE, path=tmp_path / "aufnahme.wav", process=process,
        channels=2, sample_rate=48_000, started_at=0.0,
        system_path=tmp_path / "aufnahme.system.wav", mic_path=tmp_path / "aufnahme.microphone.wav")
    recorder = capture.HardwareRecorder(tmp_path)
    recorder._active = active
    monkeypatch.setattr(capture.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="keine Audiodatei"):
        recorder.stop()
    assert geschrieben == [b"stop\n", "geschlossen"]
