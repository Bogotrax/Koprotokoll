"""FastAPI-Server: REST-API + Weboberfläche."""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import time
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from .bot.platforms import SUPPORTED, detect_platform, find_meeting_url
from .calendar_source import CalendarEvent
from .config import Settings
from .db import Database
from .events import EVENTS, EventBus
from .export import export
from .google import GoogleCalendarSource, GoogleClient, GoogleError
from .microsoft import MicrosoftCalendarSource, MicrosoftClient, MicrosoftError
from .pipeline import Pipeline
from .recap import RecapMailer
from .scheduler import CalendarScheduler
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


class BotIn(BaseModel):
    meeting_url: str
    title: str | None = None
    join_at: float | None = None  # Unix-Zeit; leer = sofort
    platform: str | None = None   # automatisch erkannt; "web" = beliebige Webseite


class KeyIn(BaseModel):
    name: str


class WebhookIn(BaseModel):
    url: str
    events: list[str] = ["*"]
    secret: str | None = None


class GoogleClientIn(BaseModel):
    client_id: str
    client_secret: str


class GoogleSettingsIn(BaseModel):
    auto_join: bool | None = None
    send_recap: bool | None = None
    recap_to_attendees: bool | None = None


class MicrosoftClientIn(BaseModel):
    client_id: str
    client_secret: str


class MicrosoftSettingsIn(BaseModel):
    auto_join: bool | None = None
    send_recap: bool | None = None
    recap_to_attendees: bool | None = None


def create_app(settings: Settings | None = None, transcriber: Transcriber | None = None,
               diarizer: Diarizer | None = None, llm: LLM | None = None,
               google: GoogleClient | None = None, background: bool = True) -> FastAPI:
    s = settings or Settings()
    s.ensure_dirs()
    db = Database(s.db_path)
    if transcriber is None:
        transcriber = ParakeetTranscriber(s.asr_model, s.asr_quantization, s.asr_model_path)
    if diarizer is None and s.diarization:
        diarizer = SherpaDiarizer(s.models_dir, s.diarization_threshold)
    if llm is None:
        llm = LLM(s)
    events = EventBus(db)
    pipeline = Pipeline(db, s.media_dir, transcriber, diarizer, llm, events)
    google = google or GoogleClient(s, db)
    microsoft = MicrosoftClient(s, db)
    events.subscribe(RecapMailer(google, s.public_url))
    from .bot.runner import BotManager

    bots = BotManager(s, db, pipeline, events, tick_seconds=5.0 if background else 3600)

    def build_sources() -> list:
        sources: list = [GoogleCalendarSource(google), MicrosoftCalendarSource(microsoft)]
        # Weitere gespeicherte Quellen-Arten (z. B. CalDAV) hier ergänzen.
        return sources

    calendar = CalendarScheduler(db, build_sources(), s.calendar_poll_seconds, s.jitsi_hosts)
    if background:
        calendar.start()

    # Nach einem Neustart unterbrochene Jobs erneut einreihen.
    for m in db.list_meetings():
        if m["status"] not in ("done", "error"):
            pipeline.submit(m["id"])

    app = FastAPI(
        title="Koprotokoll API",
        description="Lokaler Meeting-Assistent: Bots, Transkripte (Parakeet), Zusammenfassungen, Suche, "
                    "Webhooks und Google-Integration. Authentifizierung: `Authorization: Bearer kp_…` "
                    "(lokale Anfragen ohne Proxy brauchen keinen Schlüssel).",
        version="2.0",
    )
    app.state.db, app.state.pipeline, app.state.settings = db, pipeline, s
    app.state.events, app.state.bots, app.state.google, app.state.calendar = events, bots, google, calendar
    api = APIRouter(dependencies=[Depends(auth.require_auth)])
    public = APIRouter()

    def meeting_or_404(meeting_id: int) -> dict:
        m = db.get_meeting(meeting_id)
        if m is None:
            raise HTTPException(404, "Meeting nicht gefunden")
        return m

    @api.get("/api/status")
    def status():
        return {
            "asr_model": s.asr_model,
            "diarization": diarizer is not None,
            "llm": {"available": llm.available(), "model": s.llm_model, "url": s.llm_base_url},
        }

    @api.get("/api/meetings")
    def list_meetings():
        return [{k: m[k] for k in ("id", "title", "created_at", "duration", "status", "progress", "error")}
                for m in db.list_meetings()]

    @api.post("/api/meetings", status_code=201)
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

    @api.get("/api/meetings/{meeting_id}")
    def get_meeting(meeting_id: int):
        m = meeting_or_404(meeting_id)
        m.pop("audio_path", None)
        m["segments"] = db.get_segments(meeting_id)
        m["bookmarks"] = db.list_bookmarks(meeting_id)
        m["talk_time"] = talk_time(m["segments"])
        return m

    @api.patch("/api/meetings/{meeting_id}")
    def patch_meeting(meeting_id: int, patch: MeetingPatch):
        m = meeting_or_404(meeting_id)
        fields: dict = {}
        if patch.title is not None and patch.title.strip():
            fields["title"] = patch.title.strip()
        if patch.speakers is not None:
            fields["speakers"] = {**m["speakers"], **{k: v.strip() for k, v in patch.speakers.items() if v.strip()}}
        db.update_meeting(meeting_id, **fields)
        return {"ok": True}

    @api.delete("/api/meetings/{meeting_id}")
    def delete_meeting(meeting_id: int):
        m = meeting_or_404(meeting_id)
        db.delete_meeting(meeting_id)
        files = list(s.media_dir.glob(f"{meeting_id}.*"))
        if m["audio_path"]:
            files.append(Path(m["audio_path"]))
        for p in files:
            if p.resolve().parent == s.media_dir.resolve():
                p.unlink(missing_ok=True)
        return {"ok": True, "id": m["id"]}

    @api.get("/api/meetings/{meeting_id}/audio")
    def audio(meeting_id: int):
        m = meeting_or_404(meeting_id)
        wav = s.media_dir / f"{meeting_id}.wav"
        path = wav if wav.exists() else Path(m["audio_path"] or "")
        if not path.is_file():
            raise HTTPException(404, "Audio nicht vorhanden")
        return FileResponse(path)

    @api.post("/api/meetings/{meeting_id}/summarize")
    def resummarize(meeting_id: int):
        meeting_or_404(meeting_id)
        return pipeline.resummarize(meeting_id)

    @api.post("/api/meetings/{meeting_id}/ask")
    def ask_meeting(meeting_id: int, q: Question):
        m = meeting_or_404(meeting_id)
        hits = db.search(q.question, meeting_id, limit=10, any_word=True)
        return ask(q.question, db.get_segments(meeting_id), m["speakers"], llm, hits)

    @api.post("/api/meetings/{meeting_id}/bookmarks", status_code=201)
    def add_bookmark(meeting_id: int, b: BookmarkIn):
        meeting_or_404(meeting_id)
        bid = db.add_bookmark(meeting_id, b.start, max(b.end, b.start), b.kind, b.note)
        return {"id": bid}

    @api.delete("/api/meetings/{meeting_id}/bookmarks/{bookmark_id}")
    def delete_bookmark(meeting_id: int, bookmark_id: int):
        if not db.delete_bookmark(meeting_id, bookmark_id):
            raise HTTPException(404, "Markierung nicht gefunden")
        return {"ok": True}

    @api.get("/api/meetings/{meeting_id}/export")
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

    @api.get("/api/search")
    def search(q: str, meeting_id: int | None = None):
        return db.search(q, meeting_id)

    # -- Bots (Teilnehmer in Online-Meetings) -------------------------------------------
    @api.post("/api/bots", status_code=201, tags=["bots"])
    def create_bot(b: BotIn):
        url = b.meeting_url.strip()
        platform = b.platform or detect_platform(url, s.jitsi_hosts)
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "meeting_url muss mit http:// oder https:// beginnen")
        if platform is None:
            raise HTTPException(400, "Unbekannter Meeting-Link – für beliebige Webseiten platform='web' setzen")
        if platform not in SUPPORTED:
            raise HTTPException(400, f"Plattform {platform} wird vom Bot noch nicht unterstützt "
                                     f"(unterstützt: {', '.join(sorted(SUPPORTED))})")
        bot_id = db.create_bot(url, platform, (b.title or "").strip() or "Meeting", b.join_at or time.time())
        bots.dispatch_due()
        return db.get_bot(bot_id)

    @api.get("/api/bots", tags=["bots"])
    def list_bots():
        return db.list_bots()

    @api.get("/api/bots/{bot_id}", tags=["bots"])
    def get_bot(bot_id: int):
        bot = db.get_bot(bot_id)
        if bot is None:
            raise HTTPException(404, "Bot nicht gefunden")
        return bot

    @api.delete("/api/bots/{bot_id}", tags=["bots"])
    def stop_bot(bot_id: int):
        if not bots.stop(bot_id):
            raise HTTPException(404, "Kein aktiver oder geplanter Bot mit dieser ID")
        return {"ok": True}

    # -- API-Schlüssel ---------------------------------------------------------------------
    @api.get("/api/keys", tags=["admin"])
    def list_keys():
        return db.list_api_keys()

    @api.post("/api/keys", status_code=201, tags=["admin"])
    def create_key(k: KeyIn):
        return auth.create_key(db, k.name.strip() or "API-Schlüssel")

    @api.delete("/api/keys/{key_id}", tags=["admin"])
    def delete_key(key_id: int):
        if not db.delete_api_key(key_id):
            raise HTTPException(404, "Schlüssel nicht gefunden")
        return {"ok": True}

    # -- Webhooks -------------------------------------------------------------------------------
    @api.get("/api/webhooks", tags=["webhooks"])
    def list_webhooks():
        return [{**h, "secret": h["secret"][:6] + "…"} for h in db.list_webhooks()]

    @api.post("/api/webhooks", status_code=201, tags=["webhooks"])
    def add_webhook(w: WebhookIn):
        if not w.url.startswith(("http://", "https://")):
            raise HTTPException(400, "Webhook-URL muss mit http:// oder https:// beginnen")
        unknown = [e for e in w.events if e != "*" and e not in EVENTS]
        if unknown:
            raise HTTPException(400, f"Unbekannte Ereignisse: {unknown}; erlaubt: {list(EVENTS)}")
        secret = w.secret or secrets.token_urlsafe(24)
        return {"id": db.add_webhook(w.url, secret, w.events or ["*"]), "secret": secret}

    @api.delete("/api/webhooks/{webhook_id}", tags=["webhooks"])
    def delete_webhook(webhook_id: int):
        if not db.delete_webhook(webhook_id):
            raise HTTPException(404, "Webhook nicht gefunden")
        return {"ok": True}

    @api.post("/api/webhooks/{webhook_id}/test", tags=["webhooks"])
    def test_webhook(webhook_id: int):
        hook = next((h for h in db.list_webhooks() if h["id"] == webhook_id), None)
        if hook is None:
            raise HTTPException(404, "Webhook nicht gefunden")
        return {"delivered": events.deliver(hook, "test", {"message": "Hallo vom Koprotokoll"})}

    # -- Google (Kalender + Gmail) ----------------------------------------------------------------
    @api.get("/api/integrations/google", tags=["google"])
    def google_status():
        return {**google.status(), "last_sync": calendar.last_sync}

    @api.put("/api/integrations/google/client", tags=["google"])
    def google_client(c: GoogleClientIn):
        google.set_client(c.client_id, c.client_secret)
        return google.status()

    @api.get("/api/integrations/google/connect", tags=["google"])
    def google_connect():
        try:
            return RedirectResponse(google.auth_url())
        except GoogleError as e:
            raise HTTPException(400, str(e)) from e

    @public.get("/api/integrations/google/callback", include_in_schema=False)
    def google_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse("/#integrations?" + urllib.parse.urlencode({"google_error": error[:100]}))
        try:
            google.exchange(code, state)
        except (GoogleError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse("/")

    @api.patch("/api/integrations/google/settings", tags=["google"])
    def google_settings(g: GoogleSettingsIn):
        return google.update_settings(**g.model_dump(exclude_none=True))

    @api.post("/api/integrations/google/sync", tags=["google"])
    def google_sync():
        try:
            created = calendar.sync()
        except GoogleError as e:
            raise HTTPException(400, str(e)) from e
        bots.dispatch_due()
        return {"created": [db.get_bot(b) for b in created]}

    @api.delete("/api/integrations/google", tags=["google"])
    def google_disconnect():
        google.disconnect()
        return {"ok": True}

    # -- Microsoft (Kalender + Mail) ------------------------------------------------------
    @api.get("/api/integrations/microsoft", tags=["microsoft"])
    def microsoft_status():
        return {**microsoft.status(), "last_sync": calendar.last_sync}

    @api.put("/api/integrations/microsoft/client", tags=["microsoft"])
    def microsoft_client(c: MicrosoftClientIn):
        microsoft.set_client(c.client_id, c.client_secret)
        return microsoft.status()

    @api.get("/api/integrations/microsoft/connect", tags=["microsoft"])
    def microsoft_connect():
        try:
            return RedirectResponse(microsoft.auth_url())
        except MicrosoftError as e:
            raise HTTPException(400, str(e)) from e

    @public.get("/api/integrations/microsoft/callback", include_in_schema=False)
    def microsoft_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return RedirectResponse("/#integrations?" + urllib.parse.urlencode({"microsoft_error": error[:100]}))
        try:
            microsoft.exchange(code, state)
        except (MicrosoftError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse("/")

    @api.patch("/api/integrations/microsoft/settings", tags=["microsoft"])
    def microsoft_settings(m: MicrosoftSettingsIn):
        return microsoft.update_settings(**m.model_dump(exclude_none=True))

    @api.delete("/api/integrations/microsoft", tags=["microsoft"])
    def microsoft_disconnect():
        microsoft.disconnect()
        return {"ok": True}

    # -- Kommende Termine (Anzeige) --------------------------------------------------------
    @api.get("/api/upcoming", tags=["calendars"])
    def upcoming(days: int = 7):
        """Termine der nächsten Tage aus allen Kalenderquellen, inkl. Link-Erkennung.

        Anzeigefenster: 7 Tage (Entscheidung 2026-10-17). Die Bot-Einplanung bleibt
        wie gehabt auf ~15 Minuten vor Terminbeginn beschränkt.
        """
        days = max(1, min(days, 30))
        now = time.time()
        out: list[dict] = []
        for source in build_sources():
            try:
                source_events = source.events(now, now + days * 86400)
            except Exception:  # noqa: BLE001 – nicht verbundene/defekte Quellen nicht anzeigen
                continue
            for e in source_events:
                found = find_meeting_url(
                    {"location": e.location, "description": e.description,
                     "meeting_url": e.meeting_url or ""}, s.jitsi_hosts)
                url, platform = found or ("", "")
                will_join = bool(found and platform in SUPPORTED and source.enabled()
                                 and not e.all_day and e.self_response != "declined")
                if will_join:
                    reason = ""
                elif e.self_response == "declined":
                    reason = "abgelehnt – kein Bot"
                elif e.all_day:
                    reason = "ganztägig – kein Bot"
                elif not found:
                    reason = "kein Meeting-Link erkannt"
                elif platform not in SUPPORTED:
                    reason = f"„{platform}“ wird noch nicht unterstützt"
                else:
                    reason = "Auto-Join ist deaktiviert"
                out.append({
                    "id": e.id, "title": e.title, "start": e.start, "end": e.end,
                    "all_day": e.all_day, "source": source.name, "label": source.label,
                    "meeting_url": url, "platform": platform,
                    "self_response": e.self_response,
                    "will_join": will_join, "reason": reason,
                })
        out.sort(key=lambda x: x["start"])
        return out

    app.include_router(public)
    app.include_router(api)
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def talk_time(segments: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for seg in segments:
        out[seg["speaker"]] = round(out.get(seg["speaker"], 0.0) + seg["end"] - seg["start"], 2)
    return out
