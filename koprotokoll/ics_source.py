"""ICS-Kalenderquelle (iCalendar-Abolink oder lokale .ics-Datei).

Unterstützt Serien-Termine (RRULE, EXDATE, verschobene Vorkommen über
RECURRENCE-ID), Zeitzonen und ganztägige Termine. Wird für die „Kalender
veröffentlichen“-Links von Outlook.com, Microsoft 365, iCloud, Nextcloud,
Google und Thunderbird verwendet – ganz ohne Anmeldung.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from icalendar import Calendar
import recurring_ical_events

from .calendar_source import CalendarEvent

log = logging.getLogger(__name__)

TIMEOUT_S = 30


class IcsError(RuntimeError):
    """Fehler beim Laden oder Lesen einer ICS-Quelle."""


def _to_ts(value) -> tuple[float | None, bool]:
    """(Unix-Zeitstempel, ist_ganztägig) aus einem icalendar-Zeitwert."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp(), False
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp(), True
    return None, False


def _mailto(addr: str) -> str:
    addr = (addr or "").strip()
    if addr.lower().startswith("mailto:"):
        addr = addr[7:]
    return addr.strip().lower()


def _attendees(component) -> tuple[list[str], str | None]:
    """(E-Mail-Liste, eigene Antwort) aus den ATTENDEE-Einträgen ziehen."""
    raw = component.get("ATTENDEE")
    if raw is None:
        return [], None
    entries = raw if isinstance(raw, list) else [raw]
    emails: list[str] = []
    own: str | None = None
    for att in entries:
        email = _mailto(str(att))
        if not email:
            continue
        emails.append(email)
        partstat = (att.params.get("PARTSTAT") or "").upper()
        if own is None and partstat in ("DECLINED", "ACCEPTED", "TENTATIVE"):
            # ICS-Abos kennen oft nur den Kalender-Inhaber als Teilnehmer –
            # der erste Eintrag mit Teilnehmerstatus gilt als „eigene“ Antwort.
            own = {"DECLINED": "declined", "ACCEPTED": "accepted",
                   "TENTATIVE": "tentative"}[partstat]
    return emails, own


def component_to_event(component, source_name: str, own_email: str) -> CalendarEvent | None:
    """Eine VEVENT-Komponente in ein CalendarEvent übersetzen (None = überspringen)."""
    status = str(component.get("STATUS") or "").upper()
    if status == "CANCELLED":
        return None

    start_val = component.get("DTSTART")
    if start_val is None:
        return None
    start_ts, all_day = _to_ts(start_val.dt)
    if start_ts is None:
        return None

    end_ts: float | None = None
    end_val = component.get("DTEND")
    if end_val is not None:
        end_ts, _ = _to_ts(end_val.dt)
    if end_ts is None:
        duration = component.get("DURATION")
        if duration is not None:
            end_ts = start_ts + duration.dt.total_seconds()
        else:
            end_ts = start_ts + (86400 if all_day else 0)

    attendees, self_response = _attendees(component)
    if own_email:
        # Eigene Antwort prüfen, falls unter den Teilnehmenden.
        raw = component.get("ATTENDEE")
        entries = raw if isinstance(raw, list) else ([raw] if raw else [])
        for att in entries:
            if _mailto(str(att)) == own_email:
                partstat = (att.params.get("PARTSTAT") or "").upper()
                self_response = {"DECLINED": "declined", "ACCEPTED": "accepted",
                                 "TENTATIVE": "tentative"}.get(partstat)
                break

    uid = str(component.get("UID") or "")
    recurrence_id = component.get("RECURRENCE-ID")
    if recurrence_id is not None:
        rid_ts, _ = _to_ts(recurrence_id.dt)
        event_id = f"{uid}#{int(rid_ts)}" if rid_ts is not None else uid
    else:
        event_id = f"{uid}#{int(start_ts)}" if uid else f"ics#{int(start_ts)}"

    return CalendarEvent(
        id=event_id,
        title=str(component.get("SUMMARY") or "Meeting"),
        start=start_ts,
        end=end_ts,
        all_day=all_day,
        location=str(component.get("LOCATION") or ""),
        description=str(component.get("DESCRIPTION") or ""),
        attendees=attendees,
        self_response=self_response,
        cancelled=False,
        source=source_name,
    )


def expand_ics(data: bytes, time_min: float, time_max: float,
               source_name: str, own_email: str) -> list[CalendarEvent]:
    """ICS-Bytes in CalendarEvent-Objekte im Fenster [time_min, time_max) übersetzen."""
    # Zeilenenden glätten – Windows-Dateien enthalten teils doppelte CRs.
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    try:
        calendar = Calendar.from_ical(data)
    except ValueError as e:
        raise IcsError(f"ICS-Daten nicht lesbar: {e}") from e

    start_dt = datetime.fromtimestamp(time_min, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(time_max, tz=timezone.utc)
    query = recurring_ical_events.of(calendar, skip_bad_series=True)

    out: list[CalendarEvent] = []
    for component in query.between(start_dt, end_dt):
        event = component_to_event(component, source_name, own_email)
        if event is not None:
            out.append(event)
    out.sort(key=lambda e: e.start)
    return out


class IcsCalendarSource:
    """Kalenderquelle für ICS-Abolinks und lokale .ics-Dateien."""

    def __init__(self, name: str, label: str, url: str, *, own_email: str = "",
                 timeout: float = TIMEOUT_S, enabled: bool = True):
        self.name = name
        self.label = label
        self.url = url.strip()
        self.own_email = (own_email or "").strip().lower()
        self.timeout = timeout
        self.enabled_flag = enabled
        self.last_error: str | None = None

    # -- CalendarSource ---------------------------------------------------------
    def enabled(self) -> bool:
        return self.enabled_flag and bool(self.url)

    def events(self, time_min: float, time_max: float) -> list[CalendarEvent]:
        try:
            out = expand_ics(self._fetch(), time_min, time_max, self.name, self.own_email)
        except IcsError as e:
            self.last_error = str(e)
            raise
        self.last_error = None
        return out

    # -- Intern -----------------------------------------------------------------
    def _fetch(self) -> bytes:
        if self.url.startswith(("http://", "https://")):
            req = urllib.request.Request(self.url, headers={"User-Agent": "Koprotokoll/1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except (urllib.error.URLError, OSError) as e:
                raise IcsError(f"ICS-Adresse nicht erreichbar: {e}") from e
        path = Path(self.url)
        if not path.is_file():
            raise IcsError(f"ICS-Datei nicht gefunden: {self.url}")
        return path.read_bytes()

    def _to_event(self, component) -> CalendarEvent | None:
        return component_to_event(component, self.name, self.own_email)