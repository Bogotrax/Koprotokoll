# Sicherheit

Koprotokoll verarbeitet vertrauliche Gespräche. Sicherheitsprobleme nehmen wir deshalb ernst.

## Lücke melden

Bitte **kein öffentliches Issue** anlegen. Nutze stattdessen die private Meldung über GitHub:
**Security → Report a vulnerability** in diesem Repository. Beschreibe, was passiert, wie man es
nachstellt und welche Auswirkungen du siehst. Wir melden uns so schnell wie möglich.

## Hinweise für den Betrieb

- Standardmäßig lauscht der Server nur auf `127.0.0.1`. Wer ihn im Netz verfügbar macht
  (`--host 0.0.0.0` oder hinter einem Reverse-Proxy), sollte `KOPROTOKOLL_REQUIRE_KEY=1` setzen
  und HTTPS verwenden.
- API-Schlüssel und Webhook-Secrets werden nur einmal angezeigt; Schlüssel werden nur als
  SHA-256-Hash gespeichert.
- Das Datenverzeichnis (`./data`) enthält Aufnahmen, Transkripte und Google-Tokens – schütze
  es wie andere vertrauliche Daten (Festplattenverschlüsselung, Backups).
