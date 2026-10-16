# Mitmachen bei Notetaker

Schön, dass du dabei sein willst! Notetaker soll ein Meeting-Assistent werden, dem man seine
Gespräche guten Gewissens anvertrauen kann: **deutsch, lokal, offen**. Dafür braucht es
viele Hände – und nicht nur Programmierer.

## Wie du helfen kannst

| Du … | … dann zum Beispiel |
|---|---|
| nutzt Notetaker | Fehler und Wünsche als [Issue](https://github.com/enving/kontextlayer_kommunikation/issues) melden; erzählen, was in echten Meetings klappt und was nicht |
| programmierst | ein Issue mit dem Label `good first issue` oder einen Punkt aus der [Roadmap im README](README.md) übernehmen |
| kennst dich mit Sprache aus | Prompts für Zusammenfassungen verbessern, Heuristiken für deutsche Action Items schärfen |
| schreibst gerne | Doku, Anleitungen für Windows/macOS, Erklärungen für Nicht-Techniker |
| kennst Datenschutz/Recht | Hinweise zu DSGVO und Einwilligung prüfen und verbessern |
| arbeitest mit Coding-Agenten | siehe [AGENTS.md](AGENTS.md) – Agenten sind ausdrücklich willkommen |

## Entwicklungsumgebung

```bash
git clone https://github.com/enving/kontextlayer_kommunikation.git
cd kontextlayer_kommunikation
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                         # läuft ohne Modelle und ohne Internet
python -m notetaker            # App unter http://127.0.0.1:8000
```

Für den Browser-Test des Meeting-Bots einmalig `playwright install chromium`; ohne Browser
wird dieser Test übersprungen. `ffmpeg` sollte installiert sein.

Die echten Modelle (Parakeet, Sprechererkennung) brauchst du nur, wenn du an der Erkennung
selbst arbeitest: `python scripts/download_models.py`.

## Ablauf für Änderungen

1. **Issue zuerst** bei größeren Änderungen – kurz abstimmen spart doppelte Arbeit.
2. **Branch** von `main` anlegen, z. B. `feature/teams-bot` oder `fix/suche-umlaute`.
3. **Klein halten:** ein Pull Request = ein Thema.
4. **Tests** für neues Verhalten schreiben (siehe unten) und `pytest` lokal grün bekommen.
5. **Pull Request** öffnen und die Vorlage ausfüllen. Die CI führt die Tests automatisch aus.

## Konventionen

- **Sprache:** Oberfläche, Fehlermeldungen, Kommentare, Doku und Commit-Nachrichten auf **Deutsch**.
  Bezeichner im Code (Funktionen, Variablen) auf Englisch.
- **Python 3.11+**, Typannotationen, `from __future__ import annotations`.
- **Wenig Abhängigkeiten:** Bevor du eine neue Bibliothek einführst, prüfe, ob die
  Standardbibliothek reicht (die Google-Anbindung kommt z. B. ohne Google-SDK aus).
- **Frontend ohne Build-Schritt:** reines HTML/CSS/JavaScript in `notetaker/static/`.
  Nutzertexte nie per `innerHTML` einsetzen – den Helfer `h()` verwenden.
- **Lokal zuerst:** Neue Funktionen müssen ohne Internet und ohne Cloud-Konto funktionieren
  oder sauber abschaltbar sein.
- **Kein Mitschnitt ohne Wissen:** Funktionen, die aufnehmen, müssen für alle Beteiligten
  erkennbar sein.

## Tests

- Schnelle Tests verwenden **Platzhalter-Engines** (`FakeTranscriber`, `FakeDiarizer`, `NoLLM`
  in `tests/test_notetaker.py`) – sie brauchen keine Modelle.
- Externe Dienste werden **nachgebaut**, nicht echt angesprochen (siehe `FakeGoogle` und den
  Webhook-Empfänger in `tests/test_integrations.py`).
- Der Bot wird gegen eine **lokale WebRTC-Testseite** getestet (`tests/fixtures/webrtc_meeting.html`).
- Tests nie deaktivieren oder überspringen, um die CI grün zu bekommen.

## Pull-Request-Checkliste

- [ ] `pytest` läuft lokal durch
- [ ] neues Verhalten ist durch Tests abgedeckt
- [ ] Oberfläche und Doku sind auf Deutsch und aktuell (README, `docs/`)
- [ ] keine Zugangsdaten, Aufnahmen oder persönlichen Daten im Commit

## Verhaltenskodex

Wir gehen freundlich und respektvoll miteinander um – siehe [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Sicherheitslücken bitte nicht öffentlich melden, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.

## Lizenz

Mit deinem Beitrag stimmst du zu, dass er unter der [MIT-Lizenz](LICENSE) des Projekts veröffentlicht wird.
