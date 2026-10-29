# Einrichtung & Betrieb

[← zurück zum README](../README.md)

## Lokales LLM für Zusammenfassungen (empfohlen)

Ohne LLM erstellt die App eine einfache extraktive Zusammenfassung. Für echte
KI-Zusammenfassungen ein lokales Modell über eine OpenAI-kompatible API bereitstellen,
z. B. mit [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:7b-instruct
python -m koprotokoll   # nutzt standardmäßig http://localhost:11434/v1
```

Genauso funktionieren llama.cpp (`llama-server`), LM Studio oder vLLM über `KOPROTOKOLL_LLM_URL`.

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
  oder nur nach Freigabe herein. Einmalig `python -m koprotokoll bot-login` ausführen und im
  geöffneten Browser mit einem eigenen Bot-Konto anmelden.
- Läuft der Bot auf einem Server ohne Bildschirm und wird erkannt, hilft
  `KOPROTOKOLL_BOT_HEADLESS=0` zusammen mit `xvfb-run python -m koprotokoll`.

Unterstützt wird zurzeit **Google Meet** und **Jitsi** (meet.jit.si sowie eigene Instanzen
über `KOPROTOKOLL_JITSI_HOSTS`, kommagetrennte Hostnames). Zoom- und Teams-Links werden
erkannt, der Beitritt ist aber noch nicht umgesetzt. Mit `platform: "web"` nimmt der Bot den
Ton einer beliebigen Webseite auf (z. B. Webinar).

> Hinweis: Aufzeichnungen nur mit Einverständnis der Teilnehmenden. Der Bot tritt sichtbar
> unter seinem Namen bei.

## Google Kalender & Gmail einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt anlegen und die
   **Google Calendar API** und **Gmail API** aktivieren.
2. OAuth-Zustimmungsbildschirm konfigurieren (intern oder extern mit dir als Testnutzer).
3. Anmeldedaten → OAuth-Client-ID → **Webanwendung**, autorisierte Weiterleitungs-URI:
   `http://localhost:8000/api/integrations/google/callback` (bzw. `<KOPROTOKOLL_PUBLIC_URL>/api/integrations/google/callback`).
4. Client-ID und -Secret in der App unter **Integrationen & API** eintragen (oder per
   `KOPROTOKOLL_GOOGLE_CLIENT_ID` / `KOPROTOKOLL_GOOGLE_CLIENT_SECRET`) und **Mit Google verbinden**.

Berechtigungen: Kalender nur lesen, Gmail nur senden. Nach jedem Bot-Meeting geht die
Zusammenfassung an dich (optional auch an alle Termin-Teilnehmer).

## Microsoft Kalender & Outlook einrichten

> **Kurz:** Für Privatkonten (outlook.com, live.com) und Microsoft-365-Konten. Die Anmeldung
> erfolgt per **„Mit Microsoft verbinden"** (OAuth) – ganz ohne Kalender-Links zu kopieren.

1. Im [Azure-Portal](https://portal.azure.com) anmelden – geht auch mit einem privaten
   Microsoft- oder GitHub-Konto, ein Azure-Abonnement ist nicht nötig.
2. **App-Registrierungen** → **Neue Registrierung**:
   - **Name:** z. B. `Koprotokoll`
   - **Unterstützte Kontotypen:** unbedingt
     **„Any Entra ID Tenant + Personal Microsoft accounts"** („Alle Mandanten + persönliche
     Microsoft-Konten") wählen – sonst schlägt die Anmeldung mit privaten Konten fehl
     (Fehler „Mandant … nicht vorhanden").
   - **Umleitungs-URI:** Plattform **„Web"**, URI
     `http://localhost:8000/api/integrations/microsoft/callback`
     (bzw. `<KOPROTOKOLL_PUBLIC_URL>/api/integrations/microsoft/callback`)
3. **Client-ID** kopieren (Übersicht → „Application (client) ID").
4. **Client-Secret** anlegen: **Certificates & secrets** → **New client secret** →
   den **Wert** (Spalte „Value") **sofort kopieren**.
   > **Falle:** In der Tabelle stehen „Secret ID" und „Value" nebeneinander. Gebraucht wird
   > der **Wert** – die Secret ID führt zum Fehler `AADSTS7000215: Invalid client secret`.
   > Der Wert wird nur einmal angezeigt; ist er verloren, hilft nur ein neues Secret.
5. **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions** → `Calendars.Read` und `Mail.Send` hinzufügen.
6. In Koprotokoll unter **Integrationen & API** → Microsoft-Karte → **Erweiterte Einstellungen**
   Client-ID und Secret eintragen (oder per `KOPROTOKOLL_MICROSOFT_CLIENT_ID` /
   `KOPROTOKOLL_MICROSOFT_CLIENT_SECRET`) und **Mit Microsoft verbinden**.

Berechtigungen: Kalender nur lesen, Mail nur senden. Die Termine der nächsten **7 Tage**
siehst du direkt auf der Startseite – inklusive Hinweis, ob dafür ein Bot eingeplant wird.
Kurz vor Terminbeginn (~15 Minuten) legt Koprotokoll den Bot automatisch an.

> **Für Organisationen:** Die App-Registrierung ist einmalig pro Mandant – am besten durch
> die IT erstellen lassen. Privatnutzende registrieren ihre eigene App (Schritte oben).

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `KOPROTOKOLL_DATA` | `./data` | Datenbank, Mediendateien, Modelle |
| `KOPROTOKOLL_ASR_MODEL` | `nemo-parakeet-tdt-0.6b-v3` | z. B. `nemo-parakeet-tdt-0.6b-v2` (nur Englisch) |
| `KOPROTOKOLL_ASR_QUANT` | `int8` | leer lassen für fp32 (genauer, größer) |
| `KOPROTOKOLL_ASR_PATH` | – | lokaler Modellordner statt Download |
| `KOPROTOKOLL_DIARIZATION` | `1` | `0` schaltet Sprechererkennung ab |
| `KOPROTOKOLL_DIARIZATION_THRESHOLD` | `0.8` | kleiner = mehr Sprecher |
| `KOPROTOKOLL_LLM` | `1` | `0` = nur Offline-Heuristik |
| `KOPROTOKOLL_LLM_URL` | `http://localhost:11434/v1` | OpenAI-kompatibler Endpunkt |
| `KOPROTOKOLL_LLM_MODEL` | `qwen2.5:7b-instruct` | Modellname |
| `KOPROTOKOLL_PUBLIC_URL` | `http://localhost:8000` | Basis-URL für OAuth-Weiterleitung und Links in E-Mails |
| `KOPROTOKOLL_REQUIRE_KEY` | `0` | `1` = API-Schlüssel auch für lokale Anfragen |
| `KOPROTOKOLL_GOOGLE_CLIENT_ID` / `…_SECRET` | – | OAuth-Client (alternativ in der Oberfläche) |
| `KOPROTOKOLL_MICROSOFT_CLIENT_ID` / `…_SECRET` | – | OAuth-Client (alternativ unter „Erweiterte Einstellungen“) |
| `KOPROTOKOLL_BOT_NAME` | `Koprotokoll` | Anzeigename des Bots im Meeting |
| `KOPROTOKOLL_BOT_HEADLESS` | `1` | `0` = sichtbares Browserfenster |
| `KOPROTOKOLL_BOT_CHROME` | – | Pfad zu Chrome/Chromium statt Playwright-Chromium |
| `KOPROTOKOLL_BOT_SILENCE_MINUTES` | `1` | Meeting verlassen nach so langer Stille |
| `KOPROTOKOLL_BOT_MAX_MINUTES` | `180` | Höchstdauer einer Aufnahme |
| `KOPROTOKOLL_BOT_ALONE_SECONDS` | `15` | Allein-Erkennung-Zeitraum (Sekunden) |
| `KOPROTOKOLL_BOT_ADMIT_MINUTES` | `10` | Wartezeit auf Einlass |
| `KOPROTOKOLL_JITSI_HOSTS` | – | Eigene Jitsi-Instanzen (kommagetrennte Hostnames) |
