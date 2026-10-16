"""FastAPI-Server: REST-API + Weboberfläche."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .db import Database
from .export import export
from .pipeline import Pipeline
from .speech import Diarizer, ParakeetTranscriber, SherpaDiarizer, Transcriber
from .summarize import LLM, ask

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
ALLOWED_EXT = {".mp3", ".mp4", ".wav", ".m4a", ".webm", ".ogg", ".oga", ".opus", ".flac", ".aac", ".mov", ".mkv"}


class MeetingPatch(BaseModel):
    title: str | None = None
    speakers: dict[str, str] | None = None


class BookmarkIn(BaseModel):
    start: float
    end: float
    kind: str = "soundbite"
    note: str = ""


class Question(BaseModel):
    question: str


def create_app(settings: Settings | None = None, transcriber: Transcriber | None = None,
               diarizer: Diarizer | None = None, llm: LLM | None = None) -> FastAPI:
    s = settings or Settings()
    s.ensure_dirs()
    db = Database(s.db_path)
    if transcriber is None:
        transcriber = ParakeetTranscriber(s.asr_model, s.asr_quantization, s.asr_model_path)
    if diarizer is None and s.diarization:
        diarizer = SherpaDiarizer(s.models_dir, s.diarization_threshold)
    if llm is None:
        llm = LLM(s)
    pipeline = Pipeline(db, s.media_dir, transcriber, diarizer, llm)

    # Nach einem Neustart unterbrochene Jobs erneut einreihen.
    for m in db.list_meetings():
        if m["status"] not in ("done", "error"):
            pipeline.submit(m["id"])

    app = FastAPI(title="Notetaker – lokaler Meeting-Assistent")
    app.state.db, app.state.pipeline, app.state.settings = db, pipeline, s

    def meeting_or_404(meeting_id: int) -> dict:
        m = db.get_meeting(meeting_id)
        if m is None:
            raise HTTPException(404, "Meeting nicht gefunden")
        return m

    @app.get("/api/status")
    def status():
        return {
            "asr_model": s.asr_model,
            "diarization": diarizer is not None,
            "llm": {"available": llm.available(), "model": s.llm_model, "url": s.llm_base_url},
        }

    @app.get("/api/meetings")
    def list_meetings():
        return [{k: m[k] for k in ("id", "title", "created_at", "duration", "status", "progress", "error")}
                for m in db.list_meetings()]

    @app.post("/api/meetings", status_code=201)
    def upload(file: UploadFile = File(...), title: str = Form(""), num_speakers: int | None = Form(None)):
        name = Path(file.filename or "aufnahme.webm").name
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"Dateityp {ext or '?'} wird nicht unterstützt")
        meeting_id = db.create_meeting(title.strip() or Path(name).stem, Path(name).stem,
                                       num_speakers if num_speakers and num_speakers > 0 else None)
        dst = s.media_dir / f"{meeting_id}{ext}"
        with dst.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        db.update_meeting(meeting_id, audio_path=str(dst))
        pipeline.submit(meeting_id)
        return db.get_meeting(meeting_id)

    @app.get("/api/meetings/{meeting_id}")
    def get_meeting(meeting_id: int):
        m = meeting_or_404(meeting_id)
        m.pop("audio_path", None)
        m["segments"] = db.get_segments(meeting_id)
        m["bookmarks"] = db.list_bookmarks(meeting_id)
        m["talk_time"] = talk_time(m["segments"])
        return m

    @app.patch("/api/meetings/{meeting_id}")
    def patch_meeting(meeting_id: int, patch: MeetingPatch):
        m = meeting_or_404(meeting_id)
        fields: dict = {}
        if patch.title is not None and patch.title.strip():
            fields["title"] = patch.title.strip()
        if patch.speakers is not None:
            fields["speakers"] = {**m["speakers"], **{k: v.strip() for k, v in patch.speakers.items() if v.strip()}}
        db.update_meeting(meeting_id, **fields)
        return {"ok": True}

    @app.delete("/api/meetings/{meeting_id}")
    def delete_meeting(meeting_id: int):
        m = meeting_or_404(meeting_id)
        db.delete_meeting(meeting_id)
        for p in s.media_dir.glob(f"{meeting_id}.*"):
            p.unlink(missing_ok=True)
        return {"ok": True, "id": m["id"]}

    @app.get("/api/meetings/{meeting_id}/audio")
    def audio(meeting_id: int):
        m = meeting_or_404(meeting_id)
        wav = s.media_dir / f"{meeting_id}.wav"
        path = wav if wav.exists() else Path(m["audio_path"] or "")
        if not path.is_file():
            raise HTTPException(404, "Audio nicht vorhanden")
        return FileResponse(path)

    @app.post("/api/meetings/{meeting_id}/summarize")
    def resummarize(meeting_id: int):
        meeting_or_404(meeting_id)
        return pipeline.resummarize(meeting_id)

    @app.post("/api/meetings/{meeting_id}/ask")
    def ask_meeting(meeting_id: int, q: Question):
        m = meeting_or_404(meeting_id)
        hits = db.search(q.question, meeting_id, limit=10, any_word=True)
        return ask(q.question, db.get_segments(meeting_id), m["speakers"], llm, hits)

    @app.post("/api/meetings/{meeting_id}/bookmarks", status_code=201)
    def add_bookmark(meeting_id: int, b: BookmarkIn):
        meeting_or_404(meeting_id)
        bid = db.add_bookmark(meeting_id, b.start, max(b.end, b.start), b.kind, b.note)
        return {"id": bid}

    @app.delete("/api/meetings/{meeting_id}/bookmarks/{bookmark_id}")
    def delete_bookmark(meeting_id: int, bookmark_id: int):
        if not db.delete_bookmark(meeting_id, bookmark_id):
            raise HTTPException(404, "Markierung nicht gefunden")
        return {"ok": True}

    @app.get("/api/meetings/{meeting_id}/export")
    def export_meeting(meeting_id: int, format: str = "md"):
        m = meeting_or_404(meeting_id)
        m.pop("audio_path", None)
        try:
            body, mime = export(m, db.get_segments(meeting_id), db.list_bookmarks(meeting_id), format)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        filename = re.sub(r"[^\w.-]+", "_", m["title"])[:80] or "meeting"
        return Response(body, media_type=mime,
                        headers={"Content-Disposition": f'attachment; filename="{filename}.{format}"'})

    @app.get("/api/search")
    def search(q: str, meeting_id: int | None = None):
        return db.search(q, meeting_id)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def talk_time(segments: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for seg in segments:
        out[seg["speaker"]] = round(out.get(seg["speaker"], 0.0) + seg["end"] - seg["start"], 2)
    return out
