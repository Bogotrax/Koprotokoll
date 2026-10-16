"use strict";

const $ = (sel) => document.querySelector(sel);
const STATUS = {
  queued: "In Warteschlange", converting: "Konvertiere Audio", transcribing: "Transkribiere (Parakeet)",
  diarizing: "Erkenne Sprecher", summarizing: "Erstelle Zusammenfassung", done: "Fertig", error: "Fehler",
};
const COLORS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];

const state = { meetings: [], current: null, segEls: [], pollTimer: null };

// ---------------------------------------------------------------- Helfer
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined) el.append(c);
  return el;
}

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`;
}

function parseTime(str) {
  if (typeof str !== "string") return null;
  const parts = str.split(":").map(Number);
  if (parts.some(isNaN)) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

function color(speaker) {
  const n = parseInt(String(speaker).replace(/\D/g, ""), 10) || 1;
  return `var(${COLORS[(n - 1) % COLORS.length]})`;
}

function speakerName(spk) {
  return state.current?.speakers?.[spk] || spk;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

const jsonOpts = (method, body) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

// Text mit [[Treffer]]-Markierungen sicher als DOM rendern.
function highlighted(text) {
  const out = [];
  text.split(/(\[\[.*?\]\])/).forEach((part) => {
    if (part.startsWith("[[") && part.endsWith("]]")) out.push(h("mark", {}, part.slice(2, -2)));
    else if (part) out.push(part);
  });
  return out;
}

function tsLink(sec, label) {
  return h("a", { class: "ts", href: "#", onclick: (e) => { e.preventDefault(); seek(sec); } }, label ?? fmt(sec));
}

// --------------------------------------------------------- Meeting-Liste
async function loadMeetings() {
  state.meetings = await api("/api/meetings");
  const list = $("#meeting-list");
  list.replaceChildren(...state.meetings.map((m) => {
    const sub = m.status === "done"
      ? `${new Date(m.created_at * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })} · ${fmt(m.duration)}`
      : `${STATUS[m.status] || m.status}${m.status !== "error" ? ` · ${Math.round(m.progress * 100)} %` : ""}`;
    return h("div", {
      class: "m-item" + (state.current?.id === m.id ? " active" : ""),
      onclick: () => openMeeting(m.id),
    }, h("div", { class: "t" }, m.title), h("div", { class: "s" }, sub));
  }));
  const busy = state.meetings.some((m) => !["done", "error"].includes(m.status));
  clearTimeout(state.pollTimer);
  if (busy) state.pollTimer = setTimeout(refresh, 1500);
}

async function refresh() {
  await loadMeetings();
  if (state.current) {
    const m = state.meetings.find((x) => x.id === state.current.id);
    if (m && (m.status !== state.current.status || m.progress !== state.current.progress)) {
      await openMeeting(m.id, { keepTab: true });
    }
  }
}

// ------------------------------------------------------------ Meeting-Ansicht
async function openMeeting(id, { keepTab = false, seekTo = null } = {}) {
  const m = await api(`/api/meetings/${id}`);
  const changedMeeting = state.current?.id !== id;
  state.current = m;
  $("#empty").hidden = true;
  $("#meeting").hidden = false;
  $("#m-title").textContent = m.title;
  const created = new Date(m.created_at * 1000).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
  $("#m-meta").textContent = [created, m.duration ? fmt(m.duration) : null,
    Object.keys(m.speakers).length ? `${Object.keys(m.speakers).length} Sprecher` : null,
    m.summary?.engine ? `Zusammenfassung: ${m.summary.engine}` : null].filter(Boolean).join(" · ");

  const done = m.status === "done";
  $("#m-progress").hidden = done;
  if (!done) {
    $("#m-progress-bar").style.width = `${Math.round(m.progress * 100)}%`;
    $("#m-progress-label").textContent = m.status === "error"
      ? `Fehler: ${m.error}` : `${STATUS[m.status] || m.status} … ${Math.round(m.progress * 100)} %`;
  }
  const player = $("#player");
  if (changedMeeting) player.src = `/api/meetings/${id}/audio`;

  renderSummary();
  renderTranscript();
  renderBookmarks();
  if (changedMeeting) $("#ask-answer").replaceChildren();
  if (!keepTab) showTab(done ? "summary" : "transcript");
  document.querySelectorAll(".m-item").forEach((el, i) =>
    el.classList.toggle("active", state.meetings[i]?.id === id));
  if (seekTo !== null) { showTab("transcript"); seek(seekTo); }
}

function seek(sec) {
  const p = $("#player");
  const go = () => { p.currentTime = sec; p.play().catch(() => {}); };
  if (p.readyState >= 1) go(); else p.addEventListener("loadedmetadata", go, { once: true });
}

function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
}

function renderSummary() {
  const m = state.current, s = m.summary;
  const box = $("#tab-summary");
  if (!s) {
    box.replaceChildren(h("p", { class: "muted" }, "Die Zusammenfassung erscheint, sobald die Verarbeitung abgeschlossen ist."));
    return;
  }
  const timeOf = (t) => parseTime(t);
  const left = [
    h("div", { class: "card" }, h("h3", {}, "Zusammenfassung"), h("p", {}, s.overview || "–"),
      s.keywords?.length ? h("div", { class: "chips" }, s.keywords.map((k) => h("span", { class: "chip" }, k))) : null),
  ];
  if (s.action_items?.length) {
    left.push(h("div", { class: "card" }, h("h3", {}, `Action Items (${s.action_items.length})`),
      h("ul", { class: "list" }, s.action_items.map((a) => h("li", {},
        h("input", { type: "checkbox", style: "width:auto" }),
        h("span", { class: "grow" }, a.owner ? h("b", {}, `${a.owner}: `) : null, a.task,
          a.due ? h("span", { class: "muted" }, ` (bis ${a.due})`) : null),
        timeOf(a.time) !== null ? tsLink(timeOf(a.time), a.time) : null)))));
  }
  if (s.outline?.length) {
    left.push(h("div", { class: "card" }, h("h3", {}, "Gliederung"),
      h("ul", { class: "list" }, s.outline.map((o) => h("li", {},
        timeOf(o.time) !== null ? tsLink(timeOf(o.time), o.time) : null,
        h("div", { class: "grow" }, h("b", {}, o.topic),
          h("ul", {}, (o.notes || []).map((n) => h("li", { style: "display:list-item;border:none;padding:2px 0" }, n)))))))));
  }
  for (const [key, title] of [["decisions", "Entscheidungen"], ["questions", "Offene Fragen"]]) {
    if (s[key]?.length) {
      left.push(h("div", { class: "card" }, h("h3", {}, title),
        h("ul", { class: "list" }, s[key].map((x) => h("li", {}, x)))));
    }
  }

  const total = Object.values(m.talk_time).reduce((a, b) => a + b, 0) || 1;
  const speakers = h("div", { class: "card" }, h("h3", {}, "Sprecher & Redeanteil"),
    Object.keys(m.speakers).map((spk) => {
      const input = h("input", { value: speakerName(spk), "aria-label": `Name für ${spk}` });
      input.addEventListener("change", async () => {
        await api(`/api/meetings/${m.id}`, jsonOpts("PATCH", { speakers: { [spk]: input.value } }));
        await openMeeting(m.id, { keepTab: true });
      });
      const pct = Math.round((m.talk_time[spk] || 0) / total * 100);
      return h("div", {},
        h("div", { class: "speaker-row" }, h("span", { class: "swatch", style: `background:${color(spk)}` }),
          input, h("span", { class: "muted" }, `${pct} %`)),
        h("div", { class: "talk" }, h("div", { style: `width:${pct}%;background:${color(spk)}` })));
    }),
    h("p", { class: "muted", style: "font-size:12px" }, "Namen ändern: Feld bearbeiten und Enter drücken."));
  const tools = h("div", { class: "card" }, h("h3", {}, "Werkzeuge"),
    h("button", { onclick: async (e) => {
      e.target.disabled = true; e.target.textContent = "Erstelle …";
      try { await api(`/api/meetings/${m.id}/summarize`, { method: "POST" }); await openMeeting(m.id, { keepTab: true }); }
      catch (err) { alert(err.message); e.target.disabled = false; }
    } }, "Zusammenfassung neu erstellen"));

  box.replaceChildren(h("div", { class: "grid2" }, h("div", {}, left), h("div", {}, speakers, tools)));
}

function renderTranscript() {
  const m = state.current;
  const q = $("#t-filter").value.trim().toLowerCase();
  const box = $("#transcript");
  if (!m.segments.length) {
    box.replaceChildren(h("p", { class: "muted" }, m.status === "done" ? "Keine Sprache erkannt." : "Transkript wird erstellt …"));
    state.segEls = [];
    return;
  }
  state.segEls = m.segments.map((s) => {
    let text = [s.text];
    if (q) {
      const idx = s.text.toLowerCase().indexOf(q);
      if (idx < 0) return null;
      text = [s.text.slice(0, idx), h("mark", {}, s.text.slice(idx, idx + q.length)), s.text.slice(idx + q.length)];
    }
    const el = h("div", { class: "seg", "data-start": s.start, "data-end": s.end },
      h("div", { class: "who", style: `color:${color(s.speaker)}` }, speakerName(s.speaker), tsLink(s.start)),
      h("div", {}, text),
      h("button", { class: "mark", title: "Als Soundbite markieren", onclick: async () => {
        await api(`/api/meetings/${m.id}/bookmarks`, jsonOpts("POST",
          { start: s.start, end: s.end, kind: "soundbite", note: `${speakerName(s.speaker)}: ${s.text}` }));
        await openMeeting(m.id, { keepTab: true });
      } }, "★"));
    return el;
  }).filter(Boolean);
  box.replaceChildren(...state.segEls);
}

function renderBookmarks() {
  const m = state.current;
  const labels = { soundbite: "Soundbite", action: "Action Item", comment: "Kommentar" };
  $("#bookmarks").replaceChildren(...(m.bookmarks.length ? m.bookmarks.map((b) => h("li", {},
    tsLink(b.start), h("span", { class: "tag" }, labels[b.kind] || b.kind), h("span", { class: "grow" }, b.note),
    h("button", { title: "Entfernen", onclick: async () => {
      await api(`/api/meetings/${m.id}/bookmarks/${b.id}`, { method: "DELETE" });
      await openMeeting(m.id, { keepTab: true });
    } }, "✕"))) : [h("li", { class: "muted" }, "Noch keine Markierungen. Im Transkript auf ★ klicken oder oben eine Notiz zur Abspielposition anlegen.")]));
}

// Aktuelles Segment beim Abspielen hervorheben.
$("#player").addEventListener("timeupdate", () => {
  const t = $("#player").currentTime;
  for (const el of state.segEls) {
    const on = t >= +el.dataset.start && t < +el.dataset.end + 0.3;
    if (on && !el.classList.contains("current")) {
      el.classList.add("current");
      if (!$("#tab-transcript").hidden) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (!on) el.classList.remove("current");
  }
});

// ---------------------------------------------------------- Events
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("#t-filter").addEventListener("input", renderTranscript);

$("#m-title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } });
$("#m-title").addEventListener("blur", async () => {
  const title = $("#m-title").textContent.trim();
  if (state.current && title && title !== state.current.title) {
    await api(`/api/meetings/${state.current.id}`, jsonOpts("PATCH", { title }));
    state.current.title = title;
    await loadMeetings();
  }
});

$("#m-export").addEventListener("change", (e) => {
  if (e.target.value && state.current) window.location = `/api/meetings/${state.current.id}/export?format=${e.target.value}`;
  e.target.value = "";
});

$("#m-delete").addEventListener("click", async () => {
  if (!state.current || !confirm(`„${state.current.title}“ wirklich löschen?`)) return;
  await api(`/api/meetings/${state.current.id}`, { method: "DELETE" });
  state.current = null;
  $("#player").removeAttribute("src");
  $("#meeting").hidden = true;
  $("#empty").hidden = false;
  await loadMeetings();
});

$("#bm-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const t = $("#player").currentTime || 0;
  await api(`/api/meetings/${state.current.id}/bookmarks`, jsonOpts("POST",
    { start: t, end: t, kind: $("#bm-kind").value, note: $("#bm-note").value.trim() }));
  $("#bm-note").value = "";
  await openMeeting(state.current.id, { keepTab: true });
});

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#ask-answer");
  out.replaceChildren(h("p", { class: "muted" }, "Denke nach …"));
  try {
    const r = await api(`/api/meetings/${state.current.id}/ask`, jsonOpts("POST", { question: $("#ask-q").value }));
    out.replaceChildren(h("div", { class: "card" }, h("div", {}, r.answer),
      r.hits.length ? h("p", { class: "muted", style: "font-size:13px" }, "Fundstellen: ",
        r.hits.map((x) => [tsLink(x.start), " "])) : null,
      h("p", { class: "muted", style: "font-size:12px" }, `Engine: ${r.engine}`)));
  } catch (err) { out.replaceChildren(h("p", {}, `Fehler: ${err.message}`)); }
});

// Globale Suche
let searchTimer;
$("#global-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = e.target.value.trim();
    const box = $("#search-results");
    if (!q) { box.hidden = true; return; }
    const hits = await api(`/api/search?q=${encodeURIComponent(q)}`);
    box.hidden = false;
    box.replaceChildren(...(hits.length ? hits.map((x) => h("div", {
      class: "hit", onclick: () => openMeeting(x.meeting_id, { seekTo: x.start }),
    }, h("b", {}, `${x.title} · ${x.speaker_name} · ${fmt(x.start)}`), highlighted(x.snippet)))
      : [h("div", { class: "hit muted" }, "Keine Treffer")]));
  }, 200);
});

// ---------------------------------------------------------- Upload
async function uploadBlob(blob, filename, title, numSpeakers) {
  const fd = new FormData();
  fd.append("file", blob, filename);
  fd.append("title", title || "");
  if (numSpeakers) fd.append("num_speakers", numSpeakers);
  const m = await api("/api/meetings", { method: "POST", body: fd });
  await loadMeetings();
  await openMeeting(m.id);
}

$("#btn-upload").addEventListener("click", () => $("#dlg-upload").showModal());
$("#upload-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "ok") return;
  const file = $("#up-file").files[0];
  if (!file) return;
  try { await uploadBlob(file, file.name, $("#up-title").value, $("#up-speakers").value); }
  catch (err) { alert(`Upload fehlgeschlagen: ${err.message}`); }
  e.target.reset();
});

// ---------------------------------------------------------- Aufnahme
const rec = { recorder: null, streams: [], chunks: [], start: 0, timer: null, ctx: null, raf: null };

async function startRecording() {
  const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  rec.streams = [mic];
  rec.ctx = new AudioContext();
  const dest = rec.ctx.createMediaStreamDestination();
  const analyser = rec.ctx.createAnalyser();
  rec.ctx.createMediaStreamSource(mic).connect(dest);
  if ($("#rec-source").value === "mix") {
    // Online-Meeting: Ton des geteilten Tabs/Bildschirms mit aufnehmen.
    const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    rec.streams.push(display);
    if (display.getAudioTracks().length) rec.ctx.createMediaStreamSource(display).connect(dest);
    else alert("Der geteilte Inhalt liefert keinen Ton – bitte beim Teilen „Tab-Audio teilen“ aktivieren.");
  }
  rec.ctx.createMediaStreamSource(dest.stream).connect(analyser);
  rec.chunks = [];
  rec.recorder = new MediaRecorder(dest.stream, { mimeType: MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "" });
  rec.recorder.ondataavailable = (ev) => ev.data.size && rec.chunks.push(ev.data);
  rec.recorder.start(1000);
  rec.start = Date.now();
  rec.timer = setInterval(() => { $("#rec-time").textContent = fmt((Date.now() - rec.start) / 1000); }, 500);
  $("#rec-dot").classList.add("on");
  $("#rec-start").hidden = true;
  $("#rec-stop").hidden = false;
  $("#rec-source").disabled = true;
  drawLevel(analyser);
}

function drawLevel(analyser) {
  const c = $("#rec-level"), g = c.getContext("2d"), buf = new Uint8Array(analyser.fftSize);
  const loop = () => {
    analyser.getByteTimeDomainData(buf);
    const peak = buf.reduce((m, v) => Math.max(m, Math.abs(v - 128)), 0) / 128;
    g.clearRect(0, 0, c.width, c.height);
    g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent");
    g.fillRect(0, 6, c.width * Math.min(1, peak * 1.5), 12);
    rec.raf = requestAnimationFrame(loop);
  };
  loop();
}

function cleanupRecording() {
  clearInterval(rec.timer);
  cancelAnimationFrame(rec.raf);
  rec.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  rec.ctx?.close();
  rec.recorder = null;
  $("#rec-dot").classList.remove("on");
  $("#rec-start").hidden = false;
  $("#rec-stop").hidden = true;
  $("#rec-source").disabled = false;
  $("#rec-time").textContent = "00:00";
}

$("#btn-record").addEventListener("click", () => $("#dlg-record").showModal());
$("#rec-start").addEventListener("click", () => startRecording().catch((err) => {
  cleanupRecording();
  alert(`Aufnahme nicht möglich: ${err.message}`);
}));
$("#rec-stop").addEventListener("click", () => {
  const r = rec.recorder;
  if (!r) return;
  r.onstop = async () => {
    const blob = new Blob(rec.chunks, { type: "audio/webm" });
    const title = $("#rec-title").value || `Aufnahme ${new Date().toLocaleString("de-DE")}`;
    cleanupRecording();
    $("#dlg-record").close();
    try { await uploadBlob(blob, "aufnahme.webm", title); } catch (err) { alert(`Upload fehlgeschlagen: ${err.message}`); }
  };
  r.stop();
});
$("#dlg-record").addEventListener("close", () => { if (rec.recorder) { rec.recorder.onstop = null; rec.recorder.stop(); cleanupRecording(); } });

// ---------------------------------------------------------- Start
(async () => {
  try {
    const st = await api("/api/status");
    $("#engine").textContent = `Parakeet · ${st.llm.available ? "LLM" : "ohne LLM"}`;
    $("#engine").title = `ASR: ${st.asr_model}\nSprechererkennung: ${st.diarization ? "an" : "aus"}\nLLM: ${st.llm.available ? st.llm.model : "nicht erreichbar (" + st.llm.url + ")"}`;
  } catch { /* Server-Status optional */ }
  await loadMeetings();
})();
