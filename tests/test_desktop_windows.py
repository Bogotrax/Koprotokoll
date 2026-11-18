"""Infobereich für Windows ohne echten Tray und ohne Aufnahme prüfen.

Die Win32-Aufrufe selbst brauchen einen angemeldeten Desktop und werden
hier nicht gestartet; geprüft werden Einstellungen, Zustandslogik,
Serverstart und die deutschen Fehlermeldungen.
"""

import io
import json
import sys
import urllib.error

import pytest

from koprotokoll import desktop_windows
from koprotokoll.config import Settings
from koprotokoll.desktop_windows import (
    Controller,
    DesktopError,
    Preferences,
    ServerApi,
    api_key_path,
    ensure_server,
    format_duration,
    load_api_key,
    load_preferences,
    menu_text,
    preferences_path,
    recording_url,
    save_preferences,
)


class FakeApi:
    """Antwortet wie die lokale Aufnahme-API, ohne Netz."""

    def __init__(self, devices=None, status=None):
        self.base_url = "http://127.0.0.1:8000"
        self._devices = devices if devices is not None else [{"id": "1", "name": "Mikrofon"}]
        self._status = status if status is not None else {"active": False}
        self.started = []
        self.stopped = 0

    def status(self, timeout=5.0):
        return dict(self._status)

    def devices(self, timeout=75.0):
        return list(self._devices)

    def start(self, device):
        self.started.append(device)
        return {"active": True, "device": device}

    def stop(self):
        self.stopped += 1
        return {"id": 7, "title": "Gespräch"}


class FakeMonitor:
    """Zeichnet auf, wie das Kürzel eingestellt wurde."""

    def __init__(self):
        self.calls = []

    def configure(self, enabled, modifier):
        self.calls.append((enabled, modifier))


def make_controller(api=None, prefs=None, clock=None, monotonic=None, monitor=None,
                    opened=None):
    saved = []
    api = api or FakeApi()
    preferences = prefs or Preferences(audio_source_name="Mikrofon")
    controller = Controller(
        api, preferences, save=saved.append,
        open_url=(opened if opened is not None else []).append,
        clock=clock or (lambda: 100.0), monotonic=monotonic or (lambda: 100.0),
        monitor=monitor,
    )
    return controller, api, saved


# --- Einstellungen -----------------------------------------------------------


def test_ohne_datei_gelten_standardwerte(tmp_path):
    prefs = load_preferences(Settings(data_dir=tmp_path))
    assert prefs.audio_source_name is None
    assert prefs.shortcut_enabled is True
    assert prefs.double_tap_key == "alt"


def test_beschaedigte_datei_ergibt_standardwerte(tmp_path):
    settings = Settings(data_dir=tmp_path)
    preferences_path(settings).parent.mkdir(parents=True)
    preferences_path(settings).write_text("keine gültige JSON-Datei", encoding="utf-8")
    assert load_preferences(settings) == Preferences()


def test_unbekannte_werte_werden_abgesichert(tmp_path):
    settings = Settings(data_dir=tmp_path)
    preferences_path(settings).parent.mkdir(parents=True)
    preferences_path(settings).write_text(json.dumps(
        {"audio_source_name": "   ", "shortcut_enabled": "ja", "double_tap_key": "meta"}),
        encoding="utf-8")
    prefs = load_preferences(settings)
    assert prefs.audio_source_name is None
    assert prefs.shortcut_enabled is True
    assert prefs.double_tap_key == "alt"


def test_speichern_ist_atomar_und_wird_wieder_gelesen(tmp_path):
    settings = Settings(data_dir=tmp_path)
    prefs = Preferences(audio_source_name="Mikrofon Büro", shortcut_enabled=False,
                        double_tap_key="strg")
    save_preferences(settings, prefs)
    saved = preferences_path(settings)
    assert saved.exists()
    assert not saved.with_name(saved.name + ".tmp").exists()
    assert "Büro" in saved.read_text(encoding="utf-8")
    assert load_preferences(settings) == prefs


@pytest.mark.skipif(sys.platform == "win32",
                    reason="chmod kennt unter Windows keine Benutzergruppen.")
def test_einstellungen_sind_nur_fuer_den_benutzer_lesbar(tmp_path):
    settings = Settings(data_dir=tmp_path)
    save_preferences(settings, Preferences())
    assert preferences_path(settings).stat().st_mode & 0o077 == 0


# --- Controller --------------------------------------------------------------


def test_start_ordnet_die_quelle_ueber_ihren_namen_zu():
    api = FakeApi([{"id": "1", "name": "Mikrofon"}, {"id": "2", "name": "Systemton"}])
    controller, _, _ = make_controller(api=api)
    controller.start()
    assert api.started == ["1"]
    assert controller.active is True
    assert controller.status_line() == "● 00:00"


def test_start_ohne_quelle_meldet_deutsch():
    controller, api, _ = make_controller(prefs=Preferences())
    with pytest.raises(DesktopError, match="Audioquelle"):
        controller.start()
    assert api.started == []
    assert controller.active is False


def test_verschwundene_quelle_wird_benannt():
    api = FakeApi([{"id": "2", "name": "Systemton"}])
    controller, _, _ = make_controller(api=api)
    with pytest.raises(DesktopError, match="nicht verfügbar"):
        controller.start()


def test_mehrdeutige_quelle_wird_abgelehnt():
    api = FakeApi([{"id": "1", "name": "Mikrofon"}, {"id": "2", "name": "Mikrofon"}])
    controller, _, _ = make_controller(api=api)
    with pytest.raises(DesktopError, match="mehrdeutig"):
        controller.start()


def test_stopp_sichert_und_oeffnet_das_meeting():
    opened = []
    controller, api, _ = make_controller(opened=opened)
    controller.start()
    controller.stop()
    assert api.stopped == 1
    assert controller.active is False
    assert controller.status_line() == "bereit"
    assert opened == [recording_url("http://127.0.0.1:8000", 7)]
    assert opened[0].endswith("/#meeting=7")


def test_dauer_kommt_von_der_uhr():
    wall = [100.0]
    mono = [500.0]
    controller, _, _ = make_controller(clock=lambda: wall[0],
                                       monotonic=lambda: mono[0])
    controller.start()
    wall[0] = 161.5
    mono[0] = 561.5
    assert controller.elapsed() == pytest.approx(61.5)
    assert controller.status_line() == "● 01:01"
    mono[0] = 500.0 + 3700.0
    assert controller.status_line() == "● 1:01:40"


def test_toggle_startet_und_stoppt():
    controller, api, _ = make_controller()
    controller.toggle()
    assert api.started == ["1"]
    controller.toggle()
    assert api.stopped == 1


def test_einstellungen_sind_waehrend_der_aufnahme_gesperrt():
    controller, _, saved = make_controller()
    controller.start()
    with pytest.raises(DesktopError, match="gesperrt"):
        controller.choose_source("Systemton")
    with pytest.raises(DesktopError, match="gesperrt"):
        controller.configure_shortcut(key="strg")
    assert saved == []
    assert controller.locked is True


def test_kuerzel_aendern_speichert_und_aktualisiert_die_ueberwachung():
    monitor = FakeMonitor()
    controller, _, saved = make_controller(monitor=monitor)
    controller.configure_shortcut(enabled=True, key="strg")
    assert controller.preferences.double_tap_key == "strg"
    assert monitor.calls[-1][1].title == "Strg zweimal"
    assert len(saved) == 1
    controller.configure_shortcut(enabled=False)
    assert monitor.calls[-1][0] is False
    with pytest.raises(DesktopError, match="Doppeltipp"):
        controller.configure_shortcut(key="meta")


def test_sync_uebernimmt_eine_laufende_aufnahme():
    api = FakeApi(status={"active": True, "started_at": 100.0})
    controller, _, _ = make_controller(api=api, clock=lambda: 130.0)
    controller.sync()
    assert controller.active is True
    assert controller.status_line() == "● 00:30"
    api._status = {"active": False}
    controller.sync()
    assert controller.active is False


def test_dauer_springt_nicht_bei_einem_zeitabgleich():
    wall = [100.0]
    mono = [500.0]
    api = FakeApi(status={"active": True, "started_at": 100.0})
    controller, _, _ = make_controller(api=api, clock=lambda: wall[0],
                                       monotonic=lambda: mono[0])
    controller.sync()
    assert controller.elapsed() == pytest.approx(0.0)
    mono[0] += 5.0
    wall[0] += 3600.0  # NTP oder Sommerzeit stellt die Wanduhr vor
    assert controller.elapsed() == pytest.approx(5.0)


def test_erfolgreicher_abgleich_loescht_die_fehlermeldung():
    controller, _, _ = make_controller()
    controller.fail("Der lokale Server ist nicht erreichbar.")
    controller.sync()
    assert controller.error is None


def test_fehler_wird_im_status_gefuehrt_und_beim_start_geloescht():
    controller, _, _ = make_controller()
    controller.fail("Der lokale Server ist nicht erreichbar.")
    assert controller.status_line().startswith("Fehler: Der lokale Server")
    controller.start()
    assert controller.error is None


def test_fehlgeschlagener_start_wird_als_fehler_gefuehrt():
    controller, _, _ = make_controller(prefs=Preferences())
    with pytest.raises(DesktopError):
        controller.start()
    assert "Audioquelle" in controller.error
    assert controller.status_line().startswith("Fehler:")
    assert controller.locked is False


def test_fehlgeschlagener_stopp_bleibt_laufen_und_meldet_deutsch():
    class FailingApi(FakeApi):
        def stop(self):
            raise DesktopError("Die Audioaufnahme konnte nicht sauber abgeschlossen werden.")

    controller, _, _ = make_controller(api=FailingApi())
    controller.start()
    with pytest.raises(DesktopError, match="nicht sauber abgeschlossen"):
        controller.stop()
    assert controller.active is True
    assert controller.status_line().startswith("Fehler:")


# --- Takt des Infobereichs ----------------------------------------------------


def make_tray(controller, tmp_path, ticks):
    """TrayApp ohne Win32; der Takt wird über eine steuerbare Uhr geprüft."""
    app = desktop_windows.TrayApp(controller, port=8000,
                                  settings=Settings(data_dir=tmp_path),
                                  clock=lambda: ticks[0])
    actions = []

    def run_action(action, status=None):
        actions.append(action)

    app._run_action = run_action
    return app, actions


def test_takt_zieht_den_serverzustand_nach(tmp_path):
    controller, _, _ = make_controller()
    ticks = [0.0]
    app, actions = make_tray(controller, tmp_path, ticks)

    app._tick()
    assert actions == [controller.sync]
    app._tick()
    assert len(actions) == 1  # vor Ablauf des Takts kein zweiter Zugriff
    ticks[0] = desktop_windows.TrayApp.HEARTBEAT
    app._tick()
    assert len(actions) == 2


def test_takt_pausiert_waehrend_ladens_und_aktionen(tmp_path):
    controller, _, _ = make_controller()
    ticks = [0.0]
    app, actions = make_tray(controller, tmp_path, ticks)

    controller.waiting = True
    app._tick()
    assert actions == []  # der Startvorgang lädt bereits
    controller.waiting = False
    controller.busy = True
    ticks[0] = desktop_windows.TrayApp.HEARTBEAT
    app._tick()
    assert actions == []  # eine Aktion läuft
    controller.busy = False
    app._worker = object()
    ticks[0] = 2 * desktop_windows.TrayApp.HEARTBEAT
    app._tick()
    assert actions == []  # der Hintergrund-Thread ist belegt


# --- Menü, Meldungen und Beenden ----------------------------------------------


class FakeUser32:
    """Zeichnet die Win32-Aufrufe des Hauptthreads auf, ohne echte Fenster."""

    def __init__(self):
        self.messages = []
        self.destroyed = []
        self.timers = []
        self.unregistered = []

    def PostMessageW(self, hwnd, message, wparam, lparam):
        self.messages.append((hwnd, message))
        return 1

    def KillTimer(self, hwnd, timer):
        self.timers.append(timer)

    def DestroyWindow(self, hwnd):
        self.destroyed.append(hwnd)
        return 1

    def UnregisterClassW(self, name, instance):
        self.unregistered.append((name, instance))
        return 1


def make_tray_with_win32(controller, tmp_path):
    """TrayApp mit aufgezeichneten Win32-Aufrufen und gesammelten Sprechblasen."""
    app = desktop_windows.TrayApp(controller, port=8000,
                                  settings=Settings(data_dir=tmp_path))
    win32 = FakeUser32()
    app._user32 = win32
    app._instance = 42
    hints = []
    app._notify = lambda message, warning=False: hints.append((message, warning))
    return app, win32, hints


def test_belegter_hintergrund_verwirft_den_wunsch_nicht_still(tmp_path):
    controller, _, _ = make_controller()
    app, _, hints = make_tray_with_win32(controller, tmp_path)
    worker = object()
    app._worker = worker
    app._run_action(lambda: None)
    assert app._worker is worker  # kein zweiter Thread
    assert hints and "läuft noch" in hints[0][0]


def test_aktualisieren_zeigt_einen_zwischenschritt(tmp_path):
    controller, _, _ = make_controller()
    app, _, _ = make_tray_with_win32(controller, tmp_path)
    calls = []
    app._run_action = lambda action, status=None: calls.append((action, status))
    app._handle_command(desktop_windows.MENU_REFRESH)
    assert calls == [(app._refresh, "Audioquellen werden geladen …")]


def test_beenden_wartet_auf_die_aufnahme(tmp_path):
    controller, _, _ = make_controller()
    app, win32, hints = make_tray_with_win32(controller, tmp_path)
    controller.start()
    app._quit()
    assert win32.destroyed == []
    assert hints[0][0].startswith("Bitte zuerst die Aufnahme stoppen")


def test_beenden_wartet_auf_eine_laufende_aktion(tmp_path):
    controller, _, _ = make_controller()
    app, win32, hints = make_tray_with_win32(controller, tmp_path)
    controller.busy = True
    app._quit()
    assert win32.destroyed == []
    assert "warten" in hints[0][0]


def test_beenden_meldet_symbol_und_fensterklasse_ab(tmp_path):
    controller, _, _ = make_controller()
    app, win32, _ = make_tray_with_win32(controller, tmp_path)
    app._hwnd = 7
    app._quit()
    assert win32.destroyed == [7]
    assert win32.timers == [desktop_windows.TIMER_ID]
    assert win32.unregistered == [(desktop_windows.TrayApp.CLASS_NAME, 42)]
    assert app._hwnd == 0


def test_strg_c_fordert_das_beenden_an(tmp_path):
    controller, _, _ = make_controller()
    app, win32, _ = make_tray_with_win32(controller, tmp_path)
    app._hwnd = 7
    app.request_quit()
    assert win32.messages == [(7, desktop_windows.WM_CLOSE)]


# --- Serverstart -------------------------------------------------------------


def test_laufender_server_wird_nicht_doppelt_gestartet(tmp_path):
    calls = []
    assert ensure_server("http://127.0.0.1:8000", port=8000, data_dir=tmp_path,
                         ready=lambda url: True,
                         popen=lambda *a, **k: calls.append(a)) is None
    assert calls == []


def test_server_wird_einmal_gestartet_und_wartet_auf_die_api(tmp_path):
    ready = []
    calls = []
    sleeps = []

    class Process:
        def poll(self):
            return None

    def fake_ready(url):
        ready.append(url)
        return len(ready) > 2  # erst nach dem Start und einem Warteschritt erreichbar

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Process()

    process = ensure_server("http://127.0.0.1:8000", port=8123, data_dir=tmp_path,
                            ready=fake_ready, popen=fake_popen, sleep=sleeps.append)
    assert process is not None
    argv, kwargs = calls[0]
    assert argv[:3] == [sys.executable, "-m", "koprotokoll"]
    assert "--port" in argv and "8123" in argv
    assert kwargs["env"]["KOPROTOKOLL_DATA"] == str(tmp_path)
    assert sleeps == [0.5]
    assert (tmp_path / "desktop" / "server.log").exists()


def test_sofort_beendeter_server_wird_gemeldet(tmp_path):
    class Process:
        def poll(self):
            return 1

    with pytest.raises(DesktopError, match="sofort beendet"):
        ensure_server("http://127.0.0.1:8000", port=8000, data_dir=tmp_path,
                      ready=lambda url: False, popen=lambda *a, **k: Process(),
                      sleep=lambda seconds: None)


def test_nicht_bereiter_server_wird_gemeldet(tmp_path):
    class Process:
        def poll(self):
            return None

    with pytest.raises(DesktopError, match="nicht rechtzeitig"):
        ensure_server("http://127.0.0.1:8000", port=8000, data_dir=tmp_path,
                      ready=lambda url: False, popen=lambda *a, **k: Process(),
                      sleep=lambda seconds: None, timeout=0)


# --- Fehlermeldungen und Plattform -------------------------------------------


def test_api_fehler_uebernimmt_die_deutsche_meldung(monkeypatch):
    def fail(request, timeout=None):
        payload = json.dumps({"detail": "Es läuft bereits eine Hardwareaufnahme."}).encode()
        raise urllib.error.HTTPError("http://127.0.0.1:8000/api/capture/start", 409,
                                     "Conflict", {}, io.BytesIO(payload))

    monkeypatch.setattr(desktop_windows.urllib.request, "urlopen", fail)
    with pytest.raises(DesktopError, match="Es läuft bereits eine Hardwareaufnahme."):
        ServerApi("http://127.0.0.1:8000").start("1")


def test_api_fehler_ohne_detail_nennt_den_status(monkeypatch):
    def fail(request, timeout=None):
        raise urllib.error.HTTPError("http://127.0.0.1:8000/api/capture", 418,
                                     "Teapot", {}, io.BytesIO(b""))

    monkeypatch.setattr(desktop_windows.urllib.request, "urlopen", fail)
    with pytest.raises(DesktopError, match="HTTP 418"):
        ServerApi("http://127.0.0.1:8000").status()


class FakeResponse:
    """Minimalantwort für ``urllib.request.urlopen`` im Kontextmanager."""

    def __init__(self, payload=b"{}"):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_schluessel_wird_als_bearer_gesendet(monkeypatch):
    seen = []

    def capture(request, timeout=None):
        seen.append(request)
        return FakeResponse()

    monkeypatch.setattr(desktop_windows.urllib.request, "urlopen", capture)
    ServerApi("http://127.0.0.1:8000", api_key=" kp_test ").status()
    assert seen[0].get_header("Authorization") == "Bearer kp_test"
    seen.clear()
    ServerApi("http://127.0.0.1:8000").status()
    assert seen[0].get_header("Authorization") is None


def test_schluessel_kommt_aus_umgebung_oder_datei(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert load_api_key(settings, {}) is None
    path = api_key_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text("  kp_aus_datei\n", encoding="utf-8")
    assert load_api_key(settings, {}) == "kp_aus_datei"
    assert load_api_key(settings, {"KOPROTOKOLL_API_KEY": " kp_aus_env "}) == "kp_aus_env"
    assert load_api_key(settings, {"KOPROTOKOLL_API_KEY": "   "}) == "kp_aus_datei"


def test_fehlender_schluessel_wird_erklaert(monkeypatch):
    def fail(request, timeout=None):
        raise urllib.error.HTTPError("http://127.0.0.1:8000/api/capture", 401,
                                     "Unauthorized", {}, io.BytesIO(b'{"detail": "x"}'))

    monkeypatch.setattr(desktop_windows.urllib.request, "urlopen", fail)
    with pytest.raises(DesktopError, match="create-key"):
        ServerApi("http://127.0.0.1:8000").status()


def test_nicht_erreichbarer_server_wird_erklaert(monkeypatch):
    def fail(request, timeout=None):
        raise urllib.error.URLError("Verbindung abgelehnt")

    monkeypatch.setattr(desktop_windows.urllib.request, "urlopen", fail)
    with pytest.raises(DesktopError, match="nicht erreichbar"):
        ServerApi("http://127.0.0.1:8000").devices()
    assert desktop_windows.server_ready("http://127.0.0.1:8000") is False


def test_tray_lehnt_andere_betriebssysteme_ab(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit, match="nur für Windows"):
        desktop_windows.main()
    controller, _, _ = make_controller()
    app = desktop_windows.TrayApp(controller, port=8000, settings=Settings(data_dir=tmp_path))
    with pytest.raises(DesktopError, match="nur für Windows"):
        app.run()


class FakeMutexFunction:
    def __init__(self, handle):
        self.argtypes = None
        self.restype = None
        self._handle = handle

    def __call__(self, *args):
        return self._handle


class FakeKernel32:
    def __init__(self, handle):
        self.CreateMutexW = FakeMutexFunction(handle)


def patch_mutex(monkeypatch, handle, error):
    """``CreateMutexW`` und den gepufferten Fehler nachstellen (ohne echtes Win32)."""
    monkeypatch.setattr(desktop_windows, "_require_windows", lambda action: None)
    monkeypatch.setattr(desktop_windows.ctypes, "WinDLL",
                        lambda *args, **kwargs: FakeKernel32(handle), raising=False)
    monkeypatch.setattr(desktop_windows.ctypes, "get_last_error", lambda: error,
                        raising=False)


def test_freier_mutex_erlaubt_den_start(monkeypatch):
    patch_mutex(monkeypatch, handle=1, error=0)
    assert desktop_windows.single_instance_free() is True


def test_zweiter_tray_wird_erkannt(monkeypatch):
    patch_mutex(monkeypatch, handle=1, error=183)
    assert desktop_windows.single_instance_free() is False


def test_fehlender_mutex_wird_gemeldet(monkeypatch):
    patch_mutex(monkeypatch, handle=0, error=5)
    with pytest.raises(DesktopError, match="Fehler 5"):
        desktop_windows.single_instance_free()


def test_hilfsfunktionen():
    assert format_duration(0) == "00:00"
    assert format_duration(59.9) == "00:59"
    assert format_duration(3599) == "59:59"
    assert format_duration(3661) == "1:01:01"
    assert menu_text("Mikrofon & Co") == "Mikrofon && Co"
    assert recording_url("http://127.0.0.1:8000/", "a b") == "http://127.0.0.1:8000/#meeting=a%20b"
