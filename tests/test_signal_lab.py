"""Statische Sicherheitsgrenzen des separat archivierten Signal-Labors."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "docker" / "signal-lab"


def test_signal_lab_uses_fresh_base_and_explicit_files():
    dockerfile = (LAB / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM bbernhard/signal-cli-rest-api@sha256:" in dockerfile
    assert "checkpoint" not in dockerfile
    assert "COPY . " not in dockerfile
    assert "sha256sum -c -" in dockerfile
    ignored = (LAB / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignored[0] == "**"
    assert "!artifacts/signal-call-tunnel.real" in ignored
    assert "!data/" not in ignored


def test_signal_lab_is_separate_and_local_only():
    compose = (ROOT / "docker-compose.signal-lab.yml").read_text(encoding="utf-8")
    assert "./data/signal-cli" not in compose
    assert "container_name:" not in compose
    for port in ("18090:8080", "16003:6003", "19002:9002"):
        assert f'"127.0.0.1:{port}"' in compose
    assert "signal-lab-account:/home/.local/share/signal-cli" in compose
    assert compose.count("signal-lab-tmp:/tmp") == 2
    assert "acceptCall" not in compose
    assert "startCall" not in compose


def test_signal_lab_wrapper_and_relay_are_present():
    wrapper = (LAB / "signal-call-tunnel").read_text(encoding="utf-8")
    assert "SIGNAL_CALL_TUNNEL_AUDIO_MODE=pipe exec " in wrapper
    assert '"$@"' in wrapper
    relay = (LAB / "audio-relay.pl").read_text(encoding="utf-8")
    assert "my $TCP_PORT = 9002;" in relay
    assert '"\\x00" x 960' in relay
