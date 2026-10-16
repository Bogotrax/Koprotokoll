# Einrichtung & Betrieb

[← zurück zum README](../README.md)

## Lokales LLM für Zusammenfassungen (empfohlen)

Ohne LLM erstellt die App eine einfache extraktive Zusammenfassung. Für echte
KI-Zusammenfassungen ein lokales Modell über eine OpenAI-kompatible API bereitstellen,
z. B. mit [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:7b-instruct
python -m notetaker   # nutzt standardmäßig http://localhost:11434/v1
```

Genauso funktionieren llama.cpp (`llama-server`), LM Studio oder vLLM über `NOTETAKER_LLM_URL`.

## Meeting-Bot (Teilnehmer wie bei Fireflies)

Der Bot startet einen eigenen Chromium, öffnet den Meeting-Link, setzt seinen Namen,
schaltet Kamera und Mikrofon aus und klickt „Beitreten“ bzw. „Teilnahme anfragen“.
Im Meeting greift er die Audiospuren aller Teilnehmer direkt an den WebRTC-Verbindungen ab
(keine virtuelle Soundkarte nötig) und nimmt sie auf. Er verlässt das Meeting, wenn es endet,
er allein ist, zu lange Stille herrscht oder die Höchstdauer erreicht ist. Danach läuft die
normale Verarbeitung (Parakeet, Sprecher, Zusammenfassung).

- **Manuell**: Meet-Link in das Feld in der Seitenleiste einfügen oder `POST /api/bots`
- **Automatisch**: Google verbinden → der Bot tritt allen Kalenderterminen mit Meet-Link bei
  (abgelehnte Termine werden übersprungen), etwa eine Minute vor Beginn
- **Eigenes Google-Konto für den Bot (empfohlen)**: Google lässt anonyme Gäste oft gar nicht
  oder nur nach Freigabe herein. Einmalig `python -m notetaker bot-login` ausführen und im
  geöffneten Browser mit einem eigenen Bot-Konto anmelden.
- Läuft der Bot auf einem Server ohne Bildschirm und wird erkannt, hilft
  `NOTETAKER_BOT_HEADLESS=0` zusammen mit `xvfb-run python -m notetaker`.

Unterstützt wird zurzeit **Google Meet**. Zoom- und Teams-Links werden erkannt, der Beitritt
ist aber noch nicht umgesetzt. Mit `platform: "web"` nimmt der Bot den Ton einer beliebigen
Webseite auf (z. B. Webinar).

> Hinweis: Aufzeichnungen nur mit Einverständnis der Teilnehmenden. Der Bot tritt sichtbar
> unter seinem Namen bei.

## Google Kalender & Gmail einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt anlegen und die
   **Google Calendar API** und **Gmail API** aktivieren.
2. OAuth-Zustimmungsbildschirm konfigurieren (intern oder extern mit dir als Testnutzer).
3. Anmeldedaten → OAuth-Client-ID → **Webanwendung**, autorisierte Weiterleitungs-URI:
   `http://localhost:8000/api/integrations/google/callback` (bzw. `<NOTETAKER_PUBLIC_URL>/api/integrations/google/callback`).
4. Client-ID und -Secret in der App unter **Integrationen & API** eintragen (oder per
   `NOTETAKER_GOOGLE_CLIENT_ID` / `NOTETAKER_GOOGLE_CLIENT_SECRET`) und **Mit Google verbinden**.

Berechtigungen: Kalender nur lesen, Gmail nur senden. Nach jedem Bot-Meeting geht die
Zusammenfassung an dich (optional auch an alle Termin-Teilnehmer).

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
| `NOTETAKER_PUBLIC_URL` | `http://localhost:8000` | Basis-URL für OAuth-Weiterleitung und Links in E-Mails |
| `NOTETAKER_REQUIRE_KEY` | `0` | `1` = API-Schlüssel auch für lokale Anfragen |
| `NOTETAKER_GOOGLE_CLIENT_ID` / `…_SECRET` | – | OAuth-Client (alternativ in der Oberfläche) |
| `NOTETAKER_BOT_NAME` | `Notetaker` | Anzeigename des Bots im Meeting |
| `NOTETAKER_BOT_HEADLESS` | `1` | `0` = sichtbares Browserfenster |
| `NOTETAKER_BOT_CHROME` | – | Pfad zu Chrome/Chromium statt Playwright-Chromium |
| `NOTETAKER_BOT_SILENCE_MINUTES` | `10` | Meeting verlassen nach so langer Stille |
| `NOTETAKER_BOT_MAX_MINUTES` | `180` | Höchstdauer einer Aufnahme |
| `NOTETAKER_BOT_ADMIT_MINUTES` | `10` | Wartezeit auf Einlass |
