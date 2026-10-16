import shutil
import subprocess
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from koprotokoll.app import create_app
from koprotokoll.config import Settings
from koprotokoll.speech import Turn, Word, build_utterances, tokens_to_words
from koprotokoll.summarize import heuristic_summary, parse_json

SCRIPT = [
    (0.0, 0, "Guten Morgen zusammen, heute besprechen wir das Budget für das Projekt Atlas."),
    (5.0, 1, "Danke. Das Budget für Atlas liegt aktuell bei fünfzigtausend Euro."),
    (10.0, 0, "Ich werde die Kostenaufstellung bis Freitag an alle schicken."),
    (15.0, 1, "Wann startet der Pilot mit dem Kunden?"),
    (20.0, 0, "Der Pilot startet im November, das Budget reicht dafür."),
]


class FakeTranscriber:
    def transcribe(self, audio, progress):
        words = []
        for start, _, text in SCRIPT:
            toks = text.split()
            step = 4.0 / len(toks)
            words += [Word(start + i * step, start + (i + 1) * step, t) for i, t in enumerate(toks)]
        progress(1.0)
        return words


class FakeDiarizer:
    def diarize(self, audio, num_speakers, progress):
        progress(1.0)
        return [Turn(start, start + 4.5, spk) for start, spk, _ in SCRIPT]


class NoLLM:
    def available(self):
        return False


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, transcriber=FakeTranscriber(), diarizer=FakeDiarizer(), llm=NoLLM())
    with TestClient(app) as c:
        c.app = app
        yield c


def wav_bytes(tmp_path, seconds=25):
    import soundfile as sf

    p = tmp_path / "in.wav"
    sf.write(p, np.zeros(16000 * seconds, dtype=np.float32), 16000)
    return p.read_bytes()


def wait_done(client, mid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        m = client.get(f"/api/meetings/{mid}").json()
        if m["status"] in ("done", "error"):
            return m
        time.sleep(0.05)
    raise AssertionError("Verarbeitung nicht abgeschlossen")


def test_tokens_to_words_joins_subwords():
    words = tokens_to_words([" I", " have", " a", " dre", "am", "."], [0.0, 0.24, 0.48, 0.64, 0.88, 1.12], 1.0, 2.4)
    assert [w.text for w in words] == ["I", "have", "a", "dream."]
    assert words[0].start == pytest.approx(1.0)
    assert words[-1].start == pytest.approx(1.64)
    assert words[-1].end == pytest.approx(2.4)


def test_build_utterances_splits_on_speaker_change():
    words = [Word(0, 0.5, "Hallo"), Word(0.5, 1, "Welt."), Word(1.2, 1.6, "Hi"), Word(1.6, 2, "zurück.")]
    turns = [Turn(0, 1.1, 7), Turn(1.1, 2.2, 3)]
    utts = build_utterances(words, turns)
    assert [(u["speaker"], u["text"]) for u in utts] == [("S1", "Hallo Welt."), ("S2", "Hi zurück.")]


def test_build_utterances_without_diarization():
    utts = build_utterances([Word(0, 1, "Eins."), Word(1.1, 2, "Zwei.")], [])
    assert len(utts) == 1 and utts[0]["speaker"] == "S1"


def test_heuristic_summary_finds_keywords_and_actions():
    segs = [{"start": s, "end": s + 4, "speaker": f"S{k + 1}", "text": t} for s, k, t in SCRIPT]
    out = heuristic_summary(segs, {"S1": "Anna", "S2": "Ben"})
    assert "budget" in out["keywords"]
    assert any("Kostenaufstellung" in a["task"] and a["owner"] == "Anna" for a in out["action_items"])
    assert out["questions"] == ["Wann startet der Pilot mit dem Kunden?"]


def test_parse_json_tolerates_code_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_full_flow(client, tmp_path):
    r = client.post("/api/meetings", files={"file": ("weekly.wav", wav_bytes(tmp_path), "audio/wav")},
                    data={"title": "Weekly"})
    assert r.status_code == 201
    mid = r.json()["id"]
    m = wait_done(client, mid)
    assert m["status"] == "done", m["error"]
    assert m["duration"] == pytest.approx(25.0)
    assert [s["speaker"] for s in m["segments"]] == ["S1", "S2", "S1", "S2", "S1"]
    assert m["summary"]["engine"] == "heuristik"
    assert set(m["talk_time"]) == {"S1", "S2"}

    # Sprecher umbenennen
    client.patch(f"/api/meetings/{mid}", json={"speakers": {"S1": "Anna"}})
    assert client.get(f"/api/meetings/{mid}").json()["speakers"]["S1"] == "Anna"

    # Volltextsuche (Präfix, ohne Umlaut-Abhängigkeit)
    hits = client.get("/api/search", params={"q": "kostenauf"}).json()
    assert len(hits) == 1 and hits[0]["speaker_name"] == "Anna" and "[[" in hits[0]["snippet"]
    assert client.get("/api/search", params={"q": '"; DROP'}).status_code == 200

    # Markierungen
    b = client.post(f"/api/meetings/{mid}/bookmarks", json={"start": 10, "end": 14, "note": "Wichtig"}).json()
    assert len(client.get(f"/api/meetings/{mid}").json()["bookmarks"]) == 1

    # Frag das Meeting (ohne LLM → Suchtreffer)
    a = client.post(f"/api/meetings/{mid}/ask", json={"question": "Wann startet der Pilot?"}).json()
    assert a["engine"] == "suche" and a["hits"]

    # Exporte
    md = client.get(f"/api/meetings/{mid}/export", params={"format": "md"}).text
    assert "## Action Items" in md and "Anna" in md and "Wichtig" in md
    srt = client.get(f"/api/meetings/{mid}/export", params={"format": "srt"}).text
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert client.get(f"/api/meetings/{mid}/export", params={"format": "exe"}).status_code == 400

    assert client.get(f"/api/meetings/{mid}/audio").status_code == 200
    assert client.delete(f"/api/meetings/{mid}/bookmarks/{b['id']}").status_code == 200
    assert client.delete(f"/api/meetings/{mid}").status_code == 200
    assert client.get(f"/api/meetings/{mid}").status_code == 404
    assert client.get("/api/search", params={"q": "budget"}).json() == []


def test_rejects_unknown_file_type(client):
    r = client.post("/api/meetings", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_converts_compressed_upload(client, tmp_path):
    src = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=3", str(src)], check=True)
    r = client.post("/api/meetings", files={"file": ("tone.mp3", src.read_bytes(), "audio/mpeg")})
    m = wait_done(client, r.json()["id"])
    assert m["status"] == "done" and m["duration"] == pytest.approx(3.0, abs=0.1)


def test_index_served(client):
    assert "Koprotokoll" in client.get("/").text


def test_llm_summary_and_ask_via_openai_compatible_api(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from koprotokoll.summarize import LLM, ask, summarize

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"data": [{"id": "test"}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(req)
            if req.get("response_format"):
                content = '```json\n{"title": "Atlas Weekly", "overview": "Budget besprochen.", "keywords": ["Budget"],'
                content += ' "action_items": [{"owner": "Anna", "task": "Kosten schicken", "due": "Freitag", "time": "00:10"}]}\n```'
            else:
                content = "Der Pilot startet im November [00:20]."
            self._send({"choices": [{"message": {"content": content}}]})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        llm = LLM(Settings(data_dir=tmp_path, llm_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                           llm_model="test"))
        segs = [{"id": i, "start": s, "end": s + 4, "speaker": f"S{k + 1}", "text": t}
                for i, (s, k, t) in enumerate(SCRIPT)]
        out = summarize(segs, {"S1": "Anna"}, llm)
        assert out["engine"] == "llm:test" and out["title"] == "Atlas Weekly"
        assert out["action_items"][0]["owner"] == "Anna" and out["outline"] == []
        assert "[00:10] Anna: Ich werde" in calls[0]["messages"][1]["content"]
        assert ask("Wann startet der Pilot?", segs, {}, llm, [])["answer"].startswith("Der Pilot")
    finally:
        server.shutdown()
