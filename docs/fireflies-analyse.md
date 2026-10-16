# Fireflies.ai – Analyse mit REA und Abbildung im lokalen Nachbau

## Vorgehen

Die Analyse wurde mit [REA – Reverse Engineer Anything](https://github.com/morluto/rea)
(`rea-agents`, CLI) durchgeführt. Da die eigentliche Fireflies-Web-App ein Konto
voraussetzt, beschränkt sich die Analyse auf die **öffentlich erreichbare
Website** `https://fireflies.ai/`. Es wurden keine Zugangsdaten verwendet und keine
geschützten Bereiche angefragt.

```bash
# Chromium mit DevTools-Protokoll starten (headless) und Seite öffnen
chromium --headless=new --remote-debugging-port=9222 https://fireflies.ai/

npx rea list-browser-targets http://127.0.0.1:9222
npx rea inspect-web-page   http://127.0.0.1:9222 <targetId> --observation-ms 3000 --include-accessibility-text
npx rea analyze-web-bundle http://127.0.0.1:9222 <targetId> --observation-ms 3000
```

## Befunde (REA-Evidence)

| Bereich | Beobachtung | Vertrauen laut REA |
|---|---|---|
| Frontend-Stack | Next.js (mehrere `_next/static/chunks/*`), React-Runtime | hoch / mittel |
| Endpunkte | Nur Marketing-Seite: keine verwertbaren API-Routen der App sichtbar | niedrig |
| Funktionsumfang | Aus dem Accessibility-Baum/Seitentext extrahiert (s. u.) | beobachtet |

Aus den Accessibility-Texten der Seite ergibt sich das beworbene Kern-Feature-Set:

- *„Transcribe, summarize, search, and analyze all your team conversations.“*
- Notetaker tritt Kalender-Meetings bei (Google Meet, Zoom, Teams) und nimmt auf
- *„Transcribe audio and video files … (MP3, MP4, WAV, M4A)“*
- *„Fireflies identifies different speakers …“* (Sprechererkennung)
- *AI Summaries*: Überblick, Stichwörter, Gliederung, *Action Items*
- *„Remember Every Conversation With AI Powered Search“*
- *Soundbites*, *„Leave time-stamped comments or bookmark action items“*
- *Topic Trackers*, *Sentiment Analysis*, Redeanteile (Conversation Intelligence)
- *„Easily download summary, transcript …“*
- AskFred / Fragen an Meetings, Integrationen (CRM, Slack …), MCP-Server

## Abbildung im lokalen Nachbau

| Fireflies-Funktion | Lokaler Nachbau | Umsetzung |
|---|---|---|
| Notetaker-Bot tritt Meetings bei | Browser-Aufnahme: Mikrofon **oder** Mikrofon + Tab-/Bildschirmton | `MediaRecorder` + `getDisplayMedia` |
| Datei-Upload (MP3, MP4, WAV, M4A …) | ✔ | ffmpeg → 16 kHz Mono |
| Transkription | ✔ NVIDIA **Parakeet TDT 0.6B v3** (25 Sprachen inkl. Deutsch), CPU | `onnx-asr` + Silero-VAD |
| Sprechererkennung | ✔ automatisch oder feste Sprecheranzahl, Namen umbenennbar | `sherpa-onnx` (pyannote-Segmentierung + ERes2Net-Embeddings) |
| AI Summary (Überblick, Stichwörter, Gliederung, Action Items, Entscheidungen, Fragen) | ✔ lokales LLM; Offline-Heuristik als Fallback | OpenAI-kompatible API (Ollama, llama.cpp, LM Studio) |
| Suche über alle Meetings | ✔ Volltext mit Präfixsuche und Trefferhervorhebung | SQLite FTS5 |
| AskFred | ✔ „Frag das Meeting“ | LLM mit Transkript-Kontext; ohne LLM Suchtreffer |
| Soundbites / Kommentare / Action-Item-Lesezeichen | ✔ mit Zeitstempel | Tabelle `bookmarks` |
| Player mit synchronem Transkript | ✔ Klick auf Zeitstempel springt, aktuelle Zeile wird hervorgehoben | `<audio>` + `timeupdate` |
| Redeanteile (Talk-Time) | ✔ | aus Segmentdauern |
| Download | ✔ Markdown, TXT, SRT, JSON | `/api/meetings/{id}/export` |
| Kalender-Bot, CRM/Slack-Integrationen, Teams/Freigaben, Sentiment | ✘ bewusst nicht Teil der Grundfunktionen | – |
