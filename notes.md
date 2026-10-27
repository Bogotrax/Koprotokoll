# Notizen – Koprotokoll

## Kalender-Anbindung: 4-Punkte-Plan (Anwenderfreundlichkeit statt ICS-Links)

1. **OAuth „Mit Microsoft anmelden"** (Microsoft Graph / Outlook-Kalender lesen) – gleiche Erfahrung wie „Weiter mit Google"
2. **„Mit Google anmelden"** – ist in `google.py` schon vorhanden (OAuth + auto_join), ggf. ausbauen/vervollständigen
3. **CalDAV mit App-Passwort** als „Anmeldung mit Passwort" für Self-Hosted (Nextcloud, iCloud, Posteo, mailbox.org etc.)
4. **ICS-Abolink nur noch als versteckte „Experten-Option"** – nicht als Hauptweg, da für Anwender zu technisch (kein Wissen über „Kalender veröffentlichen"-Buttons, Geheim-Links etc.)

**Status 2026-10-27:** Punkt 3 (CalDAV, nur lesen, mit Anbieter-Presets) und Punkt 4 (ICS als
Experten-Option) sind umgesetzt, 1+2 existieren als OAuth-Karten (Google/Microsoft). Die
verbindliche Planung steht in `docs/plan.md` (AP2) – diese Datei bleibt Scratchpad.

**Hintergrund:** Anwender erwartet „1-Klick-Anmeldung + Zugriff erlauben", kein Suchen nach versteckten „Veröffentlichen"-Buttons oder Kopieren von geheimen ICS-URLs. Das bisher gebaute ICS-Abo-Feature gilt als „Nerd-Variante" und soll nicht der Hauptweg werden.

## Entscheidung: Anzeige 7 Tage (2026-10-17)

Die Oberfläche zeigt die **nächsten 7 Tage** an Kalenderterminen (überschaubar, so entschieden). Vorbild: Fireflies zeigt ebenfalls kommende Meetings an. Wichtig: Das gilt für die **Anzeige** – die Bot-Einplanung bleibt bei ~15 Minuten vor Terminbeginn.
