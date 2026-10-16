# AGENTS.md – Leitfaden für Coding-Agenten

Diese Datei richtet sich an KI-Coding-Agenten (Claude Code, OpenAI Codex, Cursor, GitHub Copilot,
Aider, Jules, …) und an Menschen, die mit ihnen arbeiten. Sie enthält alles, was ein Agent
braucht, um in diesem Repository sofort produktiv und sicher zu arbeiten.

## Worum es geht

**Koprotokoll** (Teil der [Ko-Familie](docs/plan.md#6-die-ko-familie)) ist ein lokaler Meeting-Assistent nach dem Vorbild von Fireflies.ai: Aufnahme
(auch per Bot, der Google Meet beitritt) → Transkription mit NVIDIA Parakeet → Sprechererkennung
→ Zusammenfassung mit lokalem LLM → Suche, Export, API, Webhooks, MCP.
**Leitprinzipien: deutsch, lokal, offen.** Kein Feature darf eine Cloud voraussetzen.

## Mitmachen als Agent: Issue → Fork → Pull Request

Dieser Ablauf gilt für Agenten, die selbstständig arbeiten (z. B. Claude Code im Web, Codex,
Copilot Coding Agent, Jules), genauso wie für Menschen mit Agent im Terminal.

1. **Aufgabe wählen.** Offene Issues mit dem Label
   [`agent-ready`](https://github.com/enving/Koprotokoll/issues?q=is%3Aissue+is%3Aopen+label%3Aagent-ready)
   sind so beschrieben, dass ein Agent sie ohne Rückfragen umsetzen kann (Kontext, Aufgabe,
   „Fertig, wenn“, betroffene Dateien). `good first issue` = klein und gut zum Einstieg.
   Reihenfolge und Abhängigkeiten zeigt das Übersichts-Issue
   [Fahrplan #21](https://github.com/enving/Koprotokoll/issues/21), den Gesamtzusammenhang [docs/plan.md](docs/plan.md).
2. **Prüfen und beanspruchen.** Ist das Issue schon zugewiesen oder hat jemand in den letzten
   7 Tagen „übernehme ich“ kommentiert, ein anderes wählen. Sonst einen Kommentar hinterlassen:
   > 🤖 Übernehme ich. Agent: *Name/Modell*, verantwortlich: @*github-name*. Plan: *1–3 Sätze*.

   Ein Anspruch verfällt, wenn nach 7 Tagen kein (Entwurfs-)Pull-Request verlinkt ist.
3. **Fork oder Branch.**
   - Ohne Schreibrechte (der Normalfall): Repository forken
     (`gh repo fork enving/Koprotokoll --clone` oder über die GitHub-Oberfläche).
   - Mit Schreibrechten: direkt im Repository einen Branch anlegen.
   - Branch-Name: `agent/<issue-nr>-<kurzname>`, z. B. `agent/12-jitsi-adapter`.
4. **Umsetzen.** Nur das, was das Issue verlangt. Die Regeln unten gelten, Tests mit `pytest -q`.
   Bei größeren Aufgaben früh einen **Entwurfs-PR** öffnen, damit andere den Stand sehen.
5. **Pull Request** gegen `main` von `enving/Koprotokoll`:
   - Titel auf Deutsch, z. B. „Bot-Adapter für Jitsi (#12)“
   - im Text `Closes #<nr>`, die PR-Vorlage ausfüllen, beschreiben, **wie getestet** wurde
   - offen sagen, was **nicht** getestet werden konnte (z. B. echte Meetings, Outlook auf Windows)
   - kennzeichnen, dass der PR von einem Agenten erstellt wurde, und den verantwortlichen Menschen nennen
6. **Review.** Die CI muss grün sein. Jeder Agenten-PR braucht die Freigabe eines Menschen –
   Agenten mergen nie selbst. Review-Kommentare im selben Branch beheben.
7. **Neue Ideen** oder zu große Aufgaben: nicht im PR mitlösen, sondern ein neues Issue anlegen
   (Vorlage „Agenten-Aufgabe“) und im PR darauf verweisen.

**Für Maintainer:** Eine Aufgabe wird `agent-ready`, wenn sie Kontext, klare Aufgabe,
„Fertig, wenn“-Kriterien und Hinweise auf die betroffenen Dateien hat – die Vorlage
„Agenten-Aufgabe“ hilft dabei. Agenten, die direkt am Issue arbeiten können (z. B. per Zuweisung
oder Erwähnung), können so ein Issue ohne weitere Absprache übernehmen.

## Schnellstart für Agenten

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                      # ~20 s, keine Modelle und kein Internet nötig
```

- Ist `ffmpeg` nicht installiert, werden ein paar Tests übersprungen.
- Der Browser-Test des Bots braucht `playwright install chromium` (oder `KOPROTOKOLL_BOT_CHROME=/pfad/zu/chrome`); fehlt der Browser, wird er übersprungen.
- App starten: `python -m koprotokoll --port 8000`, Daten liegen in `./data` (änderbar mit `KOPROTOKOLL_DATA`).
- Echte Modelle nur bei Arbeit an der Erkennung: `python scripts/download_models.py` (~700 MB).

## Landkarte des Codes

| Pfad | Inhalt |
|---|---|
| `koprotokoll/app.py` | FastAPI-App, alle REST-Endpunkte (`create_app()` ist die Fabrik, auch für Tests) |
| `koprotokoll/pipeline.py` | Hintergrund-Queue: konvertieren → transkribieren → Sprecher → zusammenfassen |
| `koprotokoll/speech.py` | Parakeet-Transkription, Diarisierung, `build_utterances()` (Wörter + Sprecher → Äußerungen) |
| `koprotokoll/summarize.py` | LLM-Client (OpenAI-kompatibel), Prompts, Offline-Heuristik, „Frag das Meeting“ |
| `koprotokoll/db.py` | SQLite-Schema, FTS5-Suche, Migrationen in `Database.__init__` |
| `koprotokoll/bot/` | Meeting-Bot: `runner.py` (Steuerung), `platforms.py` (Meet-Adapter, Link-Erkennung), `capture.js` (WebRTC-Mitschnitt im Browser) |
| `koprotokoll/google.py`, `scheduler.py`, `recap.py` | OAuth, Kalender-Auto-Join, Recap per Gmail |
| `koprotokoll/auth.py`, `events.py` | API-Schlüssel, Webhooks |
| `koprotokoll/mcp_server.py` | MCP-Server über stdio |
| `koprotokoll/static/` | Weboberfläche: Vanilla JS, kein Build-Schritt |
| `tests/` | `test_koprotokoll.py` (Kern), `test_integrations.py` (API, Bot, Google), `fixtures/` |
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

Der Gesamtplan mit Arbeitspaketen, Reihenfolge und „Fertig, wenn“-Kriterien steht in
[docs/plan.md](docs/plan.md). Kleinere Einstiege:

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
