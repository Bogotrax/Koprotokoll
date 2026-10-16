"""Plattform-Adapter: Meeting-Links erkennen, beitreten, Ende erkennen."""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Iterable

PATTERNS = {
    "google_meet": re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:\?[^\s\"'<>]*)?", re.I),
    "zoom": re.compile(r"https://(?:[\w-]+\.)?zoom\.us/(?:j|my|w)/[^\s\"'<>]+", re.I),
    "teams": re.compile(r"https://teams\.(?:microsoft|live)\.com/(?:l/meetup-join|meet)/[^\s\"'<>]+", re.I),
    "jitsi": re.compile(r"https://meet\.jit\.si/[^\s\"'<>]+", re.I),
}
# Plattformen, denen der Bot beitreten kann. Zoom/Teams werden erkannt, aber noch nicht unterstützt.
SUPPORTED = {"google_meet", "web", "jitsi"}


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


def find_meeting_url(event: dict, jitsi_hosts: Iterable[str] = ()) -> tuple[str, str] | None:
    """Meeting-Link aus einem Google-Kalender-Ereignis ziehen → (url, plattform)."""
    candidates = [event.get("hangoutLink") or ""]
    for ep in (event.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            candidates.append(ep.get("uri") or "")
    candidates += [event.get("location") or "", event.get("description") or ""]
    for text in candidates:
        for name, pattern in PATTERNS.items():
            if name == "jitsi":
                pattern = jitsi_pattern(jitsi_hosts)
            m = pattern.search(text)
            if m:
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
        return self._participants_count(page) == 1

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
    def _participants_count(page) -> int | None:
        """Teilnehmerzahl aus dem Participants-Button lesen (nur wenn eindeutig lesbar)."""
        try:
            btn = page.get_by_role("button", name=re.compile(r"participants", re.I)).first
            if not _visible(btn, 500):
                return None
            text = btn.inner_text(timeout=500)
            m = re.search(r"(\d+)", text or "")
            return int(m.group(1)) if m else None
        except Exception:  # noqa: BLE001 – Teilnehmerzahl ist optional
            return None

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


def _visible(locator, timeout_ms: int) -> bool:
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 – Timeout heißt „nicht sichtbar“
        return False


ADAPTERS = {"google_meet": GoogleMeet, "jitsi": Jitsi, "web": Adapter}
