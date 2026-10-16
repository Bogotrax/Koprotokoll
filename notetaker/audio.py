"""Audio-/Videodateien per ffmpeg in 16-kHz-Mono-WAV umwandeln."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16_000


def to_wav16k(src: Path, dst: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg wurde nicht gefunden – bitte installieren (z. B. apt install ffmpeg).")
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg konnte die Datei nicht lesen: {proc.stderr.strip()[:500]}")


def load_wav(path: Path) -> np.ndarray:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        raise ValueError(f"Erwartet {SAMPLE_RATE} Hz, erhalten {sr} Hz")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio
