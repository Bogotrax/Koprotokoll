# kontextlayer_kommunikation – lokaler Meeting-Notetaker

Ein lokaler Nachbau der Grundfunktionen von [Fireflies.ai](https://fireflies.ai/):
Meetings aufnehmen oder hochladen, mit **NVIDIA Parakeet** transkribieren, Sprecher
erkennen, zusammenfassen, Action Items extrahieren und alles durchsuchen – ohne Cloud.
Die Analyse des Vorbilds mit [REA](https://github.com/morluto/rea) steht in
[`docs/fireflies-analyse.md`](docs/fireflies-analyse.md).

## Funktionen

- **Bot als Meeting-Teilnehmer**: tritt Google-Meet-Meetings als „Notetaker“ bei – manuell per Link oder automatisch aus dem Google Kalender – und nimmt den Ton aller Teilnehmer auf
- **Google-Integration**: Kalender (Auto-Join) und Gmail (Zusammenfassung nach dem Meeting per E-Mail)
- **Native API**: REST mit API-Schlüsseln, OpenAPI-Doku unter `/docs`, signierte Webhooks, MCP-Server für KI-Assistenten
- **Aufnahme im Browser**: Mikrofon (Gespräch vor Ort) oder Mikrofon + Tab-/Bildschirmton (Online-Meeting in Zoom, Teams, Meet …)
- **Upload** von MP3, MP4, WAV, M4A, WebM, OGG, FLAC, MOV …
- **Transkription** mit Parakeet TDT 0.6B v3 (25 europäische Sprachen inkl. Deutsch, automatische Spracherkennung), läuft auf CPU
- **Sprechererkennung** (Diarisierung) mit umbenennbaren Sprechern und Redeanteilen
- **Zusammenfassung**: Überblick, Stichwörter, Gliederung mit Zeitmarken, Action Items, Entscheidungen, offene Fragen
- **Frag das Meeting** (wie „AskFred“)
- **Volltextsuche** über alle Meetings, Klick springt zur Stelle im Audio
- **Player mit synchronem Transkript**, Soundbites, Kommentare, Action-Item-Markierungen
- **Export** als Markdown, TXT, SRT-Untertitel oder JSON

## Schnellstart

Voraussetzungen: Python ≥ 3.11 und `ffmpeg`.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium         # Browser für den Meeting-Bot
python scripts/download_models.py   # optional: Modelle vorab laden (~700 MB), danach offline
python -m notetaker                 # → http://127.0.0.1:8000
```

Beim ersten Transkribieren werden die Modelle sonst automatisch geladen
(Parakeet int8 von Hugging Face, Diarisierungsmodelle von den sherpa-onnx-Releases).

### Zusammenfassungen mit lokalem LLM (empfohlen)

Ohne LLM erstellt die App eine einfache extraktive Zusammenfassung. Für echte
KI-Zusammenfassungen ein lokales Modell über eine OpenAI-kompatible API bereitstellen,
z. B. mit [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:7b-instruct
python -m notetaker   # nutzt standardmäßig http://localhost:11434/v1
```

Genauso funktionieren llama.cpp (`llama-server`), LM Studio oder vLLM über `NOTETAKER_LLM_URL`.

## Meeting-Bot (Teilnehmer wie bei Fireflies)

Der Bot startet einen eigenen Chromium, öffnet den Meeting-Link, setzt seinen Namen,
schaltet Kamera und Mikrofon aus und klickt „Beitreten“ bzw. „Teilnahme anfragen“.
Im Meeting greift er die Audiospuren aller Teilnehmer direkt an den WebRTC-Verbindungen ab
(keine virtuelle Soundkarte nötig) und nimmt sie auf. Er verlässt das Meeting, wenn es endet,
er allein ist, zu lange Stille herrscht oder die Höchstdauer erreicht ist. Danach läuft die
normale Verarbeitung (Parakeet, Sprecher, Zusammenfassung).

- **Manuell**: Meet-Link in das Feld in der Seitenleiste einfügen oder `POST /api/bots`
- **Automatisch**: Google verbinden → der Bot tritt allen Kalenderterminen mit Meet-Link bei
  (abgelehnte Termine werden übersprungen), etwa eine Minute vor Beginn
- **Eigenes Google-Konto für den Bot (empfohlen)**: Google lässt anonyme Gäste oft gar nicht
  oder nur nach Freigabe herein. Einmalig `python -m notetaker bot-login` ausführen und im
  geöffneten Browser mit einem eigenen Bot-Konto anmelden.
- Läuft der Bot auf einem Server ohne Bildschirm und wird erkannt, hilft
  `NOTETAKER_BOT_HEADLESS=0` zusammen mit `xvfb-run python -m notetaker`.

Unterstützt wird zurzeit **Google Meet**. Zoom- und Teams-Links werden erkannt, der Beitritt
ist aber noch nicht umgesetzt. Mit `platform: "web"` nimmt der Bot den Ton einer beliebigen
Webseite auf (z. B. Webinar).

> Hinweis: Aufzeichnungen nur mit Einverständnis der Teilnehmenden. Der Bot tritt sichtbar
> unter seinem Namen bei.

## Google Kalender & Gmail einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt anlegen und die
   **Google Calendar API** und **Gmail API** aktivieren.
2. OAuth-Zustimmungsbildschirm konfigurieren (intern oder extern mit dir als Testnutzer).
3. Anmeldedaten → OAuth-Client-ID → **Webanwendung**, autorisierte Weiterleitungs-URI:
   `http://localhost:8000/api/integrations/google/callback` (bzw. `<NOTETAKER_PUBLIC_URL>/api/integrations/google/callback`).
4. Client-ID und -Secret in der App unter **Integrationen & API** eintragen (oder per
   `NOTETAKER_GOOGLE_CLIENT_ID` / `NOTETAKER_GOOGLE_CLIENT_SECRET`) und **Mit Google verbinden**.

Berechtigungen: Kalender nur lesen, Gmail nur senden. Nach jedem Bot-Meeting geht die
Zusammenfassung an dich (optional auch an alle Termin-Teilnehmer).

## API

Vollständige, interaktive Doku: **http://127.0.0.1:8000/docs** (OpenAPI unter `/openapi.json`).

Anfragen direkt vom eigenen Rechner brauchen keinen Schlüssel. Von anderen Rechnern, hinter
einem Reverse-Proxy oder aus Tools wie n8n/Zapier/Make: API-Schlüssel in der Oberfläche oder per
`python -m notetaker create-key n8n` erzeugen und mitsenden. Mit `NOTETAKER_REQUIRE_KEY=1` gilt
die Schlüsselpflicht auch lokal.

```bash
KEY=nt_…
# Bot in ein Meeting schicken
curl -X POST http://localhost:8000/api/bots -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" -d '{"meeting_url": "https://meet.google.com/abc-defg-hij"}'
# Meetings, Transkript, Suche, Export
curl -H "Authorization: Bearer $KEY" http://localhost:8000/api/meetings
curl -H "Authorization: Bearer $KEY" http://localhost:8000/api/meetings/1
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/search?q=budget"
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/meetings/1/export?format=md"
```

| Bereich | Endpunkte |
|---|---|
| Meetings | `GET/POST /api/meetings`, `GET/PATCH/DELETE /api/meetings/{id}`, `…/audio`, `…/export`, `…/summarize`, `…/ask`, `…/bookmarks` |
| Suche | `GET /api/search?q=` |
| Bots | `POST/GET /api/bots`, `GET/DELETE /api/bots/{id}` |
| Webhooks | `GET/POST /api/webhooks`, `DELETE /api/webhooks/{id}`, `POST /api/webhooks/{id}/test` |
| Schlüssel | `GET/POST /api/keys`, `DELETE /api/keys/{id}` |
| Google | `GET/DELETE /api/integrations/google`, `PUT …/client`, `GET …/connect`, `PATCH …/settings`, `POST …/sync` |

### Webhooks

Ereignisse `meeting.completed`, `meeting.failed`, `bot.status` werden als `POST` mit
`{"event", "created_at", "data"}` zugestellt. Prüfung der Signatur:

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Notetaker-Signature"])
```

### MCP-Server (Claude, Cursor & Co.)

```bash
claude mcp add notetaker -- python -m notetaker mcp
# entfernter Server: NOTETAKER_URL=https://… NOTETAKER_API_KEY=nt_… python -m notetaker mcp
```

Werkzeuge: `list_meetings`, `search_meetings`, `get_summary`, `get_transcript`, `ask_meeting`, `send_bot`.

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `NOTETAKER_DATA` | `./data` | Datenbank, Mediendateien, Modelle |
| `NOTETAKER_ASR_MODEL` | `nemo-parakeet-tdt-0.6b-v3` | z. B. `nemo-parakeet-tdt-0.6b-v2` (nur Englisch) |
| `NOTETAKER_ASR_QUANT` | `int8` | leer lassen für fp32 (genauer, größer) |
| `NOTETAKER_ASR_PATH` | – | lokaler Modellordner statt Download |
| `NOTETAKER_DIARIZATION` | `1` | `0` schaltet Sprechererkennung ab |
| `NOTETAKER_DIARIZATION_THRESHOLD` | `0.8` | kleiner = mehr Sprecher |
| `NOTETAKER_LLM` | `1` | `0` = nur Offline-Heuristik |
| `NOTETAKER_LLM_URL` | `http://localhost:11434/v1` | OpenAI-kompatibler Endpunkt |
| `NOTETAKER_LLM_MODEL` | `qwen2.5:7b-instruct` | Modellname |
| `NOTETAKER_PUBLIC_URL` | `http://localhost:8000` | Basis-URL für OAuth-Weiterleitung und Links in E-Mails |
| `NOTETAKER_REQUIRE_KEY` | `0` | `1` = API-Schlüssel auch für lokale Anfragen |
| `NOTETAKER_GOOGLE_CLIENT_ID` / `…_SECRET` | – | OAuth-Client (alternativ in der Oberfläche) |
| `NOTETAKER_BOT_NAME` | `Notetaker` | Anzeigename des Bots im Meeting |
| `NOTETAKER_BOT_HEADLESS` | `1` | `0` = sichtbares Browserfenster |
| `NOTETAKER_BOT_CHROME` | – | Pfad zu Chrome/Chromium statt Playwright-Chromium |
| `NOTETAKER_BOT_SILENCE_MINUTES` | `10` | Meeting verlassen nach so langer Stille |
| `NOTETAKER_BOT_MAX_MINUTES` | `180` | Höchstdauer einer Aufnahme |
| `NOTETAKER_BOT_ADMIT_MINUTES` | `10` | Wartezeit auf Einlass |

## Architektur

```
Browser (static/)  ──HTTP──▶  FastAPI (app.py, auth.py)       ◀── REST-Clients, MCP (mcp_server.py)
  Aufnahme, Upload,             │
  Player, Suche                 ├─ bot/          Chromium-Bot tritt Meetings bei (Playwright),
                                │                capture.js greift WebRTC-Ton ab
                                ├─ scheduler.py  Google Kalender → Bots einplanen
                                ├─ google.py     OAuth, Calendar, Gmail
                                ├─ events.py     Webhooks (HMAC-signiert) + recap.py (Gmail)
                                ├─ pipeline.py   Hintergrund-Queue
                                │    ├─ audio.py      ffmpeg → 16 kHz WAV
                                │    ├─ speech.py     Parakeet (onnx-asr) + VAD,
                                │    │                Diarisierung (sherpa-onnx),
                                │    │                Wörter → Äußerungen
                                │    └─ summarize.py  lokales LLM / Heuristik
                                ├─ db.py         SQLite + FTS5
                                └─ export.py     MD / TXT / SRT / JSON
```


## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Die Tests nutzen Platzhalter-Engines und eine nachgebaute Google-API und brauchen keine
Modelle. Der Bot-Test startet einen echten Chromium gegen eine lokale WebRTC-Testseite
(`tests/fixtures/webrtc_meeting.html`) und wird übersprungen, wenn kein Browser installiert ist.
