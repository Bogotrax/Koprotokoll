"""Zusammenfassung, Stichwörter, Action Items und „Frag das Meeting“.

Primär über ein lokales LLM (OpenAI-kompatible API, z. B. Ollama). Ist keines
erreichbar, greift eine einfache extraktive Heuristik, damit die App vollständig
offline nutzbar bleibt.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections import Counter

from .config import Settings

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# LLM-Client
# --------------------------------------------------------------------------
class LLM:
    def __init__(self, settings: Settings):
        self.s = settings

    def available(self) -> bool:
        if not self.s.llm_enabled:
            return False
        try:
            req = urllib.request.Request(self.s.llm_base_url.rstrip("/") + "/models", headers=self._headers())
            with urllib.request.urlopen(req, timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _headers(self) -> dict:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.s.llm_api_key}"}

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        body: dict = {
            "model": self.s.llm_model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.s.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(), headers=self._headers(), method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.s.llm_timeout) as r:
            data = json.load(r)
        return data["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Keine JSON-Antwort")
    return json.loads(text[start:end + 1])


# --------------------------------------------------------------------------
# Transkript-Formatierung
# --------------------------------------------------------------------------
def fmt_time(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_text(segments: list[dict], names: dict[str, str]) -> str:
    return "\n".join(
        f"[{fmt_time(s['start'])}] {names.get(s['speaker'], s['speaker'])}: {s['text']}" for s in segments
    )


def _chunks(text: str, size: int) -> list[str]:
    lines, chunks, cur = text.splitlines(), [], ""
    for line in lines:
        if len(cur) + len(line) > size and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    return chunks


# --------------------------------------------------------------------------
# Zusammenfassung
# --------------------------------------------------------------------------
SUMMARY_SYSTEM = """Du bist ein präziser Meeting-Assistent. Antworte ausschließlich mit JSON in der
Sprache des Transkripts. Erfinde nichts, was nicht im Transkript steht. Schema:
{
  "title": "kurzer, aussagekräftiger Meeting-Titel",
  "overview": "2-4 Sätze Zusammenfassung",
  "keywords": ["5-10 Stichwörter"],
  "outline": [{"time": "mm:ss", "topic": "Thema", "notes": ["Stichpunkt", "..."]}],
  "action_items": [{"owner": "Name oder null", "task": "Aufgabe", "due": "Frist oder null", "time": "mm:ss"}],
  "decisions": ["getroffene Entscheidungen"],
  "questions": ["offene Fragen"]
}"""

CHUNK_SYSTEM = """Fasse diesen Abschnitt eines Meeting-Transkripts stichpunktartig zusammen.
Behalte Zeitmarken [mm:ss], Namen, Zahlen, Entscheidungen, Aufgaben und Fristen bei."""

MAX_CONTEXT_CHARS = 24_000


def summarize(segments: list[dict], names: dict[str, str], llm: LLM | None) -> dict:
    if not segments:
        return {"engine": "none", "overview": "Kein gesprochener Inhalt erkannt.", "keywords": [],
                "outline": [], "action_items": [], "decisions": [], "questions": []}
    if llm is not None and llm.available():
        try:
            return _llm_summary(segments, names, llm)
        except Exception as e:  # noqa: BLE001 – LLM-Fehler dürfen die Verarbeitung nicht abbrechen
            log.warning("LLM-Zusammenfassung fehlgeschlagen, nutze Heuristik: %s", e)
    return heuristic_summary(segments, names)


def _llm_summary(segments: list[dict], names: dict[str, str], llm: LLM) -> dict:
    text = transcript_text(segments, names)
    if len(text) > MAX_CONTEXT_CHARS:
        # Map-Reduce: Abschnitte einzeln verdichten, dann gemeinsam zusammenfassen.
        partial = [llm.chat(CHUNK_SYSTEM, c) for c in _chunks(text, MAX_CONTEXT_CHARS)]
        text = "\n\n".join(partial)
    data = parse_json(llm.chat(SUMMARY_SYSTEM, text, json_mode=True))
    result = {k: data.get(k) or [] for k in ("keywords", "outline", "action_items", "decisions", "questions")}
    result["overview"] = data.get("overview") or ""
    result["title"] = data.get("title") or None
    result["engine"] = f"llm:{llm.s.llm_model}"
    return result


# --------------------------------------------------------------------------
# Offline-Heuristik
# --------------------------------------------------------------------------
STOPWORDS = set("""
aber alle allem allen aller alles als also am an ander andere anderen auch auf aus bei beim bin bis bist da dabei
damit dann das dass daß dein deine dem den denn der des dich die dies diese diesem diesen dieser dieses dir doch dort
du durch ein eine einem einen einer eines einfach er es etwas euch euer für gegen genau gibt gut habe haben hat hatte
hier hin hinter ich ihm ihn ihnen ihr ihre im in ins ist ja jetzt kann kannst kein keine können könnte machen man
mal mehr mein meine mich mir mit muss müssen nach nein nicht nichts noch nun nur ob oder ohne okay schon sehr sein
seine sich sie sind so solche soll sollen sollte sondern sowie über um und uns unser unter viel vom von vor wann
war waren was weil weiter welche wenn wer werde werden wie wieder will wir wird wirklich wo würde zu zum zur zwar
a about after again all also am an and any are as at be because been but by can could did do does doing for from
get go going got had has have he her here him his how i if in into is it its just know like me more my no not now
of on one or our out really right so some that the their them then there these they thing think this to up us
very was we well were what when where which who will with would yeah yes you your
""".split())

ACTION_PATTERNS = [
    r"\b(ich|wir)\s+(werde|werden|kümmere|kümmern|übernehme|übernehmen|schicke|schicken|sende|senden|melde|melden)\b",
    r"\b(muss|müssen|sollte|sollten|soll|sollen)\b.{3,}\b(bis|noch|nächste|morgen|heute)\b",
    r"\b(bitte|to-?do|aufgabe|action item|nachfassen|follow[- ]?up)\b",
    r"\bbis\s+(montag|dienstag|mittwoch|donnerstag|freitag|morgen|ende|nächste)",
    r"\b(i|we)('ll| will| need to| have to| should)\b",
    r"\b(let's|lets|make sure|deadline|by (monday|tuesday|wednesday|thursday|friday|tomorrow|next))\b",
]
_ACTION_RE = [re.compile(p, re.I) for p in ACTION_PATTERNS]
_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß-]{2,}")


def _sentences(segments: list[dict]) -> list[dict]:
    out = []
    for s in segments:
        for part in re.split(r"(?<=[.!?])\s+", s["text"]):
            if part.strip():
                out.append({"text": part.strip(), "start": s["start"], "speaker": s["speaker"]})
    return out


def heuristic_summary(segments: list[dict], names: dict[str, str]) -> dict:
    sents = _sentences(segments)
    freq = Counter(
        w.lower() for s in sents for w in _WORD_RE.findall(s["text"]) if w.lower() not in STOPWORDS
    )
    keywords = [w for w, n in freq.most_common(10) if n > 1] or [w for w, _ in freq.most_common(5)]

    def score(s: dict) -> float:
        ws = [w.lower() for w in _WORD_RE.findall(s["text"])]
        return sum(freq[w] for w in ws if w not in STOPWORDS) / (len(ws) ** 0.5 or 1)

    ranked = sorted(sents, key=score, reverse=True)[: max(3, min(8, len(sents) // 8))]
    top = sorted(ranked, key=lambda s: s["start"])

    actions = []
    for s in sents:
        if any(r.search(s["text"]) for r in _ACTION_RE) and len(s["text"].split()) >= 4:
            actions.append({"owner": names.get(s["speaker"], s["speaker"]), "task": s["text"],
                            "due": None, "time": fmt_time(s["start"])})

    # Gliederung: Transkript in bis zu 6 gleich lange Zeitabschnitte teilen.
    n_parts = min(6, max(1, len(segments) // 6))
    size = -(-len(segments) // n_parts)
    outline = []
    for i in range(0, len(segments), size):
        part = segments[i:i + size]
        part_sents = [s for s in sents if part[0]["start"] <= s["start"] <= part[-1]["start"]]
        best = sorted(part_sents, key=score, reverse=True)[:2]
        part_freq = Counter(w.lower() for s in part_sents for w in _WORD_RE.findall(s["text"])
                            if w.lower() not in STOPWORDS)
        topic = ", ".join(w.capitalize() for w, _ in part_freq.most_common(3)) or "Abschnitt"
        outline.append({"time": fmt_time(part[0]["start"]), "topic": topic,
                        "notes": [b["text"] for b in sorted(best, key=lambda s: s["start"])]})

    return {
        "engine": "heuristik",
        "title": None,
        "overview": " ".join(s["text"] for s in top[:3]),
        "keywords": keywords,
        "outline": outline,
        "action_items": actions[:20],
        "decisions": [],
        "questions": [s["text"] for s in sents if s["text"].endswith("?") and len(s["text"].split()) >= 3][:10],
    }


# --------------------------------------------------------------------------
# „Frag das Meeting“
# --------------------------------------------------------------------------
ASK_SYSTEM = """Beantworte die Frage ausschließlich anhand des Meeting-Transkripts.
Nenne relevante Zeitmarken im Format [mm:ss]. Wenn die Antwort nicht im Transkript steht, sag das."""


def ask(question: str, segments: list[dict], names: dict[str, str], llm: LLM | None, search_hits: list[dict]) -> dict:
    if llm is not None and llm.available():
        text = transcript_text(segments, names)
        if len(text) > MAX_CONTEXT_CHARS:
            # Nur Umgebung der Suchtreffer mitgeben.
            keep = {h["segment_id"] for h in search_hits}
            ids = [i for i, s in enumerate(segments) if s.get("id") in keep]
            window = sorted({j for i in ids for j in range(max(0, i - 3), min(len(segments), i + 4))})
            text = transcript_text([segments[j] for j in window], names)[:MAX_CONTEXT_CHARS]
        answer = llm.chat(ASK_SYSTEM, f"Transkript:\n{text}\n\nFrage: {question}")
        return {"engine": f"llm:{llm.s.llm_model}", "answer": answer, "hits": search_hits[:5]}
    if search_hits:
        lines = [f"[{fmt_time(h['start'])}] {h['speaker_name']}: {h['snippet'].replace('[[', '').replace(']]', '')}"
                 for h in search_hits[:5]]
        answer = "Kein lokales LLM erreichbar – passende Stellen im Transkript:\n" + "\n".join(lines)
    else:
        answer = "Kein lokales LLM erreichbar und keine passenden Stellen im Transkript gefunden."
    return {"engine": "suche", "answer": answer, "hits": search_hits[:5]}
