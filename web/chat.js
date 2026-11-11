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
/* every citation opens /cite in a new tab: the real PDF page with the exact quoted line highlighted */
const pageNo = (s) => { const m = String(s ?? "").match(/(?:PDF page|page)\s*(\d+)/i); return m ? +m[1] : null; };
const citeHref = (pid, page, quote, section, product) =>
  `/cite/${encodeURIComponent(pid)}?${new URLSearchParams({ page, q: quote || "", s: section || "", product: product || "" })}`;
const citeA = (p, page, quote, section, label) => {
  const pid = p.policy_id || C.policyIdOf(p);
  const text = label || `PDF page ${page}`;
  return page ? `<a class="citelink" href="${esc(citeHref(pid, page, quote, section, C.fullName(p)))}" target="_blank" rel="noopener" title="Open PDF page ${page} with this line highlighted">${esc(text)} &#8599;</a>`
    : `<span class="cite">${esc(text)}</span>`;
};
const pdfLink = (p, cite, quote, section) => (cite?.pdf ? citeA(p, cite.pdf, quote, section, C.pageLabel(cite)) : "");
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
    <div class="kit"><p class="kit__t">${esc(L.say)}</p>${k.say.map((s) => `<p${/^(Premium|The premium|प्रीमियम)/.test(s) ? " data-money-say" : ""}>${esc(s)}</p>`).join("")}</div>
    <div class="kit"><p class="kit__t">${esc(L.wa)}</p><pre class="wa" id="wa${id}">${esc(k.whatsapp)}</pre>
      <div class="kit__row"><button type="button" class="btn btn--sm" data-act="copy" data-src="wa${id}">Copy</button>
      <a class="btn btn--sm btn--ghost" href="https://wa.me/?text=${encodeURIComponent(k.whatsapp)}" target="_blank" rel="noopener">WhatsApp</a></div></div>
    <div class="kit"><p class="kit__t">${esc(L.wait)}</p>${k.waitEnd
      ? `<p><b>${esc(k.waitEnd.date)}</b> — ${k.waitEnd.months} months. ${citeA(p, pageNo(k.waitEnd.cite), k.waitEnd.quote, k.waitEnd.section, k.waitEnd.cite)}</p>`
      : `<p>${lang === "en" ? "Not written in this product." : lang === "hi" ? "इस प्रॉडक्ट में नहीं लिखा।" : "Is product mein nahi likha."}</p>`}</div>
    ${kitLists(k, L, lang, p)}
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
    ${dateLine ? `<p class="rec__line">${dateLine}</p>` : ""}
    ${c.cite?.quote ? `<q class="citeq">“${esc(c.cite.quote.length > 260 ? c.cite.quote.slice(0, 257) + "…" : c.cite.quote)}”</q>` : ""}
    ${c.cite ? `<p class="cite"><span data-heading>${c.cite.section ? `${esc(c.cite.section)} · ` : ""}</span>${citeA(p, c.cite.pdf_page, c.cite.quote, c.cite.section, c.cite.page)}</p>` : ""}
    <div class="rec__actions">
      <button type="button" class="btn${rank === 1 ? "" : " btn--ghost"} btn--sm" data-act="pick" data-id="${esc(p.id)}">${esc(L.pick)}</button>
      <button type="button" class="pitch-btn" data-act="pitch" aria-expanded="false">${esc(L.pitch)} <span aria-hidden="true">▾</span></button>
    </div>
    <p class="rec__line" data-money-card="${esc(p.id)}"></p>
    ${pitchHtml(p, lang)}
  </div>`;
}

/* replace a coarse section label with the numbered clause heading read from the PDF page above the quote */
function fillHeadings(root) {
  root.querySelectorAll("[data-heading]").forEach(async (el) => {
    const a = el.parentElement.querySelector("a.citelink");
    if (!a) return;
    const u = new URL(a.getAttribute("href"), location.href);
    const pid = decodeURIComponent(u.pathname.split("/").pop());
    try {
      const r = await (await fetch(`/api/cite-heading?${new URLSearchParams({ policy_id: pid, page: u.searchParams.get("page"), q: u.searchParams.get("q") || "" })}`)).json();
      if (r.heading) el.textContent = `${r.heading} · `;
    } catch { /* keep the label from the wording facts */ }
  });
}

/* fill the money line on each ranked card once its figure arrives */
function fillMoney(root, need, lang) {
  root.querySelectorAll("[data-money-card]").forEach(async (el) => {
    const p = S.byId.get(el.dataset.moneyCard);
    const line = p && await moneyLine(p, need, lang);
    const say = el.closest(".rec")?.querySelector("[data-money-say]");   /* the card's "How to pitch" panel */
    if (line && say) say.textContent = line;
    if (line) el.innerHTML = `<b>${esc(line)}</b>`; else el.remove();
  });
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
  /* "(PDF page 23)" inside a sentence links to that plan's cited line */
  const linkLine = (line) => {
    let s = esc(line);
    const c = tops.find((t) => line.includes(t.label) || line.includes(t.name));
    const p = c && S.byId.get(c.id);
    if (p && c.cite) s = s.replace(/PDF page (\d+)/, (m, n) => +n === c.cite.pdf_page ? citeA(p, +n, c.cite.quote, c.cite.section, m).replace(" &#8599;", "") : m);
    return s;
  };
  const msg = add("bot rich", `${answer.map((line, i) => `<p class="${i === 0 ? "lead" : ""}">${linkLine(line)}</p>`).join("")}${scope}
    ${cards ? `<div class="recs">${cards}</div>` : ""}
    ${watch}
    ${others}
    <div class="meta">${lang === "hi" ? `${r.checked} पॉलिसी की वर्डिंग · ${ms(t0)} ms` : lang === "hinglish" ? `${r.checked} policies ki wording check · ${ms(t0)} ms` : `Read ${r.checked} policy wordings in ${ms(t0)} ms`}</div>`);
  fillMoney(msg, need, lang);
  fillHeadings(msg);
}

/* ── feature 4: simple vs hard ─────────────────────────── */

const notWritten = (p, note) => add("bot", `<span class="badge b-warn">Not written in this product</span>
  <p class="lead">This is not written in ${esc(C.fullName(p))}.</p>${note ? `<p class="small">${esc(note)}</p>` : ""}`);

/* money for one product and one customer: the insurer's official rate chart for that age and city, else the
   Quickinsure money chart only when the customer is in its age band (51-55). Returns one short line or null. */
const MONEY_T = {
  en: { chart: (x, si, where) => `Premium: ${x} a year for ${si} (insurer's official rate chart, ${where}), before any illness loading.`,
        prof: (x, warn, src) => `Premium: ${x} a year for a 55-year-old in Guwahati, ₹10 lakh cover (${src}), healthy rate before diabetes loading.${warn ? " Older version: verify before quoting." : ""}`,
        none: "Premium: not available for this plan — get a live quote from the insurer." },
  hinglish: { chart: (x, si, where) => `Premium: ${x} saal ka, ${si} cover (insurer ka official rate chart, ${where}), bimari loading se pehle.`,
        prof: (x, warn, src) => `Premium: ${x} saal ka, 55 saal, Guwahati, ₹10 lakh cover (${src}) — healthy rate, diabetes loading alag se.${warn ? " Purana version: quote se pehle confirm karo." : ""}`,
        none: "Premium: is plan ka figure nahi hai — insurer se live quote lo." },
  hi: { chart: (x, si, where) => `प्रीमियम: ${x} सालाना, ${si} कवर (इंश्योरर का official rate chart, ${where}), बीमारी loading से पहले।`,
        prof: (x, warn, src) => `प्रीमियम: ${x} सालाना, 55 साल, गुवाहाटी, ₹10 लाख कवर (${src}) — healthy rate, डायबिटीज़ loading अलग।${warn ? " पुराना version: quote से पहले कन्फ़र्म करें।" : ""}`,
        none: "प्रीमियम: इस प्लान का आंकड़ा नहीं है — इंश्योरर से live quote लें।" },
};
const MONEY = new Map();
async function moneyFor(p, age, city) {
  const key = `${p.id}|${age}|${city || ""}`;
  if (!MONEY.has(key)) {
    const q = new URLSearchParams({ product: p.id, city: city || "" });
    if (age !== null && age !== undefined) q.set("age", age);
    MONEY.set(key, fetch(`/api/premium?${q}`).then((r) => r.json()).catch(() => null));
  }
  return MONEY.get(key);
}
async function moneyLine(p, need, lang) {
  const T = MONEY_T[lang] || MONEY_T.en;
  const age = need?.age ?? null;
  const r = await moneyFor(p, age, S.needText || "");
  if (!r) return null;
  const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
  if (r.available && r.prices) {
    const x = r.prices.find((v) => v.sum_insured === 1000000) || r.prices[0];
    const val = x.min === x.max ? inr(x.min) : `${inr(x.min)}–${inr(x.max)}`;
    const where = r.zone ? `age ${r.age}, Zone ${r.zone}` : `age ${r.age}, by city zone`;
    return T.chart(val, `₹${x.sum_insured / 100000} lakh`, where);
  }
  const pq = r.profile_quote;
  if (!pq) return null;
  if (!pq.found) return T.none;
  if (age !== null && age >= 51 && age <= 55) return T.prof(inr(pq.annual_premium_inr), !!pq.warning, (pq.source || "").split(",")[0]);
  return null;   /* the money chart is for a 55-year-old: never shown as another age's price */
}

/* the insurer's official rate chart, when one is loaded for this product (premium.py) — never the wording */
async function rateCard(p, text, box) {
  /* "premium for 55, Pune": a bare two-digit number is the age, unless it is an amount (10 lakh, 5L, 20k) */
  const bare = text.match(/\b(\d{1,2})\b(?!\s*(?:lakh|lac|l\b|k\b|cr|crore|%|,\d{3}|months?|days?))/i);
  const age = C.parseNeed(text).age ?? (bare ? +bare[1] : null) ?? S.need?.age ?? null;
  const city = `${text} ${S.needText || ""}`;
  let r;
  try {
    const q = new URLSearchParams({ product: p.id, city });
    if (age !== null) q.set("age", age);
    r = await (await fetch(`/api/premium?${q}`)).json();
  } catch { r = null; }
  const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
  const lang = S.lang || "en";
  const T = RATE_T[lang] || RATE_T.en;
  /* the first line is always a direct answer: the price, or plainly that no price is loaded for this plan */
  const leadEl = box.closest(".msg")?.querySelector("[data-rate-lead]");
  const name = C.fullName(p);
  const setLead = (s) => { if (leadEl) leadEl.textContent = s; };
  const pq0 = r?.profile_quote;
  const off0 = pq0?.found && age !== null && (age < 51 || age > 55);
  if (r?.available && r.prices) {
    const x = r.prices.find((v) => v.sum_insured === 1000000) || r.prices[0];
    setLead(T.leadChart(name, x.min === x.max ? inr(x.min) : `${inr(x.min)} – ${inr(x.max)}`, x.sum_insured / 100000, r.age, r.zone));
  } else if (pq0?.found && !off0) {
    setLead(T.leadProfile(name, inr(pq0.annual_premium_inr)));
  } else if (off0) {
    setLead(T.leadOffAge(name, age));
  } else if (r?.available && r.need === "age") {
    setLead(T.leadAskAge(name));
  } else {
    setLead(T.leadNone(name));
  }
  const lakh = (n) => T.lakh(n / 100000);
  const badge = box.closest(".msg")?.querySelector("[data-money-badge]");
  const found = () => { if (badge) { badge.className = "badge b-ok"; badge.textContent = T.badge; } };
  /* the Quickinsure money chart: one fixed profile, shown with that profile so it is never read as this customer's price */
  const pq = r?.profile_quote;
  const rules = r?.rules ? `<p class="small">${esc(T.rules)}</p>` : "";
  /* the money chart is for a 55-year-old: a known age outside 51-55 never sees that figure */
  const offProfile = pq?.found && age !== null && (age < 51 || age > 55);
  const pqHtml = !pq || offProfile || !pq.found ? "" : pq.found
    ? `<div class="kit" data-money="1"><p class="small">${esc(T.profile)}: ${esc(pq.profile)}. ${esc(T.source)}: ${esc(pq.source || "")}${pq.uin ? ` (UIN ${esc(pq.uin)})` : ""}, ${esc(T.checked)} ${esc(pq.checked)}.${pq.notes ? ` ${esc(pq.notes)}.` : ""}</p>
        ${pq.warning ? `<p class="stopline">${esc(T.older)}</p>` : ""}</div>`
    : `<div class="kit" data-money="0"><p><b>Premium:</b> ${esc(T.notFound)}</p>
        <p class="small">${esc(T.profile)}: ${esc(pq.profile)}, ${esc(pq.checked)}. ${esc(T.noEstimate)}</p></div>`;
  if (pq?.found && !offProfile) found();
  if (!r?.available) { if (pqHtml) box.innerHTML = pqHtml + rules; return; }
  const src = `<p class="small">${esc(T.source)}: <a href="${esc(r.source_url)}" target="_blank">${esc(r.source)}</a> (UIN ${esc(r.uin)}), ${esc(T.checked)} ${esc(r.checked_on)}. ${esc(T.basis)}</p>`;
  if (r.need === "age") {
    box.innerHTML = `${pqHtml}<p><b>${esc(T.askAge)}</b></p>${src}${rules}`;
    return;
  }
  if (r.need) { box.innerHTML = `${pqHtml}<p>${esc(T.noAge)}</p>${src}${rules}`; return; }
  found();
  const where = r.zone ? `Zone ${esc(r.zone)} (${esc(r.zone_places)})` : esc(T.allZones(r.zones_shown));
  const rows = r.prices.map((x) => `<li>${lakh(x.sum_insured)}: <b>${x.min === x.max ? inr(x.min) : `${inr(x.min)} – ${inr(x.max)}`}</b> ${esc(T.perYear)}</li>`).join("");
  box.innerHTML = `${pqHtml}<p><b>${esc(T.official)}</b> — ${esc(T.age)} ${r.age} (${esc(r.age_band)}), ${esc(T.oneAdult)}, ${where}:</p><ul>${rows}</ul>${src}${rules}`;
}
const RATE_T = {
  en: { leadChart: (n, v, si, a, z) => `${n}: ${v} per year for ₹${si} lakh cover, age ${a}${z ? `, Zone ${z}` : ""}.`,
    leadProfile: (n, v) => `${n}: ${v} per year (age 55, Guwahati, ₹10 lakh cover).`,
    leadOffAge: (n, a) => `${n}: no price loaded for age ${a}. Get a live quote from the insurer with the age, city and cover amount.`,
    leadAskAge: (n) => `${n}: tell me the age and city to get the exact price.`,
    leadNone: (n) => `${n}: no price is loaded for this plan. Get a live quote from the insurer with the age, city and cover amount.`,
    badge: "Premium from rate chart", perYear: "per year", profile: "Profile", source: "Source", checked: "checked",
    older: "This figure is from an older version of the product. Verify with the insurer before quoting.",
    notFound: "Not found — get a live quote from the insurer.", noEstimate: "No figure is estimated.",
    offAge: (a) => `the premium figure loaded is only for a 55-year-old, so it is not shown for age ${a}. Get a live quote from the insurer for this age.`,
    rules: "Healthy-person rate, before any loading for diabetes or another illness; the insurer decides loading after the health check (usually 10-40%, up to 100-150% in some cases). GST on individual health insurance is 0% since 22 Sep 2025.",
    basis: "Base premium, 1 year, before discounts, loadings and optional covers. Final premium is set by the insurer after the health check.",
    askAge: "The insurer's official rate chart is loaded. Tell me the age and city (for example “premium for 55, Pune”) for the exact chart price.",
    noAge: "This age is not in the insurer's rate chart.", official: "From the insurer's official rate chart", age: "age",
    oneAdult: "1 adult", allZones: (n) => `all ${n} zones — name the city for the exact one`, lakh: (n) => `₹${n} lakh` },
  hinglish: { leadChart: (n, v, si, a, z) => `${n}: ${v} saal ka, ₹${si} lakh cover, umar ${a}${z ? `, Zone ${z}` : ""}.`,
    leadProfile: (n, v) => `${n}: ${v} saal ka (umar 55, Guwahati, ₹10 lakh cover).`,
    leadOffAge: (n, a) => `${n}: ${a} saal ke liye price load nahi hai. Umar, shehar aur cover batake insurer se live quote lo.`,
    leadAskAge: (n) => `${n}: exact price ke liye umar aur shehar batao.`,
    leadNone: (n) => `${n}: is plan ka price load nahi hai. Umar, shehar aur cover batake insurer se live quote lo.`,
    badge: "Rate chart se premium", perYear: "saal ka", profile: "Profile", source: "Source", checked: "check kiya",
    older: "Yeh figure product ke purane version ka hai. Quote dene se pehle insurer se confirm karo.",
    notFound: "Nahi mila — insurer se live quote lo.", noEstimate: "Koi andaaza nahi lagaya.",
    offAge: (a) => `loaded premium sirf 55 saal ke liye hai, isliye ${a} saal ke liye nahi dikhaya. Is umar ke liye insurer se live quote lo.`,
    rules: "Healthy person ka rate hai, diabetes ya bimari ki loading se pehle; loading insurer health check ke baad tay karta hai (aam taur par 10-40%, kabhi 100-150% tak). Individual health insurance par GST 22 Sep 2025 se 0% hai.",
    basis: "Base premium, 1 saal, discount, loading aur optional cover se pehle. Final premium insurer health check ke baad batata hai.",
    askAge: "Insurer ka official rate chart loaded hai. Umar aur shehar batao (jaise “premium for 55, Pune”), main exact chart price dikhaunga.",
    noAge: "Yeh umar insurer ke rate chart mein nahi hai.", official: "Insurer ke official rate chart se", age: "umar",
    oneAdult: "1 adult", allZones: (n) => `saare ${n} zones — exact ke liye shehar batao`, lakh: (n) => `₹${n} lakh` },
  hi: { leadChart: (n, v, si, a, z) => `${n}: ${v} सालाना, ₹${si} लाख कवर, उम्र ${a}${z ? `, Zone ${z}` : ""}।`,
    leadProfile: (n, v) => `${n}: ${v} सालाना (उम्र 55, गुवाहाटी, ₹10 लाख कवर)।`,
    leadOffAge: (n, a) => `${n}: ${a} साल के लिए price लोड नहीं है। उम्र, शहर और कवर बताकर इंश्योरर से live quote लें।`,
    leadAskAge: (n) => `${n}: सटीक price के लिए उम्र और शहर बताइए।`,
    leadNone: (n) => `${n}: इस प्लान का price लोड नहीं है। उम्र, शहर और कवर बताकर इंश्योरर से live quote लें।`,
    badge: "रेट चार्ट से प्रीमियम", perYear: "सालाना", profile: "प्रोफ़ाइल", source: "स्रोत", checked: "जाँचा",
    older: "यह आंकड़ा प्रोडक्ट के पुराने version का है। quote से पहले इंश्योरर से कन्फ़र्म करें।",
    notFound: "नहीं मिला — इंश्योरर से live quote लें।", noEstimate: "कोई अनुमान नहीं लगाया।",
    offAge: (a) => `लोड किया गया प्रीमियम सिर्फ़ 55 साल के लिए है, इसलिए ${a} साल के लिए नहीं दिखाया। इस उम्र के लिए इंश्योरर से live quote लें।`,
    rules: "स्वस्थ व्यक्ति का रेट, डायबिटीज़ या बीमारी की loading से पहले; loading इंश्योरर हेल्थ चेक के बाद तय करता है (आमतौर पर 10-40%, कभी 100-150% तक)। व्यक्तिगत हेल्थ इंश्योरेंस पर GST 22 सितंबर 2025 से 0% है।",
    basis: "बेस प्रीमियम, 1 साल, छूट, loading और optional cover से पहले। अंतिम प्रीमियम इंश्योरर हेल्थ चेक के बाद बताता है।",
    askAge: "इंश्योरर का official rate chart लोड है। उम्र और शहर बताइए (जैसे “premium for 55, Pune”)।",
    noAge: "यह उम्र इंश्योरर के rate chart में नहीं है।", official: "इंश्योरर के official rate chart से", age: "उम्र",
    oneAdult: "1 वयस्क", allZones: (n) => `सभी ${n} zones — सटीक के लिए शहर बताइए`, lakh: (n) => `₹${n} लाख` },
};

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
    const lang = S.lang || "en";
    const badge = { en: "Premium", hinglish: "Premium", hi: "प्रीमियम" }[lang] || "Premium";
    const el = add("bot", `${stopNote}<span class="badge b-warn" data-money-badge>${esc(badge)}</span>
      <p class="lead" data-rate-lead>…</p>
      <div class="rate" data-rate="1"></div>
      <div class="meta">Answered in ${ms(t0)} ms</div>`);
    rateCard(p, text, el.querySelector("[data-rate]"));
    return el;
  }
  if (intent?.kind === "fast" && !C.isUpload(p)) {
    const a = C.simpleAnswer(p, intent.id);
    if (a?.notWritten) return notWritten(p, a.note);
    if (a) {
      const fe = add("bot", `${stopNote}<span class="badge b-ok">Fast answer</span><p class="lead">${esc(a.lead)}</p>
        ${a.note ? `<p class="small">${esc(a.note)}</p>` : ""}
        ${a.quote ? `<q class="citeq">“${esc(a.quote)}”</q>` : ""}
        <p class="cite"><span data-heading>${a.section ? `${esc(a.section)} · ` : ""}</span>${pdfLink(p, a.cite, a.quote, a.section) || esc(C.pageLabel(a.cite))}</p>
        <div class="meta">Answered in ${ms(t0)} ms · from the verified fact table</div>`);
      fillHeadings(fe);
      return fe;
    }
  }
  if (intent?.kind === "hard" && !C.isUpload(p)) {
    const h = C.hardFact(p, intent.id);
    /* the fact table only holds the headline clause; read the whole wording before saying "not written" */
    if (h?.notWritten) return askRag(p, text);
    if (h) {
      return add("bot", `${stopNote}<span class="badge b-ok">Exact policy line</span>
        <div class="cardc"><div class="hd"><span><b>${esc(h.section || "Clause")}</b></span>${pdfLink(p, h.cite, h.quote, h.section)}</div>
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
        <a class="citelink" href="${esc(citeHref(c.policy_id || policyId, c.page, c.quote, c.section, c.product || ""))}" target="_blank" rel="noopener" title="Open PDF page ${esc(c.page)} with this line highlighted">PDF page ${esc(c.page)} &#8599;</a></div>
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
    <div class="kit"><p class="kit__t">${esc(L.say)}</p>${k.say.map((s) => `<p${/^(Premium|The premium|प्रीमियम)/.test(s) ? " data-money-say" : ""}>${esc(s)}</p>`).join("")}</div>
    <div class="kit"><p class="kit__t">${esc(L.wa)}</p><pre class="wa" id="wa${id}">${esc(k.whatsapp)}</pre>
      <div class="kit__row"><button type="button" class="btn btn--sm" data-act="copy" data-src="wa${id}">Copy</button>
      <a class="btn btn--sm btn--ghost" href="https://wa.me/?text=${encodeURIComponent(k.whatsapp)}" target="_blank" rel="noopener">WhatsApp</a></div></div>
    <div class="kit"><p class="kit__t">${esc(L.wait)}</p>${k.waitEnd
      ? `<p><b>${esc(k.waitEnd.date)}</b> — ${k.waitEnd.months} months. ${citeA(p, pageNo(k.waitEnd.cite), k.waitEnd.quote, k.waitEnd.section, k.waitEnd.cite)}</p>`
      : `<p>${lang === "en" ? "Not written in this product." : "Is product mein nahi likha."}</p>`}</div>
    ${kitLists(k, L, lang, p)}`);
  el.dataset.kit = kitKey;
  /* money: the chart figure replaces "premium comes later" in Say this, and is added to the WhatsApp text */
  moneyLine(p, S.need, lang).then((line) => {
    if (!line) return;
    const sayEl = el.querySelector("[data-money-say]");
    if (sayEl) sayEl.textContent = line;
    const pre = el.querySelector(`#wa${id}`);
    if (pre) {
      const lines = pre.textContent.split("\n");
      lines.splice(Math.max(1, lines.length - 1), 0, `• ${line}`);
      pre.textContent = lines.join("\n");
      const a = el.querySelector('a[href^="https://wa.me/"]');
      if (a) a.href = `https://wa.me/?text=${encodeURIComponent(pre.textContent)}`;
    }
  });
  return el;
}

/* sale papers and claim papers stay separate; each line cites its wording page */
function kitLists(k, L, lang, p) {
  const li = (d) => `<li${d.quote ? ` title="${esc(d.quote)}"` : ""}>${esc(d.text)}${d.cite ? ` ${p ? citeA(p, pageNo(d.cite), d.quote, d.section, d.cite) : `<span class="cite">${esc(d.cite)}</span>`}` : ""}</li>`;
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
  /* gate 1, scope: only health wordings are loaded; another line of insurance is refused, never ranked */
  const other = C.outOfScope(text);
  if (other) {
    add("bot", `<span class="badge b-stop">Not in these policies</span><br><p class="lead" data-scope="${other}">${esc(C.scopeMessage(other, S.lang || "en", S.products.length))}</p>`);
    return;
  }
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
  /* "premium for 55, Pune" on a picked product asks its rate chart; it is not a new customer story */
  if (S.selected && intent?.kind === "premium") return answer(S.byId.get(S.selected), text);
  if (C.isNeed(need, !!S.selected)) { S.choices = null; S.pending = null; S.needText = text; return needFirst(need); }
  /* gate 2: "konsa plan accha hai" with no person, age or illness — ask, never rank a guess */
  if (need.shopping && !need.real && !intent) {
    add("bot", `<p class="lead" data-askneed="1">${esc(C.ASK_NEED[S.lang] || C.ASK_NEED.en)}</p>`);
    return;
  }
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
