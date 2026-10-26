# Signal-Stacks: Live-Konfiguration vs. Git-Stand (Vergleich vom 25.10.2026)

Zweck: Prüfen, ob der laufende `koprotokoll-signal-research`-Stack gegenüber dem letzten
Git-Commit (`050a614` auf `experiment/signal-handoff`) einen dokumentierungswürdigen
Mehrwert hat. Methode: `docker inspect` der laufenden Container gegen `git show` der
committed Compose-Dateien, Zeile für Zeile.

## 1. Produktiv-Container `koprotokoll-signal` vs. `docker-compose.signal.yml`

| Aspekt | Live (docker inspect) | Git (docker-compose.signal.yml) | Differenz |
|---|---|---|---|
| Image | `koprotokoll-signal:pipe` | `koprotokoll-signal:pipe` | identisch |
| Einstieg | `/init`, `MODE=json-rpc` | `/init` (Default), `MODE=json-rpc` | identisch |
| Volume | `data/signal-cli:/home/.local/share/signal-cli` | `./data/signal-cli:/home/.local/share/signal-cli` | identisch |
| Ports | `8080→8090`, `6001→6001`, **`6003→6003`**, **`9002→9002`** | `8090:8080`, `127.0.0.1:6001:6001` | ⚠️ Live hat 6003 + 9002 zusätzlich offen (alle Interfaces); im Commit nur localhost-6001 |
| Restart | `no` | `unless-stopped` | ⚠️ geringfügig |

Bewertung: Die Zusatz-Ports sind **Experiment-Reste** (gehören zum Call-Tunnel/Lab-Test),
kein Mehrwert – im Commit-Stand enger und sicherer dokumentiert.

## 2. Research-Stack `koprotokoll-signal-research` vs. `docker-compose.signal-lab.yml`

| Aspekt | Live (signal-research) | Git (signal-lab.yml) | Differenz / Mehrwert? |
|---|---|---|---|
| Services | `signal`, `audio-relay`, `rpc-bridge` | `signal`, `rpc`, `relay` | nur Namen |
| Image | `koprotokoll-signal:handoff-20261020` (alte Generation, Image-ID `2da45ee335c1` inzwischen verwaist) | `koprotokoll-signal:handoff-20261020` (Neubau aus `./docker/signal-lab`) | Pfad-Folge der Image-Generation |
| Relay-Einstieg | `perl /opt/koprotokoll/audio-relay.pl` | `perl /opt/signal-lab/audio-relay.pl` | alter Pfad im alten Image – **kein Mehrwert** |
| RPC-Brücke | `socat TCP-LISTEN:6003,... TCP:127.0.0.1:6001` | identischer socat-Befehl | identisch |
| Netzwerk | relay/rpc teilen Netz von `signal` | `network_mode: service:signal` | identisch (nur anders geschrieben) |
| Volumes | `..._signal-sockets:/tmp`, `..._signal-account:/home/.local/share/signal-cli` | `signal-lab-tmp:/tmp`, `signal-lab-account:/home/.local/share/signal-cli` | gleiches Schema, andere Präfixe |
| Konten | **echtes Konto** angebunden | bewusst leere Labor-Konten („keine vorhandenen Konten") | einziger substanzieller Unterschied – Konto ist aber irrelevant (Nutzerentscheid) |
| Ports | `127.0.0.1: 8091→8080`, `6004→6003`, `9003→9002` | `127.0.0.1: 18090→8080`, `16003→6003`, `19002→9002` | nur andere Host-Ports (Labor bewusst verschoben) |
| Restart | `no` | `no` | identisch |
| Healthcheck | alle 3 Container: ja | `signal`: ja, `rpc`: explizit aus, `relay`: erbt | minimal |
| Build | läuft vom Image | `build: context: ./docker/signal-lab` | Git-Stand hat **mehr** (Dockerfile, Quellen) |

## 3. Urteil

Der Live-Stack ist inhaltlich **das, was `docker-compose.signal-lab.yml` beschreibt**:
drei Services (Signal-Kern + socat-RPC-Brücke + Perl-Audio-Relay), gemeinsames Socket-Volume,
geteiltes Netzwerk. Alle Abweichungen sind kosmetisch (Namen, Ports), image-generationsbedingt
(Relay-Pfad) oder konto-bezogen (irrelevant).

**Mehrwert des Live-Stands gegenüber dem letzten Commit: keiner.**

Damit ist der Signal-Bauplan auf GitHub vollständig:
- `experiment/signal-handoff`: `docker-compose.signal.yml`, `docker-compose.signal-lab.yml`,
  `Dockerfile`, Signal-Tunnel-Quellen, `signal.py`, `signal_calls.py`, Tests, Doku
- `agent/signal-recap`: `docker-compose.signal.yml`, `signal.py`

Konsequenz: Rekonstruktion von `docker-compose.signal-research.yml` ist **nicht nötig**
(wurde verworfen). Der Stack kann eingestampft werden; Neustart jederzeit aus dem
GitHub-Bauplan möglich (Neubau des Images aus `docker/signal-lab`).

## 4. Nachtrag (27.10.2026)

Die gesamte Signal-Arbeit ist in der gemeinsamen Branch `signal` konsolidiert
(Stand `050a614` plus dieses Dokument). Die im Text genannten Branches
`experiment/signal-handoff` und `agent/signal-recap` sind unter `archive/…`
archiviert. `signal` ist der einzige aktive Signal-Ast und wird bewusst
**nicht** nach `main` gemerget.
