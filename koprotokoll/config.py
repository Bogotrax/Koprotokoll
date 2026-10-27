"""Konfiguration über Umgebungsvariablen (alle Werte haben lokale Defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# CalDAV-Anbieter-Presets für die Quellen-Einrichtung. ``server`` ist die
# Basis-URL für die Server-Erkennung nach RFC 6764 (/.well-known/caldav →
# Principal → Kalender-Verzeichnis). Weicht ein Anbieter davon ab, tragen
# Nutzerinnen die Server- und Kalender-URL in den erweiterten Einstellungen
# ein – die Erkennung scheitert dann abgesichert mit einer deutschen Meldung.
# Google und Outlook.com fehlen absichtlich: deren Kalender laufen über die
# OAuth-Punkte (Google/Microsoft), nicht über klassisches CalDAV.
CALDAV_PRESETS: dict[str, dict[str, str]] = {
    "icloud": {"label": "iCloud", "server": "https://caldav.icloud.com/",
               "hint": "Apple-ID, z. B. name@icloud.com"},
    "posteo": {"label": "Posteo", "server": "https://posteo.de:8443/",
               "hint": "Posteo-E-Mail-Adresse"},
    "mailbox": {"label": "mailbox.org", "server": "https://caldav.mailbox.org/",
                "hint": "mailbox.org-Adresse"},
    "fastmail": {"label": "Fastmail", "server": "https://caldav.fastmail.com/",
                 "hint": "Fastmail-Adresse"},
    "nextcloud": {"label": "Nextcloud / eigener Server", "server": "",
                  "hint": "Server-Adresse, z. B. https://cloud.example.com"},
    "other": {"label": "Anderer Anbieter", "server": "",
              "hint": "Server- und Kalender-URL unter „Erweiterte Einstellungen“"},
}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("KOPROTOKOLL_DATA", "./data")).resolve())
    # Öffentliche Basis-URL (für OAuth-Redirect, Links in E-Mails).
    public_url: str = field(default_factory=lambda: _env("KOPROTOKOLL_PUBLIC_URL", "http://localhost:8000").rstrip("/"))

    # Parakeet (NVIDIA NeMo) über onnx-asr – läuft auf CPU, GPU optional.
    asr_model: str = field(default_factory=lambda: _env("KOPROTOKOLL_ASR_MODEL", "nemo-parakeet-tdt-0.6b-v3"))
    asr_quantization: str | None = field(default_factory=lambda: _env("KOPROTOKOLL_ASR_QUANT", "int8") or None)
    asr_model_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_ASR_PATH"))

    # Sprechererkennung (sherpa-onnx: pyannote-Segmentierung + Sprecher-Embeddings).
    diarization: bool = field(default_factory=lambda: _env("KOPROTOKOLL_DIARIZATION", "1") != "0")
    diarization_threshold: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_DIARIZATION_THRESHOLD", "0.8")))

    # Lokales LLM über eine OpenAI-kompatible API (Ollama, llama.cpp-Server, LM Studio …).
    llm_base_url: str = field(default_factory=lambda: _env("KOPROTOKOLL_LLM_URL", "http://localhost:11434/v1"))
    llm_model: str = field(default_factory=lambda: _env("KOPROTOKOLL_LLM_MODEL", "qwen2.5:7b-instruct"))
    llm_api_key: str = field(default_factory=lambda: _env("KOPROTOKOLL_LLM_API_KEY", "local"))
    llm_enabled: bool = field(default_factory=lambda: _env("KOPROTOKOLL_LLM", "1") != "0")
    llm_timeout: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_LLM_TIMEOUT", "300")))

    # Google-Integration (Kalender + Gmail) – OAuth-Client aus der Google Cloud Console.
    google_client_id: str = field(default_factory=lambda: _env("KOPROTOKOLL_GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: _env("KOPROTOKOLL_GOOGLE_CLIENT_SECRET", ""))
    calendar_poll_seconds: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_CALENDAR_POLL", "60")))

    # Microsoft-Integration (Kalender + Mail) – OAuth-Client aus der Azure-App-Registrierung.
    microsoft_client_id: str = field(default_factory=lambda: _env("KOPROTOKOLL_MICROSOFT_CLIENT_ID", ""))
    microsoft_client_secret: str = field(default_factory=lambda: _env("KOPROTOKOLL_MICROSOFT_CLIENT_SECRET", ""))

    # Meeting-Bot (tritt Meetings als Teilnehmer bei).
    bot_name: str = field(default_factory=lambda: _env("KOPROTOKOLL_BOT_NAME", "Koprotokoll"))
    bot_headless: bool = field(default_factory=lambda: _env("KOPROTOKOLL_BOT_HEADLESS", "1") != "0")
    bot_chrome_path: str | None = field(default_factory=lambda: os.environ.get("KOPROTOKOLL_BOT_CHROME"))
    bot_silence_minutes: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_SILENCE_MINUTES", "1")))
    bot_max_minutes: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_MAX_MINUTES", "180")))
    bot_admit_minutes: float = field(default_factory=lambda: float(_env("KOPROTOKOLL_BOT_ADMIT_MINUTES", "10")))

    # Eigene Jitsi-Instanzen (kommagetrennte Hostnames) zusätzlich zu meet.jit.si.
    jitsi_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower() for h in _env("KOPROTOKOLL_JITSI_HOSTS", "").split(",") if h.strip()
        )
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "koprotokoll.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def bot_profile_dir(self) -> Path:
        return self.data_dir / "bot-profile"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)
