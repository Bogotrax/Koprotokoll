"""Microsoft-Integration: OAuth 2.0 und Microsoft Graph (Outlook-Kalender lesen).

Einrichtung: In Azure (portal.azure.com) eine App-Registrierung anlegen, unter
„Authentifizierung" die Weiterleitungs-URI
``<KOPROTOKOLL_PUBLIC_URL>/api/integrations/microsoft/callback`` eintragen und unter
„API-Berechtigungen" die Berechtigungen ``Calendars.Read`` (Delegiert) hinzufügen.
Anschließend ein Client-Secret erzeugen. Client-ID und -Secret per Umgebungsvariable
``KOPROTOKOLL_MICROSOFT_CLIENT_ID`` / ``KOPROTOKOLL_MICROSOFT_CLIENT_SECRET`` oder in
der Weboberfläche hinterlegen.

Die folgende Datei enthält nur die Grundbausteine (Konstanten, Fehlerklasse,
HTTP-Helfer). Der ``MicrosoftClient`` mit OAuth-Logik kommt in einem späteren Schritt.
"""

from __future__ import annotations

import base64
import html
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .db import Database

# OAuth-Endpunkte der Microsoft Identity Platform (Tenant „common" erlaubt
# persönliche Microsoft-Konten und Azure-AD-Konten gleichermaßen).
AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

# Microsoft Graph – Kalenderereignisse des angemeldeten Benutzers.
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "openid",
    "email",
    "offline_access",          # nötig für das Refresh-Token
    "Calendars.Read",          # Outlook-/Microsoft-365-Kalender lesen
    "Mail.Send",               # Recap-Mails senden (analog zu Gmail)
]


class MicrosoftError(RuntimeError):
    pass


def _http(method: str, url: str, *, form: dict | None = None, body: dict | None = None,
          token: str | None = None) -> dict:
    headers = {}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise MicrosoftError(f"Microsoft-API {e.code}: {detail}") from e


def _jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


class MicrosoftClient:
    """OAuth-Client für Microsoft (Anmeldung, Token-Verwaltung, Graph-Zugriff).

    Dieser Schritt umfasst nur Konfiguration und Status – die OAuth-Methoden
    ``auth_url()``/``exchange()`` kommen im nächsten Schritt.
    """

    def __init__(self, settings: Settings, db: Database):
        self.s, self.db = settings, db

    # -- Konfiguration ---------------------------------------------------------
    def _client(self) -> tuple[str, str]:
        stored = self.db.get_kv("microsoft_client", {})
        return (self.s.microsoft_client_id or stored.get("client_id", ""),
                self.s.microsoft_client_secret or stored.get("client_secret", ""))

    def set_client(self, client_id: str, client_secret: str) -> None:
        self.db.set_kv("microsoft_client", {"client_id": client_id.strip(),
                                            "client_secret": client_secret.strip()})

    @property
    def redirect_uri(self) -> str:
        return f"{self.s.public_url}/api/integrations/microsoft/callback"

    def configured(self) -> bool:
        return all(self._client())

    def connected(self) -> bool:
        return bool(self.db.get_kv("microsoft_tokens", {}).get("refresh_token"))

    def status(self) -> dict:
        tokens = self.db.get_kv("microsoft_tokens", {})
        return {"configured": self.configured(), "connected": self.connected(),
                "email": tokens.get("email"), "redirect_uri": self.redirect_uri,
                "settings": self.settings()}

    def settings(self) -> dict:
        return {"auto_join": True, "send_recap": True, "recap_to_attendees": False,
                **self.db.get_kv("microsoft_settings", {})}

    def update_settings(self, **values: bool) -> dict:
        self.db.set_kv("microsoft_settings", {**self.settings(), **values})
        return self.settings()

    # -- OAuth --------------------------------------------------------------------
    def auth_url(self) -> str:
        client_id, _ = self._client()
        if not client_id:
            raise MicrosoftError("Microsoft-OAuth-Client ist nicht konfiguriert")
        state = secrets.token_urlsafe(24)
        self.db.set_kv("microsoft_oauth_state", {"state": state, "expires": time.time() + 600})
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "response_mode": "query", "state": state,
        })

    def exchange(self, code: str, state: str) -> dict:
        expected = self.db.get_kv("microsoft_oauth_state", {})
        if not expected or not secrets.compare_digest(expected.get("state", ""), state or "") \
                or expected.get("expires", 0) < time.time():
            raise MicrosoftError("Ungültiger oder abgelaufener OAuth-Status – bitte erneut verbinden")
        self.db.set_kv("microsoft_oauth_state", None)
        client_id, client_secret = self._client()
        tok = _http("POST", TOKEN_URL, form={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code",
        })
        if "refresh_token" not in tok:
            raise MicrosoftError("Microsoft hat kein Refresh-Token geliefert")
        tokens = {
            "refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
            "email": _jwt_payload(tok.get("id_token", "")).get("email"), "scope": tok.get("scope"),
        }
        self.db.set_kv("microsoft_tokens", tokens)
        return tokens

    def disconnect(self) -> None:
        self.db.set_kv("microsoft_tokens", None)

    def _access_token(self) -> str:
        tokens = self.db.get_kv("microsoft_tokens", {})
        if not tokens.get("refresh_token"):
            raise MicrosoftError("Microsoft ist nicht verbunden")
        if tokens.get("expires_at", 0) > time.time():
            return tokens["access_token"]
        client_id, client_secret = self._client()
        tok = _http("POST", TOKEN_URL, form={
            "refresh_token": tokens["refresh_token"], "client_id": client_id,
            "client_secret": client_secret, "grant_type": "refresh_token",
            "scope": " ".join(SCOPES),
        })
        tokens.update(access_token=tok["access_token"],
                      expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
        self.db.set_kv("microsoft_tokens", tokens)
        return tokens["access_token"]

    # -- Kalender (Microsoft Graph) ---------------------------------------------------
    def events(self, time_min: float, time_max: float) -> list[dict]:
        def iso(t: float) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

        params = urllib.parse.urlencode({
            "startDateTime": iso(time_min), "endDateTime": iso(time_max),
            "$orderby": "start/dateTime", "$top": "50",
        })
        data = _http("GET", f"{GRAPH_BASE}/me/calendarview?{params}", token=self._access_token())
        return data.get("value", [])


class MicrosoftCalendarSource:
    """Kalenderquelle, die Microsoft-/Outlook-Termine als ``CalendarEvent`` liefert."""

    name = "microsoft"
    label = "Microsoft Kalender"

    def __init__(self, client: MicrosoftClient):
        self.client = client

    def enabled(self) -> bool:
        return self.client.connected() and self.client.settings().get("auto_join", True)

    def events(self, time_min: float, time_max: float) -> list["CalendarEvent"]:
        from .calendar_source import CalendarEvent

        out: list[CalendarEvent] = []
        for item in self.client.events(time_min, time_max):
            if item.get("isCancelled"):
                continue
            start_ts = _graph_ts(item.get("start"))
            if start_ts is None:  # ganztägige Termine überspringen
                continue
            end_ts = _graph_ts(item.get("end"))
            attendees = [a.get("emailAddress", {}).get("address", "")
                         for a in (item.get("attendees") or [])]
            attendees = [e for e in attendees if e]
            # Eigene Antwort steht in responseStatus, nicht bei den anderen Teilnehmenden.
            response = {"accepted": "accepted", "organizer": "accepted", "declined": "declined",
                        "tentativelyAccepted": "tentative"}.get(
                (item.get("responseStatus") or {}).get("response"))
            meeting_url = ""
            online = item.get("onlineMeeting") or {}
            if online.get("joinUrl"):
                meeting_url = online["joinUrl"]
            out.append(CalendarEvent(
                id=item.get("id", ""),
                title=item.get("subject") or "Meeting",
                start=start_ts,
                end=end_ts,
                all_day=False,
                location=(item.get("location") or {}).get("displayName", ""),
                description=_event_text(item),
                attendees=attendees,
                self_response=response,
                meeting_url=meeting_url,
                source=self.name,
            ))
        return out


def _event_text(item: dict) -> str:
    """Beschreibungstext eines Graph-Termins: vollen Body nutzen, HTML entfernen.

    Wichtig: Auch ``href="..."``-Ziele bleiben erhalten – Meeting-Links stehen
    in Outlook-Terminen oft nur als klickbarer Link im HTML-Body.
    """
    body = (item.get("body") or {}).get("content", "") or item.get("bodyPreview") or ""
    if not body:
        return ""
    links = re.findall(r'href="([^"]+)"', body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return " ".join(text.split() + links)


def _graph_ts(value: dict | None) -> float | None:
    if not value or "dateTime" not in value:
        return None
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()