"""Konfiguration über Umgebungsvariablen (alle Werte haben lokale Defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("NOTETAKER_DATA", "./data")).resolve())

    # Parakeet (NVIDIA NeMo) über onnx-asr – läuft auf CPU, GPU optional.
    asr_model: str = field(default_factory=lambda: _env("NOTETAKER_ASR_MODEL", "nemo-parakeet-tdt-0.6b-v3"))
    asr_quantization: str | None = field(default_factory=lambda: _env("NOTETAKER_ASR_QUANT", "int8") or None)
    asr_model_path: str | None = field(default_factory=lambda: os.environ.get("NOTETAKER_ASR_PATH"))

    # Sprechererkennung (sherpa-onnx: pyannote-Segmentierung + Sprecher-Embeddings).
    diarization: bool = field(default_factory=lambda: _env("NOTETAKER_DIARIZATION", "1") != "0")
    diarization_threshold: float = field(default_factory=lambda: float(_env("NOTETAKER_DIARIZATION_THRESHOLD", "0.8")))

    # Lokales LLM über eine OpenAI-kompatible API (Ollama, llama.cpp-Server, LM Studio …).
    llm_base_url: str = field(default_factory=lambda: _env("NOTETAKER_LLM_URL", "http://localhost:11434/v1"))
    llm_model: str = field(default_factory=lambda: _env("NOTETAKER_LLM_MODEL", "qwen2.5:7b-instruct"))
    llm_api_key: str = field(default_factory=lambda: _env("NOTETAKER_LLM_API_KEY", "local"))
    llm_enabled: bool = field(default_factory=lambda: _env("NOTETAKER_LLM", "1") != "0")
    llm_timeout: float = field(default_factory=lambda: float(_env("NOTETAKER_LLM_TIMEOUT", "300")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "notetaker.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)
