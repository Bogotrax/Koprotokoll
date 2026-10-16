"""API-Schlüssel für externe Anbindungen.

Regel: Anfragen direkt von diesem Rechner (Loopback, ohne Proxy-Header) dürfen ohne
Schlüssel zugreifen – so funktioniert die lokale Weboberfläche ohne Einrichtung.
Alle anderen Anfragen brauchen ``Authorization: Bearer nt_…`` (oder ``X-API-Key``).
Mit ``NOTETAKER_REQUIRE_KEY=1`` gilt die Pflicht auch lokal.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import HTTPException, Request

from .db import Database

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(db: Database, name: str) -> dict:
    key = "nt_" + secrets.token_urlsafe(32)
    key_id = db.add_api_key(name, key[:10], hash_key(key))
    return {"id": key_id, "name": name, "key": key}


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK and not any(h in request.headers for h in PROXY_HEADERS)


def presented_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key") or request.query_params.get("key")


def require_auth(request: Request) -> None:
    db: Database = request.app.state.db
    key = presented_key(request)
    if key and db.find_api_key(hash_key(key)):
        return
    if not key and is_local(request) and os.environ.get("NOTETAKER_REQUIRE_KEY", "0") != "1":
        return
    raise HTTPException(401, "Gültiger API-Schlüssel erforderlich (Authorization: Bearer nt_…)",
                        headers={"WWW-Authenticate": "Bearer"})
