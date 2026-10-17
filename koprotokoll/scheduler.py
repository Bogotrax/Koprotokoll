"""Kalender-Automatik: Bots für anstehende Termine mit Meeting-Link einplanen.

Der Scheduler arbeitet mit einer Liste von ``CalendarSource``-Quellen
(ICS-Abolink, Google, später Microsoft Graph …) und legt pro Termin mit
unterstütztem Meeting-Link einen Bot an.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

from .bot.platforms import SUPPORTED, find_meeting_url
from .calendar_source import CalendarEvent, CalendarSource
from .db import Database

log = logging.getLogger(__name__)

LOOKAHEAD_S = 15 * 60
JOIN_EARLY_S = 60


class CalendarScheduler:
    def __init__(self, db: Database, sources: Iterable[CalendarSource],
                 poll_seconds: float = 60, jitsi_hosts: tuple[str, ...] = ()):
        self.db = db
        self.sources = list(sources)
        self.poll = poll_seconds
        self.jitsi_hosts = jitsi_hosts
        self._stop = threading.Event()
        self.last_sync: dict = {}

    def start(self) -> None:
        threading.Thread(target=self._loop, name="calendar", daemon=True).start()

    def shutdown(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll):
            try:
                self.sync()
            except Exception as e:  # noqa: BLE001 – eine Quelle soll die anderen nicht blockieren
                log.warning("Kalender-Abgleich fehlgeschlagen: %s", e)
                self.last_sync = {"at": time.time(), "error": str(e)}

    def sync(self, now: float | None = None) -> list[int]:
        """Termine im Fenster [jetzt-5 min, jetzt+15 min] prüfen und neue Bots anlegen."""
        now = now or time.time()
        created: list[int] = []
        errors: list[str] = []
        for source in self.sources:
            if not source.enabled():
                continue
            try:
                events = source.events(now - 5 * 60, now + LOOKAHEAD_S)
            except Exception as e:  # noqa: BLE001
                log.warning("Kalenderquelle %s fehlgeschlagen: %s", source.name, e)
                errors.append(f"{source.name}: {e}")
                continue
            created += self._plan(source, events, now)
        self.last_sync = {"at": now, "created": created}
        if errors:
            self.last_sync["error"] = "; ".join(errors)
        return created

    def _plan(self, source: CalendarSource, events: list[CalendarEvent], now: float) -> list[int]:
        created: list[int] = []
        for event in events:
            if event.cancelled:
                continue
            start, end = event.start, event.end
            if event.all_day or end is not None and end <= now:
                continue
            if event.self_response == "declined":
                continue
            found = find_meeting_url(
                {"location": event.location, "description": event.description,
                 "meeting_url": event.meeting_url or ""},
                self.jitsi_hosts)
            if not found or found[1] not in SUPPORTED:
                continue
            url, platform = found
            bot_id = self.db.create_bot(
                url, platform, event.title or "Meeting", max(now, start - JOIN_EARLY_S),
                calendar_event_id=f"{source.name}:{event.id}@{int(start)}",  # verschoben → neuer Bot
                meta={"calendar_event_id": event.id, "calendar_source": source.name,
                      "event_start": start, "event_end": end, "attendees": event.attendees},
            )
            if bot_id:
                log.info("Bot %s für „%s“ eingeplant", bot_id, event.title)
                created.append(bot_id)
        return created
