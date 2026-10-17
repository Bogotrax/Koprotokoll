"""OpenAI-kompatible Kurz-Transkription (``/v1/audio/transcriptions``), z. B. für Kodiktat.

Anders als ein Meeting wird hier nichts gespeichert: keine Sprechererkennung, keine
Zusammenfassung, die Aufnahme liegt nur während der Anfrage in einem temporären Ordner.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

import numpy as np

from .audio import SAMPLE_RATE, load_wav, to_wav16k
from .export import _srt_time
from .speech import Transcriber, Word, build_utterances

FORMATS = ("json", "text", "verbose_json", "srt", "vtt")


def transcribe_upload(data: BinaryIO, suffix: str, transcriber: Transcriber,
                      tmp_root: Path) -> tuple[list[Word], float]:
    """Hochgeladene Datei temporär ablegen, in 16 kHz umwandeln, transkribieren und wieder löschen."""
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tmp_root) as tmp:
        src = Path(tmp) / f"upload{suffix}"
        with src.open("wb") as out:
            shutil.copyfileobj(data, out)
        audio = _load(src, Path(tmp) / "audio.wav")
        words = transcriber.transcribe(audio, lambda _frac: None)
    return words, round(len(audio) / SAMPLE_RATE, 2)


def _load(src: Path, wav: Path) -> np.ndarray:
    # 16-kHz-WAV (so schickt es Kodiktat) direkt lesen, alles andere über ffmpeg.
    try:
        return load_wav(src)
    except Exception:  # noqa: BLE001 – kein passendes WAV, dann eben ffmpeg
        to_wav16k(src, wav)
        return load_wav(wav)


def render(words: list[Word], duration: float, fmt: str, language: str | None) -> tuple[str | dict, str]:
    """Ergebnis im gewünschten ``response_format`` als (Inhalt, MIME-Typ)."""
    text = " ".join(w.text for w in words).strip()
    if fmt == "json":
        return {"text": text}, "application/json"
    if fmt == "text":
        return text + "\n", "text/plain; charset=utf-8"
    segments = [{"id": i, "start": u["start"], "end": u["end"], "text": u["text"]}
                for i, u in enumerate(build_utterances(words, []))]
    if fmt == "verbose_json":
        return {"task": "transcribe", "language": language or "", "duration": duration,
                "text": text, "segments": segments}, "application/json"
    if fmt == "srt":
        blocks = [f"{i}\n{_srt_time(s['start'])} --> {_srt_time(s['end'])}\n{s['text']}\n"
                  for i, s in enumerate(segments, 1)]
        return "\n".join(blocks), "application/x-subrip; charset=utf-8"
    if fmt == "vtt":
        blocks = [f"{_srt_time(s['start']).replace(',', '.')} --> {_srt_time(s['end']).replace(',', '.')}\n{s['text']}\n"
                  for s in segments]
        return "WEBVTT\n\n" + "\n".join(blocks), "text/vtt; charset=utf-8"
    raise ValueError(f"Unbekanntes response_format: {fmt} (erlaubt: {', '.join(FORMATS)})")
