"""Globaler Doppeltipp auf Alt, Strg oder Umschalt für Windows.

Die Regeln sind mit der macOS-Umsetzung (``macos_hotkey.swift``) identisch:
Zwei kurze, einzelne Tipps auf dieselbe Modifikatortaste schalten die
Aufnahme um. Ein Tipp darf höchstens ``MAXIMUM_HOLD`` Sekunden gedrückt
bleiben, zwischen beiden Tipps dürfen höchstens ``MAXIMUM_GAP`` Sekunden
liegen, und jede weitere Taste während des Tipps unterbricht den Vorgang.

Statt eines Systemhocks liest der Monitor wie unter macOS nur den
Tastenzustand des Betriebssystems (alle ``INTERVAL`` Sekunden, unter
Windows über ``GetAsyncKeyState``). Die Überwachung verändert dadurch
kein Verhalten anderer Anwendungen. Die Erkennung selbst ist frei von
Betriebssystemaufrufen gehalten und damit überall ohne echte Aufnahme
prüfbar.
"""

from __future__ import annotations

import ctypes
import enum
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAXIMUM_GAP = 0.45  # Sekunden zwischen den beiden Tipps
MAXIMUM_HOLD = 0.35  # Sekunden, die ein einzelner Tipp höchstens dauert


class ShortcutModifier(enum.Enum):
    """Die Doppeltipp-Taste mit linkem und rechtem Windows-Tastencode."""

    ALT = (0xA4, 0xA5, 0x12)      # VK_LMENU, VK_RMENU, VK_MENU
    CONTROL = (0xA2, 0xA3, 0x11)  # VK_LCONTROL, VK_RCONTROL, VK_CONTROL
    SHIFT = (0xA0, 0xA1, 0x10)    # VK_LSHIFT, VK_RSHIFT, VK_SHIFT

    @property
    def virtual_keys(self) -> tuple[int, ...]:
        """Alle Tastencodes der Taste: links, rechts und der allgemeine Code."""
        return self.value

    @property
    def title(self) -> str:
        """Anzeigename für Einstellungen."""
        names = {ShortcutModifier.ALT: "Alt zweimal",
                 ShortcutModifier.CONTROL: "Strg zweimal",
                 ShortcutModifier.SHIFT: "Umschalt zweimal"}
        return names[self]


@dataclass
class ModifierDoubleTap:
    """Zwei kurze Tipps auf eine Modifikatortaste erkennen (reine Logik)."""

    was_down: bool = False
    pressed_at: float = 0.0
    first_release: float | None = None
    interrupted: bool = False

    @property
    def needs_other_keys(self) -> bool:
        """Ob ein Tipp begonnen hat und andere Tasten stören würden."""
        return self.was_down or self.first_release is not None

    def update(self, down: bool, other_key: bool, now: float) -> bool:
        """Einen abgefragten Tastenzustand verarbeiten; True beim Doppeltipp."""
        triggered = False
        if self.first_release is not None and now - self.first_release > MAXIMUM_GAP:
            self.first_release = None
        if other_key:
            self.first_release = None
            self.interrupted = True
        if down and not self.was_down:
            self.pressed_at = now
            self.interrupted = other_key
        if not down and self.was_down:
            if not self.interrupted and now - self.pressed_at <= MAXIMUM_HOLD:
                if self.first_release is not None and now - self.first_release <= MAXIMUM_GAP:
                    triggered = True
                    self.first_release = None
                else:
                    self.first_release = now
            else:
                self.first_release = None
            self.interrupted = False
        self.was_down = down
        return triggered


@dataclass
class ConfigurableShortcut:
    """Das ein- und ausschaltbare Kürzel mit gewählter Doppeltipp-Taste."""

    enabled: bool = False
    modifier: ShortcutModifier = ShortcutModifier.ALT
    detector: ModifierDoubleTap = field(default_factory=ModifierDoubleTap)

    @property
    def needs_other_keys(self) -> bool:
        """Ob ein Tipp begonnen hat und andere Tasten stören würden."""
        return self.detector.needs_other_keys

    def configure(self, enabled: bool, modifier: ShortcutModifier) -> None:
        """Einstellungen übernehmen; ein begonnener Tipp wird verworfen."""
        if self.enabled != enabled or self.modifier != modifier:
            self.detector = ModifierDoubleTap()
        self.enabled = enabled
        self.modifier = modifier

    def update(self, down: bool, other_key: bool, now: float) -> bool:
        """Nur bei eingeschaltetem Kürzel fließt der Zustand in die Erkennung."""
        return self.enabled and self.detector.update(down, other_key, now)


# Tasten, die einen Tipp nicht unterbrechen: die Feststelltaste und die
# Maustasten (Tastencodes 1 bis 6). Sie ändern die Bedeutung der
# Modifikatortaste nicht und dürfen einen begonnenen Tipp nicht verwerfen.
IGNORED_KEYS = frozenset({0x14}) | frozenset(range(0x01, 0x07))


def key_pressed(virtual_key: int) -> bool:
    """Den aktuellen, globalen Zustand einer Taste abfragen (nur Windows)."""
    if sys.platform != "win32":
        raise RuntimeError("Die Tastenabfrage ist in dieser Version nur für Windows verfügbar.")
    return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000)


class ShortcutMonitor:
    """Die gewählte Taste im Hintergrund durch regelmäßiges Abfragen beobachten."""

    INTERVAL = 0.01  # Sekunden zwischen zwei Abfragen

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._detector = ConfigurableShortcut()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def configure(self, enabled: bool, modifier: ShortcutModifier) -> None:
        """Einstellungen übernehmen; ein begonnener Tipp wird verworfen."""
        with self._lock:
            self._detector.configure(enabled, modifier)

    def sample(self, now: float | None = None) -> bool:
        """Einen Abfragemoment auswerten; True beim erkannten Doppeltipp."""
        with self._lock:
            detector = self._detector
            if not detector.enabled:
                return False
            keys = detector.modifier.virtual_keys
            down = any(key_pressed(virtual_key) for virtual_key in keys)
            other = (down or detector.needs_other_keys) and any(
                key_pressed(virtual_key)
                for virtual_key in range(1, 0xFF)
                if virtual_key not in keys and virtual_key not in IGNORED_KEYS
            )
            return detector.update(down, other, time.monotonic() if now is None else now)

    def start(self, on_trigger: Callable[[], object]) -> None:
        """Den Hintergrund-Thread starten; der Aufruf wartet nicht auf Tasten."""
        if sys.platform != "win32":
            raise RuntimeError("Die Tastenüberwachung ist in dieser Version nur für Windows verfügbar.")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        thread = threading.Thread(target=self._run, args=(on_trigger,), daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Den Hintergrund-Thread beenden und auf ihn warten."""
        self._stopped.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def _run(self, on_trigger: Callable[[], object]) -> None:
        while not self._stopped.wait(self.INTERVAL):
            if self.sample():
                on_trigger()