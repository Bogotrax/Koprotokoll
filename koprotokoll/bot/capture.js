// Wird vor jedem Seitenskript geladen: greift die Audiospuren aller Teilnehmer an
// den WebRTC-Verbindungen ab, mischt sie und nimmt sie mit MediaRecorder auf.
// Die Daten gehen in Stücken über window.__ntChunk an Python.
(() => {
  if (window.__ntInstalled) return;
  window.__ntInstalled = true;

  const st = { ctx: null, dest: null, analyser: null, rec: null, tracks: new Set(), chain: Promise.resolve() };
  const notify = (obj) => { try { window.__ntEvent && window.__ntEvent(JSON.stringify(obj)); } catch (e) { /* ignore */ } };

  function ensureGraph() {
    if (st.ctx) return;
    st.ctx = new AudioContext({ sampleRate: 48000 });
    st.dest = st.ctx.createMediaStreamDestination();
    st.analyser = st.ctx.createAnalyser();
    st.analyser.fftSize = 2048;
  }

  function addTrack(track) {
    if (!track || track.kind !== "audio" || st.tracks.has(track.id)) return;
    ensureGraph();
    st.tracks.add(track.id);
    const src = st.ctx.createMediaStreamSource(new MediaStream([track]));
    src.connect(st.dest);
    src.connect(st.analyser);
    track.addEventListener("ended", () => { st.tracks.delete(track.id); src.disconnect(); });
    if (st.ctx.state === "suspended") st.ctx.resume();
    notify({ type: "track", count: st.tracks.size });
  }

  const Native = window.RTCPeerConnection;
  if (Native) {
    function Wrapped(...args) {
      const pc = new Native(...args);
      pc.addEventListener("track", (e) => addTrack(e.track));
      return pc;
    }
    Wrapped.prototype = Native.prototype;
    Object.setPrototypeOf(Wrapped, Native);
    window.RTCPeerConnection = Wrapped;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = Wrapped;
  }

  // Fallback: Audio direkt aus HTMLMediaElement-Elementen abgreifen.
  // Jitsi (und manche andere Plattformen) spielen Remote-Audio über <audio>-Elemente
  // mit srcObject oder src statt über AudioContext.
  const srcObjectDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'srcObject');
  if (srcObjectDesc && srcObjectDesc.set) {
    Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', {
      configurable: true,
      get: srcObjectDesc.get ? function() { return srcObjectDesc.get.call(this); } : undefined,
      set: function(stream) {
        try {
          if (stream && stream.getAudioTracks) {
            stream.getAudioTracks().forEach((t) => addTrack(t));
          }
        } catch (e) { /* ignore */ }
        return srcObjectDesc.set.call(this, stream);
      }
    });
  }

  // Zusätzlich: src-Setter abgreifen (für URL.createObjectURL(MediaStream)-Ansätze).
  const srcDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
  if (srcDesc && srcDesc.set) {
    Object.defineProperty(HTMLMediaElement.prototype, 'src', {
      configurable: true,
      get: srcDesc.get ? function() { return srcDesc.get.call(this); } : undefined,
      set: function(val) {
        // blob:-URLs können MediaStreams sein – AudioTracks aus dem Element abgreifen
        try {
          if (this.srcObject && this.srcObject.getAudioTracks) {
            this.srcObject.getAudioTracks().forEach((t) => addTrack(t));
          }
        } catch (e) { /* ignore */ }
        return srcDesc.set.call(this, val);
      }
    });
  }

  // Zusätzlich: play() abgreifen – falls srcObject nach play() gesetzt wird.
  const origPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function(...args) {
    try {
      if (this.srcObject && this.srcObject.getAudioTracks) {
        this.srcObject.getAudioTracks().forEach((t) => addTrack(t));
      }
    } catch (e) { /* ignore */ }
    return origPlay.apply(this, args);
  };

  // Zusätzlich: AudioContext.createMediaStreamSource abgreifen (für Plattformen, die
  // den Web-Audio-Kontext nutzen).
  const origCreateMediaStreamSource = AudioContext.prototype.createMediaStreamSource;
  AudioContext.prototype.createMediaStreamSource = function(stream) {
    try {
      if (stream && stream.getAudioTracks) {
        stream.getAudioTracks().forEach((t) => addTrack(t));
      }
    } catch (e) { /* ignore */ }
    return origCreateMediaStreamSource.call(this, stream);
  };

  function toBase64(bytes) {
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }

  window.__ntStart = (timesliceMs) => {
    ensureGraph();
    if (st.rec) return true;
    if (st.ctx.state === "suspended") st.ctx.resume();
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    st.rec = new MediaRecorder(st.dest.stream, { mimeType: mime, audioBitsPerSecond: 64000 });
    // Reihenfolge der Stücke über eine Promise-Kette garantieren.
    st.rec.ondataavailable = (e) => {
      if (!e.data.size) return;
      st.chain = st.chain.then(async () => window.__ntChunk(toBase64(new Uint8Array(await e.data.arrayBuffer()))));
    };
    st.rec.onstop = () => { st.chain = st.chain.then(() => notify({ type: "stopped" })); };
    st.rec.start(timesliceMs || 5000);
    return true;
  };

  window.__ntStop = () => {
    if (st.rec && st.rec.state !== "inactive") st.rec.stop();
    else notify({ type: "stopped" });
  };

  window.__ntLevel = () => {
    if (!st.analyser) return 0;
    const buf = new Float32Array(st.analyser.fftSize);
    st.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    return Math.sqrt(sum / buf.length);
  };

  window.__ntTrackCount = () => st.tracks.size;
})();
