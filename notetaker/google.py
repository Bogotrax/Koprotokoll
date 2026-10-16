"""Google-Integration: OAuth 2.0, Kalender (Termine lesen) und Gmail (Recap versenden).

Einrichtung: In der Google Cloud Console einen OAuth-Client vom Typ „Webanwendung“
anlegen, als Weiterleitungs-URI ``<NOTETAKER_PUBLIC_URL>/api/integrations/google/callback``
eintragen und die Calendar- sowie Gmail-API aktivieren. Client-ID und -Secret per
Umgebungsvariable oder in der Weboberfläche hinterlegen.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .config import Settings
from .db import Database

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CALENDAR_EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


class GoogleError(RuntimeError):
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
        raise GoogleError(f"Google-API {e.code}: {detail}") from e


def _jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


class GoogleClient:
    def __init__(self, settings: Settings, db: Database):
        self.s, self.db = settings, db

    # -- Konfiguration ---------------------------------------------------------
    def _client(self) -> tuple[str, str]:
        stored = self.db.get_kv("google_client", {})
        return (self.s.google_client_id or stored.get("client_id", ""),
                self.s.google_client_secret or stored.get("client_secret", ""))

    def set_client(self, client_id: str, client_secret: str) -> None:
        self.db.set_kv("google_client", {"client_id": client_id.strip(), "client_secret": client_secret.strip()})

    @property
    def redirect_uri(self) -> str:
        return f"{self.s.public_url}/api/integrations/google/callback"

    def configured(self) -> bool:
        return all(self._client())

    def connected(self) -> bool:
        return bool(self.db.get_kv("google_tokens", {}).get("refresh_token"))

    def status(self) -> dict:
        tokens = self.db.get_kv("google_tokens", {})
        return {"configured": self.configured(), "connected": self.connected(),
                "email": tokens.get("email"), "redirect_uri": self.redirect_uri,
                "settings": self.settings()}

    def settings(self) -> dict:
        return {"auto_join": True, "send_recap": True, "recap_to_attendees": False,
                **self.db.get_kv("google_settings", {})}

    def update_settings(self, **values: bool) -> dict:
        self.db.set_kv("google_settings", {**self.settings(), **values})
        return self.settings()

    # -- OAuth --------------------------------------------------------------------
    def auth_url(self) -> str:
        client_id, _ = self._client()
        if not client_id:
            raise GoogleError("Google-OAuth-Client ist nicht konfiguriert")
        state = secrets.token_urlsafe(24)
        self.db.set_kv("google_oauth_state", {"state": state, "expires": time.time() + 600})
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "state": state,
        })

    def exchange(self, code: str, state: str) -> dict:
        expected = self.db.get_kv("google_oauth_state", {})
        if not expected or not secrets.compare_digest(expected.get("state", ""), state or "") \
                or expected.get("expires", 0) < time.time():
            raise GoogleError("Ungültiger oder abgelaufener OAuth-Status – bitte erneut verbinden")
        self.db.set_kv("google_oauth_state", None)
        client_id, client_secret = self._client()
        tok = _http("POST", TOKEN_URL, form={
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code",
        })
        if "refresh_token" not in tok:
            raise GoogleError("Google hat kein Refresh-Token geliefert")
        tokens = {
            "refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
            "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
            "email": _jwt_payload(tok.get("id_token", "")).get("email"), "scope": tok.get("scope"),
        }
        self.db.set_kv("google_tokens", tokens)
        return tokens

    def disconnect(self) -> None:
        tokens = self.db.get_kv("google_tokens", {})
        if tokens.get("refresh_token"):
            try:
                _http("POST", REVOKE_URL, form={"token": tokens["refresh_token"]})
            except (GoogleError, OSError):
                pass
        self.db.set_kv("google_tokens", None)

    def _access_token(self) -> str:
        tokens = self.db.get_kv("google_tokens", {})
        if not tokens.get("refresh_token"):
            raise GoogleError("Google ist nicht verbunden")
        if tokens.get("expires_at", 0) > time.time():
            return tokens["access_token"]
        client_id, client_secret = self._client()
        tok = _http("POST", TOKEN_URL, form={
            "refresh_token": tokens["refresh_token"], "client_id": client_id,
            "client_secret": client_secret, "grant_type": "refresh_token",
        })
        tokens.update(access_token=tok["access_token"],
                      expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
        self.db.set_kv("google_tokens", tokens)
        return tokens["access_token"]

    # -- Kalender -------------------------------------------------------------------
    def events(self, time_min: float, time_max: float) -> list[dict]:
        def iso(t: float) -> str:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

        params = urllib.parse.urlencode({
            "timeMin": iso(time_min), "timeMax": iso(time_max), "singleEvents": "true",
            "orderBy": "startTime", "maxResults": "50",
        })
        data = _http("GET", f"{CALENDAR_EVENTS}?{params}", token=self._access_token())
        return data.get("items", [])

    # -- Gmail -------------------------------------------------------------------------
    def send_mail(self, to: list[str], subject: str, text: str, html: str | None = None) -> dict:
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return _http("POST", GMAIL_SEND, body={"raw": raw}, token=self._access_token())
