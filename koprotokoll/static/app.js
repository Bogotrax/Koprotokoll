"use strict";

const $ = (sel) => document.querySelector(sel);
const STATUS = {
  queued: "In Warteschlange", converting: "Konvertiere Audio", transcribing: "Transkribiere (Parakeet)",
  diarizing: "Erkenne Sprecher", summarizing: "Erstelle Zusammenfassung", done: "Fertig", error: "Fehler",
};
const COLORS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];

const state = { meetings: [], bots: [], current: null, segEls: [], pollTimer: null };
const ACTIVE_BOT = ["scheduled", "joining", "waiting_admit", "recording", "stopping"];
const BOT_STATUS = {
  scheduled: "geplant", joining: "tritt bei", waiting_admit: "wartet auf Einlass", recording: "nimmt auf",
  stopping: "beendet", completed: "fertig", stopped: "gestoppt", failed: "fehlgeschlagen", cancelled: "abgesagt",
};

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

// API-Schlüssel nur nötig, wenn die Oberfläche von einem anderen Rechner aus genutzt wird.
const KEY_STORE = "koprotokoll_api_key";
function apiKey() { try { return localStorage.getItem(KEY_STORE) || ""; } catch { return ""; } }
function withKey(url) { const k = apiKey(); return k ? `${url}${url.includes("?") ? "&" : "?"}key=${encodeURIComponent(k)}` : url; }

async function api(path, opts = {}, retry = true) {
  const headers = { ...(opts.headers || {}) };
  if (apiKey()) headers.Authorization = `Bearer ${apiKey()}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401 && retry) {
    const k = prompt("Dieser Server verlangt einen API-Schlüssel (kp_…):");
    if (k) { try { localStorage.setItem(KEY_STORE, k.trim()); } catch { /* ignore */ } return api(path, opts, false); }
  }
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
  await loadBots();
  const botsBusy = state.bots.some((b) => ACTIVE_BOT.includes(b.status));
  clearTimeout(state.pollTimer);
  if (busy || botsBusy) state.pollTimer = setTimeout(refresh, botsBusy && !busy ? 3000 : 1500);
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
  showView("meeting");
  if (location.hash !== `#meeting=${id}`) history.replaceState(null, "", `#meeting=${id}`);
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
  if (changedMeeting) player.src = withKey(`/api/meetings/${id}/audio`);

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
  if (e.target.value && state.current) window.location = withKey(`/api/meetings/${state.current.id}/export?format=${e.target.value}`);
  e.target.value = "";
});

$("#m-delete").addEventListener("click", async () => {
  if (!state.current || !confirm(`„${state.current.title}“ wirklich löschen?`)) return;
  await api(`/api/meetings/${state.current.id}`, { method: "DELETE" });
  state.current = null;
  $("#player").removeAttribute("src");
  showView("empty");
  history.replaceState(null, "", "#");
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

// ---------------------------------------------------------- Ansichten
function showView(name) {
  for (const v of ["empty", "meeting", "integrations"]) $(`#${v}`).hidden = v !== name;
  $("#btn-integrations").classList.toggle("active", name === "integrations");
  if (name !== "meeting") document.querySelectorAll(".m-item").forEach((el) => el.classList.remove("active"));
}

// ---------------------------------------------------------- Bots
async function loadBots() {
  try { state.bots = await api("/api/bots"); } catch { state.bots = []; }
  const live = state.bots.filter((b) => ACTIVE_BOT.includes(b.status));
  $("#bot-live").replaceChildren(...live.map((b) => h("div", { class: "live" },
    h("span", { class: `badge ${b.status}` }, BOT_STATUS[b.status] || b.status),
    h("span", { class: "t", title: b.meeting_url }, b.title),
    h("button", { title: "Bot stoppen", onclick: () => stopBot(b.id) }, "■"))));
  if (!$("#integrations").hidden) renderBots();
}

function renderBots() {
  $("#bot-list").replaceChildren(...state.bots.slice(0, 15).map((b) => h("li", {},
    h("span", { class: `badge ${b.status}` }, BOT_STATUS[b.status] || b.status),
    h("span", { class: "grow" }, h("b", {}, b.title), " ",
      h("span", { class: "muted small" }, new Date(b.join_at * 1000).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" })),
      b.error ? h("div", { class: "muted small" }, b.error) : null),
    b.meeting_id ? h("a", { class: "ts", href: `#meeting=${b.meeting_id}` }, "Öffnen") : null,
    ACTIVE_BOT.includes(b.status) ? h("button", { onclick: () => stopBot(b.id) }, b.status === "scheduled" ? "Absagen" : "Stoppen") : null)));
}

async function sendBot(url, title, joinAt) {
  const body = { meeting_url: url.trim(), title: title || null };
  if (joinAt) body.join_at = joinAt;
  await api("/api/bots", jsonOpts("POST", body));
  await loadMeetings();
}

async function stopBot(id) {
  await api(`/api/bots/${id}`, { method: "DELETE" });
  await loadMeetings();
}

$("#bot-quick").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("#bot-quick-url").value.trim();
  if (!url) return;
  try { await sendBot(url); $("#bot-quick-url").value = ""; } catch (err) { alert(err.message); }
});

$("#bot-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const at = $("#bot-at").value ? new Date($("#bot-at").value).getTime() / 1000 : null;
  try { await sendBot($("#bot-url").value, $("#bot-title").value, at); e.target.reset(); } catch (err) { alert(err.message); }
});

// ---------------------------------------------------------- Integrationen
async function openIntegrations() {
  showView("integrations");
  if (location.hash.split("?")[0] !== "#integrations") history.replaceState(null, "", "#integrations");
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const err = params.get("google_error") || params.get("microsoft_error");
  if (err) alert(`Anmeldung abgebrochen: ${err}`);
  renderBots();
  await Promise.all([renderGoogle(), renderMicrosoft(), renderSignal(), renderKeys(), renderHooks()]);
}

async function renderGoogle() {
  const g = await api("/api/integrations/google");
  const box = $("#google-box");
  if (!g.configured) {
    const id = h("input", { placeholder: "Client-ID" }), secret = h("input", { placeholder: "Client-Secret", type: "password" });
    box.replaceChildren(
      h("p", { class: "muted small" }, "1. In der Google Cloud Console Calendar- und Gmail-API aktivieren. 2. OAuth-Client vom Typ „Webanwendung“ anlegen mit Weiterleitungs-URI:"),
      h("pre", {}, g.redirect_uri),
      h("div", { class: "stack" }, id, secret, h("button", { class: "primary", onclick: async () => {
        await api("/api/integrations/google/client", jsonOpts("PUT", { client_id: id.value, client_secret: secret.value }));
        renderGoogle();
      } }, "Speichern")));
    return;
  }
  if (!g.connected) {
    box.replaceChildren(
      h("p", { class: "muted small" }, "Verbinde dein Google-Konto: Der Bot tritt dann automatisch deinen Kalender-Meetings bei, und nach jedem Meeting kommt die Zusammenfassung per Gmail."),
      h("button", { class: "primary", onclick: () => { window.location = withKey("/api/integrations/google/connect"); } }, "Mit Google verbinden"));
    return;
  }
  const toggle = (key, label) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = !!g.settings[key];
    cb.addEventListener("change", () => api("/api/integrations/google/settings", jsonOpts("PATCH", { [key]: cb.checked })));
    return h("label", { class: "switch" }, cb, label);
  };
  const sync = g.last_sync?.at ? `Letzter Abgleich: ${new Date(g.last_sync.at * 1000).toLocaleTimeString("de-DE")}${g.last_sync.error ? ` – Fehler: ${g.last_sync.error}` : ""}` : "";
  box.replaceChildren(
    h("p", {}, "Verbunden als ", h("b", {}, g.email || "?")),
    toggle("auto_join", "Bot tritt Kalender-Meetings mit Meet-Link automatisch bei"),
    toggle("send_recap", "Zusammenfassung nach dem Meeting per Gmail an mich"),
    toggle("recap_to_attendees", "… auch an alle Teilnehmer des Termins"),
    sync ? h("p", { class: "muted small" }, sync) : null,
    h("div", { class: "row" },
      h("button", { onclick: async (e) => {
        e.target.disabled = true;
        try { const r = await api("/api/integrations/google/sync", { method: "POST" }); alert(`${r.created.length} neue(r) Bot(s) eingeplant.`); await loadMeetings(); }
        catch (err) { alert(err.message); }
        e.target.disabled = false; renderGoogle();
      } }, "Kalender jetzt prüfen"),
      h("button", { class: "danger", onclick: async () => {
        if (!confirm("Google-Verbindung trennen?")) return;
        await api("/api/integrations/google", { method: "DELETE" }); renderGoogle();
      } }, "Trennen")));
}

function msAdvanced(g, open) {
  const id = h("input", { placeholder: "Client-ID" }),
    secret = h("input", { placeholder: "Client-Secret", type: "password" });
  return h("details", open ? { open: true } : {},
    h("summary", { class: "muted small" }, "Erweiterte Einstellungen (Client-ID / Secret)"),
    h("p", { class: "muted small" }, "Einmalige Einrichtung im Azure-Portal (portal.azure.com) – die Anmeldung dort geht auch mit einem privaten Microsoft- oder GitHub-Konto, ein Abonnement ist nicht nötig:"),
    h("ol", { class: "muted small" },
      h("li", {}, "In der Suche nach „App-Registrierungen“ suchen → „Neue Registrierung“, Name z. B. „Koprotokoll“."),
      h("li", {}, h("b", {}, "Unterstützte Kontotypen: „Any Entra ID Tenant + Personal Microsoft accounts“ (der unterste Eintrag)"),
        " – nur damit sind auch Privatkonten (outlook.com, live.com) erlaubt. Falsch gewählt erscheint später der Fehler „Mandant … nicht vorhanden“."),
      h("li", {}, "Umleitungs-URI: Plattform „Web“ auswählen und diese Adresse eintragen:"),
      h("pre", {}, g.redirect_uri),
      h("li", {}, "„Registrieren“ → danach die ", h("b", {}, "Client-ID („Application (client) ID“)"),
        " aus der Übersicht kopieren und unten eintragen."),
      h("li", {}, "Client-Secret anlegen: „Certificates & secrets“ → „New client secret“ → nach dem Anlegen den ",
        h("b", {}, "Wert aus der Spalte „Value“ sofort kopieren"),
        " – in der Tabelle stehen „Secret ID“ und „Value“ nebeneinander, gebraucht wird der Wert. Er wird nur einmal angezeigt; bei Verlust hilft nur ein neues Secret."),
      h("li", {}, "„API permissions“ → „Add a permission“ → „Microsoft Graph“ → „Delegated permissions“ → ",
        h("code", {}, "Calendars.Read"), " und ", h("code", {}, "Mail.Send"), " hinzufügen."),
      h("li", {}, "Hier unten Client-ID und Secret eintragen und speichern, dann „Mit Microsoft verbinden“.")),
    h("div", { class: "stack" }, id, secret, h("button", { onclick: async () => {
      await api("/api/integrations/microsoft/client", jsonOpts("PUT", { client_id: id.value, client_secret: secret.value }));
      renderMicrosoft();
    } }, "Speichern")));
}

async function renderMicrosoft() {
  const g = await api("/api/integrations/microsoft");
  const box = $("#microsoft-box");
  if (!g.configured) {
    box.replaceChildren(
      h("p", { class: "muted small" }, "Für die Anbindung wird eine einmalige Azure-App-Registrierung benötigt – die Zugangsdaten kommen unten unter „Erweiterte Einstellungen“."),
      msAdvanced(g, true));
    return;
  }
  if (!g.connected) {
    box.replaceChildren(
      h("p", { class: "muted small" }, "Verbinde dein Microsoft-/Outlook-Konto: Der Bot tritt dann automatisch deinen Kalender-Meetings bei, und nach jedem Meeting kommt die Zusammenfassung per Mail."),
      h("button", { class: "primary", onclick: () => { window.location = withKey("/api/integrations/microsoft/connect"); } }, "Mit Microsoft verbinden"),
      msAdvanced(g, false));
    return;
  }
  const toggle = (key, label) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = !!g.settings[key];
    cb.addEventListener("change", () => api("/api/integrations/microsoft/settings", jsonOpts("PATCH", { [key]: cb.checked })));
    return h("label", { class: "switch" }, cb, label);
  };
  const sync = g.last_sync?.at ? `Letzter Abgleich: ${new Date(g.last_sync.at * 1000).toLocaleTimeString("de-DE")}${g.last_sync.error ? ` – Fehler: ${g.last_sync.error}` : ""}` : "";
  box.replaceChildren(...[
    h("p", {}, "Verbunden als ", h("b", {}, g.email || "?")),
    toggle("auto_join", "Bot tritt Kalender-Meetings mit Meet-/Jitsi-Link automatisch bei"),
    sync ? h("p", { class: "muted small" }, sync) : null,
    h("div", { class: "row" },
      h("button", { class: "danger", onclick: async () => {
        if (!confirm("Microsoft-Verbindung trennen?")) return;
        await api("/api/integrations/microsoft", { method: "DELETE" }); renderMicrosoft();
      } }, "Trennen")),
    msAdvanced(g, false),
  ].filter(Boolean));
}

async function renderSignal() {
  const g = await api("/api/integrations/signal");
  const box = $("#signal-box");
  const num = h("input", { placeholder: "+491701234567", value: g.settings.recipient || "" });
  const toggle = (key, label) => {
    const cb = h("input", { type: "checkbox" });
    cb.checked = !!g.settings[key];
    cb.addEventListener("change", () => api("/api/integrations/signal/settings", jsonOpts("PATCH", { [key]: cb.checked })));
    return h("label", { class: "switch" }, cb, label);
  };
  const parts = [
    h("p", { class: "muted small" },
      "Zusammenfassungen nach dem Meeting kommen per Signal auf dein Handy – über einen lokalen signal-cli-Dienst, alles bleibt auf diesem Rechner."),
  ];
  if (g.connected) {
    parts.push(h("p", {}, "Signal verbunden als ", h("b", {}, g.number || "?")));
  } else {
    parts.push(h("p", { class: "muted small" },
      "Noch nicht verbunden. Scanne den QR-Code mit deinem Signal-Handy ",
      "(Einstellungen → Verbundene Geräte → +) – der Dienst koppelt sich als Sekundärgerät."));
    const qr = h("img", { class: "signal-qr", alt: "QR-Code zum Verknüpfen von Signal",
                          src: withKey("/api/integrations/signal/qr") });
    qr.addEventListener("error", () => {
      qr.replaceWith(h("p", { class: "muted small" },
        "QR-Code nicht verfügbar – läuft der Signal-Dienst? (docker compose -f docker-compose.signal.yml up -d)"));
    });
    parts.push(qr);
  }
  parts.push(
    h("div", { class: "row" }, num, h("button", { onclick: async () => {
      try {
        await api("/api/integrations/signal/settings", jsonOpts("PATCH", { recipient: num.value }));
        renderSignal();
      } catch (err) { alert(err.message); }
    } }, "Nummer speichern")),
    toggle("send_recap", "Zusammenfassung nach dem Meeting per Signal an mich"),
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: async (e) => {
        e.target.disabled = true;
        try {
          await api("/api/integrations/signal/test", { method: "POST" });
          alert("Testnachricht verschickt – sie sollte jetzt auf deinem Handy sein.");
        } catch (err) { alert(err.message); }
        e.target.disabled = false;
      } }, "Testnachricht schicken")),
  );
  box.replaceChildren(...parts.filter(Boolean));
}

async function renderUpcoming() {
  const events = await api("/api/upcoming");
  const list = $("#upcoming-list");
  if (!events.length) {
    list.replaceChildren(h("li", { class: "muted small" },
      "Keine Termine in den nächsten 7 Tagen. Verbundene Kalender werden automatisch angezeigt."));
    return;
  }
  list.replaceChildren(...events.map((e) => {
    const when = new Date(e.start * 1000).toLocaleString("de-DE",
      { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    return h("li", {},
      h("span", { class: "grow" },
        h("b", {}, e.title),
        h("div", { class: "muted small" }, `${when} · ${e.label}`),
        h("div", { class: "muted small" },
          e.will_join ? `Bot wird eingeplant (${e.platform})` : e.reason,
          e.meeting_url ? " · " : "",
          e.meeting_url ? h("a", { href: e.meeting_url, target: "_blank", rel: "noreferrer" }, "Link") : null)));
  }));
}

async function renderKeys() {
  const keys = await api("/api/keys");
  $("#key-list").replaceChildren(...keys.map((k) => h("li", {},
    h("span", { class: "grow" }, h("b", {}, k.name), " ", h("code", {}, `${k.prefix}…`),
      h("div", { class: "muted small" }, k.last_used ? `zuletzt genutzt ${new Date(k.last_used * 1000).toLocaleString("de-DE")}` : "noch nie genutzt")),
    h("button", { onclick: async () => { if (confirm(`Schlüssel „${k.name}“ widerrufen?`)) { await api(`/api/keys/${k.id}`, { method: "DELETE" }); renderKeys(); } } }, "Widerrufen"))));
}

$("#key-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const k = await api("/api/keys", jsonOpts("POST", { name: $("#key-name").value }));
  $("#key-new").replaceChildren(h("div", { class: "secret" }, "Neuer Schlüssel (wird nur jetzt angezeigt): ", h("code", {}, k.key)));
  e.target.reset();
  renderKeys();
});

async function renderHooks() {
  const hooks = await api("/api/webhooks");
  $("#hook-list").replaceChildren(...hooks.map((w) => h("li", {},
    h("span", { class: "grow" }, h("code", {}, w.url), h("div", { class: "muted small" }, w.events.join(", "))),
    h("button", { onclick: async () => { const r = await api(`/api/webhooks/${w.id}/test`, { method: "POST" }); alert(r.delivered ? "Test zugestellt." : "Zustellung fehlgeschlagen."); } }, "Testen"),
    h("button", { onclick: async () => { await api(`/api/webhooks/${w.id}`, { method: "DELETE" }); renderHooks(); } }, "✕"))));
}

$("#hook-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const w = await api("/api/webhooks", jsonOpts("POST", { url: $("#hook-url").value, events: [$("#hook-events").value] }));
    $("#hook-new").replaceChildren(h("div", { class: "secret" }, "Signatur-Secret (wird nur jetzt angezeigt): ", h("code", {}, w.secret)));
    e.target.reset();
    renderHooks();
  } catch (err) { alert(err.message); }
});

$("#btn-integrations").addEventListener("click", openIntegrations);

async function route() {
  const hash = location.hash;
  const m = hash.match(/^#meeting=(\d+)/);
  if (m) await openMeeting(+m[1]).catch(() => showView("empty"));
  else if (hash.startsWith("#integrations")) await openIntegrations();
  else { showView("empty"); await renderUpcoming(); }
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------- Start
(async () => {
  try {
    const st = await api("/api/status");
    $("#engine").textContent = `Parakeet · ${st.llm.available ? "LLM" : "ohne LLM"}`;
    $("#engine").title = `ASR: ${st.asr_model}\nSprechererkennung: ${st.diarization ? "an" : "aus"}\nLLM: ${st.llm.available ? st.llm.model : "nicht erreichbar (" + st.llm.url + ")"}`;
  } catch { /* Server-Status optional */ }
  await loadMeetings();
  await route();
})();
