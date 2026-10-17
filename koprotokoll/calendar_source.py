"""Gemeinsame Schnittstelle für Kalender-Quellen (ICS, Google, Microsoft 365 …).

Jede Quelle liefert normalisierte ``CalendarEvent``-Objekte, unabhängig davon,
woher die Termine stammen. Der ``CalendarScheduler`` arbeitet ausschließlich
mit dieser Schnittstelle – neue Quellen (CalDAV, Outlook, Graph …) können ohne
Änderung am Scheduler ergänzt werden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class CalendarEvent:
    """Quellenunabhängiger Kalendertermin.

    ``start``/``end`` sind Unix-Zeitstempel (Sekunden seit 1970, UTC).
    ``id`` ist innerhalb einer Quelle eindeutig (UID, Graph-ID, …).
    """

    id: str
    title: str
    start: float
    end: float | None = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    attendees: list[str] = field(default_factory=list)
    self_response: str | None = None  # "accepted" / "declined" / "tentative" / None
    cancelled: bool = False
    meeting_url: str | None = None
    source: str = ""  # Quellen-Kürzel, z. B. "ics", "google", "ms365"


class CalendarSource(Protocol):
    """Schnittstelle für Kalender-Quellen."""

    name: str        # eindeutiger Schlüssel der Quelle, z. B. "ics-1"
    label: str       # Anzeigename in der Oberfläche

    def enabled(self) -> bool:
        """True, wenn die Quelle Termine liefern kann (z. B. angemeldet)."""

    def events(self, time_min: float, time_max: float) -> list[CalendarEvent]:
        """Termine im halboffenen Intervall [time_min, time_max) zurückgeben."""