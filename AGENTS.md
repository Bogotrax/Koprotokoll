# AGENTS.md – Leitfaden für Coding-Agenten

Diese Datei richtet sich an KI-Coding-Agenten (Claude Code, OpenAI Codex, Cursor, GitHub Copilot,
Aider, Jules, …) und an Menschen, die mit ihnen arbeiten. Sie enthält alles, was ein Agent
braucht, um in diesem Repository sofort produktiv und sicher zu arbeiten.

## Worum es geht

**Notetaker** ist ein lokaler Meeting-Assistent nach dem Vorbild von Fireflies.ai: Aufnahme
(auch per Bot, der Google Meet beitritt) → Transkription mit NVIDIA Parakeet → Sprechererkennung
→ Zusammenfassung mit lokalem LLM → Suche, Export, API, Webhooks, MCP.
**Leitprinzipien: deutsch, lokal, offen.** Kein Feature darf eine Cloud voraussetzen.

## Schnellstart für Agenten

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                      # ~20 s, keine Modelle und kein Internet nötig
```

- Ist `ffmpeg` nicht installiert, werden ein paar Tests übersprungen.
- Der Browser-Test des Bots braucht `playwright install chromium` (oder `NOTETAKER_BOT_CHROME=/pfad/zu/chrome`); fehlt der Browser, wird er übersprungen.
- App starten: `python -m notetaker --port 8000`, Daten liegen in `./data` (änderbar mit `NOTETAKER_DATA`).
- Echte Modelle nur bei Arbeit an der Erkennung: `python scripts/download_models.py` (~700 MB).

## Landkarte des Codes

| Pfad | Inhalt |
|---|---|
| `notetaker/app.py` | FastAPI-App, alle REST-Endpunkte (`create_app()` ist die Fabrik, auch für Tests) |
| `notetaker/pipeline.py` | Hintergrund-Queue: konvertieren → transkribieren → Sprecher → zusammenfassen |
| `notetaker/speech.py` | Parakeet-Transkription, Diarisierung, `build_utterances()` (Wörter + Sprecher → Äußerungen) |
| `notetaker/summarize.py` | LLM-Client (OpenAI-kompatibel), Prompts, Offline-Heuristik, „Frag das Meeting“ |
| `notetaker/db.py` | SQLite-Schema, FTS5-Suche, Migrationen in `Database.__init__` |
| `notetaker/bot/` | Meeting-Bot: `runner.py` (Steuerung), `platforms.py` (Meet-Adapter, Link-Erkennung), `capture.js` (WebRTC-Mitschnitt im Browser) |
| `notetaker/google.py`, `scheduler.py`, `recap.py` | OAuth, Kalender-Auto-Join, Recap per Gmail |
| `notetaker/auth.py`, `events.py` | API-Schlüssel, Webhooks |
| `notetaker/mcp_server.py` | MCP-Server über stdio |
| `notetaker/static/` | Weboberfläche: Vanilla JS, kein Build-Schritt |
| `tests/` | `test_notetaker.py` (Kern), `test_integrations.py` (API, Bot, Google), `fixtures/` |
| `docs/` | Einrichtung, API, Architektur, Grafiken |

Details: [docs/architektur.md](docs/architektur.md).

## Regeln

1. **Tests zuerst grün halten.** Vor jedem Commit `pytest -q`. Tests niemals löschen,
   überspringen oder abschwächen, um durchzukommen.
2. **Neues Verhalten = neuer Test.** Externe Dienste (Google, LLM, Webhook-Empfänger) werden
   nachgebaut, nie echt angesprochen. Vorlagen: `FakeTranscriber`, `FakeDiarizer`, `NoLLM`,
   `FakeGoogle`, `Receiver`.
3. **Deutsch** für alles, was Menschen lesen: UI-Texte, Fehlermeldungen (`HTTPException`),
   Kommentare, Docstrings, Doku, Commit-Nachrichten. Bezeichner im Code auf Englisch.
4. **Lokal zuerst, wenig Abhängigkeiten.** Keine neuen Pakete ohne guten Grund; HTTP über
   `urllib`, kein Frontend-Framework, kein Build-Schritt.
5. **Sicherheit:**
   - Nutzertexte im Frontend nur über `h()` bzw. `textContent` einsetzen, nie `innerHTML`.
   - Neue `/api/…`-Endpunkte gehören an den Router `api` (mit `require_auth`), nicht an `public`.
   - SQL nur mit Platzhaltern; Suchbegriffe laufen durch `_fts_query()`.
   - Keine Zugangsdaten, Tokens oder echten Aufnahmen committen.
6. **Datenbank-Änderungen** abwärtskompatibel: neue Spalten per `ALTER TABLE` in
   `Database.__init__` nachziehen, bestehende Daten nicht verlieren.
7. **Aufnahme-Ethik:** Der Bot tritt immer sichtbar unter seinem Namen bei. Keine Funktionen
   bauen, die heimlich aufnehmen oder die Erkennung als Bot verschleiern sollen, um Teilnehmer
   zu täuschen.
8. **Kleine, fokussierte Änderungen.** Ein Pull Request = ein Thema; README/`docs/` mit anpassen.

## Typische Aufgaben – so geht's

**Neuer API-Endpunkt:** In `create_app()` an `api` hängen, Pydantic-Modell für den Body,
Fehler als `HTTPException(4xx, "deutsche Meldung")`, Test mit `TestClient` in
`tests/test_integrations.py`, Endpunkt in `docs/api.md` eintragen.

**Neue Meeting-Plattform für den Bot (z. B. Teams):**
1. Muster in `PATTERNS` (`bot/platforms.py`) gibt es schon für `zoom` und `teams`.
2. Adapter-Klasse mit `join()`, `ended()`, `alone()`, `leave()` schreiben (Vorbild: `GoogleMeet`).
3. In `ADAPTERS` und `SUPPORTED` eintragen.
4. Selektoren möglichst über Rollen/ARIA-Namen (`get_by_role`) und erzwungene englische UI.
5. Der Ton-Mitschnitt (`capture.js`) ist plattformunabhängig und braucht meist keine Änderung.
6. Unit-Tests für Link-Erkennung; echte Beitritte lassen sich in CI nicht testen – im PR beschreiben, wie manuell getestet wurde.

**Prompt oder Zusammenfassung verbessern:** `SUMMARY_SYSTEM` in `summarize.py`. Das
JSON-Schema dort ist ein Vertrag mit der Oberfläche (`renderSummary()` in `static/app.js`) und
dem Export – Felder nur ergänzen, nicht umbenennen.

**Oberfläche ändern:** `static/index.html`, `style.css`, `app.js`. Farben über die
CSS-Variablen in `:root` (heller und dunkler Modus). Prüfen auf Desktop- und Handy-Breite.

## Gute Einstiegsaufgaben

- Bot-Adapter für **Microsoft Teams** (Web-Client, Gastzugang)
- **CalDAV/ICS**-Kalender als Alternative zu Google (`scheduler.py` verallgemeinern)
- **Live-Transkript** während der Bot-Aufnahme (Stücke aus `runner.py` laufend transkribieren)
- **Dockerfile** inkl. ffmpeg und Chromium
- Heuristik für deutsche **Action Items** in `summarize.py` verbessern (mit Testfällen)
- Sprecher über Meetings hinweg **wiedererkennen** (Embeddings speichern)

## Definition of Done

- `pytest -q` grün, neue Tests vorhanden
- Oberfläche manuell geprüft, falls betroffen (keine Fehler in der Browser-Konsole)
- Doku (`README.md`, `docs/*.md`, diese Datei bei neuen Konventionen) aktualisiert
- Commit-Nachricht auf Deutsch: kurze Zusammenfassung, Leerzeile, Begründung
