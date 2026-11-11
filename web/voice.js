/* WhatsApp-style voice input. Hold the mic to speak, release to send; slide left to cancel.
   A quick tap starts hands-free mode: tap again to send. Uses the browser's speech recognition
   (Chrome / Edge, needs https or localhost). The mic shows only while the box is empty, like WhatsApp. */
(function () {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const form = document.getElementById("f");
  const q = document.getElementById("q");
  const send = document.getElementById("send");
  if (!SR || !form || !q || !send) return;

  const LANGS = [["en-IN", "EN"], ["hi-IN", "हि"]];
  let langIdx = 0;
  try { langIdx = Number(localStorage.getItem("voiceLang")) === 1 ? 1 : 0; } catch (e) {}

  const css = document.createElement("style");
  css.textContent = `
  button.mic { flex:none; width:44px; height:44px; border:0; border-radius:50%; background:var(--slate,#1f2c34);
    color:var(--green,#25d366); display:none; align-items:center; justify-content:center; cursor:pointer;
    touch-action:none; user-select:none; -webkit-user-select:none; transition:transform .15s; }
  form.voice-idle button.mic { display:flex; } form.voice-idle button.send { display:none; }
  button.mic.rec { background:#e53935; color:#fff; transform:scale(1.25); }
  .vbar { position:absolute; left:8px; right:60px; bottom:8px; min-height:44px; display:none; align-items:center; gap:10px;
    padding:0 14px; border-radius:22px; background:var(--panel,#fff); color:var(--ink,#111); font-size:14px;
    box-shadow:0 1px 3px rgba(0,0,0,.15); z-index:5; }
  .vbar.on { display:flex; }
  .vbar .dot { width:10px; height:10px; border-radius:50%; background:#e53935; animation:vblink 1s infinite; flex:none; }
  .vbar .t { font-variant-numeric:tabular-nums; flex:none; }
  .vbar .txt { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; opacity:.8; }
  .vbar .hint { flex:none; opacity:.55; font-size:12px; }
  .vbar .lang { flex:none; border:1px solid currentColor; border-radius:10px; padding:1px 7px; font-size:12px; background:none; color:inherit; cursor:pointer; }
  .vbar.cancel .hint { color:#e53935; opacity:1; }
  @keyframes vblink { 50% { opacity:.2; } }`;
  document.head.appendChild(css);

  form.style.position = form.style.position || "relative";
  const mic = document.createElement("button");
  mic.type = "button"; mic.className = "mic"; mic.id = "mic";
  mic.setAttribute("aria-label", "Hold to speak");
  mic.title = "Hold to speak, release to send. Tap for hands-free.";
  mic.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>';
  send.after(mic);

  const bar = document.createElement("div");
  bar.className = "vbar"; bar.setAttribute("aria-live", "polite");
  bar.innerHTML = '<span class="dot"></span><span class="t">0:00</span><span class="txt"></span><button type="button" class="lang"></button><span class="hint"></span>';
  form.appendChild(bar);
  const tEl = bar.querySelector(".t"), txtEl = bar.querySelector(".txt"), hintEl = bar.querySelector(".hint"), langBtn = bar.querySelector(".lang");

  function syncIdle() { form.classList.toggle("voice-idle", !q.value.trim() && !rec); }
  q.addEventListener("input", syncIdle);
  new MutationObserver(syncIdle).observe(q, { attributes: true });
  setInterval(syncIdle, 400);   /* the chat clears the box by script after sending */

  let rec = null, finalText = "", interim = "", startAt = 0, timer = 0, mode = "", cancelled = false, startX = 0, pressT = 0;

  function setLangLabel() { langBtn.textContent = LANGS[langIdx][1]; langBtn.title = "Speech language: " + LANGS[langIdx][0]; }
  setLangLabel();
  langBtn.addEventListener("pointerdown", (e) => e.stopPropagation());
  langBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    langIdx = 1 - langIdx; setLangLabel();
    try { localStorage.setItem("voiceLang", String(langIdx)); } catch (err) {}
    if (rec) { const keep = mode; stop(false); setTimeout(() => start(keep), 250); }
  });

  function start(m) {
    if (rec) return;
    mode = m; cancelled = false; finalText = ""; interim = "";
    const r = new SR();
    r.lang = LANGS[langIdx][0]; r.interimResults = true; r.continuous = true; r.maxAlternatives = 1;
    r.onresult = (ev) => {
      interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const s = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += (finalText ? " " : "") + s.trim(); else interim += s;
      }
      txtEl.textContent = (finalText + " " + interim).trim() || "Listening…";
    };
    r.onerror = (ev) => {
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed") hintEl.textContent = "Mic blocked: allow it in the browser";
      else if (ev.error === "network") hintEl.textContent = "Voice needs internet";
    };
    r.onend = () => { if (rec === r) finish(); };
    rec = r;
    try { r.start(); } catch (e) { rec = null; return; }
    startAt = Date.now();
    mic.classList.add("rec"); bar.classList.add("on"); bar.classList.remove("cancel");
    txtEl.textContent = "Listening…";
    hintEl.textContent = m === "hold" ? "◀ slide to cancel" : "tap mic to send";
    clearInterval(timer);
    timer = setInterval(() => { const s = Math.floor((Date.now() - startAt) / 1000); tEl.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }, 250);
    syncIdle();
  }

  function stop(submit) {
    if (!rec) return;
    cancelled = !submit;
    try { rec.stop(); } catch (e) { finish(); }
  }

  function finish() {
    const text = (finalText + " " + interim).trim();
    rec = null; clearInterval(timer); tEl.textContent = "0:00";
    mic.classList.remove("rec"); bar.classList.remove("on", "cancel");
    if (!cancelled && text) {
      q.value = text;
      q.dispatchEvent(new Event("input", { bubbles: true }));
      if (form.requestSubmit) form.requestSubmit(send); else send.click();
    }
    syncIdle();
  }

  mic.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    if (rec && mode === "tap") { stop(true); return; }
    startX = e.clientX; pressT = Date.now();
    try { mic.setPointerCapture(e.pointerId); } catch (err) {}
    start("hold");
  });
  mic.addEventListener("pointermove", (e) => {
    if (!rec || mode !== "hold") return;
    const away = startX - e.clientX > 80;
    bar.classList.toggle("cancel", away);
    hintEl.textContent = away ? "release to cancel" : "◀ slide to cancel";
  });
  function release(e) {
    if (!rec || mode !== "hold") return;
    if (Date.now() - pressT < 350) { mode = "tap"; hintEl.textContent = "tap mic to send"; return; }  /* quick tap: hands-free */
    stop(!bar.classList.contains("cancel"));
  }
  mic.addEventListener("pointerup", release);
  mic.addEventListener("pointercancel", () => stop(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && rec) stop(false); });

  syncIdle();
})();
