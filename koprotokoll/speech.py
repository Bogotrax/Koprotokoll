"""Spracherkennung (Parakeet), Sprechererkennung und Zusammenführung zu Äußerungen."""

from __future__ import annotations

import logging
import tarfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from .audio import SAMPLE_RATE

log = logging.getLogger(__name__)

ProgressFn = Callable[[float], None]


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Turn:
    start: float
    end: float
    speaker: int


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, progress: ProgressFn) -> list[Word]: ...


class Diarizer(Protocol):
    def diarize(self, audio: np.ndarray, num_speakers: int | None, progress: ProgressFn) -> list[Turn]: ...


# --------------------------------------------------------------------------
# Parakeet über onnx-asr
# --------------------------------------------------------------------------
class ParakeetTranscriber:
    """NVIDIA Parakeet TDT (Standard: v3, 25 europäische Sprachen inkl. Deutsch)."""

    def __init__(self, model: str, quantization: str | None, path: str | None = None):
        self._model_name, self._quant, self._path = model, quantization, path
        self._lock = threading.Lock()
        self._asr = None

    def _load(self):
        with self._lock:
            if self._asr is None:
                import onnx_asr

                log.info("Lade ASR-Modell %s (%s) …", self._model_name, self._quant or "fp32")
                model = onnx_asr.load_model(self._model_name, self._path, quantization=self._quant)
                vad = onnx_asr.load_vad("silero")
                self._asr = model.with_vad(vad, max_speech_duration_s=25).with_timestamps()
        return self._asr

    def transcribe(self, audio: np.ndarray, progress: ProgressFn) -> list[Word]:
        asr = self._load()
        total = max(len(audio) / SAMPLE_RATE, 1e-6)
        words: list[Word] = []
        for seg in asr.recognize(audio, sample_rate=SAMPLE_RATE):
            words.extend(tokens_to_words(seg.tokens or [], seg.timestamps or [], seg.start, seg.end, seg.text))
            progress(min(seg.end / total, 1.0))
        return words


def tokens_to_words(tokens: list[str], stamps: list[float], seg_start: float, seg_end: float,
                    fallback_text: str = "") -> list[Word]:
    """Subword-Tokens (Leerzeichen = Wortanfang) mit relativen Zeitstempeln zu Wörtern zusammensetzen."""
    if not tokens or len(tokens) != len(stamps):
        text = fallback_text.strip()
        return [Word(seg_start, seg_end, text)] if text else []
    words: list[Word] = []
    cur_text, cur_start = "", None
    for i, (tok, ts) in enumerate(zip(tokens, stamps)):
        t = seg_start + ts
        if cur_start is None or tok.startswith(" "):
            if cur_text.strip():
                words.append(Word(cur_start, t, cur_text.strip()))
            cur_text, cur_start = tok, t
        else:
            cur_text += tok
    if cur_text.strip():
        words.append(Word(cur_start, max(seg_end, cur_start), cur_text.strip()))
    return words


# --------------------------------------------------------------------------
# Sprechererkennung über sherpa-onnx
# --------------------------------------------------------------------------
SEGMENTATION_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMBEDDING_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                 "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")


def ensure_diarization_models(models_dir: Path) -> tuple[Path, Path]:
    seg = models_dir / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    emb = models_dir / "speaker-embedding-eres2net.onnx"
    models_dir.mkdir(parents=True, exist_ok=True)
    if not seg.exists():
        archive = models_dir / "segmentation.tar.bz2"
        log.info("Lade Segmentierungsmodell …")
        urllib.request.urlretrieve(SEGMENTATION_URL, archive)
        with tarfile.open(archive) as tar:
            tar.extractall(models_dir, filter="data")
        archive.unlink()
    if not emb.exists():
        log.info("Lade Sprecher-Embedding-Modell …")
        tmp = emb.with_suffix(".part")
        urllib.request.urlretrieve(EMBEDDING_URL, tmp)
        tmp.rename(emb)
    return seg, emb


class SherpaDiarizer:
    def __init__(self, models_dir: Path, threshold: float):
        self._models_dir, self._threshold = models_dir, threshold

    def diarize(self, audio: np.ndarray, num_speakers: int | None, progress: ProgressFn) -> list[Turn]:
        import sherpa_onnx

        seg, emb = ensure_diarization_models(self._models_dir)
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg))),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=num_speakers if num_speakers else -1, threshold=self._threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not cfg.validate():
            raise RuntimeError("Ungültige Diarisierungs-Konfiguration")
        sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)

        def cb(done: int, total: int) -> int:
            progress(done / max(total, 1))
            return 0

        result = sd.process(audio, callback=cb).sort_by_start_time()
        return [Turn(r.start, r.end, r.speaker) for r in result]


# --------------------------------------------------------------------------
# Wörter + Sprecherwechsel → lesbare Äußerungen
# --------------------------------------------------------------------------
SENTENCE_END = (".", "?", "!", "…")


def speaker_for(word: Word, turns: list[Turn]) -> int | None:
    """Sprecher mit der größten zeitlichen Überlappung; sonst der nächstgelegene."""
    best, best_overlap = None, 0.0
    for t in turns:
        overlap = min(word.end, t.end) - max(word.start, t.start)
        if overlap > best_overlap:
            best, best_overlap = t.speaker, overlap
    if best is not None or not turns:
        return best
    mid = (word.start + word.end) / 2
    return min(turns, key=lambda t: min(abs(mid - t.start), abs(mid - t.end))).speaker


def build_utterances(words: list[Word], turns: list[Turn], max_len: float = 30.0,
                     max_gap: float = 1.5) -> list[dict]:
    """Wörter zu Äußerungen gruppieren; Sprecher-IDs in Auftrittsreihenfolge nummerieren."""
    labels: dict[int, str] = {}

    def label(spk: int | None) -> str:
        if spk is None:
            spk = -1
        if spk not in labels:
            labels[spk] = f"S{len(labels) + 1}"
        return labels[spk]

    out: list[dict] = []
    cur: dict | None = None
    for w in words:
        spk = label(speaker_for(w, turns) if turns else None)
        new = (
            cur is None
            or spk != cur["speaker"]
            or w.start - cur["end"] > max_gap
            or (cur["end"] - cur["start"] > max_len and cur["text"].endswith(SENTENCE_END))
        )
        if new:
            if cur:
                out.append(cur)
            cur = {"start": w.start, "end": w.end, "speaker": spk, "text": w.text}
        else:
            cur["text"] += " " + w.text
            cur["end"] = max(cur["end"], w.end)
    if cur:
        out.append(cur)
    for u in out:
        u["start"], u["end"] = round(u["start"], 2), round(u["end"], 2)
    return out
