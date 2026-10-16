# API, Webhooks & MCP

[← zurück zum README](../README.md)

Vollständige, interaktive Doku: **http://127.0.0.1:8000/docs** (OpenAPI unter `/openapi.json`).

Anfragen direkt vom eigenen Rechner brauchen keinen Schlüssel. Von anderen Rechnern, hinter
einem Reverse-Proxy oder aus Tools wie n8n/Zapier/Make: API-Schlüssel in der Oberfläche oder per
`python -m notetaker create-key n8n` erzeugen und mitsenden. Mit `NOTETAKER_REQUIRE_KEY=1` gilt
die Schlüsselpflicht auch lokal.

```bash
KEY=nt_…
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
| Schlüssel | `GET/POST /api/keys`, `DELETE /api/keys/{id}` |
| Google | `GET/DELETE /api/integrations/google`, `PUT …/client`, `GET …/connect`, `PATCH …/settings`, `POST …/sync` |

## Webhooks

Ereignisse `meeting.completed`, `meeting.failed`, `bot.status` werden als `POST` mit
`{"event", "created_at", "data"}` zugestellt. Prüfung der Signatur:

```python
import hmac, hashlib
expected = "sha256=" + hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Notetaker-Signature"])
```

## MCP-Server (Claude, Cursor & Co.)

```bash
claude mcp add notetaker -- python -m notetaker mcp
# entfernter Server: NOTETAKER_URL=https://… NOTETAKER_API_KEY=nt_… python -m notetaker mcp
```

Werkzeuge: `list_meetings`, `search_meetings`, `get_summary`, `get_transcript`, `ask_meeting`, `send_bot`.
