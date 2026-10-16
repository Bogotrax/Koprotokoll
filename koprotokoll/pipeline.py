"""Hintergrund-Verarbeitung: Konvertieren → Transkribieren → Sprecher → Zusammenfassen."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from .audio import SAMPLE_RATE, load_wav, to_wav16k
from .db import Database
from .events import EventBus
from .speech import Diarizer, Transcriber, build_utterances
from .summarize import LLM, summarize

log = logging.getLogger(__name__)

# Fortschrittsanteile der einzelnen Schritte (Summe = 1).
STEPS = {"converting": (0.0, 0.05), "transcribing": (0.05, 0.7), "diarizing": (0.7, 0.9), "summarizing": (0.9, 1.0)}


class Pipeline:
    def __init__(self, db: Database, media_dir: Path, transcriber: Transcriber,
                 diarizer: Diarizer | None, llm: LLM | None, events: EventBus | None = None):
        self.db, self.media_dir, self.events = db, media_dir, events
        self.transcriber, self.diarizer, self.llm = transcriber, diarizer, llm
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._thread.start()

    def submit(self, meeting_id: int) -> None:
        self._queue.put(meeting_id)

    def join(self) -> None:
        self._queue.join()

    def _run(self) -> None:
        while True:
            meeting_id = self._queue.get()
            try:
                self.process(meeting_id)
            except Exception as e:  # noqa: BLE001
                log.exception("Verarbeitung von Meeting %s fehlgeschlagen", meeting_id)
                self.db.update_meeting(meeting_id, status="error", error=str(e))
                self._emit("meeting.failed", meeting_id)
            finally:
                self._queue.task_done()

    def _step(self, meeting_id: int, step: str):
        lo, hi = STEPS[step]
        self.db.update_meeting(meeting_id, status=step, progress=lo)

        def progress(frac: float) -> None:
            self.db.update_meeting(meeting_id, progress=round(lo + (hi - lo) * frac, 3))

        return progress

    def process(self, meeting_id: int) -> None:
        meeting = self.db.get_meeting(meeting_id)
        if meeting is None:
            return
        src = Path(meeting["audio_path"])
        wav = self.media_dir / f"{meeting_id}.wav"

        self._step(meeting_id, "converting")
        if not wav.exists():
            to_wav16k(src, wav)
        audio = load_wav(wav)
        self.db.update_meeting(meeting_id, duration=round(len(audio) / SAMPLE_RATE, 2))

        words = self.transcriber.transcribe(audio, self._step(meeting_id, "transcribing"))

        turns = []
        if self.diarizer is not None and words:
            progress = self._step(meeting_id, "diarizing")
            try:
                turns = self.diarizer.diarize(audio, meeting.get("num_speakers"), progress)
            except Exception as e:  # noqa: BLE001 – ohne Sprecher ist das Transkript trotzdem nützlich
                log.warning("Sprechererkennung fehlgeschlagen: %s", e)

        segments = build_utterances(words, turns)
        self.db.replace_segments(meeting_id, segments)
        speakers = {s["speaker"]: meeting["speakers"].get(s["speaker"], f"Sprecher {s['speaker'][1:]}")
                    for s in segments}
        self.db.update_meeting(meeting_id, speakers=speakers)

        self._step(meeting_id, "summarizing")
        summary = summarize(self.db.get_segments(meeting_id), speakers, self.llm)
        fields: dict = {"summary": summary, "status": "done", "progress": 1.0, "error": None}
        if summary.get("title") and meeting["title"] == meeting.get("source_name"):
            fields["title"] = summary["title"]
        self.db.update_meeting(meeting_id, **fields)
        self._emit("meeting.completed", meeting_id)

    def _emit(self, event: str, meeting_id: int) -> None:
        if self.events is None:
            return
        m = self.db.get_meeting(meeting_id)
        if m is not None:
            self.events.emit(event, meeting_payload(m))

    def resummarize(self, meeting_id: int) -> dict:
        meeting = self.db.get_meeting(meeting_id)
        summary = summarize(self.db.get_segments(meeting_id), meeting["speakers"], self.llm)
        self.db.update_meeting(meeting_id, summary=summary)
        return summary


def meeting_payload(m: dict) -> dict:
    """Öffentliche Darstellung eines Meetings für Webhooks und E-Mails."""
    return {k: m.get(k) for k in ("id", "title", "created_at", "duration", "status", "error",
                                    "speakers", "summary", "meta")}
