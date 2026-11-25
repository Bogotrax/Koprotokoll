"""Doppeltipp-Erkennung für Windows ohne echte Aufnahme prüfen.

Die Fälle entsprechen der macOS-Prüfung in ``tests/fixtures/hotkey_macos_test.swift``;
``koprotokoll/windows_hotkey.py`` hält dieselben Zeitregeln ein wie ``macos_hotkey.swift``.
"""

import sys
import time

import pytest

from koprotokoll import windows_hotkey
from koprotokoll.windows_hotkey import (
    ConfigurableShortcut,
    ModifierDoubleTap,
    ShortcutModifier,
    ShortcutMonitor,
)


def count_taps(events):
    """Eine Zustandsfolge laufen lassen und Auslösungen zählen."""
    detector = ModifierDoubleTap()
    count = 0
    for now, down, other in events:
        if detector.update(down, other, now):
            count += 1
    return count


MAC_FAELLE = [
    ("zwei-kurze-alt-tipps",
     [(0.0, True, False), (0.05, False, False),
      (0.20, True, False), (0.25, False, False)], 1),
    ("halten-ist-kein-doppeltipp",
     [(0.0, True, False), (0.6, False, False),
      (0.7, True, False), (0.75, False, False)], 0),
    ("zu-grosser-abstand",
     [(0.0, True, False), (0.05, False, False),
      (1.0, True, False), (1.05, False, False)], 0),
    ("alt-kombination-unterbricht",
     [(0.0, True, False), (0.01, True, True),
      (0.05, False, False), (0.2, True, False),
      (0.25, False, False)], 0),
    ("andere-taste-zwischen-tipps",
     [(0.0, True, False), (0.05, False, False),
      (0.1, False, True), (0.2, True, False),
      (0.25, False, False)], 0),
    ("gehaltene-weitere-taste",
     [(0.0, True, True), (0.05, False, False),
      (0.2, True, False), (0.25, False, False)], 0),
    ("wiederholter-zustand-zaehlt-nicht",
     [(0.0, True, False), (0.01, True, False),
      (0.02, True, False), (0.05, False, False)], 0),
    ("zwei-doppeltipps-schalten-zweimal",
     [(0.0, True, False), (0.05, False, False),
      (0.2, True, False), (0.25, False, False),
      (0.4, True, False), (0.45, False, False),
      (0.6, True, False), (0.65, False, False)], 2),
]


@pytest.mark.parametrize("events, expected", [case[1:] for case in MAC_FAELLE],
                         ids=[case[0] for case in MAC_FAELLE])
def test_mac_faelle_gelten_auf_windows_gleich(events, expected):
    assert count_taps(events) == expected


def test_zeitregeln_bleiben_mit_macos_identisch():
    assert windows_hotkey.MAXIMUM_GAP == 0.45
    assert windows_hotkey.MAXIMUM_HOLD == 0.35


def test_verfallene_erste_reaktion_vergisst_die_taste():
    detector = ModifierDoubleTap()
    detector.update(True, False, 0.0)
    detector.update(False, False, 0.05)
    detector.update(False, False, 1.0)
    assert detector.needs_other_keys is False


def test_begonnener_tipp_meldet_andere_tasten_bis_zur_ausloesung():
    detector = ModifierDoubleTap()
    assert detector.needs_other_keys is False
    detector.update(True, False, 0.0)
    assert detector.needs_other_keys is True
    detector.update(False, False, 0.05)
    assert detector.needs_other_keys is True
    detector.update(True, False, 0.2)
    detector.update(False, False, 0.25)
    assert detector.needs_other_keys is False


def test_tastencodes_und_anzeigenamen():
    assert ShortcutModifier.ALT.virtual_keys == (0xA4, 0xA5, 0x12)
    assert ShortcutModifier.CONTROL.virtual_keys == (0xA2, 0xA3, 0x11)
    assert ShortcutModifier.SHIFT.virtual_keys == (0xA0, 0xA1, 0x10)
    assert ShortcutModifier.ALT.title == "Alt zweimal"
    assert ShortcutModifier.CONTROL.title == "Strg zweimal"
    assert ShortcutModifier.SHIFT.title == "Umschalt zweimal"


def test_ignorierte_tasten_stoeren_nicht():
    assert 0x14 in windows_hotkey.IGNORED_KEYS        # Feststelltaste
    assert 0x01 in windows_hotkey.IGNORED_KEYS        # linke Maustaste
    assert 0x06 in windows_hotkey.IGNORED_KEYS        # weitere Maustaste
    assert 0x41 not in windows_hotkey.IGNORED_KEYS     # Buchstaben stören
    assert 0xA2 not in windows_hotkey.IGNORED_KEYS     # Strg stört Alt-Doppeltipp


def test_ausgeschaltetes_kuerzel_loest_nie_aus():
    shortcut = ConfigurableShortcut()
    assert shortcut.update(True, False, 0.0) is False
    assert shortcut.update(False, False, 0.05) is False
    shortcut.configure(False, ShortcutModifier.ALT)
    assert shortcut.update(True, False, 0.2) is False
    assert shortcut.update(False, False, 0.25) is False


def test_wechsel_der_einstellungen_verwirft_begonnenen_tipp():
    shortcut = ConfigurableShortcut()
    shortcut.configure(True, ShortcutModifier.ALT)
    shortcut.update(True, False, 0.0)
    shortcut.update(False, False, 0.05)
    shortcut.configure(True, ShortcutModifier.CONTROL)
    shortcut.update(True, False, 0.2)
    assert shortcut.update(False, False, 0.25) is False
    shortcut.update(True, False, 0.4)
    assert shortcut.update(False, False, 0.45) is True


def test_ausschalten_verwirft_begonnenen_tipp():
    shortcut = ConfigurableShortcut()
    shortcut.configure(True, ShortcutModifier.CONTROL)
    shortcut.update(True, False, 1.0)
    shortcut.update(False, False, 1.05)
    shortcut.configure(False, ShortcutModifier.CONTROL)
    shortcut.update(True, False, 1.20)
    assert shortcut.update(False, False, 1.25) is False
    shortcut.configure(True, ShortcutModifier.CONTROL)
    shortcut.update(True, False, 1.30)
    assert shortcut.update(False, False, 1.35) is False


def test_monitor_erkennt_doppeltipp_ueber_die_zustaende(monkeypatch):
    pressed: set[int] = set()
    monkeypatch.setattr(windows_hotkey, "key_pressed", lambda vk: vk in pressed)
    monitor = ShortcutMonitor()
    monitor.configure(enabled=True, modifier=ShortcutModifier.ALT)
    pressed.add(0xA4)
    assert monitor.sample(now=0.0) is False
    pressed.clear()
    assert monitor.sample(now=0.05) is False
    pressed.add(0xA5)  # rechts zählt genauso wie links
    assert monitor.sample(now=0.20) is False
    pressed.clear()
    assert monitor.sample(now=0.25) is True


def test_monitor_bricht_bei_weiterer_taste_waehrend_des_tipps_ab(monkeypatch):
    pressed: set[int] = set()
    monkeypatch.setattr(windows_hotkey, "key_pressed", lambda vk: vk in pressed)
    monitor = ShortcutMonitor()
    monitor.configure(enabled=True, modifier=ShortcutModifier.ALT)
    pressed.add(0xA4)
    assert monitor.sample(now=0.0) is False
    pressed.update({0xA4, 0x41})  # Buchstabe während des ersten Tipps
    assert monitor.sample(now=0.05) is False
    pressed.clear()
    assert monitor.sample(now=0.20) is False
    assert monitor.sample(now=0.25) is False


def test_monitor_ignoriert_feststelltaste_und_maus(monkeypatch):
    pressed: set[int] = set()
    monkeypatch.setattr(windows_hotkey, "key_pressed", lambda vk: vk in pressed)
    monitor = ShortcutMonitor()
    monitor.configure(enabled=True, modifier=ShortcutModifier.ALT)
    pressed.update({0xA4, 0x14, 0x02})
    assert monitor.sample(now=0.0) is False
    pressed.clear(); pressed.add(0x14)
    assert monitor.sample(now=0.05) is False
    pressed.clear(); pressed.update({0xA5, 0x14})
    assert monitor.sample(now=0.20) is False
    pressed.clear(); pressed.add(0x14)
    assert monitor.sample(now=0.25) is True


def test_monitor_ohne_einstellungen_macht_nichts(monkeypatch):
    pressed: set[int] = set()
    monkeypatch.setattr(windows_hotkey, "key_pressed", lambda vk: vk in pressed)
    monitor = ShortcutMonitor()
    monitor.configure(enabled=False, modifier=ShortcutModifier.ALT)
    pressed.update({0xA4})
    assert monitor.sample(now=0.0) is False
    pressed.clear()
    assert monitor.sample(now=0.05) is False


def test_tastenabfrage_und_start_lehnen_fremde_betriebssysteme_ab(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="nur für Windows"):
        windows_hotkey.key_pressed(0xA4)
    with pytest.raises(RuntimeError, match="nur für Windows"):
        ShortcutMonitor().start(lambda: None)


def test_run_beendet_sofort_ohne_ausloesung():
    triggered: list = []
    monitor = ShortcutMonitor()
    monitor._stopped.set()
    monitor._run(triggered.append)
    assert triggered == []


@pytest.mark.skipif(sys.platform != "win32",
                    reason="Der Hintergrundthread braucht echte Windows-Tastenabfragen.")
def test_hintergrundthread_startet_und_stoppt_ohne_ausloesung():
    triggered: list = []
    monitor = ShortcutMonitor()
    monitor.configure(enabled=True, modifier=ShortcutModifier.ALT)
    monitor.start(triggered.append)
    time.sleep(0.05)
    monitor.stop()
    assert triggered == []


def test_monitor_ignoriert_den_allgemeinen_modifiercode(monkeypatch):
    """Windows meldet Modifikatortasten zusätzlich unter dem allgemeinen Code
    (VK_MENU 0x12, VK_CONTROL 0x11, VK_SHIFT 0x10). Der allgemeine Code ist
    dieselbe Taste und darf einen Tipp deshalb nicht unterbrechen."""
    for modifier, (spezifisch, allgemein) in (
        (ShortcutModifier.ALT, (0xA4, 0x12)),
        (ShortcutModifier.CONTROL, (0xA2, 0x11)),
        (ShortcutModifier.SHIFT, (0xA0, 0x10)),
    ):
        pressed: set[int] = set()
        monkeypatch.setattr(windows_hotkey, "key_pressed", lambda vk: vk in pressed)
        monitor = ShortcutMonitor()
        monitor.configure(enabled=True, modifier=modifier)
        pressed.update({spezifisch, allgemein})
        assert monitor.sample(now=0.0) is False
        pressed.clear()
        assert monitor.sample(now=0.05) is False
        pressed.update({spezifisch, allgemein})
        assert monitor.sample(now=0.20) is False
        pressed.clear()
        assert monitor.sample(now=0.25) is True