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

import koprotokoll.google as google_mod
import koprotokoll.microsoft as microsoft_mod
from koprotokoll.app import create_app
from koprotokoll.bot.platforms import detect_platform, find_meeting_url
from koprotokoll.config import Settings
from koprotokoll.recap import build_recap
from test_koprotokoll import FakeDiarizer, FakeTranscriber, NoLLM, wait_done, wav_bytes

FIXTURES = Path(__file__).parent / "fixtures"
REMOTE = {"X-Forwarded-For": "203.0.113.7"}  # simuliert Zugriff von außen (über Proxy)


def make_client(tmp_path, **settings):
    s = Settings(data_dir=tmp_path, google_client_id="cid", google_client_secret="csecret",
                 **settings)
    app = create_app(s, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(), llm=NoLLM(), background=False)
    return TestClient(app)


def make_ms_client(tmp_path, **settings):
    s = Settings(data_dir=tmp_path, microsoft_client_id="mid", microsoft_client_secret="msecret",
                 **settings)
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
    assert detect_platform("https://teams.live.com/meet/9378471055035?p=x") == "teams"
    assert detect_platform("https://teams.live.com/v2/meet/9378471055035?p=x") == "teams"
    # Vorbereitungsseite „Sofortbesprechung“ ist kein Beitrittslink
    assert detect_platform("https://teams.live.com/v2/meet/meet-now") is None
    assert detect_platform("https://teams.live.com/v2/meet/meet-now?launch=true") is None
    assert detect_platform("https://example.com/") is None


def test_find_meeting_url_prefers_conference_data():
    event = {"conferenceData": {"entryPoints": [
        {"entryPointType": "phone", "uri": "tel:+49"},
        {"entryPointType": "video", "uri": "https://meet.google.com/xyz-abcd-efg"}]},
        "description": "Alternativ: https://zoom.us/j/999"}
    assert find_meeting_url(event) == ("https://meet.google.com/xyz-abcd-efg", "google_meet")
    assert find_meeting_url({"description": "<a href=\"https://zoom.us/j/999\">"}) == ("https://zoom.us/j/999", "zoom")
    assert find_meeting_url({"summary": "Mittag"}) is None


def test_detect_platform_jitsi():
    assert detect_platform("https://meet.jit.si/MeinRaum") == "jitsi"
    assert detect_platform("https://meet.jit.si/Team-Sync?password=x") == "jitsi"
    # Eigene Instanzen werden nur mit Konfiguration erkannt
    assert detect_platform("https://jitsi.example.org/Meeting") is None
    assert detect_platform("https://jitsi.example.org/Meeting", jitsi_hosts=["jitsi.example.org"]) == "jitsi"
    # meet.jit.si bleibt auch mit eigener Host-Liste erkannt
    assert detect_platform("https://meet.jit.si/Raum", jitsi_hosts=["jitsi.example.org"]) == "jitsi"


def test_find_meeting_url_jitsi():
    assert find_meeting_url({"description": "Termin: https://meet.jit.si/Planung"}) == (
        "https://meet.jit.si/Planung", "jitsi")
    assert find_meeting_url(
        {"location": "https://jitsi.example.org/Review"},
        jitsi_hosts=["jitsi.example.org"]) == ("https://jitsi.example.org/Review", "jitsi")
    # Ohne Konfiguration wird der eigene Host nicht erkannt
    assert find_meeting_url({"location": "https://jitsi.example.org/Review"}) is None


def test_find_meeting_url_prefers_supported_platform():
    # Zoom-Link im Termin (noch nicht unterstützt), Jitsi in der Beschreibung → Jitsi hat Vorrang
    event = {"meeting_url": "https://us02web.zoom.us/j/123456?pwd=x",
             "description": "Alternativ: https://meet.jit.si/Planung"}
    assert find_meeting_url(event) == ("https://meet.jit.si/Planung", "jitsi")
    # Ohne unterstützten Link bleibt der Zoom-Link (Anzeige kann ihn zeigen)
    only_zoom = {"meeting_url": "https://us02web.zoom.us/j/123456?pwd=x"}
    assert find_meeting_url(only_zoom) == ("https://us02web.zoom.us/j/123456?pwd=x", "zoom")
    # Teams ist unterstützt: der Link im Termin gewinnt vor dem Beschreibungslink
    mixed = {"meeting_url": "https://teams.live.com/meet/123?p=x",
             "description": "Alternativ: https://meet.jit.si/Planung"}
    assert find_meeting_url(mixed) == ("https://teams.live.com/meet/123?p=x", "teams")


def test_jitsi_join_url():
    from koprotokoll.bot.platforms import Jitsi
    url = Jitsi._join_url("https://meet.jit.si/Raum", "Koprotokoll")
    assert "config.defaultLanguage=" in url
    assert "config.startWithAudioMuted=true" in url
    assert "config.startWithVideoMuted=true" in url
    assert "userInfo.displayName=" in url
    assert "Koprotokoll" in url
    # Bestehender Fragment wird erweitert, nicht ersetzt
    url2 = Jitsi._join_url("https://meet.jit.si/Raum#config.prejoinConfig.enabled=true", "Bot")
    assert "config.prejoinConfig.enabled=true" in url2
    assert "config.defaultLanguage=" in url2


def test_teams_join_url():
    from koprotokoll.bot.platforms import Teams
    url = Teams._join_url("https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0?context=abc")
    assert "anon=true" in url
    assert "context=abc" in url  # bestehende Parameter bleiben erhalten
    # Ein bereits vorhandenes anon wird nicht überschrieben
    url2 = Teams._join_url("https://teams.live.com/meet/123?anon=false")
    assert "anon=false" in url2


# --------------------------------------------------------------------------- API-Schlüssel
def test_remote_requests_need_api_key(client):
    assert client.get("/api/meetings").status_code == 200  # lokal ohne Schlüssel
    assert client.get("/api/meetings", headers=REMOTE).status_code == 401
    key = client.post("/api/keys", json={"name": "n8n"}).json()["key"]
    assert key.startswith("kp_")
    ok = client.get("/api/meetings", headers={**REMOTE, "Authorization": f"Bearer {key}"})
    assert ok.status_code == 200
    assert client.get(f"/api/meetings?key={key}", headers=REMOTE).status_code == 200
    assert client.get("/api/meetings", headers={**REMOTE, "Authorization": "Bearer kp_falsch"}).status_code == 401
    listed = client.get("/api/keys").json()
    assert listed[0]["name"] == "n8n" and "key" not in listed[0] and listed[0]["last_used"]
    client.delete(f"/api/keys/{listed[0]['id']}")
    assert client.get("/api/meetings", headers={**REMOTE, "Authorization": f"Bearer {key}"}).status_code == 401
    # OpenAPI-Doku und Weboberfläche bleiben erreichbar
    assert client.get("/openapi.json", headers=REMOTE).status_code == 200


def test_require_key_also_locally(client, monkeypatch):
    monkeypatch.setenv("KOPROTOKOLL_REQUIRE_KEY", "1")
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
        assert headers["X-Koprotokoll-Signature"] == expected
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


def test_bot_rejects_teams_preparation_page(client):
    # „Sofortbesprechung“-Vorbereitungsseite statt Einladungslink → deutscher Tipp statt Ratlosigkeit
    r = client.post("/api/bots", json={"meeting_url": "https://teams.live.com/v2/meet/meet-now"})
    assert r.status_code == 400
    assert "Beitrittslink" in r.json()["detail"]


def _chrome_path() -> str | None:
    custom = os.environ.get("KOPROTOKOLL_BOT_CHROME")
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


def test_jitsi_adapter_join_and_leave(tmp_path):
    """Jitsi-Adapter gegen einen lokalen Stub: Prejoin → Meeting → Verlassen."""
    pytest.importorskip("playwright")
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    web = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import sync_playwright
        from koprotokoll.bot.platforms import Jitsi

        url = f"http://127.0.0.1:{web.server_port}/jitsi_stub.html"
        statuses = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome, headless=True)
            page = browser.new_page()
            adapter = Jitsi()

            # Beitritt: Prejoin ausfüllen und Join klicken
            adapter.join(page, url, "Koprotokoll", 10, statuses.append)
            assert not adapter.ended(page)

            # Allein-Erkennung: Stub zeigt Teilnehmerzahl 1
            assert adapter.alone(page)

            # Teilnehmerzahl auf 2 setzen → nicht mehr allein
            page.evaluate("window.__setParticipants(2)")
            assert not adapter.alone(page)

            # Verlassen
            adapter.leave(page)
            assert adapter.ended(page)
            browser.close()
    finally:
        web.shutdown()


def test_jitsi_adapter_denied(tmp_path):
    """Jitsi-Adapter: Passwort-geschützter Raum wird als abgelehnt erkannt."""
    pytest.importorskip("playwright")
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    web = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import sync_playwright
        from koprotokoll.bot.platforms import BotError, Jitsi

        url = f"http://127.0.0.1:{web.server_port}/jitsi_stub.html?denied=1"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome, headless=True)
            page = browser.new_page()
            adapter = Jitsi()
            with pytest.raises(BotError, match="abgelehnt"):
                adapter.join(page, url, "Koprotokoll", 5, lambda s: None)
            browser.close()
    finally:
        web.shutdown()


# --------------------------------------------------------------------------- Teams
def test_teams_adapter_join_and_leave(tmp_path):
    """Teams-Adapter gegen einen lokalen Stub: Interstitial → Prejoin → Lobby → Meeting → Verlassen."""
    pytest.importorskip("playwright")
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    web = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import sync_playwright
        from koprotokoll.bot.platforms import Teams

        url = f"http://127.0.0.1:{web.server_port}/teams_stub.html"
        statuses = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome, headless=True)
            page = browser.new_page()
            adapter = Teams()

            # Beitritt: „In diesem Browser fortfahren" → Prejoin → Lobby → Meeting
            adapter.join(page, url, "Koprotokoll", 15, statuses.append)
            assert "waiting_admit" in statuses  # Lobby wurde durchlaufen
            assert not adapter.ended(page)

            # Allein-Erkennung: keine Kacheln + Hinweistext → allein
            assert adapter.alone(page)

            # Zwei Kacheln (echtes DOM-Muster: Rolle menuitem) → nicht allein
            page.evaluate("window.__setParticipants(2)")
            assert not adapter.alone(page)

            # Nur die eigene Kachel → allein
            page.evaluate("window.__setTiles(['Koprotokoll'])")
            assert adapter.alone(page)

            # Nur eine fremde Kachel → nicht allein
            page.evaluate("window.__setTiles(['Sebastian Schade'])")
            assert not adapter.alone(page)

            # Verlassen
            adapter.leave(page)
            assert adapter.ended(page)
            browser.close()
    finally:
        web.shutdown()


def test_teams_ui_labels_match_live_capture():
    """Teams-Selektoren müssen die live aufgenommenen Original-Strings matchen.

    Die Strings stammen aus einer ARIA-Aufnahme der echten deutschen
    teams.live.com-Oberfläche (Light-Meetings, Gastbeitritt, 2026-10) – sie
    dürfen nicht „geraten" werden, dieser Test pinnt die Realität fest.
    """
    from koprotokoll.bot.platforms import Teams

    t = Teams()
    # Interstitial: Accessible Name des Buttons (die Überschrift darin ist ein
    # eigener String und trägt nicht zum Accessible Name bei)
    assert t.BROWSER.search("Join meeting from this browser")
    assert t.BROWSER.search("Continue on this browser")
    assert t.BROWSER.search("Treten Sie der Besprechung über diesen Browser bei")
    assert t.BROWSER.search("In diesem Browser fortfahren")
    # Prejoin
    assert t.JOIN.search("Join now")
    assert t.JOIN.search("Jetzt teilnehmen")
    assert t.NO_MEDIA.search("Continue without audio or video")
    assert t.NO_MEDIA.search("Ohne Audio oder Video fortfahren")
    assert t.NAME_FIELD.search("Type your name")
    assert t.NAME_FIELD.search("Geben Sie Ihren Namen ein")
    # Lobby
    assert t.WAITING.search("Hi, Koprotokoll Bot. Someone will let you in shortly.")
    assert t.WAITING.search("Hallo, Koprotokoll Bot. Jemand wird Sie einlassen, "
                            "wenn die Besprechung beginnt.")
    # In-Meeting: Verlassen (title ohne aria-label; #hangup-button ist der Anker)
    assert t.LEAVE.search("Verlassen")
    # Allein
    assert t.ALONE.search("Warten auf weitere Teilnehmer...")
    # Beendet
    assert t.ENDED.search("Did you leave by mistake?")
    assert t.ENDED.search("Rejoin the call")
    assert t.ENDED.search("Returning to Teams meetings")
    assert t.ENDED.search("Sind Sie aus Versehen gegangen?")
    assert t.ENDED.search("Erneut am Anruf teilnehmen")


def test_teams_adapter_denied(tmp_path):
    """Teams-Adapter: Gesperrter Gastbeitritt wird als abgelehnt erkannt."""
    pytest.importorskip("playwright")
    chrome = _chrome_path()
    if chrome is None:
        pytest.skip("Chromium für Playwright nicht installiert")
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a: None
    web = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    try:
        from playwright.sync_api import sync_playwright
        from koprotokoll.bot.platforms import BotError, Teams

        url = f"http://127.0.0.1:{web.server_port}/teams_stub.html?denied=1"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome, headless=True)
            page = browser.new_page()
            adapter = Teams()
            with pytest.raises(BotError, match="abgelehnt"):
                adapter.join(page, url, "Koprotokoll", 5, lambda s: None)
            browser.close()
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
    assert r.status_code in (302, 307) and r.headers["location"] == "/"
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


# --------------------------------------------------------------------------- Microsoft
class FakeMicrosoft:
    """Ersetzt microsoft._http: simuliert Token-Endpunkt und Microsoft Graph Kalender."""

    def __init__(self, events):
        self.events, self.calls = events, []

    def __call__(self, method, url, *, form=None, body=None, token=None):
        self.calls.append((method, url))
        if url == microsoft_mod.TOKEN_URL:
            claims = base64.urlsafe_b64encode(json.dumps({"email": "ich@example.com"}).encode()).decode().rstrip("=")
            out = {"access_token": "at", "expires_in": 3600, "id_token": f"x.{claims}.y"}
            if form.get("grant_type") == "authorization_code":
                out["refresh_token"] = "rt"
            return out
        if url.startswith(microsoft_mod.GRAPH_BASE + "/me/calendarview"):
            assert token == "at"
            return {"value": self.events}
        return {}


def test_microsoft_oauth_calendar_autojoin(monkeypatch, tmp_path):
    now = time.time()
    events = [
        {"id": "m1", "subject": "Team-Weekly", "isCancelled": False,
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 3600)},
         "attendees": [{"emailAddress": {"address": "ich@example.com"},
                        "status": {"response": "accepted"}},
                       {"emailAddress": {"address": "kollege@example.com"}}],
         "onlineMeeting": {"joinUrl": "https://meet.jit.si/TeamWeekly"}},
        {"id": "m2", "subject": "Abgelehnt", "isCancelled": False,
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 900)},
         "attendees": [{"emailAddress": {"address": "ich@example.com"},
                        "status": {"response": "declined"}}]},
        {"id": "m3", "subject": "Abgesagt", "isCancelled": True,
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 900)}},
        {"id": "m4", "subject": "Teams-Call", "isCancelled": False,
         "start": {"dateTime": _iso(now + 600)}, "end": {"dateTime": _iso(now + 1200)},
         "attendees": [{"emailAddress": {"address": "ich@example.com"},
                        "status": {"response": "accepted"}}],
         "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/"
                                      "19%3ameeting_x%40thread.v2/0?context=t"}},
    ]
    fake = FakeMicrosoft(events)
    monkeypatch.setattr(microsoft_mod, "_http", fake)

    with make_ms_client(tmp_path) as c:
        st = c.get("/api/integrations/microsoft").json()
        assert st["configured"] and not st["connected"]
        assert st["redirect_uri"].endswith("/api/integrations/microsoft/callback")

        r = c.get("/api/integrations/microsoft/connect", follow_redirects=False)
        assert r.status_code in (302, 307)
        loc = r.headers["location"]
        assert loc.startswith(microsoft_mod.AUTH_URL) and "Calendars.Read" in loc
        state = loc.split("state=")[1].split("&")[0]

        # Falscher Status wird abgelehnt, richtiger führt zur Verbindung
        assert c.get("/api/integrations/microsoft/callback?code=c&state=falsch",
                     headers=REMOTE).status_code == 400
        r = c.get(f"/api/integrations/microsoft/callback?code=c&state={state}",
                  headers=REMOTE, follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/"
        st = c.get("/api/integrations/microsoft").json()
        assert st["connected"] and st["email"] == "ich@example.com"

        created = c.post("/api/integrations/google/sync").json()["created"]
        titles = [b["title"] for b in created]
        assert "Team-Weekly" in titles
        assert "Abgelehnt" not in titles and "Abgesagt" not in titles
        weekly = next(b for b in created if b["title"] == "Team-Weekly")
        assert weekly["platform"] == "jitsi"
        assert weekly["join_at"] == pytest.approx(now + 240, abs=5)
        # Teams-joinUrl aus dem Graph-Termin wird als Teams-Bot geplant
        teams_bot = next(b for b in created if b["title"] == "Teams-Call")
        assert teams_bot["platform"] == "teams"

        c.delete("/api/integrations/microsoft")
        assert not c.get("/api/integrations/microsoft").json()["connected"]


def test_scheduler_plans_ahead_and_cancels_gone_events(tmp_path):
    """Bots werden früh sichtbar geplant; entfallene Termine werden storniert."""
    import koprotokoll.scheduler as scheduler_mod
    from koprotokoll.calendar_source import CalendarEvent
    from koprotokoll.db import Database

    now = time.time()
    far = CalendarEvent(
        id="e1", title="Später", start=now + 3 * 3600, end=now + 4 * 3600,
        description="https://teams.live.com/meet/1234567890123?p=token")

    class FakeSource:
        name = "fake"
        label = "Fake-Kalender"

        def __init__(self):
            self.items = [far]

        def enabled(self):
            return True

        def events(self, time_min, time_max):
            return [e for e in self.items if time_min <= e.start < time_max]

    src = FakeSource()
    db = Database(tmp_path / "t.db")
    cal = scheduler_mod.CalendarScheduler(db, [src])

    # Termin in 3 Stunden → Bot wird JETZT schon angelegt, tritt aber erst
    # 60 Sekunden vor Beginn bei
    created = cal.sync(now)
    assert len(created) == 1
    bot = db.get_bot(created[0])
    assert bot["platform"] == "teams"
    assert bot["status"] == "scheduled"
    assert bot["join_at"] == pytest.approx(far.start - scheduler_mod.JOIN_EARLY_S, abs=2)

    # Idempotent: erneuter Abgleich legt keinen Zwilling an
    assert cal.sync(now) == []

    # Termin wird abgesagt → der geplante Bot wird beim nächsten Abgleich storniert
    far.cancelled = True
    cal.sync(now)
    stale = db.get_bot(bot["id"])
    assert stale["status"] == "cancelled"
    assert "entfällt" in stale["error"]


def test_upcoming_lists_calendar_events(monkeypatch, tmp_path):
    now = time.time()
    events = [
        {"id": "m1", "subject": "Team-Weekly", "isCancelled": False,
         "start": {"dateTime": _iso(now + 300)}, "end": {"dateTime": _iso(now + 3600)},
         "attendees": [], "onlineMeeting": {"joinUrl": "https://meet.jit.si/TeamWeekly"}},
        {"id": "m2", "subject": "Abgelehnt", "isCancelled": False,
         "start": {"dateTime": _iso(now + 3600)}, "end": {"dateTime": _iso(now + 7200)},
         "attendees": [{"emailAddress": {"address": "ich@example.com"},
                        "status": {"response": "declined"}}]},
        {"id": "m3", "subject": "Abgesagt", "isCancelled": True,
         "start": {"dateTime": _iso(now + 9000)}, "end": {"dateTime": _iso(now + 9600)}},
    ]
    monkeypatch.setattr(microsoft_mod, "_http", FakeMicrosoft(events))

    with make_ms_client(tmp_path) as c:
        # Vor der Verbindung: keine Termine (Quelle nicht erreichbar)
        assert c.get("/api/upcoming").json() == []

        # Verbinden
        r = c.get("/api/integrations/microsoft/connect", follow_redirects=False)
        state = r.headers["location"].split("state=")[1].split("&")[0]
        c.get(f"/api/integrations/microsoft/callback?code=c&state={state}", headers=REMOTE)

        upcoming = c.get("/api/upcoming").json()
        assert [e["title"] for e in upcoming] == ["Team-Weekly", "Abgelehnt"]  # Abgesagt fehlt
        weekly = upcoming[0]
        assert weekly["platform"] == "jitsi" and weekly["will_join"] is True
        assert weekly["meeting_url"] == "https://meet.jit.si/TeamWeekly"
        assert weekly["reason"] == ""
        declined = upcoming[1]
        assert declined["will_join"] is False  # abgelehnt = kein Bot
        assert declined["reason"] == "abgelehnt – kein Bot"
