# kontextlayer_kommunikation – lokaler Meeting-Notetaker

Ein lokaler Nachbau der Grundfunktionen von [Fireflies.ai](https://fireflies.ai/):
Meetings aufnehmen oder hochladen, mit **NVIDIA Parakeet** transkribieren, Sprecher
erkennen, zusammenfassen, Action Items extrahieren und alles durchsuchen – ohne Cloud.
Die Analyse des Vorbilds mit [REA](https://github.com/morluto/rea) steht in
[`docs/fireflies-analyse.md`](docs/fireflies-analyse.md).

## Funktionen

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

## Architektur

```
Browser (static/)  ──HTTP──▶  FastAPI (app.py)
  Aufnahme, Upload,             │
  Player, Suche                 ├─ pipeline.py   Hintergrund-Queue
                                │    ├─ audio.py      ffmpeg → 16 kHz WAV
                                │    ├─ speech.py     Parakeet (onnx-asr) + VAD,
                                │    │                Diarisierung (sherpa-onnx),
                                │    │                Wörter → Äußerungen
                                │    └─ summarize.py  lokales LLM / Heuristik
                                ├─ db.py         SQLite + FTS5
                                └─ export.py     MD / TXT / SRT / JSON
```

REST-API: `GET/POST /api/meetings`, `GET/PATCH/DELETE /api/meetings/{id}`,
`GET /api/meetings/{id}/audio`, `POST …/summarize`, `POST …/ask`,
`POST/DELETE …/bookmarks`, `GET …/export?format=md|txt|srt|json`, `GET /api/search?q=`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Die Tests nutzen Platzhalter-Engines und brauchen keine Modelle.
