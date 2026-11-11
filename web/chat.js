/* Policy chat — the agent's phone view. Every catalogue policy is known up
   front (data/catalogue.json); a PDF upload only adds one more. The five
   agent rules live in agent-core.js; this file is the conversation. */

const C = window.QPCore;
const $ = (s) => document.querySelector(s);
const chat = $("#chat"), qEl = $("#q"), sendBtn = $("#send");
const pdfInput = $("#pdf-file"), attachBtn = $("#attach");
const PROFILE_KEY = "claviq.myInsurers";

const S = {
  products: [], byId: new Map(), kit: null, mine: new Set(),
  selected: null, need: null, lang: "en", pending: null, choices: null, uploading: false,
};

const BADGE = {
  answered: ["b-ok", "Checked against the policy text"],
  compared: ["b-ok", "From the policy fact table"],
  not_stated: ["b-warn", "Not written in this product"],
  refused: ["b-warn", "Outside this wording"],
  blocked: ["b-stop", "Answer held: a figure did not match the policy"],
  error: ["b-stop", "Error"],
};
const CHIPS_START = [
  "Meri maa 55 saal ki hai, diabetes hai, sasta plan chahiye",
  "Mother is 50, has diabetes, wants room rent above 10,000",
  "Optima Secure",
  "Care Supreme",
  "National Mediclaim",
];
const CHIPS_PRODUCT = [
  "Diabetes waiting kitna hai?",
  "Room rent kitna milega?",
  "Entry age limit?",
  "Premium kitna hoga?",
  "Is robotic surgery covered?",
  "Cataract limit?",
  "AYUSH covered?",
  "Close the sale",
];

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const withRefs = (text, id) => esc(text).replace(/\[(\d+)\]/g, (m, n) => `<span class="ref" data-b="${id}" data-n="${n}">${n}</span>`);
const ms = (t0) => Math.max(1, Math.round(performance.now() - t0));
const pdfLink = (p, cite) => (cite?.pdf ? `<a class="open" href="/pdf/${esc(C.policyIdOf(p))}#page=${cite.pdf}" target="_blank" rel="noopener">${esc(C.pageLabel(cite))} &#8599;</a>` : "");
let BID = 0;

/* long answers: put the start of the bot bubble at the top of the pane,
   so the agent reads from the first line — never jump straight to the end */
function reveal(el) {
  if (!el) { chat.scrollTop = chat.scrollHeight; return; }
  const go = () => { chat.scrollTop = Math.max(0, el.offsetTop - 4); };
  requestAnimationFrame(() => { go(); setTimeout(go, 40); });
}
function add(cls, html) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.innerHTML = html;
  chat.appendChild(d);
  /* only pin bot answers; scrolling the user bubble first then the answer
     mid-animation used to land past the first line of the result */
  if (/\bbot\b/.test(cls)) reveal(d);
  return d;
}
function addSys(html, kind) {
  const d = document.createElement("div");
  d.className = "sys" + (kind ? ` sys--${kind}` : "");
  d.innerHTML = html;
  chat.appendChild(d);
  reveal(d);
  return d;
}
function typing(text) {
  const b = add("bot", `<span class="typing-row"><span class="avatar-mini" data-qpa="mark"></span><span class="typing">${esc(text)}</span></span>`);
  if (window.QPAvatar) {
    QPAvatar.mountAll(b);
    const holder = b.querySelector("[data-qpa]");
    const mini = QPAvatar.get(holder);
    (async () => {
      while (mini && holder.isConnected && (await mini.scan())) await new Promise((r) => setTimeout(r, 250));
    })();
  }
  return b;
}

/* ── data: the built-in policy database ─────────────────── */

function uploadAsProduct(u) {
  return {
    id: u.policy_id, policy_id: u.policy_id, insurer: u.insurer || "Uploaded", name: u.product || u.policy_id,
    variant: null, status: "active", source: "upload", uin: u.uin || "your upload", facts: {},
    differentiator: "Your upload — answers read straight from the PDF",
  };
}

async function loadData() {
  const cat = await fetch("data/catalogue.json", { cache: "no-cache" }).then((r) => r.json());
  S.products = cat.products;
  try {
    const pols = await (await fetch("/api/policies")).json();
    for (const u of pols.filter((p) => p.source === "upload" || String(p.policy_id || "").startsWith("upload_"))) {
      S.products.push(uploadAsProduct(u));
    }
  } catch { /* uploads are optional */ }
  /* policy terms come from the wording (server: retrieve the clause, read it), never from the
     catalogue's fact table; the catalogue only names the products and their sale status */
  for (const p of S.products) if (!C.isUpload(p)) { p.facts = {}; p.close_kit = {}; }
  S.byId = new Map(S.products.map((p) => [p.id, p]));
  await loadWording();
  try {
    const raw = JSON.parse(localStorage.getItem(PROFILE_KEY) || "[]");
    if (Array.isArray(raw)) S.mine = new Set(raw.filter((i) => S.products.some((p) => p.insurer === i)));
  } catch { /* no saved insurers */ }
}
async function loadWording() {
  for (let i = 0; i < 90; i++) {  /* the server reads every wording once at start-up */
    try {
      const r = await fetch("/api/wording-facts");
      if (r.ok) {
        const wf = await r.json();
        for (const [id, w] of Object.entries(wf)) {
          const p = S.byId.get(id);
          if (p) { p.facts = w.facts || {}; p.close_kit = w.close_kit || {}; }
        }
        S.wordingReady = true;
        return;
      }
    } catch { /* server still starting */ }
    await new Promise((res) => setTimeout(res, 2000));
  }
}
const saveMine = () => { try { localStorage.setItem(PROFILE_KEY, JSON.stringify([...S.mine])); } catch { /* session only */ } };
const insurers = () => [...new Set(S.products.filter((p) => !C.isUpload(p)).map((p) => p.insurer))];

/* ── top bar: product pill + my insurers ────────────────── */

function renderBar() {
  const p = S.byId.get(S.selected);
  const pill = $("#ppill");
  pill.classList.toggle("ppill--stop", p?.status === "withdrawn");
  $("#ppill-name").textContent = p ? C.label(p) : "Not picked — I'll ask";
  $("#ppill-k").textContent = p ? (p.status === "withdrawn" ? "Stopped" : "Answering") : "Product";
  $("#ppill-act").textContent = p ? "Change" : "Pick";
  $("#ipill-n").textContent = S.mine.size ? [...S.mine].map((i) => i.replace(/ \(.*\)$/, "")).join(", ") : "All insurers";
  renderChips();
}
function renderChips() {
  const list = S.selected ? CHIPS_PRODUCT : CHIPS_START;
  $("#chips").innerHTML = list.map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join("");
  $("#chips").querySelectorAll(".chip").forEach((c) => (c.onclick = () => send(c.textContent)));
}

/* ── sheets: pick a product · my insurers ───────────────── */

const sheet = $("#sheet");
function openSheet(kind) {
  sheet.hidden = false;
  sheet.dataset.kind = kind;
  $("#sheet-title").textContent = kind === "product" ? "Pick the exact product" : "Insurers you sell";
  $("#sheet-search").hidden = kind !== "product";
  $("#sheet-search").value = "";
  fillSheet();
  requestAnimationFrame(() => sheet.classList.add("sheet--open"));
  if (kind === "product") $("#sheet-search").focus();
}
function closeSheet() {
  sheet.classList.remove("sheet--open");
  setTimeout(() => (sheet.hidden = true), 220);
}
function fillSheet() {
  const body = $("#sheet-list");
  if (sheet.dataset.kind === "insurers") {
    body.innerHTML =
      `<p class="sheet__hint">Your insurers come first in every answer. Saved on this phone.</p><div class="ins">` +
      insurers().map((i) => `<button type="button" class="chip" data-ins="${esc(i)}" aria-pressed="${S.mine.has(i)}">${esc(i)}</button>`).join("") +
      `</div>`;
    body.querySelectorAll("[data-ins]").forEach((b) => (b.onclick = () => {
      const i = b.dataset.ins;
      S.mine.has(i) ? S.mine.delete(i) : S.mine.add(i);
      b.setAttribute("aria-pressed", String(S.mine.has(i)));
      saveMine();
      renderBar();
    }));
    return;
  }
  const q = $("#sheet-search").value.trim();
  let list = q ? C.matchProducts(q, S.products, S.mine) : [...S.products];
  if (!q) list.sort((a, b) => (S.mine.has(b.insurer) ? 1 : 0) - (S.mine.has(a.insurer) ? 1 : 0) ||
    (C.isUpload(a) ? 1 : 0) - (C.isUpload(b) ? 1 : 0) || (a.status === "withdrawn") - (b.status === "withdrawn"));
  body.innerHTML = list.length ? list.map(optionHtml).join("") : `<p class="sheet__hint">No product matches “${esc(q)}”.</p>`;
}
$("#sheet-search").addEventListener("input", fillSheet);
$("#sheet-close").onclick = closeSheet;
sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });
$("#ppill").onclick = () => openSheet("product");
$("#ipill").onclick = () => openSheet("insurers");

/* ── feature 1: exact product, picked before any answer ─── */

function optionHtml(p, diff = C.difference(p)) {
  const stop = p.status === "withdrawn";
  const next = stop ? S.byId.get(p.replaced_by) : null;
  return `<button type="button" class="opt${stop ? " opt--stop" : ""}" data-act="pick" data-id="${esc(p.id)}">
    <span class="opt__ins">${esc(p.insurer)}${S.mine.has(p.insurer) ? ` <i class="tag tag--mine">Yours</i>` : ""}${C.isUpload(p) ? ` <i class="tag">Upload</i>` : ""}${stop ? ` <i class="tag tag--stop">Stopped</i>` : ""}</span>
    <span class="opt__name">${esc(C.fullName(p))}</span>
    <span class="opt__diff">${esc(diff)}${next ? ` · new: ${esc(C.label(next))}` : ""}</span>
  </button>`;
}

function askWhich(list, pendingQ) {
  S.pending = pendingQ;
  S.choices = list;
  const show = list.slice(0, 3);
  const more = list.length - show.length;
  const diffs = C.differences(show);
  add("bot", `<p class="lead">Which exact product? These names look alike — the difference is written under each.</p>
    <div class="opts">${show.map((p) => optionHtml(p, diffs.get(p.id))).join("")}</div>
    ${more > 0 ? `<p class="small">${more} more match. Type the variant name, or tap the product bar to search.</p>` : ""}
    <p class="small">I answer only after you pick one, and never mix two products.</p>`);
}

function pick(id, { pendingQ = null, kit = false, quiet = false } = {}) {
  const p = S.byId.get(id);
  if (!p) return;
  const changed = S.selected !== id;
  S.selected = id;
  closeSheet();
  renderBar();
  if (quiet) {
    S.pending = null;
    S.choices = null;
    return;
  }
  if (changed) addSys(`Now answering only about <b>${esc(C.label(p))}</b>${p.uin ? ` · UIN ${esc(p.uin)}` : ""}. No other product is mixed in.`);
  if (p.status === "withdrawn") stoppedNotice(p);
  const q = pendingQ ?? S.pending;
  S.pending = null;
  S.choices = null;
  if (q) answer(p, q);
  else if (kit) closeKitMsg(p);
  else if (changed && p.status !== "withdrawn") add("bot", `<p class="lead">${esc(C.fullName(p))} is ready.</p><p class="small">Ask about waiting, room rent, age, premium — or something hard like robotic surgery, cataract or AYUSH. Tap “Close the sale” when you're done.</p>`);
}

/* ── feature 5: stopped products point to the new one ───── */

function stoppedNotice(p) {
  const next = S.byId.get(p.replaced_by);
  add("bot stopcard", `<span class="badge b-stop">Stopped product</span>
    <p class="lead">${esc(C.fullName(p))} was stopped for new sales${p.withdrawn_on ? ` on ${esc(C.fmtDate(p.withdrawn_on))}` : ""}. Renewals only — do not sell it to a new customer.</p>
    ${p.stop_reason ? `<p class="small">${esc(p.stop_reason)}</p>` : ""}
    ${next ? `<p>Sell the current product instead:</p><div class="opts">${optionHtml(next)}</div>` : ""}`);
}

/* ── feature 2: need-first ─────────────────────────────── */

function rankLabel(rank, lang) {
  if (lang === "hi") return rank === 1 ? "सबसे अच्छा" : rank === 2 ? "दूसरा" : "तीसरा";
  if (lang === "hinglish") return rank === 1 ? "Best" : rank === 2 ? "Doosra" : "Teesra";
  return rank === 1 ? "Best fit" : rank === 2 ? "Second" : "Third";
}

function pitchHtml(p, lang) {
  const k = C.closeKit(p, S.need, lang);
  const id = ++BID;
  const L = k.labels;
  return `<div class="pitch" hidden>
    <div class="kit"><p class="kit__t">${esc(L.say)}</p>${k.say.map((s) => `<p>${esc(s)}</p>`).join("")}</div>
    <div class="kit"><p class="kit__t">${esc(L.wa)}</p><pre class="wa" id="wa${id}">${esc(k.whatsapp)}</pre>
      <div class="kit__row"><button type="button" class="btn btn--sm" data-act="copy" data-src="wa${id}">Copy</button>
      <a class="btn btn--sm btn--ghost" href="https://wa.me/?text=${encodeURIComponent(k.whatsapp)}" target="_blank" rel="noopener">WhatsApp</a></div></div>
    <div class="kit"><p class="kit__t">${esc(L.wait)}</p>${k.waitEnd
      ? `<p><b>${esc(k.waitEnd.date)}</b> — ${k.waitEnd.months} months. <span class="cite">${esc(k.waitEnd.cite)}</span></p>`
      : `<p>${lang === "en" ? "Not written in this product." : lang === "hi" ? "इस प्रॉडक्ट में नहीं लिखा।" : "Is product mein nahi likha."}</p>`}</div>
    ${kitLists(k, L, lang)}
  </div>`;
}

/* the server's need (rank.py) in the shape the close kit reads */
function clientNeed(n, text) {
  return { text, relation: n.relation, age: n.age, conditions: n.conditions || [], absent: n.absent || [],
    roomRentMin: n.room_rent_min, budgetMax: n.budget_max, lowCost: n.low_cost, pedMaxMonths: null,
    kind: n.kind, maternity: n.maternity, surgery: n.surgery, signals: n.signals || 1 };
}

function coverLabel(need, lang) {
  const k = need.kind;
  const what = k === "maternity" ? { en: "Maternity cover", hinglish: "Maternity cover", hi: "मातृत्व कवर" }
    : k === "surgery" ? { en: `${(need.surgery?.kind || "Surgery").replace(/^./, (c) => c.toUpperCase())} cover`, hinglish: `${need.surgery?.kind || "Surgery"} cover`, hi: `${need.surgery?.kind || "सर्जरी"} कवर` }
      : need.conditions.includes("diabetes") ? { en: "Diabetes cover", hinglish: "Diabetes cover", hi: "डायबिटीज कवर" }
        : { en: "Declared-condition cover", hinglish: "Declared bimari cover", hi: "बताई बीमारी का कवर" };
  return what[lang] || what.en;
}

/* one ranked card from /api/recommend; every figure on it carries its wording page */
function recCard(c, rank, r, need, lang) {
  const p = S.byId.get(c.id);
  if (!p) return "";
  const L = C.closeKit(p, need, lang).labels;
  const opt = r.label_mode === "options";
  const rankText = opt ? (lang === "hi" ? `विकल्प ${rank}` : `Option ${rank}`) : rankLabel(rank, lang);
  const dateLine = c.cover_date && need.kind !== "healthy"
    ? `${esc(coverLabel(need, lang))} ${lang === "en" ? "from" : ":"} <b>${esc(c.cover_date)}</b>`
    : "";
  const cite = c.cite ? `${c.cite.page}${c.cite.section ? ` · ${c.cite.section}` : ""}` : "";
  return `<div class="rec${rank === 1 ? " rec--best" : ""}" data-rec="${esc(p.id)}">
    <span class="rec__rank">${esc(rankText)}</span>
    <span class="opt__ins">${esc(p.insurer)}${S.mine.has(p.insurer) ? ` <i class="tag tag--mine">Yours</i>` : ""}</span>
    <span class="opt__name">${esc(C.fullName(p))}</span>
    <p class="rec__why">${esc(c.why)}</p>
    ${dateLine ? `<p class="rec__line">${dateLine} <span class="cite">${esc(cite)}</span></p>` : ""}
    ${c.cite?.quote ? `<details class="how"><summary>${lang === "en" ? "Exact line" : "Exact line"}</summary><q>${esc(c.cite.quote)}</q></details>` : ""}
    <div class="rec__actions">
      <button type="button" class="btn${rank === 1 ? "" : " btn--ghost"} btn--sm" data-act="pick" data-id="${esc(p.id)}">${esc(L.pick)}</button>
      <button type="button" class="pitch-btn" data-act="pitch" aria-expanded="false">${esc(L.pitch)} <span aria-hidden="true">▾</span></button>
    </div>
    ${pitchHtml(p, lang)}
  </div>`;
}

/* need-first: the server parses the need (negation as a filter) and ranks on it from the wordings */
async function needFirst(localNeed) {
  const t0 = performance.now();
  S.lang = C.detectLang(localNeed.text);
  const lang = S.lang;
  const bubble = typing(lang === "en" ? "Reading the wordings…" : lang === "hi" ? "वर्डिंग पढ़ रहा हूँ…" : "Wordings padh raha hoon…");
  let r;
  try {
    const resp = await fetch("/api/recommend", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: localNeed.text, mine: [...S.mine] }) });
    if (!resp.ok) throw new Error(`/api/recommend returned ${resp.status}`);
    r = await resp.json();
  } catch (e) {
    bubble.innerHTML = `<span class="badge b-stop">Couldn't reach the wording search</span><br>${esc(e.message)}.`;
    return;
  }
  bubble.remove();
  const need = clientNeed(r.need, localNeed.text);
  S.need = need;
  const answer = r.lines[lang] || r.lines.en;
  const tops = [r.best, r.second].filter(Boolean); /* Feature 3: best + second only */
  const cards = tops.length ? tops.map((c, i) => recCard(c, i + 1, r, need, lang)).join("") : "";
  const scope = r.scope === "mine"
    ? `<p class="small">${lang === "hi" ? "पहले आपके इंश्योरर:" : lang === "hinglish" ? "Pehle aapke insurers:" : "Your insurers first:"} ${esc([...S.mine].join(", "))}.</p>`
    : r.scope === "mine-none"
      ? `<p class="small">${lang === "hi" ? "आपके इंश्योरर में फिट नहीं — बाकी दिखा रहा हूँ।" : lang === "hinglish" ? "Aapke insurers mein fit nahi — baaki dikha raha hoon." : "None of your insurers fit — showing every insurer."}</p>`
      : "";
  const w = r.watch;
  const watch = w
    ? `<div class="watch"><p><b>${lang === "hi" ? "यह भी देखें (रैंक नहीं)" : lang === "hinglish" ? "Yeh bhi check karo (not ranked)" : "Also check (not ranked)"}: ${esc(w.label)}</b> — ${esc(w.why)}</p>
        ${w.cover_date && need.kind !== "healthy" ? `<p class="small">${esc(coverLabel(need, lang))}: ${esc(w.cover_date)} · ${esc(w.cite?.page || "")}</p>` : ""}
        <button type="button" class="btn" data-act="pick" data-id="${esc(w.id)}">${esc(lang === "en" ? "Open this product" : "Yeh product kholo")}</button></div>`
    : "";
  const others = r.others.length
    ? `<details class="others"><summary>${lang === "hi" ? "बाकी क्यों नहीं" : lang === "hinglish" ? "Baaki kyun nahi" : "Why not the others"}</summary><ul>${r.others.map((o) =>
        `<li><b>${esc(o.products.join(", "))}</b> — ${esc(o.reason)}</li>`).join("")}</ul></details>`
    : "";
  add("bot rich", `${answer.map((line, i) => `<p class="${i === 0 ? "lead" : ""}">${esc(line)}</p>`).join("")}${scope}
    ${cards ? `<div class="recs">${cards}</div>` : ""}
    ${watch}
    ${others}
    <div class="meta">${lang === "hi" ? `${r.checked} पॉलिसी की वर्डिंग · ${ms(t0)} ms` : lang === "hinglish" ? `${r.checked} policies ki wording check · ${ms(t0)} ms` : `Read ${r.checked} policy wordings in ${ms(t0)} ms`}</div>`);
}

/* ── feature 4: simple vs hard ─────────────────────────── */

const notWritten = (p, note) => add("bot", `<span class="badge b-warn">Not written in this product</span>
  <p class="lead">This is not written in ${esc(C.fullName(p))}.</p>${note ? `<p class="small">${esc(note)}</p>` : ""}`);

function answer(p, text) {
  const t0 = performance.now();
  const intent = C.classify(text);
  const stopNote = p.status === "withdrawn" ? `<p class="stopline">Stopped product — for renewals only.</p>` : "";

  if (intent?.kind === "close") return closeKitMsg(p);
  if (intent?.kind === "premium") {
    const f = p.facts?.premium_inr;
    if (f?.stated) {
      return add("bot", `<span class="badge b-ok">Fast answer</span><p class="lead">Premium: ₹${esc(Number(f.value).toLocaleString("en-IN"))}.</p><div class="meta">${ms(t0)} ms</div>`);
    }
    return add("bot", `${stopNote}<span class="badge b-warn">Not written in this product</span>
      <p class="lead">Premium is not written in this wording. Money is not compared. Premium comes from the insurer rate card after the health check.</p>
      <div class="meta">Answered in ${ms(t0)} ms</div>`);
  }
  if (intent?.kind === "fast" && !C.isUpload(p)) {
    const a = C.simpleAnswer(p, intent.id);
    if (a?.notWritten) return notWritten(p, a.note);
    if (a) {
      return add("bot", `${stopNote}<span class="badge b-ok">Fast answer</span><p class="lead">${esc(a.lead)}</p>
        ${a.note ? `<p class="small">${esc(a.note)}</p>` : ""}
        <p class="cite">${esc(C.pageLabel(a.cite))}${a.section ? ` · ${esc(a.section)}` : ""} ${pdfLink(p, a.cite)}</p>
        ${a.quote ? `<details class="how"><summary>Exact line</summary><q>${esc(a.quote)}</q></details>` : ""}
        <div class="meta">Answered in ${ms(t0)} ms · from the verified fact table</div>`);
    }
  }
  if (intent?.kind === "hard" && !C.isUpload(p)) {
    const h = C.hardFact(p, intent.id);
    /* the fact table only holds the headline clause; read the whole wording before saying "not written" */
    if (h?.notWritten) return askRag(p, text);
    if (h) {
      return add("bot", `${stopNote}<span class="badge b-ok">Exact policy line</span>
        <div class="cardc"><div class="hd"><span><b>${esc(h.section || "Clause")}</b></span>${pdfLink(p, h.cite)}</div>
        ${h.quote ? `<q>“${esc(h.quote)}”</q>` : `<p><b>This sits in a table on that page.</b> Open the page and read it with the customer.</p>`}</div>
        <p class="plain"><b>In simple words:</b> ${esc(h.plain)}</p>
        ${h.note ? `<p class="small">${esc(h.note)}</p>` : ""}
        <div class="meta">${ms(t0)} ms · ${esc(C.pageLabel(h.cite))}${h.section ? ` · ${esc(h.section)}` : ""}</div>`);
    }
  }
  return askRag(p, text);
}

/* everything without a verified fact goes to retrieval over that one wording */
async function askRag(p, question) {
  const policyId = C.policyIdOf(p);
  const bubble = typing("Reading the wording…");
  const t0 = performance.now();
  const id = ++BID;
  let streamed = "";
  try {
    const resp = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, policy_id: policyId }) });
    if (!resp.ok) throw new Error(`/api/ask returned ${resp.status}`);
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ""); buf = buf.slice(i + 2);
        if (!line.trim()) continue;
        let ev; try { ev = JSON.parse(line); } catch { continue; }
        if (ev.type === "token") { streamed += ev.text; bubble.innerHTML = `<div class="ans">${withRefs(streamed, id)}</div>`; }
        if (ev.type === "final") {
          const [cls, lbl] = BADGE[ev.status] || BADGE.error;
          const secs = ((performance.now() - t0) / 1000).toFixed(1);
          const body = ev.status === "not_stated"
            ? `<p class="lead">This is not written in ${esc(C.fullName(p))}.</p>`
            : `<div class="ans">${withRefs(ev.answer, id)}</div>`;
          bubble.innerHTML = `<span class="badge ${cls}">${lbl}</span><br>${body}` +
            (ev.status === "not_stated" ? "" : renderCards(ev.citations, policyId, id)) +
            `<div class="meta">${secs}s · exact lines from ${esc(C.fullName(p))} only</div>`;
          bubble.querySelectorAll(".ref").forEach((r) => (r.onclick = () => {
            const el = document.getElementById(`c${r.dataset.b}-${r.dataset.n}`);
            if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("hl"); setTimeout(() => el.classList.remove("hl"), 1500); }
          }));
          reveal(bubble);
        }
      }
    }
  } catch (e) {
    bubble.innerHTML = `<span class="badge b-stop">Couldn't reach the wording search</span><br>${esc(e.message)}. Fast answers still work.`;
  }
}

function renderCards(cites, policyId, id) {
  if (!cites || !cites.length) return "";
  return `<div class="cards">` + cites.map((c) => `
    <div class="cardc" id="c${id}-${c.n}">
      <div class="hd"><span>[${c.n}] <b>${esc(c.section)}</b></span>
        <a href="/pdf/${esc(c.policy_id || policyId)}#page=${c.page}" target="_blank">Page ${esc(c.printed_page)} &#8599;</a></div>
      <q>${esc(c.quote)}</q>
      ${c.text && c.text !== c.quote ? `<details><summary>Full clause</summary><div class="full">${esc(c.text)}</div></details>` : ""}
    </div>`).join("") + `</div>`;
}

/* ── feature 5: close the sale ─────────────────────────── */

function closeKitMsg(p) {
  if (p.status === "withdrawn") return stoppedNotice(p);
  const lang = S.lang || "en";
  /* tapping "Close the sale" again shows the same panel, it does not add a second one */
  const kitKey = `${p.id}|${lang}|${JSON.stringify(S.need?.conditions || [])}`;
  const last = [...chat.querySelectorAll("[data-kit]")].pop();
  const after = [];
  for (let el = last?.nextElementSibling; el; el = el.nextElementSibling) after.push(el);
  if (last && last.dataset.kit === kitKey && after.every((el) => el.classList.contains("me"))) {
    last.scrollIntoView({ block: "start", behavior: "smooth" });
    last.classList.add("hl"); setTimeout(() => last.classList.remove("hl"), 1200);
    return last;
  }
  const k = C.closeKit(p, S.need, lang);
  const id = ++BID;
  const L = k.labels;
  const el = add("bot rich", `<p class="kit__h">${esc(L.pitch)} · ${esc(C.fullName(p))}</p>
    <div class="kit"><p class="kit__t">${esc(L.say)}</p>${k.say.map((s) => `<p>${esc(s)}</p>`).join("")}</div>
    <div class="kit"><p class="kit__t">${esc(L.wa)}</p><pre class="wa" id="wa${id}">${esc(k.whatsapp)}</pre>
      <div class="kit__row"><button type="button" class="btn btn--sm" data-act="copy" data-src="wa${id}">Copy</button>
      <a class="btn btn--sm btn--ghost" href="https://wa.me/?text=${encodeURIComponent(k.whatsapp)}" target="_blank" rel="noopener">WhatsApp</a></div></div>
    <div class="kit"><p class="kit__t">${esc(L.wait)}</p>${k.waitEnd
      ? `<p><b>${esc(k.waitEnd.date)}</b> — ${k.waitEnd.months} months. <span class="cite">${esc(k.waitEnd.cite)}</span></p>`
      : `<p>${lang === "en" ? "Not written in this product." : "Is product mein nahi likha."}</p>`}</div>
    ${kitLists(k, L, lang)}`);
  el.dataset.kit = kitKey;
  return el;
}

/* sale papers and claim papers stay separate; each line cites its wording page */
function kitLists(k, L, lang) {
  const li = (d) => `<li${d.quote ? ` title="${esc(d.quote)}"` : ""}>${esc(d.text)}${d.cite ? ` <span class="cite">${esc(d.cite)}</span>` : ""}</li>`;
  const noneBuy = lang === "en" ? "This wording does not list sale papers — ask the insurer." : lang === "hi" ? "इस वर्डिंग में खरीद के कागज़ नहीं लिखे — इंश्योरर से पूछें।" : "Is wording mein buy papers likhe nahi — insurer se poochho.";
  const noneClaim = lang === "en" ? "This wording does not list claim papers — ask the insurer." : lang === "hi" ? "इस वर्डिंग में क्लेम के कागज़ नहीं लिखे — इंश्योरर से पूछें।" : "Is wording mein claim papers likhe nahi — insurer se poochho.";
  const buy = `<div class="kit"><p class="kit__t">${esc(L.papers)}</p>${k.papers.length ? `<ul>${k.papers.map(li).join("")}</ul>` : `<p class="small">${esc(noneBuy)}</p>`}</div>`;
  const claim = `<div class="kit"><p class="kit__t">${esc(L.claimPapers)}</p>${(k.claimPapers || []).length ? `<ul>${k.claimPapers.map(li).join("")}</ul>` : `<p class="small">${esc(noneClaim)}</p>`}</div>`;
  return `${buy}${claim}<div class="kit kit--warn"><p class="kit__t">${esc(L.dont)}</p><ul>${k.dont.map(li).join("")}</ul></div>`;
}

chat.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  if (b.dataset.act === "pick") pick(b.dataset.id);
  if (b.dataset.act === "pick-kit") pick(b.dataset.id, { kit: true });
  if (b.dataset.act === "pitch") {
    const card = b.closest(".rec");
    const panel = card?.querySelector(".pitch");
    if (!panel) return;
    const open = panel.hasAttribute("hidden");
    panel.toggleAttribute("hidden", !open);
    b.setAttribute("aria-expanded", String(open));
    b.classList.toggle("pitch-btn--open", open);
    const label = b.childNodes[0];
    if (label && label.nodeType === Node.TEXT_NODE) {
      /* keep the language label; only flip the arrow */
    }
    const arrow = b.querySelector("span");
    if (arrow) arrow.textContent = open ? "▴" : "▾";
    if (open) {
      const id = card.dataset.rec;
      if (id) pick(id, { quiet: true });
      panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }
  if (b.dataset.act === "copy") {
    const src = document.getElementById(b.dataset.src);
    try { await navigator.clipboard.writeText(src.textContent); b.textContent = "Copied"; }
    catch { b.textContent = "Select and copy"; }
    setTimeout(() => (b.textContent = "Copy"), 1800);
  }
});
$("#sheet-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-act=pick]");
  if (b) pick(b.dataset.id);
});

/* ── the router: need-first, name-first, or a question ─── */

function send(raw) {
  const text = (raw ?? qEl.value).trim();
  if (!text) return;
  qEl.value = "";
  qEl.style.height = "42px";
  add("me", esc(text));

  const need = C.parseNeed(text);
  const intent = C.classify(text);
  /* a short command ("Close the sale" chip) keeps the conversation's language */
  /* a short English message (a product name, "Close the sale") keeps the conversation's language */
  const lang0 = C.detectLang(text);
  if (!(lang0 === "en" && text.split(/\s+/).length <= 4 && S.lang && S.lang !== "en")) S.lang = lang0;
  const matches = C.matchProducts(text, S.products, S.mine);
  /* "Is maternity covered?" on a picked product asks about it; it does not switch to Care Joy */
  const topicOnly = !!S.selected && C.onlyTopicMatch(text, S.products);
  const named = matches.length && need.signals < 2 && !topicOnly;

  if (named) {
    const already = matches.length === 1 && matches[0].id === S.selected;
    if (already) return answer(matches[0], text);
    if (matches.length === 1) return pick(matches[0].id, { pendingQ: intent ? text : null });
    return askWhich(matches, intent ? text : null);
  }
  if (C.isNeed(need, !!S.selected)) { S.choices = null; S.pending = null; return needFirst(need); }
  if (S.choices) {
    add("bot", `<p class="lead">Pick the exact product first — then I'll answer “${esc(text)}” for that one only.</p>`);
    return askWhich(S.choices, text);
  }
  if (S.selected) return answer(S.byId.get(S.selected), text);
  if (intent) {
    const list = [...S.products].filter((p) => !C.isUpload(p) && p.status !== "withdrawn")
      .sort((a, b) => (S.mine.has(b.insurer) ? 1 : 0) - (S.mine.has(a.insurer) ? 1 : 0));
    add("bot", `<p class="lead">For which product? Type its name, or tell me about the customer (age, illness, room rent, budget) and I'll find the best one.</p>`);
    return askWhich(list, text);
  }
  add("bot", `<p class="lead">Tell me one of two things:</p>
    <p>• <b>The customer's need</b> — “Mother 50, diabetes, room rent above 10,000”</p>
    <p>• <b>A product name</b> — “Optima Secure”, “Care Supreme”</p>
    <p class="small">I know all ${S.products.length} policies already. A PDF is optional.</p>`);
}

$("#f").onsubmit = (e) => { e.preventDefault(); send(); };
qEl.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
qEl.addEventListener("input", () => { qEl.style.height = "42px"; qEl.style.height = Math.min(qEl.scrollHeight, 120) + "px"; });

/* ── optional: add one more wording by PDF ─────────────── */

async function uploadPdf(file) {
  if (!file || S.uploading) return;
  if (!/\.pdf$/i.test(file.name)) { addSys("Upload a PDF policy wording.", "err"); return; }
  S.uploading = true;
  attachBtn.disabled = true;
  attachBtn.classList.add("attach--busy");
  const t0 = Date.now();
  const note = addSys(`Reading <b>${esc(file.name)}</b>… 0s`, "upload");
  const tick = setInterval(() => { note.innerHTML = `Reading <b>${esc(file.name)}</b>… ${Math.round((Date.now() - t0) / 1000)}s`; }, 500);
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("insurer", "Uploaded");
  fd.append("product", file.name.replace(/\.pdf$/i, "").replace(/[_-]+/g, " "));
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg || JSON.stringify(x)).join("; ") : data.message || res.statusText);
    }
    const p = uploadAsProduct({ policy_id: data.policy_id, insurer: data.product?.insurer || "Uploaded", product: data.product?.name || data.policy_id });
    if (!S.byId.has(p.id)) { S.products.push(p); S.byId.set(p.id, p); }
    const speed = data.under_10s ? `under 10 seconds (${data.ms} ms)` : `${(data.ms / 1000).toFixed(1)}s`;
    note.innerHTML = `<b>${esc(p.name)} is ready.</b> ${esc(String(data.n_pages))} pages · ${esc(String(data.n_chunks))} clauses · ${esc(speed)}.`;
    pick(p.id);
  } catch (err) {
    note.className = "sys sys--err";
    note.textContent = `Couldn't read that PDF — ${err.message || err}. Try a text PDF under 60 pages.`;
  } finally {
    clearInterval(tick);
    S.uploading = false;
    attachBtn.disabled = false;
    attachBtn.classList.remove("attach--busy");
    pdfInput.value = "";
  }
}
attachBtn.addEventListener("click", () => { if (!S.uploading) pdfInput.click(); });
pdfInput.addEventListener("change", () => { if (pdfInput.files?.[0]) uploadPdf(pdfInput.files[0]); });
["dragover", "dragleave", "drop"].forEach((evt) => document.addEventListener(evt, (e) => {
  if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
  e.preventDefault();
  attachBtn.classList.toggle("attach--drag", evt === "dragover");
  if (evt === "drop" && e.dataTransfer.files?.[0]) uploadPdf(e.dataTransfer.files[0]);
}));

const known = () => S.products.filter((p) => !C.isUpload(p)).length;
async function pollStatus() {
  try {
    const s = await (await fetch("/api/status")).json();
    $("#status").textContent = s.error ? `${known()} policies · search offline` : s.ready ? `${known()} policies known` : "Starting search…";
    if (!s.ready && !s.error) setTimeout(pollStatus, 2000);
  } catch { $("#status").textContent = `${known()} policies · offline search`; setTimeout(pollStatus, 3000); }
}

/* ── phone / website simulation ────────────────────────── */

const device = $("#device");
const stage = $(".stage");
const SIZES = { iphone: 852, pixel: 915 };
const INTRO = {
  iphone: {
    kicker: "Mobile app · live simulation",
    title: 'The policy expert <em>in your pocket.</em>',
    copy: "This is the agent's phone view, running live. Ask in English, Hindi or Hinglish — every answer comes from the wording, with clause and page.",
  },
  pixel: {
    kicker: "Mobile app · live simulation",
    title: 'The policy expert <em>in your pocket.</em>',
    copy: "This is the agent's phone view, running live. Ask in English, Hindi or Hinglish — every answer comes from the wording, with clause and page.",
  },
  website: {
    kicker: "Website · live simulation",
    title: 'The policy expert <em>on the desk.</em>',
    copy: "This is the agent's website view, running live. Same chat, same answers — every figure from the wording, with clause and page.",
  },
};
function setView(name) {
  device.dataset.device = name;
  stage.classList.toggle("stage--website", name === "website");
  const copy = INTRO[name] || INTRO.iphone;
  $("#intro-kicker").textContent = copy.kicker;
  $("#intro-title").innerHTML = copy.title;
  $("#intro-copy").textContent = copy.copy;
  document.querySelectorAll(".devices [data-set]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.set === name)));
  fitDevice();
}
function fitDevice() {
  if (matchMedia("(max-width: 720px)").matches || device.dataset.device === "website") {
    device.style.removeProperty("--fit");
    return;
  }
  device.style.setProperty("--fit", Math.min(1, (innerHeight - 48) / (SIZES[device.dataset.device] + 24)).toFixed(3));
}
document.querySelectorAll(".devices [data-set]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.set)));
addEventListener("resize", fitDevice);
fitDevice();
function tickClock() { $("#clock").textContent = new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: false }); }
tickClock(); setInterval(tickClock, 15000);
addEventListener("load", () => {
  const M = !matchMedia("(prefers-reduced-motion: reduce)").matches && window.Motion;
  if (!M || matchMedia("(max-width: 720px)").matches) return;
  M.animate(".device__frame", { opacity: [0, 1], y: [60, 0], rotate: [-2, 0] }, { type: "spring", stiffness: 140, damping: 18 });
  M.animate(".intro > *", { opacity: [0, 1], x: [-24, 0] }, { type: "spring", stiffness: 220, damping: 24, delay: M.stagger(0.07) });
});

(async () => {
  try {
    await loadData();
  } catch (e) {
    addSys(`Couldn't load the policy database — ${esc(e.message)}.`, "err");
  }
  renderBar();
  pollStatus();
  const active = S.products.filter((p) => !C.isUpload(p) && p.status === "active").length;
  const stopped = S.products.filter((p) => p.status === "withdrawn").length;
  chat.innerHTML = "";
  add("bot", `<p class="lead">Namaste! I already know ${active} live policies${stopped ? ` and ${stopped} stopped one` : ""} — no PDF needed.</p>
    <p>• Tell me the <b>customer's need</b> and I'll pick the best two.</p>
    <p>• Or name a <b>product</b> and I'll check only that one.</p>
    <p class="small">Every figure comes with its page and rule. Tap <b>My insurers</b> above so your companies show first.</p>`);
})();
