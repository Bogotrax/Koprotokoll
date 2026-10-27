"""CalDAV-Kalenderquelle (nur lesen) für Nextcloud, iCloud, Posteo, mailbox.org, Fastmail …

Anbindung nach RFC 4791 mit einem schlanken urllib-Transport: PROPFIND zur
Server-Erkennung (RFC 6764) und ein ``calendar-query``-REPORT für den Abruf
der Termine. Die ICS-Antworten werden mit derselben Logik ausgewertet wie die
ICS-Quelle (``expand_ics``). Schreibender Zugriff (Termine anlegen) ist
bewusst nicht Teil dieser Quelle – Aufgaben/To-dos sind Thema AP3.
"""

from __future__ import annotations

import base64
import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .calendar_source import CalendarEvent
from .ics_source import TIMEOUT_S, IcsError, expand_ics

log = logging.getLogger(__name__)

DAV_NS = "DAV:"
CAL_NS = "urn:ietf:params:xml:ns:caldav"

_PRINCIPAL_PROPFIND = (
    f'<?xml version="1.0" encoding="UTF-8"?>'
    f'<d:propfind xmlns:d="{DAV_NS}"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
).encode()


class CalDavError(RuntimeError):
    """Fehler beim Erreichen oder Auslesen einer CalDAV-Quelle."""


def normalize_server_url(provider: str, server_url: str) -> str:
    """Server-URL ergänzen: https-Prefix, für Nextcloud der /remote.php/dav-Pfad."""
    url = (server_url or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    if provider == "nextcloud":
        parsed = urllib.parse.urlsplit(url)
        if parsed.path in ("", "/"):
            url = f"{parsed.scheme}://{parsed.netloc}/remote.php/dav"
    return url


def _request(method: str, url: str, *, body: bytes | None = None, depth: str | None = None,
             username: str = "", password: str = "", timeout: float = TIMEOUT_S
             ) -> tuple[int, bytes, str]:
    """DAV-Anfrage senden und (Status, Körper, End-URL) zurückgeben.

    urllib leitet PROPFIND/REPORT nicht selbst um, deshalb folgen wir Redirects
    hier von Hand. HTTP-Fehler werden als ``CalDavError`` mit deutscher Meldung
    gemeldet, damit sie direkt in der Oberfläche landen können.
    """
    headers = {"User-Agent": "Koprotokoll/1", "Accept": "text/calendar, application/xml"}
    if body is not None:
        headers["Content-Type"] = "application/xml; charset=utf-8"
    if depth is not None:
        headers["Depth"] = depth
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    for _ in range(5):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), url
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urllib.parse.urljoin(url, e.headers.get("Location", ""))
                if e.code == 303:
                    method, body = "GET", None
                continue
            if e.code == 401:
                raise CalDavError("Anmeldung fehlgeschlagen – Benutzername und Passwort "
                                  "(bei den meisten Anbietern ein App-Passwort) prüfen.") from e
            if e.code == 404:
                raise CalDavError(f"Adresse auf dem CalDAV-Server nicht gefunden: {url}") from e
            raise CalDavError(f"CalDAV-Server meldet Fehler {e.code} für {url}") from e
        except (urllib.error.URLError, OSError) as e:
            raise CalDavError(f"CalDAV-Server nicht erreichbar ({url}): {e}") from e
    raise CalDavError("Zu viele Weiterleitungen vom CalDAV-Server.")


def _propfind(url: str, props: str, *, depth: str, username: str, password: str,
              timeout: float = TIMEOUT_S) -> tuple[list[ET.Element], str]:
    """PROPFIND senden; zurück kommen die response-Elemente und die End-URL."""
    body = (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<d:propfind xmlns:d="{DAV_NS}" xmlns:c="{CAL_NS}"><d:prop>{props}</d:prop></d:propfind>')
    status, data, final_url = _request("PROPFIND", url, body=body.encode(), depth=depth,
                                       username=username, password=password, timeout=timeout)
    return _responses(status, data, "PROPFIND"), final_url


def _responses(status: int, data: bytes, what: str) -> list[ET.Element]:
    if status not in (200, 207):
        raise CalDavError(f"Unerwartete Antwort {status} des CalDAV-Servers auf {what}.")
    try:
        root = ET.fromstring(data or b"")
    except ET.ParseError as e:
        raise CalDavError(f"XML-Antwort des CalDAV-Servers nicht lesbar: {e}") from e
    return list(root.iter(f"{{{DAV_NS}}}response"))


def _href(response: ET.Element) -> str:
    node = response.find(f"{{{DAV_NS}}}href")
    return (node.text or "").strip() if node is not None else ""


def _props(response: ET.Element) -> dict[str, ET.Element]:
    """Alle prop-Kinderelemente der Antwort als Tag→Element-Schlüssel sammeln."""
    out: dict[str, ET.Element] = {}
    for prop in response.iter(f"{{{DAV_NS}}}prop"):
        for child in list(prop):
            out[child.tag] = child
    return out


def _first_href(props: dict[str, ET.Element], tag: str) -> str:
    node = props.get(tag)
    if node is None:
        return ""
    href = node.find(f"{{{DAV_NS}}}href")
    return ((href.text if href is not None else node.text) or "").strip()


def discover(server_url: str, username: str = "", password: str = "",
             timeout: float = TIMEOUT_S) -> list[dict]:
    """Alle VEVENT-Kalender eines Kontos suchen (RFC 6764 + RFC 4791).

    Zurück kommt eine Liste aus ``{"url", "name"}`` – ein Konto kann mehrere
    Kalender haben, die dann gemeinsam ausgewertet werden. Jeder Schritt hat
    einen Fallback, damit auch sparsame Server laufen; scheitert die Suche
    komplett, gibt es eine deutsche Meldung für die erweiterten Einstellungen.
    """
    base = normalize_server_url("", server_url)
    if not base.startswith(("http://", "https://")):
        raise CalDavError("Server-URL muss mit http:// oder https:// beginnen.")
    auth = {"username": username, "password": password, "timeout": timeout}

    # 1. Well-known-Endpunkt (RFC 6764) am Host-Root – sonst die Basis-URL selbst nehmen.
    parsed = urllib.parse.urlsplit(base)
    well_known = f"{parsed.scheme}://{parsed.netloc}/.well-known/caldav"
    try:
        _, _, root = _request("PROPFIND", well_known,
                              body=_PRINCIPAL_PROPFIND, depth="0", **auth)
    except CalDavError:
        root = base

    # 2. Eigener Principal des Kontos.
    try:
        responses, root = _propfind(root, "<d:current-user-principal/>", depth="0", **auth)
        principal = (_first_href(_props(responses[0]), f"{{{DAV_NS}}}current-user-principal")
                     if responses else "")
    except CalDavError:
        principal = ""
    principal_url = urllib.parse.urljoin(root, principal) if principal else root

    # 3. Kalender-Verzeichnis (calendar-home-set).
    try:
        responses, base = _propfind(principal_url, "<c:calendar-home-set/>", depth="0", **auth)
        home = (_first_href(_props(responses[0]), f"{{{CAL_NS}}}calendar-home-set")
                if responses else "")
    except CalDavError:
        home = ""
    home_url = urllib.parse.urljoin(base, home) if home else principal_url

    # 4. Kalender-Kollektionen auflisten; Adressbücher/Aufgaben aussortieren.
    responses, home_url = _propfind(
        home_url, "<d:resourcetype/><d:displayname/><c:supported-calendar-component-set/>",
        depth="1", **auth)
    collections: list[dict] = []
    for response in responses:
        props = _props(response)
        rtype = props.get(f"{{{DAV_NS}}}resourcetype")
        if rtype is None or rtype.find(f"{{{DAV_NS}}}calendar") is None:
            continue
        comps = props.get(f"{{{CAL_NS}}}supported-calendar-component-set")
        if comps is not None:
            names = [c.get("name", "") for c in comps if c.tag.endswith("}comp")]
            if names and "VEVENT" not in names:
                continue
        href = _href(response)
        if not href:
            continue
        display = props.get(f"{{{DAV_NS}}}displayname")
        name = ((display.text or "").strip() if display is not None else "") or "Kalender"
        collections.append({"url": urllib.parse.urljoin(home_url, href), "name": name})
    if not collections:
        raise CalDavError("Keine Kalender gefunden – die Kalender-URL lässt sich unter "
                          "„Erweiterte Einstellungen“ von Hand eintragen.")
    return collections


class CalDavCalendarSource:
    """Kalenderquelle für CalDAV-Konten (nur lesen, mehrere Quellen möglich).

    ``calendar_url`` zeigt direkt auf eine Kalender-Kollektion (dann ohne
    Erkennung), sonst wird ``server_url`` wie in :func:`discover` aufgelöst.
    """

    def __init__(self, name: str, label: str, *, server_url: str = "", calendar_url: str = "",
                 username: str = "", password: str = "", own_email: str = "",
                 timeout: float = TIMEOUT_S, enabled: bool = True):
        self.name = name
        self.label = label
        self.server_url = normalize_server_url("", server_url)
        self.calendar_url = (calendar_url or "").strip()
        self.username = (username or "").strip()
        self.password = password or ""
        self.own_email = (own_email or "").strip().lower()
        self.timeout = timeout
        self.enabled_flag = enabled
        self.last_error: str | None = None
        self._collections: list[dict] | None = None

    # -- CalendarSource ---------------------------------------------------------
    def enabled(self) -> bool:
        return self.enabled_flag

    def events(self, time_min: float, time_max: float) -> list[CalendarEvent]:
        try:
            out: list[CalendarEvent] = []
            for collection in self._calendar_collections():
                for ics in self._fetch_collection(collection["url"], time_min, time_max):
                    out += expand_ics(ics, time_min, time_max, self.name, self.own_email)
        except (CalDavError, IcsError) as e:
            self.last_error = str(e)
            raise
        out.sort(key=lambda e: e.start)
        self.last_error = None
        return out

    # -- Intern -----------------------------------------------------------------
    def _calendar_collections(self) -> list[dict]:
        if self.calendar_url:
            return [{"url": self.calendar_url, "name": self.label}]
        if self._collections is not None:
            return self._collections
        if not self.server_url:
            raise CalDavError("Keine Server- oder Kalender-URL hinterlegt – bitte die "
                              "Anbieterdaten unter „Erweiterte Einstellungen“ ergänzen.")
        self._collections = discover(self.server_url, self.username, self.password, self.timeout)
        log.debug("CalDAV-Erkennung für %s: %d Kalender", self.name, len(self._collections))
        return self._collections

    def _fetch_collection(self, url: str, time_min: float, time_max: float) -> list[bytes]:
        """ICS-Rohdaten einer Kalender-Kollektion per calendar-query holen."""
        start = datetime.fromtimestamp(time_min, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end = datetime.fromtimestamp(time_max, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<c:calendar-query xmlns:d="{DAV_NS}" xmlns:c="{CAL_NS}">'
            f"<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            f'<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{start}" end="{end}"/>'
            f"</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
        )
        status, data, _ = _request("REPORT", url, body=body.encode(), depth="1",
                                   username=self.username, password=self.password,
                                   timeout=self.timeout)
        out: list[bytes] = []
        for response in _responses(status, data, "REPORT"):
            payloads = [node for node in response.iter(f"{{{CAL_NS}}}calendar-data")]
            got = False
            for node in payloads:
                ics = "".join(node.itertext()).strip()
                if ics:
                    out.append(ics.encode())
                    got = True
            if not got:
                href = _href(response)
                if href:  # Server liefert nur etags – ICS-Datei einzeln abrufen
                    _, single, _ = _request("GET", urllib.parse.urljoin(url, href),
                                            username=self.username, password=self.password,
                                            timeout=self.timeout)
                    out.append(single)
        return out