"""Bot-Verwaltung: geplante Bots starten, Browser steuern, Aufnahme an die Pipeline geben."""

from __future__ import annotations

import base64
import json
import logging
import shutil
import tempfile
import threading
import time
import wave
from pathlib import Path

from ..config import Settings
from ..db import Database
from ..events import EventBus
from ..pipeline import Pipeline
from .platforms import ADAPTERS, BotError

log = logging.getLogger(__name__)

CAPTURE_JS = (Path(__file__).parent / "capture.js").read_text(encoding="utf-8")
ACTIVE = ("joining", "waiting_admit", "recording", "stopping")
SOUND_LEVEL = 0.003  # RMS-Schwelle für „es wird gesprochen“
ALONE_GRACE_S = 120


class BotManager:
    def __init__(self, settings: Settings, db: Database, pipeline: Pipeline, events: EventBus | None,
                 max_concurrent: int = 3, tick_seconds: float = 5.0):
        self.s, self.db, self.pipeline, self.events = settings, db, pipeline, events
        self.max_concurrent, self.tick = max_concurrent, tick_seconds
        self._sessions: dict[int, BotSession] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        for bot in db.list_bots(ACTIVE):  # nach Neustart verwaiste Bots aufräumen
            db.update_bot(bot["id"], status="failed", error="Server wurde während der Aufnahme neu gestartet",
                          ended_at=time.time())
        self._thread = threading.Thread(target=self._loop, name="bots", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        for session in list(self._sessions.values()):
            session.stop_requested.set()

    def set_status(self, bot_id: int, status: str, **fields) -> None:
        if status in ("completed", "failed", "stopped", "cancelled"):
            fields.setdefault("ended_at", time.time())
        self.db.update_bot(bot_id, status=status, **fields)
        if self.events:
            bot = self.db.get_bot(bot_id)
            self.events.emit("bot.status", {k: bot[k] for k in
                                            ("id", "status", "error", "meeting_url", "platform", "title", "meeting_id")})

    def stop(self, bot_id: int) -> bool:
        bot = self.db.get_bot(bot_id)
        if bot is None:
            return False
        if bot["status"] == "scheduled":
            self.set_status(bot_id, "cancelled")
            return True
        session = self._sessions.get(bot_id)
        if session:
            session.stop_requested.set()
            return True
        return False

    def _loop(self) -> None:
        while not self._stop.wait(self.tick):
            try:
                self.dispatch_due()
            except Exception:  # noqa: BLE001
                log.exception("Bot-Planung fehlgeschlagen")

    def dispatch_due(self) -> None:
        now = time.time()
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v.thread.is_alive()}
            for bot in sorted(self.db.list_bots(("scheduled",)), key=lambda b: b["join_at"]):
                if bot["join_at"] > now or len(self._sessions) >= self.max_concurrent:
                    continue
                session = BotSession(self, bot["id"])
                self._sessions[bot["id"]] = session
                session.thread.start()


class BotSession:
    def __init__(self, manager: BotManager, bot_id: int):
        self.m, self.bot_id = manager, bot_id
        self.stop_requested = threading.Event()
        self.stopped_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"bot-{bot_id}", daemon=True)
        self.audio_path = manager.s.media_dir / f"bot-{bot_id}.webm"
        self.bytes_written = 0

    # Aufrufe aus dem Browser -------------------------------------------------
    def _on_chunk(self, _source, b64: str) -> None:
        data = base64.b64decode(b64)
        with self.audio_path.open("ab") as f:
            f.write(data)
        self.bytes_written += len(data)

    def _on_event(self, _source, payload: str) -> None:
        event = json.loads(payload)
        if event.get("type") == "stopped":
            self.stopped_event.set()

    def run(self) -> None:
        bot = self.m.db.get_bot(self.bot_id)
        try:
            self.m.set_status(self.bot_id, "joining")
            self._record(bot)
        except BotError as e:
            self.m.set_status(self.bot_id, "failed", error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Bot %s fehlgeschlagen", self.bot_id)
            self.m.set_status(self.bot_id, "failed", error=f"{type(e).__name__}: {e}")
            return
        if self.bytes_written < 2000:
            self.m.set_status(self.bot_id, "failed", error="Keine Audiodaten aufgenommen")
            return
        meta = {"source": "bot", "bot_id": self.bot_id, "meeting_url": bot["meeting_url"],
                "platform": bot["platform"], **bot["meta"]}
        meeting_id = self.m.db.create_meeting(bot["title"], None, meta=meta)
        self.m.db.update_meeting(meeting_id, audio_path=str(self.audio_path))
        self.m.pipeline.submit(meeting_id)
        status = "stopped" if self.stop_requested.is_set() else "completed"
        self.m.set_status(self.bot_id, status, meeting_id=meeting_id)

    def _record(self, bot: dict) -> None:
        from playwright.sync_api import sync_playwright

        s = self.m.s
        adapter = ADAPTERS[bot["platform"]]()
        self.audio_path.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="koprotokoll-bot-") as tmp:
            profile = Path(tmp) / "profile"
            if s.bot_profile_dir.exists():  # angemeldetes Bot-Konto mitnehmen
                shutil.copytree(s.bot_profile_dir, profile, ignore=shutil.ignore_patterns("Singleton*", "*.lock"))
            silence = Path(tmp) / "silence.wav"
            _write_silence(silence)
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    str(profile),
                    headless=s.bot_headless,
                    executable_path=s.bot_chrome_path or None,
                    locale="en-US",
                    viewport={"width": 1280, "height": 800},
                    permissions=["microphone", "camera"],
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        f"--use-file-for-fake-audio-capture={silence}",
                        "--disable-blink-features=AutomationControlled",
                        "--lang=en-US",
                    ],
                )
                try:
                    ctx.expose_binding("__ntChunk", self._on_chunk)
                    ctx.expose_binding("__ntEvent", self._on_event)
                    ctx.add_init_script(CAPTURE_JS)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    adapter.join(page, bot["meeting_url"], s.bot_name, s.bot_admit_minutes * 60,
                                 lambda st: self.m.set_status(self.bot_id, st))
                    self._capture(page, adapter)
                finally:
                    ctx.close()

    def _capture(self, page, adapter) -> None:
        page.evaluate("() => window.__ntStart(5000)")
        self.m.set_status(self.bot_id, "recording")
        started = self.last_sound = time.time()
        self.alone_since = None
        while not self.stop_requested.is_set():
            if not self._tick(page, adapter, started):
                break
        self._finish(page, adapter)

    def _tick(self, page, adapter, started: float) -> bool:
        """Ein Überwachungsschritt; False beendet die Aufnahme."""
        from playwright.sync_api import Error as PlaywrightError

        try:
            return self._check(page, adapter, started)
        except PlaywrightError as e:  # Seite geschlossen/navigiert
            log.info("Bot %s: Seite nicht mehr erreichbar (%s)", self.bot_id, e)
            return False

    def _check(self, page, adapter, started: float) -> bool:
        s = self.m.s
        now = time.time()
        if now - started > s.bot_max_minutes * 60:
            log.info("Bot %s: maximale Dauer erreicht", self.bot_id)
            return False
        if adapter.ended(page):
            return False
        if page.evaluate("() => window.__ntLevel()") > SOUND_LEVEL:
            self.last_sound = now
        if now - self.last_sound > s.bot_silence_minutes * 60:
            log.info("Bot %s: zu lange Stille, verlasse das Meeting", self.bot_id)
            return False
        if adapter.alone(page):
            self.alone_since = self.alone_since or now
            if now - self.alone_since > ALONE_GRACE_S:
                return False
        else:
            self.alone_since = None
        page.wait_for_timeout(1000)
        return True

    def _finish(self, page, adapter) -> None:
        from playwright.sync_api import Error as PlaywrightError

        if page.is_closed():
            return
        self.m.set_status(self.bot_id, "stopping")
        try:
            page.evaluate("() => window.__ntStop()")
            deadline = time.time() + 20
            while not self.stopped_event.is_set() and time.time() < deadline:
                page.wait_for_timeout(250)  # hält die Ereignisschleife für ausstehende Stücke am Laufen
            adapter.leave(page)
        except PlaywrightError as e:
            log.info("Bot %s: Abschluss unvollständig (%s)", self.bot_id, e)


def _write_silence(path: Path, seconds: int = 2) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000 * seconds)
