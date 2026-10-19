# Signal/Molly – gesicherter Zwischenstand vom 19.10.2026

## Verbindliches Ziel

Zwei Menschen telefonieren in Signal/Molly auf ihren Geräten. Koprotokoll
zeichnet nach Information und Zustimmung beider Beteiligten beide Stimmen auf:
sowohl bei eingehenden als auch bei ausgehenden Anrufen. Es nutzt das eigene
verknüpfte Konto, braucht keine zusätzliche Rufnummer, nimmt nicht selbst ab
und startet oder übernimmt keine Anrufe. Aufnahme und Gespräch sind getrennt
steuerbar; das Gesprächsende soll die Aufnahme beenden.

**Dieses Ziel ist noch nicht umgesetzt; seine Machbarkeit ist offen.**
Ein verknüpftes Gerät erhält nicht allein durch die Kopplung nachweislich das
Audio eines auf einem anderen Gerät geführten Gesprächs.

## Tatsächlich nachgewiesener Teilerfolg

- Das gewünschte Molly-Konto ist als verknüpftes Konto erreichbar.
- Ein Mensch ruft an, das Testskript nimmt mit `acceptCall` an.
- Zustände: `RINGING_INCOMING` → `CONNECTING` → `CONNECTED` → `ENDED`.
- Negative vorzeichenbehaftete Call-IDs funktionieren bei der Annahme.
- Der Tunnel liefert Audio über einen Unix-Socket, das Relay über TCP 9002.
- `_Recorder` schreibt PCM 16 Bit, mono, 48 kHz als WAV auf dem Windows-Host.
- Zweiter eingehender Test: 1.075.200 PCM-Bytes, 11,2 Sekunden, kein
  Aufnahmefehler; Auflegen erkannt (`RemoteHangup`).
- Nach Konvertierung auf 16 kHz wurde Meeting 19 erfolgreich verarbeitet.
  Sprachinhalt bestätigt die Aufnahme; echte Transkripte werden nicht eingecheckt.
- Auch ein ausgehender Anruf mit Koprotokoll als Anruf-Endpunkt wurde zuvor
  erfolgreich aufgenommen (Meeting 17).

**Das beweist Audioempfang als Anruf-Endpunkt, nicht passives Begleiten.**
Die automatische Annahme war ein befristeter Test, kein laufender Dienst.

## Gesicherte Bestandteile und lokale Abhängigkeiten

- `koprotokoll/signal_calls.py`: experimenteller Anrufmanager und Recorder.
- `scripts/audio-relay.pl`: tatsächlich verwendetes Audio-Relay; im Container
  aktuell manuell als `perl /tmp/relay-next.pl` gestartet.
- `scripts/signal_incoming_test.py`: aus dem temporären Test abgeleitetes,
  portables Diagnosewerkzeug für den eingehenden Endpunkt-Test. Nimmt
  **nur mit explizitem `--accept-call`** ab;
  gehört nicht zur geplanten Begleiterfunktion. Wird nicht automatisch gestartet.
- `scripts/audio-bridge.sh`: älterer socat-Versuch, nicht parallel zum Relay
  starten; der Tunnel akzeptiert nur eine Socket-Verbindung je Anruf.
- `scripts/probe-listen.pl`: Diagnoseversuch, ebenfalls nicht parallel verwenden.
- Lokales Docker-Image: `koprotokoll-signal:pipe`, ID
  `sha256:6caf59852af927205392dacae776940fa5050a6d91db5a161edbcc8dde560622`.
- Zusätzlich aktueller Container-Dateistand lokal gesichert als
  `koprotokoll-signal:checkpoint-20261019`, Image-ID
  `sha256:5e7981d140fc95fdbc7534232bbf4248cd92bbed8ae34509a4a9d89c2487a4a7`.
  Dieser Snapshot enthält keine gemounteten Kontodaten und keine laufenden
  Prozesse. Er kann temporäre Diagnosedaten enthalten: nicht veröffentlichen.
- Tunnel-Binary und Wrapper liegen zusätzlich in `data/signal-tunnel/`.
- Kontoschlüssel: `data/signal-cli/`; lokale Daten, Aufnahmen und Protokolle
  liegen unter `data/`. Sie bleiben absichtlich git-ignoriert.

Git sichert Quellcode, Tests und diese Anleitung, **nicht** Docker-Images,
Modelle, Kontoschlüssel oder Aufnahmen. Das ist ein lokaler Sicherungspunkt,
kein externes Backup. Nicht `data/` löschen und keinen Docker-Prune durchführen.
Docker-Build und manuelle RPC-/Audio-Brücken sind noch nicht reproduzierbar
in Compose integriert; ein Container-Neustart kann manuelle Prozesse verlieren.

## Bekannte offene Fehler

1. Upload einer 48-kHz-WAV scheiterte bei Meeting 18: `Erwartet 16000 Hz,
   erhalten 48000 Hz`. Upload-Ziel und Konvertierungsziel sind beide
   `<id>.wav`; die Pipeline überspringt die Konvertierung bei vorhandener Datei.
   Test-Workaround: vorher `koprotokoll.audio.to_wav16k` auf eine neue Datei
   anwenden, dann diese hochladen. Der Produktfehler ist noch nicht behoben.
2. Ausgehender Manager liest die Call-ID nicht zuverlässig aus verschachtelten
   RPC-Antworten; globaler Zustand statt Sitzungszustand. Automatisches Auflegen
   ist deshalb nicht zuverlässig. Die eingehende Testannahme nutzt Ereignisse.
3. Relay: blockierende Clients, Teil-Schreibvorgänge und Verbindungsabbrüche
   robuster behandeln; Dienste und Ports sicher und dauerhaft integrieren.
4. Oberfläche trennt Nachrichtenempfänger und verknüpftes Konto noch nicht klar;
   QR-Neuverknüpfung bei vorhandenem Konto fehlt.

## Nächster Schritt – keine weiteren Bot-Anruftests

1. Rein beobachtenden Test vorbereiten: kein `acceptCall`, kein `startCall`,
   kein `hangupCall`. Nur Anrufereignisse und verfügbare Audioquellen prüfen.
2. Eingehend: Mensch nimmt **auf Molly** ab und telefoniert mit zweitem Menschen.
3. Ausgehend: Mensch ruft **auf Molly** den zweiten Menschen an.
4. Jeweils feststellen, ob das verknüpfte Gerät Ereignisse und beide Audiospuren
   erhält, ohne den Anruf zu übernehmen. Keine Aufnahme ohne Zustimmung.
5. Wenn kein Audiozugriff möglich ist: Protokoll-/Implementierungsbefund
   absichern und Einschränkung offen benennen. Desktop-/gerätebasierte Aufnahme
   wäre eine gesondert abzustimmende Alternative, keine stillschweigende Erfüllung.

## Validierung des Sicherungsstands

`python -m pytest -q --tb=short`: **40 bestanden** am 19.10.2026.
Der Live-Test mit Meeting 19 ist zusätzlich geprüft. Das ist kein Nachweis
für den noch offenen passiven Begleiter. Kein neuer UI-Funktionstest im Rahmen
dieser Sicherung durchgeführt. Das abgeleitete Diagnosewerkzeug wurde auf
Syntax und `--help` geprüft; damit wurde kein weiterer echter Anruf gestartet.