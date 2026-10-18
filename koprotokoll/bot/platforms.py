"""Plattform-Adapter: Meeting-Links erkennen, beitreten, Ende erkennen."""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Iterable

PATTERNS = {
    "google_meet": re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:\?[^\s\"'<>]*)?", re.I),
    "zoom": re.compile(r"https://(?:[\w-]+\.)?zoom\.us/(?:j|my|w)/[^\s\"'<>]+", re.I),
    "teams": re.compile(r"https://teams\.(?:microsoft|live)\.com/(?:l/meetup-join|(?:v2/)?meet)/(?!meet-now(?:[/?#]|$))[^\s\"'<>]+", re.I),
    "jitsi": re.compile(r"https://meet\.jit\.si/[^\s\"'<>]+", re.I),
}
# Plattformen, denen der Bot beitreten kann. Zoom wird erkannt, aber noch nicht unterstützt.
SUPPORTED = {"google_meet", "web", "jitsi", "teams"}


class BotError(RuntimeError):
    pass


def jitsi_pattern(jitsi_hosts: Iterable[str] = ()) -> re.Pattern:
    """Regex für Jitsi-Links: meet.jit.si plus konfigurierte eigene Instanzen."""
    hosts = ["meet\\.jit\\.si"]
    for host in jitsi_hosts:
        host = host.strip().lower()
        if host and host != "meet.jit.si":
            hosts.append(re.escape(host))
    return re.compile(r"https://(?:" + "|".join(hosts) + r")/[^\s\"'<>]+", re.I)


def detect_platform(url: str, jitsi_hosts: Iterable[str] = ()) -> str | None:
    for name, pattern in PATTERNS.items():
        if name == "jitsi":
            pattern = jitsi_pattern(jitsi_hosts)
        if pattern.match(url.strip()):
            return name
    return None


# Bekannte Meeting-Seiten, deren URLs aber keine Beitritts-URL sind – für klare Fehlermeldungen.
LINK_HINTS = (
    (re.compile(r"https://teams\.(?:microsoft|live)\.com/", re.I),
     "Das ist kein Teams-Beitrittslink. Kopiere den Einladungslink aus der Besprechung "
     "(…/l/meetup-join/… bzw. …/meet/…) – die Adresszeile der Vorbereitungsseite "
     "(z. B. „meet-now“) funktioniert nicht."),
    (re.compile(r"https://(?:[\w-]+\.)?zoom\.us/", re.I),
     "Zoom-Links werden erkannt, aber der Beitritt ist noch nicht umgesetzt."),
)


def link_hint(url: str) -> str | None:
    """Tipp, wenn die URL von einer bekannten Meeting-Seite stammt, aber kein Beitrittslink ist."""
    for pattern, hint in LINK_HINTS:
        if pattern.match(url.strip()):
            return hint
    return None


def find_meeting_url(event: dict, jitsi_hosts: Iterable[str] = ()) -> tuple[str, str] | None:
    """Meeting-Link aus einem Kalendertermin ziehen → (url, plattform).

    Akzeptiert Google-spezifische Felder (``hangoutLink``, ``conferenceData``)
    sowie die generischen Felder ``location``/``description`` – so finden
    ICS-, Graph- und Google-Termine ihren Meeting-Link.
    """
    candidates = [event.get("hangoutLink") or "", event.get("meeting_url") or ""]
    for ep in (event.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            candidates.append(ep.get("uri") or "")
    candidates += [event.get("location") or "", event.get("description") or ""]
    # Zwei Durchläufe: erst nach unterstützten Plattformen suchen (z. B. Jitsi in
    # der Beschreibung, obwohl ein Teams-Link im Termin steht), dann alle anderen.
    for want_supported in (True, False):
        for text in candidates:
            for name, pattern in PATTERNS.items():
                if name == "jitsi":
                    pattern = jitsi_pattern(jitsi_hosts)
                m = pattern.search(text)
                if m and (not want_supported or name in SUPPORTED):
                    return m.group(0), name
    return None


class Adapter:
    """Basisklasse: beliebige Webseite öffnen und deren Ton aufnehmen (Plattform „web“)."""

    def join(self, page, url: str, bot_name: str, admit_timeout: float, on_status) -> None:
        page.goto(url, wait_until="domcontentloaded")

    def ended(self, page) -> bool:
        return page.is_closed()

    def alone(self, page) -> bool:
        return False

    def leave(self, page) -> None:
        pass


class GoogleMeet(Adapter):
    """Google Meet über die Weboberfläche (englische UI erzwungen für stabile Selektoren)."""

    JOIN = re.compile(r"^(ask to join|join now|join anyway|switch here)$", re.I)
    LEAVE = re.compile(r"leave call", re.I)
    DENIED = re.compile(r"(you can't join this video call|denied your request|no one responded|"
                        r"meeting has ended|check your meeting code|you've been removed|this call has ended)", re.I)
    ENDED = re.compile(r"(you left the meeting|you've been removed|the call ended|this call has ended|"
                       r"return to home screen|meeting has ended)", re.I)
    ALONE = re.compile(r"(you're the only one here|no one else is here)", re.I)

    def join(self, page, url: str, bot_name: str, admit_timeout: float, on_status) -> None:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        query["hl"] = ["en"]
        page.goto(urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, doseq=True))),
                  wait_until="domcontentloaded")
        self._dismiss(page)

        name_box = page.locator("input[type=text][aria-label*='name' i], input[placeholder*='name' i]").first
        if _visible(name_box, 8000):
            name_box.fill(bot_name)

        for label in ("Turn off microphone", "Turn off camera"):
            btn = page.get_by_role("button", name=re.compile(label, re.I)).first
            if _visible(btn, 3000):
                btn.click()

        join_btn = page.get_by_role("button", name=self.JOIN).first
        if not _visible(join_btn, 20000):
            raise BotError(self._body_hint(page) or "Beitreten-Schaltfläche nicht gefunden")
        asked = "ask" in (join_btn.inner_text() or "").lower()
        join_btn.click()
        if asked:
            on_status("waiting_admit")

        deadline = time.time() + admit_timeout
        leave = page.get_by_role("button", name=self.LEAVE).first
        while time.time() < deadline:
            if _visible(leave, 1000):
                self._dismiss(page)
                return
            hint = self._body_hint(page, self.DENIED)
            if hint:
                raise BotError(f"Beitritt abgelehnt: {hint}")
        raise BotError("Niemand hat den Bot rechtzeitig in das Meeting gelassen")

    def ended(self, page) -> bool:
        if page.is_closed():
            return True
        if _visible(page.get_by_role("button", name=self.LEAVE).first, 500):
            return False
        return bool(self._body_hint(page, self.ENDED))

    def alone(self, page) -> bool:
        return bool(self._body_hint(page, self.ALONE))

    def leave(self, page) -> None:
        btn = page.get_by_role("button", name=self.LEAVE).first
        if _visible(btn, 1000):
            btn.click()

    @staticmethod
    def _dismiss(page) -> None:
        for name in ("Got it", "Dismiss", "Close"):
            btn = page.get_by_role("button", name=name, exact=True).first
            if _visible(btn, 300):
                try:
                    btn.click(timeout=1000)
                except Exception:  # noqa: BLE001 – Hinweisdialoge sind optional
                    pass

    @staticmethod
    def _body_hint(page, pattern: re.Pattern | None = None) -> str | None:
        try:
            text = page.inner_text("body", timeout=2000)
        except Exception:  # noqa: BLE001
            return None
        m = (pattern or GoogleMeet.DENIED).search(text)
        return m.group(0) if m else None


class Jitsi(Adapter):
    """Jitsi Meet (meet.jit.si und eigene Instanzen) über die Weboberfläche.

    Englische UI wird über URL-Parameter erzwungen, damit die Selektoren stabil
    bleiben. Name, stummgeschaltetes Mikrofon und ausgeschaltete Kamera werden
    ebenfalls über die URL-Konfiguration gesetzt; die Prejoin-Seite dient als
    Rückfallebene, falls eine Instanz die URL-Overrides ignoriert.
    """

    JOIN = re.compile(r"^(join meeting|join now|join)$", re.I)
    LEAVE = re.compile(r"^(leave|hangup|leave the meeting|leave meeting)$", re.I)
    DENIED = re.compile(r"(password (is )?(incorrect|required|protected)|this conference is password"
                        r"|you (have been )?(kicked|banned)|not allowed|unable to join"
                        r"|conference not found|room (name )?(is )?invalid)", re.I)
    ENDED = re.compile(r"(you have left the meeting|you've left the meeting|the meeting has been ended"
                       r"|meeting has ended|this conference (has )?ended|you have been kicked)", re.I)
    ALONE = re.compile(r"(you are the only one (in the meeting|here)|no one else is here)", re.I)

    def join(self, page, url: str, bot_name: str, admit_timeout: float, on_status) -> None:
        page.goto(self._join_url(url, bot_name), wait_until="domcontentloaded")
        self._dismiss(page)

        # Prüfe zuerst, ob der Beitritt direkt abgelehnt wird (z. B. Passwortschutz).
        hint = self._body_hint(page, self.DENIED)
        if hint:
            raise BotError(f"Beitritt abgelehnt: {hint}")

        # Direktbeitritt prüfen: Falls URL-Overrides greifen, sind wir schon im Meeting.
        leave = page.get_by_role("button", name=self.LEAVE).first
        if _visible(leave, 3000):
            self._dismiss(page)
            return

        # Rückfallebene: Prejoin-Seite (falls URL-Overrides von der Instanz ignoriert werden).
        name_box = page.locator("input[type=text][aria-label*='name' i], input[placeholder*='name' i], "
                                "input#premeeting-name-input").first
        if _visible(name_box, 8000):
            name_box.fill(bot_name)

        for label in (r"^(mute|turn off|stop|disable) (the )?(microphone|mic|camera|video)",
                      r"^(turn off (microphone|camera))$"):
            btn = page.get_by_role("button", name=re.compile(label, re.I)).first
            if _visible(btn, 2000):
                btn.click()

        join_btn = page.get_by_role("button", name=self.JOIN).first
        if not _visible(join_btn, 20000):
            raise BotError(self._body_hint(page) or "Beitreten-Schaltfläche nicht gefunden")
        join_btn.click()

        deadline = time.time() + admit_timeout
        leave = page.get_by_role("button", name=self.LEAVE).first
        while time.time() < deadline:
            if _visible(leave, 1000):
                self._dismiss(page)
                return
            hint = self._body_hint(page, self.DENIED)
            if hint:
                raise BotError(f"Beitritt abgelehnt: {hint}")
            waiting = self._body_hint(page, re.compile(r"(please wait|waiting for (the )?(host|moderator))", re.I))
            if waiting:
                on_status("waiting_admit")
        raise BotError("Niemand hat den Bot rechtzeitig in das Meeting gelassen")

    def ended(self, page) -> bool:
        if page.is_closed():
            return True
        if _visible(page.get_by_role("button", name=self.LEAVE).first, 500):
            return False
        return bool(self._body_hint(page, self.ENDED))

    def alone(self, page) -> bool:
        if self._body_hint(page, self.ALONE):
            return True
        return _participants_count(page) == 1

    def leave(self, page) -> None:
        btn = page.get_by_role("button", name=self.LEAVE).first
        if _visible(btn, 1000):
            try:
                btn.scroll_into_view_if_needed(timeout=2000)
                btn.click(timeout=3000)
            except Exception:  # noqa: BLE001 – Fallback: per JavaScript klicken
                try:
                    btn.evaluate("el => el.click()")
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _join_url(url: str, bot_name: str) -> str:
        """Meeting-URL um Jitsi-Konfiguration erweitern (englische UI, Bot stumm, Name setzen)."""
        overrides = {
            "config.defaultLanguage": '"en"',
            "config.prejoinConfig.enabled": "false",
            "config.prejoinPageEnabled": "false",
            "config.startWithAudioMuted": "true",
            "config.startWithVideoMuted": "true",
            "config.disableDeepLinking": "true",
            "userInfo.displayName": f'"{bot_name}"',
        }
        parts = urllib.parse.urlsplit(url)
        fragment = parts.fragment
        if fragment.startswith("config.") or fragment.startswith("userInfo."):
            sep = "&" if fragment else ""
            fragment = fragment + sep + urllib.parse.urlencode(overrides, quote_via=urllib.parse.quote)
        else:
            fragment = urllib.parse.urlencode(overrides, quote_via=urllib.parse.quote)
        return urllib.parse.urlunsplit(parts._replace(fragment=fragment))

    @staticmethod
    def _dismiss(page) -> None:
        for name in ("Got it", "Dismiss", "Close"):
            btn = page.get_by_role("button", name=name, exact=True).first
            if _visible(btn, 300):
                try:
                    btn.click(timeout=1000)
                except Exception:  # noqa: BLE001 – Hinweisdialoge sind optional
                    pass

    @staticmethod
    def _body_hint(page, pattern: re.Pattern | None = None) -> str | None:
        try:
            text = page.inner_text("body", timeout=2000)
        except Exception:  # noqa: BLE001
            return None
        m = (pattern or Jitsi.DENIED).search(text)
        return m.group(0) if m else None


class Teams(Adapter):
    """Microsoft Teams (Web) über die Weboberfläche, Gastbeitritt.

    Der Beitritt erfolgt anonym über den Browser: Interstitial „In diesem
    Browser fortfahren" („Continue on this browser"), Namen setzen, optionalen
    Audio/Video-Hinweisdialog wegklicken, eventuell in der Lobby warten, bis
    jemand einlässt. Klappt nur, wenn die Organisation anonyme Beitritte
    erlaubt; sonst meldet der Bot einen klaren Fehler.
    Die Beschriftungen stammen aus einer Live-Aufnahme der deutschen
    teams.live.com-Oberfläche (Light-Meetings, Stand 2026-10) und decken
    zusätzlich die englischen Varianten ab; die UI-Sprache folgt dem
    Microsoft-Konto, nicht der Browsersprache.
    """

    JOIN = re.compile(r"^(join now|join|jetzt teilnehmen)$", re.I)
    BROWSER = re.compile(r"(continue on this browser|join on (the )?web instead"
                         r"|continue without (an )?app|use the web app instead"
                         r"|in diesem browser fortfahren"
                         r"|treten sie der besprechung über diesen browser bei)", re.I)
    NO_MEDIA = re.compile(r"(ohne audio oder video fortfahren"
                          r"|continue without (audio( or video)?|video))", re.I)
    NAME_FIELD = re.compile(r"(name|namen)", re.I)
    LEAVE = re.compile(r"^(leave|leave (the )?(call|meeting)|verlassen)$", re.I)
    DENIED = re.compile(r"(can't join this meeting|cannot join this meeting|you need permission"
                        r"|guests? (are )?(not|n't) (allowed|permitted)"
                        r"|anonymous (users? )?(are )?(not|n't) (allowed|permitted)"
                        r"|(this )?meeting (has ended|is over)|meeting not found|no longer available"
                        r"|you('ve| have) been removed)", re.I)
    ENDED = re.compile(r"(you have left the meeting|you've left the meeting"
                       r"|(this )?meeting has ended|the call ended"
                       r"|you('ve| have) been removed|return to home screen"
                       r"|sind sie aus versehen gegangen"
                       r"|zu teams-besprechungen zurückkehren"
                       r"|erneut am anruf teilnehmen)", re.I)
    ALONE = re.compile(r"(you are the only one (here|in (this|the) meeting)|no one else is here"
                       r"|warten auf weitere teilnehmer)", re.I)
    WAITING = re.compile(r"(waiting for someone to let you in|please wait"
                         r"|waiting for (the )?(host|organizer|moderator)"
                         r"|jemand wird sie einlassen)", re.I)

    @staticmethod
    def _join_url(url: str) -> str:
        """Meeting-URL um den anonymen Beitritt erweitern (``anon=true``).

        Teams-Links leiten sonst ggf. auf die Anmelde-Seite um; ``anon=true``
        erzwingt den Gastbeitritt. Ein bereits vorhandener Parameter bleibt.
        """
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        query.setdefault("anon", ["true"])
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, doseq=True)))

    def join(self, page, url: str, bot_name: str, admit_timeout: float, on_status) -> None:
        page.goto(self._join_url(url), wait_until="domcontentloaded")
        self._dismiss(page)

        # Interstitial „Teams-App öffnen?" → im Browser fortfahren (Button oder Link).
        for loc in (page.get_by_role("button", name=self.BROWSER).first,
                    page.get_by_role("link", name=self.BROWSER).first):
            if _visible(loc, 2500):
                try:
                    loc.click(timeout=3000)
                except Exception:  # noqa: BLE001 – Fallback: per JavaScript klicken
                    try:
                        loc.evaluate("el => el.click()")
                    except Exception:  # noqa: BLE001
                        pass
                break

        # Prüfe, ob der Beitritt direkt abgelehnt wird (z. B. Gastbeitritt gesperrt).
        hint = self._body_hint(page, self.DENIED)
        if hint:
            raise BotError(f"Beitritt abgelehnt: {hint}")

        # Prejoin: optionalen Audio/Video-Hinweisdialog schließen (blockiert sonst
        # die Bedienung), Namen setzen, Mikrofon und Kamera ausschalten.
        self._dismiss_no_media(page)
        name_box = page.get_by_role("textbox", name=self.NAME_FIELD).first
        if not _visible(name_box, 3000):
            name_box = page.locator("input[type=text][aria-label*='name' i], input[placeholder*='name' i]").first
        if _visible(name_box, 10000):
            name_box.fill(bot_name)

        for pattern in (r"^(mute|turn off|stop|disable) (the )?(microphone|mic)$",
                        r"^(mute|turn off|stop|disable) (the )?(camera|video)$"):
            btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
            if _visible(btn, 2000):
                btn.click()

        self._dismiss_no_media(page)
        join_btn = page.get_by_role("button", name=self.JOIN).first
        if not _visible(join_btn, 20000):
            raise BotError(self._body_hint(page) or "Beitreten-Schaltfläche nicht gefunden")
        join_btn.click()

        # Auf Einlass warten (Lobby) oder direkten Beitritt erkennen.
        deadline = time.time() + admit_timeout
        while time.time() < deadline:
            if self._leave_btn(page, 1000):
                self._dismiss(page)
                return
            hint = self._body_hint(page, self.DENIED)
            if hint:
                raise BotError(f"Beitritt abgelehnt: {hint}")
            if self._body_hint(page, self.WAITING):
                on_status("waiting_admit")
        raise BotError("Niemand hat den Bot rechtzeitig in das Meeting gelassen")

    def ended(self, page) -> bool:
        if page.is_closed():
            return True
        if self._leave_btn(page, 500):
            return False
        return bool(self._body_hint(page, self.ENDED))

    def alone(self, page) -> bool:
        if self._body_hint(page, self.ALONE):
            return True
        return _participants_count(page) == 1

    def leave(self, page) -> None:
        btn = self._leave_btn(page, 1000)
        if btn:
            try:
                btn.click(timeout=3000)
            except Exception:  # noqa: BLE001 – Fallback: per JavaScript klicken
                try:
                    btn.evaluate("el => el.click()")
                except Exception:  # noqa: BLE001
                    pass

    def _leave_btn(self, page, timeout_ms: int = 1000):
        """Verlassen-Button: per Beschriftung (z. B. „Verlassen" als title) oder fester id.

        Der Teams-Verlassen-Button hat kein ``aria-label``, nur ``title`` – der
        Name-Locator findet ihn trotzdem (title fließt in den Accessible Name
        ein), die ``#hangup-button``-Id ist der robuste Fallback.
        """
        for loc in (page.get_by_role("button", name=self.LEAVE).first,
                    page.locator("#hangup-button").first):
            if _visible(loc, timeout_ms):
                return loc
        return None

    def _dismiss_no_media(self, page) -> None:
        """Hinweisdialog ohne Audio/Video-Geräte schließen (blockiert sonst den Beitritt).

        Ohne Mikrofon-/Kamerarechte zeigt Teams den Dialog „Ohne Audio oder
        Video fortfahren" („Continue without audio or video“) – ohne Klick auf
        diesen Button kommt der Beitritt nie zustande.
        """
        btn = page.get_by_role("button", name=self.NO_MEDIA).first
        if _visible(btn, 1500):
            try:
                btn.click(timeout=2000)
            except Exception:  # noqa: BLE001 – Fallback: per JavaScript klicken
                try:
                    btn.evaluate("el => el.click()")
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _dismiss(page) -> None:
        for name in ("Got it", "Dismiss", "Close", "Not now", "Maybe later"):
            btn = page.get_by_role("button", name=name, exact=True).first
            if _visible(btn, 300):
                try:
                    btn.click(timeout=1000)
                except Exception:  # noqa: BLE001 – Hinweisdialoge sind optional
                    pass

    @staticmethod
    def _body_hint(page, pattern: re.Pattern | None = None) -> str | None:
        try:
            text = page.inner_text("body", timeout=2000)
        except Exception:  # noqa: BLE001
            return None
        m = (pattern or Teams.DENIED).search(text)
        return m.group(0) if m else None


def _participants_count(page) -> int | None:
    """Teilnehmerzahl aus dem Participants-Button lesen (nur wenn eindeutig lesbar)."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"participants|personen", re.I)).first
        if not _visible(btn, 500):
            return None
        text = btn.inner_text(timeout=500)
        m = re.search(r"(\d+)", text or "")
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001 – Teilnehmerzahl ist optional
        return None


def _visible(locator, timeout_ms: int) -> bool:
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 – Timeout heißt „nicht sichtbar“
        return False


ADAPTERS = {"google_meet": GoogleMeet, "jitsi": Jitsi, "teams": Teams, "web": Adapter}
