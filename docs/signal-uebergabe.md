# Signal-Labor: Übergabe vom 20.10.2026

## Entscheidung und belastbarer Befund

Branch: `experiment/signal-handoff`. Dies ist ein Forschungsarchiv, kein benutzbares
Produktfeature. Signal-/Molly-Live-Anruftranskription am Handy wird vorerst
nicht weiterverfolgt. Nachrichten/Sprachnachrichten sind davon getrennt.

Ziel war: zwei Menschen telefonieren normal in unverändertem Signal/Molly
auf ihren Geräten; Koprotokoll nimmt mit Zustimmung beide Stimmen auf, ohne
selbst anzunehmen, anzurufen oder einen zusätzlichen Teilnehmer einzuführen.
Dafür haben wir keinen zuverlässigen Aufnahmeweg nachgewiesen.

**Nachgewiesen:** Bot nimmt mit `acceptCall` an, empfängt PCM über den Tunnel,
Recorder schreibt WAV; nach 16-kHz-Konvertierung funktioniert Transkription.
Der erfolgreiche eingehende Test am 19.10. lieferte 1.075.200 PCM-Bytes
(11,2 s, 48 kHz/mono/16 Bit). Das ist Empfang der anrufenden Seite, kein
Nachweis beider Stimmen eines Mensch-zu-Mensch-Gesprächs. Kein PC-Mikrofon
war erforderlich: Recorder und Relay schicken im Rückkanal Stille.

**Beobachtet am 20.10.:** eingehend zunächst `RINGING_INCOMING`, nach Annahme
am Handy Koordinationsnachricht `ACCEPTED` und Bot-Zustand `ENDED`.
Ausgehend am Handy im Beobachtungsfenster keine `callEvent`-Sitzung und kein
zugehöriger neuer Tunnel-Socket. Kein Audio-Socket wurde im passiven Test
verbunden. Diese Beobachtungen belegen die Grenze unseres Aufbaus, nicht
universelle Unmöglichkeit im Signal-Protokoll. `ACCEPTED` allein ist kein
Beweis für einen bestimmten Audiofluss.

Keine weiteren eigenen Telefonoberflächen, Client-Forks oder zusätzlichen
Bot-Brücken als Ersatz für das ursprüngliche Ziel entwickeln.

## Was gesichert ist

- Koprotokoll-Code, Recorder und Diagnosewerkzeuge sind im Branch enthalten.
- `docker/signal-lab/Dockerfile`: frischer Aufbau auf festem Basisimage-Digest;
  **kein** `docker commit` und keine Schichten der lokalen Versuchssnapshots.
- `docker/signal-lab/audio-relay.pl`: tatsächlich laufende Variante
  `/tmp/relay-next.pl`, mit übersetzten Kommentaren/Logtexten archiviert.
- `docker-compose.signal-lab.yml`: eigenständiges Labor mit eigenem Kontovolume,
  RPC-Brücke und Relay als expliziten Diensten statt manueller Hintergrundprozesse.
- Image-Tag: `koprotokoll-signal:handoff-20261020`, Plattform **linux/amd64**.
- Lokales Transportarchiv: `data/signal-handoff/koprotokoll-signal-handoff-20261020.tar`.
  Image-Archiv und Binary gehören **nicht ins Git**. Ein Branch transportiert
  Quellcode und Bauanleitung, nicht das Image; das TAR muss separat übergeben werden.

**Nicht weitergeben:** `koprotokoll-signal:pipe`, `:checkpoint-20261019`,
`data/signal-cli/`, `data/media/` und echte Logs. Die alten Image-Schichten
enthalten temporäre Audiodateien und Diagnoseinformationen. Löschen in einer
zusätzlichen Docker-Schicht würde sie nicht sicher entfernen.

## Start auf einem anderen Rechner

Befehle ab Repository-Wurzel, einzeln ausführen. Docker mit Linux-Containern
und Python-Umgebung gemäß README vorausgesetzt. Keine echten Kontodaten übernehmen.

```powershell
git fetch origin experiment/signal-handoff
git switch experiment/signal-handoff
docker load --input ./data/signal-handoff/koprotokoll-signal-handoff-20261020.tar
docker compose -p signal-lab -f docker-compose.signal-lab.yml up -d --no-build
docker compose -p signal-lab -f docker-compose.signal-lab.yml ps
```

Lokale Ports (nicht ins LAN/Internet freigeben, keine Authentifizierung):

| Zweck | Adresse |
|---|---|
| REST-API | `http://127.0.0.1:18090` |
| Rohes JSON-RPC, eine JSON-Zeile je Nachricht | `127.0.0.1:16003` |
| PCM-Audio-Relay | `127.0.0.1:19002` |

Eigenes Testkonto über die REST-API verknüpfen; kein Konto im Image vorhanden.
Der Stack nimmt **nicht automatisch ab** und startet keine Anrufe.
Für QR-Verknüpfung siehe Dokumentation von signal-cli-rest-api; der
Koprotokoll-Signal-Endpunkt muss für dieses Labor auf Port 18090 zeigen.

Rein beobachtend (keine Aufnahme):

```powershell
python scripts/signal_observe_test.py --rpc-port 16003 --seconds 300
```

Endpunkt-Test nur nach Information/Zustimmung aller Beteiligten. **Nimmt ab
und zeichnet auf**, erfüllt nicht das passive Begleiterziel:

```powershell
python scripts/signal_incoming_test.py --accept-call --rpc-port 16003 --audio-port 19002 --seconds 180
```

Beenden (Kontovolume bleibt erhalten):

```powershell
docker compose -p signal-lab -f docker-compose.signal-lab.yml down
```

Nicht `down -v` verwenden, wenn die neue Verknüpfung erhalten bleiben soll.
Keine anderen Socat-/Relay-Prozesse parallel am selben Tunnel-Socket betreiben.

## Image erneut bauen und Grenzen der Reproduzierbarkeit

Das ursprüngliche lokal gebaute Tunnel-Binary ist gesichert; sein genauer
Quell-Commit und damalige Build-Patches wurden nicht dokumentiert. Deshalb
ist dies **kein vollständig aus Quellen reproduzierbarer Tunnel-Build**.
Für eine öffentliche Weiterverteilung müssen Herkunft, Lizenzen und ggf.
Pflichten zur Bereitstellung zugehöriger Quellen geklärt werden. Das Image
wird hier nur lokal archiviert, nicht in eine öffentliche Registry geladen.

Das Binary kann aus dem übergebenen Image gewonnen werden:

```powershell
docker create --name signal-lab-extract --entrypoint /bin/true koprotokoll-signal:handoff-20261020
docker cp signal-lab-extract:/opt/signal-cli-0.14.8/bin/signal-call-tunnel.real ./docker/signal-lab/artifacts/signal-call-tunnel.real
docker container rm signal-lab-extract
docker compose -p signal-lab -f docker-compose.signal-lab.yml build signal
```

Binary-SHA256:
`68277110239ba60ac090a999f6e41699543a4a6141a6ef0e156b6bb25706c326`.
Dockerfile prüft diesen Wert. Basis: signal-cli-rest-api-Digest
`sha256:2cf09d66a86fdd210e42ee91662216364acc27da1163a8c44167a77ea3a6559a`,
enthält signal-cli 0.14.8. APT-Pakete sind nicht versionsfixiert; TAR ist der
exakte binäre Sicherungspunkt, der Dockerfile ein nachvollziehbarer Neuaufbau.

## Prüfung der Übergabe

- `python -m pytest -q --tb=short`: 43 Tests bestanden.
- Compose-Konfiguration erfolgreich validiert.
- Separater Stack gestartet: Signal und Relay gesund, RPC-Dienst läuft.
  Der wartende Startbefehl wurde abgebrochen; der anschließende Statuscheck
  bestätigte die laufenden Dienste. Keine Kontoverknüpfung und kein echter Anruf.
- TAR-SHA256: `c21bd63627d5769f1338befb630048cbcfefeaf57c62478779a4dec457a70111`.

## Bekannte Baustellen für die nächste Person

1. **Zuerst Machbarkeit klären**, nicht weiter Produkt-UI bauen: erhält ein
   unveränderter Client irgendeinen vorgesehenen Export beider Audiorichtungen?
   Mehrgeräte-Koordination im Quellcode nachvollziehen, nicht aus Eventnamen
   pauschale Protokollregeln ableiten. Quellenstand und Commit-IDs dokumentieren.
2. Tunnel-Herkunft/Build rekonstruieren; Quellen statt unbekannter Binärdatei
   reproduzierbar bauen. ARM64 wird durch dieses Binary nicht unterstützt.
3. Relay ist unverändert experimentell: blockierende Clients, fehlende Behandlung
   von Teil-Schreibvorgängen und SIGPIPE, Auswahl des neuesten statt eindeutig
   zugeordneten Sockets, Keepalive und Lebenszyklus nicht produktionsreif.
4. `signal_calls.py`: verschachtelte Call-ID-Antworten, globaler Sitzungszustand,
   Auflegen und Fehlerfälle prüfen. Rückkanal ist Stille, kein getesteter
   Duplex-Telefoniepfad. Keine beiden Stimmen behaupten.
5. Pipeline: Upload einer 48-kHz-WAV kann Konvertierung überspringen, wenn
   Quell- und Zielname identisch sind. Bekannter Workaround: vor Upload separat
   mit `koprotokoll.audio.to_wav16k` konvertieren.
6. Der neue Stack wurde ohne Kontoverknüpfung getestet; kein erneuter echter
   Anruf. Für weitere Live-Tests eigenes Konto und ausdrückliche Zustimmung.

Historischer Stand: [signal-zwischenstand.md](signal-zwischenstand.md).
Diese Übergabe korrigiert frühere pauschale Aussagen: weder PC-Mikrofon noch
fertiger Freisprechbetrieb wurden nachgewiesen. Ein eigener Molly-Fork oder
Handy-WebRTC-Hörer wäre ein anderer Anwendungsfall und ist nicht beschlossen.

Quellen: [Anruf-Tunnel](https://github.com/asamk/signal-cli/blob/master/docs/CALL_TUNNEL.md),
[REST-API](https://github.com/bbernhard/signal-cli-rest-api),
[Android-Audioaufnahmegrenzen](https://developer.android.com/media/platform/av-capture).
