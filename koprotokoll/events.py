"""Ereignisse (meeting.completed, bot.status …) an Webhooks und interne Abonnenten verteilen.

Webhook-Zustellung: ``POST <url>`` mit JSON ``{"event", "created_at", "data"}`` und
Signatur ``X-Koprotokoll-Signature: sha256=<HMAC-SHA256(secret, body)>``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from .db import Database

log = logging.getLogger(__name__)

EVENTS = ("meeting.completed", "meeting.failed", "bot.status")
Listener = Callable[[str, dict], None]


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class EventBus:
    def __init__(self, db: Database, retries: int = 3, backoff: float = 2.0):
        self.db, self.retries, self.backoff = db, retries, backoff
        self._listeners: list[Listener] = []
        self._queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        threading.Thread(target=self._run, name="events", daemon=True).start()

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def emit(self, event: str, data: dict) -> None:
        self._queue.put((event, data))

    def join(self) -> None:
        self._queue.join()

    def _run(self) -> None:
        while True:
            event, data = self._queue.get()
            try:
                for listener in self._listeners:
                    try:
                        listener(event, data)
                    except Exception:  # noqa: BLE001
                        log.exception("Ereignis-Abonnent für %s fehlgeschlagen", event)
                for hook in self.db.list_webhooks():
                    if "*" in hook["events"] or event in hook["events"]:
                        self.deliver(hook, event, data)
            finally:
                self._queue.task_done()

    def deliver(self, hook: dict, event: str, data: dict) -> bool:
        body = json.dumps({"event": event, "created_at": time.time(), "data": data}, ensure_ascii=False).encode()
        req = urllib.request.Request(hook["url"], data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "Koprotokoll-Webhook/1",
            "X-Koprotokoll-Event": event,
            "X-Koprotokoll-Signature": sign(hook["secret"], body),
        })
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    if 200 <= r.status < 300:
                        return True
            except (urllib.error.URLError, OSError) as e:
                log.warning("Webhook %s (%s) Versuch %d fehlgeschlagen: %s", hook["url"], event, attempt + 1, e)
            time.sleep(self.backoff * (attempt + 1))
        return False
