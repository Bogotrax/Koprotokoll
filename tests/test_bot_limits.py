"""Abbruchgrenzen mit simulierter Uhr statt echter Wartezeit prüfen."""

from types import SimpleNamespace

import pytest

from koprotokoll.bot.runner import BotSession
from koprotokoll.config import Settings


@pytest.mark.parametrize("grace", [15, 30])
def test_alone_timeout_resets_when_participant_returns(tmp_path, monkeypatch, grace):
    monkeypatch.setenv("KOPROTOKOLL_BOT_ALONE_SECONDS", str(grace))
    manager = SimpleNamespace(s=Settings(data_dir=tmp_path))
    session = BotSession(manager, 1)
    session.alone_since = None
    session.last_sound = 1000
    clock = [1000.0]
    alone = [True]
    monkeypatch.setattr("koprotokoll.bot.runner.time.time", lambda: clock[0])
    page = SimpleNamespace(evaluate=lambda _: 0.01, wait_for_timeout=lambda _: None)
    adapter = SimpleNamespace(ended=lambda _: False, alone=lambda _: alone[0])

    assert session._check(page, adapter, 1000)
    clock[0] += grace - 1
    assert session._check(page, adapter, 1000)
    alone[0] = False
    assert session._check(page, adapter, 1000)
    alone[0] = True
    clock[0] += 1
    assert session._check(page, adapter, 1000)
    clock[0] += grace
    assert session._check(page, adapter, 1000)
    clock[0] += 1
    assert not session._check(page, adapter, 1000)


def test_silence_timeout_resets_after_speech(tmp_path, monkeypatch):
    monkeypatch.delenv("KOPROTOKOLL_BOT_SILENCE_MINUTES", raising=False)
    session = BotSession(SimpleNamespace(s=Settings(data_dir=tmp_path)), 1)
    session.last_sound, session.alone_since = 1000, None
    clock, level = [1059.0], [0.0]
    monkeypatch.setattr("koprotokoll.bot.runner.time.time", lambda: clock[0])
    page = SimpleNamespace(evaluate=lambda _: level[0], wait_for_timeout=lambda _: None)
    adapter = SimpleNamespace(ended=lambda _: False, alone=lambda _: False)

    assert session._check(page, adapter, 1000)
    level[0] = 0.01
    assert session._check(page, adapter, 1000)
    level[0] = 0.0
    clock[0] = 1119
    assert session._check(page, adapter, 1000)
    clock[0] = 1120
    assert not session._check(page, adapter, 1000)
