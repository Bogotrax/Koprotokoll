"""Recap-E-Mail nach einem Meeting (wie bei Fireflies) über Gmail versenden."""

from __future__ import annotations

import html
import logging

from .google import GoogleClient, GoogleError
from .signal import SignalClient, SignalError
from .summarize import fmt_time

log = logging.getLogger(__name__)


def build_recap(meeting: dict, public_url: str) -> tuple[str, str, str]:
    s = meeting.get("summary") or {}
    link = f"{public_url}/#meeting={meeting['id']}"
    subject = f"Meeting-Zusammenfassung: {meeting['title']}"

    def items(key: str) -> list[str]:
        out = []
        for a in s.get(key) or []:
            if isinstance(a, dict):
                who = f"{a['owner']}: " if a.get("owner") else ""
                due = f" (bis {a['due']})" if a.get("due") else ""
                out.append(f"{who}{a.get('task', '')}{due}")
            else:
                out.append(str(a))
        return out

    sections = [("Action Items", items("action_items")), ("Entscheidungen", items("decisions")),
                ("Offene Fragen", items("questions"))]
    duration = fmt_time(meeting["duration"]) if meeting.get("duration") else "–"

    text = [meeting["title"], f"Dauer: {duration}", "", "Zusammenfassung", s.get("overview") or "–", ""]
    for title, lines in sections:
        if lines:
            text += [title] + [f"- {x}" for x in lines] + [""]
    text += [f"Transkript und Aufnahme: {link}"]

    e = html.escape
    body = [f"<h2>{e(meeting['title'])}</h2>", f"<p style='color:#666'>Dauer: {e(duration)}</p>",
            "<h3>Zusammenfassung</h3>", f"<p>{e(s.get('overview') or '–')}</p>"]
    if s.get("keywords"):
        body.append(f"<p><b>Stichwörter:</b> {e(', '.join(map(str, s['keywords'])))}</p>")
    for title, lines in sections:
        if lines:
            body += [f"<h3>{e(title)}</h3>", "<ul>"] + [f"<li>{e(x)}</li>" for x in lines] + ["</ul>"]
    body.append(f"<p><a href='{e(link)}'>Transkript und Aufnahme öffnen</a></p>")
    html_body = "<div style='font-family:system-ui,sans-serif;max-width:640px'>" + "".join(body) + "</div>"
    return subject, "\n".join(text), html_body


class RecapMailer:
    """Abonniert meeting.completed und verschickt die Zusammenfassung für Bot-Aufnahmen."""

    def __init__(self, google: GoogleClient, public_url: str):
        self.google, self.public_url = google, public_url

    def __call__(self, event: str, meeting: dict) -> None:
        if event != "meeting.completed" or (meeting.get("meta") or {}).get("source") != "bot":
            return
        settings = self.google.settings()
        if not (self.google.connected() and settings["send_recap"]):
            return
        own = self.google.status().get("email")
        to = [own] if own else []
        if settings["recap_to_attendees"]:
            to += [a for a in meeting["meta"].get("attendees", []) if a != own]
        if not to:
            return
        subject, text, html_body = build_recap(meeting, self.public_url)
        try:
            self.google.send_mail(to, subject, text, html_body)
            log.info("Recap für Meeting %s an %s verschickt", meeting["id"], ", ".join(to))
        except (GoogleError, OSError) as e:
            log.warning("Recap-Versand fehlgeschlagen: %s", e)


class SignalRecap:
    """Abonniert meeting.completed und schickt die Zusammenfassung per Signal.

    Wie ``RecapMailer``, nur als DSGVO-freundlicher Kanal über den lokalen
    signal-cli-Dienst: nach jeder Bot-Aufnahme geht der Klartext an die
    hinterlegte Empfängernummer.
    """

    def __init__(self, signal: SignalClient, public_url: str):
        self.signal, self.public_url = signal, public_url

    def __call__(self, event: str, meeting: dict) -> None:
        if event != "meeting.completed" or (meeting.get("meta") or {}).get("source") != "bot":
            return
        settings = self.signal.settings()
        if not settings["send_recap"] or not settings.get("recipient"):
            return
        _, text, _ = build_recap(meeting, self.public_url)
        try:
            self.signal.send(text)
            log.info("Recap für Meeting %s per Signal an %s verschickt",
                     meeting["id"], settings["recipient"])
        except (SignalError, OSError) as e:
            log.warning("Signal-Recap fehlgeschlagen: %s", e)
