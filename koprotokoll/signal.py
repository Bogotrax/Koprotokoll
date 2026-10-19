"""Signal-Integration: Zusammenfassungen und Statusmeldungen per Signal verschicken.

Läuft über den lokalen signal-cli-Dienst (siehe ``docker-compose.signal.yml``), der als
Sekundärgerät mit dem Signal-Konto verbunden wird. Es verlässt nichts den Rechner.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import Settings
from .db import Database


class SignalError(RuntimeError):
    pass


def _http(method: str, url: str, *, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise SignalError(f"Signal-API {e.code}: {detail}") from e


def normalize_number(number: str) -> str:
    """Räumt eine Rufnummer auf und prüft das internationale Format (+49…)."""
    n = number.strip().replace(" ", "").replace("-", "").replace("/", "").replace("(", "").replace(")", "")
    if not n.startswith("+") or not n[1:].isdigit():
        raise SignalError("Bitte die Nummer im internationalen Format angeben, z. B. +491701234567")
    return n


class SignalClient:
    """Spricht mit dem signal-cli-Dienst und hält die Signal-Einstellungen.

    Einstellungen (in der DB unter ``signal_settings``): ``send_recap`` (Zusammenfassung
    nach dem Meeting schicken) und ``recipient`` (Empfängernummer im Format +49…).
    """

    def __init__(self, settings: Settings, db: Database):
        self.s, self.db = settings, db

    # -- Einstellungen -------------------------------------------------------------
    def settings(self) -> dict:
        return {"send_recap": True, "recipient": "", **self.db.get_kv("signal_settings", {})}

    def update_settings(self, **values) -> dict:
        merged = {**self.settings(), **values}
        if merged.get("recipient"):
            merged["recipient"] = normalize_number(merged["recipient"])
        self.db.set_kv("signal_settings", merged)
        return self.settings()

    # -- Status --------------------------------------------------------------------
    def accounts(self) -> list[str]:
        data = _http("GET", f"{self.s.signal_api_url}/v1/accounts")
        return [str(a) for a in data] if isinstance(data, list) else []

    def connected(self) -> bool:
        try:
            return bool(self.accounts())
        except (SignalError, OSError):
            return False

    def status(self) -> dict:
        accounts: list[str] = []
        try:
            accounts = self.accounts()
        except (SignalError, OSError):
            pass
        return {"connected": bool(accounts), "number": accounts[0] if accounts else None,
                "api_url": self.s.signal_api_url, "settings": self.settings()}

    # -- Versand ---------------------------------------------------------------------
    # -- Verknüpfung (QR-Code) ------------------------------------------------------
    def qr_png(self) -> bytes:
        """Liefert den QR-Code zum Verknüpfen des Signal-Kontos als PNG-Bytes.

        Ruft ``/v1/qrcodelink`` des signal-cli-Dienstes auf; der QR-Code wird mit dem
        Signal-Handy gescannt (Einstellungen → Verbundene Geräte → +), damit dieser
        Dienst als Sekundärgerät verbunden wird.
        """
        req = urllib.request.Request(
            f"{self.s.signal_api_url}/v1/qrcodelink?device_name=koprotokoll")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise SignalError(f"Signal-API {e.code}: {detail}") from e
        except OSError as e:
            raise SignalError(
                "Signal-Dienst nicht erreichbar – gestartet? "
                "(docker compose -f docker-compose.signal.yml up -d)") from e

    # -- Versand ---------------------------------------------------------------------

    def send(self, text: str, to: str | None = None) -> dict:
        """Schickt eine Textnachricht per Signal an ``to`` (Default: hinterlegte Nummer)."""
        to = to or self.settings().get("recipient") or ""
        if not to:
            raise SignalError("Keine Empfängernummer für Signal hinterlegt")
        to = normalize_number(to)
        accounts = self.accounts()
        if not accounts:
            raise SignalError("Signal ist nicht verbunden – bitte das Signal-Konto verknüpfen")
        return _http("POST", f"{self.s.signal_api_url}/v2/send",
                     body={"message": text, "number": accounts[0], "recipients": [to]})