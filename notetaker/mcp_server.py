"""MCP-Server: Meetings für KI-Assistenten (Claude, Cursor …) verfügbar machen.

Spricht die REST-API eines laufenden Notetaker-Servers an:
``NOTETAKER_URL`` (Standard http://127.0.0.1:8000) und optional ``NOTETAKER_API_KEY``.

Beispiel (Claude Code):  claude mcp add notetaker -- python -m notetaker mcp
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer

from .summarize import fmt_time

server = MCPServer("notetaker", instructions=(
    "Zugriff auf lokal aufgezeichnete Meetings: Suche, Transkripte, Zusammenfassungen, "
    "Action Items und einen Bot, der Online-Meetings als Teilnehmer beitritt."))


def _call(method: str, path: str, body: dict | None = None):
    base = os.environ.get("NOTETAKER_URL", "http://127.0.0.1:8000").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("NOTETAKER_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['NOTETAKER_API_KEY']}"
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


@server.tool()
def list_meetings(limit: int = 20) -> list[dict]:
    """Die neuesten Meetings (id, Titel, Datum, Dauer, Status)."""
    return _call("GET", "/api/meetings")[:limit]


@server.tool()
def search_meetings(query: str) -> list[dict]:
    """Volltextsuche über alle Transkripte; liefert Meeting, Sprecher, Zeitpunkt und Textausschnitt."""
    hits = _call("GET", "/api/search?" + urllib.parse.urlencode({"q": query}))
    return [{"meeting_id": h["meeting_id"], "meeting": h["title"], "speaker": h["speaker_name"],
             "time": fmt_time(h["start"]), "text": h["snippet"].replace("[[", "").replace("]]", "")}
            for h in hits]


@server.tool()
def get_summary(meeting_id: int) -> dict:
    """Zusammenfassung eines Meetings: Überblick, Stichwörter, Gliederung, Action Items, Entscheidungen."""
    m = _call("GET", f"/api/meetings/{meeting_id}")
    return {"title": m["title"], "duration": m["duration"], "speakers": m["speakers"], "summary": m["summary"]}


@server.tool()
def get_transcript(meeting_id: int) -> str:
    """Vollständiges Transkript mit Zeitmarken und Sprechernamen."""
    m = _call("GET", f"/api/meetings/{meeting_id}")
    names = m["speakers"]
    return "\n".join(f"[{fmt_time(s['start'])}] {names.get(s['speaker'], s['speaker'])}: {s['text']}"
                     for s in m["segments"])


@server.tool()
def ask_meeting(meeting_id: int, question: str) -> str:
    """Eine Frage zu einem Meeting beantworten lassen (lokales LLM, sonst passende Transkriptstellen)."""
    return _call("POST", f"/api/meetings/{meeting_id}/ask", {"question": question})["answer"]


@server.tool()
def send_bot(meeting_url: str, title: str = "") -> dict:
    """Den Notetaker-Bot jetzt in ein Online-Meeting (z. B. Google Meet) schicken."""
    return _call("POST", "/api/bots", {"meeting_url": meeting_url, "title": title or None})


def main() -> None:
    server.run("stdio")
