import base64
import hashlib
import hmac
import json
import os
import shutil
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import notetaker.google as google_mod
from notetaker.app import create_app
from notetaker.bot.platforms import detect_platform, find_meeting_url
from notetaker.config import Settings
from notetaker.recap import build_recap
from test_notetaker import FakeDiarizer, FakeTranscriber, NoLLM, wait_done, wav_bytes

FIXTURES = Path(__file__).parent / "fixtures"
REMOTE = {"X-Forwarded-For": "203.0.113.7"}  # simuliert Zugriff von außen (über Proxy)


def make_client(tmp_path, **settings):
    s = Settings(data_dir=tmp_path, google_client_id="cid", google_client_secret="csecret", **settings)
    app = create_app(s, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(), llm=NoLLM(), background=False)
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


# --------------------------------------------------------------------------- Plattformen
def test_detect_platform():
    assert detect_platform("https://meet.google.com/abc-defg-hij") == "google_meet"
    assert detect_platform("https://us02web.zoom.us/j/123456?pwd=x") == "zoom"
    assert detect_platform("https://teams.microsoft.com/l/meetup-join/19%3a…") == "teams"
    assert detect_platform("https://example.com/") is None


def test_find_meeting_url_prefers_conference_data():
    event = {"conferenceData": {"entryPoints": [
        {"entryPointType": "phone", "uri": "tel:+49"},
        {"entryPointType": "video", "uri": "https://meet.google.com/xyz-abcd-efg"}]},
        "description": "Alternativ: https://zoom.us/j/999"}
    assert find_meeting_url(event) == ("https://meet.google.com/xyz-abcd-efg", "google_meet")
    assert find_meeting_url({"description": "<a href=\"https://zoom.us/j/999\">"}) == ("https://zoom.us/j/999", "zoom")
    assert find_meeting_url({"summary": "Mittag"}) is None


# --------------------------------------------------------------------------- API-Schlüssel
def test_remote_requests_need_api_key(client):
    assert client.get("/api/meetings").status_code == 200  # lokal ohne Schlüssel
    assert client.get("/api/meetings", headers=REMOTE).status_code == 401
    key = client.post("/api/keys", json={"name": "n8n"}).json()["key"]
    assert key.startswith("nt_")
    ok = client.get("/api/meetings", headers={**REMOTE, "Authorization": f"Bearer {key}"})
    assert ok.status_code == 200
    assert client.get(f"/api/meetings?key={key}", headers=REMOTE).status_code == 200
    assert client.get("/api/meetings", headers={**REMOTE, "Authorization": "Bearer nt_falsch"}).status_code == 401
    listed = client.get("/api/keys").json()
    assert listed[0]["name"] == "n8n" and "key" not in listed[0] and listed[0]["last_used"]
    client.delete(f"/api/keys/{listed[0]['id']}")
    assert client.get("/api/meetings", headers={**REMOTE, "Authorization": f"Bearer {key}"}).status_code == 401
    # OpenAPI-Doku und Weboberfläche bleiben erreichbar
    assert client.get("/openapi.json", headers=REMOTE).status_code == 200


def test_require_key_also_locally(client, monkeypatch):
    monkeypatch.setenv("NOTETAKER_REQUIRE_KEY", "1")
    assert client.get("/api/meetings").status_code == 401


# --------------------------------------------------------------------------- Webhooks
class Receiver:
    def __init__(self):
        self.requests = []
        received = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((dict(self.headers), body))
                self.send_response(204)
                self.end_headers()

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait(self, n=1, timeout=10):
        deadline = time.time() + timeout
        while len(self.requests) < n and time.time() < deadline:
            time.sleep(0.05)
        return self.requests


def test_webhook_signed_meeting_completed(client, tmp_path):
    rx = Receiver()
    try:
        assert client.post("/api/webhooks", json={"url": rx.url, "events": ["nope"]}).status_code == 400
        hook = client.post("/api/webhooks", json={"url": rx.url, "events": ["meeting.completed"]}).json()
        r = client.post("/api/meetings", files={"file": ("a.wav", wav_bytes(tmp_path), "audio/wav")})
        wait_done(client, r.json()["id"])
        (headers, body), = rx.wait()
        expected = "sha256=" + hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
        assert headers["X-Notetaker-Signature"] == expected
        payload = json.loads(body)
        assert payload["event"] == "meeting.completed"
        assert payload["data"]["id"] == r.json()["id"] and payload["data"]["summary"]["action_items"]
        assert client.get("/api/webhooks").json()[0]["secret"].endswith("…")
        assert client.post(f"/api/webhooks/{hook['id']}/test").json() == {"delivered": True}
    finally:
        rx.server.shutdown()


# --------------------------------------------------------------------------- Bots
def test_bot_api_schedule_and_cancel(client):
    later = time.time() + 3600
    r = client.post("/api/bots", json={"meeting_url": "https://meet.google.com/abc-defg-hij",
                                       "title": "Weekly", "join_at": later})
    assert r.status_code == 201
    bot = r.json()
    assert bot["platform"] == "google_meet" and bot["status"] == "scheduled"
    assert client.post("/api/bots", json={"meeting_url": "https://zoom.us/j/1"}).status_code == 400
    assert client.post("/api/bots", json={"meeting_url": "https://example.com"}).status_code == 400
    assert client.post("/api/bots", json={"meeting_url": "javascript:alert(1)", "platform": "web"}).status_code == 400
    assert client.delete(f"/api/bots/{bot['id']}").status_code == 200
    assert client.get(f"/api/bots/{bot['id']}").json()["status"] == "cancelled"
    assert client.delete(f"/api/bots/{bot['id']}").status_code == 404


def _chrome_path() -> str | None:
    custom = os.environ.get("NOTETAKER_BOT_CHROME")
    if custom and Path(custom).exists():
        return custom
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return pw.chromium.executable_path if Path(pw.chromium.executable_path).exists() else None
    except Exception:  # noqa: BLE001
        return None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_bot_records_webrtc_meeting(tmp_path):
    """Echter Browser: Bot tritt einer lokalen WebRTC-Testseite bei und nimmt den Ton des Gegenübers auf."""
    pytest.importorskip("playwright")
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    web = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        with make_client(tmp_path, bot_chrome_path=chrome, bot_max_minutes=0.15) as c:
            url = f"http://127.0.0.1:{web.server_port}/webrtc_meeting.html"
            bot = c.post("/api/bots", json={"meeting_url": url, "platform": "web", "title": "WebRTC-Test"}).json()
            deadline = time.time() + 90
            while time.time() < deadline:
                bot = c.get(f"/api/bots/{bot['id']}").json()
                if bot["status"] in ("completed", "failed", "stopped"):
                    break
                time.sleep(0.5)
            assert bot["status"] == "completed", bot["error"]
            m = wait_done(c, bot["meeting_id"], timeout=60)
            assert m["status"] == "done", m["error"]
            assert m["meta"]["source"] == "bot" and m["meta"]["meeting_url"] == url
            assert 6 <= m["duration"] <= 15
            audio = Path(c.app.state.db.get_meeting(m["id"])["audio_path"])
            assert audio.exists()
            c.delete(f"/api/meetings/{m['id']}")
            assert not audio.exists()
    finally:
        web.shutdown()


# --------------------------------------------------------------------------- Google
class FakeGoogle:
    """Ersetzt google._http: simuliert Token-Endpunkt, Kalender und Gmail."""

    def __init__(self, events):
        self.events, self.sent, self.calls = events, [], []

    def __call__(self, method, url, *, form=None, body=None, token=None):
        self.calls.append((method, url))
        if url == google_mod.TOKEN_URL:
            claims = base64.urlsafe_b64encode(json.dumps({"email": "ich@example.com"}).encode()).decode().rstrip("=")
            out = {"access_token": "at", "expires_in": 3600, "id_token": f"x.{claims}.y"}
            if form.get("grant_type") == "authorization_code":
                out["refresh_token"] = "rt"
            return out
        if url.startswith(google_mod.CALENDAR_EVENTS):
            assert token == "at"
            return {"items": self.events}
        if url == google_mod.GMAIL_SEND:
            self.sent.append(base64.urlsafe_b64decode(body["raw"]).decode())
            return {"id": "m1"}
        return {}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def test_google_oauth_calendar_autojoin_and_recap(client, monkeypatch):
    now = time.time()
    events = [
        {"id": "e1", "summary": "Kundentermin", "hangoutLink": "https://meet.google.com/aaa-bbbb-ccc",
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 3600)},
         "attendees": [{"email": "ich@example.com", "self": True, "responseStatus": "accepted"},
                       {"email": "kunde@example.com"}, {"email": "raum@resource", "resource": True}]},
        {"id": "e2", "summary": "Abgelehnt", "hangoutLink": "https://meet.google.com/ddd-eeee-fff",
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 900)},
         "attendees": [{"email": "ich@example.com", "self": True, "responseStatus": "declined"}]},
        {"id": "e3", "summary": "Zoom-Call", "location": "https://zoom.us/j/123",
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 900)}},
        {"id": "e4", "summary": "Ganztägig", "start": {"date": "2026-10-16"}, "end": {"date": "2026-10-17"}},
    ]
    fake = FakeGoogle(events)
    monkeypatch.setattr(google_mod, "_http", fake)

    st = client.get("/api/integrations/google").json()
    assert st["configured"] and not st["connected"]
    assert st["redirect_uri"].endswith("/api/integrations/google/callback")

    r = client.get("/api/integrations/google/connect", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith(google_mod.AUTH_URL) and "gmail.send" in loc and "access_type=offline" in loc
    state = loc.split("state=")[1].split("&")[0]

    # Callback ist ohne Schlüssel erreichbar, prüft aber den OAuth-Status
    assert client.get("/api/integrations/google/callback?code=c&state=falsch", headers=REMOTE).status_code == 400
    r = client.get(f"/api/integrations/google/callback?code=c&state={state}", headers=REMOTE, follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/#integrations"
    st = client.get("/api/integrations/google").json()
    assert st["connected"] and st["email"] == "ich@example.com"

    created = client.post("/api/integrations/google/sync").json()["created"]
    assert [b["title"] for b in created] == ["Kundentermin"]
    bot = created[0]
    assert bot["status"] == "scheduled" and bot["platform"] == "google_meet"
    assert bot["meta"]["attendees"] == ["ich@example.com", "kunde@example.com"]
    assert bot["join_at"] == pytest.approx(now + 240, abs=5)
    assert client.post("/api/integrations/google/sync").json()["created"] == []  # idempotent

    # Recap per Gmail nach einer Bot-Aufnahme
    client.patch("/api/integrations/google/settings", json={"recap_to_attendees": True})
    app = client.app
    meeting = {"id": 7, "title": "Kundentermin", "duration": 125, "status": "done",
               "summary": {"overview": "Alles besprochen.", "action_items": [{"owner": "Anna", "task": "Angebot senden"}]},
               "meta": {"source": "bot", "attendees": ["ich@example.com", "kunde@example.com"]}}
    app.state.events.emit("meeting.completed", meeting)
    app.state.events.emit("meeting.completed", {**meeting, "meta": {"source": "upload"}})  # kein Recap
    app.state.events.join()
    assert len(fake.sent) == 1
    mail = fake.sent[0]
    assert "To: ich@example.com, kunde@example.com" in mail
    assert "Meeting-Zusammenfassung: Kundentermin" in mail

    client.delete("/api/integrations/google")
    assert not client.get("/api/integrations/google").json()["connected"]


def test_build_recap_escapes_html():
    subject, text, html = build_recap(
        {"id": 3, "title": "<b>Plan</b>", "duration": 61,
         "summary": {"overview": "x < y", "action_items": [{"owner": None, "task": "Doku"}], "decisions": ["Go"]}},
        "http://localhost:8000")
    assert subject == "Meeting-Zusammenfassung: <b>Plan</b>"
    assert "&lt;b&gt;Plan&lt;/b&gt;" in html and "x &lt; y" in html and "<b>Plan</b>" not in html
    assert "- Doku" in text and "http://localhost:8000/#meeting=3" in text
