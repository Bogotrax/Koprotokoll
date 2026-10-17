# API, Webhooks & MCP

[← zurück zum README](../README.md)

Vollständige, interaktive Doku: **http://127.0.0.1:8000/docs** (OpenAPI unter `/openapi.json`).

Anfragen direkt vom eigenen Rechner brauchen keinen Schlüssel. Von anderen Rechnern, hinter
einem Reverse-Proxy oder aus Tools wie n8n/Zapier/Make: API-Schlüssel in der Oberfläche oder per
`python -m koprotokoll create-key n8n` erzeugen und mitsenden. Mit `KOPROTOKOLL_REQUIRE_KEY=1` gilt
die Schlüsselpflicht auch lokal.

```bash
KEY=kp_…
# Bot in ein Meeting schicken
curl -X POST http://localhost:8000/api/bots -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" -d '{"meeting_url": "https://meet.google.com/abc-defg-hij"}'
# Meetings, Transkript, Suche, Export
curl -H "Authorization: Bearer $KEY" http://localhost:8000/api/meetings
curl -H "Authorization: Bearer $KEY" http://localhost:8000/api/meetings/1
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/search?q=budget"
curl -H "Authorization: Bearer $KEY" "http://localhost:8000/api/meetings/1/export?format=md"
```

| Bereich | Endpunkte |
|---|---|
| Meetings | `GET/POST /api/meetings`, `GET/PATCH/DELETE /api/meetings/{id}`, `…/audio`, `…/export`, `…/summarize`, `…/ask`, `…/bookmarks` |
| Suche | `GET /api/search?q=` |
| Bots | `POST/GET /api/bots`, `GET/DELETE /api/bots/{id}` |
| Webhooks | `GET/POST /api/webhooks`, `DELETE /api/webhooks/{id}`, `POST /api/webhooks/{id}/test` |
| Transkription (OpenAI-kompatibel) | `POST /v1/audio/transcriptions` |
| Schlüssel | `GET/POST /api/keys`, `DELETE /api/keys/{id}`, `GET /api/tokens` (Schlüssel prüfen) |
| Google | `GET/DELETE /api/integrations/google`, `PUT …/client`, `GET …/connect`, `PATCH …/settings`, `POST …/sync` |

## Diktieren: `/v1/audio/transcriptions` (OpenAI-kompatibel)

Für kurze Aufnahmen – etwa aus **Kodiktat** oder jedem Programm, das die OpenAI-Transkriptions-API
spricht. Es wird **kein Meeting angelegt**, keine Sprechererkennung und keine Zusammenfassung
gemacht; die Datei liegt nur während der Anfrage in `data/tmp` und wird danach gelöscht.

| Feld (multipart) | Bedeutung |
|---|---|
| `file` | Pflicht. WAV (16 kHz wird direkt gelesen), sonst alles, was ffmpeg kann (mp3, m4a, ogg, webm …) |
| `response_format` | `json` (Standard, `{"text": …}`), `text`, `verbose_json` (mit `segments`, `start`/`end` in Sekunden), `srt`, `vtt` |
| `language` | optional, wird in `verbose_json` zurückgegeben; Parakeet erkennt die Sprache selbst |
| `model`, `prompt`, `temperature` | werden angenommen und ignoriert – es gibt die eine eingestellte Engine |

```bash
curl http://localhost:8000/v1/audio/transcriptions -H "Authorization: Bearer $KEY" \
     -F file=@notiz.wav -F response_format=text
```

`GET /api/tokens` antwortet mit `200`, wenn der Schlüssel gültig ist, sonst mit `401` – so prüft
Kodiktat die Verbindung.

**Kodiktat einrichten:** Anbieter „Heimserver“ wählen, als **Basis-URL** die Adresse des
Koprotokoll-Servers ohne `/v1` eintragen (z. B. `http://192.168.1.20:8000`) und als **Schlüssel**
einen API-Schlüssel aus „Integrationen & API“ (`kp_…`). Läuft Koprotokoll auf einem anderen
Rechner, ihn mit `--host 0.0.0.0` starten oder hinter einen Reverse-Proxy mit HTTPS stellen.

## Webhooks

Ereignisse `meeting.completed`, `meeting.failed`, `bot.status` werden als `POST` mit
`{"event", "created_at", "data"}` zugestellt. Prüfung der Signatur:

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Koprotokoll-Signature"])
```

## MCP-Server (Claude, Cursor & Co.)

```bash
claude mcp add koprotokoll -- python -m koprotokoll mcp
# entfernter Server: KOPROTOKOLL_URL=https://… KOPROTOKOLL_API_KEY=kp_… python -m koprotokoll mcp
```

Werkzeuge: `list_meetings`, `search_meetings`, `get_summary`, `get_transcript`, `ask_meeting`, `send_bot`.
