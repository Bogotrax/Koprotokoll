# Architektur

[← zurück zum README](../README.md)

![Ablauf](assets/ablauf.svg)

## Module

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

## Wichtige Abläufe

**Upload / Aufnahme → Ergebnis.** `POST /api/meetings` speichert die Datei unter `data/media/`
und reiht das Meeting in die `Pipeline` ein. Ein Hintergrund-Thread konvertiert mit ffmpeg,
transkribiert mit Parakeet (Wörter mit Zeitstempeln), ordnet Wörter per zeitlicher Überlappung
den Sprechern der Diarisierung zu (`speech.build_utterances`), fasst zusammen und schreibt
Segmente samt FTS5-Index in SQLite. Am Ende feuert `meeting.completed`.

**Meeting-Bot.** `BotManager` startet geplante Bots in eigenen Threads. Jede `BotSession`
öffnet einen Playwright-Chromium mit `capture.js` als Init-Skript. Das Skript ersetzt
`RTCPeerConnection`, hängt jede eingehende Audiospur an einen gemeinsamen `AudioContext` und
nimmt die Mischung mit `MediaRecorder` auf. Die Stücke kommen über `expose_binding` nach Python
und werden an eine `.webm`-Datei angehängt. Plattform-Adapter (`bot/platforms.py`) kapseln
Beitritt und Ende-Erkennung.

**Ereignisse.** `EventBus` verteilt Ereignisse an interne Abonnenten (z. B. `RecapMailer`)
und an registrierte Webhooks, signiert per HMAC-SHA256.

## Designentscheidungen

- **Lokal zuerst:** keine Cloud-Pflicht; alles, was das Netz braucht (Google, LLM), ist optional.
- **Wenige Abhängigkeiten:** kein Frontend-Build, keine Google-SDKs; HTTP über die Standardbibliothek.
- **Austauschbare Engines:** `Transcriber`, `Diarizer` und das LLM sind Schnittstellen, die Tests ersetzen sie durch Platzhalter.
- **Deutsch als Standard:** Oberfläche, Prompts, Doku und Fehlermeldungen sind deutsch.
