"""Logiktests für den Windows-Aufnahme-Helfer; kein echtes Audiogerät nötig."""

import ctypes
import io
import json
import struct
import threading
import time
import wave
from ctypes import c_void_p
from pathlib import Path

import numpy as np
import pytest

from koprotokoll import capture_windows as cw


_gehalten: list = []  # ctypes-Strukturen am Leben halten, solange ihre Zeiger gelesen werden.


def _format(tag: int, bits: int, channels: int = 2, rate: int = 48_000) -> c_void_p:
    kopf = cw.WAVEFORMATEX()
    kopf.wFormatTag = tag
    kopf.nChannels = channels
    kopf.nSamplesPerSec = rate
    kopf.wBitsPerSample = bits
    _gehalten.append(kopf)
    return ctypes.cast(ctypes.byref(kopf), c_void_p)


def _extensible(subformat: cw.GUID, bits: int) -> c_void_p:
    erweitert = cw.WAVEFORMATEXTENSIBLE()
    erweitert.Format.wFormatTag = cw.WAVE_FORMAT_EXTENSIBLE
    erweitert.Format.nChannels = 2
    erweitert.Format.nSamplesPerSec = 48_000
    erweitert.Format.wBitsPerSample = bits
    erweitert.SubFormat = subformat
    _gehalten.append(erweitert)
    return ctypes.cast(ctypes.byref(erweitert), c_void_p)


def test_pegel_db_begruendet_stille_und_rundet():
    assert cw.pegel_db(0.0) == -120.0
    assert cw.pegel_db(0.5) == -6.0
    assert cw.pegel_db(0.9) == -0.9


def test_zu_pcm16_begrenzt_uebersteuerung():
    feld = np.array([[0.5], [-2.0]], dtype=np.float32)
    erwartet = (np.clip(feld, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    assert cw.zu_pcm16(feld) == erwartet


def test_wav_spur_schreibt_liest_und_begrenzt_spitzenpegel(tmp_path):
    spur = cw.WavSpur("Systemton", tmp_path / "system.wav")
    spur.starten(48_000, 2)
    spur.schreibe(np.array([[0.25, -0.5], [0.125, 0.0]], dtype=np.float32))
    assert spur.pegel_und_reset() == pytest.approx(0.5)
    assert spur.pegel_und_reset() == 0.0
    assert spur.dauer() == pytest.approx(2 / 48_000)
    spur.schliessen()
    with wave.open(str(tmp_path / "system.wav"), "rb") as datei:
        assert (datei.getframerate(), datei.getnchannels(), datei.getsampwidth()) == (48_000, 2, 2)
        assert datei.getnframes() == 2
    with pytest.raises(RuntimeError, match="geschlossen"):
        spur.schreibe(np.zeros((1, 2), dtype=np.float32))


def test_format_lesen_erkennt_float32_und_pcm16():
    assert cw.format_lesen("Systemton", _format(cw.WAVE_FORMAT_IEEE_FLOAT, 32)) == (2, 48_000, "float32")
    assert cw.format_lesen("Mikrofon", _format(1, 16, channels=1, rate=44_100)) == (1, 44_100, "int16")
    assert cw.format_lesen("Mikrofon", _extensible(cw.KSDATAFORMAT_SUBTYPE_PCM, 16)) == (2, 48_000, "int16")
    assert cw.format_lesen("Systemton", _extensible(cw.KSDATAFORMAT_SUBTYPE_IEEE_FLOAT, 32)) == (2, 48_000, "float32")


def test_format_lesen_lehnt_unterstuetzte_formate_ab():
    with pytest.raises(RuntimeError, match="nicht unterstuetzt|nicht unterstützt"):
        cw.format_lesen("Systemton", _format(1, 24))
    with pytest.raises(RuntimeError, match="nicht unterstuetzt|nicht unterstützt"):
        cw.format_lesen("Systemton", _extensible(cw.KSDATAFORMAT_SUBTYPE_PCM, 32))
    with pytest.raises(RuntimeError, match="nicht unterstuetzt|nicht unterstützt"):
        cw.format_lesen("Mikrofon", _format(7, 16))


def test_lese_paket_wandelt_pcm16_mit_vorzeichen_und_stille():
    werte = np.array([-16384, 16384, 0, 1, -1, 32767, -32768, 2], dtype="<i2")
    feld = cw.lese_paket("Mikrofon", "int16", werte.ctypes.data_as(c_void_p), 4, 2, False)
    assert feld[:, 0].tolist() == pytest.approx([-0.5, 0.0, -1 / 32768, -1.0])
    assert feld[0, 1] == pytest.approx(0.5)
    stille = cw.lese_paket("Mikrofon", "int16", c_void_p(), 4, 2, True)
    assert stille.shape == (4, 2) and not stille.any()


def test_lese_paket_wandelt_float32_und_verwirft_leere_pakete():
    feld32 = np.array([[0.25, -0.125], [0.0, 0.5]], dtype=np.float32)
    feld = cw.lese_paket("Systemton", "float32", feld32.ctypes.data_as(c_void_p), 2, 2, False)
    assert feld.tolist() == [[0.25, -0.125], [0.0, 0.5]]
    leer = cw.lese_paket("Systemton", "float32", c_void_p(), 0, 2, False)
    assert leer.shape == (0, 2)


def test_stoppzeile_oder_eof_beendet_bewachung(monkeypatch):
    stop_event = threading.Event()
    monkeypatch.setattr(cw.sys, "stdin", io.StringIO("stop\n"))
    cw.stopp_bewachen(stop_event)
    assert stop_event.is_set()

    stop_event = threading.Event()
    monkeypatch.setattr(cw.sys, "stdin", io.StringIO(""))
    cw.stopp_bewachen(stop_event)
    assert stop_event.is_set()


def test_befehlszeile_verlangt_zwei_ziele():
    with pytest.raises(SystemExit):
        cw.parse_args([])


def test_fehler_kanal_behaelt_nur_den_ersten_text():
    fehler = cw.FehlerKanal()
    fehler.melden("erst")
    fehler.melden("zweit")
    assert fehler.abfragen() == "erst"


def test_status_zeilen_protokoll_meldet_bereitschaft_und_pegel(monkeypatch, capsys):
    monkeypatch.setattr(cw, "STATUS_INTERVALL", 0.02)
    stop_event = threading.Event()
    fehler = cw.FehlerKanal()
    system = cw.WavSpur("Systemton", Path("ohne-Belang"))
    mikrofon = cw.WavSpur("Mikrofon", Path("ohne-Belang"))
    thread = threading.Thread(target=cw.status_melden,
                              args=(system, mikrofon, fehler, stop_event), daemon=True)
    thread.start()
    system.erste.set()
    mikrofon.erste.set()
    time.sleep(0.12)
    stop_event.set()
    thread.join(timeout=2)
    zeilen = [json.loads(zeile) for zeile in capsys.readouterr().out.splitlines()]
    assert {"ready": True} in zeilen
    assert {"level_db": -120.0} in zeilen


def test_status_meldet_bereitschaft_erst_nach_beiden_spuren(monkeypatch, capsys):
    monkeypatch.setattr(cw, "STATUS_INTERVALL", 0.02)
    stop_event = threading.Event()
    fehler = cw.FehlerKanal()
    system = cw.WavSpur("Systemton", Path("ohne-Belang"))
    mikrofon = cw.WavSpur("Mikrofon", Path("ohne-Belang"))
    system.erste.set()  # Nur eine Spur hat geliefert; „ready" bleibt aus.
    thread = threading.Thread(target=cw.status_melden,
                              args=(system, mikrofon, fehler, stop_event), daemon=True)
    thread.start()
    time.sleep(0.1)
    stop_event.set()
    thread.join(timeout=2)
    zeilen = [json.loads(zeile) for zeile in capsys.readouterr().out.splitlines()]
    assert zeilen and not any(zeile == {"ready": True} for zeile in zeilen)


def test_status_melden_endet_bei_spurfehler(monkeypatch):
    monkeypatch.setattr(cw, "STATUS_INTERVALL", 0.01)
    stop_event = threading.Event()
    fehler = cw.FehlerKanal()
    system = cw.WavSpur("Systemton", Path("ohne-Belang"))
    mikrofon = cw.WavSpur("Mikrofon", Path("ohne-Belang"))
    thread = threading.Thread(target=cw.status_melden,
                              args=(system, mikrofon, fehler, stop_event), daemon=True)
    thread.start()
    fehler.melden("Systemton: kein Gerät")
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_vorspann_kuerzen_entfernt_anfangsbilder(tmp_path):
    pfad = tmp_path / "spur.wav"
    with wave.open(str(pfad), "wb") as datei:
        datei.setnchannels(2)
        datei.setsampwidth(2)
        datei.setframerate(48_000)
        datei.writeframes(b"".join(struct.pack("<hh", i, -i) for i in range(10)))
    assert cw.vorspann_kuerzen(pfad, 3) == 3
    with wave.open(str(pfad), "rb") as datei:
        assert (datei.getframerate(), datei.getnchannels(), datei.getnframes()) == (48_000, 2, 7)
        assert struct.unpack("<hh", datei.readframes(1)) == (3, -3)


def test_vorspann_kuerzen_ist_bei_null_und_zu_grossem_wert_harmlos(tmp_path):
    pfad = tmp_path / "spur.wav"
    with wave.open(str(pfad), "wb") as datei:
        datei.setnchannels(1)
        datei.setsampwidth(2)
        datei.setframerate(44_100)
        datei.writeframes(struct.pack("<hh", 1, 2))
    assert cw.vorspann_kuerzen(pfad, 0) == 0
    assert cw.vorspann_kuerzen(pfad, 5) == 0
    assert cw.vorspann_kuerzen(tmp_path / "fehlt.wav", 3) == 0
    with wave.open(str(pfad), "rb") as datei:
        assert datei.getnframes() == 2


def test_synchronisieren_haelt_stand_beider_spuren_fest(tmp_path):
    system = cw.WavSpur("Systemton", tmp_path / "system.wav")
    mikrofon = cw.WavSpur("Mikrofon", tmp_path / "mikrofon.wav")
    system.bilder = 5
    mikrofon.bilder = 2
    sync: dict[str, int] = {}
    thread = threading.Thread(target=cw.synchronisieren,
                              args=(system, mikrofon, sync), daemon=True)
    thread.start()
    time.sleep(0.05)  # Ohne Bereitschaft wartet die Synchronisierung noch.
    assert sync == {}
    system.erste.set()
    mikrofon.erste.set()
    thread.join(timeout=2)
    assert sync == {"Systemton": 5, "Mikrofon": 2}