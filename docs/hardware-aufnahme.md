# Hardwareaufnahme ohne Meeting- oder Browserabhängigkeit

Koprotokoll verarbeitet lokale Audiodateien. Für die Aufnahme ist deshalb
nicht entscheidend, ob der Ton aus Alfaview, einem Browser, einem Telefon oder
einem anderen Programm kommt. Entscheidend ist allein ein vom Betriebssystem
bereitgestellter Audioeingang:

```text
Mikrofon / USB-Interface / System-Loopback
                  ↓
          WAV-Originaldatei
                  ↓
      Koprotokoll-Dateiimport
                  ↓
 Transkription → Sprechererkennung → Zusammenfassung
```

Die Koprotokoll-Aufnahme öffnet den lokalen Systemeingang direkt über ffmpeg;
Firefox, Chromium, Safari und eine bestimmte Meeting-Anwendung sind dafür nicht
erforderlich. Während der Aufnahme entstehen nur PCM-Samples, eine lokale WAV
und ein laufender Spitzenpegel. Parakeet und Sherpa starten erst nach dem
ausdrücklichen Stopp.

## Mac: per Alt-Doppeltipp aufnehmen

Nach dem [Schnellstart](../README.md#-schnellstart) im Projektordner
**Koprotokoll.command** doppelt anklicken. Alternativ im aktivierten Python-Environment:

```bash
python -m koprotokoll desktop
```

Beim ersten Start baut Koprotokoll eine kleine lokale App unter
`data/desktop/Koprotokoll.app`. Dafür werden macOS 12+, `ffmpeg` und die
Apple-Entwicklerwerkzeuge (`swiftc`) benötigt. Die App startet den lokalen
Server oder verwendet einen bereits laufenden Koprotokoll-Server am gewählten
Port. Ein Finder-Einstieg unter `~/Applications/Koprotokoll.app` öffnet dieselbe
App per Doppelklick; eine bereits vorhandene andere Installation wird erhalten.
Das Einstellungsfenster öffnet sich beim Start und beim erneuten Öffnen der App.
Es ist auch über **Ko → Einstellungen …** erreichbar. Es beginnt noch keine Aufnahme.

1. Unter **Audioquelle** den gewünschten Eingang wählen. Er lässt sich auch über
   das Ko-Menü wechseln. Die Quelle wird anhand ihres Namens gespeichert und vor
   jeder Aufnahme neu zugeordnet. Ist sie nicht mehr eindeutig verfügbar, wird
   der Start mit einer Fehlermeldung abgebrochen.
2. Unter **Tastenkürzel** das globale Kürzel ein- oder ausschalten und die
   Doppeltipp-Taste wählen: **Alt/Option**, **Control** oder **Umschalt**, jeweils
   links oder rechts. Standard bleibt Alt. Änderungen werden sofort gespeichert
   und gelten ohne Neustart. Beim Wechsel verwirft die App einen begonnenen
   Doppeltipp. Während einer Aufnahme sind diese Einstellungen gesperrt.
3. **Freigabe prüfen** zeigt den aktuellen Zustand der Eingabeüberwachung und
   fordert sie bei Bedarf an. **macOS-Einstellungen öffnen** führt zur
   Einstellungs-App. Dort Koprotokoll unter
   **Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung**
   freigeben. Falls macOS es verlangt, die App neu öffnen. Die App zeigt die
   Freigabe an, erteilt sie aber nicht selbst.
   Meldet die App trotz eingeschaltetem macOS-Schalter weiterhin eine fehlende
   Freigabe, kann nach einem lokalen Neubau noch die alte App zugeordnet sein.
   In der Eingabeüberwachung nur den Koprotokoll-Eintrag entfernen und über **+**
   die aktuelle `data/desktop/Koprotokoll.app` erneut auswählen. Anschließend
   **Beenden & erneut öffnen** bestätigen und den Status in Koprotokoll prüfen.
   Das Mac-Passwort ausschließlich im macOS-Dialog eingeben.
4. **Die gewählte Taste zweimal kurz drücken und loslassen** startet die Aufnahme.
   In der Menüleiste erscheint **●** mit der Aufnahmedauer. Kombinationen mit
   anderen Tasten und längeres Halten lösen keine Aufnahme aus.
5. **Dieselbe Taste zweimal erneut** stoppt und sichert die Aufnahme. Erst danach beginnt
   die Auswertung; das Meeting öffnet sich im Standardbrowser.

Start und Stopp sind auch im Einstellungsfenster und im Ko-Menü verfügbar, unabhängig von der
Tastenkürzel-Freigabe. Audioquelle und Pegel müssen vor dem eigentlichen
Gespräch wie unten beschrieben geprüft werden. Für Systemton plus Mikrofon
gelten weiterhin die gesonderten Voraussetzungen; die Menüleistensteuerung
belegt nicht, dass beide Gesprächsseiten aufgenommen werden.

Das Tastenkürzel speichert keine Tastendrücke oder Texte. Es steht zur Verfügung, solange die Menüleisten-App
läuft; ein automatischer Start bei der Anmeldung wird nicht eingerichtet.
**Tastenkürzel beenden** schließt die Menüleiste nach dem Stopp einer Aufnahme.
Der lokale Server bleibt für die Auswertung und Oberfläche geöffnet. Sein
Startprotokoll liegt unter `data/desktop/server.log`.

## Windows: per Alt-Doppeltipp aufnehmen

Im aktivierten Python-Environment im Projektordner:

```powershell
python -m koprotokoll tray
```

Koprotokoll legt ein Symbol im Infobereich an. Es verwendet einen bereits
laufenden lokalen Server oder startet ihn; das Symbol erscheint sofort und
zeigt währenddessen **Server wird gestartet …**. Das Startprotokoll liegt unter
`data/desktop/server.log`. Zusätzliche Pakete, ein Browser oder eine bestimmte
Meeting-Anwendung sind nicht nötig. Ein zweiter Start meldet
„Koprotokoll läuft bereits im Infobereich.“

Ein Klick mit der linken oder rechten Maustaste öffnet das Menü:

1. **Aufnahme starten** (bzw. **Aufnahme stoppen**) – die Aufnahme beginnt erst
   nach diesem ausdrücklichen Klick oder dem Tastenkürzel.
2. **Audioquelle** – ein Untermenü mit allen Eingängen, die Windows über
   `ffmpeg` meldet, darunter **Systemton + Standardmikrofon (Windows)**. Die
   Quelle wird über ihren Namen in `data/desktop/windows-einstellungen.json`
   gespeichert und vor jeder Aufnahme neu zugeordnet. Ist sie nicht mehr
   vorhanden oder nicht eindeutig, bricht der Start mit einer deutschen
   Meldung ab; es wird keine andere Quelle ersatzweise aufgenommen.
   **Audioquellen aktualisieren** liest die Liste neu ein, etwa nach dem
   Anstecken eines USB-Interfaces.
3. **Tastenkürzel** – **Aktiviert** schaltet das globale Kürzel ein oder aus;
   darunter lässt sich **Alt zweimal**, **Strg zweimal** oder
   **Umschalt zweimal** wählen. Änderungen gelten sofort und ohne Neustart.
   Während einer Aufnahme ist das Untermenü gesperrt.
4. **Koprotokoll öffnen** zeigt die Weboberfläche im Standardbrowser.
5. **Beenden** entfernt das Symbol. Während einer Aufnahme ist der Eintrag
   gesperrt und weist darauf hin, zuerst zu stoppen und zu sichern.

**Die gewählte Taste zweimal kurz drücken und loslassen** startet die Aufnahme,
dieselbe Taste zweimal stoppt und sichert sie. Im Symbol erscheint **●** mit der
Aufnahmedauer (ab einer Stunde `H:MM:SS`), abwechselnd mit dem Status. Längeres
Halten, Kombinationen mit anderen Tasten und ein zu großer Abstand lösen keine
Aufnahme aus. Das Kürzel fragt nur den Tastenzustand ab, zeichnet keine Tasten
auf und braucht keine Freigabe. Es gilt, solange das Symbol läuft; ein
automatischer Start bei der Anmeldung wird nicht eingerichtet.

Fehler zeigt Koprotokoll als Sprechblase am Symbol und als Statuszeile im Menü;
nach dem nächsten erfolgreichen Schritt verschwindet die Meldung. Das Beenden
des Symbols beendet den Server nicht – die Auswertung und die Oberfläche
bleiben erreichbar. Das Symbol steuert die Aufnahme ausschließlich über
`/api/capture*`; die Weboberfläche kann parallel starten und stoppen, der
nächste Klick im Tray übernimmt den tatsächlichen Zustand des Servers.

## Geeigneten Eingang wählen

1. Vor dem Gespräch alle Beteiligten über die Aufnahme informieren und die
   rechtlich nötige Einwilligung einholen.
2. Den vorhandenen Eingang auflisten:

   ```bash
   python scripts/capture_audio.py --list
   ```

   Der Befehl verwendet nur `ffmpeg` und die Geräte-API des jeweiligen
   Betriebssystems: AVFoundation unter macOS, PulseAudio/PipeWire unter Linux
   und DirectShow unter Windows. Es wird dabei nichts aufgenommen oder an einen
   Dienst übertragen.
3. In Koprotokoll **Aufnehmen → Audioeingang dieses Rechners** wählen und eine
   Quelle auswählen. Unter macOS 15+ und unter Windows gibt es zusätzlich
   **Systemton + Standardmikrofon**. Die laufende Pegelanzeige bestätigt Samples
   am Eingang;
   sie allein bestätigt noch nicht, dass beide Gesprächsseiten enthalten sind.
   Für den eigenständigen Entwickler-/CLI-Test kann eine Quelle gezielt
   ausgewählt werden. Auf macOS ist das die Gerätenummer aus der Liste; unter
   Linux und Windows der Gerätename:

   ```bash
   python scripts/capture_audio.py --device 3 --seconds 180 \
     --output ./testaufnahme.wav
   ```

   Die Originaldatei ist verlustfreies WAV (standardmäßig 48 kHz, Stereo) und
   wird wegen `ffmpeg -n` nie überschrieben. Sie bleibt vor der Konvertierung
   erhalten. Bei der Systemtonaufnahme (macOS und Windows) vergleicht
   Koprotokoll vor dem Start der Pipeline die Dauer der beiden Spuren. Weichen
   sie um mehr als 0,5 Sekunden ab, wird das Meeting nicht angelegt; die Spuren
   bleiben lokal für die Prüfung.
4. Sofort prüfen, ob sie wirklich beide Seiten enthält:

   ```bash
   ffprobe -v error -show_entries format=duration:stream=codec_name,codec_type,sample_rate,channels \
     -of default=noprint_wrappers=1 ./testaufnahme.wav
   ```

   Die Datei mit einem lokalen Player vollständig anhören, insbesondere
   Sprecherwechsel und gleichzeitiges Sprechen. Eine Pegelanzeige allein ist
   kein Nachweis.

Für ein Raumgespräch reicht ein Mikrofon, sofern beide Stimmen verständlich
ankommen. Für ein Online-Gespräch müssen die Stimmen der Freunde zusätzlich als
Signalquelle sichtbar sein: etwa über einen vom Betriebssystem oder
  Audiointerface angebotenen Loopback-Eingang. Unter macOS 15+ kann die
  Systemtonfunktion die aktuelle Audioausgabe plus Standardmikrofon erfassen.
  Sie verwendet einen privaten **Core-Audio-Tap** für den Systemton und
  AVAudioEngine für das Mikrofon. Beim ersten Start benötigt Koprotokoll die
  Freigaben **Nur Aufnahme von Systemaudio** und **Mikrofon** unter
  **Datenschutz & Sicherheit**. Eine Bildschirmaufnahmefreigabe wird für diesen
  Weg nicht benötigt; Bildschirme und Fenster werden nicht abgefragt.
  Der Audioprozess läuft aus demselben Koprotokoll-App-Paket, dessen Zwecktexte
  macOS bei der Freigabe anzeigt. Eine alte, ausgeschaltete Bildschirmfreigabe
  kann in den macOS-Einstellungen weiterhin aufgeführt sein.
  Für diesen nativen Weg braucht der lokale Entwicklungsrechner `swiftc` aus den
  Apple-Entwicklerwerkzeugen; ohne Compiler wird die Option nicht angeboten.
  Unter Windows läuft derselbe Weg über WASAPI: Der Helfer
  `koprotokoll/capture_windows.py` nimmt den Loopback des
  Standardwiedergabegeräts und das Standardmikrofon als getrennte Spuren auf
  (nur Python und `numpy`, keine Treiberinstallation, keine Freigabedialoge).
  Beide Spuren werden auf den Moment beschnitten, in dem beide Geräte bereit
  waren; ein Rückstau im Loopback und ein später öffnendes USB-Mikrofon
  verschieben die Spuren dadurch nicht mehr gegeneinander. Die App wählt keinen
  Treiber und ändert weder Standard-Ein- noch Standard-Ausgabe. Unter Linux
  hängt Systemtonzugriff davon ab, ob die Audioinfrastruktur einen Monitor-/
  Loopback-Eingang anbietet. Auf Geräten ohne passenden Eingang bleibt der
  Import einer lokalen Aufnahmedatei verfügbar.

## In Koprotokoll importieren und prüfen

Die WAV-Datei über **Hochladen** importieren oder lokal per API senden:

```bash
curl -F file=@./testaufnahme.wav -F title='Hardwaretest' \
  http://127.0.0.1:8000/api/meetings
```

Ohne `num_speakers` wird die Sprecherzahl automatisch geschätzt. Eine Vorgabe
wie `-F num_speakers=2` ist ein gesonderter Vergleichslauf und kein Nachweis
einer automatisch erkannten Sprecherzahl.

Danach liefert `GET /api/meetings/<id>` neben Segmenten die Rohzeiten der
Sprechererkennung unter `meta.diarization.turns`. Die Oberfläche zeigt einen
fehlgeschlagenen oder von der angeforderten Sprecherzahl abweichenden Lauf
sichtbar an; ein einzelnes Label `S1` wird damit nicht mehr als Erfolg
missverstanden.

Die Messung gegen eine unabhängig erstellte Referenz läuft vollständig lokal:

```bash
curl http://127.0.0.1:8000/api/meetings/1 > ergebnis.json
python scripts/evaluate_audio_sample.py referenz.json ergebnis.json
```

`referenz.json` enthält `turns` mit `speaker`, `start`, `end` und `text`
(alternativ direkt eine Liste dieser Abschnitte). Die Referenz muss unabhängig
vom Modellergebnis am Originalton erstellt und von einem Menschen geprüft sein.
Zeitangaben sind Sekunden ab Dateianfang; überlappende Stimmen bekommen eigene
Abschnitte.

Die Ausgabe enthält die tatsächlich verwendeten Bewertungsregeln unter `rules`:

- WER vergleicht die Wortfolgen in Reihenfolge der Abschnittsanfänge.
  Unicode-casefold vereinheitlicht Groß-/Kleinschreibung; Satzzeichen trennen
  Wörter, innere Apostrophe und Bindestriche bleiben erhalten. Zahlen werden
  nicht in Zahlwörter umgewandelt. Gleichzeitiges Sprechen hat keine eindeutige
  Wortreihenfolge; diese WER ist daher keine überlappungsinvariante Bewertung.
- DER verwendet standardmäßig ein 100-ms-Raster und schließt Rastermittelpunkte
  innerhalb von ±250 ms um Referenzgrenzen aus. `--resolution` und `--collar`
  ändern diese Werte. Das Fenster reicht von 0 bis zum letzten Referenz- oder
  Hypothesenende; ein angebrochener letzter Rasterabschnitt wird gekürzt.
  Erkannte Sprache in Referenzpausen zählt als Fehlalarm. Beliebige Sprecher-IDs
  werden global eineindeutig nach größter korrekter Sprecherzeit zugeordnet.
- `der.overlap` bewertet die verbleibenden Rasterabschnitte mit mehreren aktiven
  Referenzstimmen separat, mit derselben Zuordnung und Grenztoleranz. Jede aktive
  Stimme zählt zur Referenzsprecherzeit. Kurze Einwürfe können ganz innerhalb
  des ausgeschlossenen Grenzbereichs liegen; ein ergänzender Lauf mit
  `--collar 0` macht diesen Unterschied sichtbar.
- `error_frames` nennt die fehlerhaften Rasterzeitstellen und die Anzahl der
  jeweils verpassten, zusätzlichen oder verwechselten Stimmen. Die Zeitstellen
  sind Rasterwerte, keine exakten Fehlergrenzen im Originalton.
- Ohne Referenzwörter ist WER `null`; ohne bewertbare Referenzsprecherzeit ist
  DER `null`. Das bedeutet „nicht bestimmbar“, nicht fehlerfrei. Eine
  fehlgeschlagene oder leere Sprechererkennung wird vom Skript abgewiesen.

Das ist eine transparente Vergleichsmessung ohne zusätzliche Abhängigkeiten,
kein Ersatz für die Metrik eines anderen Werkzeugs. Aufnahmefehler wie fehlende
oder doppelte Stimmen müssen zusätzlich am Originalton geprüft werden.

## Geräte- und Zugriffsgrenzen

| Weg | Voraussetzung | Ausweichweg |
| --- | --- | --- |
| Direkter Hardwareeingang | Betriebssystem sieht Mikrofon/Interface/Loopback | WAV auf einem anderen Gerät aufnehmen und importieren |
| Systemton | macOS 15+ (Systemaudio- und Mikrofonfreigabe) oder Windows 10/11 (Standardgeräte, kein Dialog); Linux mit Monitor-/Loopback-Eingang | Freigegebene lokale Aufnahmedatei importieren |
| Browseraufnahme | Sichere Seite, Browser-Berechtigung, passende Audiofreigabe | Hardware-WAV importieren |
| Mobiles Gerät | Lokale Aufnahme-App oder geeigneter Eingang | Datei in das lokale Netz übertragen und importieren |

Die Hardwareaufnahme läuft auf dem Rechner, auf dem der Koprotokoll-Server
läuft. Für Zugriffe aus dem lokalen Netz muss ein API-Schlüssel gesendet werden.
Mobile Geräte können eine lokale Aufnahme über den gemeinsamen Dateiimport
beisteuern. Nicht jedes Betriebssystem stellt Systemton als Eingang bereit;
diese Einschränkung wird pro Gerät benannt.
