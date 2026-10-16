"""Kalender-Automatik: Bots für anstehende Termine mit Meeting-Link einplanen."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .bot.platforms import SUPPORTED, find_meeting_url
from .db import Database
from .google import GoogleClient, GoogleError

log = logging.getLogger(__name__)

LOOKAHEAD_S = 15 * 60
JOIN_EARLY_S = 60


def _ts(value: dict | None) -> float | None:
    if not value or "dateTime" not in value:  # ganztägige Termine haben kein dateTime
        return None
    return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00")).timestamp()


class CalendarScheduler:
    def __init__(self, db: Database, google: GoogleClient, poll_seconds: float = 60,
                 jitsi_hosts: tuple[str, ...] = ()):
        self.db, self.google, self.poll = db, google, poll_seconds
        self.jitsi_hosts = jitsi_hosts
        self._stop = threading.Event()
        self.last_sync: dict = {}

    def start(self) -> None:
        threading.Thread(target=self._loop, name="calendar", daemon=True).start()

    def shutdown(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll):
            if self.google.connected() and self.google.settings()["auto_join"]:
                try:
                    self.sync()
                except (GoogleError, OSError) as e:
                    log.warning("Kalender-Abgleich fehlgeschlagen: %s", e)
                    self.last_sync = {"at": time.time(), "error": str(e)}

    def sync(self, now: float | None = None) -> list[int]:
        """Termine im Fenster [jetzt, jetzt+15 min] prüfen und neue Bots anlegen."""
        now = now or time.time()
        created = []
        for event in self.google.events(now - 5 * 60, now + LOOKAHEAD_S):
            if event.get("status") == "cancelled":
                continue
            start, end = _ts(event.get("start")), _ts(event.get("end"))
            if start is None or (end is not None and end <= now):
                continue
            attendees = event.get("attendees") or []
            me = next((a for a in attendees if a.get("self")), None)
            if me and me.get("responseStatus") == "declined":
                continue
            found = find_meeting_url(event, self.jitsi_hosts)
            if not found or found[1] not in SUPPORTED:
                continue
            url, platform = found
            bot_id = self.db.create_bot(
                url, platform, event.get("summary") or "Meeting", max(now, start - JOIN_EARLY_S),
                calendar_event_id=f"{event['id']}@{int(start)}",
                meta={"calendar_event_id": event["id"], "event_start": start, "event_end": end,
                      "attendees": [a["email"] for a in attendees if a.get("email") and not a.get("resource")]},
            )
            if bot_id:
                log.info("Bot %s für „%s“ eingeplant", bot_id, event.get("summary"))
                created.append(bot_id)
        self.last_sync = {"at": now, "created": created}
        return created
