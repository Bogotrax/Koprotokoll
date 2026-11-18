"""Infobereich (Tray) für Windows mit gespeicherten Einstellungen.

Der Tray ist die Windows-Entsprechung zur Mac-Menüleiste aus
``desktop_macos.swift``. Er steuert die Aufnahme ausschließlich über die
lokale Server-API: kein zweiter Aufnahmeweg, keine zusätzlichen Pakete.

Aufbau:

* ``Preferences`` – Audioquelle (Name), Kürzel an/aus und Doppeltipp-Taste
  in ``data/desktop/windows-einstellungen.json``, atomar gespeichert. Die
  Datei enthält keine Geheimnisse; die Rechte 0600 gelten auf POSIX, unter
  Windows schützt die Vererbung des Benutzerprofils.
* ``ServerApi`` – ``urllib``-Zugriff auf ``/api/capture*`` mit deutschen
  Fehlermeldungen. Verlangt der Server einen Schlüssel
  (``KOPROTOKOLL_REQUIRE_KEY=1``), liest der Tray ihn aus
  ``KOPROTOKOLL_API_KEY`` oder ``data/desktop/api-schluessel.txt``.
* ``Controller`` – Zustand und Abläufe ohne Betriebssystemaufrufe; damit
  ohne echten Tray und ohne Aufnahme prüfbar.
* ``TrayApp`` – Symbol, Menü, Aufnahmedauer und Fehlermeldungen über
  ``Shell_NotifyIconW``. Netz- und länger dauernde Arbeit läuft im
  Hintergrund, damit das Menü nicht einfriert; Win32-Fenster und Menü
  arbeiten nur im Hauptthread, die wenige hundert Byte große
  Einstellungsdatei schreibt er dort ebenfalls (unter einer Millisekunde).
* ``ensure_server`` – startet bei Bedarf ``python -m koprotokoll serve``
  und wartet auf die Aufnahme-API.

Das Tray beendet den Server nicht: die Auswertung und die Oberfläche
bleiben nach dem Stopp der Aufnahme erreichbar. Das Startprotokoll des
Servers liegt unter ``data/desktop/server.log``.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .windows_hotkey import ShortcutModifier, ShortcutMonitor

log = logging.getLogger(__name__)

PREFS_NAME = "windows-einstellungen.json"
API_KEY_ENV = "KOPROTOKOLL_API_KEY"       # Vorrang vor der Datei
API_KEY_NAME = "api-schluessel.txt"       # in ``data/desktop``
SERVER_TIMEOUT = 75.0  # Sekunden, bis der Server bzw. eine Aufnahme bereit ist

# Anzeigenamen und gespeicherte Schlüssel der drei Doppeltipp-Tasten.
KEY_NAMES: dict[str, ShortcutModifier] = {
    "alt": ShortcutModifier.ALT,
    "strg": ShortcutModifier.CONTROL,
    "umschalt": ShortcutModifier.SHIFT,
}
DEFAULT_KEY = "alt"


class DesktopError(RuntimeError):
    """Ein Fehler, der im Tray als deutsche Meldung erscheint."""


@dataclass
class Preferences:
    """Gespeicherte Tray-Einstellungen; die Quelle wird über ihren Namen gehalten."""

    audio_source_name: str | None = None
    shortcut_enabled: bool = True
    double_tap_key: str = DEFAULT_KEY

    @property
    def modifier(self) -> ShortcutModifier:
        return KEY_NAMES.get(self.double_tap_key, ShortcutModifier.ALT)


def preferences_path(settings: Settings) -> Path:
    return settings.data_dir / "desktop" / PREFS_NAME


def api_key_path(settings: Settings) -> Path:
    """Wo der Tray einen API-Schlüssel erwartet, wenn ``KOPROTOKOLL_REQUIRE_KEY=1`` gilt."""
    return settings.data_dir / "desktop" / API_KEY_NAME


def load_api_key(settings: Settings, environ: dict | None = None) -> str | None:
    """Den API-Schlüssel des Trays lesen: Umgebungsvariable zuerst, dann die Datei.

    Die Umgebungsvariable hat Vorrang, damit ein Test oder ein Aufruf aus einer
    anderen Anwendung den Schlüssel mitgeben kann. Fehlt beides, bleibt es bei
    ``None``; der Server antwortet dann – falls die Pflicht gilt – mit 401, und
    die Meldung erklärt den Weg zum Schlüssel.
    """
    source = (os.environ if environ is None else environ).get(API_KEY_ENV)
    if isinstance(source, str) and source.strip():
        return source.strip()
    try:
        saved = api_key_path(settings).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return saved or None


def load_preferences(settings: Settings) -> Preferences:
    """Einstellungen lesen; fehlende oder beschädigte Dateien ergeben Standardwerte."""
    try:
        saved = json.loads(preferences_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Preferences()
    if not isinstance(saved, dict):
        return Preferences()
    name = saved.get("audio_source_name")
    source = name.strip() or None if isinstance(name, str) else None
    enabled = saved.get("shortcut_enabled")
    key = saved.get("double_tap_key")
    return Preferences(
        audio_source_name=source,
        shortcut_enabled=enabled if isinstance(enabled, bool) else True,
        double_tap_key=key if key in KEY_NAMES else DEFAULT_KEY,
    )


def save_preferences(settings: Settings, preferences: Preferences) -> None:
    """Einstellungen atomar schreiben.

    Die Rechte 0600 gelten auf POSIX; unter Windows richten ACLs die Rechte
    ein, dort schützt die Vererbung des Benutzerprofils. Geheimnisse stehen
    nicht in der Datei.
    """
    settings.ensure_dirs()
    path = preferences_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "audio_source_name": preferences.audio_source_name,
        "shortcut_enabled": preferences.shortcut_enabled,
        "double_tap_key": preferences.double_tap_key,
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.chmod(0o600)
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def format_duration(seconds: float) -> str:
    """Aufnahmedauer als ``MM:SS``, ab einer Stunde als ``H:MM:SS``."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def recording_url(base_url: str, meeting_id: str) -> str:
    """Adresse des fertigen Meetings in der Weboberfläche (``#meeting=<id>``)."""
    return f"{base_url.rstrip('/')}/#meeting={urllib.parse.quote(str(meeting_id))}"


def _http_detail(exc: urllib.error.HTTPError) -> str:
    """Die deutsche ``detail``-Meldung der API aus einer HTTP-Antwort lesen."""
    if exc.code == 401:
        # Der Server sagt nur „Schlüssel erforderlich“; hier steht, woher der Tray ihn nimmt.
        return ("Der Server verlangt einen API-Schlüssel (KOPROTOKOLL_REQUIRE_KEY=1). "
                "Einmalig „python -m koprotokoll create-key tray“ ausführen und den "
                f"Schlüssel als {API_KEY_ENV} bereitstellen oder in "
                f"data/desktop/{API_KEY_NAME} ablegen.")
    try:
        payload = json.loads(exc.read() or b"null")
    except (OSError, ValueError):
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if exc.code == 409:
        return "Der lokale Server hat die Anfrage abgelehnt. Läuft bereits eine Aufnahme?"
    return f"Der lokale Server antwortete mit HTTP {exc.code}."


class ServerApi:
    """Die lokale Aufnahme-API über ``urllib``; optional mit ``Bearer``-Schlüssel."""

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip() or None

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: float = 10.0) -> object:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers,
                                         method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise DesktopError(_http_detail(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DesktopError(
                f"Der lokale Server unter {self.base_url} ist nicht erreichbar. "
                "Läuft „python -m koprotokoll serve“?") from exc
        try:
            return json.loads(raw or b"null")
        except ValueError as exc:
            raise DesktopError("Der lokale Server antwortete mit einer unlesbaren Nachricht.") from exc

    def status(self, timeout: float = 5.0) -> dict:
        state = self._request("GET", "/api/capture", timeout=timeout)
        return state if isinstance(state, dict) else {}

    def devices(self, timeout: float = SERVER_TIMEOUT) -> list[dict]:
        """Die sichtbaren Audioquellen; der erste Aufruf startet ffmpeg."""
        state = self._request("GET", "/api/capture/devices", timeout=timeout)
        entries = state.get("devices", []) if isinstance(state, dict) else []
        return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]

    def start(self, device: str) -> dict:
        state = self._request("POST", "/api/capture/start",
                              {"device": device, "title": ""}, timeout=SERVER_TIMEOUT)
        return state if isinstance(state, dict) else {}

    def stop(self) -> dict:
        state = self._request("POST", "/api/capture/stop", {}, timeout=SERVER_TIMEOUT)
        return state if isinstance(state, dict) else {}


class Controller:
    """Zustand und Abläufe des Trays; im Test ohne Netz und ohne Betriebssystemaufrufe."""

    def __init__(self, api: ServerApi, preferences: Preferences, *,
                 save: Callable[[Preferences], None],
                 open_url: Callable[[str], object] = webbrowser.open,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 monitor: ShortcutMonitor | None = None) -> None:
        self.api = api
        self.preferences = preferences
        self.busy = False
        self.waiting = False
        self.active = False
        self.error: str | None = None
        self.status_text = "bereit"
        self._save = save
        self._open_url = open_url
        self._clock = clock      # Wanduhr: nur zum Vergleich mit ``started_at`` des Servers
        self._mono = monotonic   # Dauer misst ohne Sprünge durch Zeitabgleich
        self._monitor = monitor
        self._started_at: float | None = None  # Startzeit auf der monotonen Uhr

    @property
    def base_url(self) -> str:
        return self.api.base_url

    @property
    def locked(self) -> bool:
        """Während einer laufenden oder startenden Aufnahme sind Einstellungen gesperrt."""
        return self.busy or self.active

    def attach_monitor(self, monitor: ShortcutMonitor) -> None:
        """Die Tastenüberwachung übernehmen und sofort auf die Einstellungen anwenden."""
        self._monitor = monitor
        self.apply_shortcut()

    def apply_shortcut(self) -> None:
        if self._monitor is not None:
            self._monitor.configure(self.preferences.shortcut_enabled,
                                    self.preferences.modifier)

    def status_line(self) -> str:
        """Kurztext für Menü und Symbol: bereit, Dauer, Zwischenschritt oder Fehler."""
        if self.busy or self.waiting:
            return self.status_text
        if self.error:
            return f"Fehler: {self.error}"
        if self.active:
            return f"● {format_duration(self.elapsed())}"
        return "bereit"

    def elapsed(self) -> float:
        """Laufende Dauer in Sekunden, gemessen auf der monotonen Uhr."""
        if self._started_at is None:
            return 0.0
        return max(0.0, self._mono() - self._started_at)

    def fail(self, message: str) -> None:
        """Einen Fehler für Menü und Meldung merken."""
        self.error = message.strip() or "Unbekannter Fehler."
        self.status_text = f"Fehler: {self.error}"
        self.busy = False

    def devices(self) -> list[dict]:
        return self.api.devices()

    def sync(self) -> None:
        """Den Zustand des Servers übernehmen; die Aufnahme kann in der Oberfläche laufen."""
        state = self.api.status()
        self.error = None  # der Server antwortet; ältere Meldungen verfallen
        if bool(state.get("active")):
            started = state.get("started_at")
            if isinstance(started, (int, float)):
                # Der Server nennt den Start in Wanduhrzeit; einmal in die monotone Uhr
                # umrechnen, damit ein späterer Zeitabgleich die Dauer nicht springen lässt.
                age = max(0.0, self._clock() - float(started))
                self._started_at = self._mono() - age
            else:
                self._started_at = self._mono()
            self.active = True
        else:
            self.active = False
            self._started_at = None

    def device_id(self) -> str:
        """Die gespeicherte Quelle über ihren Namen eindeutig zuordnen."""
        name = (self.preferences.audio_source_name or "").strip()
        if not name:
            raise DesktopError("Bitte zuerst unter „Audioquelle“ einen Eingang wählen.")
        matches = [entry for entry in self.devices() if entry.get("name") == name]
        if not matches:
            raise DesktopError(f"Die gespeicherte Audioquelle „{name}“ ist nicht verfügbar. "
                               "Bitte im Menü neu wählen.")
        if len(matches) > 1:
            raise DesktopError(f"Die Audioquelle „{name}“ ist mehrdeutig. "
                               "Bitte im Menü eine eindeutige Quelle wählen.")
        return str(matches[0]["id"])

    def start(self) -> None:
        if self.active:
            raise DesktopError("Es läuft bereits eine Aufnahme.")
        if self.busy:
            raise DesktopError("Eine Aktion läuft bereits.")
        self.busy = True
        self.error = None
        self.status_text = "Aufnahme wird gestartet …"
        try:
            self.api.start(self.device_id())
            self.active = True
            self._started_at = self._mono()
            self.status_text = "bereit"
        except DesktopError as exc:
            self.fail(str(exc))
            raise
        finally:
            self.busy = False

    def stop(self) -> dict:
        if self.busy:
            raise DesktopError("Eine Aktion läuft bereits.")
        self.busy = True
        self.error = None
        self.status_text = "Aufnahme wird gesichert …"
        try:
            meeting = self.api.stop()
        except DesktopError as exc:
            self.fail(str(exc))
            raise
        finally:
            self.busy = False
        self.active = False
        self._started_at = None
        self.status_text = "bereit"
        meeting_id = str(meeting.get("id") or "")
        if meeting_id:
            self._open_url(recording_url(self.base_url, meeting_id))
        return meeting

    def toggle(self) -> None:
        if self.busy:
            raise DesktopError("Eine Aktion läuft bereits.")
        if self.active:
            self.stop()
        else:
            self.start()

    def choose_source(self, name: str) -> None:
        self._require_unlocked()
        name = name.strip()
        if not name:
            raise DesktopError("Bitte eine Audioquelle auswählen.")
        self.preferences.audio_source_name = name
        self._save(self.preferences)

    def configure_shortcut(self, *, enabled: bool | None = None, key: str | None = None) -> None:
        self._require_unlocked()
        if enabled is None and key is None:
            return
        if key is not None:
            if key not in KEY_NAMES:
                raise DesktopError("Unbekannte Doppeltipp-Taste.")
            self.preferences.double_tap_key = key
        if enabled is not None:
            self.preferences.shortcut_enabled = enabled
        self._save(self.preferences)
        self.apply_shortcut()

    def _require_unlocked(self) -> None:
        if self.locked:
            raise DesktopError("Während einer Aufnahme sind die Einstellungen gesperrt.")


def server_ready(base_url: str, timeout: float = 3.0) -> bool:
    """Ob unter der Adresse ein Koprotokoll-Server die Aufnahme-API beantwortet."""
    try:
        ServerApi(base_url).status(timeout=timeout)
        return True
    except DesktopError:
        return False


def ensure_server(base_url: str, *, port: int, data_dir: Path,
                  ready: Callable[[str], bool] | None = None,
                  popen: Callable[..., subprocess.Popen] | None = None,
                  cwd: Path | None = None, timeout: float = SERVER_TIMEOUT,
                  sleep: Callable[[float], None] = time.sleep) -> subprocess.Popen | None:
    """Lokalen Server verwenden oder sichtbar starten; ``None`` heißt: er lief schon."""
    check = ready or server_ready
    if check(base_url):
        return None
    log_dir = data_dir / "desktop"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    launch = popen or subprocess.Popen
    with log_path.open("ab") as output:
        process = launch(
            [sys.executable, "-m", "koprotokoll", "serve", "--port", str(port)],
            cwd=str(cwd or Path(__file__).resolve().parents[1]),
            env=dict(os.environ, KOPROTOKOLL_DATA=str(data_dir)),
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    log.info("Server für den Infobereich gestartet, Protokoll: %s", log_path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DesktopError("Der lokale Server wurde sofort beendet. "
                               f"Einzelheiten stehen in {log_path}.")
        if check(base_url):
            return process
        sleep(0.5)
    raise DesktopError("Der lokale Server wurde nicht rechtzeitig bereit. "
                       f"Einzelheiten stehen in {log_path}.")


# --- Windows-Infobereich -------------------------------------------------------
# Die Strukturen und Konstanten sind auch auf anderen Betriebssystemen definiert;
# geladen werden die Bibliotheken erst in ``TrayApp.run``.

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1        # Mausklick auf das Symbol
WM_SHORTCUT = WM_APP + 2    # Doppeltipp im Hintergrund-Thread erkannt
WM_ACTION_DONE = WM_APP + 3  # Hintergrundaktion beendet
TIMER_ID = 1
TRAY_ID = 1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x0001, 0x0002, 0x0004, 0x0010
NIIF_INFO, NIIF_WARNING = 0x0001, 0x0002

MF_STRING, MF_GRAYED = 0x0000, 0x0001
MF_CHECKED, MF_POPUP, MF_SEPARATOR = 0x0008, 0x0010, 0x0800
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002

MENU_STATUS = 1
MENU_TOGGLE = 2
MENU_OPEN = 3
MENU_QUIT = 4
MENU_SHORTCUT = 10
MENU_REFRESH = 11
MENU_KEY_BASE = 20   # + Reihenfolge aus KEY_NAMES
MENU_SOURCE_BASE = 100  # + Index im Geräte-Cache

# Auf 64 Bit müssen die Rückgabetypen gesetzt sein, sonst schneidet ctypes Zeiger ab.
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    """Das Symbol samt Kurzinfo, Zustand und Meldung (``Shell_NotifyIconW``)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON), ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256), ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def _declare(user32, shell32, kernel32) -> None:
    """Alle Prototypen setzen; ohne Rückgabetypen schneidet ctypes Zeiger auf 64 Bit ab."""
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT,
                                wintypes.LPVOID]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                   wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      wintypes.LPVOID]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadIconW.restype = wintypes.HICON
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _require_windows(action: str) -> None:
    if sys.platform != "win32":
        raise DesktopError(f"{action} ist in dieser Version nur für Windows verfügbar.")


def menu_text(text: str) -> str:
    """Dateinamen mit ``&`` werden in Win32-Menüs sonst als Kürzel gelesen."""
    return text.replace("&", "&&")


class TrayApp:
    """Symbol im Infobereich mit Menü, Aufnahmedauer und Meldungen.

    Win32-Fenster, Menü und Symbol werden ausschließlich im Hauptthread
    bedient. Netz- und länger dauernde Arbeit läuft in einem
    Hintergrund-Thread; nach dem Abschluss weckt er den Hauptthread über
    ``WM_ACTION_DONE``. Die wenige hundert Byte große Einstellungsdatei
    schreibt der Hauptthread selbst, das dauert unter einer Millisekunde.
    """

    CLASS_NAME = "KoprotokollTrayWindow"
    HEARTBEAT = 10.0  # Sekunden zwischen zwei Zustandsabfragen beim Server

    def __init__(self, controller: Controller, *, port: int, settings: Settings,
                 base_url: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.controller = controller
        self.port = port
        self.settings = settings
        self.base_url = (base_url or f"http://127.0.0.1:{port}").rstrip("/")
        self.devices: list[dict] = []
        self.devices_loaded = False
        self.monitor: ShortcutMonitor | None = None
        self._worker: threading.Thread | None = None
        self._clock = clock
        self._next_sync = 0.0
        self._server_lock = threading.Lock()
        self._tooltip = ""
        self._shown_error: str | None = None
        self._icon_added = False
        self._hwnd = 0
        self._instance = None
        self._icon_handle = None
        self._user32 = None
        self._shell32 = None
        # Der Callback muss für die Lebensdauer des Fensters referenziert bleiben.
        self._wndproc = WNDPROC(self._on_message)

    def run(self) -> None:
        """Symbol anlegen und die Windows-Nachrichtenschleife starten (blockiert)."""
        _require_windows("Der Infobereich")
        self._prepare()
        self._add_icon()
        self.monitor = ShortcutMonitor()
        self.controller.attach_monitor(self.monitor)
        self._start_hotkey()
        self._user32.SetTimer(self._hwnd, TIMER_ID, 1000, None)
        threading.Thread(target=self._initial_load, name="koprotokoll-tray-start",
                         daemon=True).start()
        self._message_loop()

    # --- Fenster und Symbol ---------------------------------------------------

    def _prepare(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _declare(self._user32, self._shell32, kernel32)
        self._instance = kernel32.GetModuleHandleW(None)
        self._icon_handle = self._user32.LoadIconW(None, ctypes.c_void_p(32512))
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self._instance
        window_class.lpszClassName = self.CLASS_NAME
        if not self._user32.RegisterClassExW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise DesktopError("Das Fenster für den Infobereich ließ sich nicht "
                                   f"anlegen (Fehler {error}).")
        # Nie angezeigtes Fenster: es nimmt nur Symbol- und Timernachrichten an.
        self._hwnd = self._user32.CreateWindowExW(
            0, self.CLASS_NAME, "Koprotokoll", 0, 0, 0, 0, 0,
            None, None, self._instance, None)
        if not self._hwnd:
            # Ohne Fenster bleibt die registrierte Klasse sonst bis zum Prozessende stehen.
            self._user32.UnregisterClassW(self.CLASS_NAME, self._instance)
            raise DesktopError("Das Fenster für den Infobereich ließ sich nicht anlegen "
                               f"(Fehler {ctypes.get_last_error()}).")

    def _icon_data(self, flags: int) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = TRAY_ID
        data.uFlags = flags
        return data

    def _add_icon(self) -> None:
        data = self._icon_data(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon_handle
        text = self._tooltip_text()
        data.szTip = text
        if not self._shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise DesktopError("Das Symbol ließ sich nicht im Infobereich anlegen.")
        self._tooltip = text
        self._icon_added = True

    def _tooltip_text(self) -> str:
        return f"Koprotokoll – {self.controller.status_line()}"[:127]

    def _update_tooltip(self) -> None:
        if not self._icon_added:
            return
        text = self._tooltip_text()
        if text == self._tooltip:
            return
        data = self._icon_data(NIF_TIP)
        data.szTip = text
        if self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)):
            self._tooltip = text

    def _notify(self, message: str, *, warning: bool = False) -> None:
        """Eine Sprechblase am Symbol anzeigen (dauert je nach Windows-Einstellung kurz)."""
        if not self._icon_added:
            return
        data = self._icon_data(NIF_INFO)
        data.szInfoTitle = "Koprotokoll"
        data.szInfo = message[:255]
        data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def _remove_icon(self) -> None:
        if not self._icon_added:
            return
        self._shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._icon_data(0)))
        self._icon_added = False

    # --- Menü ---------------------------------------------------------------

    def _append(self, menu, command: int, text: str, *, checked: bool = False,
                locked: bool = False, submenu: int | None = None) -> None:
        flags = MF_POPUP if submenu is not None else MF_STRING
        if checked:
            flags |= MF_CHECKED
        if locked:
            flags |= MF_GRAYED
        item = submenu if submenu is not None else command
        self._user32.AppendMenuW(menu, flags, item, text)

    def _separator(self, menu) -> None:
        self._user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

    def _show_menu(self) -> None:
        """Das Menü an der Mausposition anzeigen; nur der Hauptthread ruft es auf."""
        menu = self._build_menu()
        if not menu:
            return
        user32 = self._user32
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self._hwnd)
        command = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                        point.x, point.y, 0, self._hwnd, None)
        # Ohne diese Nachricht bleibt das Menü in manchen Shells "kleben".
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if command:
            self._handle_command(int(command))

    def _build_menu(self):
        """Alle Menüeinträge passend zum Zustand aufbauen; Rückgabe ist das HMENU."""
        user32 = self._user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return 0
        state = self.controller
        locked = state.locked
        preferences = state.preferences
        self._append(menu, MENU_STATUS, state.status_line(), locked=True)
        self._separator(menu)
        self._append(menu, MENU_TOGGLE, "Aufnahme stoppen" if state.active
                     else "Aufnahme starten", locked=state.busy)
        self._append(menu, MENU_OPEN, "Koprotokoll öffnen")

        sources = user32.CreatePopupMenu()
        if sources:
            if self.devices:
                for index, device in enumerate(self.devices):
                    name = str(device.get("name") or "")
                    self._append(sources, MENU_SOURCE_BASE + index, menu_text(name),
                                 checked=name == preferences.audio_source_name,
                                 locked=locked)
            else:
                hint = "Keine Quelle gefunden" if self.devices_loaded else "Wird geladen …"
                self._append(sources, MENU_SOURCE_BASE, hint, locked=True)
            self._append(menu, 0, "Audioquelle", submenu=sources, locked=locked)

        keys = user32.CreatePopupMenu()
        if keys:
            self._append(keys, MENU_SHORTCUT, "Aktiviert",
                         checked=preferences.shortcut_enabled, locked=locked)
            self._separator(keys)
            for offset, (key, modifier) in enumerate(KEY_NAMES.items()):
                self._append(keys, MENU_KEY_BASE + offset, modifier.title,
                             checked=preferences.double_tap_key == key,
                             locked=locked or not preferences.shortcut_enabled)
            self._append(menu, 0, "Tastenkürzel", submenu=keys, locked=locked)

        self._append(menu, MENU_REFRESH, "Audioquellen aktualisieren", locked=locked)
        self._separator(menu)
        self._append(menu, MENU_QUIT, "Beenden", locked=locked)
        return menu

    def _handle_command(self, command: int) -> None:
        # Einstellungen schreiben wenige hundert Byte; das erledigt der Hauptthread.
        if command == MENU_TOGGLE:
            self._run_action(self._toggle)
        elif command == MENU_OPEN:
            webbrowser.open(self.base_url + "/")
        elif command == MENU_REFRESH:
            # Der erste Aufruf kann den Server starten und dauert; daher mit Zwischenstatus.
            self._run_action(self._refresh, status="Audioquellen werden geladen …")
        elif command == MENU_SHORTCUT:
            try:
                self.controller.configure_shortcut(
                    enabled=not self.controller.preferences.shortcut_enabled)
            except DesktopError as exc:
                self._show_error(exc)
        elif MENU_KEY_BASE <= command < MENU_KEY_BASE + len(KEY_NAMES):
            key = list(KEY_NAMES)[command - MENU_KEY_BASE]
            try:
                self.controller.configure_shortcut(key=key)
            except DesktopError as exc:
                self._show_error(exc)
        elif command >= MENU_SOURCE_BASE:
            self._choose_source(command - MENU_SOURCE_BASE)
        elif command == MENU_QUIT:
            self._quit()
        self._update_tooltip()

    def _quit(self) -> None:
        """Symbol entfernen, Fensterklasse abmelden und die Nachrichtenschleife beenden."""
        if self.controller.active:
            self._notify("Bitte zuerst die Aufnahme stoppen und sichern.", warning=True)
            return
        if self.controller.locked:
            # Start oder Stopp läuft gerade und sichert bzw. startet noch.
            self._notify("Bitte warten, bis die laufende Aktion beendet ist.", warning=True)
            return
        if self.monitor is not None:
            self.monitor.stop()
        self._user32.KillTimer(self._hwnd, TIMER_ID)
        self._remove_icon()
        self._user32.DestroyWindow(self._hwnd)
        self._hwnd = 0
        # Die Fensterklasse für den ganzen Prozess abmelden, nicht nur das Fenster.
        self._user32.UnregisterClassW(self.CLASS_NAME, self._instance)

    def request_quit(self) -> None:
        """Beenden anfordern, auch aus einem Signalhandler heraus (threadsicher)."""
        self._post(WM_CLOSE)

    # --- Nachrichten --------------------------------------------------------

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            # Nur einfache Klicks: die Fensterklasse hat kein CS_DBLCLKS, daher
            # kommen keine Doppelklick-Nachrichten an.
            if int(lparam) & 0xFFFF in (WM_LBUTTONUP, WM_RBUTTONUP):
                self._show_menu()
            return 0
        if message == WM_SHORTCUT:
            self._run_action(self._toggle)
            return 0
        if message == WM_ACTION_DONE:
            self._worker = None
            self._report_error()
            self._update_tooltip()
            return 0
        if message == WM_TIMER and int(wparam) == TIMER_ID:
            self._tick()
            self._update_tooltip()
            return 0
        if message == WM_CLOSE:
            self._quit()
            return 0
        if message == WM_DESTROY:
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _message_loop(self) -> None:
        message = wintypes.MSG()
        while True:
            result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result == -1:
                raise DesktopError("Die Windows-Nachrichtenschleife wurde unerwartet beendet.")
            if result == 0:
                break
            self._user32.TranslateMessage(ctypes.byref(message))
            self._user32.DispatchMessageW(ctypes.byref(message))
        self._remove_icon()

    def _post(self, message: int) -> None:
        """Aus einem Hintergrund-Thread in den Hauptthread wechseln."""
        if self._hwnd and self._user32 is not None:
            self._user32.PostMessageW(self._hwnd, message, 0, 0)

    def _tick(self) -> None:
        """Sekundentakt: den Zustand des Servers regelmäßig nachziehen.

        So übernimmt das Menü auch eine in der Oberfläche gestartete Aufnahme
        samt Dauer. Läuft der Startvorgang oder gerade eine Aktion, fällt
        dieser Takt aus, damit sich die Zugriffe nicht überschneiden.
        """
        now = self._clock()
        if now < self._next_sync:
            return
        self._next_sync = now + self.HEARTBEAT
        if self._worker is None and not self.controller.busy and not self.controller.waiting:
            self._run_action(self.controller.sync)

    # --- Abläufe ------------------------------------------------------------

    def _run_action(self, action: Callable[[], object], status: str | None = None) -> None:
        """Netz- oder Dateizugriff im Hintergrund; das Menü bleibt bedienbar."""
        if self._worker is not None:
            # Ein Doppeltipp oder Menüklick kam während einer laufenden Aktion:
            # nicht still verwerfen, sonst wirkt der Tray taub.
            self._notify("Eine Aktion läuft noch – bitte kurz warten.")
            return
        if status:
            self.controller.waiting = True
            self.controller.status_text = status
            self._update_tooltip()

        def work() -> None:
            try:
                action()
            except DesktopError as exc:
                self.controller.fail(str(exc))
            except Exception as exc:  # ein Tray darf nie still scheitern
                log.exception("Aktion im Infobereich fehlgeschlagen")
                self.controller.fail(f"Unerwarteter Fehler: {type(exc).__name__}: {exc}")
            finally:
                self.controller.waiting = False
                self._post(WM_ACTION_DONE)

        self._worker = threading.Thread(target=work, name="koprotokoll-tray", daemon=True)
        self._worker.start()

    def _ensure_server(self) -> None:
        with self._server_lock:
            ensure_server(self.base_url, port=self.port, data_dir=self.settings.data_dir)

    def _toggle(self) -> None:
        self._ensure_server()
        self.controller.sync()
        self.controller.toggle()

    def _refresh(self) -> None:
        self._ensure_server()
        self.devices = self.controller.devices()
        self.devices_loaded = True

    def _initial_load(self) -> None:
        """Server prüfen, Zustand übernehmen und die Quellen für das Menü laden."""
        self.controller.waiting = True
        self.controller.status_text = "Server wird gestartet …"
        try:
            self._ensure_server()
            self.controller.sync()
            self.devices = self.controller.devices()
            self.devices_loaded = True
        except DesktopError as exc:
            self.controller.fail(str(exc))
        finally:
            self.controller.waiting = False
            self._post(WM_ACTION_DONE)

    def _choose_source(self, index: int) -> None:
        if not 0 <= index < len(self.devices):
            return
        name = str(self.devices[index].get("name") or "")
        try:
            self.controller.choose_source(name)
        except DesktopError as exc:
            self._show_error(exc)

    def _start_hotkey(self) -> None:
        if self.monitor is None:
            return
        try:
            self.monitor.start(lambda: self._post(WM_SHORTCUT))
        except RuntimeError as exc:  # Tray bleibt per Menü bedienbar
            self.controller.fail(str(exc))
            self._shown_error = self.controller.error
            self._notify(str(exc), warning=True)

    def _show_error(self, exc: Exception) -> None:
        self.controller.fail(str(exc))
        self._shown_error = self.controller.error
        self._notify(str(exc), warning=True)
        self._update_tooltip()

    def _report_error(self) -> None:
        """Eine neue Fehlermeldung genau einmal als Sprechblase zeigen."""
        if self.controller.error and self.controller.error != self._shown_error:
            self._shown_error = self.controller.error
            self._notify(self.controller.error, warning=True)
        elif not self.controller.error:
            self._shown_error = None


_MUTEX = None


def single_instance_free() -> bool:
    """Nur ein Tray je Anmeldung; der Mutex bleibt bis zum Prozessende gehalten.

    Schlägt ``CreateMutexW`` fehl, lässt sich nicht sagen, ob schon ein Tray
    läuft; dann wird der Start mit einer Meldung abgebrochen, statt ein
    zweites Symbol zu erlauben.
    """
    global _MUTEX
    _require_windows("Der Infobereich")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\KoprotokollTray")
    error = ctypes.get_last_error()  # vor allen weiteren Aufrufen lesen
    if not handle:
        raise DesktopError("Es ließ sich nicht prüfen, ob der Infobereich bereits läuft "
                           f"(Fehler {error}).")
    _MUTEX = handle
    return error != 183  # ERROR_ALREADY_EXISTS


def main(port: int = 8000) -> None:
    """Infobereich starten; der Aufruf kehrt mit „Beenden“ oder Strg+C zurück."""
    if sys.platform != "win32":
        raise SystemExit("Der Infobereich ist in dieser Version nur für Windows verfügbar. "
                         "Unter macOS startet „python -m koprotokoll desktop“ die Menüleiste.")
    settings = Settings()
    settings.ensure_dirs()
    base_url = f"http://127.0.0.1:{port}"
    controller = Controller(
        ServerApi(base_url, api_key=load_api_key(settings)), load_preferences(settings),
        save=lambda values: save_preferences(settings, values),
    )
    tray = TrayApp(controller, port=port, settings=settings, base_url=base_url)
    try:
        if not single_instance_free():
            raise SystemExit("Koprotokoll läuft bereits im Infobereich.")
        # Strg+C wirkt wie „Beenden“. Der Signalhandler läuft im Hauptthread, der
        # hier in GetMessageW wartet; der Sekundentakt weckt ihn spätestens nach
        # einer Sekunde.
        signal.signal(signal.SIGINT, lambda *_: tray.request_quit())
        tray.run()
    except DesktopError as exc:
        raise SystemExit(str(exc)) from exc
