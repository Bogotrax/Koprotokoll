"""Plattform-Adapter: Meeting-Links erkennen, beitreten, Ende erkennen."""

from __future__ import annotations

import re
import time
import urllib.parse

PATTERNS = {
    "google_meet": re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:\?[^\s\"'<>]*)?", re.I),
    "zoom": re.compile(r"https://(?:[\w-]+\.)?zoom\.us/(?:j|my|w)/[^\s\"'<>]+", re.I),
    "teams": re.compile(r"https://teams\.(?:microsoft|live)\.com/(?:l/meetup-join|meet)/[^\s\"'<>]+", re.I),
}
# Plattformen, denen der Bot beitreten kann. Zoom/Teams werden erkannt, aber noch nicht unterstützt.
SUPPORTED = {"google_meet", "web"}


class BotError(RuntimeError):
    pass


def detect_platform(url: str) -> str | None:
    for name, pattern in PATTERNS.items():
        if pattern.match(url.strip()):
            return name
    return None


def find_meeting_url(event: dict) -> tuple[str, str] | None:
    """Meeting-Link aus einem Google-Kalender-Ereignis ziehen → (url, plattform)."""
    candidates = [event.get("hangoutLink") or ""]
    for ep in (event.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            candidates.append(ep.get("uri") or "")
    candidates += [event.get("location") or "", event.get("description") or ""]
    for text in candidates:
        for name, pattern in PATTERNS.items():
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


def _visible(locator, timeout_ms: int) -> bool:
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 – Timeout heißt „nicht sichtbar“
        return False


ADAPTERS = {"google_meet": GoogleMeet, "web": Adapter}
